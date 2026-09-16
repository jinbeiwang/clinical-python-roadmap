#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
案例 07 · 临床数据 QC Agent（LLM + 工具 + 循环）
================================================

**目标**：写通一个**真正意义上的 Agent**，而不是"包装得花哨的 if-else"。

**覆盖教程章节**：第 16 章（Agent 开发入门）

Agent ≠ 脚本
------------
========================  ==========================================
脚本                       你写死的流程：读数据 → 计算 → 输出
LLM 调用                   把文本给模型，拿回文本
**Agent**                  **LLM 自己决定调哪些工具、按什么顺序、调几次**
========================  ==========================================

Agent 的本质 = **LLM + 工具 + 循环**。

两种运行模式
------------
1. **MOCK 模式（默认，离线兜底）**
   不需要任何 API Key、不联网。用一个确定性的"决策器"按预设顺序调用工具，
   把工具**真实计算**的结果收集起来，再生成报告。
   它的价值是：验证「工具能不能用、结果对不对、流程通不通」。

2. **真实 LLM 模式**
   设置 ``OPENAI_API_KEY`` 即自动切换。同一个 Agent 主循环，
   换成真实模型后，**调哪些工具、调几次完全由模型现场决定**。

运行
----
    python cases/case07_临床数据QC_Agent.py                    # Mock 模式
    python cases/case07_临床数据QC_Agent.py --verbose-tools     # 打印完整工具返回

    # 真实 LLM（OpenAI 兼容接口，也适用于 DeepSeek / 通义 / 本地 vLLM）
    pip install openai
    set OPENAI_API_KEY=sk-xxxx                     # Windows CMD
    $env:OPENAI_API_KEY = "sk-xxxx"                # Windows PowerShell
    export OPENAI_API_KEY=sk-xxxx                  # Linux/macOS
    python cases/case07_临床数据QC_Agent.py

    # 指向其他兼容接口
    $env:OPENAI_BASE_URL = "https://api.deepseek.com/v1"
    $env:LLM_MODEL = "deepseek-chat"

安全说明（重要）
----------------
工具层做了**数据集白名单**校验（``clinic/agent_tools.ALLOWED_DATASETS``），
Agent 无法读取白名单以外的任何文件。
**边界必须写在代码里，不能只写在提示词里** —— LLM 是不可信的执行者。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import clinic.agent_tools as T          # noqa: E402


# ==========================================================================
# 1) 客户端：两种模式共用同一个接口 decide(messages) -> 决策
# ==========================================================================
class MockLLMClient:
    """离线兜底客户端。

    **它不做推理**，而是按预设顺序依次调用工具 ——
    这足以验证「工具是否可用、结果是否正确、循环是否能收敛」。
    最后一"步"返回根据真实工具输出拼装的报告，而不是一句空话。

    为什么要保留 Mock 模式？
    ------------------------
    - 内网/合规环境不能访问外部 LLM；
    - 调试工具实现时，你不想每一步都等模型响应；
    - 单元测试需要**确定性**输出（真实 LLM 的输出不可复现）。
    """

    def __init__(self, plan: list[tuple[str, dict]] | None = None):
        self._step = 0
        self._plan = plan or [
            ("list_datasets", {}),
            ("describe_dataset", {"name": "adsl"}),
            ("run_qc_checks", {"name": "adsl", "dataset_type": "adam"}),
            ("run_qc_checks", {"name": "dm", "dataset_type": "sdtm"}),
            ("run_qc_checks", {"name": "ae", "dataset_type": "sdtm"}),
            ("check_subject_consistency", {"domains": ["ae", "ex", "ds", "adsl"]}),
            ("summarize_by_group",
             {"name": "adsl", "group_var": "TRT01P", "value_var": "AGE"}),
            ("frequency", {"name": "adsl", "var": "SEX"}),
            ("count_events", {"level": "soc", "treatment_only": True}),
        ]
        self.transcript: list[dict] = []

    def decide(self, messages: list[dict]) -> dict:
        if self._step >= len(self._plan):
            return {"type": "final", "content": build_mock_report(self.transcript)}
        name, args = self._plan[self._step]
        self._step += 1
        return {"type": "tool_call", "name": name, "arguments": args}


class OpenAIClient:
    """真实 LLM 客户端（OpenAI 兼容接口）。

    同一套 ``decide()`` 接口，所以主循环完全不用改 ——
    **这就是把"客户端"与"主循环"解耦的好处**。
    """

    def __init__(self, model: str | None = None, base_url: str | None = None):
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover
            raise SystemExit(
                "未安装 openai 包。请运行：pip install openai\n"
                "（或者不设置 OPENAI_API_KEY，直接用 Mock 模式）"
            ) from exc

        key = (os.getenv("OPENAI_API_KEY") or os.getenv("DEEPSEEK_API_KEY")
               or os.getenv("ARK_API_KEY"))
        if not key:
            raise SystemExit("未找到 API Key 环境变量。")

        self.model = model or os.getenv("LLM_MODEL", "gpt-4o-mini")
        self.client = OpenAI(api_key=key,
                             base_url=base_url or os.getenv("OPENAI_BASE_URL") or None)

    def decide(self, messages: list[dict]) -> dict:
        resp = self.client.chat.completions.create(
            model=self.model, messages=messages, tools=T.TOOLS,
            tool_choice="auto", temperature=0.1,
        )
        msg = resp.choices[0].message
        if msg.tool_calls:
            tc = msg.tool_calls[0]
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            return {"type": "tool_call", "name": tc.function.name,
                    "arguments": args, "raw": msg}
        return {"type": "final", "content": msg.content or ""}


# ==========================================================================
# 2) 工具执行器（薄薄一层，真正的边界在 agent_tools._load 里）
# ==========================================================================
def execute_tool(name: str, args: dict) -> str:
    """执行工具，返回可喂回 LLM 的 JSON 字符串。任何异常都转成可读信息。"""
    return T.execute(name, args)


# ==========================================================================
# 3) Mock 模式的报告生成：用真实结果拼装，不是写死的套话
# ==========================================================================
def _safe_json(s: str):
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return None


def build_mock_report(transcript: list[dict]) -> str:
    """把工具的真实返回值整理成一份结构化报告。

    ★ 注意：这不是 LLM 写的。它是**确定性规则**生成的。
      这么做的意义在于：把"Agent 的决策"与"报告的呈现"分开看 ——
      真实 LLM 模式下，这一段的活是模型干的，但**数据来源完全一样**
      （都来自工具返回值），所以结论不会跑偏。
    """
    L: list[str] = []
    L.append("# 临床数据质量审查报告（MOCK 模式）")
    L.append("")
    L.append("> 本报告由**确定性规则**根据工具返回的真实计算结果生成，")
    L.append("> 未经 LLM 润色。如需 LLM 自主决策版本，请设置 `OPENAI_API_KEY` 后重跑。")
    L.append("")
    L.append(f"共执行 **{len(transcript)}** 次工具调用。")
    L.append("")

    # ---- 数据全景 ----
    for e in transcript:
        if e["tool"] == "list_datasets":
            r = _safe_json(e["result"])
            if r:
                L.append("## 1. 可访问数据集")
                L.append("")
                L.append("| 数据集 | 可用 | 大小(KB) |")
                L.append("|---|---|---|")
                for d in r.get("数据集", []):
                    L.append(f"| {d['名称']} | {'✓' if d['可用'] else '✗'} "
                             f"| {d.get('文件大小KB')} |")
                L.append("")

    # ---- 数据结构 ----
    for e in transcript:
        if e["tool"] == "describe_dataset" and _safe_json(e["result"]):
            r = _safe_json(e["result"])
            L.append(f"## 2. 数据结构：{r['数据集'].upper()}")
            L.append("")
            L.append(f"- 规模：**{r['行数']} 行 × {r['列数']} 列**")
            top = sorted([v for v in r["变量"] if v["缺失数"] > 0],
                         key=lambda v: -v["缺失率%"])[:8]
            if top:
                L.append("- 缺失最多的变量：")
                L.append("")
                L.append("| 变量 | 缺失数 | 缺失率 |")
                L.append("|---|---|---|")
                for v in top:
                    L.append(f"| {v['变量']} | {v['缺失数']} | {v['缺失率%']}% |")
            else:
                L.append("- 无缺失值。")
            L.append("")

    # ---- QC 检查 ----
    qc_rows = []
    for e in transcript:
        if e["tool"] == "run_qc_checks":
            r = _safe_json(e["result"])
            if not r:
                continue
            qc_rows.append(r)
            L.append(f"### QC 检查：{r['数据集'].upper()}（{r['数据集类型']}）")
            L.append("")
            L.append(f"- 结论：**{'通过（无高优先级问题）' if r['通过'] else '未通过'}**"
                     f"｜问题总数 {r['问题总数']}｜高优先级 {r['高优先级问题数']}")
            L.append("")
            if r["问题"]:
                L.append("| 严重性 | 类别 | 详情 |")
                L.append("|---|---|---|")
                for i in r["问题"]:
                    L.append(f"| {i['严重性']} | {i['类别']} | {str(i['详情'])[:110]} |")
                L.append("")

    # ---- 跨数据集一致性 ----
    for e in transcript:
        if e["tool"] == "check_subject_consistency":
            r = _safe_json(e["result"])
            if not r:
                continue
            L.append("### 跨数据集受试者一致性")
            L.append("")
            L.append(f"- DM 受试者总数：**{r['DM 受试者总数']}**")
            L.append("")
            L.append("| 域 | 受试者数 | 不在DM中(错误) | DM中有但本域无记录(通常正常) |")
            L.append("|---|---|---|---|")
            for d, v in r["各域明细"].items():
                L.append(f"| {d} | {v['受试者数']} | {v['不在DM中']} "
                         f"| {v['DM中有但本域无记录']} |")
            L.append("")

    # ---- 人口学 ----
    for e in transcript:
        if e["tool"] == "summarize_by_group":
            r = _safe_json(e["result"])
            if not r:
                continue
            L.append(f"### 人口学：按 {r['分组变量']} 汇总 {r['统计变量']}")
            L.append("")
            L.append("| 分组 | N | Mean | SD | Median | Min | Max |")
            L.append("|---|---|---|---|---|---|---|")
            for row in r["结果"]:
                L.append("| {} | {} | {} | {} | {} | {} | {} |".format(
                    row.get(r["分组变量"]), row.get("N"), row.get("Mean"),
                    row.get("SD"), row.get("Median"), row.get("Min"),
                    row.get("Max")))
            L.append("")

    # ---- 频数 ----
    for e in transcript:
        if e["tool"] == "frequency":
            r = _safe_json(e["result"])
            if not r:
                continue
            L.append(f"### 频数分布：{r['数据集'].upper()}.{r['变量']}"
                     f"（总记录 {r['总记录数']}）")
            L.append("")
            L.append("| 取值 | 频数 | 占比 |")
            L.append("|---|---|---|")
            for row in r["分布"]:
                L.append(f"| {row['取值']} | {row['频数']} | {row['占比%']}% |")
            L.append("")

    # ---- AE ----
    for e in transcript:
        if e["tool"] == "count_events":
            r = _safe_json(e["result"])
            # ★ 工具可能返回 {"错误": ...}（例如参数不合法）。
            #   报告生成器必须能容忍这种情况 —— 否则一次工具报错
            #   会让整个 Agent 崩掉，而"工具报错"本来就是预期内的分支。
            if not r or "结果" not in r:
                if r and "错误" in r:
                    L.append(f"### 不良事件汇总：工具返回错误 —— {r['错误']}")
                    L.append("")
                continue
            trts = [k for k in (r["结果"][0].keys() if r["结果"] else []) if k != "层级"]
            L.append(f"### 不良事件汇总（{r['统计层级']} 层级，{r['事件范围']}）")
            L.append("")
            L.append(f"- 分母（安全性人群）：`{r['分母（安全性人群）']}`")
            L.append(f"- 有任一 TEAE 的受试者数：`{r['有任一 TEAE 的受试者数']}`")
            L.append("")
            L.append("| 层级 | " + " | ".join(trts) + " |")
            L.append("|---" * (len(trts) + 1) + "|")
            for row in r["结果"][:12]:
                L.append("| " + " | ".join([str(row["层级"])] +
                                           [str(row.get(t, "0")) for t in trts]) + " |")
            L.append("")
            L.append(f"> {r['注意事项']}")
            L.append("")

    # ---- 结论 ----
    L.append("## 结论与后续建议")
    L.append("")
    if qc_rows:
        high = sum(r["高优先级问题数"] for r in qc_rows)
        allissues = sum(r["问题总数"] for r in qc_rows)
        L.append(f"- 本次共检查 {len(qc_rows)} 个数据集，累计 {allissues} 条检查结论，"
                 f"其中**高优先级问题 {high} 条**。")
        if high == 0:
            L.append("- 未发现高优先级问题；数据集可用作分析输入。")
        else:
            L.append("- 存在高优先级问题，**必须先定位根因并修复**，"
                     "修复后重新运行本 Agent 复验。")
    L.append("- 需要人工确认的项目：受控术语版本（MedDRA/WHODrug）"
             "与 define.xml 声明是否一致 —— **Agent 不做这类判断**。")
    L.append("- ★ 所有数值均直接取自工具返回值。在临床场景下，"
             "**任何由模型「凭记忆」给出的数字都是不可接受的**。")
    L.append("")
    return "\n".join(L)


# ==========================================================================
# 4) Agent 主循环 —— 全篇最核心的 20 行
# ==========================================================================
SYSTEM_PROMPT = """你是一位资深临床统计编程专家（CDISC SDTM/ADaM 方向）。

你的任务是审查临床数据集的质量，并给出专业结论。

工作原则：
1. 先了解数据（describe_dataset），再做检查（run_qc_checks），最后才下结论。
2. 所有结论必须有工具返回的数据支撑，**不要凭经验猜测**。
3. 报告问题时要说明：问题是什么、影响什么、建议怎么处理。
4. 明确区分「数据本身的问题」与「符合预期的情况」。
   例如：ADSL 只有 254 人而 DM 有 306 人，这是**正常的**
   （ADSL 只含随机化人群，Screen Failure 不进分析人群），不是数据错误。
5. 如果你需要的工具不存在，直接说明「信息不足」，
   **绝对不要编造数据或凭记忆给出数值**。
6. 最后给出结构化结论：整体质量评价、高优先级问题、建议的后续动作。

输出使用中文。"""

USER_TASK = """请对 CDISC 试点项目（CDISCPILOT01）的以下数据集做一次质量审查：

- ADSL（ADaM 受试者层级数据集）
- DM（SDTM 人口学域）
- AE（SDTM 不良事件域）

要求：
1. 检查数据完整性与跨数据集一致性
2. 汇总安全性人群的人口学特征（按治疗组）
3. 汇总治疗中出现的不良事件（按 SOC，受试者层级）
4. 给出质量结论与后续建议

请自行决定调用哪些工具、按什么顺序。"""


def run_agent(client, verbose: bool = True, max_steps: int = 25,
              preview_len: int = 420) -> tuple[str, list]:
    """Agent 主循环。

    只有四件事：**问模型 → 执行工具 → 把结果喂回去 → 重复**。
    所有复杂度都在"工具实现"里，循环本身必须保持简单。

    Parameters
    ----------
    max_steps : 最大步数上限。**必须有这个上限** ——
        否则一个卡在循环里的 Agent 会把你的 API 额度烧光。
    preview_len : 终端预览工具返回时的截断长度，
        避免一行 JSON 把屏幕刷爆（记录文件里存的是完整内容）。
    """
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": USER_TASK},
    ]
    transcript: list[dict] = []
    if hasattr(client, "transcript"):
        client.transcript = transcript          # Mock 用它生成报告

    for step in range(1, max_steps + 1):
        decision = client.decide(messages)

        if decision["type"] == "final":
            if verbose:
                print(f"\n{'=' * 76}\n  最终报告（第 {step} 步收敛）\n{'=' * 76}")
                print(decision["content"])
            return decision["content"], transcript

        name, args = decision["name"], decision["arguments"]
        if verbose:
            print(f"\n[步骤 {step}] 调用工具：{name}")
            print(f"          参数：{json.dumps(args, ensure_ascii=False)}")

        result = execute_tool(name, args)
        transcript.append({"step": step, "tool": name, "args": args, "result": result})

        if verbose:
            preview = (result if len(result) < preview_len
                       else result[:preview_len] + " …(已截断)")
            print(f"          返回：{preview}")

        # 把「工具调用」与「工具结果」写回对话历史（两种模式的格式不同）
        if decision.get("raw") is not None:                # 真实 LLM
            messages.append(decision["raw"])
            messages.append({"role": "tool",
                             "tool_call_id": decision["raw"].tool_calls[0].id,
                             "content": result})
        else:                                              # Mock
            messages.append({"role": "assistant",
                             "content": f"调用工具 {name}，参数 {args}"})
            messages.append({"role": "user", "content": f"工具返回：{result}"})

    return "（达到最大步数限制，未收敛）", transcript


# ==========================================================================
# main
# ==========================================================================
def main() -> None:
    ap = argparse.ArgumentParser(description="临床数据 QC Agent（Mock / 真实 LLM 双模式）")
    ap.add_argument("--verbose-tools", action="store_true",
                    help="打印工具返回的完整内容（默认截断到 420 字符）")
    ap.add_argument("--max-steps", type=int, default=25, help="最大步数上限")
    args = ap.parse_args()

    real_key = (os.getenv("OPENAI_API_KEY") or os.getenv("DEEPSEEK_API_KEY")
                or os.getenv("ARK_API_KEY"))
    mode = "真实 LLM" if real_key else "MOCK（离线兜底）"

    print("=" * 76)
    print(f"  案例 07 · 临床数据 QC Agent   ——   模式：{mode}")
    print("=" * 76)
    print(f"  可用工具（{len(T.TOOLS)} 个）："
          + "、".join(t['function']['name'] for t in T.TOOLS))
    print(f"  数据边界（白名单）：{sorted(T.ALLOWED_DATASETS)}")

    if not real_key:
        print("""
  ┌─ 当前为 MOCK 模式 ────────────────────────────────────────────────────┐
  │ 不联网、不需要 API Key，用确定性决策序列调用工具，                    │
  │ 但**工具执行的是真实逻辑**，结果与真实模式完全一致。                  │
  │                                                                      │
  │ 想切换到真实 LLM（由模型自行决定调用哪些工具）：                       │
  │   Windows PowerShell:  $env:OPENAI_API_KEY = "sk-xxxx"                │
  │   Windows CMD:         set OPENAI_API_KEY=sk-xxxx                     │
  │   Linux / macOS:       export OPENAI_API_KEY=sk-xxxx                  │
  │   可选：$env:OPENAI_BASE_URL / $env:LLM_MODEL 指向 DeepSeek 等兼容接口 │
  │   依赖：pip install openai                                            │
  └──────────────────────────────────────────────────────────────────────┘""")

    client = OpenAIClient() if real_key else MockLLMClient()
    # 工具返回常常是一大段 JSON；默认截断显示，加 --verbose-tools 看全量
    preview_len = 10 ** 9 if args.verbose_tools else 420
    report, transcript = run_agent(client, verbose=True, max_steps=args.max_steps,
                                   preview_len=preview_len)

    out = BASE / "outputs"
    out.mkdir(exist_ok=True)
    tp = out / "agent_transcript.json"
    rp = out / "agent_report.md"
    tp.write_text(json.dumps(transcript, ensure_ascii=False, indent=2, default=str),
                  encoding="utf-8")
    rp.write_text(report, encoding="utf-8")

    print(f"\n{'=' * 76}")
    print("  输出文件")
    print("=" * 76)
    print(f"  对话记录（可审计）：{tp}   （{len(transcript)} 步）")
    print(f"  审查报告：          {rp}")
    print("""
  ★ 为什么要保存完整的 transcript？
    这是 Agent 能被**质量审查**的前提。
    没有记录 = 无法审计 = 不能用在实际生产。
    记录里应包含：每步调了什么工具、参数是什么、返回什么、模型版本、时间戳。""")


if __name__ == "__main__":
    main()
