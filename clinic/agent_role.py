#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多 Agent 协作（第 22 章）。

本章最核心的一条：**多 Agent 的价值来自"独立性"，不是来自数量。**

所以这个模块里最重要的不是编排器，而是 :class:`IndependenceGuard` ——
它强制检查两个"验证者"有没有偷偷共享上下文。
共享上下文的两个 Agent 做验证，等于自己检查自己。

三种拓扑都提供了最小实现：

* :class:`Supervisor`          —— 监督者：分派 + 汇总
* :class:`Pipeline`            —— 流水线：A 的产物交给 B
* :class:`DualProgrammingPair` —— 对等：两个独立实现 + 确定性比对 ★ 最常用
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

__all__ = [
    "RoleCard", "AgentMessage", "Challenge",
    "Discrepancy", "classify_discrepancy",
    "IndependenceGuard", "IndependenceError",
    "Supervisor", "Pipeline", "DualProgrammingPair", "PairReport",
]


# ===========================================================================
# 1. 角色：契约，不是人设
# ===========================================================================
@dataclass
class RoleCard:
    """一个 Agent 角色的完整定义。

    注意 ``tools`` / ``outputs`` / ``forbidden`` 这三个字段 ——
    它们才是防止"角色重叠、互相甩锅"的关键。
    "你是一个资深统计师"这种描述对工程没有约束力。
    """
    name: str
    goal: str                                    # 一句话目标
    system_prompt: str
    tools: tuple[str, ...] = ()                  # ★ 最小权限：只能看到这些工具
    inputs: tuple[str, ...] = ()                 # 需要哪些输入 key
    outputs: tuple[str, ...] = ()                # 必须产出哪些 key
    forbidden: tuple[str, ...] = ()              # ★ 明确不能做什么
    max_steps: int = 12

    def scope(self) -> str:
        return (f"角色 {self.name} ｜ 可用工具 {len(self.tools)} 个 ｜ "
                f"产出 {list(self.outputs)}")

    def render(self) -> str:
        lines = [f"# 角色：{self.name}", f"目标：{self.goal}", "",
                 self.system_prompt.strip(), ""]
        if self.tools:
            lines.append(f"只能使用这些工具：{', '.join(self.tools)}")
        if self.forbidden:
            lines.append("明确禁止：")
            lines += [f"- {f}" for f in self.forbidden]
        return "\n".join(lines)


# ===========================================================================
# 2. 结构化消息：4 种 kind 就够了
# ===========================================================================
@dataclass
class AgentMessage:
    """Agent 之间的消息。

    **不要用自由文本通信** —— 会同时发生信息丢失和"互相客气"。
    结构化消息强制发送方把话说清楚，尤其是 :class:`Challenge`。
    """
    kind: str                       # request / result / challenge / verdict
    from_role: str
    to_role: str
    payload: dict[str, Any] = field(default_factory=dict)
    refs: tuple[str, ...] = ()      # 引用的产物 key，便于追溯
    ts: float = field(default_factory=time.time)

    def render(self) -> str:
        head = f"[{self.kind}] {self.from_role} → {self.to_role}"
        body = json.dumps(self.payload, ensure_ascii=False, indent=None,
                          default=str)
        if len(body) > 400:
            body = body[:400] + "…"
        refs = f"\n  引用：{list(self.refs)}" if self.refs else ""
        return f"{head}\n  {body}{refs}"

    def to_dict(self) -> dict:
        return {"kind": self.kind, "from": self.from_role, "to": self.to_role,
                "payload": self.payload, "refs": list(self.refs), "ts": self.ts}


@dataclass
class Challenge:
    """一条质疑。**必须带 expected 和 actual** —— 编不出来。

    这是最实用的结构化消息：它把"我觉得不对"变成"我期望 X、实际是 Y、
    受影响的是这些记录"，从而完全可核查。
    """
    target: str                     # 针对哪一项（变量名 / 结论 / 行号）
    reason: str
    expected: Any
    actual: Any
    affected: tuple[str, ...] = ()  # 受影响的记录标识
    severity: str = "major"         # critical / major / minor

    def to_message(self, from_role: str, to_role: str) -> AgentMessage:
        return AgentMessage(kind="challenge", from_role=from_role,
                            to_role=to_role, payload=asdict(self))

    @classmethod
    def from_payload(cls, p: dict) -> "Challenge":
        return cls(target=p.get("target", ""), reason=p.get("reason", ""),
                   expected=p.get("expected"), actual=p.get("actual"),
                   affected=tuple(p.get("affected") or ()),
                   severity=p.get("severity", "major"))


# ===========================================================================
# 3. 差异分级
# ===========================================================================
@dataclass
class Discrepancy:
    """两个独立实现之间的一处差异。**差异是信号，不是错误。**"""
    key: str
    a: Any
    b: Any
    level: str = "major"            # critical / major / minor / explainable
    root_cause: str | None = None
    affected: tuple[str, ...] = ()

    def render(self) -> str:
        tag = {"critical": "★★ 关键", "major": "★ 主要",
               "minor": "· 次要", "explainable": "≈ 口径差异"}.get(self.level, self.level)
        line = f"{tag}  {self.key}：A={_brief(self.a)} / B={_brief(self.b)}"
        if self.affected:
            line += f"\n     受影响 {len(self.affected)} 条：{list(self.affected[:5])}"
        if self.root_cause:
            line += f"\n     根因：{self.root_cause}"
        return line


def _brief(v: Any, n: int = 60) -> str:
    s = str(v)
    return s if len(s) <= n else s[: n - 1] + "…"


def classify_discrepancy(discrepancy: Discrepancy,
                         explainable_rules: dict[str, str] | None = None,
                         impact_ratio: float | None = None) -> Discrepancy:
    """按影响面给差异定级。

    ``explainable_rules`` 是"两种口径都成立"的白名单：命中的差异
    **不需要"修好"，只需要记录下来并说明选择了哪一个**（写进 ADRG）。

    第 13 章 case05 里 ``TRT01A`` 按 ``DM.ACTARM`` 还是按"最高剂量"派生，
    两种口径都说得通、差 12 个人 —— 就是这类。这种差异不需要被消灭。
    """
    rules = explainable_rules or {}
    for key, why in rules.items():
        if key in discrepancy.key:
            discrepancy.level = "explainable"
            discrepancy.root_cause = why
            return discrepancy
    if impact_ratio is None:
        ratio = len(discrepancy.affected) if discrepancy.affected else 0
        discrepancy.level = "major" if ratio else "minor"
        return discrepancy
    if impact_ratio >= 0.05:
        discrepancy.level = "critical"
    elif impact_ratio >= 0.01:
        discrepancy.level = "major"
    elif impact_ratio > 0:
        discrepancy.level = "minor"
    return discrepancy


# ===========================================================================
# 4. 独立性守卫 ★ 本模块最重要的东西
# ===========================================================================
class IndependenceError(Exception):
    """独立性被破坏 —— 双编程的验证价值已经归零。"""


class IndependenceGuard:
    """监控两个"独立实现者"有没有共享上下文。

    **判定依据：谁读了谁的东西。** 所以守卫必须同时记录两件事 ——
    每个实现者读过什么（``record_read``）、产出过什么（``record_produce``）。
    只看"读过什么"是查不出违规的：抄作业的人读的是对方**产出**的文件，
    而那个文件对方从来没有"读"过。这是本类最容易写错的地方。

    用法::

        guard = IndependenceGuard(shared_inputs=["inputs/adsl.csv"])
        guard.declare("impl_a").declare("impl_b")
        guard.record_read("impl_a", "inputs/adsl.csv")        # 共享输入：允许
        guard.record_read("impl_b", "inputs/adsl.csv")        # 共享输入：允许
        guard.record_produce("impl_a", "artifacts/impl_a.csv")
        guard.record_read("impl_b", "artifacts/impl_a.csv")   # ★ 违规
        guard.assert_independent()                            # → 抛异常

    在真实系统里，这条纪律靠架构强制（物理隔离上下文、产物命名空间隔离），
    这里的守卫是它的**可测试版本** —— 把"独立性"变成一条可失败的断言。

    ⚠️ 两条必须说清的局限：

    1. **审计只覆盖被显式记录的路径。** 没记录 ≠ 独立。一份"零违规"的
       审计报告，如果覆盖的来源是 0 个，它什么也没证明 —— 所以 ``report()``
       会把"本次审计覆盖了 N 个来源"一并写出来，N 很小的时候你该怀疑它。
    2. **共享输入要显式声明。** 双方都读 SAP、都读样本数据是合法的；
       没声明就会被记进 ``undeclared`` 提示区（不是违规，但值得补上声明）。
    """

    def __init__(self, shared_inputs: Sequence[str] = ()) -> None:
        self.shared_inputs = set(shared_inputs)
        self.actors: list[str] = []
        self.reads: dict[str, list[str]] = {}
        self.produces: dict[str, list[str]] = {}
        self.violations: list[str] = []

    def declare(self, actor: str) -> "IndependenceGuard":
        if actor not in self.actors:
            self.actors.append(actor)
            self.reads[actor] = []
            self.produces[actor] = []
        return self

    # ------------------------------------------------------------------
    def record_read(self, actor: str, source: str) -> None:
        """记录一次读取。若读的是**别的实现者的产物** → 违规。"""
        self.declare(actor)
        self.reads[actor].append(source)
        for other in self.actors:
            if other == actor:
                continue
            if source in self.produces.get(other, []):
                self._flag(f"{actor} 读取了 {other} 的产物 {source!r}"
                           f" → 独立性被破坏（两条实现路径已合流）")

    def record_produce(self, actor: str, artifact: str) -> None:
        """记录一次产出。若别的实现者已经读过这个路径 → 违规。"""
        self.declare(actor)
        self.produces[actor].append(artifact)
        for other in self.actors:
            if other == actor:
                continue
            if artifact in self.reads.get(other, []):
                self._flag(f"{actor} 产出的 {artifact!r} 已被 {other} 读过"
                           f" → 独立性被破坏（两条实现路径已合流）")

    def _flag(self, msg: str) -> None:
        if msg not in self.violations:
            self.violations.append(msg)

    # ------------------------------------------------------------------
    @property
    def undeclared(self) -> list[str]:
        """双方都读过、却没声明为共享输入的来源（提示，不是违规）。"""
        seen: dict[str, int] = {}
        for actor in self.actors:
            for src in set(self.reads[actor]):
                seen[src] = seen.get(src, 0) + 1
        return sorted(s for s, n in seen.items()
                      if n > 1 and s not in self.shared_inputs)

    @property
    def coverage(self) -> int:
        """本次审计实际覆盖的来源数（去重）。**这个数才是审计的可信度。**"""
        return len({s for a in self.actors for s in self.reads[a]})

    @property
    def independent(self) -> bool:
        return not self.violations

    def assert_independent(self) -> None:
        if self.violations:
            raise IndependenceError(
                "独立性检查失败：\n  - " + "\n  - ".join(self.violations)
                + "\n提示：若确实需要共享该输入，请显式加入 shared_inputs；"
                  "若需要读对方产物，那就不是双编程了。")

    def report(self) -> str:
        lines = ["# 独立性审计", ""]
        for actor in self.actors:
            lines.append(f"- {actor}：读取 {len(self.reads[actor])} 个来源，"
                         f"产出 {len(self.produces[actor])} 个产物")
        lines.append("")
        lines.append(f"- 覆盖来源数：{self.coverage}")
        lines.append("")
        if self.violations:
            lines.append("## 结论：✗ 独立性已破坏")
            lines.append("")
            lines += [f"- ⚠️ {v}" for v in self.violations]
        else:
            lines.append("## 结论：✓ 未发现跨实现读取")
        if self.undeclared:
            lines.append("")
            lines.append("## 提示：双方都读过、但未声明为共享输入")
            lines.append("")
            lines += [f"- {s}" for s in self.undeclared]
            lines.append("")
            lines.append("（若确属共享输入，请显式加入 shared_inputs，"
                         "让审计清单与事实一致。）")
        lines.append("")
        lines.append("> 注意：审计只覆盖被 record_read / record_produce 记录过的路径。"
                     "未记录 ≠ 已证明独立。")
        return "\n".join(lines)


# ===========================================================================
# 5. 三种拓扑
# ===========================================================================
@dataclass
class PairReport:
    """双编程的产出。"""
    name: str
    a_summary: str
    b_summary: str
    discrepancies: list[Discrepancy] = field(default_factory=list)
    independent: bool = True
    coverage: int = 0
    elapsed: float = 0.0

    @property
    def independence_label(self) -> str:
        """审计结论的**三种**状态，不是两种。

        覆盖率 0 时不能标 ✓ —— "没查到"和"查过了没事"是两回事。
        真实项目里最常见的自欺就是：审计脚本一行没记，报告上写着"独立性通过"。
        """
        if self.coverage == 0:
            return "未审计（没有记录任何来源）"
        return "✓ 通过" if self.independent else "✗ 已破坏"

    @property
    def verdict(self) -> str:
        # ★ 独立性优先于差异数量：独立性被破坏时，"零差异"是坏消息不是好消息。
        #   两个人抄同一份错，比对结果必然一致 —— 那只能证明错误被复制了一遍。
        if not self.independent:
            if not self.discrepancies:
                return "不可采信（0 处差异，但独立性已破坏 → 疑似同一份错误被复制）"
            return "不可采信（独立性已破坏，差异数不具参考价值）"
        lv = {d.level for d in self.discrepancies}
        if not self.discrepancies:
            return "一致（两个独立实现未发现差异）"
        if "critical" in lv:
            return "存在关键差异 → 必须人工审查后才能使用"
        if "major" in lv:
            return "存在主要差异 → 建议人工审查"
        if lv == {"explainable"}:
            return "仅口径差异 → 记录进 ADRG 即可"
        return "仅有次要差异 → 可接受"

    def render(self) -> str:
        lines = [f"# 双编程比对报告 · {self.name}", ""]
        lines.append(f"**结论**：{self.verdict}")
        lines.append(f"耗时 {self.elapsed:.2f}s ｜ 独立性 {self.independence_label}"
                     f"（审计覆盖 {self.coverage} 个来源）")
        lines.append("")
        lines.append(f"- 实现者 A：{self.a_summary}")
        lines.append(f"- 实现者 B：{self.b_summary}")
        if self.discrepancies:
            lines.append("")
            lines.append(f"## 差异（{len(self.discrepancies)} 处）")
            for d in self.discrepancies:
                lines.append("- " + d.render())
        else:
            lines.append("")
            lines.append("两个独立实现在所有比对项上完全一致。")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {"name": self.name, "a": self.a_summary, "b": self.b_summary,
                "verdict": self.verdict, "independent": self.independent,
                "coverage": self.coverage,
                "elapsed": round(self.elapsed, 3),
                "discrepancies": [asdict(d) for d in self.discrepancies]}


class DualProgrammingPair:
    """对等拓扑（双编程）—— 临床统计里最实用的多 Agent 模式。

    三条硬约束（对应第 22.7 节）：

    * ``share_inputs=True``   共享输入数据
    * ``share_context=False`` **绝不共享对话历史**
    * ``communication="none"`` 实现阶段零交流

    一旦允许交流，两个实现就会趋同，验证价值归零。
    """

    def __init__(self, name: str,
                 impl_a: Callable[[], Any],
                 impl_b: Callable[[], Any],
                 comparator: Callable[[Any, Any], list[Discrepancy]],
                 summarize_a: Callable[[Any], str] | None = None,
                 summarize_b: Callable[[Any], str] | None = None,
                 src_a: str = "impl_a", src_b: str = "impl_b",
                 shared_inputs: Sequence[str] = ()) -> None:
        self.name = name
        self.impl_a, self.impl_b = impl_a, impl_b
        self.comparator = comparator
        self.summarize_a = summarize_a or (lambda r: _brief(r, 120))
        self.summarize_b = summarize_b or (lambda r: _brief(r, 120))
        self.shared_inputs = tuple(shared_inputs)
        self.guard = IndependenceGuard(shared_inputs=self.shared_inputs)

    # ------------------------------------------------------------------
    def run(self, workdir: str | Path = "outputs/pair",
            reads_a: Sequence[str] = (),
            reads_b: Sequence[str] = ()) -> PairReport:
        """跑两个实现并比对。

        ``reads_a`` / ``reads_b`` 要**如实**填入双方各自读过的来源 ——
        守卫只会检查你告诉它的东西，这里图省事写空，独立性审计就退化成一张
        写得漂亮的空表。本方法会把两个产物路径自动登记为各自的 ``produces``，
        所以"B 读了 A 的产物"这种违规不需要你手工记录。
        """
        workdir = Path(workdir)
        workdir.mkdir(parents=True, exist_ok=True)
        t0 = time.perf_counter()

        # 两个实现各自运行，**互不可见对方产物**
        self.guard.declare("impl_a").declare("impl_b")
        for s in reads_a:
            self.guard.record_read("impl_a", s)
        for s in reads_b:
            self.guard.record_read("impl_b", s)

        res_a = self.impl_a()
        path_a = workdir / f"{self.name}_impl_a.json"
        path_a.write_text(_safe_json(res_a), encoding="utf-8")
        self.guard.record_produce("impl_a", str(path_a))

        res_b = self.impl_b()
        path_b = workdir / f"{self.name}_impl_b.json"
        path_b.write_text(_safe_json(res_b), encoding="utf-8")
        self.guard.record_produce("impl_b", str(path_b))

        # 比对（确定性代码，不是 LLM）
        disps = self.comparator(res_a, res_b)

        independent = self.guard.independent

        (workdir / f"{self.name}_independence.md").write_text(
            self.guard.report(), encoding="utf-8")

        return PairReport(name=self.name,
                          a_summary=self.summarize_a(res_a),
                          b_summary=self.summarize_b(res_b),
                          discrepancies=disps, independent=independent,
                          coverage=self.guard.coverage,
                          elapsed=time.perf_counter() - t0)


def _safe_json(obj: Any) -> str:
    try:
        import pandas as pd
        if isinstance(obj, pd.DataFrame):
            return obj.head(500).to_json(orient="records", force_ascii=False)
    except Exception:                                   # noqa: BLE001
        pass
    return json.dumps(obj, ensure_ascii=False, default=str)


class Supervisor:
    """监督者拓扑：一个协调者分派任务给若干角色，收集结果并汇总。

    风险：协调者可能成为瓶颈，也可能"误解"子任务的返回。
    因此这里强制每个角色都返回**结构化 payload**，而不是自然语言。
    """

    def __init__(self, roles: Sequence[RoleCard]) -> None:
        self.roles = {r.name: r for r in roles}
        self.messages: list[AgentMessage] = []
        self.results: dict[str, Any] = {}

    def dispatch(self, targets: Sequence[str], task: dict[str, Any]) -> list[AgentMessage]:
        out = []
        for name in targets:
            if name not in self.roles:
                raise KeyError(f"未注册的角色：{name}")
            m = AgentMessage(kind="request", from_role="supervisor",
                             to_role=name, payload=task)
            self.messages.append(m)
            out.append(m)
        return out

    def collect(self, role: str, outputs: dict[str, Any],
                evidence: Sequence[str] = ()) -> AgentMessage:
        card = self.roles[role]
        missing = [k for k in card.outputs if k not in outputs]
        if missing:
            raise ValueError(f"角色 {role} 未按契约产出：{missing}（要求 {list(card.outputs)}）")
        m = AgentMessage(kind="result", from_role=role, to_role="supervisor",
                         payload={"outputs": outputs}, refs=tuple(evidence))
        self.messages.append(m)
        self.results.update(outputs)
        return m

    def challenge(self, target: str, reason: str, expected: Any, actual: Any,
                  affected: Sequence[str] = (), severity: str = "major"
                  ) -> AgentMessage:
        """任何角色都可以提出结构化质疑。"""
        c = Challenge(target=target, reason=reason, expected=expected,
                      actual=actual, affected=tuple(affected), severity=severity)
        m = c.to_message("supervisor", "implementer")
        self.messages.append(m)
        return m

    def transcript(self) -> str:
        return "\n".join(m.render() for m in self.messages)


class Pipeline:
    """流水线拓扑：A 的产物作为 B 的输入。

    风险：错误会沿链条放大。所以每一环都应该有**输入契约检查**
    （见 :meth:`stage` 的 ``validate`` 参数）。
    """

    def __init__(self, stages: Sequence[str]) -> None:
        self.stages = list(stages)
        self.log: list[dict[str, Any]] = []

    def run(self, initial: Any,
            handlers: dict[str, Callable[[Any], Any]],
            validators: dict[str, Callable[[Any], str | None]] | None = None
            ) -> Any:
        validators = validators or {}
        data = initial
        for stage in self.stages:
            if stage not in handlers:
                raise KeyError(f"缺少阶段处理器：{stage}")
            t0 = time.perf_counter()
            data = handlers[stage](data)
            check = validators.get(stage)
            problem = check(data) if check else None
            self.log.append({"stage": stage,
                             "elapsed": round(time.perf_counter() - t0, 3),
                             "ok": problem is None, "problem": problem})
            if problem:
                raise ValueError(f"阶段 {stage} 输出未通过契约检查：{problem}")
        return data

    def report(self) -> str:
        lines = ["# 流水线执行报告", ""]
        for r in self.log:
            tag = "✓" if r["ok"] else "✗"
            lines.append(f"{tag} {r['stage']}  {r['elapsed']:.2f}s"
                         + (f"  ← {r['problem']}" if r["problem"] else ""))
        return "\n".join(lines)
