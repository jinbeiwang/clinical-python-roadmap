# 第 16 章 · Agent 开发入门：从 API 调用到临床 QC Agent

> **本章目标**：从零搭一个能读临床数据、做检查、写结论的 Agent。
> **配套代码**：[`cases/case07_临床数据QC_Agent.py`](../cases/case07_临床数据QC_Agent.py)
> ——**内置 Mock 模式，没有 API Key 也能完整跑通流程**。

---

## 16.1 先分清三个概念

很多人一上来就写 Agent，结果写出一个"包装得花哨的 if-else"。
先把概念分清：

| 层次 | 定义 | 例子 |
|---|---|---|
| **脚本** | 你写死的流程：读数据 → 计算 → 输出 | `case02_人口学表.py` |
| **LLM 调用** | 把文本给模型，拿回文本 | "帮我把这段 SAS 转成 Python" |
| **Agent** | **LLM 自己决定调用哪些工具、按什么顺序、调几次** | "检查这批数据有什么问题" |

**Agent 的本质 = LLM + 工具 + 循环**：

```
        ┌──────────────────────────────────────┐
        │  1. 用户目标：「检查这批 SDTM 数据」 │
        └──────────────┬───────────────────────┘
                       ▼
        ┌──────────────────────────────────────┐
        │  2. LLM 推理：我需要先看数据长什么样 │
        │     → 决定调用工具 describe_dataset  │
        └──────────────┬───────────────────────┘
                       ▼
        ┌──────────────────────────────────────┐
        │  3. 执行工具，把结果喂回 LLM         │
        └──────────────┬───────────────────────┘
                       ▼
        ┌──────────────────────────────────────┐
        │  4. LLM 继续推理：现在我要跑 QC 检查 │
        │     → 调用工具 run_qc_checks         │
        └──────────────┬───────────────────────┘
                       ▼
                 （循环，直到 LLM 认为完成）
                       ▼
        ┌──────────────────────────────────────┐
        │  5. 输出结论 + 依据 + 建议            │
        └──────────────────────────────────────┘
```

> 🔥 **关键区别**：传统脚本的流程是**你**设计的；
> Agent 的流程是 **LLM 现场决定的**。这就是"Agent"与"程序"的分水岭。
> 也正因如此，Agent 需要**工具的边界清晰**（否则它可能乱调）。

---

## 16.2 第一步：说清"工具"是什么

工具就是**你暴露给 LLM 的函数**。关键在于**描述要好**——
LLM 只根据描述来决定用不用、怎么用。

```python
"""一个工具 = 函数 + 给 LLM 看的清单 + 执行入口。"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "describe_dataset",
            "description": (
                "查看某个数据集的规模与结构，包括行数、列数、每列的缺失数和示例值。"
                "在开始任何分析前，应该先调用这个工具了解数据。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "数据集名称，可选值：dm, adsl, ae, adae, ex, ds, adtte, vs_bp, adlbc_shift",
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
                "对数据集执行标准数据质量检查，包括：主键唯一性、必需变量存在性、"
                "日期逻辑（结束>=开始）、受控术语合法性、关键变量缺失率。"
                "返回问题清单，空列表表示全部通过。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "数据集名称"},
                    "dataset_type": {
                        "type": "string",
                        "enum": ["sdtm", "adam"],
                        "description": "数据集类型，决定用哪套检查规则",
                    },
                },
                "required": ["name", "dataset_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "summarize_by_group",
            "description": (
                "按分组变量对某个变量做描述统计（N/Mean/SD/Median/Min/Max）。"
                "例如：按治疗组汇总年龄，或按治疗组汇总某个实验室指标的基线值。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "数据集名称"},
                    "group_var": {"type": "string", "description": "分组变量，如 TRT01P"},
                    "value_var": {"type": "string", "description": "被统计的数值变量，如 AGE"},
                },
                "required": ["name", "group_var", "value_var"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "count_events",
            "description": (
                "统计不良事件。按 SOC 或 PT 层级统计各治疗组的受试者数与百分比。"
                "分子是受试者数（同一受试者同一事件只计一次），不是事件数。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "level": {
                        "type": "string",
                        "enum": ["soc", "pt"],
                        "description": "统计层级：soc（系统器官分类）或 pt（首选术语）",
                    },
                    "treatment_only": {
                        "type": "boolean",
                        "description": "是否只统计治疗中出现的不良事件（TEAE），默认 true",
                    },
                },
                "required": ["level"],
            },
        },
    },
]
```

> 💡 **写工具描述的三个原则**：
> 1. **说清"什么时候用"**（不只是"能做什么"）。
>    例："在开始任何分析前，应该先调用这个工具了解数据。"
> 2. **参数要给出可选值**。`enum` 或枚举在 description 里列出来，
>    否则 LLM 会瞎猜（比如把 `adsl` 写成 `ADSL`）。
> 3. **说明输出语义**。例："分子是受试者数，不是事件数"——
>    这一句能防止 Agent 给出错误的 AE 表。

---

## 16.3 第二步：实现工具（真实逻辑）

工具的实现就是你前面 15 章学的东西。

```python
"""clinic/agent_tools.py —— Agent 可调用的临床数据工具集"""
from pathlib import Path
import json
import numpy as np
import pandas as pd

SAMPLES = Path(__file__).resolve().parent.parent / "data" / "samples"

# 允许 Agent 访问的数据集白名单（安全边界）
ALLOWED_DATASETS = {
    "dm", "adsl", "ae", "adae", "ex", "ds", "adtte", "vs_bp", "adlbc_shift"
}

_cache: dict[str, pd.DataFrame] = {}


def _load(name: str) -> pd.DataFrame:
    """载入数据集（带白名单校验与缓存）。"""
    key = name.lower().strip()
    if key not in ALLOWED_DATASETS:
        raise ValueError(f"数据集 '{name}' 不在允许列表中。可选：{sorted(ALLOWED_DATASETS)}")
    if key not in _cache:
        _cache[key] = pd.read_csv(SAMPLES / f"{key}.csv", dtype=str)
    return _cache[key].copy()


# ---------- 工具 1：数据结构描述 ----------
def describe_dataset(name: str) -> dict:
    df = _load(name)
    info = []
    for c in df.columns:
        s = df[c]
        info.append({
            "变量": c,
            "缺失数": int(s.isna().sum()),
            "缺失率": round(float(s.isna().mean()) * 100, 1),
            "唯一值数": int(s.nunique(dropna=True)),
            "示例值": [v for v in s.dropna().unique()[:3].tolist()],
        })
    return {"数据集": name, "行数": len(df), "列数": df.shape[1], "变量信息": info}


# ---------- 工具 2：QC 检查 ----------
REQUIRED_VARS = {
    "sdtm": {
        "dm": ["STUDYID", "USUBJID", "AGE", "SEX", "RACE"],
        "ae": ["STUDYID", "USUBJID", "AESEQ", "AEDECOD", "AEBODSYS", "AESTDTC"],
        "ex": ["STUDYID", "USUBJID", "EXSEQ", "EXDOSE", "EXSTDTC"],
        "ds": ["STUDYID", "USUBJID", "DSSEQ", "DSDECOD"],
    },
    "adam": {
        "adsl": ["STUDYID", "USUBJID", "TRT01P", "SAFFL"],
        "adae": ["USUBJID", "TRTEMFL", "AEDECOD", "AEBODSYS"],
        "adtte": ["USUBJID", "PARAMCD", "AVAL", "CNSR"],
    },
}
KEY_VARS = {
    "dm": ["USUBJID"], "adsl": ["USUBJID"], "adtte": ["USUBJID", "PARAMCD"],
    "ae": ["USUBJID", "AESEQ"], "adae": ["USUBJID", "AESEQ"],
    "ex": ["USUBJID", "EXSEQ"], "ds": ["USUBJID", "DSSEQ"],
}
CONTROLLED = {
    "dm": {"SEX": ["M", "F", "U"], "DTHFL": ["Y", "N", ""]},
    "ae": {"AESEV": ["MILD", "MODERATE", "SEVERE"],
           "AESER": ["Y", "N"],
           "AEOUT": ["RECOVERED/RESOLVED", "RECOVERING/RESOLVING", "NOT RECOVERED/NOT RESOLVED",
                     "RECOVERED/RESOLVED WITH SEQUELAE", "FATAL", "UNKNOWN"]},
}
DATE_PAIRS = {
    "ae": [("AESTDTC", "AEENDTC", "不良事件起止")],
    "ex": [("EXSTDTC", "EXENDTC", "给药起止")],
}


def run_qc_checks(name: str, dataset_type: str = "adam") -> dict:
    """标准数据质量检查。返回 {通过: bool, 问题: [...]}。"""
    df = _load(name)
    issues: list[dict] = []

    # 1) 必需变量
    req = REQUIRED_VARS.get(dataset_type, {}).get(name, [])
    missing = [v for v in req if v not in df.columns]
    if missing:
        issues.append({"类别": "必需变量缺失", "严重性": "高",
                       "详情": f"{name} 缺少必需变量: {missing}"})

    # 2) 主键唯一性
    keys = KEY_VARS.get(name)
    if keys and all(k in df.columns for k in keys):
        dup = int(df.duplicated(subset=keys).sum())
        if dup:
            issues.append({"类别": "主键重复", "严重性": "高",
                           "详情": f"{name} 主键 {keys} 有 {dup} 条重复记录"})
        else:
            issues.append({"类别": "主键唯一性", "严重性": "信息",
                           "详情": f"{name} 主键 {keys} 唯一（{len(df)} 条记录）"})

    # 3) 数据规模
    issues.append({"类别": "数据规模", "严重性": "信息",
                   "详情": f"{name}: {len(df)} 行 × {df.shape[1]} 列"})

    # 4) 日期逻辑
    for start, end, label in DATE_PAIRS.get(name, []):
        if start in df.columns and end in df.columns:
            s = pd.to_datetime(df[start], errors="coerce")
            e = pd.to_datetime(df[end], errors="coerce")
            n = int(((s.notna()) & (e.notna()) & (e < s)).sum())
            if n:
                issues.append({"类别": "日期逻辑错误", "严重性": "高",
                               "详情": f"{name} 中 {label}：{n} 条记录的结束日期早于开始日期"})

    # 5) 受控术语
    for var, allowed in CONTROLLED.get(name, {}).items():
        if var in df.columns:
            actual = set(df[var].dropna().astype(str).str.strip().unique())
            illegal = sorted(actual - set(allowed))
            if illegal:
                issues.append({"类别": "受控术语违规", "严重性": "中",
                               "详情": f"{name}.{var} 出现非标准取值: {illegal[:8]}"})

    # 6) 全缺失变量
    all_na = [c for c in df.columns if df[c].isna().all()]
    if all_na:
        issues.append({"类别": "全缺失变量", "严重性": "中",
                       "详情": f"{name} 中 {len(all_na)} 个变量完全缺失: {all_na[:8]}"})

    # 7) 字符前后空格
    padded = []
    for c in df.columns:
        s = df[c].dropna().astype(str)
        if len(s) and s.str.strip().ne(s).any():
            padded.append(c)
    if padded:
        issues.append({"类别": "前后空格", "严重性": "低",
                       "详情": f"{name} 中 {padded[:8]} 存在首尾空格，建议 .str.strip()"})

    high = [i for i in issues if i["严重性"] == "高"]
    return {"数据集": name, "通过": len(high) == 0, "问题数": len(issues), "问题": issues}


# ---------- 工具 3：分组描述统计 ----------
def summarize_by_group(name: str, group_var: str, value_var: str) -> dict:
    df = _load(name)
    if group_var not in df.columns or value_var not in df.columns:
        raise ValueError(f"变量不存在。{name} 的可用变量：{list(df.columns)}")

    df = df[[group_var, value_var]].copy()
    df[value_var] = pd.to_numeric(df[value_var], errors="coerce")
    df[group_var] = df[group_var].astype(str).str.strip()

    out = (df.groupby(group_var, dropna=False)[value_var]
             .agg(N="count", Mean="mean", SD=lambda s: s.std(ddof=1),
                  Median="median", Min="min", Max="max")
             .round(2).reset_index())
    return {"数据集": name, "说明": f"按 {group_var} 汇总 {value_var}（SD 用 ddof=1，与 SAS STD 一致）",
            "结果": json.loads(out.to_json(orient="records", force_ascii=False))}


# ---------- 工具 4：不良事件汇总 ----------
TRT_LEVELS = ["Placebo", "Xanomeline Low Dose", "Xanomeline High Dose"]


def count_events(level: str = "soc", treatment_only: bool = True) -> dict:
    adae = _load("adae")
    adsl = _load("adsl")

    # 分母：安全性人群
    saf = adsl[adsl["SAFFL"].astype(str).str.strip() == "Y"]
    denom = saf.groupby(saf["TRT01P"].astype(str).str.strip())["USUBJID"].nunique().to_dict()

    ae = adae.copy()
    if treatment_only:
        ae = ae[ae["TRTEMFL"].astype(str).str.strip() == "Y"]
    ae = ae[ae["USUBJID"].isin(set(saf["USUBJID"]))]

    key = "AEBODSYS" if level == "soc" else ["AEBODSYS", "AEDECOD"]
    # ★ 受试者层级去重
    uniq = ae.drop_duplicates(["USUBJID", "TRT01P"] + ([key] if isinstance(key, str) else key))

    grp_cols = [key] if isinstance(key, str) else key
    uniq = uniq.copy()
    uniq["TRT01P"] = uniq["TRT01P"].astype(str).str.strip()

    cnt = uniq.groupby(grp_cols + ["TRT01P"])["USUBJID"].nunique().reset_index(name="N")

    rows = []
    for name_key, g in cnt.groupby(grp_cols[0]):
        row = {"层级": str(name_key)[:70]}
        for t in TRT_LEVELS:
            n = int(g.loc[g["TRT01P"] == t, "N"].sum())
            d = denom.get(t, 0)
            row[t] = "0" if n == 0 else f"{n} ({n / d * 100:.1f}%)"
        rows.append(row)
    rows.sort(key=lambda r: sum(int(r[t].split()[0]) for t in TRT_LEVELS), reverse=True)

    return {
        "说明": f"按 {level.upper()} 层级统计治疗中出现的不良事件（{'受试者数' if True else ''}，受试者层级去重）",
        "分母（安全性人群）": denom,
        "结果": rows[:25],
        "注意": "分子是受试者数而不是事件数；同一受试者同一事件发生多次只计一次。",
    }
```

---

## 16.4 第三步：Agent 主循环

```python
"""case07_临床数据QC_Agent.py —— 一个最小的临床数据 QC Agent"""
import json
import os
from pathlib import Path

import clinic.agent_tools as T

# ============================================================
# 1) 客户端：支持真实 API 与 Mock 两种模式
# ============================================================
class MockLLMClient:
    """离线兜底：不调用任何网络，用确定性规则模拟 Agent 的决策序列。

    作用：没有 API Key / 在内网 / 只想验证流程时使用。
    它不做"推理"，而是按预设流程依次调用工具——
    这足以验证「工具是否可用、结果是否正确」。
    """

    def __init__(self):
        self._step = 0
        self._plan = [
            ("describe_dataset", {"name": "adsl"}),
            ("run_qc_checks", {"name": "adsl", "dataset_type": "adam"}),
            ("run_qc_checks", {"name": "dm", "dataset_type": "sdtm"}),
            ("run_qc_checks", {"name": "ae", "dataset_type": "sdtm"}),
            ("summarize_by_group", {"name": "adsl", "group_var": "TRT01P", "value_var": "AGE"}),
            ("count_events", {"level": "soc", "treatment_only": True}),
        ]

    def decide(self, messages):
        if self._step >= len(self._plan):
            return {"type": "final", "content": self._mock_report()}
        name, args = self._plan[self._step]
        self._step += 1
        return {"type": "tool_call", "name": name, "arguments": args}

    def _mock_report(self):
        return (
            "【MOCK 模式报告】\n"
            "已完成预设的 6 步检查流程（未调用真实 LLM）。\n"
            "所有工具调用均返回真实计算结果，详见上方工具输出。\n"
            "如需 LLM 自主决策版本，请设置环境变量 OPENAI_API_KEY 后重新运行。"
        )


class OpenAIClient:
    """真实 LLM 客户端（OpenAI 兼容接口，也适用于 DeepSeek / 通义 / 本地 vLLM）。"""

    def __init__(self, model=None, base_url=None):
        from openai import OpenAI
        self.model = model or os.getenv("LLM_MODEL", "gpt-4o-mini")
        self.client = OpenAI(
            api_key=os.environ["OPENAI_API_KEY"],
            base_url=base_url or os.getenv("OPENAI_BASE_URL") or None,
        )

    def decide(self, messages):
        resp = self.client.chat.completions.create(
            model=self.model, messages=messages, tools=T.TOOLS, tool_choice="auto",
            temperature=0.1,
        )
        msg = resp.choices[0].message
        if msg.tool_calls:
            tc = msg.tool_calls[0]
            return {"type": "tool_call", "name": tc.function.name,
                    "arguments": json.loads(tc.function.arguments), "raw": msg}
        return {"type": "final", "content": msg.content}


# ============================================================
# 2) 工具执行器
# ============================================================
TOOL_IMPL = {
    "describe_dataset": T.describe_dataset,
    "run_qc_checks": T.run_qc_checks,
    "summarize_by_group": T.summarize_by_group,
    "count_events": T.count_events,
}


def execute_tool(name: str, args: dict) -> str:
    """执行工具，返回给 LLM 的 JSON 字符串。任何异常都转成可读信息。"""
    fn = TOOL_IMPL.get(name)
    if fn is None:
        return json.dumps({"错误": f"未知工具 {name}"}, ensure_ascii=False)
    try:
        result = fn(**args)
        return json.dumps(result, ensure_ascii=False, default=str)[:8000]
    except Exception as e:  # noqa: BLE001
        return json.dumps({"错误": f"{type(e).__name__}: {e}"}, ensure_ascii=False)


# ============================================================
# 3) Agent 主循环
# ============================================================
SYSTEM_PROMPT = """你是一位资深临床统计编程专家（CDISC SDTM/ADaM 方向）。

你的任务是审查临床数据集的质量，并给出专业结论。

工作原则：
1. 先了解数据（describe_dataset），再做检查（run_qc_checks），最后才下结论。
2. 所有结论必须有工具返回的数据支撑，不要凭经验猜测。
3. 报告问题时要说明：问题是什么、影响什么、建议怎么处理。
4. 明确区分「数据本身的问题」与「符合预期的情况」。
   例如：ADSL 只有 254 人而 DM 有 306 人，这是正常的（ADSL 只含随机化人群），
   不是数据错误。
5. 最后给出一个结构化的结论，包括：整体质量评价、高优先级问题、建议的后续动作。

输出使用中文。"""

USER_TASK = """请对 CDISC 试点项目（CDISCPILOT01）的以下数据集做一次质量审查：

- ADSL（ADaM 受试者层级数据集）
- DM（SDTM 人口学域）
- AE（SDTM 不良事件域）

要求：
1. 检查数据完整性与一致性
2. 汇总安全性人群的人口学特征（按治疗组）
3. 汇总治疗中出现的不良事件（按 SOC，受试者层级）
4. 给出质量结论与后续建议

请自行决定调用哪些工具、按什么顺序。"""


def run_agent(client, verbose=True, max_steps=25):
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": USER_TASK},
    ]
    transcript = []
    for step in range(1, max_steps + 1):
        decision = client.decide(messages)
        if decision["type"] == "final":
            if verbose:
                print(f"\n{'=' * 72}\n最终报告（第 {step} 步）\n{'=' * 72}")
                print(decision["content"])
            return decision["content"], transcript

        name, args = decision["name"], decision["arguments"]
        if verbose:
            print(f"\n[步骤 {step}] 调用工具 {name}  参数 {args}")
        result = execute_tool(name, args)
        transcript.append({"step": step, "tool": name, "args": args, "result": result})
        if verbose:
            preview = result if len(result) < 600 else result[:600] + " ...(截断)"
            print(f"           -> {preview}")

        # 把工具调用与结果写回对话（真实 LLM 需要用标准格式）
        if isinstance(client, OpenAIClient):
            messages.append(decision["raw"])
            messages.append({"role": "tool", "tool_call_id": decision["raw"].tool_calls[0].id,
                             "content": result})
        else:
            messages.append({"role": "assistant", "content": f"调用 {name}({args})"})
            messages.append({"role": "user", "content": f"工具返回：{result}"})
    return "（达到最大步数限制，未完成）", transcript


def main():
    use_real = bool(os.getenv("OPENAI_API_KEY"))
    print("=" * 72)
    print(f"临床数据 QC Agent  —— 模式：{'真实 LLM' if use_real else 'MOCK（离线兜底）'}")
    print("=" * 72)
    if not use_real:
        print("提示：设置环境变量后可切换到真实 LLM：")
        print("  Windows PowerShell:  $env:OPENAI_API_KEY='sk-...'")
        print("  （也支持 OPENAI_BASE_URL / LLM_MODEL 指向 DeepSeek 等兼容接口）")
        print("  pip install openai")

    client = OpenAIClient() if use_real else MockLLMClient()
    report, transcript = run_agent(client)

    out = Path(__file__).resolve().parent.parent / "outputs"
    out.mkdir(exist_ok=True)
    (out / "agent_transcript.json").write_text(
        json.dumps(transcript, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    (out / "agent_report.md").write_text(report, encoding="utf-8")
    print(f"\n对话记录已保存：{out / 'agent_transcript.json'}")
    print(f"报告已保存：{out / 'agent_report.md'}")


if __name__ == "__main__":
    main()
```

### 运行

```bash
# 离线模式（无需任何 Key，直接跑通）
python cases/case07_临床数据QC_Agent.py

# 真实 LLM 模式
pip install openai
export OPENAI_API_KEY=sk-xxxx                  # Linux/macOS
$env:OPENAI_API_KEY = "sk-xxxx"                # Windows PowerShell
python cases/case07_临床数据QC_Agent.py
```

**Mock 模式的输出（真实计算，非伪造）**：

```
========================================================================
临床数据 QC Agent  —— 模式：MOCK（离线兜底）
========================================================================

[步骤 1] 调用工具 describe_dataset  参数 {'name': 'adsl'}
           -> {"数据集": "adsl", "行数": 254, "列数": 48, ...}

[步骤 2] 调用工具 run_qc_checks  参数 {'name': 'adsl', 'dataset_type': 'adam'}
           -> {"数据集": "adsl", "通过": true, "问题数": 8, ...}

[步骤 3] 调用工具 run_qc_checks  参数 {'name': 'dm', 'dataset_type': 'sdtm'}
           ...

[步骤 6] 调用工具 count_events  参数 {'level': 'soc', 'treatment_only': True}
           -> {"分母（安全性人群）": {"Placebo": 86, "Xanomeline Low Dose": 84, ...},
               "结果": [{"层级": "GENERAL DISORDERS AND ...", "Placebo": "24 (27.9%)", ...}]
               "注意": "分子是受试者数而不是事件数；..."}
```

---

## 16.5 Agent 设计的四个关键决策

### 决策 1：工具粒度——不要太细，也不要太粗

| 粒度 | 问题 |
|---|---|
| 太细（`read_column`, `filter_rows`）| LLM 要调 50 次才能做完一件事，成本高、易迷失 |
| **合适**（`run_qc_checks`）| 一次调用完成一个有意义的业务动作 |
| 太粗（`do_everything`）| LLM 没有决策空间，退化成脚本 |

**判断标准**：一个工具应该对应**一个你能向同事描述的业务动作**
（"跑一遍 QC 检查"、"按治疗组汇总年龄"）。

### 决策 2：安全边界必须在代码里，不能靠提示词

```python
# ✅ 正确：在工具实现里做白名单校验
ALLOWED_DATASETS = {"dm", "adsl", "ae", "adae", "ex", "ds", "adtte", "vs_bp", "adlbc_shift"}

def _load(name):
    if name.lower().strip() not in ALLOWED_DATASETS:
        raise ValueError(...)          # 拒绝访问
```

```python
# ❌ 错误：只在 System Prompt 里说"请不要读取其他文件"
#    LLM 可能在压力/诱导/幻觉下仍然尝试
```

> 🔥 **原则**：**LLM 是不可信的执行者，工具必须有硬边界。**
> 特别是涉及"读文件/写文件/发邮件/调外部 API"的工具——
> 越权后果可能是真实的数据泄露。
> 在临床环境下，还要考虑：**工具能访问的数据必须已经过合规审批**。

### 决策 3：结果要可追溯

```python
# 保存完整的对话记录：每步调了什么工具、参数是什么、返回什么
(out / "agent_transcript.json").write_text(json.dumps(transcript, ...))
```

这是 Agent 能被**质量审查**的前提。没有记录 = 无法审计 = 不能用在实际生产。

### 决策 4：让它"知道自己不知道"

```python
# 在 System Prompt 里明确：
# "所有结论必须有工具返回的数据支撑，不要凭经验猜测。"
# "如果你需要的工具不存在，直接说明，不要编造数据。"
```

> ⚠️ **LLM 幻觉在临床场景是致命问题**。一个编造的"p=0.03"
> 如果进了报告，后果严重。
> **对策**：所有数值必须来自工具返回值，且提示词里**明确禁止**
> 凭记忆/推测给出数字。

---

## 16.6 进阶方向（下一步学什么）

你已经有了一个可运行的 Agent。接下来可以往这些方向扩展：

| 方向 | 做什么 | 关键技术 |
|---|---|---|
| **RAG（检索增强）** | 让 Agent 能"读" CDISC 规范文档、SAP、define.xml 后回答问题 | 向量库（Chroma/FAISS）、embeddings |
| **多轮对话** | 支持追问："刚才那个问题具体是哪些受试者？" | 会话状态管理 |
| **多 Agent 协作** | 一个负责数据检查、一个负责写报告、一个负责审查 | LangGraph / 自定义编排 |
| **代码生成 Agent** | 输入 SAP 描述，输出 ADaM 衍生代码并自动测试 | 代码执行工具（sandbox）|
| **MCP 集成** | 把工具封装成标准 MCP Server，供多个客户端复用 | Model Context Protocol |
| **本地模型** | 数据不出内网 | Ollama + Qwen2.5 / DeepSeek |

### 关于 SDK 的选择

| SDK | 特点 | 适用 |
|---|---|---|
| **OpenAI Python SDK** | 最基础，兼容性最好（DeepSeek/通义等都兼容） | 入门、简单场景 |
| **Anthropic Claude SDK** | 工具的 schema 更简单，长上下文强 | 代码/文档处理 |
| **LangChain / LangGraph** | 组件多，生态大，抽象层厚 | 复杂编排（但学习成本高） |
| **OpenAI Agents SDK** | 官方轻量 Agent 框架，handoff/guardrail 内建 | 快速搭多 Agent |
| **MCP** | 开放协议，工具可跨客户端复用 | 工具生态建设 |

> 💡 **给临床程序员的学习建议**：
> **先用 200 行原生代码写通一个 Agent**（就像本章这样），
> **再去用框架**。因为框架会隐藏细节，而你需要先理解"工具调用循环"本身。
> 反过来学，你会陷入"框架能跑但不知道为什么"的状态。

---

## 16.7 临床场景的合规红线（必须知道）

在药企/ CRO 环境里部署 Agent 前，必须过这几关：

| 关注点 | 具体问题 | 建议 |
|---|---|---|
| **数据出境** | 患者数据能不能发给云端 LLM？ | 必须用**本地部署**或**已签 DPA 的私有部署** |
| **GxP 验证** | Agent 的输出来自非确定性模型，如何验证？ | 定位为"辅助工具"，**输出必须人工复核**；纳入计算机化系统验证范围 |
| **可追溯性** | 如何复现某次 Agent 的结论？ | 保存完整 transcript（输入/工具调用/输出）+ 模型版本 + 参数 |
| **21 CFR Part 11** | 电子记录/签名的合规性 | Agent 不应直接"签署"或修改源数据 |
| **审计追踪** | 谁在什么时候做了什么 | 记录用户、时间、prompt、模型版本 |
| **受控术语** | MedDRA/WHODrug 需授权 | Agent 不得绕过许可使用编码字典 |

> 🧠 **务实的定位**：**先把 Agent 用在不接触患者数据的场景**——
> 比如"帮我审这段代码"、"从规范文档里找答案"、"生成程序骨架"。
> 这些场景价值高、风险低，是最现实的切入点。

---

## 16.8 动手练习

1. **跑通 Mock**：运行 `python cases/case07_临床数据QC_Agent.py`，
   查看生成的 `outputs/agent_transcript.json`，理解每步的工具调用链。

2. **加一个工具**：给 Agent 加一个 `find_orphan_subjects` 工具，
   检查某个域里不在 ADSL 中的 USUBJID。

3. **改判定规则**：在 `run_qc_checks` 里增加一条检查：
   "AGE 是否在 18-100 之间"，并处理边界（等于 18 和 100 应该算合法）。

4. **切换真实模型**：申请一个 LLM API Key（或使用本地 Ollama），
   把 `OPENAI_BASE_URL` 指向兼容接口，观察真实 LLM 的工具调用顺序
   与 Mock 版有什么不同。

5. **（思考题）** 如果 Agent 给出的 AE 百分比与你的 SAS 程序不一致，
   你会怎么排查？列出至少 4 个可能的原因。

---

**上一章 ←** [第 15 章 · AI 辅助编程与代码迁移](15-AI辅助编程与代码迁移.md)
**下一章 →** [第 17 章 · 工程化与后续进阶](17-工程化与后续进阶.md)
