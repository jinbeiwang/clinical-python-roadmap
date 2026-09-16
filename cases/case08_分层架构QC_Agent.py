#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
案例 08 · 分层架构的临床 QC Agent（工程版）
============================================

**目标**：把 `case07` 那个单文件 Agent 拆成**可维护、可审计、可恢复**的工程结构。

**覆盖章节**：第 18 章（Agent 架构设计）、第 20 章（工具调用进阶）

案例 07 与案例 08 的区别
------------------------
======================  =============================  ==============================
                         case07（教学版）                case08（工程版）
======================  =============================  ==============================
代码组织                 单文件，一个 ``run_agent()``    四层分离（编排/工具/领域/基础）
工具契约                 提示词里写一段说明              JSON Schema + 参数校验 + 副作用分级
状态                     散在局部变量里                   ``AgentState``，可 JSON 序列化
安全边界                 靠提示词约束                    ``Policy`` + 白名单 + 副作用闸门
可观测性                 print                            ``Observer``（控制台 / JSONL 审计）
预算控制                 无                              步数 / token / 时间三道闸门
断点恢复                 不可能                          保存 → 加载 → 续跑
======================  =============================  ==============================

**核心结论**：Agent 的"智能"来自 LLM，但"可靠"来自这些**看不见的脚手架**。

运行
----
    python cases/case08_分层架构QC_Agent.py                 # 跑全部 7 个场景
    python cases/case08_分层架构QC_Agent.py --only b        # 只跑某个场景
    python cases/case08_分层架构QC_Agent.py --trace         # 打印完整步骤轨迹

七个场景
--------
a) **契约自检** —— 工具 schema 不合规，Agent 上线前就该发现
b) **正常跑通** —— Mock LLM + 真实工具 + 事实表 + 指标
c) **错误自纠** —— 参数写错 → 结构化错误 → 模型改正后成功
d) **副作用闸门** —— 写操作必须人工确认，"提示词约束"挡不住的东西用代码挡
e) **预算截断** —— 步数超限时**优雅收尾**（给部分结论），而不是抛异常
f) **断点恢复** —— 放宽预算后续跑，不重复已完成的步骤
g) **审计落盘** —— JSONL 轨迹 + 密钥自动脱敏

离线可跑：不需要 API Key，不需要联网。工具**真实执行**，
结论里的数字来自真实返回，不是编造的常量。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import clinic.agent_tools as T                                   # noqa: E402
from clinic import agent_core as core                            # noqa: E402

OUT = BASE / "outputs"


# ==========================================================================
# 第 1 层 · 工具层：把领域函数包装成"带契约、带边界"的工具
# ==========================================================================
ALLOWED = frozenset(T.ALLOWED_DATASETS)

# 检查名（LLM 用的名字）→ qc 模块里的问题类别（领域实现的名字）
# 这一层映射存在的意义：**对外用稳定的抽象名，对内可以随时重构实现**。
CHECK_ALIASES: dict[str, tuple[str, ...]] = {
    "missing_rate": ("全缺失变量",),
    "required_vars": ("必需变量缺失",),
    "key_unique": ("主键唯一性", "主键不可用"),
    "date_pairs": ("日期逻辑错误",),
    "codelist": ("受控术语违规",),
    "range": ("离群值",),
    "whitespace": ("首尾空格",),
}


def build_registry() -> core.ToolRegistry:
    """构建工具注册表。

    注意参数名统一用 ``dataset`` —— 这是**框架与领域层之间的约定**：
    ``ToolRegistry.execute()`` 的数据集白名单只认 ``dataset`` 这个键，
    白名单校验是"物理隔离"，不能靠每个工具自觉。
    """
    reg = core.ToolRegistry()

    # ---------------------------------------------------------------- 只读
    @reg.tool("list_datasets",
              "列出 Agent 可以访问的所有数据集及其行数、变量数。"
              "在开始任何分析前，先用它确认有哪些数据可用。",
              {"type": "object", "properties": {}, "required": [],
               "additionalProperties": False})
    def _list(args: dict) -> dict:
        return T.list_datasets()

    @reg.tool("describe_dataset",
              "查看某个数据集的结构：每列的变量名、类型、缺失率、唯一值数、示例值。"
              "对应 SAS 的 PROC CONTENTS + PROC FREQ 组合。",
              {"type": "object",
               "properties": {
                   "dataset": {"type": "string", "enum": sorted(ALLOWED),
                               "description": "数据集名称，如 adsl / adae / dm"},
                   "max_vars": {"type": "integer", "minimum": 1, "maximum": 200,
                                "description": "最多返回多少个变量，默认 40"}},
               "required": ["dataset"], "additionalProperties": False})
    def _describe(args: dict) -> dict:
        d = T.describe_dataset(args["dataset"])
        n = int(args.get("max_vars") or 40)
        if isinstance(d.get("变量"), list) and len(d["变量"]) > n:
            d = dict(d)
            d["变量"] = d["变量"][:n]
            d["提示"] = f"共 {len(d['变量'])} 列，此处只显示前 {n} 列"
        return d

    @reg.tool("run_qc_checks",
              "对数据集执行标准数据质量检查（必需变量、主键唯一性、日期逻辑、"
              "受控术语、全缺失变量、首尾空格、离群值）。"
              "返回问题总数与按严重性分级的问题清单。",
              {"type": "object",
               "properties": {
                   "dataset": {"type": "string", "enum": sorted(ALLOWED),
                               "description": "数据集名称"},
                   "dataset_type": {"type": "string",
                                    "enum": ["auto", "sdtm", "adam"],
                                    "description": "标准类型；auto 表示按数据集名自动判断"},
                   "checks": {"type": "array",
                              "items": {"type": "string",
                                        "enum": sorted(CHECK_ALIASES)},
                              "description": "只跑指定的检查项；不传则跑全部"},
                   "max_issues": {"type": "integer", "minimum": 1,
                                  "maximum": 200,
                                  "description": "最多返回多少条问题明细，默认 30"}},
               "required": ["dataset"], "additionalProperties": False})
    def _run_qc(args: dict) -> dict:
        res = T.run_qc_checks(args["dataset"],
                              args.get("dataset_type") or "auto")
        want = args.get("checks")
        if want:
            cats = {c for k in want for c in CHECK_ALIASES.get(k, ())}
            res = dict(res)
            res["问题"] = [i for i in res["问题"] if i["类别"] in cats]
            res["问题总数"] = len(res["问题"])
            res["已应用过滤"] = sorted(want)
        n = int(args.get("max_issues") or 30)
        if len(res["问题"]) > n:
            res = dict(res)
            res["问题"] = res["问题"][:n]
            res["明细截断"] = f"仅显示前 {n} 条明细"
        return res

    @reg.tool("check_subject_consistency",
              "检查各数据集里的 USUBJID 是否都存在于 DM 主数据集中，"
              "用于发现'孤儿受试者'这类跨域数据错误。",
              {"type": "object",
               "properties": {
                   "domains": {"type": "array",
                               "items": {"type": "string", "enum": sorted(ALLOWED)},
                               "description": "要检查的域名，默认 ae/ex/ds/adsl"}},
               "required": [], "additionalProperties": False})
    def _consistency(args: dict) -> dict:
        return T.check_subject_consistency(args.get("domains") or None)

    @reg.tool("frequency",
              "统计某个变量的频数分布（对应 PROC FREQ），**包含缺失值**。",
              {"type": "object",
               "properties": {
                   "dataset": {"type": "string", "enum": sorted(ALLOWED),
                               "description": "数据集名称"},
                   "var": {"type": "string",
                           "description": "要统计的变量名，如 TRT01P、SEX、AESEV"},
                   "top_n": {"type": "integer", "minimum": 1, "maximum": 100,
                             "description": "最多显示多少个取值，默认 20"}},
               "required": ["dataset", "var"], "additionalProperties": False})
    def _frequency(args: dict) -> dict:
        return T.frequency(args["dataset"], args["var"],
                           int(args.get("top_n") or 20))

    # ------------------------------------------------------- 写操作（★ 分级）
    @reg.tool("save_report",
              "把核查报告写入文件。这是**写操作**，执行前需要人工确认。",
              {"type": "object",
               "properties": {
                   "filename": {"type": "string",
                                "description": "文件名（只能是文件名，不能带路径）"},
                   "content": {"type": "string", "description": "报告正文（Markdown）"}},
               "required": ["filename", "content"],
               "additionalProperties": False},
              side_effect="write", idempotent=False, tags=("write", "artifact"))
    def _save(args: dict) -> dict:
        name = Path(args["filename"]).name                # ★ 只取文件名，防路径穿越
        if not name.endswith((".md", ".txt")):
            raise ValueError(f"只允许写 .md / .txt，收到 {name!r}")
        target = OUT / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(args["content"], encoding="utf-8")
        return {"已写入": str(target.relative_to(BASE)), "字节数": target.stat().st_size}

    # ------------------------------------------------------ 事实提取规则
    # 让模型"读事实"而不是"回忆数字"（第 21.4 节）
    reg.set_fact_rule("run_qc_checks", lambda a, d: {
        f"{a.get('dataset')}.问题总数": d.get("问题总数"),
        f"{a.get('dataset')}.高优先级问题数": d.get("高优先级问题数"),
        f"{a.get('dataset')}.是否有高优先级问题": not d.get("通过", True),
    })
    reg.set_fact_rule("check_subject_consistency", lambda a, d: {
        "DM受试者总数": d.get("DM 受试者总数"),
        "孤儿受试者问题数": sum(1 for i in d.get("问题", [])
                                if "不在" in str(i.get("详情", ""))),
    })
    reg.set_fact_rule("describe_dataset", lambda a, d: {
        f"{a.get('dataset')}.行数": d.get("行数") or d.get("记录数"),
        f"{a.get('dataset')}.变量数": d.get("列数") or len(d.get("变量") or []),
    })
    return reg


# ==========================================================================
# 第 2 层 · 基础层：一个"会读错误信息"的假模型（演示错误自纠）
# ==========================================================================
class SelfCorrectingMock(core.MockLLMClient):
    """演示**结构化错误为什么值钱**。

    它第一次故意把数据集名写成 ``adcl``（不存在的拼写），
    然后从工具返回的 ``options`` 里读出合法取值并改正 ——
    这正是真实 LLM 在结构化错误下会做的事。

    如果工具只回一句 ``KeyError: 'adcl'``，模型除了瞎猜没有别的选择。
    """

    def __init__(self) -> None:
        super().__init__()
        self._phase = 0
        self.corrected: str | None = None

    def chat(self, messages, tools):
        if self._phase == 0:
            self._phase = 1
            return core.Message.assistant(tool_calls=[core.ToolCall(
                "c1", "describe_dataset", {"dataset": "adcl"})])       # ← 故意写错
        if self._phase == 1:
            self._phase = 2
            last = next((m.content for m in reversed(messages)
                         if m.role == "tool"), "")
            self.corrected = "adsl" if "adsl" in last else "dm"
            return core.Message.assistant(tool_calls=[core.ToolCall(
                "c2", "describe_dataset", {"dataset": self.corrected})])
        return super().chat(messages, tools)         # 之后走默认计划


# ==========================================================================
# 通用装配
# ==========================================================================
def make_agent(llm=None, *, max_steps: int = 25, verbose: bool = True,
               confirm: bool = True, metrics: core.Metrics | None = None,
               audit: Path | None = None,
               allowed: frozenset[str] = ALLOWED) -> tuple[core.Agent, core.Metrics]:
    """按"依赖注入"的方式装配一个 Agent。

    三个外部依赖全部可替换：LLM（真假）、Observer（控制台/JSONL/无）、
    Policy（预算与安全）。**装配代码集中在这里**，业务代码不碰这些细节。
    """
    reg = build_registry()
    policy = core.Policy(max_steps=max_steps, confirm_side_effects=confirm,
                         allowed_datasets=allowed)
    observers: list[core.Observer] = []
    if verbose:
        observers.append(core.ConsoleObserver(verbose=True, color=True))
    if audit:
        observers.append(core.JsonlObserver(audit))
    obs = observers[0] if len(observers) == 1 else _MultiObserver(observers)
    met = metrics or core.Metrics()
    agent = core.Agent(llm or core.MockLLMClient(), reg, policy,
                       observer=obs, metrics=met)
    return agent, met


class _MultiObserver(core.Observer):
    """把多个 Observer 组合成一个（观察者模式的标准做法）。"""

    def __init__(self, items: list[core.Observer]) -> None:
        self.items = items

    def on_start(self, state):
        for o in self.items:
            o.on_start(state)

    def on_tool(self, name, args, result, elapsed):
        for o in self.items:
            o.on_tool(name, args, result, elapsed)

    def on_finish(self, state):
        for o in self.items:
            o.on_finish(state)


def _h(title: str) -> None:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def _facts(state: core.AgentState) -> str:
    if not state.facts:
        return "（无）"
    return "\n".join(f"  · {f.key} = {f.value}   ← {f.source}"
                     for f in state.facts)


# ==========================================================================
# 场景 a · 契约自检
# ==========================================================================
def scenario_a() -> None:
    _h("场景 a · 契约自检：让不合规的工具在**上线前**就暴露")

    reg = build_registry()
    problems = reg.validate()
    print(f"已注册工具：{reg.names()}")
    print(f"契约自检结果：{'✓ 全部通过' if not problems else problems}")

    # 故意写一个"偷懒"的工具，看看自检能不能抓出来
    bad = core.ToolRegistry()
    bad.register(core.ToolSpec(
        name="broken_tool", description="太短",          # ← 描述 < 10 字符
        parameters={"type": "array", "properties": {"x": {}}},   # ← 应为 object
        func=lambda a: {}))
    print("\n故意注册一个不合规工具后：")
    for p in bad.validate():
        print(f"  ✗ {p}")

    print("""
为什么值得在 CI 里跑一遍？
  · 描述过短 → 模型不知道什么时候该调它 → 漏调
  · 缺 additionalProperties=false → 模型会塞不存在的参数 → 静默失效
  · 参数缺 description → 模型只能靠名字猜 → 传错类型
这三类错误在运行时表现为"Agent 有点笨"，很难排查。""")

    # 参数归一化别名：容忍模型把 dataset 写成 name
    reg2 = build_registry()
    print(f"参数归一化：name='ADSL ' → {reg2.normalize_args('describe_dataset', {'dataset': 'ADSL '})}"
          f"   ← 统一小写 + 去空格，避免大小写导致的假失败")


# ==========================================================================
# 场景 b · 正常跑通
# ==========================================================================
def scenario_b(verbose: bool) -> core.AgentState:
    _h("场景 b · 正常跑通：Mock LLM + 真实工具 + 事实表 + 指标")

    goal = "对 ADSL 数据集做质量核查，关注缺失值和主键唯一性问题，并给出结论"
    print(f"任务目标：{goal}\n")

    audit = OUT / "audit" / "case08_trace.jsonl"
    agent, met = make_agent(verbose=verbose, audit=audit)
    state = agent.run(goal)

    print(f"\n【最终结论】\n{state.answer}\n")
    print(f"【结构化事实表】（每轮都重新注入上下文，模型'读'而不是'回忆'）\n"
          f"{_facts(state)}")
    print(f"\n【步骤轨迹】共 {len(state.steps)} 步：")
    for s in state.steps[:12]:
        tag = "→" if s.kind == "tool" else "·"
        print(f"  {tag} [{s.index:02d}] {s.kind:6s} {s.tool or '':26s} "
              f"{s.elapsed:.2f}s {'✗ ' + (s.error or '')[:40] if s.error else ''}")
    print(f"\n【指标】{met.summary()}")
    print(f"【审计】每次工具调用已落盘：{audit.relative_to(BASE)}")
    return state


# ==========================================================================
# 场景 c · 错误自纠
# ==========================================================================
def scenario_c(verbose: bool) -> None:
    _h("场景 c · 错误自纠：参数写错 → 结构化错误 → 模型改正后成功")

    goal = "检查 DM 数据集的结构"
    llm = SelfCorrectingMock()
    agent, _ = make_agent(llm, verbose=verbose)
    state = agent.run(goal)

    bad = next((s for s in state.steps if s.error), None)
    if bad:
        print(f"\n【模型第一次调用的错误返回】\n  {bad.error_type}: {bad.error}")
    print(f"\n【模型改正后的取值】{llm.corrected}")
    ok = [s for s in state.steps if s.tool == "describe_dataset" and not s.error]
    print(f"【describe_dataset 成功调用次数】{len(ok)}")

    print("""
对比一下两种错误信息的"可修复性"：

  ❌ 不结构化：  KeyError: 'adcl'
                 → 模型只能猜，很可能再猜错一次

  ✅ 结构化：    [permission] 数据集 'adcl' 不在允许列表中
                 可选值：['adae','adlbc_shift','adsl','adtte','ae','dm',...]
                 提示：可能是拼写错误；请只用枚举中的值
                 → 模型一次就能改对

这就是第 20 章说的：**错误信息是给模型看的接口**，不是给出错的人的抱怨。""")


# ==========================================================================
# 场景 d · 副作用闸门
# ==========================================================================
def scenario_d(verbose: bool) -> None:
    _h("场景 d · 副作用闸门：写操作必须人工确认")

    plan = [{"tool": "run_qc_checks", "args": {"dataset": "adae"}},
            {"tool": "save_report",
             "args": {"filename": "case08_qc_report.md",
                      "content": "# AE 数据核查报告\n\n（由 Agent 在人工确认后写入）\n"}}]
    target = OUT / "case08_qc_report.md"
    if target.exists():
        target.unlink()                       # 保证这次运行是从"文件不存在"开始的
    llm = core.MockLLMClient(plan=plan)
    agent, _ = make_agent(llm, verbose=verbose)
    state = agent.run("核查 ADAE 并把报告写入文件")

    print(f"\n【执行到写操作时的状态】status = {state.status!r}")
    print(f"【暂停原因】{state.answer}")
    print(f"【文件是否已生成】{target.exists()}   ← 没有确认就不该落盘")

    # ---- 人工确认后：用 confirm_side_effects=False 重放这一步 ----
    print("\n人工确认后，重放这一步（策略里关掉闸门）：")
    reg = build_registry()
    relax = core.Policy(confirm_side_effects=False)
    res = reg.execute("save_report",
                      {"filename": "case08_qc_report.md",
                       "content": "# AE 数据核查报告\n\n（由 Agent 在人工确认后写入）\n"},
                      relax)
    print(f"  ok={res.ok}  data={res.data}")

    # ---- 反面例子：文件名也被约束（防路径穿越 / 防写出可执行文件）----
    print("\n顺手验证一下“文件名”也被约束住：")
    for bad_name in ("../../etc/passwd", "oops.sh", "sub/dir/report.md"):
        r = reg.execute("save_report",
                        {"filename": bad_name, "content": "x"}, relax)
        outcome = r.data.get("已写入") if r.ok else f"{r.error_type}: {r.error}"
        print(f"  {bad_name:24s} → ok={r.ok!s:5s} {str(outcome)[:64]}")
    print("""
三条边界都要写在代码里（而不是提示词里）：
  1. side_effect="write"      → 执行前必须人工确认
  2. Path(...).name           → 只取文件名，把 sub/dir/report.md 压成 report.md
  3. 扩展名白名单 .md/.txt    → 不允许写出可执行文件（oops.sh 被拒）

注意第 2 条的效果：路径被**剥掉**而不是"报错"——
   ../../etc/passwd 变成 passwd，最终又被扩展名白名单挡下，
   两次拦截互为保险。**不要只依赖一层**。

提示词是“建议”，代码里的 if 才是“边界”。""")


# ==========================================================================
# 场景 e · 预算截断（优雅收尾）
# ==========================================================================
def scenario_e(verbose: bool) -> core.AgentState:
    _h("场景 e · 预算截断：步数超限时给部分结论，而不是抛异常")

    goal = "对 ADSL 做质量核查，检查跨域受试者一致性，并汇总治疗组分布"
    print(f"任务目标：{goal}")
    print("预算：max_steps = 2（故意设小，模拟“跑一半超预算”）\n")

    agent, _ = make_agent(verbose=verbose, max_steps=2)
    state = agent.run(goal)

    print(f"\n【状态】{state.status}  步数 {state.budget.steps_used}"
          f"/{state.budget.max_steps}")
    print(f"【返回的结论文本（节选）】\n{state.answer[:600]}")
    print(f"""
为什么必须"优雅收尾"而不是抛异常？
  · 抛异常 → 前面几步真实算出来的结果全丢，调用方拿到一句报错，不知道进展
  · 部分结论 + 明确标注"只覆盖已完成部分" → 人可以判断要不要放宽预算重跑

⚠️ 注意措辞：**"以下是已经完成的部分"** 必须写清楚。
   在临床场景里，一份没标注"未完成"的部分报告，会被误当成完整核查 ——
   这比直接失败更危险。

省下的状态在这里：{OUT / 'case08_state.json'}""")
    state.save(OUT / "case08_state.json")
    print(f"（已保存，大小 { (OUT / 'case08_state.json').stat().st_size } 字节）")
    return state


# ==========================================================================
# 场景 f · 断点恢复
# ==========================================================================
def scenario_f(verbose: bool) -> None:
    _h("场景 f · 断点恢复：放宽预算后续跑，不重复已完成的步骤")

    path = OUT / "case08_state.json"
    if not path.exists():
        print(f"未找到 {path}，请先跑场景 e（--only e）。")
        return

    state = core.AgentState.load(path)
    before_steps = [s.tool for s in state.steps if s.kind == "tool"]
    print(f"从磁盘恢复：request_id={state.request_id}")
    print(f"已有 {len(state.steps)} 步，已调工具 {before_steps}")
    print(f"已确认事实 {len(state.facts)} 条，累计 token {state.budget.tokens_used}")
    print("→ 这些都是**从 JSON 里读回来的**，不是重新算的。\n")

    # 放宽预算：保留已用量，避免审计数字归零
    used, tk = state.budget.steps_used, state.budget.tokens_used
    state.budget = core.Policy(max_steps=25).budget()
    state.budget.steps_used, state.budget.tokens_used = used, tk
    state.budget.start()

    agent, _ = make_agent(verbose=verbose)
    state = agent.run(state.goal, state)

    after = [s.tool for s in state.steps if s.kind == "tool"]
    new = after[len(before_steps):]
    print(f"\n【续跑新增的步骤】{new}   ← 只补做没做完的，不重复 describe/qc")
    print(f"【最终状态】{state.status}，总步数 {state.budget.steps_used}")
    print(f"\n【最终结论（节选）】\n{state.answer[:500]}")

    print(f"""
断点恢复能成立的前提，只有一条：**状态是纯数据**。
  · messages / steps / facts / budget 全部可 JSON 序列化
  · 工具函数、LLM 客户端、Observer 都不在状态里（它们不是数据）
  · 所以"恢复"= 反序列化 + 继续 while 循环，不需要任何特殊逻辑

反过来，如果状态散在闭包、lambda、数据库连接、线程局部变量里，
你就永远拿不回"它当时走到哪了"。""")

    # 再跑一次：验证结论可复现（监管要求）
    print("\n【可复现性检查】把恢复后的状态再跑一次，看事实表是否一致：")
    facts1 = dict(state.fact_map())
    reg = build_registry()
    a2, _ = make_agent(verbose=False)
    s2 = a2.run(state.goal)
    facts2 = dict(s2.fact_map())
    same = facts1 == facts2
    print(f"  第一次事实表：{facts1}")
    print(f"  第二次事实表：{facts2}")
    print(f"  完全一致？**{same}**   ← 这是“结论可复现”的最小验证")


# ==========================================================================
# 场景 g · 审计与脱敏
# ==========================================================================
def scenario_g() -> None:
    _h("场景 g · 审计落盘与密钥脱敏")

    audit = OUT / "audit" / "case08_redact.jsonl"
    if audit.exists():
        audit.unlink()

    print("模拟一次“参数里混进了 token”的工具调用：")
    o = core.JsonlObserver(audit)
    o.on_tool("some_api_call",
              {"url": "https://api.example.com/v2/subjects",
               "api_key": "sk-live-abcdef1234567890",
               "Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
               "nested": {"db_password": "P@ssw0rd!"}},
              core.ToolResult.success({"ok": True}), 0.42)

    print("落盘后的实际内容（脱敏前是危险的原样记录）：")
    print("  " + audit.read_text(encoding="utf-8").strip().replace("\n", "\n  "))
    print("""
注意 finish 事件里的 "tokens" 也被打成了 *** —— 这是**误伤**：
  脱敏规则按"键名包含 token"匹配，把"token 用量"也当成了"token 凭证"。
  必须知道这类误报的存在：真实项目里要么给白名单（tokens/token_count 不脱敏），
  要么接受误报 —— **宁可误报，不可漏报**。反过来（为了少误报而放宽规则）
  才是真正危险的。

为什么把脱敏做在 Observer 里，而不是"记得别打印"？
  · 审计要求"记录完整请求以便追溯" ↔ 安全要求"不泄漏凭证"，两者天然冲突
  · 靠人自觉的结果是：要么漏记（无法追溯），要么泄漏（安全事故）
  · 正确做法是**把脱敏变成管道的固定环节** —— 想落盘，必先过 redact()

生产里还应加上：日志文件权限、轮转策略、异地备份、访问审计。
完整实现见 clinic/agent_http.py 的 redact() / 审计部分（第 23 章）。""")

    print(f"\n【JSONL 轨迹样例（场景 b 已生成）】")
    trace = OUT / "audit" / "case08_trace.jsonl"
    if trace.exists():
        lines = trace.read_text(encoding="utf-8").strip().splitlines()
        for line in lines[:3]:
            print("  " + line[:150])
        print(f"  …… 共 {len(lines)} 行")


# ==========================================================================
def main() -> None:
    ap = argparse.ArgumentParser(
        description="案例 08 · 分层架构的临床 QC Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", default="all",
                    choices=["all", "a", "b", "c", "d", "e", "f", "g"],
                    help="只跑指定场景（默认全部）")
    ap.add_argument("--quiet", action="store_true", help="不打印工具调用过程")
    ap.add_argument("--trace", action="store_true",
                    help="场景 b 结束后打印完整步骤轨迹")
    args = ap.parse_args()

    v = not args.quiet
    if args.only in ("all", "a"):
        scenario_a()
    if args.only in ("all", "b"):
        st = scenario_b(v)
        if args.trace:
            print("\n【完整轨迹】\n" + st.trace(limit=40))
    if args.only in ("all", "c"):
        scenario_c(v)
    if args.only in ("all", "d"):
        scenario_d(v)
    if args.only in ("all", "e"):
        scenario_e(v)
    if args.only in ("all", "f"):
        scenario_f(v)
    if args.only in ("all", "g"):
        scenario_g()

    print("\n" + "=" * 70)
    print("案例 08 结束。下一步：")
    print("  · 案例 10 —— 把多个核查步骤组织成可并行、可重试的任务图（第 19 章）")
    print("  · 案例 11 —— 让两个独立 Agent 互相挑错（双编程范式，第 22 章）")
    print("=" * 70)


if __name__ == "__main__":
    main()
