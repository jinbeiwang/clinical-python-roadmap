#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""任务规划与调度（第 19 章）。

把"一个目标"变成一张**可执行、可并行、可重试的任务图**。

三个设计决定，每个都有明确理由：

1. :class:`Task` **不持有函数引用** —— 只放 ``tool`` 名与 ``args``。
   这样任务图可以 JSON 序列化：能存盘、能断点恢复、能把计划发给同事审阅。
2. **执行前必做拓扑排序** —— 循环依赖在写代码时看不出来，
   只有跑一次才会暴露。报错成本 1 秒，跑起来再发现的成本是半天。
3. **部分失败必须显式汇总** —— 一个"看起来完整但漏了 3 项检查"的报告，
   比一个"明确写着 3 项没跑"的报告危险得多。

任务状态机
----------
::

    pending ──▶ running ──┬──▶ done        成功
                          ├──▶ failed      失败（重试用尽 / 不可重试错误）
                          ├──▶ waiting     ★ 命中人工确认点，等确认后重跑
                          └──▶ skipped     前置任务失败，本轮未执行

``waiting`` 与 ``skipped`` 必须分开，这决定了**断点续跑的正确性**：

- ``skipped``：本轮因依赖失败而没跑 —— 依赖修好后应该重跑；
- ``waiting``：等人工确认 —— 人确认后应该重跑；
- ``done`` / ``failed``：已到终态 —— **默认不重跑**（否则"断点续跑"就退化成
  "整批重跑"了），除非显式设 ``PlanPolicy(rerun_failed=True)``。
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from clinic.agent_core import Policy, ToolRegistry, ToolResult

__all__ = [
    "PlanError", "PlanCycleError",
    "Task", "Plan", "PlanPolicy",
    "topological_order", "levels", "execute_plan", "render_plan",
]


# --------------------------------------------------------------------- 异常
class PlanError(Exception):
    """任务图本身有问题（依赖不存在、id 重复等）。"""


class PlanCycleError(PlanError):
    """存在循环依赖。**必须在执行前抛出**。"""


# --------------------------------------------------------------------- 任务
@dataclass
class Task:
    """一个可调度的任务。

    ``depends_on`` 是显式依赖声明 —— 这正是 DAG 相对于
    "按文件顺序 %include" 的核心差别（第 19.9 节）。
    """
    id: str
    title: str
    tool: str = ""                          # 要调用的工具名（空 = 纯编排节点）
    args: dict[str, Any] = field(default_factory=dict)
    depends_on: tuple[str, ...] = ()
    optional: bool = False                  # True = 本任务失败**不阻塞下游**
    status: str = "pending"                 # pending/running/done/failed/skipped/waiting
    result: Any = None
    error: str | None = None
    error_type: str | None = None
    elapsed: float = 0.0
    attempts: int = 0

    @property
    def finished(self) -> bool:
        """本轮是否已到终态（不再需要执行）。``waiting`` 不算 —— 它还等着被跑。"""
        return self.status in ("done", "failed", "skipped")

    @property
    def settled(self) -> bool:
        """是否**不需要再执行**。终态 + 人工确认中的任务都属于这一类。

        断点续跑时用 ``settled`` 判断"这个任务要不要跳过"。
        """
        return self.status in ("done", "failed")

    @property
    def ok(self) -> bool:
        return self.status == "done"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["depends_on"] = list(self.depends_on)
        return d


# --------------------------------------------------------------------- 计划
@dataclass
class Plan:
    """一张任务图。"""
    goal: str
    tasks: list[Task] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------- 构造
    def add(self, task: Task) -> "Plan":
        self.tasks.append(task)
        return self

    def by_id(self, tid: str) -> Task | None:
        for t in self.tasks:
            if t.id == tid:
                return t
        return None

    # ------------------------------------------------------------- 校验
    def validate(self) -> list[str]:
        """静态校验。**在 plan 阶段就报错，不要等到执行**。"""
        problems: list[str] = []
        ids = [t.id for t in self.tasks]
        dup = {i for i in ids if ids.count(i) > 1}
        if dup:
            problems.append(f"任务 id 重复：{sorted(dup)}")
        known = set(ids)
        for t in self.tasks:
            for dep in t.depends_on:
                if dep not in known:
                    problems.append(f"{t.id} 依赖了不存在的任务 {dep}")
        if not problems:
            try:
                topological_order(self.tasks)
            except PlanCycleError as e:
                problems.append(str(e))
        return problems

    # ------------------------------------------------------------- 状态
    def summary(self) -> str:
        n = len(self.tasks)
        done = sum(t.ok for t in self.tasks)
        failed = [t for t in self.tasks if t.status == "failed"]
        skipped = [t for t in self.tasks if t.status == "skipped"]
        waiting = [t for t in self.tasks if t.status == "waiting"]
        elapsed = sum(t.elapsed for t in self.tasks)
        tail = f" ｜ 累计耗时 {elapsed:.2f}s"

        if failed or waiting:
            bits = [f"完成 {done}/{n} 项"]
            if failed:
                bits.append(f"**{len(failed)} 项失败**"
                            f"（{', '.join(t.title for t in failed)}）")
            if waiting:
                bits.append(f"**{len(waiting)} 项等待人工确认**"
                            f"（{', '.join(t.title for t in waiting)}）")
            if skipped:
                bits.append(f"{len(skipped)} 项跳过")
            return (" ｜ ".join(bits) + tail +
                    "\n⚠️ 结论仅覆盖已完成的部分，未完成项需单独处理。")
        if skipped:
            return (f"完成 {done}/{n} 项 ｜ {len(skipped)} 项跳过"
                    f"（{', '.join(t.title for t in skipped)}）{tail}")
        return f"全部 {done}/{n} 项完成{tail}"

    def to_dict(self) -> dict:
        return {"goal": self.goal, "meta": self.meta,
                "tasks": [t.to_dict() for t in self.tasks]}

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, default=str,
                          indent=indent)

    @classmethod
    def from_dict(cls, d: dict) -> "Plan":
        p = cls(goal=d.get("goal", ""), meta=d.get("meta") or {})
        for td in d.get("tasks", []):
            td = dict(td)
            td["depends_on"] = tuple(td.get("depends_on") or ())
            p.tasks.append(Task(**td))
        return p

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.to_json(), encoding="utf-8")
        return p

    @classmethod
    def load(cls, path: str | Path) -> "Plan":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


@dataclass
class PlanPolicy:
    """调度策略。"""
    max_workers: int = 4
    max_retries: int = 2
    backoff_base: float = 2.0
    stop_on_failure: bool = False           # True = 一失败就整体中止
    hitl_before: tuple[str, ...] = ()       # 命中这些工具时暂停，等人工确认
    dry_run: bool = False
    rerun_failed: bool = False              # 断点续跑时是否重跑已失败的任务
    rerun_done: bool = False                # 强制重算（谨慎：写操作会重复执行）
    confirmed: tuple[str, ...] = ()         # ★ 已人工确认的任务 id（单独放行它的写操作）


# --------------------------------------------------------------------- 算法
def topological_order(tasks: Sequence[Task]) -> list[Task]:
    """Kahn 拓扑排序。有环抛 :class:`PlanCycleError`。

    这是**执行前的最后一道防线** —— 循环依赖往往藏在数据里
    （表 3 依赖表 2 的衍生结果，表 2 又需要表 3 的统计量），
    写代码时根本看不出来。
    """
    by_id = {t.id: t for t in tasks}
    indeg = {t.id: len(t.depends_on) for t in tasks}
    children: dict[str, list[str]] = {t.id: [] for t in tasks}

    for t in tasks:
        for dep in t.depends_on:
            if dep not in by_id:
                raise PlanError(f"任务 {t.id} 依赖了不存在的任务 {dep}")
            children[dep].append(t.id)

    queue = [tid for tid, d in indeg.items() if d == 0]
    order: list[str] = []
    while queue:
        tid = queue.pop(0)
        order.append(tid)
        for ch in children[tid]:
            indeg[ch] -= 1
            if indeg[ch] == 0:
                queue.append(ch)

    if len(order) != len(tasks):
        stuck = sorted(set(indeg) - set(order))
        raise PlanCycleError(
            f"任务图存在循环依赖，涉及：{stuck}。"
            f"依赖关系："
            + "; ".join(f"{t.id}→{list(t.depends_on)}" for t in tasks if t.id in stuck))
    return [by_id[tid] for tid in order]


def levels(tasks: Sequence[Task]) -> list[list[Task]]:
    """按"最早可执行轮次"分层。**同一层内的任务互不依赖，可以并行。**

    TLF 场景下这就是"哪些表能一起跑"的答案。
    """
    by_id = {t.id: t for t in tasks}
    depth: dict[str, int] = {}

    def d(tid: str) -> int:
        if tid not in depth:
            deps = by_id[tid].depends_on
            depth[tid] = 0 if not deps else 1 + max(d(x) for x in deps)
        return depth[tid]

    buckets: dict[int, list[Task]] = {}
    for t in topological_order(tasks):
        buckets.setdefault(d(t.id), []).append(t)
    return [buckets[k] for k in sorted(buckets)]


def render_plan(tasks: Sequence[Task], use_ascii: bool = True) -> str:
    """把任务图渲染成可读的文本（含分层与并行提示）。"""
    try:
        layers = levels(tasks)
    except PlanError as e:
        return f"（计划无效：{e}）"
    lines = []
    for i, layer in enumerate(layers):
        names = ", ".join(f"{t.id}({t.tool or '编排'})" for t in layer)
        tag = " ← 可并行" if len(layer) > 1 else ""
        lines.append(f"  L{i}: {names}{tag}")
    return "\n".join(lines)


# --------------------------------------------------------------------- 执行
def _dep_failed(t: Task, plan: Plan) -> bool:
    """依赖是否"本轮不可用"。

    三条规则，每条都对应一个真实场景：

    1. 依赖**缺失**（id 写错）→ 视为不可用。校验阶段其实已经拦住了，
       这里是运行期的兜底。
    2. 依赖标记 ``optional=True`` → **它失败也不阻塞下游**。
       例：查 CDISC 受控术语版本失败，不该阻塞整批出表 ——
       出表用的是本地 CT，外部接口只是核对。
    3. 依赖处于 ``failed / skipped / waiting`` → 不可用。
       ``waiting``（等人工确认）也算：依赖没就绪就往下跑，
       等于拿着半成品继续做，这正是流水线最该避免的事。
    """
    for dep in t.depends_on:
        d = plan.by_id(dep)
        if d is None:
            return True
        if d.optional:
            continue
        if d.status in ("failed", "skipped", "waiting"):
            return True
    return False


def execute_plan(plan: Plan, registry: ToolRegistry,
                 policy: Policy | None = None,
                 ppolicy: PlanPolicy | None = None,
                 on_task: Callable[[Task], None] | None = None,
                 ) -> Plan:
    """按层执行：**同层并行，层间串行**。

    用线程池而不是进程池 —— 工具调用绝大多数时间在等 I/O
    （读文件、调 API），线程足够；而进程池需要 pickle 参数，
    对一个 DataFrame 来说序列化开销会吃掉全部收益（第 19.5 节）。
    """
    policy = policy or Policy()
    ppolicy = ppolicy or PlanPolicy()

    problems = plan.validate()
    if problems:
        raise PlanError("计划校验失败：\n  - " + "\n  - ".join(problems))

    layers = levels(plan.tasks)

    for layer in layers:
        ready: list[Task] = []
        for t in layer:
            # ★ 断点续跑的第一道判断：已到终态的任务**不再重跑**。
            #   没有这一条，"从盘上恢复计划继续跑"就等于"整批重跑一遍"，
            #   而且会重复执行写操作（重复递交！）。
            if t.status == "done" and not ppolicy.rerun_done:
                continue
            if t.status == "failed" and not ppolicy.rerun_failed:
                continue
            if _dep_failed(t, plan):
                t.status = "skipped"
                t.error = "依赖任务未成功"
                if on_task:
                    on_task(t)
                continue
            ready.append(t)
        if not ready:
            continue

        if ppolicy.dry_run:
            for t in ready:
                t.status = "skipped"
                t.error = "dry-run 未执行"
                if on_task:
                    on_task(t)
            continue

        workers = max(1, min(ppolicy.max_workers, len(ready)))
        if workers == 1:
            for t in ready:
                _run_one(t, registry, policy, ppolicy, on_task)
                if ppolicy.stop_on_failure and t.status == "failed":
                    _skip_rest(plan, on_task)
                    return plan
        else:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {ex.submit(_run_one, t, registry, policy, ppolicy,
                                  on_task): t for t in ready}
                for f in as_completed(futs):
                    f.result()
            if ppolicy.stop_on_failure and any(t.status == "failed" for t in ready):
                _skip_rest(plan, on_task)
                return plan

    return plan


def _skip_rest(plan: Plan, on_task: Callable[[Task], None] | None) -> None:
    for t in plan.tasks:
        if t.status == "waiting":
            continue            # 人工确认点保持等待，不因别处失败而被作废
        if not t.finished:
            t.status = "skipped"
            t.error = "因前置任务失败而中止"
            if on_task:
                on_task(t)


RETRYABLE = {"transient"}


def _run_one(task: Task, registry: ToolRegistry, policy: Policy,
             ppolicy: PlanPolicy,
             on_task: Callable[[Task], None] | None) -> None:
    """执行单个任务，按错误类型决定是否重试。"""
    # 人工确认闸门（计划层）
    spec = registry.get(task.tool) if task.tool else None
    if spec is not None and task.tool in ppolicy.hitl_before:
        # ★ 记 waiting 而不是 skipped：人确认后要能**只重跑这一个任务**。
        task.status = "waiting"
        task.error = f"命中人工确认点（{task.tool}），等待确认后重跑"
        if on_task:
            on_task(task)
        return

    # 人工确认放行（工具层）：**逐个任务放行**，不是全局关掉闸门。
    # 全局关掉意味着"确认过一次之后，所有写操作都不再需要确认" ——
    # 这正是审批流最容易被绕过的地方。
    task_policy = policy
    if task.id in ppolicy.confirmed and policy.confirm_side_effects:
        task_policy = replace(policy, confirm_side_effects=False)

    task.status = "running"
    if not task.tool:                       # 纯编排节点
        task.status = "done"
        task.result = {"note": "编排节点，无工具调用"}
        if on_task:
            on_task(task)
        return

    for attempt in range(1, ppolicy.max_retries + 2):
        task.attempts = attempt
        t0 = time.perf_counter()
        res: ToolResult = registry.execute(task.tool, task.args, task_policy)
        task.elapsed = time.perf_counter() - t0

        if res.ok:
            task.status, task.result, task.error = "done", res.data, None
            break

        if res.pending:                     # 需要人工确认
            task.status = "waiting"         # 与 hitl_before 同语义：等确认后重跑
            task.error = res.error
            break

        if res.error_type in RETRYABLE and attempt <= ppolicy.max_retries:
            time.sleep(min(ppolicy.backoff_base ** attempt, 10.0))
            continue

        task.status = "failed"
        task.error = res.error
        task.error_type = res.error_type
        break

    if on_task:
        on_task(task)
