"""
clinic.qc —— 数据质量检查规则

把临床编程里靠"人眼看日志 / 双编程比对"的检查，固化成**可重复运行的代码**。

设计要点
--------
- 每个检查返回结构化的 issue 列表（而不是 print），便于汇总成报告。
- 严重性分三级：``高``（必须修复）/ ``中``（需确认）/ ``信息``（记录状态）。
- 检查规则通过常量声明，便于按项目定制。

对应 SAS 场景
-------------
- ``PROC CONTENTS``  → ``profile``
- ``PROC SORT nodupkey`` → 主键唯一性检查
- 双编程 + ``PROC COMPARE`` → ``compare_frames``
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------
# 检查规则配置
# --------------------------------------------------------------------------

# 各数据集的必需变量（按 CDISC 标准的最小集）
REQUIRED_VARS: dict[str, list[str]] = {
    # SDTM
    "dm": ["STUDYID", "DOMAIN", "USUBJID", "SUBJID", "RFSTDTC", "AGE", "SEX", "RACE"],
    "ae": ["STUDYID", "DOMAIN", "USUBJID", "AESEQ", "AEDECOD", "AEBODSYS", "AESTDTC"],
    "cm": ["STUDYID", "DOMAIN", "USUBJID", "CMSEQ", "CMDECOD", "CMSTDTC"],
    "ex": ["STUDYID", "DOMAIN", "USUBJID", "EXSEQ", "EXDOSE", "EXSTDTC"],
    "ds": ["STUDYID", "DOMAIN", "USUBJID", "DSSEQ", "DSDECOD"],
    "vs": ["STUDYID", "DOMAIN", "USUBJID", "VSSEQ", "VSTESTCD", "VSORRES"],
    "lb": ["STUDYID", "DOMAIN", "USUBJID", "LBSEQ", "LBTESTCD", "LBORRES"],
    # ADaM
    "adsl": ["STUDYID", "USUBJID", "SUBJID", "TRT01P", "TRT01A", "SAFFL"],
    "adae": ["USUBJID", "TRTEMFL", "AEDECOD", "AEBODSYS", "ASTDT"],
    "adtte": ["USUBJID", "PARAMCD", "AVAL", "CNSR"],
    "adlbc": ["USUBJID", "PARAMCD", "AVAL", "ANRIND"],
    "advs": ["USUBJID", "PARAMCD", "AVAL"],
    "adqsadas": ["USUBJID", "PARAMCD", "AVAL"],
}

# 主键（用于唯一性检查）
KEY_VARS: dict[str, list[str]] = {
    "dm": ["USUBJID"], "adsl": ["USUBJID"], "adtte": ["USUBJID", "PARAMCD"],
    "ae": ["USUBJID", "AESEQ"], "adae": ["USUBJID", "AESEQ"], "cm": ["USUBJID", "CMSEQ"],
    "ex": ["USUBJID", "EXSEQ"], "ds": ["USUBJID", "DSSEQ"], "vs": ["USUBJID", "VSSEQ"],
    "lb": ["USUBJID", "LBSEQ"], "adlbc": ["USUBJID", "PARAMCD", "AVISITN", "LBSEQ"],
    "advs": ["USUBJID", "PARAMCD", "AVISITN", "VSSEQ"],
}

# 受控术语（CDISC CT 的常用子集）
CONTROLLED_TERMS: dict[str, dict[str, list[str]]] = {
    "dm": {
        "SEX": ["M", "F", "U", "UNDIFFERENTIATED"],
        "DTHFL": ["Y", "N"],
        "RACE": ["WHITE", "BLACK OR AFRICAN AMERICAN",
                 "AMERICAN INDIAN OR ALASKA NATIVE", "ASIAN",
                 "NATIVE HAWAIIAN OR OTHER PACIFIC ISLANDER",
                 "OTHER", "MULTIPLE"],
    },
    "ae": {
        "AESEV": ["MILD", "MODERATE", "SEVERE"],
        "AESER": ["Y", "N"],
        "AEOUT": ["RECOVERED/RESOLVED", "RECOVERING/RESOLVING",
                  "NOT RECOVERED/NOT RESOLVED", "RECOVERED/RESOLVED WITH SEQUELAE",
                  "FATAL", "UNKNOWN"],
    },
    "adsl": {"SAFFL": ["Y", "N"], "ITTFL": ["Y", "N"], "EFFFL": ["Y", "N"]},
    "adae": {"TRTEMFL": ["Y", "N"], "AESEV": ["MILD", "MODERATE", "SEVERE"]},
}

# 日期逻辑检查：结束日期不得早于开始日期
DATE_PAIRS: dict[str, list[tuple[str, str]]] = {
    "ae": [("AESTDTC", "AEENDTC")],
    "cm": [("CMSTDTC", "CMENDTC")],
    "ex": [("EXSTDTC", "EXENDTC")],
    "adae": [("ASTDT", "AENDT")],
}


# --------------------------------------------------------------------------
# 工具
# --------------------------------------------------------------------------
def _issue(category: str, severity: str, detail: str) -> dict:
    return {"类别": category, "严重性": severity, "详情": detail}


def profile(df: pd.DataFrame, name: str = "数据集") -> pd.DataFrame:
    """快速数据体检（≈ ``PROC CONTENTS`` + 缺失概况）。

    Returns
    -------
    DataFrame
        每行一个变量：dtype / 缺失数 / 缺失率 / 唯一值数 / 示例值
    """
    rows = []
    for c in df.columns:
        s = df[c]
        nn = s.dropna()
        rows.append({
            "变量": c,
            "类型": str(s.dtype),
            "缺失数": int(s.isna().sum()),
            "缺失率%": round(float(s.isna().mean() * 100), 1),
            "唯一值数": int(s.nunique(dropna=True)),
            "示例值": ", ".join(str(v) for v in nn.unique()[:3]),
        })
    out = pd.DataFrame(rows)
    out.attrs["shape"] = df.shape
    out.attrs["name"] = name
    return out


def check_dataset(df: pd.DataFrame, name: str,
                  dataset_type: str = "auto") -> list[dict]:
    """对单个数据集执行标准质量检查。

    Parameters
    ----------
    dataset_type : ``'sdtm'`` / ``'adam'`` / ``'auto'``。
        ``'auto'`` 时按名称是否以 ``ad`` 开头判断。

    Returns
    -------
    list[dict]
        问题列表。每项含 ``类别`` / ``严重性`` / ``详情``。
        **空列表表示未发现"高"严重性问题。**
    """
    key = name.lower().strip()
    issues: list[dict] = []

    # 0) 数据规模（信息）
    issues.append(_issue("数据规模", "信息", f"{name}: {len(df)} 行 × {df.shape[1]} 列"))

    # 1) 必需变量存在性
    required = REQUIRED_VARS.get(key, [])
    missing = [v for v in required if v not in df.columns]
    if missing:
        issues.append(_issue("必需变量缺失", "高",
                             f"{name} 缺少必需变量 {missing}"))

    # 2) 主键唯一性
    keys = KEY_VARS.get(key)
    if keys:
        present = [k for k in keys if k in df.columns]
        if len(present) == len(keys):
            dup_mask = df.duplicated(subset=keys, keep=False)
            n_dup = int(dup_mask.sum())
            if n_dup:
                sample = (df.loc[dup_mask, keys].drop_duplicates().head(3)
                            .to_dict(orient="records"))
                issues.append(_issue(
                    "主键重复", "高",
                    f"{name} 主键 {keys} 有 {n_dup} 条重复记录；示例：{sample}"))
            else:
                issues.append(_issue("主键唯一性", "信息",
                                     f"{name} 主键 {keys} 唯一（{len(df)} 条）"))
        else:
            issues.append(_issue("主键不可用", "中",
                                 f"{name} 主键变量 {keys} 不完整（存在：{present}）"))

    # 3) 日期逻辑
    for start, end in DATE_PAIRS.get(key, []):
        if start in df.columns and end in df.columns:
            s = pd.to_datetime(df[start], errors="coerce")
            e = pd.to_datetime(df[end], errors="coerce")
            n_bad = int(((s.notna()) & (e.notna()) & (e < s)).sum())
            if n_bad:
                issues.append(_issue(
                    "日期逻辑错误", "高",
                    f"{name}: {n_bad} 条记录的 {end} 早于 {start}"))

    # 4) 受控术语
    for var, allowed in CONTROLLED_TERMS.get(key, {}).items():
        if var in df.columns:
            actual = set(df[var].dropna().astype(str).str.strip().unique())
            illegal = sorted(actual - set(allowed))
            if illegal:
                issues.append(_issue(
                    "受控术语违规", "中",
                    f"{name}.{var} 出现非标准取值 {illegal[:8]}"
                    f"（共 {len(illegal)} 种）"))

    # 5) 全缺失变量
    all_na = [c for c in df.columns if df[c].isna().all()]
    if all_na:
        issues.append(_issue("全缺失变量", "中",
                             f"{name} 有 {len(all_na)} 个变量完全缺失: {all_na[:8]}"))

    # 6) 字符首尾空格
    padded = []
    for c in df.columns:
        if pd.api.types.is_numeric_dtype(df[c]) or pd.api.types.is_datetime64_any_dtype(df[c]):
            continue
        s = df[c].dropna().astype(str)
        if len(s) and bool(s.str.strip().ne(s).any()):
            padded.append(c)
    if padded:
        issues.append(_issue("首尾空格", "低",
                             f"{name} 中 {padded[:8]} 存在首尾空格"))

    # 7) 数值变量的异常值（粗筛，便于人工确认）
    for c in df.columns:
        if not pd.api.types.is_numeric_dtype(df[c]):
            continue
        s = df[c].dropna()
        if len(s) < 30:
            continue
        q1, q3 = s.quantile([0.25, 0.75])
        iqr = q3 - q1
        if iqr > 0:
            far = int(((s < q1 - 5 * iqr) | (s > q3 + 5 * iqr)).sum())
            if far:
                issues.append(_issue("离群值", "低",
                                     f"{name}.{c} 有 {far} 个极端值（超出 Q1/Q3 ± 5×IQR）"))
    return issues


def check_subject_consistency(dm: pd.DataFrame,
                              others: dict[str, pd.DataFrame]) -> list[dict]:
    """检查各域的 USUBJID 是否都在 DM 中（真实 QC 场景）。

    Returns
    -------
    list[dict]
        对每个域给出一条信息或问题记录。
    """
    base = set(dm["USUBJID"].dropna().astype(str))
    issues: list[dict] = [
        _issue("受试者基准", "信息", f"DM 中共 {len(base)} 位受试者")
    ]
    for name, df in others.items():
        if "USUBJID" not in df.columns:
            continue
        subj = set(df["USUBJID"].dropna().astype(str))
        orphan = subj - base
        no_rec = base - subj
        if orphan:
            issues.append(_issue(
                "孤儿受试者", "高",
                f"{name} 有 {len(orphan)} 位受试者不在 DM 中："
                f"{sorted(orphan)[:5]}"))
        else:
            issues.append(_issue(
                "受试者一致性", "信息",
                f"{name}: {len(subj)} 位受试者全部在 DM 中；"
                f"{len(no_rec)} 位 DM 中的受试者在 {name} 无记录"
                f"（通常属正常情况：该受试者没有此类记录）"))
    return issues


def compare_frames(a: pd.DataFrame, b: pd.DataFrame, keys: list[str],
                   tolerance: float = 1e-9) -> dict:
    """比对两个 DataFrame（≈ ``PROC COMPARE``）。

    用于端到端验证：例如把 Python 生成的 ADSL 与 SAS 生成的 ADSL 对照。

    Returns
    -------
    dict
        含 ``行数一致`` / ``仅A有`` / ``仅B有`` / ``差异单元格`` 等键。
    """
    a = a.copy(); b = b.copy()
    for df in (a, b):
        for k in keys:
            df[k] = df[k].astype(str)

    set_a = set(map(tuple, a[keys].to_numpy()))
    set_b = set(map(tuple, b[keys].to_numpy()))

    common = set_a & set_b
    ai = a.set_index(keys)
    bi = b.set_index(keys)
    common_idx = [k for k in common]
    if not common_idx:
        return {"行数一致": False, "仅A有": sorted(set_a - set_b)[:5],
                "仅B有": sorted(set_b - set_a)[:5], "差异单元格": {}}

    ai = ai.loc[common_idx]; bi = bi.loc[common_idx]
    shared_cols = [c for c in ai.columns if c in bi.columns]

    diffs: dict[str, list] = {}
    for c in shared_cols:
        x, y = ai[c], bi[c]
        if pd.api.types.is_numeric_dtype(x) and pd.api.types.is_numeric_dtype(y):
            bad = ~np.isclose(x.astype(float), y.astype(float),
                              rtol=tolerance, atol=tolerance, equal_nan=True)
        else:
            bad = x.astype(str).ne(y.astype(str)) & ~(x.isna() & y.isna())
        if bool(bad.any()):
            diffs[c] = list(zip(ai.index[bad][:3], x[bad].tolist()[:3], y[bad].tolist()[:3]))

    return {
        "行数一致": len(a) == len(b),
        "A 行数": len(a), "B 行数": len(b),
        "仅A有": sorted(set_a - set_b)[:5],
        "仅B有": sorted(set_b - set_a)[:5],
        "共同记录数": len(common_idx),
        "差异变量": diffs,
        "结论": "一致" if (len(a) == len(b) and not diffs
                          and set_a == set_b) else "存在差异",
    }


def report(issues: list[dict], name: str = "") -> str:
    """把 issue 列表渲染成可读文本。"""
    high = [i for i in issues if i["严重性"] == "高"]
    mid = [i for i in issues if i["严重性"] == "中"]
    info = [i for i in issues if i["严重性"] == "信息"]

    lines = [f"{'=' * 70}",
             f"QC 报告 {name}  ——  高 {len(high)} / 中 {len(mid)} / 信息 {len(info)}",
             "=" * 70]
    for label, group in (("高", high), ("中", mid), ("信息", info)):
        for it in group:
            lines.append(f"[{label}] {it['类别']:12s} {it['详情']}")
    lines.append(f"\n结论：{'未发现高优先级问题' if not high else f'发现 {len(high)} 个高优先级问题'}")
    return "\n".join(lines)
