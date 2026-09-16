"""
clinic.agent_tools —— 暴露给 LLM Agent 的工具集

工具 = 函数 + 给 LLM 看的描述（JSON Schema）+ 执行入口。

设计原则
--------
1. **粒度合适**：一个工具对应一个业务动作（"跑 QC 检查"、"按组汇总"），
   而不是 `read_column` 这种原子操作。
2. **硬边界**：数据集白名单在代码里校验，**不依赖提示词约束 LLM**。
3. **返回 JSON 友好**：所有返回值都可被 `json.dumps` 序列化。
4. **失败可读**：异常转成结构化错误信息，让 Agent 能理解并调整。

安全提示
--------
LLM 是**不可信的执行者**。生产环境中还应加上：
权限校验、数据分类分级、访问审计、输出脱敏。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from . import qc
from .report import TRT_LEVELS

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# 数据访问层（白名单 + 缓存）
# --------------------------------------------------------------------------
_BASE = Path(__file__).resolve().parent.parent
_SAMPLES = _BASE / "data" / "samples"
_RAW = _BASE / "data" / "raw"

# ★ 安全边界：Agent 只能访问这些数据集
ALLOWED_DATASETS: set[str] = {
    "dm", "adsl", "ae", "adae", "ex", "ds", "adtte", "vs_bp", "adlbc_shift",
}

_cache: dict[str, pd.DataFrame] = {}


def _load(name: str) -> pd.DataFrame:
    """载入数据集；不在白名单内直接拒绝。"""
    key = str(name).strip().lower()
    if key not in ALLOWED_DATASETS:
        raise ValueError(
            f"数据集 '{name}' 不在允许访问的列表中。"
            f"可选值：{sorted(ALLOWED_DATASETS)}"
        )
    if key not in _cache:
        path = _SAMPLES / f"{key}.csv"
        if not path.exists():
            raise FileNotFoundError(
                f"样本文件不存在：{path}。请先运行 python scripts/make_samples.py"
            )
        _cache[key] = pd.read_csv(path, dtype=str, low_memory=False)
        log.debug("已缓存 %s（%d 行）", key, len(_cache[key]))
    return _cache[key].copy()


def list_datasets() -> dict:
    """列出 Agent 可以访问的所有数据集。"""
    out = []
    for name in sorted(ALLOWED_DATASETS):
        p = _SAMPLES / f"{name}.csv"
        out.append({"名称": name, "可用": p.exists(),
                    "文件大小KB": round(p.stat().st_size / 1024) if p.exists() else None})
    return {"数据集": out, "说明": "CDISC 试点项目 CDISCPILOT01（公开数据）"}


# --------------------------------------------------------------------------
# 工具 1：数据结构描述
# --------------------------------------------------------------------------
def describe_dataset(name: str) -> dict:
    """查看数据集规模与每个变量的分布概况。

    在开始任何分析前应优先调用本工具了解数据结构。
    """
    df = _load(name)
    variables = []
    for c in df.columns:
        s = df[c]
        variables.append({
            "变量": c,
            "缺失数": int(s.isna().sum()),
            "缺失率%": round(float(s.isna().mean() * 100), 1),
            "唯一值数": int(s.nunique(dropna=True)),
            "示例值": [str(v) for v in s.dropna().unique()[:3]],
        })
    return {"数据集": name, "行数": len(df), "列数": df.shape[1], "变量": variables}


# --------------------------------------------------------------------------
# 工具 2：QC 检查
# --------------------------------------------------------------------------
def run_qc_checks(name: str, dataset_type: str = "auto") -> dict:
    """对数据集执行标准数据质量检查。

    检查项：必需变量存在性、主键唯一性、日期逻辑、受控术语、全缺失变量、
    首尾空格、极端离群值。

    Parameters
    ----------
    name : 数据集名称
    dataset_type : ``'sdtm'`` / ``'adam'`` / ``'auto'``
    """
    df = _load(name)
    if dataset_type == "auto":
        dataset_type = "adam" if name.lower().startswith("ad") else "sdtm"

    issues = qc.check_dataset(df, name, dataset_type)
    high = [i for i in issues if i["严重性"] == "高"]
    return {
        "数据集": name,
        "数据集类型": dataset_type,
        "通过": len(high) == 0,
        "问题总数": len(issues),
        "高优先级问题数": len(high),
        "问题": issues,
    }


def check_subject_consistency(domains: list[str] | None = None) -> dict:
    """检查各数据集的 USUBJID 是否都在 DM 中（跨数据集一致性）。

    Parameters
    ----------
    domains : 要检查的域名列表；默认检查 ae、ex、ds、adsl
    """
    domains = domains or ["ae", "ex", "ds", "adsl"]
    dm = _load("dm")
    others = {d: _load(d) for d in domains if d != "dm"}
    issues = qc.check_subject_consistency(dm, others)

    detail = {}
    dm_subj = set(dm["USUBJID"].dropna().astype(str))
    for d, df_ in others.items():
        s = set(df_["USUBJID"].dropna().astype(str))
        detail[d] = {
            "受试者数": len(s),
            "不在DM中": len(s - dm_subj),
            "DM中有但本域无记录": len(dm_subj - s),
        }
    return {
        "DM 受试者总数": len(dm_subj),
        "各域明细": detail,
        "问题": issues,
        "解读提示": (
            "'DM中有但本域无记录'通常是正常的（例如没有发生不良事件的受试者）。"
            "'不在DM中'则是必须修复的数据错误。"
        ),
    }


# --------------------------------------------------------------------------
# 工具 3：分组描述统计
# --------------------------------------------------------------------------
def summarize_by_group(name: str, group_var: str, value_var: str) -> dict:
    """按分组变量对数值变量做描述统计（N/Mean/SD/Median/Min/Max）。

    适用于：按治疗组汇总年龄、按治疗组汇总基线 BMI 等。
    SD 使用 ddof=1，与 SAS 的 STD 一致。
    """
    df = _load(name)
    for v in (group_var, value_var):
        if v not in df.columns:
            raise ValueError(
                f"变量 '{v}' 不存在于 {name}。可用变量：{list(df.columns)}"
            )

    sub = df[[group_var, value_var]].copy()
    sub[value_var] = pd.to_numeric(sub[value_var], errors="coerce")
    sub[group_var] = sub[group_var].astype(str).str.strip()

    out = (sub.groupby(group_var, dropna=False)[value_var]
              .agg(N="count", Mean="mean", SD=lambda s: s.std(ddof=1),
                   Median="median", Min="min", Max="max")
              .round(2).reset_index())

    return {
        "数据集": name,
        "分组变量": group_var,
        "统计变量": value_var,
        "说明": "SD 采用 ddof=1（样本标准差），与 SAS 的 STD 一致",
        "结果": json.loads(out.to_json(orient="records", force_ascii=False)),
    }


def frequency(name: str, var: str, top_n: int = 20) -> dict:
    """统计某个变量的频数分布（对应 PROC FREQ），**包含缺失值**。

    SAS 的 PROC FREQ 默认显示缺失；pandas 的 value_counts 默认不显示，
    本工具与 SAS 行为对齐。
    """
    df = _load(name)
    if var not in df.columns:
        raise ValueError(f"变量 '{var}' 不存在于 {name}。可用变量：{list(df.columns)}")

    s = df[var].astype(str).str.strip()
    vc = s.value_counts(dropna=False).head(top_n)
    total = len(s)
    rows = [
        {"取值": ("<缺失>" if (k == "nan" or pd.isna(k)) else str(k)),
         "频数": int(v),
         "占比%": round(v / total * 100, 1)}
        for k, v in vc.items()
    ]
    return {"数据集": name, "变量": var, "总记录数": total, "分布": rows}


# --------------------------------------------------------------------------
# 工具 4：不良事件汇总
# --------------------------------------------------------------------------
def count_events(level: str = "soc", treatment_only: bool = True,
                 top_n: int = 20) -> dict:
    """统计不良事件：按 SOC 或 PT 层级给出各治疗组的受试者数与百分比。

    .. important::
       **分子是受试者数，不是事件数。**
       同一受试者发生同一事件多次只计一次 —— 这是 CSR 的标准做法，
       也是与"事件数表"的根本区别。

    Parameters
    ----------
    level : ``'soc'``（系统器官分类）或 ``'pt'``（首选术语）
    treatment_only : 是否只统计治疗中出现的不良事件（TEAE，TRTEMFL='Y'）
    """
    if level not in ("soc", "pt"):
        raise ValueError("level 必须是 'soc' 或 'pt'")

    adae = _load("adae")
    adsl = _load("adsl")

    # 分母：安全性人群
    saf = adsl[adsl["SAFFL"].astype(str).str.strip() == "Y"].copy()
    saf["TRT01P"] = saf["TRT01P"].astype(str).str.strip()
    denom = saf.groupby("TRT01P")["USUBJID"].nunique().to_dict()

    ae = adae.copy()
    ae["TRTEMFL"] = ae["TRTEMFL"].astype(str).str.strip()
    if treatment_only:
        ae = ae[ae["TRTEMFL"] == "Y"]
    ae = ae[ae["USUBJID"].isin(set(saf["USUBJID"]))]
    # 分母所依据的分组必须与分子一致 —— ADAE 只有 TRTA（实际治疗），
    # 而报表分母用的是随机化分组 TRT01P，所以必须从 ADSL 带过来。
    # 这对应 SAS 里最常见的 `merge adae(in=a) adsl(in=b keep=usubjid trt01p);`
    if "TRT01P" not in ae.columns:
        ae = ae.merge(saf[["USUBJID", "TRT01P"]], on="USUBJID", how="left")
    ae["TRT01P"] = ae["TRT01P"].astype(str).str.strip()

    group_keys = ["AEBODSYS"] if level == "soc" else ["AEBODSYS", "AEDECOD"]
    keys = ["USUBJID", "TRT01P"] + group_keys
    uniq = ae.drop_duplicates(subset=keys)          # ★ 受试者层级去重

    cnt = (uniq.groupby(group_keys + ["TRT01P"])["USUBJID"]
                .nunique().reset_index(name="N"))

    rows: dict[str, dict] = {}
    for _, r in cnt.iterrows():
        soc = str(r["AEBODSYS"]) if r["AEBODSYS"] is not None else ""
        label = soc if level == "soc" else f"    {r['AEDECOD']}"
        key = label
        if key not in rows:
            rows[key] = {"层级": label[:80], "_soc": soc, "_total": 0}
        t = r["TRT01P"]
        if t in denom:
            n = int(r["N"])
            rows[key][t] = f"{n} ({n / denom[t] * 100:.1f}%)"
            rows[key]["_total"] += n

    # 各治疗组的总事件受试者数
    any_teae = (ae.drop_duplicates(["USUBJID", "TRT01P"])
                  .groupby("TRT01P")["USUBJID"].nunique().to_dict())

    result = sorted(rows.values(), key=lambda x: x["_total"], reverse=True)[:top_n]
    for r in result:
        r.pop("_total", None)
        for t in TRT_LEVELS:
            r.setdefault(t, "0")

    return {
        "统计层级": level.upper(),
        "事件范围": "治疗中出现的不良事件（TEAE）" if treatment_only else "全部不良事件",
        "分母（安全性人群）": denom,
        "有任一 TEAE 的受试者数": any_teae,
        "结果": [{"层级": r["层级"], **{t: r.get(t, "0") for t in TRT_LEVELS}}
                 for r in result],
        "注意事项": (
            "分子为受试者数（同一受试者同一事件只计一次）；"
            "百分比的分母为该治疗组的受试者总数。"
        ),
    }


# --------------------------------------------------------------------------
# 工具 5：数据集比对
# --------------------------------------------------------------------------
def compare_datasets(name_a: str, name_b: str, keys: list[str] | None = None) -> dict:
    """比对两个数据集（≈ PROC COMPARE），用于结果核对。

    Parameters
    ----------
    keys : 匹配键；默认按数据集自动推断（优先 USUBJID+AESEQ 等）
    """
    a, b = _load(name_a), _load(name_b)
    if keys is None:
        candidates = [["USUBJID", "AESEQ"], ["USUBJID"], ["USUBJID", "PARAMCD"]]
        keys = next((k for k in candidates if all(c in a.columns and c in b.columns for c in k)),
                    None)
        if keys is None:
            raise ValueError(f"无法自动推断匹配键；{name_a} 列：{list(a.columns)}")

    res = qc.compare_frames(a, b, keys)
    res.update({"数据集A": name_a, "数据集B": name_b, "匹配键": keys})
    return res


# --------------------------------------------------------------------------
# 工具 Schema（给 LLM 的说明书）
# --------------------------------------------------------------------------
TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "list_datasets",
            "description": "列出当前可以访问的所有数据集名称及其可用状态。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "describe_dataset",
            "description": (
                "查看某个数据集的规模（行数/列数）与每个变量的分布概况"
                "（缺失数、唯一值数、示例值）。"
                "**在开始任何分析之前都应该先调用这个工具了解数据结构。**"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "enum": sorted(ALLOWED_DATASETS),
                        "description": "数据集名称",
                    }
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_qc_checks",
            "description": (
                "对数据集执行标准数据质量检查，包括：必需变量存在性、主键唯一性、"
                "日期逻辑（结束不得早于开始）、受控术语合法性、全缺失变量、"
                "首尾空格、极端离群值。返回问题清单（含高/中/低严重性）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "enum": sorted(ALLOWED_DATASETS)},
                    "dataset_type": {
                        "type": "string",
                        "enum": ["sdtm", "adam", "auto"],
                        "description": "数据集类型，决定使用哪套检查规则；默认 auto",
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_subject_consistency",
            "description": (
                "检查各数据集的 USUBJID 是否都存在于 DM 中（跨数据集一致性）。"
                "用于发现'孤儿受试者'这类数据错误。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "domains": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "要检查的域名，如 ['ae','ex','ds','adsl']",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "summarize_by_group",
            "description": (
                "按分组变量对某个数值变量做描述统计（N/Mean/SD/Median/Min/Max）。"
                "例如：按治疗组（TRT01P）汇总年龄（AGE）或基线 BMI（BMIBL）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "enum": sorted(ALLOWED_DATASETS)},
                    "group_var": {"type": "string", "description": "分组变量，如 TRT01P、AGEGR1、SEX"},
                    "value_var": {"type": "string", "description": "被统计的数值变量，如 AGE、BMIBL"},
                },
                "required": ["name", "group_var", "value_var"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "frequency",
            "description": (
                "统计某个变量的频数分布（对应 SAS 的 PROC FREQ），**包含缺失值**。"
                "用于了解分类变量的取值分布，如 SEX、RACE、AESEV。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "enum": sorted(ALLOWED_DATASETS)},
                    "var": {"type": "string", "description": "要统计的变量名"},
                    "top_n": {"type": "integer", "description": "最多显示多少个取值，默认 20"},
                },
                "required": ["name", "var"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "count_events",
            "description": (
                "统计不良事件：按 SOC 或 PT 层级给出各治疗组的受试者数与百分比。"
                "**分子是受试者数而不是事件数**（同一受试者同一事件只计一次）。"
                "会自动限定在安全性人群并可选只统计治疗中出现的 AE。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "level": {
                        "type": "string",
                        "enum": ["soc", "pt"],
                        "description": "统计层级：soc=系统器官分类，pt=首选术语",
                    },
                    "treatment_only": {
                        "type": "boolean",
                        "description": "是否只统计治疗中出现的不良事件（TEAE），默认 true",
                    },
                    "top_n": {"type": "integer", "description": "最多返回多少行，默认 20"},
                },
                "required": ["level"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare_datasets",
            "description": (
                "比对两个数据集是否一致（类似 PROC COMPARE），"
                "检查行数、仅一方拥有的记录、以及共有记录的变量差异。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name_a": {"type": "string", "enum": sorted(ALLOWED_DATASETS)},
                    "name_b": {"type": "string", "enum": sorted(ALLOWED_DATASETS)},
                    "keys": {
                        "type": "array", "items": {"type": "string"},
                        "description": "匹配键，如 ['USUBJID','AESEQ']；不填则自动推断",
                    },
                },
                "required": ["name_a", "name_b"],
            },
        },
    },
]

# 工具名 → 实现函数
TOOL_IMPL: dict[str, callable] = {
    "list_datasets": list_datasets,
    "describe_dataset": describe_dataset,
    "run_qc_checks": run_qc_checks,
    "check_subject_consistency": check_subject_consistency,
    "summarize_by_group": summarize_by_group,
    "frequency": frequency,
    "count_events": count_events,
    "compare_datasets": compare_datasets,
}


def execute(name: str, arguments: dict) -> str:
    """执行工具并把结果转成 JSON 字符串（供喂回 LLM）。

    任何异常都会被转成可读的错误信息，**不会让 Agent 崩掉**。
    """
    fn = TOOL_IMPL.get(name)
    if fn is None:
        return json.dumps({"错误": f"未知工具 '{name}'",
                           "可用工具": sorted(TOOL_IMPL)}, ensure_ascii=False)
    try:
        result = fn(**arguments)
        return json.dumps(result, ensure_ascii=False, default=str)[:12000]
    except Exception as exc:  # noqa: BLE001
        log.warning("工具 %s 执行失败: %s", name, exc)
        return json.dumps({"错误": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False)
