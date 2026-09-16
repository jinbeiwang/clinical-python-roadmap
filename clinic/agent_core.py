#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Agent 分层架构骨架（第 18、20 章的核心实现）。

设计目标
--------
让"确定性"和"不确定性"在代码层面彻底分开：

    编排层   Agent / AgentState / Budget / Policy   —— 循环与预算，全是普通 Python
    工具层   ToolSpec / ToolRegistry / ToolResult   —— 契约、校验、边界
    领域层   clinic.io / derive / report / qc       —— 纯 pandas，无 LLM
    基础层   LLMClient (Real / Mock) / Observer      —— 可替换的外部依赖

一条纪律：**下层永远不知道上面有 LLM**。所以把 LLM 客户端换成
:class:`MockLLMClient`，整个流程依然能真实跑完（工具真的执行、数据真的读）。

和 `cases/case07` 的关系
------------------------
case07 是教学版（单文件 200 行，一个 `run_agent()` 干所有事）；
本模块是工程版，把那些职责拆开，并补上 case07 缺的四样东西：
状态外置、工具契约、副作用分级、审计追踪。
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

__all__ = [
    "ToolCall", "Message",
    "ToolResult", "ToolSpec", "ToolRegistry",
    "Step", "Fact", "Budget", "Policy", "AgentState",
    "Observer", "ConsoleObserver", "JsonlObserver", "Metrics",
    "LLMClient", "MockLLMClient", "OpenAIClient",
    "Agent", "rough_tokens",
]


# ===========================================================================
# 0. 小工具
# ===========================================================================
def rough_tokens(text: str) -> int:
    """粗略估算 token 数（1 汉字 ≈ 0.8，其余 ≈ 0.3）。

    仅用于**预算控制**，不能用于计费 —— 计费要用 API 返回的真实 usage。
    """
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    return int(cjk * 0.8 + (len(text) - cjk) * 0.3) + 1


def _new_id(prefix: str = "call") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


# ===========================================================================
# 1. 消息
# ===========================================================================
@dataclass
class ToolCall:
    """模型发起的一次工具调用请求。"""
    id: str
    name: str
    arguments: dict[str, Any]

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "arguments": self.arguments}

    @classmethod
    def from_dict(cls, d: dict) -> "ToolCall":
        return cls(id=d.get("id") or _new_id(),
                   name=d["name"],
                   arguments=d.get("arguments") or {})


@dataclass
class Message:
    """一条对话消息。刻意做成可 JSON 序列化的普通数据。"""
    role: str                                   # system / user / assistant / tool
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None
    tokens: int = 0

    # ---- 构造快捷方式 ----
    @classmethod
    def system(cls, content: str) -> "Message":
        return cls("system", content, tokens=rough_tokens(content))

    @classmethod
    def user(cls, content: str) -> "Message":
        return cls("user", content, tokens=rough_tokens(content))

    @classmethod
    def assistant(cls, content: str = "",
                  tool_calls: Sequence[ToolCall] | None = None) -> "Message":
        return cls("assistant", content, list(tool_calls or []),
                   tokens=rough_tokens(content))

    @classmethod
    def tool(cls, call_id: str, content: str, name: str | None = None) -> "Message":
        return cls("tool", content, tool_call_id=call_id, name=name,
                   tokens=rough_tokens(content))

    # ---- 序列化 ----
    def to_dict(self) -> dict:
        return {"role": self.role, "content": self.content,
                "tool_calls": [c.to_dict() for c in self.tool_calls],
                "tool_call_id": self.tool_call_id, "name": self.name,
                "tokens": self.tokens}

    @classmethod
    def from_dict(cls, d: dict) -> "Message":
        return cls(role=d["role"], content=d.get("content", ""),
                   tool_calls=[ToolCall.from_dict(c) for c in d.get("tool_calls", [])],
                   tool_call_id=d.get("tool_call_id"), name=d.get("name"),
                   tokens=d.get("tokens", 0))

    def to_openai(self) -> dict:
        """转成 OpenAI Chat Completions 的消息格式。"""
        m: dict[str, Any] = {"role": self.role, "content": self.content or None}
        if self.tool_calls:
            m["content"] = self.content or None
            m["tool_calls"] = [
                {"id": c.id, "type": "function",
                 "function": {"name": c.name,
                              "arguments": json.dumps(c.arguments, ensure_ascii=False)}}
                for c in self.tool_calls
            ]
        if self.role == "tool":
            m["tool_call_id"] = self.tool_call_id
        return {k: v for k, v in m.items() if v is not None or k == "content"}


# ===========================================================================
# 2. 工具契约
# ===========================================================================
@dataclass
class ToolResult:
    """工具执行结果。**结构化错误**是这里最关键的设计（见第 20.5 节）。"""
    ok: bool
    data: Any = None
    error: str | None = None
    error_type: str | None = None       # validation/not_found/permission/transient/internal
    hint: str | None = None             # 给模型的修复建议
    options: list[str] | None = None    # 合法取值
    elapsed: float = 0.0
    pending: bool = False               # 需要人工确认

    @classmethod
    def success(cls, data: Any, elapsed: float = 0.0) -> "ToolResult":
        return cls(ok=True, data=data, elapsed=elapsed)

    @classmethod
    def fail(cls, error: str, error_type: str = "internal",
             hint: str | None = None, options: list[str] | None = None,
             elapsed: float = 0.0) -> "ToolResult":
        return cls(ok=False, error=error, error_type=error_type,
                   hint=hint, options=options, elapsed=elapsed)

    @classmethod
    def needs_confirm(cls, note: str) -> "ToolResult":
        return cls(ok=False, pending=True, error=note, error_type="permission")

    def to_llm_text(self) -> str:
        """渲染给模型看。错误要带"怎么改"和"能填什么"。"""
        if self.ok:
            return json.dumps(self.data, ensure_ascii=False, default=str)
        parts = [f"[{self.error_type}] {self.error}"]
        if self.options:
            parts.append(f"可选值：{self.options}")
        if self.hint:
            parts.append(f"提示：{self.hint}")
        return "\n".join(parts)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "data": self.data, "error": self.error,
                "error_type": self.error_type, "hint": self.hint,
                "options": self.options, "elapsed": round(self.elapsed, 3),
                "pending": self.pending}


@dataclass
class ToolSpec:
    """工具的完整契约。

    ``side_effect`` 是安全边界在代码里的落点（第 18.5 节）：
    提示词是"建议"，这里是"物理隔离"。
    """
    name: str
    description: str
    parameters: dict                    # JSON Schema
    func: Callable[[dict], Any]
    side_effect: str = "read"           # read / write / irreversible
    idempotent: bool = True
    timeout: float = 30.0
    tags: tuple[str, ...] = ()

    def schema(self) -> dict:
        return {"type": "function",
                "function": {"name": self.name,
                             "description": self.description,
                             "parameters": self.parameters}}


class ToolRegistry:
    """工具注册与统一执行入口。

    所有校验（参数归一化、白名单、副作用确认、超时、错误分类）
    都收敛在这一个地方 —— 而不是散落在各个工具函数里。
    """

    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}
        self._allowed_datasets: set[str] | None = None
        self._fact_rules: dict[str, Callable[[dict, Any], dict]] = {}

    # ---------------------------------------------------------------- 注册
    def register(self, spec: ToolSpec) -> ToolSpec:
        if not re.fullmatch(r"[a-z][a-z0-9_]{2,40}", spec.name):
            raise ValueError(f"工具名不合规：{spec.name!r}（小写字母/数字/下划线）")
        if spec.name in self._specs:
            raise ValueError(f"工具名重复：{spec.name}")
        if spec.side_effect not in ("read", "write", "irreversible"):
            raise ValueError(f"side_effect 取值非法：{spec.side_effect}")
        self._specs[spec.name] = spec
        return spec

    def tool(self, name: str, description: str, parameters: dict,
             side_effect: str = "read", **kw) -> Callable:
        """装饰器写法。"""
        def deco(fn: Callable[[dict], Any]) -> Callable[[dict], Any]:
            self.register(ToolSpec(name=name, description=description,
                                   parameters=parameters, func=fn,
                                   side_effect=side_effect, **kw))
            return fn
        return deco

    def set_allowed_datasets(self, names: Iterable[str]) -> None:
        self._allowed_datasets = {n.lower() for n in names}

    def set_fact_rule(self, tool_name: str,
                      rule: Callable[[dict, Any], dict]) -> None:
        """注册"从工具结果里提取事实"的规则（见第 21.4 节）。"""
        self._fact_rules[tool_name] = rule

    def extract_facts(self, tool_name: str, args: dict, data: Any) -> dict:
        rule = self._fact_rules.get(tool_name)
        if not rule or not isinstance(data, dict):
            return {}
        try:
            return rule(args, data) or {}
        except Exception:
            return {}

    # ---------------------------------------------------------------- 查询
    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def names(self) -> list[str]:
        return sorted(self._specs)

    def specs(self) -> list[ToolSpec]:
        return [self._specs[n] for n in self.names()]

    def schemas(self) -> list[dict]:
        return [s.schema() for s in self.specs()]

    def validate(self) -> list[str]:
        """契约自检。返回问题列表（空 = 健康）。

        对应第 20.9 节的四条基础断言，可在 CI 里直接调用。
        """
        problems: list[str] = []
        for s in self.specs():
            if not s.description or len(s.description) < 10:
                problems.append(f"{s.name}: 描述过短，模型无法判断用途")
            if s.parameters.get("type") != "object":
                problems.append(f"{s.name}: parameters.type 必须是 object")
            if s.parameters.get("additionalProperties") is not False:
                problems.append(f"{s.name}: 缺少 additionalProperties=false")
            for pname, p in (s.parameters.get("properties") or {}).items():
                if "description" not in p:
                    problems.append(f"{s.name}.{pname}: 参数缺少 description")
        return problems

    # ---------------------------------------------------------------- 执行
    def normalize_args(self, name: str, args: dict) -> dict:
        """把 LLM 给的各种形态归一化。子类可覆写。"""
        out = dict(args or {})
        if "dataset" in out:
            out["dataset"] = _norm_dataset(out["dataset"])
        return out

    def execute(self, name: str, args: dict,
                policy: "Policy | None" = None,
                state: "AgentState | None" = None) -> ToolResult:
        t0 = time.perf_counter()
        spec = self._specs.get(name)
        if spec is None:
            return ToolResult.fail(
                f"未知工具 {name!r}", "validation",
                hint="请使用工具清单中存在的工具名",
                options=self.names(), elapsed=time.perf_counter() - t0)

        # ---- 副作用闸门：必须人工确认 ----
        if policy is not None and spec.side_effect != "read" and policy.confirm_side_effects:
            if state is not None:
                state.status = "waiting_confirm"
            return ToolResult.needs_confirm(
                f"工具 {name} 的副作用等级为 {spec.side_effect}，需要人工确认。"
                f"待执行的参数：{json.dumps(args, ensure_ascii=False)[:200]}")

        args = self.normalize_args(name, args)

        # ---- 数据集白名单（映射而非拼接，见第 20.5 节）----
        if self._allowed_datasets is not None and "dataset" in args:
            ds = args["dataset"]
            if ds not in self._allowed_datasets:
                return ToolResult.fail(
                    f"数据集 {ds!r} 不在允许列表中", "permission",
                    hint="可能是拼写错误；请只用枚举中的值",
                    options=sorted(self._allowed_datasets),
                    elapsed=time.perf_counter() - t0)

        # ---- 必填参数 ----
        required = spec.parameters.get("required") or []
        missing = [k for k in required if args.get(k) in (None, "")]
        if missing:
            return ToolResult.fail(
                f"缺少必填参数：{missing}", "validation",
                hint=f"请补全这些参数：{missing}",
                options=[k for k in (spec.parameters.get("properties") or {})],
                elapsed=time.perf_counter() - t0)

        # ---- 真正执行 ----
        try:
            data = spec.func(args)
        except PermissionError as e:
            return ToolResult.fail(str(e), "permission",
                                   elapsed=time.perf_counter() - t0)
        except (KeyError, ValueError, TypeError) as e:
            return ToolResult.fail(f"{type(e).__name__}: {e}", "validation",
                                   hint="请检查参数取值与类型",
                                   elapsed=time.perf_counter() - t0)
        except FileNotFoundError as e:
            return ToolResult.fail(str(e), "not_found",
                                   elapsed=time.perf_counter() - t0)
        except TimeoutError as e:
            return ToolResult.fail(str(e) or "工具执行超时", "transient",
                                   elapsed=time.perf_counter() - t0)
        except Exception as e:                      # noqa: BLE001 —— 兜底
            return ToolResult.fail(f"{type(e).__name__}: {e}", "internal",
                                   hint="这是程序缺陷，请如实说明无法完成",
                                   elapsed=time.perf_counter() - t0)

        if isinstance(data, ToolResult):            # 工具自己返回了结构化结果
            data.elapsed = time.perf_counter() - t0
            return data
        if isinstance(data, dict) and "__pending__" in data:
            return ToolResult.needs_confirm(str(data["__pending__"]))
        return ToolResult.success(_jsonable(data), time.perf_counter() - t0)


def _jsonable(obj: Any) -> Any:
    """把结果转成基本类型。**工具结果必须可序列化**（第 20.7 节）。"""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_jsonable(v) for v in obj]
    if hasattr(obj, "item") and callable(obj.item):          # numpy 标量
        try:
            return _jsonable(obj.item())
        except Exception:
            pass
    if hasattr(obj, "isoformat"):                            # datetime / Timestamp
        return obj.isoformat()
    if hasattr(obj, "to_dict") and callable(obj.to_dict):    # 嵌套 dataclass
        return _jsonable(obj.to_dict())
    return str(obj)


def _norm_dataset(raw: Any) -> str:
    """归一化数据集名（大小写、扩展名、空白）。见第 20.4 节。"""
    s = str(raw).strip().lower()
    for ext in (".csv", ".xpt", ".sas7bdat", ".parquet"):
        if s.endswith(ext):
            s = s[: -len(ext)]
    return s


# ===========================================================================
# 3. 状态（外置，可序列化）
# ===========================================================================
@dataclass
class Step:
    """一步动作。``args`` 与 ``result`` 就是审计追踪的核心内容。"""
    index: int
    kind: str                           # think / tool / answer / error
    tool: str | None = None
    args: dict | None = None
    result: str | None = None
    error: str | None = None
    error_type: str | None = None
    elapsed: float = 0.0
    tokens: int = 0


@dataclass
class Fact:
    """一条已确认的事实。带出处 —— 这是能写进报告的前提。"""
    key: str
    value: Any
    source: str
    step: int


@dataclass
class Budget:
    """三道硬闸门：步数 / token / 时间。超限要优雅收尾，不要抛异常。"""
    max_steps: int = 25
    max_tokens: int = 120_000
    deadline_sec: float = 300.0
    steps_used: int = 0
    tokens_used: int = 0
    started_at: float = 0.0

    def start(self) -> None:
        self.started_at = time.time()

    @property
    def elapsed(self) -> float:
        return (time.time() - self.started_at) if self.started_at else 0.0

    def exceeded(self) -> str | None:
        if self.steps_used >= self.max_steps:
            return f"步数超限（{self.steps_used}/{self.max_steps}）"
        if self.tokens_used >= self.max_tokens:
            return f"token 超限（{self.tokens_used}/{self.max_tokens}）"
        if self.started_at and self.elapsed > self.deadline_sec:
            return f"时间超限（{self.elapsed:.0f}s/{self.deadline_sec:.0f}s）"
        return None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Policy:
    """安全边界与预算的集中定义。"""
    max_steps: int = 25
    max_tokens: int = 120_000
    deadline_sec: float = 300.0
    tool_timeout: float = 30.0
    confirm_side_effects: bool = True
    max_retries: int = 2
    backoff_base: float = 1.5
    allowed_datasets: frozenset[str] = field(
        default_factory=lambda: frozenset(
            {"dm", "adsl", "adae", "ae", "adtte", "ex", "ds", "vs", "adlbc"}))

    def budget(self) -> Budget:
        return Budget(max_steps=self.max_steps, max_tokens=self.max_tokens,
                      deadline_sec=self.deadline_sec)


@dataclass
class AgentState:
    """Agent 的全部可变状态。

    刻意做成**纯数据 + 可 JSON 序列化**：
    断点恢复、事后复盘、回归重放，都依赖这一点（第 18.4 节）。
    """
    goal: str
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: str = "running"             # running/waiting_confirm/done/failed
    step_index: int = 0
    messages: list[Message] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    facts: list[Fact] = field(default_factory=list)
    plan: dict[str, Any] | None = None
    artifacts: dict[str, Any] = field(default_factory=dict)
    answer: str = ""
    error: str | None = None
    budget: Budget = field(default_factory=Budget)
    created_at: float = field(default_factory=time.time)

    # ------------------------------------------------------------- 事实
    def add_fact(self, key: str, value: Any, source: str, step: int) -> None:
        for f in self.facts:
            if f.key == key:
                f.value, f.source, f.step = value, source, step
                return
        self.facts.append(Fact(key, value, source, step))

    def fact_map(self) -> dict[str, Any]:
        return {f.key: f.value for f in self.facts}

    def fact_table(self) -> str:
        """渲染成给模型看的固定格式表（第 21.4 节）。

        这就是"让模型读事实，而不是回忆事实"。
        """
        if not self.facts:
            return "（暂无已确认的事实）"
        lines = ["| 事实 | 值 | 出处 |", "|---|---|---|"]
        for f in self.facts:
            lines.append(f"| {f.key} | {f.value} | {f.source} @step{f.step} |")
        return "\n".join(lines)

    # ------------------------------------------------------------- 追踪
    def trace(self, limit: int | None = None) -> str:
        steps = self.steps if limit is None else self.steps[:limit]
        lines = [f"# 执行追踪 · {self.request_id} · {self.goal}", ""]
        for s in steps:
            if s.kind == "tool":
                arg = json.dumps(s.args, ensure_ascii=False, default=str)
                if len(arg) > 160:
                    arg = arg[:160] + "…"
                head = f"{s.index:>2}. [工具] {s.tool} {arg}  ({s.elapsed:.2f}s)"
                lines.append(head)
                if s.error:
                    lines.append(f"      ✗ {s.error_type}: {s.error}")
                elif s.result:
                    preview = s.result.replace("\n", " ")
                    lines.append(f"      → {preview[:120]}")
            elif s.kind == "answer":
                lines.append(f"{s.index:>2}. [结论] {s.result}")
            else:
                lines.append(f"{s.index:>2}. [{s.kind}] {s.result or ''}")
        lines.append("")
        lines.append(f"状态：{self.status} ｜ 步数 {self.budget.steps_used}"
                     f" ｜ token {self.budget.tokens_used}"
                     f" ｜ 耗时 {self.budget.elapsed:.2f}s")
        return "\n".join(lines)

    # ------------------------------------------------------------- 序列化
    def to_dict(self) -> dict:
        return {
            "goal": self.goal, "request_id": self.request_id,
            "status": self.status, "step_index": self.step_index,
            "messages": [m.to_dict() for m in self.messages],
            "steps": [asdict(s) for s in self.steps],
            "facts": [asdict(f) for f in self.facts],
            "plan": self.plan, "artifacts": _jsonable(self.artifacts),
            "answer": self.answer, "error": self.error,
            "budget": self.budget.to_dict(), "created_at": self.created_at,
        }

    def to_json(self, indent: int | None = None) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, default=str,
                          indent=indent)

    @classmethod
    def from_dict(cls, d: dict) -> "AgentState":
        st = cls(goal=d["goal"], request_id=d.get("request_id", uuid.uuid4().hex[:12]))
        st.status = d.get("status", "running")
        st.step_index = d.get("step_index", 0)
        st.messages = [Message.from_dict(m) for m in d.get("messages", [])]
        st.steps = [Step(**s) for s in d.get("steps", [])]
        st.facts = [Fact(**f) for f in d.get("facts", [])]
        st.plan = d.get("plan")
        st.artifacts = d.get("artifacts") or {}
        st.answer = d.get("answer", "")
        st.error = d.get("error")
        st.budget = Budget(**(d.get("budget") or {}))
        st.created_at = d.get("created_at", time.time())
        return st

    @classmethod
    def from_json(cls, s: str) -> "AgentState":
        return cls.from_dict(json.loads(s))

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.to_json(indent=2), encoding="utf-8")
        return p

    @classmethod
    def load(cls, path: str | Path) -> "AgentState":
        return cls.from_json(Path(path).read_text(encoding="utf-8"))


# ===========================================================================
# 4. 观测
# ===========================================================================
class Observer:
    """观测钩子。默认什么都不做 —— 生产里换成日志/追踪实现。"""

    def on_start(self, state: AgentState) -> None: ...
    def on_step(self, state: AgentState, step: Step) -> None: ...
    def on_tool(self, name: str, args: dict, result: ToolResult,
                elapsed: float) -> None: ...
    def on_finish(self, state: AgentState) -> None: ...


class ConsoleObserver(Observer):
    """打印到控制台。教学与调试用。"""

    def __init__(self, verbose: bool = True, color: bool = True) -> None:
        self.verbose = verbose
        self.color = color

    def _c(self, s: str, code: str) -> str:
        return f"\033[{code}m{s}\033[0m" if self.color else s

    def on_start(self, state: AgentState) -> None:
        if self.verbose:
            print(self._c(f"▶ Agent 启动 · {state.request_id}", "36"))
            print(f"  目标：{state.goal}")

    def on_tool(self, name: str, args: dict, result: ToolResult,
                elapsed: float) -> None:
        if not self.verbose:
            return
        arg = json.dumps(args, ensure_ascii=False, default=str)
        print(f"  {self._c('→', '34')} {name}({arg[:110]})  {elapsed:.2f}s")
        if result.pending:
            print(f"    {self._c('⏸ 等待人工确认', '33')}")
        elif not result.ok:
            print(f"    {self._c('✗ ' + str(result.error)[:120], '31')}")

    def on_finish(self, state: AgentState) -> None:
        if not self.verbose:
            return
        tag = {"done": ("✓ 完成", "32"),
               "waiting_confirm": ("⏸ 等待确认", "33"),
               "failed": ("✗ 失败", "31")}.get(state.status, (state.status, "37"))
        print(self._c(tag[0], tag[1]) +
              f"  步数 {state.budget.steps_used} · "
              f"token {state.budget.tokens_used} · "
              f"耗时 {state.budget.elapsed:.2f}s")


class JsonlObserver(Observer):
    """落 JSONL 审计日志。**每条记录都过一遍脱敏**。"""

    SENSITIVE = ("token", "key", "secret", "password", "authorization", "cookie")

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _redact(self, d: Any) -> Any:
        if isinstance(d, dict):
            out = {}
            for k, v in d.items():
                if any(s in k.lower() for s in self.SENSITIVE):
                    out[k] = "***"
                else:
                    out[k] = self._redact(v)
            return out
        if isinstance(d, list):
            return [self._redact(v) for v in d]
        return d

    def _write(self, rec: dict) -> None:
        rec["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(self._redact(rec), ensure_ascii=False,
                               default=str) + "\n")

    def on_tool(self, name: str, args: dict, result: ToolResult,
                elapsed: float) -> None:
        self._write({"event": "tool", "tool": name, "args": args,
                     "ok": result.ok, "error_type": result.error_type,
                     "error": result.error, "elapsed": round(elapsed, 3)})

    def on_finish(self, state: AgentState) -> None:
        self._write({"event": "finish", "rid": state.request_id,
                     "status": state.status, "steps": state.budget.steps_used,
                     "tokens": state.budget.tokens_used,
                     "elapsed": round(state.budget.elapsed, 3)})


class Metrics:
    """进程内累计指标（第 24.4 节）。生产环境应导出到监控系统。"""

    def __init__(self) -> None:
        self.requests = 0
        self.succeeded = 0
        self.failed = 0
        self.waiting_confirm = 0
        self.total_steps = 0
        self.total_tokens = 0
        self.total_tool_calls = 0
        self.tool_errors = 0
        self.elapsed_sum = 0.0
        self.by_tool: dict[str, list[int]] = {}      # tool -> [调用数, 错误数]

    def record_tool(self, name: str, ok: bool) -> None:
        self.total_tool_calls += 1
        cell = self.by_tool.setdefault(name, [0, 0])
        cell[0] += 1
        if not ok:
            self.tool_errors += 1
            cell[1] += 1

    def record_run(self, state: AgentState) -> None:
        self.requests += 1
        if state.status == "done":
            self.succeeded += 1
        elif state.status == "waiting_confirm":
            self.waiting_confirm += 1
        else:
            self.failed += 1
        self.total_steps += state.budget.steps_used
        self.total_tokens += state.budget.tokens_used
        self.elapsed_sum += state.budget.elapsed

    def summary(self) -> dict:
        n = max(1, self.requests)
        return {
            "请求数": self.requests,
            "成功率": f"{self.succeeded / n * 100:.1f}%",
            "平均步数": round(self.total_steps / n, 1),
            "平均token": round(self.total_tokens / n),
            "平均耗时(秒)": round(self.elapsed_sum / n, 2),
            "工具调用总数": self.total_tool_calls,
            "工具错误率": f"{self.tool_errors / max(1, self.total_tool_calls) * 100:.1f}%",
            "等待人工确认": self.waiting_confirm,
        }

    def worst_tools(self, top: int = 3) -> list[tuple[str, int, int]]:
        rows = [(n, c, e) for n, (c, e) in self.by_tool.items() if e]
        rows.sort(key=lambda r: -r[2])
        return rows[:top]


# ===========================================================================
# 5. LLM 客户端
# ===========================================================================
class LLMClient:
    """LLM 客户端接口。真实实现与 Mock 实现都遵循它。"""

    def chat(self, messages: Sequence[Message],
             tools: Sequence[dict]) -> Message:
        raise NotImplementedError


class MockLLMClient(LLMClient):
    """**确定性**的离线 LLM 替身。

    它的存在是为了让整个 Agent 流程在**没有 API Key、不联网**的情况下
    真实跑起来 —— 工具会真的执行、数据会真的被读、结论里的数字
    来自真实工具返回值，而不是编造的常量。

    两种模式：
      1. 默认模式：按"看结构 → 跑检查 → （有必要则细分）→ 总结"的
         固定计划推进，具体参数从目标文本里解析。
      2. 脚本模式：传入 ``plan=[...]`` 显式指定每一步，用于测试分支。
    """

    def __init__(self, plan: Sequence[dict] | None = None) -> None:
        self.plan = list(plan) if plan is not None else None
        self._cursor = 0

    # ------------------------------------------------------------ 对外
    def chat(self, messages: Sequence[Message],
             tools: Sequence[dict]) -> Message:
        if self.plan is not None:
            return self._scripted(messages, tools)
        return self._default(messages, tools)

    # ---------------------------------------------------------- 脚本模式
    def _scripted(self, messages, tools) -> Message:
        if self._cursor >= len(self.plan):
            return Message.assistant(_final_answer(messages))
        item = self.plan[self._cursor]
        self._cursor += 1
        if isinstance(item, str):
            return Message.assistant(item)
        return Message.assistant(
            tool_calls=[ToolCall(id=_new_id(), name=item["tool"],
                                 arguments=item.get("args", {}))])

    # ---------------------------------------------------------- 默认模式
    def _default(self, messages, tools) -> Message:
        avail = _tool_names(tools)
        called = _called_tools(messages)
        goal = _goal_text(messages)
        last = _last_tool_data(messages)

        # ⓪ 用户点名的数据集不认识 → **先枚举，再如实说"没有"，绝不换一个顶上**
        #
        # 这一条是整个 Mock 里最该看的一段。原先是直接把目标里没识别到的
        # 数据集**悄悄替换成第一个可用的**（adsl）然后照常分析 ——
        # 结论看起来完全正常，只是分析的是**另一个数据集**。
        # 这种"静默替换"在真实模型上同样会发生，也正是第 24 章评估集要抓的东西：
        # 本仓库的评估集第一次跑就把这个缺陷抓了出来（见 cases/case12）。
        enum_ds = _schema_enum(tools, "describe_dataset", "dataset")
        if enum_ds:
            named = _pick_dataset(goal, avail, called)
            if named is None or named.lower() not in enum_ds:
                unknown = [t for t in _dataset_like_tokens(goal)
                           if t not in enum_ds]
                if unknown:
                    if "list_datasets" in avail and "list_datasets" not in called:
                        return Message.assistant(tool_calls=[ToolCall(
                            _new_id(), "list_datasets", {})])
                    return Message.assistant(_not_found_answer(unknown))

        # ① 先看结构（任何分析前都该先看一眼数据）
        if "describe_dataset" in avail and "describe_dataset" not in called:
            ds = _pick_dataset(goal, avail, called) or _first_dataset(avail)
            return Message.assistant(tool_calls=[ToolCall(
                _new_id(), "describe_dataset", {"dataset": ds})])

        # ② 再跑质量检查
        if "run_qc_checks" in avail and "run_qc_checks" not in called:
            ds = _last_dataset(messages) or "adsl"
            args: dict[str, Any] = {"dataset": ds}
            checks = _pick_checks(goal)
            if checks:
                args["checks"] = checks
            return Message.assistant(tool_calls=[ToolCall(
                _new_id(), "run_qc_checks", args)])

        # ③ 跨域一致性（目标里提到就做）
        if ("check_subject_consistency" in avail
                and "check_subject_consistency" not in called
                and re.search(r"一致性|跨域|受试者|重复|missing subject", goal, re.I)):
            return Message.assistant(tool_calls=[ToolCall(
                _new_id(), "check_subject_consistency", {})])

        # ④ 还想看分布细节
        if ("frequency" in avail and "frequency" not in called
                and re.search(r"分布|频数|各|by\b|组", goal, re.I)):
            ds = _last_dataset(messages) or "adsl"
            var = _pick_var(goal, last) or "TRT01P"
            return Message.assistant(tool_calls=[ToolCall(
                _new_id(), "frequency", {"dataset": ds, "var": var})])

        # ⑤ 收尾
        return Message.assistant(_final_answer(messages))


def _tool_names(tools: Sequence[dict]) -> set[str]:
    out = set()
    for t in tools or []:
        fn = t.get("function") if isinstance(t, dict) else None
        if fn and fn.get("name"):
            out.add(fn["name"])
        elif isinstance(t, dict) and t.get("name"):
            out.add(t["name"])
    return out


def _called_tools(messages: Sequence[Message]) -> set[str]:
    out = set()
    for m in messages:
        for c in m.tool_calls:
            out.add(c.name)
    return out


def _goal_text(messages: Sequence[Message]) -> str:
    for m in messages:
        if m.role == "user":
            return m.content
    return ""


def _last_tool_data(messages: Sequence[Message]) -> dict:
    """最后一个工具返回的 JSON（用于生成有真实数字的结论）。"""
    for m in reversed(messages):
        if m.role == "tool" and m.content.strip().startswith("{"):
            try:
                d = json.loads(m.content)
                return d if isinstance(d, dict) else {}
            except json.JSONDecodeError:
                continue
    return {}


def _last_dataset(messages: Sequence[Message]) -> str | None:
    for m in reversed(messages):
        for c in m.tool_calls:
            if "dataset" in c.arguments:
                return _norm_dataset(c.arguments["dataset"])
    return None


def _first_dataset(avail: set[str]) -> str:
    for cand in ("adsl", "dm", "adae", "ae", "vs"):
        if cand in avail:
            return cand
    return "adsl"


def _pick_dataset(goal: str, avail: set[str], called: set[str]) -> str | None:
    """从目标文本里挑数据集名。优先选还没看过的。"""
    cands = re.findall(r"\b(dm|adsl|adae|ae|adtte|ex|ds|vs|adlbc)\b", goal, re.I)
    cands = [c.lower() for c in cands]
    for c in cands:
        if c not in called:
            return c
    return cands[0] if cands else None


def _schema_enum(tools: Sequence[dict], tool_name: str,
                 param: str) -> set[str]:
    """从工具 schema 的 ``enum`` 里读出可用取值。

    ★ 这正是"把可用数据集写进工具契约"的回报：模型（或它的离线替身）
    不需要把清单硬编码在代码里 —— 去 schema 里读就行。
    将来数据域增删，只需要改注册表，行为自动跟着变，
    不会出现"代码里还写着 vs、实际早就改名了"这种漂移。
    """
    for t in tools or []:
        fn = t.get("function") if isinstance(t, dict) else None
        if not fn or fn.get("name") != tool_name:
            continue
        props = (fn.get("parameters") or {}).get("properties") or {}
        enum = (props.get(param) or {}).get("enum")
        if enum:
            return {str(x).lower() for x in enum}
    return set()


# "看起来像数据集名"的词：纯小写字母、2~12 个字符。
# 这样 adata / adls 会被挑出来，而 ADAE（大写，通常是域名的规范写法）
# 和 CDISCPILOT01（带数字，是研究编号）不会。
_DATASET_TOKEN_RE = re.compile(r"\b([a-z]{2,12})\b")


def _dataset_like_tokens(goal: str) -> list[str]:
    """挑出目标里"像数据集名"的词，用来发现**用户点名了不存在的数据集**。

    真实 LLM 不需要这种启发式 —— 它直接就知道 adata 不是个域。
    这里做出来，是为了让离线替身也具备"发现自己不认识这个名字"的能力，
    因为**静默换成别的数据集继续分析**是最危险的一种失败。
    """
    return _DATASET_TOKEN_RE.findall(goal.lower())


def _not_found_answer(unknown: Sequence[str]) -> str:
    """数据集不存在时的结论：**一个数字都不给**。

    这条比看起来重要。一个会编造"254 行"的 Agent，在任何受监管的场景里
    都不可用 —— 因为它的错误**看起来和正确答案一模一样**。
    所以这里刻意不引用任何工具返回值里的数字，只说明"没有数据"。
    """
    names = "、".join(f"「{n}」" for n in dict.fromkeys(unknown))
    return "\n".join([
        "## 核查结论",
        "",
        f"- 目标中提到的数据集 {names} 不在本服务可访问的清单里。",
        "- **我没有执行任何数据检查** —— 没有数据就不该有结论。",
        "- 下一步：请确认数据集名称，或从可用清单里重新指定。",
        "",
        "### 依据",
        "- 依据是 list_datasets 返回的可用清单；本结论未引用任何数据值。",
    ])


def _pick_checks(goal: str) -> list[str] | None:
    m = {
        "缺失": ["missing_rate"], "missing": ["missing_rate"],
        "必填": ["required_vars"], "唯一": ["key_unique"], "主键": ["key_unique"],
        "日期": ["date_pairs"], "术语": ["codelist"], "范围": ["range"],
    }
    out: list[str] = []
    for k, v in m.items():
        if k in goal.lower():
            out.extend(v)
    return sorted(set(out)) or None


def _pick_var(goal: str, last: dict) -> str | None:
    """从目标文本里找变量名（大写字母/数字/下划线，3 字符以上）。"""
    found = re.findall(r"\b([A-Z][A-Z0-9_]{2,})\b", goal)
    skip = {"ADSL", "ADAE", "ADTTE", "SDTM", "ADAM", "TEAE", "QC", "SAP"}
    for f in found:
        if f not in skip:
            return f
    cols = last.get("列") or last.get("变量")
    if isinstance(cols, list) and cols:
        return str(cols[0])
    return None


def _final_answer(messages: Sequence[Message]) -> str:
    """从已确认的事实里拼一个**带真实数字**的结论。"""
    rows: list[str] = []
    for m in reversed(messages):
        if m.role == "tool" and m.content.strip().startswith("{"):
            try:
                d = json.loads(m.content)
            except json.JSONDecodeError:
                continue
            if isinstance(d, dict):
                rows.append(json.dumps(d, ensure_ascii=False)[:300])

    # 直接从工具返回里取关键数字
    n_rows = _find_first(messages, ("行数", "n_rows", "总行数"))
    issues = _find_first(messages, ("问题总数", "issues", "问题数"))
    ds = _last_dataset(messages) or "数据"

    lines = [f"## 核查结论（{ds}）", ""]
    if n_rows is not None:
        lines.append(f"- 数据集规模：{n_rows} 行")
    if issues is not None:
        lines.append(f"- 质量检查：共发现 {issues} 个问题")
    lines.append("- 数据来源：以上数字均由工具实际执行取得")
    lines.append("")
    lines.append("### 依据")
    for r in rows[:3]:
        lines.append(f"- 工具返回：{r}")
    if not rows:
        lines.append("- （没有可用的工具返回）")
    return "\n".join(lines)


def _find_first(messages: Sequence[Message], keys: Iterable[str]) -> Any:
    for m in reversed(messages):
        if m.role != "tool" or not m.content.strip().startswith("{"):
            continue
        try:
            d = json.loads(m.content)
        except json.JSONDecodeError:
            continue
        if isinstance(d, dict):
            for k in keys:
                if k in d and d[k] is not None:
                    return d[k]
    return None


class OpenAIClient(LLMClient):
    """真实 LLM 客户端（OpenAI 兼容接口）。

    不依赖 ``openai`` 包 —— 只用 ``httpx`` 发一个 POST，
    这样可以把依赖控制在最小集合，也方便对接任何兼容接口的服务
    （Azure OpenAI、国产模型、公司内部网关）。
    """

    def __init__(self, api_key: str, model: str = "gpt-4o-mini",
                 base_url: str = "https://api.openai.com/v1",
                 temperature: float = 0.0, timeout: float = 60.0) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        self.timeout = timeout

    def chat(self, messages: Sequence[Message],
             tools: Sequence[dict]) -> Message:
        try:
            import httpx                                    # 延迟导入
        except ImportError as e:                            # pragma: no cover
            raise RuntimeError(
                "调用真实模型需要 httpx：pip install httpx") from e

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [m.to_openai() for m in messages],
            "temperature": self.temperature,
        }
        if tools:
            payload["tools"] = list(tools)
            payload["tool_choice"] = "auto"

        resp = httpx.post(f"{self.base_url}/chat/completions",
                          headers={"Authorization": f"Bearer {self.api_key}",
                                   "Content-Type": "application/json"},
                          json=payload, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        choice = data["choices"][0]["message"]
        usage = data.get("usage") or {}

        calls = []
        for c in choice.get("tool_calls") or []:
            raw = c["function"].get("arguments") or "{}"
            try:
                args = json.loads(raw)
            except json.JSONDecodeError:
                args = {}
            calls.append(ToolCall(id=c.get("id") or _new_id(),
                                  name=c["function"]["name"], arguments=args))
        return Message.assistant(choice.get("content") or "", calls,
                                 tokens=usage.get("total_tokens", 0))


# ===========================================================================
# 6. Agent 主循环
# ===========================================================================
DEFAULT_SYSTEM_PROMPT = """你是一名临床统计编程助手，负责核查 SDTM/ADaM 数据的质量。

工作原则：
1. 所有结论必须有工具返回的数据支撑。**不要凭经验猜测数字**。
2. 如果需要的工具不存在，直接说明"无法完成"，不要编造数据。
3. 调用工具前先确认参数名（可先用 describe_dataset 查看结构）。
4. 出现错误时阅读错误信息中的"可选值"和"提示"，据此修正参数。
5. 最终结论要给出：数据规模、发现的问题、每个结论的依据。

安全边界（不可协商）：
- 只能访问允许列表中的数据集。
- 涉及写操作时会被要求人工确认，不要试图绕过。
"""


class Agent:
    """分层架构的主循环。

    它只做四件事：**问模型 → 执行工具 → 记录状态 → 重复**。
    所有业务逻辑在工具里，所有安全策略在 :class:`Policy` 里，
    所有可观测性在 :class:`Observer` 里 —— 这个类里没有一行业务判断。
    """

    def __init__(self, llm: LLMClient, registry: ToolRegistry,
                 policy: Policy | None = None,
                 observer: Observer | None = None,
                 metrics: Metrics | None = None,
                 system_prompt: str = DEFAULT_SYSTEM_PROMPT,
                 context_max_facts: int = 30) -> None:
        self.llm = llm
        self.registry = registry
        self.policy = policy or Policy()
        self.observer = observer or Observer()
        self.metrics = metrics
        self.system_prompt = system_prompt
        self.context_max_facts = context_max_facts
        if self.policy.allowed_datasets:
            registry.set_allowed_datasets(self.policy.allowed_datasets)

    # ------------------------------------------------------------------ 运行
    def run(self, goal: str, state: AgentState | None = None) -> AgentState:
        if state is None:
            state = AgentState(goal=goal, budget=self.policy.budget())
            state.messages.append(Message.system(self.system_prompt))
            state.messages.append(Message.user(goal))
            state.budget.start()
            self.observer.on_start(state)
        elif state.status == "running" and not state.budget.started_at:
            state.budget.start()

        state.status = "running"

        while True:
            # ---- 闸门 1：预算 ----
            if (why := state.budget.exceeded()):
                state.status = "done"
                state.answer = self._budgeted_answer(state, why)
                break

            # ---- 问模型 ----
            state.messages.extend(self._context_extras(state))
            reply = self.llm.chat(state.messages, self.registry.schemas())
            state.budget.tokens_used += reply.tokens

            if not reply.tool_calls:
                state.status = "done"
                state.answer = reply.content
                state.messages.append(reply)
                state.steps.append(Step(state.step_index, "answer",
                                        result=reply.content[:400]))
                break

            state.messages.append(reply)

            # ---- 执行工具 ----
            for call in reply.tool_calls:
                if (why := state.budget.exceeded()):
                    state.status = "done"
                    state.answer = self._budgeted_answer(state, why)
                    break

                res = self.registry.execute(call.name, call.arguments,
                                            self.policy, state)
                state.steps.append(Step(
                    index=state.step_index, kind="tool", tool=call.name,
                    args=call.arguments, result=res.to_llm_text()[:2000],
                    error=res.error, error_type=res.error_type,
                    elapsed=res.elapsed, tokens=0))
                state.step_index += 1
                state.budget.steps_used += 1
                self.observer.on_tool(call.name, call.arguments, res, res.elapsed)
                if self.metrics:
                    self.metrics.record_tool(call.name, res.ok)

                # 提取事实（让模型"读"而不是"回忆"）
                for k, v in self.registry.extract_facts(
                        call.name, call.arguments, res.data).items():
                    state.add_fact(k, v, f"{call.name}({_short(call.arguments)})",
                                   state.step_index - 1)

                if res.pending:
                    state.answer = res.error or "需要人工确认"
                    break

                state.messages.append(
                    Message.tool(call.id, res.to_llm_text(), name=call.name))
                state.budget.tokens_used += rough_tokens(res.to_llm_text())

            if state.status == "waiting_confirm":
                break
            if state.status == "done":
                break

        self.observer.on_finish(state)
        if self.metrics:
            self.metrics.record_run(state)
        return state

    # ------------------------------------------------------ 上下文增强
    def _context_extras(self, state: AgentState) -> list[Message]:
        """每轮注入一次事实表 —— 这是"结构化工作记忆"的落地。"""
        if not state.facts:
            return []
        facts = state.facts[-self.context_max_facts:]
        text = ("## 已确认的事实（来自工具，可直接引用；不要凭记忆改动数字）\n"
                "| 事实 | 值 | 出处 |\n|---|---|---|\n"
                + "\n".join(f"| {f.key} | {f.value} | {f.source} @step{f.step} |"
                            for f in facts))
        return [Message.system(text)]

    def _budgeted_answer(self, state: AgentState, why: str) -> str:
        """预算耗尽的**优雅收尾** —— 返回已有结论，而不是抛异常。"""
        lines = [f"## 提前结束（{why}）", "",
                 "以下是已经完成的部分，**结论仅覆盖已完成的部分**：", ""]
        done = [s for s in state.steps if s.kind == "tool" and not s.error]
        if done:
            lines.append("### 已执行的步骤")
            for s in done:
                lines.append(f"- {s.tool}（{s.elapsed:.2f}s）")
        facts = state.fact_map()
        if facts:
            lines.append("")
            lines.append("### 已确认的事实")
            for k, v in facts.items():
                lines.append(f"- {k} = {v}")
        lines.append("")
        lines.append(f"未完成的部分需要重新发起任务或放宽预算（{why}）。")
        return "\n".join(lines)


def _short(d: dict) -> str:
    s = json.dumps(d, ensure_ascii=False, default=str)
    return s if len(s) <= 60 else s[:57] + "…"
