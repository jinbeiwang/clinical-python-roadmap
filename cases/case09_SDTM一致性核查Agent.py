#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
案例 09 · SDTM 跨域一致性核查 Agent（规则检索 + 可追溯引用）
=============================================================

**目标**：让 Agent 的每一句结论都能**指回两样东西** ——
一条工具跑出来的数字，和一条写在核查计划里的规则。

**覆盖章节**：第 21 章（记忆与上下文管理）、第 20 章（工具调用进阶）、
第 12 章（SDTM 数据处理）

为什么这个案例值得单独写
------------------------
单域的格式检查（变量存在、类型正确、主键唯一）用脚本就够了。
真正耗时的是**跨域一致性核查**：

    各域的 USUBJID 都能在 DM 里找到吗？
    AE 的开始日期早于首次给药，该不该算 TEAE？
    "DM 有受试者但 AE 域没有记录" 是错误还是正常？

这些判断依赖两样东西：
  1. **数据**（要靠工具真实计算，不能靠模型回忆）
  2. **规则**（写在核查计划 / SOP 里，模型没读过，必须检索出来）

于是这个案例的结构就是：**工具给数字，检索给依据，事实表把它们绑在一起**。

五个场景
--------
a) **知识库与检索** —— 把核查计划切对，按标题加权召回
b) **核查 Agent 跑通** —— 工具 + 规则检索 + 带引用的结论
c) **记忆与上下文预算** —— 三种记忆策略在 10 轮会话下的成本对比
d) **数字可追溯** —— 从结论里的 306 一路回溯到原始数据行，并**重放验证**
e) **规则检索的阈值靠标定** —— 用 15 条样例扫出最优阈值，并守住
   "检索不到就说没找到"的红线

运行
----
    python cases/case09_SDTM一致性核查Agent.py
    python cases/case09_SDTM一致性核查Agent.py --only e

离线可跑：不需要 API Key。工具**真实执行**，数字来自真实数据。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import clinic.agent_tools as T                                   # noqa: E402
from clinic import agent_core as core                            # noqa: E402
from clinic import agent_memory as mem                           # noqa: E402

OUT = BASE / "outputs"
KB_DIR = OUT / "kb"
ALLOWED = frozenset(T.ALLOWED_DATASETS)


# ==========================================================================
# 「外部文档」：一份核查计划（QC Plan）的节选
# ==========================================================================
# 真实项目里，这段内容来自公司的 SOP / 核查计划 / SAP。
# 我们把它当作"知识库文档"，验证 Agent 能不能**检索到规则并引用它**。
QC_PLAN_TEXT = """# 核查计划 · CDISCPILOT01（节选）

## 3. 受试者层面一致性

### 3.1 受试者标识一致性
各数据集的 USUBJID 必须全部存在于 DM 域。

- "某域存在但 DM 中不存在" → **严重级：高**，必须修复（数据链条断裂）。
- "DM 中存在但某域无记录" → **严重级：信息**，通常属正常
  （例如未发生不良事件的受试者，AE 域自然没有记录）。
- 判定不得只看记录数是否相等，必须做集合运算。

### 3.2 受试者数量关系
受试者数量的常规关系（用于交叉核对，不构成单独的错误判定）：

    筛选人数 ≥ 随机化人数 ≥ 安全性人群（SAFFL='Y'）≥ 有效性人群（EFFFL='Y'）

CDISCPILOT01：筛选 306 人，随机化 254 人。
任何一层出现"下层大于上层"，都说明分层标志位（SAFFL / EFFFL）有误。

### 3.3 缺失值与零
SDTM 中空值表示"未收集"，**不允许用 0 代替缺失**。
数值变量为 0 时必须能溯源到 CRF 上的实际取值。

## 4. 日期与时间一致性

### 4.1 治疗中出现的定义（TEAE）
不良事件开始日期（AESTDTC）**早于**首次给药日期（EX 域最早 EXSTDTC）的，
判定为"用药前事件"，不纳入 TEAE 汇总。

日期比较要求精确到日；任一侧日期不完整（缺失日或月）时，
**不得直接比较**，应标记为"无法判定"并人工核查 —— 不允许默认按相等处理。

### 4.2 日期先后关系
- AESTDTC ≤ AEENDTC
- 处置日期（DSSTDTC）不应早于首次给药日期
- 派生自同一来源的两个日期变量必须保持一致

## 5. 严重性分级与处置

| 严重级 | 含义 | 处置 |
|---|---|---|
| 高 | 会导致递交物错误 | 必须修复后才能出表 |
| 中 | 可能影响结论，需确认 | 人工确认并留痕 |
| 低 | 格式/记录问题 | 记录，择机修复 |
| 信息 | 状态记录，不是错误 | 无需处理 |

## 6. 结论与可追溯性要求

### 6.1 每个数字必须有出处
核查报告中出现的**每一个数字**，都必须能追溯到
「工具 → 数据集 → 筛选条件 → 记录」这条链路上的具体位置。

模型或人给出的数字，若无法落到工具返回值上，一律不得写入报告。

### 6.2 规则引用要写到章节号
结论中每条判定所依据的规则，必须写明核查计划或 SAP 的**章节号**，
不允许写"根据相关规定"这类无法核查的表述。

## 7. 报告模板要求

结论按"问题 → 依据 → 处置建议"三段式组织。
未能完成的核查项必须显式列出，**不允许省略**（省略会让报告被误读为全覆盖）。
"""


def build_knowledge_base(min_score: float = 0.25
                         ) -> tuple[list[mem.Chunk], mem.KeywordRetriever]:
    """构建知识库：把核查计划导出为 Markdown，再切 chunk 建索引。

    ★ 关键设计：**"检索"是一个工具，不是一段提示词。**
    把文档塞进 System Prompt 的做法在文档变大后必然失效，
    而且模型引用的是"它记得的"，不是"文档里写的"。

    ``min_score`` 决定"多相关才算找到"。**这个数不能拍脑袋定** ——
    怎么定，见场景 e。
    """
    KB_DIR.mkdir(parents=True, exist_ok=True)
    plan = KB_DIR / "QC_PLAN_v3.md"
    plan.write_text(QC_PLAN_TEXT, encoding="utf-8")

    # 语料 = 核查计划 + 教程里讲方法与陷阱的两章（真实文件）
    paths = [plan,
             BASE / "docs" / "12-SDTM数据处理实战.md",
             BASE / "docs" / "10-日期缺失值格式与数据质量.md",
             BASE / "docs" / "09-合并重塑与分组汇总.md"]
    chunks = mem.load_corpus([p for p in paths if Path(p).exists()], max_chars=1200)
    return chunks, mem.KeywordRetriever(chunks, min_score=min_score)


# ==========================================================================
# 工具层：在案例 08 的基础上，多加一个 search_rules
# ==========================================================================
def build_registry(retriever: mem.KeywordRetriever) -> core.ToolRegistry:
    reg = core.ToolRegistry()

    @reg.tool("list_datasets", "列出可访问的数据集及规模。",
              {"type": "object", "properties": {}, "required": [],
               "additionalProperties": False})
    def _list(args: dict) -> dict:
        return T.list_datasets()

    @reg.tool("run_qc_checks",
              "对数据集执行标准质量检查（必需变量、主键、日期逻辑、受控术语等）。",
              {"type": "object",
               "properties": {
                   "dataset": {"type": "string", "enum": sorted(ALLOWED),
                               "description": "数据集名称"},
                   "dataset_type": {"type": "string",
                                    "enum": ["auto", "sdtm", "adam"],
                                    "description": "标准类型，auto=按名称推断"},
                   "max_issues": {"type": "integer", "minimum": 1,
                                  "maximum": 100,
                                  "description": "最多返回多少条明细，默认 20"}},
               "required": ["dataset"], "additionalProperties": False})
    def _qc(args: dict) -> dict:
        res = T.run_qc_checks(args["dataset"], args.get("dataset_type") or "auto")
        n = int(args.get("max_issues") or 20)
        res = dict(res)
        res["问题"] = mem.cap_rows(res["问题"], n)      # ★ 统一截断并标明"已截断"
        return res

    @reg.tool("check_subject_consistency",
              "检查各域 USUBJID 是否都存在于 DM 中（跨域一致性核查）。",
              {"type": "object",
               "properties": {
                   "domains": {"type": "array",
                               "items": {"type": "string", "enum": sorted(ALLOWED)},
                               "description": "要检查的域，默认 ae/ex/ds/adsl"}},
               "required": [], "additionalProperties": False})
    def _consistency(args: dict) -> dict:
        return T.check_subject_consistency(args.get("domains") or None)

    @reg.tool("search_rules",
              "在核查计划 / SOP / SAP 知识库中检索规则依据。"
              "**凡是要下'是否符合要求'的判断，必须先调用本工具取依据。**"
              "返回结果带章节号，引用时必须照抄章节号。",
              {"type": "object",
               "properties": {
                   "query": {"type": "string",
                             "description": "检索问题，如'USUBJID 一致性 判定规则'"},
                   "top_k": {"type": "integer", "minimum": 1, "maximum": 5,
                             "description": "返回几条依据，默认 3"}},
               "required": ["query"], "additionalProperties": False})
    def _search(args: dict) -> dict:
        hits = retriever.search(args["query"], int(args.get("top_k") or 3))
        good = [(c, s) for c, s in hits if s >= retriever.min_score]
        if not good:                       # ★ 红线：检索不到就明说
            return {"查询": args["query"], "找到": 0, "未找到": True,
                    "说明": "知识库中没有与该问题相关的规定。"
                            "请勿凭推测作答；应列入'未能完成的核查项'。",
                    "最高分": hits[0][1] if hits else 0.0}
        return {"查询": args["query"], "找到": len(good), "未找到": False,
                "依据": [{"引用": c.citation(), "标题": c.title,
                          "相关度": s, "片段": c.text[:600]} for c, s in good]}

    # 事实提取：把关键数字和引用来源一起固化下来
    reg.set_fact_rule("check_subject_consistency", lambda a, d: {
        "DM受试者总数": d.get("DM 受试者总数"),
        "孤儿受试者数": sum(int(v.get("不在DM中") or 0)
                            for v in (d.get("各域明细") or {}).values()),
    })
    reg.set_fact_rule("run_qc_checks", lambda a, d: {
        f"{a.get('dataset')}.问题总数": d.get("问题总数"),
        f"{a.get('dataset')}.高优先级问题数": d.get("高优先级问题数"),
    })
    reg.set_fact_rule("search_rules", lambda a, d: (
        {"规则依据章节": "、".join(x["引用"] for x in d.get("依据", []))}
        if d.get("依据") else {"规则依据章节": "未找到（不得凭推测作答）"}))
    return reg


# ==========================================================================
# 一个"像核查员一样工作"的假模型：先取依据、再取数据、最后带引用作答
# ==========================================================================
class ChecklistMock(core.LLMClient):
    """按真实核查流程走：

    ① 检索规则（"该按什么标准判"）
    ② 跑跨域一致性核查（"数据实际是什么样"）
    ③ 跑单域质量检查（"有没有格式问题"）
    ④ 组织结论：**每条判断都挂上章节号**

    它不做推理（是确定性脚本），但它演示了**正确的工具编排顺序** ——
    真实 LLM 在好的提示词和好的工具描述下，走的也是这个顺序。
    """

    def __init__(self) -> None:
        self._step = 0

    def chat(self, messages, tools):
        plan = [
            ("search_rules", {"query": "USUBJID 受试者标识 一致性 严重性 判定规则"}),
            ("check_subject_consistency", {"domains": ["ae", "ex", "ds", "adsl"]}),
            ("search_rules", {"query": "日期先后关系 部分日期 不允许直接比较"}),
            ("run_qc_checks", {"dataset": "ae"}),
        ]
        if self._step < len(plan):
            name, args = plan[self._step]
            self._step += 1
            return core.Message.assistant(tool_calls=[
                core.ToolCall(f"call_{self._step}", name, args)])
        return core.Message.assistant(self._answer(messages))

    @staticmethod
    def _answer(messages) -> str:
        """从工具返回值里取数字与章节号，拼一份**可核查**的报告。"""
        facts: dict[str, object] = {}
        cites: list[str] = []
        for m in messages:
            if m.role != "tool" or not m.content.strip().startswith("{"):
                continue
            try:
                d = json.loads(m.content)
            except json.JSONDecodeError:
                continue
            if "DM 受试者总数" in d:
                facts["DM 受试者总数"] = d["DM 受试者总数"]
                for k, v in (d.get("各域明细") or {}).items():
                    facts[f"{k}.不在DM中"] = v.get("不在DM中")
            if "依据" in d:
                cites += [x["引用"] for x in d["依据"]]
            if "问题总数" in d:
                facts[f"{d.get('数据集')}.问题总数"] = d["问题总数"]

        orphan = sum(int(v or 0) for k, v in facts.items() if k.endswith("不在DM中"))
        lines = [
            "## 结论：跨域受试者一致性",
            "",
            f"- **DM 受试者总数 {facts.get('DM 受试者总数')} 人**"
            f"（依据：check_subject_consistency 工具返回值）",
            f"- 各域'存在但不在 DM 中'的受试者合计 **{orphan}** 人",
        ]
        if orphan == 0:
            lines.append("  → 判定：**通过**。数据链条完整，无孤儿受试者。")
        else:
            lines.append("  → 判定：**不通过**，需立即修复（见下方依据）。")
        lines += [
            f"- AE 域质量问题 {facts.get('ae.问题总数')} 条（含信息级状态记录）",
            "",
            "### 判定依据（规则出处）",
        ]
        for c in dict.fromkeys(cites):
            lines.append(f"- {c}")
        lines += [
            "",
            "### 说明",
            "- “DM 存在但某域无记录”按规则属正常情况，**不计入错误**；"
            "本结论只统计“某域存在但 DM 不存在”。",
            "- 上述每个数字均来自工具返回，未做任何估算。",
        ]
        return "\n".join(lines)


# ==========================================================================
def _h(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


# ==========================================================================
# 场景 a · 知识库与检索
# ==========================================================================
def scenario_a() -> mem.KeywordRetriever:
    _h("场景 a · 知识库构建与检索：文档切对了，召回才准")
    chunks, ret = build_knowledge_base()

    print(f"知识库：{len(chunks)} 个 chunk，来自 "
          f"{len({c.doc for c in chunks})} 个文档")
    by_doc: dict[str, int] = {}
    for c in chunks:
        by_doc[c.doc] = by_doc.get(c.doc, 0) + 1
    for doc, n in sorted(by_doc.items(), key=lambda x: -x[1]):
        print(f"  · {doc:36s} {n:3d} chunk")

    print("\n切分策略：**按 Markdown 标题切** —— 结构化文档的第一原则。\n"
          "命中标题的 chunk 会加权 0.3、命中章节号的加权 0.2，\n"
          "这就是“关键词检索为什么可解释”：你能说清它为什么排第一。\n\n"
          "⚠️ 上表里教程文档的引用只显示文件名、没有章节号，原因是那些\n"
          "   小标题没有编号（“### 任务 1：…”）。另外教程里有大量示例代码，\n"
          "   代码块里的 `# 注释` 会被朴素的行解析误判成标题 ——\n"
          "   load_corpus() 用围栏标记做了规避，这是实测踩出来的坑。")

    queries = [
        "DM 中有但某域没有记录的受试者算错误吗",
        "USUBJID 一致性 严重性分级",
        "部分日期 缺失 能不能直接比较",
        "受试者数量 安全性人群 关系",
    ]
    for q in queries:
        print(f"\n▶ 检索：{q}")
        for c, s in ret.search(q, top_k=3):
            flag = "✓" if s >= ret.min_score else "✗（低于阈值，不采用）"
            print(f"   [{s:5.2f}] {c.citation():34s} {c.title[:26]:28s} {flag}")
        print(f"   → 采用 {sum(1 for _c, s in ret.search(q, 3) if s >= ret.min_score)} 条")

    print("""
⚠️ 上面用的阈值是库默认的 0.25 —— 它其实**偏低**，会捞回不相关的内容。
   场景 e 会用一组标定样例把它定到 0.40，并讲清为什么"不能拍脑袋"。

为什么不用向量检索？
  · 知识库只有几十个 chunk —— 关键词检索足够；
  · 临床场景要求**可解释**：被审计问"为什么引用了这一条"时，
    你能回答"因为查询里的'一致性'和标题命中"；向量相似度很难解释；
  · 零依赖、离线可用、结果确定（同一问题永远同一答案）。
  文档超过约 200 个 chunk 再考虑向量库，且要保留本方法作为兜底。""")
    return ret


# ==========================================================================
# 场景 b · 核查 Agent 跑通
# ==========================================================================
def scenario_b(retriever: mem.KeywordRetriever,
               verbose: bool) -> core.AgentState:
    _h("场景 b · 核查 Agent：工具给数字，检索给依据")

    reg = build_registry(retriever)
    policy = core.Policy(max_steps=12, allowed_datasets=ALLOWED)
    audit = OUT / "audit" / "case09_trace.jsonl"
    agent = core.Agent(ChecklistMock(), reg, policy,
                       observer=core.ConsoleObserver(verbose=verbose),
                       metrics=(met := core.Metrics()))
    state = agent.run("对 CDISCPILOT01 做跨域一致性核查，并给出带依据的结论")

    print(f"\n【Agent 最终结论】\n{state.answer}")
    print(f"\n【结构化事实表】\n{state.fact_table()}")
    print(f"\n【指标】{met.summary()}")
    print(f"【审计】{audit.relative_to(BASE)}")

    print("""
注意结论里的三个特征（第 21.8 节的标准）：
  1. **有数字** —— 306 / 0，全部来自工具返回值
  2. **有出处** —— 判定规则写到章节号（QC_PLAN_v3 第 3.1 节）
  3. **有反例提示** —— 明确说明"DM 有但某域无记录"不算错误

三者缺一，结论就不可核查。第 3 条尤其重要：
如果只报"AE 域有 81 人缺失"，核查人员会白忙一场 ——
那 81 人只是**没有发生不良事件**，按规则属正常情况。""")
    return state


# ==========================================================================
# 场景 c · 记忆与上下文预算
# ==========================================================================
def scenario_c(retriever: mem.KeywordRetriever) -> None:
    _h("场景 c · 记忆与上下文预算：10 轮核查会话的真实成本")

    # 用**真实工具返回值**构造一段 10 轮会话（不是编造的假数据）
    reg = build_registry(retriever)
    real = {
        "consistency": reg.execute("check_subject_consistency", {}).data,
        "ae_qc": reg.execute("run_qc_checks", {"dataset": "ae"}).data,
        "adsl_qc": reg.execute("run_qc_checks", {"dataset": "adsl"}).data,
        "rules": reg.execute("search_rules",
                             {"query": "受试者标识一致性 判定规则"}).data,
    }

    msgs: list[core.Message] = [core.Message.system("你是临床统计编程核查助手。")]
    turns = [
        ("核查各域受试者一致性", "consistency"),
        ("AE 域有哪些质量问题", "ae_qc"),
        ("ADSL 域呢", "adsl_qc"),
        ("判定规则是怎么规定的", "rules"),
    ]
    for i in range(3):                                   # 重复追问，模拟真实的多轮
        for q, key in turns:
            msgs.append(core.Message.user(f"（第 {i + 1} 轮）{q}"))
            msgs.append(core.Message.assistant(
                tool_calls=[core.ToolCall(f"c{i}{key}", key, {})]))
            msgs.append(core.Message.tool(
                f"c{i}{key}", mem.render_tool_result(real[key], max_chars=2500),
                name=key))
    print(f"会话规模：{len(msgs)} 条消息")

    strategies = [("全量保留 FullMemory", mem.FullMemory()),
                  ("滑窗 WindowMemory(6)", mem.WindowMemory(keep_recent=6)),
                  ("摘要 SummaryMemory(4)", mem.SummaryMemory(keep_recent=4))]
    print(f"\n{'策略':24s} {'轮数':>5s} {'字符数':>9s} {'估算token':>10s} "
          f"{'相对全量':>9s}")
    base = None
    for name, st in strategies:
        s = st.stats(msgs)
        base = base or s["字符数"]
        print(f"{name:24s} {s['轮数']:>5d} {s['字符数']:>9,d} "
              f"{s['估算token']:>10,d} {s['字符数'] / base * 100:>8.1f}%")

    sm = mem.SummaryMemory(keep_recent=4)
    ctx = sm.build(msgs)
    text = "\n".join(m.content for m in ctx)
    print(f"\n【摘要记忆保留了哪些数字】")
    nums = sorted({n for n in re.findall(r"\d+(?:\.\d+)?", text) if len(n) >= 2},
                  key=lambda x: -len(x))[:12]
    print(f"  摘要+近期消息中出现的数字：{nums}")
    print(f"  关键数字是否还在：306 → {'306' in text}，254 → {'254' in text}")
    print(f"\n【摘要文本前 500 字】\n{text[:500]}")

    print("\n【上下文预算分配】")
    print(mem.ContextBudget(total=32_000).report())
    print("\n故意把总额压到 8K 试试（会报警）：")
    print(mem.ContextBudget(total=8_000).report())

    print("""
三个结论：
  1. **全量保留必然超窗** —— 只是早晚问题；10 轮就已经很贵了。
  2. **摘要必须保留数字** —— rule_based_summary 的第一条规则就是"所有数字都保留"。
     在临床场景里，254 变成"约 250"是不可接受的（那是结论本身）。
  3. **预算是分配问题，不是容量问题** —— 把 32K 分给 8 个用途，
     每处都有上限，超了就在那一处截断，而不是让某一处无限膨胀。

⚠️ 滑窗有个真实的坑：窗口边界如果正好切在 assistant 的 tool_call 和
   tool 结果之间，API 会报 400。WindowMemory 的 align 参数就是干这个的。""")


# ==========================================================================
# 场景 d · 数字可追溯
# ==========================================================================
def scenario_d(state: core.AgentState) -> None:
    _h("场景 d · 数字可追溯：从结论里的 306 一路回到原始数据行")

    print("结论里的第一个数字：DM 受试者总数 = 306")
    print("要回答的问题是：**这个 306 是谁算出来的？怎么复现？**\n")

    # ---- ① 从事实表找出来源 ----
    fact = next((f for f in state.facts if f.key == "DM受试者总数"), None)
    print(f"① 事实表记录：{fact.key} = {fact.value}")
    print(f"   出处（source）：{fact.source}")
    print(f"   发生在第 {fact.step} 步")

    # ---- ② 从轨迹里找到那一步，取出当时的参数 ----
    step = next((s for s in state.steps if s.tool == "check_subject_consistency"), None)
    print(f"\n② 轨迹里的那一步：tool={step.tool} args={step.args}")
    print(f"   耗时 {step.elapsed:.3f}s，结果摘要 {str(step.result)[:90]}…")

    # ---- ③ 重放：用同样的参数再跑一次，看数字是否一致 ----
    print("\n③ 重放验证（用轨迹里记下的参数再跑一次）：")
    reg = build_registry(build_knowledge_base()[1])
    replay = reg.execute(step.tool, step.args or {})
    got = (replay.data or {}).get("DM 受试者总数")
    print(f"   重放结果 = {got}   与结论一致：**{got == fact.value}**")

    # ---- ④ 落到原始数据行 ----
    print("\n④ 从工具回到原始数据（工具内部读的是 data/samples/dm.csv）：")
    import pandas as pd
    dm = pd.read_csv(BASE / "data" / "samples" / "dm.csv", dtype=str,
                     low_memory=False)
    n_rows = len(dm)
    n_subj = dm["USUBJID"].nunique()
    print(f"   dm.csv 行数 = {n_rows}，USUBJID 去重数 = {n_subj}")
    print(f"   与结论数字一致：**{n_subj == fact.value}**")
    print("""
这就是"可追溯"的完整链路：

    结论数字 306
      → 事实表 Fact(key, value, source='check_subject_consistency({...})', step=)
        → 轨迹 Step(tool, args, result, elapsed)      ← 当时用的参数
          → 工具实现（clinic/agent_tools.check_subject_consistency）
            → 数据文件 data/samples/dm.csv 的 306 个不重复 USUBJID

四段里任何一段缺失，复核人员就得重做一遍核查。
而只要四段都在，复核只需要 **30 秒**（重放一遍，比对数字）。

⚠️ 注意 source 里存的是**工具的调用参数**，不是"工具名"。
   只记工具名，你无法知道它当时查了哪些域 —— 也就无法重放。""")


# ==========================================================================
# 场景 e · 红线：检索不到就说没找到（阈值靠标定，不靠感觉）
# ==========================================================================
# 标定集：**该命中的问题**（覆盖各个章节）
SHOULD_HIT: list[str] = [
    "DM 中有但某域没有记录的受试者算错误吗",
    "USUBJID 一致性 严重性分级 处置",
    "部分日期 缺失 能不能直接比较",
    "安全性人群 与 随机化人数 是什么关系",
    "TEAE 如何定义 用药前事件怎么处理",
    "每个数字 必须有出处 可以追溯",
    "结论引用要写到章节号",
    "空值 能不能用 0 代替",
]
# 标定集：**不该命中的问题**（知识库里根本没有这些内容）
SHOULD_MISS: list[str] = [
    "PD 与 ADA 免疫原性的剪接分析方法学要求",
    "生物标志物 检测方法 验证 要求",
    "PK 参数 药代动力学 采血点 设计",
    "随机化分层因素与区组大小如何确定",
    "统计分析计划中的多重性调整策略",
    "深度学习 模型 微调 学习率",
    "基因测序 变异注释 流程",
]


def scenario_e(retriever: mem.KeywordRetriever) -> None:
    _h("场景 e · 红线：检索不到就说“没找到”（阈值靠标定，不靠感觉）")

    print("先看看默认阈值（clinic 里给的 0.25）会遇到什么麻烦 ——\n"
          "问一个知识库里**根本没有**的问题：\n")
    q = SHOULD_MISS[0]
    print(f"▶ {q}")
    for c, s in retriever.search(q, top_k=3):
        print(f"   [{s:5.2f}] {c.citation()[:44]:46s} {c.title[:24]}")
    print("""
  ↑ 三条命中的相关度 0.37~0.39，看起来"还行"，
    但内容是**完全不相关**的（报告模板、数据体检清单、pandas 多级索引）。
    如果这些被当成"依据"喂给模型，它很可能据此写出一段
    很有说服力的错误结论 —— 这比明说"没找到"危险得多。

  原因：中文关键词检索按 2-gram 切分，"要求""方法""参数""数据"这类
  高频二字组合几乎在任何文档里都能命中。所以——
  **阈值必须靠标定，不能拍脑袋。**""")

    # ---- 标定：在候选阈值上统计"召回"与"误报" ----
    print("\n【标定过程】用上面 8 + 7 条样例，扫一遍候选阈值：\n")
    print(f"{'阈值':>6s}  {'该命中(召回)':>14s}  {'不该命中(误报)':>16s}  说明")
    best = None
    for th in (0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.65):
        probe = mem.KeywordRetriever(retriever.chunks, min_score=th)
        rec = sum(1 for x in SHOULD_HIT
                  if any(s >= th for _c, s in probe.search(x, 3)))
        fp = sum(1 for x in SHOULD_MISS
                 if any(s >= th for _c, s in probe.search(x, 3)))
        note = ""
        if fp == 0 and rec == len(SHOULD_HIT):
            note = "← 召回满、零误报（选它）"
            best = th if best is None else best
        elif fp == 0:
            note = "零误报，但已经漏掉该命中的"
        elif rec == len(SHOULD_HIT):
            note = "召回满，但会误报"
        print(f"{th:>6.2f}  {rec:>10d}/{len(SHOULD_HIT):<3d}  "
              f"{fp:>12d}/{len(SHOULD_MISS):<3d}  {note}")

    print(f"""
  → 选 **{best:.2f}**：在这个阈值上召回和误报同时最优。
     0.40 以下开始出现误报（0.25 时 7 条里误报 4 条）；
     0.45 开始漏掉"空值能不能用 0 代替"（它的最高分是 0.43）。

  ⚠️ 两点必须说清楚（否则这个标定会误导你）：
     1. **样例太少**（15 条）。真实项目应该上百条，并且覆盖每个章节。
     2. **裕度很薄**：0.43 对 0.40，只差 0.03。所以上线前必须扩大标定集，
        并且把标定集做成**回归测试**（第 24 章）：改切分方式、换模型、
        文档更新后，重跑一遍看召回/误报有没有变化。
        这类阈值是"配"出来的，不是"写"出来的 —— 所以要能一键重跑。""")

    # ---- 用标定出的阈值重跑那个问题 ----
    tuned = mem.KeywordRetriever(retriever.chunks, min_score=best)
    reg = build_registry(tuned)
    print(f"\n【用 min_score={best:.2f} 重跑同一个问题】")
    r = reg.execute("search_rules", {"query": q})
    print(f"  结构化返回：{json.dumps(r.data, ensure_ascii=False)}")
    print(f"  事实表会记下："
          f"{reg.extract_facts('search_rules', {'query': q}, r.data)}")

    print("""
【对照：同一个问题在 0.25 下的返回】
  找到 3 条"依据"，内容是报告模板、数据体检清单、pandas 多级索引 ——
  全都与"免疫原性剪接分析"无关（见本节开头的分数表）。

这就是"红线"的落点 —— 它不是一个提示词，而是**工具里的一个 if**：

    if not good:                       # 低于阈值
        return {"未找到": True,
                "说明": "知识库中没有相关规定。请勿凭推测作答；"
                        "应列入'未能完成的核查项'。"}

为什么必须是默认拒绝而不是"尽量给点东西"：
  通用场景里，模型编一条"看起来对"的规则，用户可能察觉不到；
  临床场景里，一条编造的规则会直接进入递交物 —— 那是监管问题。
  所以：
    · 低于阈值 → 工具直接返回"未找到 + 请勿凭推测作答"
    · 事实表记下"规则依据章节 = 未找到（不得凭推测作答）"
    · 结论里必须列入"未能完成的核查项"（核查计划第 7 节就是这么要求的）
    这三步走完，"不知道"才变成一个**可见的、有人负责的状态**。""")

    # ---- 阈值放宽不会影响正常问题 ----
    print("\n【会不会误伤正常问题？检查场景 a/b 用的那几条询问】")
    for x in ("DM 中有但某域没有记录的受试者算错误吗",
              "USUBJID 一致性 严重性分级",
              "部分日期 缺失 能不能直接比较",
              "受试者数量 安全性人群 关系",
              "日期先后关系 部分日期 不允许直接比较"):
        top = tuned.search(x, 3)
        ok = bool(top) and top[0][1] >= tuned.min_score
        print(f"  [{top[0][1] if top else 0:5.2f}] {'仍命中 ' if ok else '被拦下 '} {x}")
    print("  → 调高阈值后，这些真实问题全部不受影响。")
    print("""
一个反直觉的取舍要记住：
    · 阈值低 → 引了不相关的依据（误报）→ **结论有依据，但依据是错的** ★ 最危险
    · 阈值高 → 该引的没引到（漏报）→ 结论缺依据，但不会错
  在临床场景里，**宁可漏报**。漏掉的会被"未完成项"清单暴露出来，
  误报则会静悄悄地混进递交物。""")


# ==========================================================================
def main() -> None:
    ap = argparse.ArgumentParser(
        description="案例 09 · SDTM 跨域一致性核查 Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", default="all",
                    choices=["all", "a", "b", "c", "d", "e"])
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    v = not args.quiet

    state = None
    ret = None
    if args.only in ("all", "a"):
        ret = scenario_a()
    elif args.only in ("b", "c", "d", "e"):
        ret = build_knowledge_base()[1]        # 静默构建，不重复打印场景 a
    if args.only in ("all", "b", "d"):
        state = scenario_b(ret, v)
    if args.only in ("all", "c"):
        scenario_c(ret)
    if args.only in ("all", "d"):
        scenario_d(state)
    if args.only in ("all", "e"):
        scenario_e(ret)

    print("\n" + "=" * 72)
    print("案例 09 结束。下一步：")
    print("  · 案例 10 —— 把核查步骤组织成可并行、可重试、可断点续跑的任务图")
    print("  · 案例 11 —— 双编程：让两个 Agent 独立实现后互相挑错")
    print("=" * 72)


if __name__ == "__main__":
    main()
