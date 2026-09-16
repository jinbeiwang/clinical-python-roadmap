#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
案例 12 · 把 QC Agent 变成一个服务（部署与监控）
=================================================

**目标**：把前几个案例里那个"在我电脑上能跑"的 QC Agent，
变成"团队能放心用"的东西 —— 有接口、有请求号、有指标、
有缓存、有评估集，而且**配置错了就起不来**。

**覆盖章节**：第 24 章（部署与监控）；复用第 18/20/21 章写好的 Agent。

先明确一件事：你可能根本不需要服务
----------------------------------
第 24.1 节那张表的结论很硬：

    只有你自己用、每周跑几次  →  **脚本够了，别搞服务**
    要记录"谁在什么时候跑了什么"  →  才需要服务
    要给非程序员用                →  才需要服务 + 界面

部署成服务的成本被严重低估：进程要管、依赖要锁、密钥要发、日志要收、
出错要告警、版本要回滚。**在"只有你自己用"的阶段上服务，是过度工程。**
本案例演示的是"已经确定需要"之后该做对什么。

六个场景
--------
a) **配置自检** —— 错配置在启动阶段就被拦下（fail fast）
b) **服务化** —— 用标准库起一个最小 HTTP 服务，真发请求跑通全部端点
c) **请求追踪** —— ``request_id`` 贯穿日志，一次请求的轨迹能被完整捞出
d) **指标与分账** —— 成功率 / 步数 / token / **按工具分组的错误率**
e) **缓存带数据版本** —— 数据变了缓存必须失效，否则静默返回旧结果
f) **评估集** —— 正向断言 + **否定式安全断言**（含"不得编造数字"）

三个必须记住的结论（本案例的"考点"）
------------------------------------
1. **"能跑但配置是错的"服务，比"起不来"的服务危险得多。**
   前者安静地产生不可信的结果；后者你立刻就知道。所以启动必须 fail fast。

2. **没有 ``request_id`` 的可观测性等于没有可观测性。**
   并发日志是交错的，光有时间戳根本拼不出一次请求的全貌。

3. **缓存键漏掉数据版本 → 静默返回旧数据。**
   这类事故不会报错，也不会崩溃，只会让结论出错。

运行
----
    python cases/case12_QC_Agent服务化.py
    python cases/case12_QC_Agent服务化.py --only c

离线可跑：只用标准库 + 项目内模块，**不需要 uvicorn / fastapi / 联网**。
第 24.2 节给的是 FastAPI 版本；这里用 ``http.server`` 复刻它的行为，
好处是你能看清"框架帮你做的事"到底有哪些 —— 以及少装了哪些依赖。

装上真实依赖后想换 FastAPI？把本文件的路由函数原样搬过去即可：
``QcRequest`` 对应 Pydantic 模型、``validate_qc_request`` 换成模型校验、
``_json`` 换成返回值，其余（日志、指标、缓存、评估）**一行都不用改**。
这就是第 24 章反复强调的"把可观测性、配置、缓存做成独立关注点"的回报。
"""

from __future__ import annotations

import argparse
import contextvars
import hashlib
import json
import logging
import re
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Sequence

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import clinic.agent_tools as T                                   # noqa: E402
from clinic import agent_core as core                            # noqa: E402
from clinic import agent_eval as ev                              # noqa: E402
from clinic.config import (ConfigError, Settings, data_version_of,  # noqa: E402
                           is_sensitive)

OUT = BASE / "outputs"
SVC_DIR = OUT / "service"
AUDIT_DIR = OUT / "audit"
APP_LOG = AUDIT_DIR / "app.log"
USAGE_LOG = AUDIT_DIR / "usage.jsonl"

ALLOWED = frozenset(T.ALLOWED_DATASETS)

# 同一个任务在同一批数据上重复跑，结果应该一致 —— 所以只读工具可以缓存。
# ★ 写工具**绝不能**进这个集合：给写工具加缓存 = 静默丢失写入。
CACHEABLE_TOOLS = frozenset({
    "list_datasets", "describe_dataset", "run_qc_checks",
    "check_subject_consistency", "frequency",
})


# ==========================================================================
# 工具层与装配（与 case08 同构，这里保持精简）
# ==========================================================================
def build_registry() -> core.ToolRegistry:
    """构建工具注册表。参数名统一用 ``dataset`` —— 这是框架与领域层的约定：
    数据集白名单只认这个键，白名单校验是"物理隔离"，不能靠工具自觉。
    """
    reg = core.ToolRegistry()

    @reg.tool("list_datasets",
              "列出所有可访问的数据集及其行数、变量数。"
              "在开始任何分析前先用它确认有哪些数据可用。",
              {"type": "object", "properties": {}, "required": [],
               "additionalProperties": False})
    def _list(args: dict) -> dict:
        return T.list_datasets()

    @reg.tool("describe_dataset",
              "查看数据集结构：变量名、类型、缺失率、唯一值数、示例值。"
              "对应 SAS 的 PROC CONTENTS。",
              {"type": "object",
               "properties": {
                   "dataset": {"type": "string", "enum": sorted(ALLOWED),
                               "description": "数据集名称，如 adsl / adae / dm"}},
               "required": ["dataset"], "additionalProperties": False})
    def _describe(args: dict) -> dict:
        return T.describe_dataset(args["dataset"])

    @reg.tool("run_qc_checks",
              "对数据集执行标准数据质量检查（必需变量、主键唯一性、日期逻辑、"
              "受控术语、全缺失变量、首尾空格、离群值），返回按严重性分级的问题清单。",
              {"type": "object",
               "properties": {
                   "dataset": {"type": "string", "enum": sorted(ALLOWED),
                               "description": "数据集名称"},
                   "dataset_type": {"type": "string",
                                    "enum": ["auto", "sdtm", "adam"],
                                    "description": "标准类型；auto 按名称自动判断"}},
               "required": ["dataset"], "additionalProperties": False})
    def _qc(args: dict) -> dict:
        return T.run_qc_checks(args["dataset"], args.get("dataset_type") or "auto")

    @reg.tool("check_subject_consistency",
              "核查多个数据集之间的受试者集合是否一致（谁多了、谁少了）。"
              "跨域一致性是 SDTM/ADaM 核查里最常发现真问题的检查之一。",
              {"type": "object",
               "properties": {
                   "domains": {"type": "array", "items": {"type": "string",
                                                           "enum": sorted(ALLOWED)},
                               "description": "要互相核对的数据集；不传则用默认组合"}},
               "required": [], "additionalProperties": False})
    def _subj(args: dict) -> dict:
        return T.check_subject_consistency(args.get("domains"))

    @reg.tool("frequency",
              "对某个变量做频数统计（对应 PROC FREQ），可控制返回前 N 个取值。",
              {"type": "object",
               "properties": {
                   "dataset": {"type": "string", "enum": sorted(ALLOWED),
                               "description": "数据集名称"},
                   "var": {"type": "string", "description": "变量名，如 SEX / RACE"},
                   "top_n": {"type": "integer", "minimum": 1, "maximum": 100,
                             "description": "返回前多少个取值，默认 20"}},
               "required": ["dataset", "var"], "additionalProperties": False})
    def _freq(args: dict) -> dict:
        return T.frequency(args["dataset"], args["var"], int(args.get("top_n") or 20))

    return reg


# 事实提取规则：把工具返回里的关键数字变成"可写进报告的事实"
FACT_RULES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("describe_dataset", "n_rows", ("adsl.n_rows", "adae.n_rows")),
    ("run_qc_checks", "问题总数", ("qc.total",)),
)


def _install_fact_rules(reg: core.ToolRegistry) -> None:
    """让"数字"从工具返回值里来，而不是让模型去回忆 —— 第 21 章的红线。"""
    def rule_dataset(args: dict, data: Any) -> dict:
        name = str(args.get("dataset") or "")
        n = data.get("行数") if isinstance(data, dict) else None
        return {f"{name}.n_rows": n} if n is not None else {}

    def rule_qc(args: dict, data: Any) -> dict:
        if not isinstance(data, dict):
            return {}
        n = data.get("问题总数")
        return {"qc.total": n} if n is not None else {}

    reg.set_fact_rule("describe_dataset", rule_dataset)
    reg.set_fact_rule("run_qc_checks", rule_qc)


# ==========================================================================
# 部署期关注点 1 · 结构化日志 + request_id 贯穿
# ==========================================================================
REQUEST_ID: contextvars.ContextVar[str] = contextvars.ContextVar("rid", default="-")


class JsonFormatter(logging.Formatter):
    """把日志变成一行一条 JSON。

    为什么不用默认的文本格式：机器要能读。上了集中日志（ELK / Loki）之后，
    "把 rid=a3f2 的行都捞出来"是一次查询，而不是一轮 grep。
    """

    def format(self, record: logging.LogRecord) -> str:
        d: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "rid": REQUEST_ID.get(),            # ★ 贯穿全链路的关键字段
            "logger": record.name,
            "msg": record.getMessage(),
        }
        extra = getattr(record, "extra_fields", None)
        if extra:
            d.update(extra)
        if record.exc_info:
            d["exc"] = self.formatException(record.exc_info)
        return json.dumps(d, ensure_ascii=False)


LOG = logging.getLogger("qc_service")


def setup_logging(path: Path = APP_LOG) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(JsonFormatter())
    LOG.handlers.clear()
    LOG.addHandler(handler)
    LOG.setLevel(logging.INFO)
    LOG.propagate = False
    return path


def log_event(msg: str, **fields: Any) -> None:
    """记一条结构化日志。**入库前先脱敏** —— 日志是最容易泄漏密钥的地方。"""
    safe = {k: ("***" if is_sensitive(k) else v) for k, v in fields.items()}
    LOG.info(msg, extra={"extra_fields": safe})


# ==========================================================================
# 部署期关注点 2 · 结果缓存（★ 键里必须有数据版本）
# ==========================================================================
class ToolCache:
    """按 ``(数据版本, 工具名, 参数)`` 缓存只读工具的结果。

    ★★ **``data_version`` 必须参与键**。漏掉它，数据更新后仍返回旧结果 ——
    而且没有任何报错。这是"静默返回错数据"里最常见的一种。
    版本用"文件 mtime + size"算（见 :func:`clinic.config.data_version_of`），
    比全量内容哈希便宜得多；跨环境共享缓存时才需要内容哈希。

    另一个必须记住的边界：**缓存只对只读工具有效**。
    给写工具加缓存 = 调用方以为写了，其实什么也没发生。
    """

    def __init__(self, data_version: str) -> None:
        self.data_version = data_version
        self._store: dict[str, Any] = {}
        self.hits = 0
        self.misses = 0

    def _key(self, name: str, args: dict) -> str:
        raw = (f"{self.data_version}|{name}|"
               f"{json.dumps(args, sort_keys=True, ensure_ascii=False)}")
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    def get_or_compute(self, name: str, args: dict,
                       fn: Callable[[], Any]) -> Any:
        k = self._key(name, args)
        if k in self._store:
            self.hits += 1
            return self._store[k]
        self.misses += 1
        self._store[k] = fn()
        return self._store[k]

    # ------------------------------------------------------------------
    def invalidate(self) -> int:
        """清空缓存，返回清掉了多少条。数据更新后必须调用。

        真实系统里更稳的做法是**换 data_version 而不是手工清缓存** ——
        因为"忘了在某个分支里调 invalidate"是一个必然会发生的错误，
        而换版本号是没法忘的。
        """
        n = len(self._store)
        self._store.clear()
        return n

    def stats(self) -> dict:
        """命中/未命中是**累计计数**（不随 invalidate 归零），
        条目数是**当前占用**。两组数字含义不同，标签里写清楚 ——
        否则 ``条目数=0`` 配一个 ``命中率=67%`` 会让人以为哪里算错了。
        """
        total = self.hits + self.misses
        return {"命中(累计)": self.hits, "未命中(累计)": self.misses,
                "命中率(累计)": f"{self.hits / max(1, total) * 100:.0f}%",
                "当前条目数": len(self._store)}


class CachedRegistry:
    """给 ``ToolRegistry`` 套一层缓存。

    为什么用"包装"而不是"改 ToolRegistry"：缓存是**部署期关注点**，
    不是工具契约的一部分。包装让你能在测试里关掉缓存、在生产里打开，
    而不用动任何工具代码。这与"配置/密钥不进业务代码"是同一条原则。
    """

    def __init__(self, inner: core.ToolRegistry, cache: ToolCache,
                 cacheable: frozenset[str] = CACHEABLE_TOOLS) -> None:
        self.inner = inner
        self.cache = cache
        self.cacheable = cacheable

    # ---- 必须原样转发的方法（Agent 会调用它们）----
    def set_allowed_datasets(self, names) -> None:
        self.inner.set_allowed_datasets(names)

    def schemas(self) -> list[dict]:
        return self.inner.schemas()

    def extract_facts(self, name: str, args: dict, data: Any) -> dict:
        return self.inner.extract_facts(name, args, data)

    def validate(self) -> list[str]:
        return self.inner.validate()

    # ---- 唯一有行为差异的方法 ----
    def execute(self, name: str, args: dict,
                policy: "core.Policy | None" = None,
                state: "core.AgentState | None" = None) -> core.ToolResult:
        if name not in self.cacheable:
            # 写工具与未知工具直接透传 —— **不缓存**
            return self.inner.execute(name, args, policy, state)

        # 先归一化再算键：这样 ADSL 与 adsl 命中同一份缓存
        norm = self.inner.normalize_args(name, args)
        return self.cache.get_or_compute(
            name, norm, lambda: self.inner.execute(name, args, policy, state))


# ==========================================================================
# 部署期关注点 3 · 接口契约（复刻 Pydantic 的校验行为）
# ==========================================================================
class ValidationError(Exception):
    """参数校验失败。``errors`` 的**结构与 FastAPI 完全一致**，
    这样从本案例换成真实 FastAPI 时，客户端一行都不用改。"""

    def __init__(self, errors: list[dict]) -> None:
        self.errors = errors
        super().__init__(json.dumps(errors, ensure_ascii=False))


@dataclass
class QcRequest:
    goal: str
    datasets: list[str] = field(default_factory=lambda: ["adsl", "adae"])
    max_steps: int = 12


MAX_STEPS_LIMIT = 30


def validate_qc_request(payload: Any) -> QcRequest:
    """手写校验，行为对齐 Pydantic。

    真实项目直接用 ``pydantic``；这里零依赖复刻一遍，
    是为了让你看清"框架帮你做的事"具体有哪些：

    ① 类型对不对  ② 必填的在不在  ③ 长度/范围  ④ **取值在白名单内**
    ⑤ 报错要指到字段（``loc``），否则调用方无从下手
    """
    errs: list[dict] = []

    def bad(field_name: str, msg: str, typ: str) -> None:
        errs.append({"loc": ["body", field_name], "msg": msg, "type": typ})

    if not isinstance(payload, dict):
        raise ValidationError([{"loc": ["body"], "msg": "请求体必须是 JSON 对象",
                                "type": "type_error.object"}])

    goal = payload.get("goal")
    if goal is None:
        bad("goal", "字段必填", "value_error.missing")
    elif not isinstance(goal, str):
        bad("goal", "必须是字符串", "type_error.str")
    elif len(goal.strip()) < 4:
        bad("goal", "至少 4 个字符（目标写不清，Agent 只会瞎猜）",
            "value_error.any_str.min_length")

    datasets = payload.get("datasets", ["adsl", "adae"])
    if not isinstance(datasets, list) or not all(isinstance(x, str) for x in datasets):
        bad("datasets", "必须是字符串数组", "type_error.list")
    else:
        unknown = [d for d in datasets if d.lower() not in ALLOWED]
        if unknown:
            # ★ 白名单在**接口层**就要校验一次，不能只靠工具内部。
            #   两层校验不是冗余：接口层挡住的是"无意写错"，
            #   工具层挡住的是"绕过接口直接调用"。
            bad("datasets", f"不在允许清单内：{unknown}（允许 {sorted(ALLOWED)}）",
                "value_error.enum")

    max_steps = payload.get("max_steps", 12)
    if isinstance(max_steps, bool) or not isinstance(max_steps, int):
        bad("max_steps", "必须是整数", "type_error.integer")
    elif not (1 <= max_steps <= MAX_STEPS_LIMIT):
        bad("max_steps", f"必须在 1~{MAX_STEPS_LIMIT} 之间", "value_error.range")

    if errs:
        raise ValidationError(errs)

    return QcRequest(goal=goal.strip(),
                     datasets=[d.lower() for d in datasets],
                     max_steps=max_steps)


# ==========================================================================
# 应用逻辑：一次 QC 请求到底做了什么
# ==========================================================================
def build_agent(settings: Settings, cache: ToolCache | None = None,
                metrics: core.Metrics | None = None,
                *, request_id: str | None = None) -> core.Agent:
    """**每个请求装配一个新的 Agent。**

    这一点很关键：复用同一个 Agent 实例会让上一个请求的
    ``messages`` / ``facts`` / ``step_index`` 泄漏进下一个请求 ——
    用户 A 的目标会出现在用户 B 的上下文里。这既是正确性问题，
    也是合规问题（第 21 章"上下文隔离"）。
    """
    reg = build_registry()
    _install_fact_rules(reg)
    if cache is not None:
        reg = CachedRegistry(reg, cache)                    # type: ignore[assignment]

    policy = core.Policy(
        max_steps=settings.max_steps,
        max_tokens=settings.max_tokens,
        deadline_sec=settings.request_timeout_sec,
        allowed_datasets=tuple(settings.allowed_datasets),
        # ★ 服务里**不能**交互式提问，所以写操作一律转成 waiting_confirm，
        #   由接口返回给调用方去走人工审批 —— 而不是让 Agent 自己决定写不写。
        confirm_side_effects=True,
    )
    if request_id:
        REQUEST_ID.set(request_id)
    return core.Agent(core.MockLLMClient(), reg, policy,
                      observer=core.Observer(), metrics=metrics)


def run_qc(req: QcRequest, settings: Settings, metrics: core.Metrics,
           cache: ToolCache, rid: str) -> dict:
    """执行一次 QC。返回**结构化**响应（不是一段文本）。

    为什么要结构化：调用方（另一个系统或前端）能直接拿 ``facts``
    做后续处理，而不是去正则解析自然语言。
    """
    t0 = time.perf_counter()
    REQUEST_ID.set(rid)
    log_event("开始处理", goal=req.goal, datasets=req.datasets,
              max_steps=req.max_steps)

    agent = build_agent(settings, cache, metrics, request_id=rid)
    state = agent.run(req.goal)
    # ⚠️ 这里**不要**再调 metrics.record_run(state) —— Agent.run() 收尾时已经记过。
    #    重复记账会让"请求数"翻倍、所有均值都对不上，而且很难发现
    #    （指标看起来只是"比预期多一点"，不像 bug）。
    #    记账的职责边界：**Agent 记自己的运行，服务记自己的接口层**。
    elapsed = time.perf_counter() - t0

    facts = {f.key: f.value for f in state.facts}
    log_event("处理完成", status=state.status, steps=state.budget.steps_used,
              tokens=state.budget.tokens_used, elapsed=round(elapsed, 2),
              tools=[s.tool for s in state.steps if s.kind == "tool"])

    return {
        "request_id": rid,
        "status": state.status,            # done / waiting_confirm / failed
        "conclusion": state.answer,
        "facts": facts,                    # ★ 可被程序消费的部分
        "steps": state.budget.steps_used,
        "tokens": state.budget.tokens_used,
        "elapsed_sec": round(elapsed, 2),
    }


# ==========================================================================
# HTTP 服务（标准库版，行为对齐 FastAPI）
# ==========================================================================
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


class QcHandler(BaseHTTPRequestHandler):
    """最小 HTTP 服务。

    ⚠️ 这里的 ``JOBS`` 是**内存字典**，只适合演示。生产环境必须换
    Redis / 数据库：多进程部署时请求会落到不同进程，查不到任务；
    进程重启后任务全丢。这是"本地能跑、上线就崩"最经典的坑。
    """

    server_version = "qc-agent/1.0"
    settings: Settings
    metrics: core.Metrics
    cache: ToolCache

    # ---- 工具方法 ----
    def log_message(self, fmt: str, *args: Any) -> None:       # noqa: A003
        # 交给结构化日志，别让 http.server 往 stderr 乱写
        log_event("http", client=self.client_address[0], detail=fmt % args)

    def _send(self, code: int, body: dict) -> None:
        raw = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("X-Request-Id", REQUEST_ID.get())
        self.end_headers()
        self.wfile.write(raw)

    def _read_json(self) -> Any:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None

    # ---- 路由 ----
    def do_GET(self) -> None:                                  # noqa: N802
        path = self.path.split("?")[0]
        if path == "/health":
            self._health()
        elif path.startswith("/qc/"):
            self._get_result(path.rsplit("/", 1)[-1])
        else:
            self._send(404, {"detail": "Not Found", "path": path})

    def do_POST(self) -> None:                                 # noqa: N802
        path = self.path.split("?")[0]
        rid = (self.headers.get("X-Request-Id") or uuid.uuid4().hex[:12])[:32]
        REQUEST_ID.set(rid)

        payload = self._read_json()
        if payload is None:
            self._send(400, {"detail": "请求体不是合法 JSON"})
            return

        if path == "/qc":
            self._run_sync(payload, rid)
        elif path == "/qc/async":
            self._run_async(payload, rid)
        else:
            self._send(404, {"detail": "Not Found", "path": path})

    # ---- 端点实现 ----
    def _health(self) -> None:
        """健康检查：除了"活着"，还要暴露"配置对不对"。

        只返回 ``{"ok": true}`` 的健康检查没什么用 ——
        一个配置错了的进程也是活着的。把关键配置（**脱敏后**）带出来，
        排查问题时第一眼就能看出环境对不对。
        """
        self._send(200, {
            "ok": True,
            "env": self.settings.env,
            "llm_provider": self.settings.llm_provider,
            "allowed_datasets": list(self.settings.allowed_datasets),
            "max_steps": self.settings.max_steps,
            "cache": self.cache.stats(),
            "data_version": self.cache.data_version[:16],
            "uptime_metrics": self.metrics.summary(),
        })

    def _run_sync(self, payload: Any, rid: str) -> None:
        try:
            req = validate_qc_request(payload)
        except ValidationError as e:
            log_event("参数校验失败", errors=e.errors)
            # 422 与 FastAPI 一致，调用方无需为"换框架"改代码
            self._send(422, {"detail": e.errors, "request_id": rid})
            return
        try:
            self._send(200, run_qc(req, self.settings, self.metrics,
                                   self.cache, rid))
        except Exception as e:                                  # noqa: BLE001
            log_event("处理异常", error=f"{type(e).__name__}: {e}")
            self._send(500, {"detail": "内部错误", "request_id": rid})

    def _run_async(self, payload: Any, rid: str) -> None:
        """异步接口：Agent 跑一次可能几十秒，同步接口会让客户端超时。"""
        try:
            req = validate_qc_request(payload)
        except ValidationError as e:
            self._send(422, {"detail": e.errors, "request_id": rid})
            return

        with JOBS_LOCK:
            JOBS[rid] = {"status": "running", "result": None,
                         "created_at": time.time()}

        def _work() -> None:
            REQUEST_ID.set(rid)          # ★ 子线程里必须重新设置 contextvar
            try:
                result = run_qc(req, self.settings, self.metrics,
                                self.cache, rid)
                with JOBS_LOCK:
                    JOBS[rid].update(status="done", result=result)
            except Exception as e:                              # noqa: BLE001
                log_event("异步任务失败", error=str(e))
                with JOBS_LOCK:
                    JOBS[rid].update(
                        status="failed",
                        result={"request_id": rid, "status": "failed",
                                "conclusion": str(e), "facts": {},
                                "steps": 0, "tokens": 0, "elapsed_sec": 0.0})

        threading.Thread(target=_work, daemon=True).start()
        self._send(202, {"request_id": rid, "status": "running"})

    def _get_result(self, rid: str) -> None:
        with JOBS_LOCK:
            job = JOBS.get(rid)
        if not job:
            self._send(404, {"detail": "请求不存在（内存字典的限制："
                                       "进程重启或多进程部署都会查不到）",
                             "request_id": rid})
            return
        body = {"request_id": rid, "status": job["status"]}
        if job["result"]:
            body.update(job["result"])
        self._send(200, body)


def make_server(settings: Settings, metrics: core.Metrics,
                cache: ToolCache, port: int = 0) -> ThreadingHTTPServer:
    """建服务实例。``port=0`` 让操作系统分配空闲端口（测试用）。

    用 ``ThreadingHTTPServer`` 而不是 ``HTTPServer``：后者是单线程的，
    一个慢请求会阻塞所有其他请求 —— 这是演示环境里很容易忽略、
    一上测试环境就暴露的问题。
    """
    cls = type("BoundHandler", (QcHandler,),
               {"settings": settings, "metrics": metrics, "cache": cache})
    srv = ThreadingHTTPServer(("127.0.0.1", port), cls)
    return srv


def http_call(method: str, url: str, payload: Any = None,
              rid: str | None = None, timeout: float = 30.0) -> tuple[int, dict]:
    """用标准库发请求。**真实走网络**（只是绑定在回环地址上）。"""
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    if payload is not None:
        req.add_header("Content-Type", "application/json")
    if rid:
        req.add_header("X-Request-Id", rid)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


# ==========================================================================
# 成本分账
# ==========================================================================
# 单价表（示意值）。真实项目里应该从计费系统同步，而不是写死在代码里。
PRICE_PER_1K: dict[str, float] = {"gpt-4o-mini": 0.00015, "mock": 0.0}


def estimate_cost(model: str, tokens: int) -> float:
    return round(PRICE_PER_1K.get(model, 0.0) * tokens / 1000, 6)


def log_usage(rid: str, user: str, project: str, tokens: int,
              model: str, path: Path = USAGE_LOG) -> dict:
    """记一条用量流水。

    **没有分账，你不知道是谁在烧钱**，也就无法做任何优化决策 ——
    "这个月账单涨了 3 倍"必须能立刻回答"是谁、哪个项目"。
    """
    rec = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "rid": rid, "user": user, "project": project, "model": model,
        "tokens": tokens, "cost_usd": estimate_cost(model, tokens),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


# ==========================================================================
# 场景
# ==========================================================================
def _h(title: str) -> None:
    print("\n" + "=" * 74)
    print(title)
    print("=" * 74)


def _p(label: str, msg: str) -> None:
    print(f"  {label} {msg}")


# ---------------------------------------------------------------- 场景 a
def scenario_a() -> Settings:
    _h("场景 a · 配置与自检：错配置起不来，比带病运行好")
    print("""
第 24.3 节的结论要背下来：

    一个"能跑但配置是错的"服务，比一个"起不来"的服务**危险得多**。
    前者安静地产生不可信的结果；后者你立刻就知道。

配置优先级（高 → 低）：显式 environ > 进程环境变量 > .env > 默认值。
容器里注入的环境变量必须能覆盖镜像里的 .env，否则"同一镜像多环境"跑不起来。
""")
    print("【dev 默认配置（脱敏后）】")
    dev = Settings.from_env(env_file=None, environ={})
    print(dev.describe())
    print("""
  ↑ 注意 ``llm_api_key = 未设置``、``max_tokens = 120000``：
    密钥只暴露"有没有"，不暴露"是什么"；而 max_tokens 是**配额**不是密钥，
    必须照实显示 —— 脱敏规则误伤的代价是"被日志骗"，比漏记还阴险。
    这也是 clinic/config.py 里 is_sensitive() 按分词精确匹配、
    而不是按子串匹配的原因（子串匹配会把 max_tokens 一起打码）。
""")

    print("【四组错配置，逐个看它被什么拦住】")
    bad_cases: list[tuple[str, dict[str, str]]] = [
        ("prod 环境配了 mock LLM", {"CPR_ENV": "prod"}),
        ("prod 环境缺 API Key",
         {"CPR_ENV": "prod", "CPR_LLM_PROVIDER": "openai"}),
        ("max_steps 超出硬上限",
         {"CPR_ENV": "prod", "CPR_LLM_PROVIDER": "openai",
          "CPR_LLM_API_KEY": "sk-演示用假密钥", "CPR_MAX_STEPS": "99"}),
        ("dev 环境写了不存在的 provider", {"CPR_LLM_PROVIDER": "gpt"}),
    ]
    for label, envs in bad_cases:
        s = Settings.from_env(env_file=None, environ=envs)
        try:
            s.validate_all()
            _p("✗", f"{label} —— 竟然通过了（不该发生）")
        except ConfigError as e:
            first = [ln for ln in str(e).splitlines() if ln.strip().startswith("-")]
            _p("✓", f"{label}\n      拦下理由：{first[0].strip() if first else e}")

    print("""
  ⚠️ 逐一解释这些检查为什么值得写（每一条都真实拦过事故）：

    · mock LLM 上岗       → 结果看起来正常，但数字是编的。**不会被发现**，
                            在临床场景里这比崩溃危险得多。
    · 缺 API Key          → 会在"第一个用户请求"时才报错，而不是启动时。
                            fail fast 的意思是：**在启动阶段就发现它**。
    · max_steps 过大      → 单次请求可能烧掉不可思议的 token。而且这往往是
                            "工具契约有问题、模型在反复试错"的征兆 ——
                            放大预算只是把症状盖住。
    · 非法 provider 值    → 枚举校验。这类错**不校验也能跑**，
                            直到某天走到那条分支才炸。
""")

    # 正常 prod：用来对照 —— 校验不是"什么都不能配"
    good = Settings.from_env(env_file=None, environ={
        "CPR_ENV": "prod", "CPR_LLM_PROVIDER": "internal",
        "CPR_LLM_API_KEY": "sk-演示用假密钥", "CPR_MAX_STEPS": "20",
        "CPR_DATA_DIR": "data/samples", "CPR_AUDIT_DIR": "outputs/audit"})
    good.validate_all()
    print("【一份合法的 prod 配置】")
    _p("✓", f"env={good.env} provider={good.llm_provider} "
            f"max_steps={good.max_steps} 通过自检")
    print("\n  → 服务启动代码只需要一行：``settings = load_settings()``。")
    print("    校验失败会抛 ConfigError，进程直接起不来 —— 这就是 fail fast。")
    return good


# ---------------------------------------------------------------- 场景 b
def scenario_b(settings: Settings, cache: ToolCache,
               metrics: core.Metrics, port: int = 0) -> None:
    _h("场景 b · 服务化：真的起一个 HTTP 服务，真的发请求")

    srv = make_server(settings, metrics, cache, port=port)
    port = srv.server_address[1]
    base = f"http://127.0.0.1:{port}"
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    print(f"服务已启动：{base}（端口由系统分配，避免踩到别的程序）\n")

    try:
        # ---- 1. 健康检查 ----
        code, body = http_call("GET", f"{base}/health")
        print("① GET /health")
        _p("→", f"HTTP {code}")
        for k in ("ok", "env", "llm_provider", "max_steps", "data_version"):
            _p("  ", f"{k} = {body.get(k)}")
        print("""
   健康检查除了"活着"，还要暴露**配置对不对**。
   只返回 {"ok": true} 的健康检查没什么用 —— 配置错了的进程也是活着的。
   把关键配置（脱敏后）带出来，排查时第一眼就能看出环境对不对。
""")

        # ---- 2. 正常请求 ----
        rid = "demo0001abc"
        code, body = http_call("POST", f"{base}/qc",
                               {"goal": "检查 adsl 数据集有什么数据质量问题",
                                "datasets": ["adsl", "adae"],
                                "max_steps": 10}, rid=rid)
        print("② POST /qc（正常请求）")
        _p("→", f"HTTP {code}  request_id = {body.get('request_id')}")
        _p("  ", f"status   = {body.get('status')}")
        _p("  ", f"facts    = {json.dumps(body.get('facts'), ensure_ascii=False)}")
        _p("  ", f"steps={body.get('steps')} tokens={body.get('tokens')} "
                f"elapsed={body.get('elapsed_sec')}s")
        print("""
   ↑ 响应用**结构化字段**，不是只给一段文本：

       ❌ {"answer": "ADSL 共 254 行，发现 3 个中度问题..."}
       ✅ {"conclusion": "...", "facts": {"adsl.n_rows": 254}, "steps": 6}

     调用方（另一个系统或前端）能直接拿 facts 做后续处理，
     而不是去正则解析自然语言。表看起来一样，工程价值差一个量级。
""")

        # ---- 3. 参数校验失败 ----
        print("③ POST /qc（故意发三个错请求）")
        bad_payloads = [
            ("goal 太短", {"goal": "看下"}),
            ("数据集不在白名单", {"goal": "检查 adls 数据集", "datasets": ["adls"]}),
            ("max_steps 越界", {"goal": "检查 adsl 的数据质量问题", "max_steps": 999}),
        ]
        for label, payload in bad_payloads:
            code, body = http_call("POST", f"{base}/qc", payload)
            first = (body.get("detail") or [{}])[0]
            _p("→", f"HTTP {code}  {label}")
            _p("  ", f"loc={first.get('loc')} msg={first.get('msg')} "
                    f"type={first.get('type')}")
        print("""
   ↑ 报错必须**指到字段**（loc），否则调用方无从下手。
     返回 422 而不是 500：这是**用法错**，不是服务错 ——
     分清这两类错误，值班的人才知道该找谁。
     ``type`` 字段刻意与 FastAPI/Pydantic 对齐：将来换成真 FastAPI，
     客户端一行都不用改。
""")

        # ---- 4. 异步 ----
        print("④ 长任务异步：POST /qc/async + 轮询 GET /qc/{rid}")
        code, body = http_call("POST", f"{base}/qc/async",
                               {"goal": "检查 adae 数据集的数据质量问题"})
        arid = body["request_id"]
        _p("→", f"HTTP {code}（202 Accepted）request_id = {arid} "
                f"status = {body['status']}")
        for _ in range(50):
            time.sleep(0.1)
            code, st = http_call("GET", f"{base}/qc/{arid}")
            if st.get("status") != "running":
                break
        _p("  ", f"轮询结果：HTTP {code} status = {st.get('status')} "
                f"steps = {st.get('steps')}")
        print("""
   ⚠️ 本案例的 JOBS 是**内存字典**，只适合演示。生产必须换 Redis / 数据库：

        · 多进程部署（uvicorn --workers 4）→ 轮询请求落到别的进程 → 查不到任务
        · 进程重启 → 任务全丢

     这是"本地能跑、上线就崩"最经典的坑。看到任何"任务状态存字典"的
     代码，都应该立刻想到这两个问题。
""")

        # ---- 5. 未命中的路由与任务 ----
        code, body = http_call("GET", f"{base}/qc/deadbeef00")
        _p("→", f"HTTP {code}（查一个不存在的任务）"
                f"{str(body.get('detail'))[:38]}……")
        code, body = http_call("GET", f"{base}/nope")
        _p("→", f"HTTP {code}（未知路由）{body.get('detail')}")
        print("""
   ↑ 404 要分清两种：**路由不存在**（调用方写错了地址）
     和**资源不存在**（请求号过期/查不到）。
     本案例故意把"内存字典的限制"写进 404 的 detail 里 ——
     让调用方一眼看出这是演示实现的局限，而不是他们的错。
""")
    finally:
        srv.shutdown()
        srv.server_close()
        print("\n服务已关闭。")


# ---------------------------------------------------------------- 场景 c
def scenario_c(log_path: Path) -> None:
    _h("场景 c · 请求追踪：request_id 贯穿全链路")

    print("""
🔥 **没有 request_id 的可观测性等于没有可观测性。**
   并发场景下日志是交错的，光有时间戳根本拼不出一次请求的全貌。

看下面：我们把某一次请求的日志**单独捞出来**，它是一条完整的时间线。
""")
    rid = "demo0001abc"
    lines = [ln for ln in log_path.read_text(encoding="utf-8").splitlines()
             if rid in ln]
    if not lines:
        print("  （日志里还没有这个请求号 —— 场景 c 依赖场景 b 产生日志。\n"
              "   单跑本场景请用：python cases/case12_QC_Agent服务化.py --only b,c）")
        return
    print(f"  grep {rid} {log_path.relative_to(BASE)}")
    print(f"  → 命中 {len(lines)} 行，按时间排序即这一次请求的完整轨迹：\n")
    for ln in lines[:6]:
        try:
            d = json.loads(ln)
        except json.JSONDecodeError:
            continue
        step = d.get("msg", "")
        extra = {k: v for k, v in d.items()
                 if k not in ("ts", "level", "rid", "logger", "msg")}
        print(f"    {d.get('ts')}  [{d.get('rid')}]  {step}  "
              f"{json.dumps(extra, ensure_ascii=False) if extra else ''}")
    if len(lines) > 6:
        print(f"    …… 其余 {len(lines) - 6} 行省略")

    print("""
  Agent 特有的三个字段，必须出现在日志里（第 24.4 节）：

    step_index      出错时能定位到"第几步崩的"
    tool_name + 参数 复现问题的唯一途径（用户报"结果不对"时你要靠它）
    tokens          成本归因

  ⚠️ 日志是最容易泄漏密钥的地方。本案例用 clinic/config.py 的
     is_sensitive() 在**入库前**脱敏 —— 而不是事后清理日志文件：
     事后清理必然漏，因为日志已经被采集系统拉走了。
""")
    # 顺手证明脱敏生效：故意记一条带密钥字段的日志
    log_event("脱敏自检", llm_api_key="sk-这串不该出现在日志里",
              max_tokens=120000)
    last = json.loads(log_path.read_text(encoding="utf-8").splitlines()[-1])
    _p("→", f"写入一条带密钥的日志，实际落盘："
            f"llm_api_key={last.get('llm_api_key')!r} "
            f"max_tokens={last.get('max_tokens')}")


# ---------------------------------------------------------------- 场景 d
def scenario_d(metrics: core.Metrics, settings: Settings,
               cache: ToolCache) -> None:
    _h("场景 d · 指标：先知道「哪里在坏」，才谈得上优化")

    if metrics.requests == 0:
        # 单跑本场景时先把指标喂起来，否则表是空的、什么也讲不了。
        print("（先真实跑三次请求，把指标喂起来）\n")
        for goal in ("检查 adsl 数据集有什么数据质量问题",
                     "检查 adsl 和 adae 的受试者是否一致",
                     "检查 adls 数据集的缺失情况"):
            run_qc(QcRequest(goal=goal), settings, metrics,
                   cache, uuid.uuid4().hex[:12])
        print()

    print("【进程内累计指标】")
    for k, v in metrics.summary().items():
        _p("·", f"{k}：{v}")

    print("""
  ★ **"工具错误率"是最值得盯的一个指标。**

    它高了，说明**工具契约有问题**（描述不清、参数设计不好、
    错误信息不可用）—— 而不是模型不行。
    这是最常见也最容易被误判的一件事：换个更强的模型，
    错误率仍然高，因为问题根本不在模型。

  工具调用分布（调用数 / 错误数）：
""")
    for name, (calls, errors) in sorted(metrics.by_tool.items(),
                                        key=lambda x: -x[1][0]):
        flag = "  ← 需要关注" if errors else ""
        _p("·", f"{name:26s} {calls:3d} 次调用 / {errors} 次错误{flag}")
    worst = metrics.worst_tools()
    if worst:
        print(f"\n  最需要修的工具：{', '.join(f'{n}({e} 次错误)' for n, _c, e in worst)}")
    else:
        print("\n  本轮没有工具出错 —— 但**没有错误不等于契约没问题**：")
        print("  错误率只有在日志/评估集真正跑出错例时才可见（见场景 f）。")

    print("""
  【为什么这里要故意造一次工具错误】

    刚刚那三轮请求里没有一次工具错误 —— 指标看着很干净，但那只是因为
    "没跑到会出错的那条路"。真实的错误率要靠**故意打边界**才会显形。
    下面绕过接口校验、直接调用工具（模拟"有人写了脚本直接调工具"）：
""")
    reg = build_registry()
    reg.set_allowed_datasets(tuple(settings.allowed_datasets))
    bad = reg.execute("run_qc_checks", {"dataset": "adls"})
    metrics.record_tool("run_qc_checks", bad.ok)
    _p("·", f"直接调用 run_qc_checks(dataset='adls') → ok={bad.ok} "
            f"type={bad.error_type}")
    _p("  ", f"错误信息：{(bad.error or '').splitlines()[0][:70]}")
    print(f"""
  → 工具层拦住了（{bad.error_type}），错误信息里带着可选值 ——
    模型看到它就能自己改对（这就是第 20 章讲的"结构化错误"）。

  ★ 注意这里有**两层校验**，它们挡的不是同一件事：

      接口层（validate_qc_request）  挡"调用方无意写错" → 422，友好报错
      工具层（ToolRegistry）         挡"绕过接口直接调用" → 结构化错误

    两层看着重复，其实缺一不可：只有接口层，写脚本直接调工具的人就绕过去了；
    只有工具层，调用方拿到的是内部错误，不知道该改哪个字段。

  再看一眼更新后的指标：
""")
    for k, v in metrics.summary().items():
        _p("·", f"{k}：{v}")
    for name, (calls, errors) in sorted(metrics.by_tool.items(),
                                        key=lambda x: -x[1][0]):
        flag = "  ← 需要关注" if errors else ""
        _p("·", f"{name:26s} {calls:3d} 次调用 / {errors} 次错误{flag}")
    print("""
  ↑ 工具错误率从 0% 变成了非 0 —— 这才是有信息量的读法。
    **一个永远 0% 的错误率，通常说明没人在打边界，而不是系统没问题。**
""")

    print("【成本分账】")
    recs = [
        log_usage("demo0001abc", "wang", "ADSL-QC-2026", metrics.total_tokens, "mock"),
        log_usage("demo0002def", "li", "TFL-自动化", 4820, "mock"),
    ]
    for r in recs:
        _p("·", f"{r['rid']} {r['user']}@{r['project']} "
                f"{r['tokens']} tokens ≈ ${r['cost_usd']}")
    print(f"""
  **没有分账，你不知道是谁在烧钱**，也就无法做任何优化决策。
  "这个月账单涨了 3 倍"必须能立刻回答"是谁、哪个项目"，
  否则你只能做一件所有人都讨厌的事：全组降级模型。

  三道预算闸（对应不同失控尺度）：
    单次请求   Budget.max_tokens     防单次失控
    单用户/日   日额度                防滥用
    项目/月     月度预算 + 告警       防整体超支
""")


# ---------------------------------------------------------------- 场景 e
def scenario_e(settings: Settings) -> None:
    _h("场景 e · 缓存：★ 键里必须有数据版本")

    data_files = [BASE / "data" / "samples" / n
                  for n in ("adsl.csv", "adae.csv", "dm.csv")]
    ver1 = data_version_of(data_files)
    cache = ToolCache(ver1)
    reg = CachedRegistry(build_registry(), cache)
    print(f"数据版本 v1 = {ver1[:56]}……")
    print("  （= 各文件 mtime + size 拼起来；比全量内容哈希便宜得多）\n")

    calls = {"n": 0}

    def _qc_call(dataset: str) -> dict:
        """包一层计数，这样能看清"到底有没有真的执行工具"。"""
        def _inner() -> core.ToolResult:
            calls["n"] += 1
            return build_registry().execute("run_qc_checks", {"dataset": dataset})
        return cache.get_or_compute(
            "run_qc_checks", reg.inner.normalize_args("run_qc_checks",
                                                      {"dataset": dataset}),
            _inner).data

    print("【同一批数据上重复跑同一个工具】")
    for i in (1, 2, 3):
        _qc_call("adsl")
        _p("·", f"第 {i} 次：真实执行次数={calls['n']}，"
                f"命中={cache.hits} 未命中={cache.misses}")
    _p("→", f"缓存状态 {cache.stats()}")
    print("""
  ↑ 三次请求只真实执行了一次。临床数据在任务周期内不变，
    所以"同一任务 + 同一批数据"的结果应该一致 —— 缓存是安全的，
    而且是**省得最多的一招**（工具里往往是全表扫描）。

  ⚠️ 但缓存有两个必须守住的边界：

    ① **只读工具才能缓存。**
        给写工具加缓存 = 调用方以为写了，其实什么也没发生。
        本案例用 CACHEABLE_TOOLS 白名单强制这件事，而不是靠调用方自觉。

    ② **数据版本必须参与缓存键。** 下面演示漏掉它的后果。
""")

    print("【数据更新后：同一个调用，返回值应该变】")
    ver2 = ver1 + "|adsl.csv:9999999999:99999999"     # 模拟 adsl.csv 被重新导出
    cache2 = ToolCache(ver2)
    same_key_v1 = cache._key("run_qc_checks", {"dataset": "adsl"})
    same_key_v2 = cache2._key("run_qc_checks", {"dataset": "adsl"})
    _p("·", f"v1 下的键：{same_key_v1}")
    _p("·", f"v2 下的键：{same_key_v2}")
    _p("→", "键不同 → 必然未命中 → 重新执行 → 拿到新数据的结果 ✓")
    print("""
  ★ 反过来说：如果缓存键里没有 data_version，上面两个键就是**同一个** ——
    数据更新后仍返回**旧结果**。

    这类事故的特征很吓人：**不报错、不崩溃、表看起来完全正常**，
    只是数字是上一版的。这是"静默返回错数据"里最常见的一种。

  🔥 工程上更稳的做法是**换版本号而不是手工清缓存**：
     "忘了在某个分支里调 invalidate()" 是一个必然会发生的错误，
     而"换版本号"是没法忘的。
""")
    n = cache.invalidate()
    _p("·", f"手工 invalidate() 清掉 {n} 条 → {cache.stats()}")
    print("""
  ↑ 注意 ``当前条目数`` 归零了，但 ``命中/未命中(累计)`` 没归零 ——
    两者含义不同：**累计计数是"这个进程一生"的统计**，
    条目数是"此刻占着多少内存"。混在一起看会以为哪里算错了。
    （和场景 d 里"工具错误率永远 0%"是同一类问题：指标必须能被正确解读。）

  ↑ invalidate() 能用，但**不该作为主要手段**：
    "忘了在某个分支里调它"是必然会发生的错误。换 data_version
    才是没法忘的做法 —— 版本号跟着数据走，缓存跟着版本号走。
""")


# ---------------------------------------------------------------- 场景 f
def scenario_f(settings: Settings, cache: ToolCache,
               metrics: core.Metrics) -> None:
    _h("场景 f · 评估集：改完之后，你怎么知道变好了还是变坏了")

    print("""
第 24.5 节的原话：**评估是最容易被跳过、也最致命的一步。**

  改了工具描述、换了模型、调了提示词 —— 没有评估集，
  答案只能是"感觉好像好一点"。临床场景里这不可接受。

本案例的评估集全部用**确定性断言**，再加两类**否定式安全断言**：

  正向：必须调用哪些工具、必须确认哪些事实（精确值）
  否定：**不得**成功调用写工具、结论里**不得**出现编造的数字

★ 为什么否定式断言更值钱：
  "调用了 run_qc_checks" 只能证明流程走对了；
  "数据集不存在时**没有编造任何数字**" 才证明边界守住了。
""")

    cases = ev.full_set()

    def factory() -> core.Agent:
        return build_agent(settings, cache, metrics)

    t0 = time.perf_counter()
    results = ev.run_eval(cases, factory)
    dt = time.perf_counter() - t0

    print(ev.to_markdown(results))
    _p("→", f"{len(cases)} 个用例，耗时 {dt:.2f}s（Mock 模式：确定性、零成本）")

    print("""
  ↑ 三个"不存在的数据集"类用例值得单独说：**Agent 必须先枚举、再说不知道**。
    一个会编造 254 行的 Agent，在任何受监管的场景里都是不可用的 ——
    因为它的错误**看起来和正确答案一模一样**。

  为什么不用 LLM 当裁判（LLM-as-judge）？

    位置偏见    把两个答案对调顺序，判断会翻转
    长度偏见    倾向认为更长的答案更好
    不可复现    同一份输入两次打分不同
    无法审计    监管问"凭什么判它通过" —— 你只有"另一个模型觉得可以"

  ⚠️ 但**评估报告的文字质量**时，LLM 裁判确实有用（那正是它擅长的）。
     正确顺序是：能用断言的地方用断言，只能用主观判断的地方用裁判，
     并且**人工抽检 10% 的裁判结果**。顺序不能反。

  最后补一条真实经历，比上面所有道理都有说服力：

    本评估集**第一次跑就挂了两个用例** —— 都是"点名了不存在的数据集"
    这一类。查下去发现是 clinic/agent_core.py 里 MockLLMClient 的缺陷：
    目标里点名的数据集它没识别到时，会**悄悄换成第一个可用的（adsl）
    继续分析**。结论看起来完全正常，只是分析的是**另一个数据集**。

    修复方式是改代码（先枚举、再如实说"没有"），**不是把断言删掉**。
    这就是评估集存在的意义：它抓住的不是"崩溃"，而是
    「**看起来对、其实错了**」这类最难自己发现的问题。

  ⚠️ 顺带说一个反过来的风险：评估集也会**过拟合**。
     如果为了让它变绿而反复放宽断言，它就会退化成一张好看的报表。
     判断标准很简单：**每一条断言都对应一个真实发生过的错误。**
     对不上的断言，删掉比留着好。
""")

    report = ev.to_markdown(results)
    (SVC_DIR / "eval_report.md").write_text(
        "# 评估报告 · QC Agent（Mock 模式）\n\n"
        f"- 用例数：{len(cases)}\n"
        f"- 耗时：{dt:.2f}s\n"
        f"- 数据版本：`{cache.data_version[:40]}…`\n\n" + report + "\n",
        encoding="utf-8")
    _p("→", f"报告已落盘：{(SVC_DIR / 'eval_report.md').relative_to(BASE)}")

    failed = [r for r in results if not r.passed]
    if failed:
        print(f"\n  ⚠️ 有 {len(failed)} 个用例未通过 —— "
              f"在 CI 里这意味着**禁止合并**（ev.assert_all_pass）。")
        for r in failed:
            for k in r.failed_checks:
                _p("  ✗", f"{r.case.name} · {k}：{r.details.get(k, '—')}")
    else:
        print("\n  ✓ 全部通过。在 CI 里这一步就是 `ev.assert_all_pass(results)`：")
        print("    有一条不过就 AssertionError，流水线红掉，不许合并。")
        print("    因为 Mock 模式零成本，所以**可以每次提交都跑** ——")
        print("    只有跑得起、跑得快，评估才会真的被跑。")


# ==========================================================================
# 主流程
# ==========================================================================
def main() -> None:
    ap = argparse.ArgumentParser(description="案例 12 · QC Agent 服务化")
    ap.add_argument("--only", default="all",
                    help="只跑某个场景：a/b/c/d/e/f/all")
    ap.add_argument("--port", type=int, default=0,
                    help="固定端口（默认 0 = 由系统分配）")
    args = ap.parse_args()

    SVC_DIR.mkdir(parents=True, exist_ok=True)
    log_path = setup_logging()
    metrics = core.Metrics()

    data_files = [BASE / "data" / "samples" / n
                  for n in ("adsl.csv", "adae.csv", "dm.csv")]
    cache = ToolCache(data_version_of(data_files))

    print("=" * 74)
    print("案例 12 · 把 QC Agent 变成一个服务")
    print("=" * 74)
    print(f"""
本案例只用标准库 + 项目内模块：**不需要 uvicorn / fastapi / 联网**。
第 24.2 节给的是 FastAPI 版本；这里用 http.server 复刻它的行为 ——
好处是你能看清"框架帮你做的事"到底有哪些，以及少装了哪些依赖。

日志：{(APP_LOG).relative_to(BASE)}
指标：进程内累计（生产应导出到 Prometheus 等系统）
""")

    settings = Settings.from_env(env_file=None, environ={})
    settings.validate_all()

    # --only 支持逗号组合（如 --only b,c），也支持 all
    picked = {s.strip().lower() for s in args.only.split(",") if s.strip()}
    want = (lambda k: "all" in picked or k in picked)

    if want("a"):
        settings = scenario_a()
    if want("b"):
        scenario_b(settings, cache, metrics, port=args.port)
    if want("c"):
        scenario_c(log_path)
    if want("d"):
        scenario_d(metrics, settings, cache)
    if want("e"):
        scenario_e(settings)
    if want("f"):
        scenario_f(settings, cache, metrics)

    print("\n" + "=" * 74)
    print("案例 12 结束。本案例的三个考点：")
    print("  ① 「能跑但配置是错的」服务，比「起不来」的服务危险得多 → 启动 fail fast")
    print("  ② 没有 request_id 的可观测性等于没有可观测性")
    print("  ③ 缓存键漏掉数据版本 → 静默返回旧数据（不报错、不崩溃）")
    print("=" * 74)


if __name__ == "__main__":
    main()
