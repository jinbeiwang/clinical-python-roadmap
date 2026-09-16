#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""带重试、退避、限流与审计的 HTTP 客户端（第 23 章的核心实现）。

为什么要有这一层
----------------
直接写 ``requests.get(url)`` 的代码，在临床环境里会踩四个坑：

1. **没有超时** —— ``requests`` 默认永不超时，对方半死不活时任务永久挂起；
2. **失败就崩** —— EDC/统计平台夜间跑批时抖动是常态，一次失败不该终止整批；
3. **重试不安全** —— POST 失败了盲目重试，可能创建两份递交；
4. **密钥进了日志** —— 想记全请求细节就得记 header，而 header 里就有 token。

所以这一层把「超时 / 重试 / 退避 / 限流 / 幂等 / 脱敏 / 审计」七件事
收敛到一个类里。业务代码只需要写 ``client.get("/api/v2/xxx")``。

零依赖
------
传输层是**可注入**的（``Transport`` 协议）：

- :class:`UrllibTransport` —— 默认实现，只用标准库 ``urllib``，不需要装任何包；
- :class:`MockTransport` —— 离线替身：不发网络请求，按路径返回预置数据，
  还能模拟"前两次超时、第三次成功""401 鉴权失败"等分支；
- 如果你项目里已经用 httpx / requests 做统一网关接入，
  写一个 30 行的 ``HttpxTransport`` 换进来即可，本模块其它逻辑一行不用改。

可注入的传输层让"外部依赖"在测试和演示里变成可控的 ——
这与 ``clinic.io`` 用 CSV 代替 XPT、``agent_core.MockLLMClient``
代替真实 API 是同一种思路（第 23.9 节）。

合规红线（第 23.8 节）
----------------------
本模块内置三样东西，因为它们**不能靠自觉**：

- :func:`redact` —— 任何落日志的字典先过一遍脱敏；
- :func:`check_deidentification` —— 扫描 DataFrame 列名，拦住直接标识符外发；
- :func:`log_llm_call` —— 每次模型调用落一行审计（只存 sha256 摘要 + 预览）。

运行演示
--------
    python clinic/agent_http.py          # 离线跑完 8 个场景，不联网、不睡眠
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

__all__ = [
    "HttpError", "TransientError", "AuthError", "ContractError",
    "Response", "Transport", "UrllibTransport", "MockTransport",
    "RateLimiter", "classify", "stable_key", "redact", "SENSITIVE_KEYS",
    "RETRYABLE_STATUS", "HttpClient",
    "Watermark", "CachedFallback",
    "parse_subjects", "build_llm_payload", "check_deidentification",
    "log_llm_call",
]


# ===========================================================================
# 0. 异常与错误分类
# ===========================================================================
class HttpError(Exception):
    """HTTP 层错误基类。带 ``error_type`` 与 ``retryable`` 两个机器可读字段。

    错误分类不是"好看"，而是**决定框架行为的开关**：
    可重试的错误走退避重试，不可重试的立即上交（第 23.5 节）。
    """

    def __init__(self, message: str, error_type: str = "internal",
                 retryable: bool = False, status: int | None = None,
                 hint: str | None = None) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.retryable = retryable
        self.status = status
        self.hint = hint

    def to_dict(self) -> dict:
        return {"error_type": self.error_type, "retryable": self.retryable,
                "status": self.status, "message": str(self),
                "hint": self.hint}


class TransientError(HttpError):
    """网络抖动 / 5xx / 429 —— 退避后可以再试。"""

    def __init__(self, message: str, status: int | None = None,
                 hint: str | None = None) -> None:
        super().__init__(message, "transient", True, status, hint)


class AuthError(HttpError):
    """401 / 403 —— **绝不能自动重试**，反复试会触发账号锁定。"""

    def __init__(self, message: str, status: int = 401,
                 hint: str | None = None) -> None:
        super().__init__(message, "permission", False, status,
                         hint or "凭证可能已过期，请检查 token 有效期或联系管理员")


class ContractError(HttpError):
    """对方返回的 JSON 不符合预期 —— 重试无用，要报清"哪里不对"。"""

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message, "validation", False, None, hint)


# 可重试的状态码。注意 400/422/404/401/403 **不在**其中。
RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}
# 绝不重试的状态码：鉴权类单独拎出来，因为后果最严重（账号锁定）
AUTH_STATUS = {401, 403}


def classify(status: int | None = None,
             exc: BaseException | None = None) -> tuple[str, bool]:
    """把「状态码 / 异常」映射成 ``(错误类型, 是否可重试)``。

    这是第 23.5 节那张表的代码化 —— 集中在一处，别散落在业务里。
    """
    if exc is not None:
        if isinstance(exc, (TransientError, TimeoutError, socket.timeout,
                            socket.gaierror, ConnectionError)):
            return "transient", True
        if isinstance(exc, AuthError):
            return "permission", False
        if isinstance(exc, ContractError):
            return "validation", False
        if isinstance(exc, HttpError):
            return exc.error_type, exc.retryable
        return "internal", False

    if status is None:
        return "internal", False
    if status in (400, 422):
        return "validation", False        # 请求本身有问题，重试无用
    if status in AUTH_STATUS:
        return "permission", False        # ★ 终止并告警
    if status == 404:
        return "not_found", False
    if status in RETRYABLE_STATUS:
        return "transient", True
    if 500 <= status < 600:
        return "transient", True
    return "internal", False


# ===========================================================================
# 1. 脱敏（第 23.3 节）
# ===========================================================================
SENSITIVE_KEYS = ("token", "key", "secret", "password", "authorization",
                  "cookie", "session", "credential", "passwd", "apikey")
_HEX32 = re.compile(r"^[0-9a-fA-F]{32,}$")


def redact(d: Any, prefix_keep: int = 4) -> Any:
    """递归脱敏。**任何要落日志/落盘的东西都必须先过这里**。

    - 键名命中 :data:`SENSITIVE_KEYS` → 只留前 ``prefix_keep`` 个字符 + ``***``；
    - 长十六进制串（≥32 位）→ 整体替换为 ``***``（常见于签名、哈希、会话 ID）。
    """
    if isinstance(d, Mapping):
        out: dict[Any, Any] = {}
        for k, v in d.items():
            k_low = str(k).lower()
            if any(s in k_low for s in SENSITIVE_KEYS):
                out[k] = _mask(v, prefix_keep)
            else:
                out[k] = redact(v, prefix_keep)
        return out
    if isinstance(d, (list, tuple)):
        return [redact(v, prefix_keep) for v in d]
    if isinstance(d, str) and _HEX32.match(d):
        return "***"
    return d


def _mask(v: Any, prefix_keep: int = 4) -> str:
    """打码。保留前几位是为了**认出这是哪把钥匙**，不是为了好看。

    ``Bearer sk-xxx`` 这种"方案 + 凭证"的写法必须特殊处理：
    否则保留前 4 位得到的是 ``Bear***`` —— 等于什么也没保留，
    多环境多密钥时依然分不清是哪一个。
    """
    if v is None or v == "":
        return "***"
    s = str(v)
    scheme, sep, cred = s.partition(" ")
    if sep and cred and len(scheme) <= 12:      # 形如 "Bearer <credential>"
        if len(cred) <= prefix_keep:
            return f"{scheme} ***"
        return f"{scheme} {cred[:prefix_keep]}***"
    return f"{s[:prefix_keep]}***" if len(s) > prefix_keep else "***"


# ===========================================================================
# 2. 幂等键（第 23.4 节）
# ===========================================================================
def stable_key(payload: Any, scope: str = "") -> str:
    """根据**内容**算出稳定的幂等键。

    ``sort_keys=True`` 保证字典顺序变化不影响结果 ——
    否则"同一份内容"会算出不同的 key，去重就失效了。
    """
    body = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                      default=str, separators=(",", ":"))
    return hashlib.sha256(f"{scope}|{body}".encode("utf-8")).hexdigest()[:32]


# ===========================================================================
# 3. 传输层（可注入）
# ===========================================================================
@dataclass
class Response:
    """统一的响应表示 —— 与具体 HTTP 库解耦。"""
    status: int
    text: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    elapsed: float = 0.0

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def json(self) -> Any:
        try:
            return json.loads(self.text or "null")
        except json.JSONDecodeError as e:
            raise ContractError(
                f"响应不是合法 JSON（{e.msg}，位置 {e.lineno}:{e.colno}）。"
                f"前 120 字符：{self.text[:120]!r}",
                hint="确认接口地址是否正确；有些服务错误时会返回 HTML 页面") from e


class Transport:
    """传输层协议。想换成 httpx / requests / 公司内部网关，实现这一个方法即可。"""

    def request(self, method: str, url: str, *,
                headers: Mapping[str, str] | None = None,
                params: Mapping[str, Any] | None = None,
                json_body: Any = None,
                timeout: float = 30.0) -> Response:
        raise NotImplementedError


class UrllibTransport(Transport):
    """默认传输层：只用标准库，不引入任何依赖。

    与 httpx 的差别要说清楚，免得踩坑：

    ===================  =========================  =======================
    能力                  urllib（本类）              httpx
    ===================  =========================  =======================
    连接/读取超时分开      ✗ 只有一个 socket 超时     ✓ ``Timeout(connect, read)``
    HTTP/2                ✗                          ✓
    连接池复用             ✗                          ✓
    ===================  =========================  =======================

    所以生产环境、高并发批处理推荐换 httpx；本类保证的是
    **"不装任何包也能跑通全部逻辑"**。
    """

    def request(self, method: str, url: str, *,
                headers: Mapping[str, str] | None = None,
                params: Mapping[str, Any] | None = None,
                json_body: Any = None,
                timeout: float = 30.0) -> Response:
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            if clean:
                url = f"{url}?{urllib.parse.urlencode(clean)}"

        body: bytes | None = None
        hdrs = dict(headers or {})
        if json_body is not None:
            body = json.dumps(json_body, ensure_ascii=False,
                              default=str).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json; charset=utf-8")

        req = urllib.request.Request(url, data=body, headers=hdrs,
                                     method=method.upper())
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return Response(status=resp.status,
                                text=resp.read().decode("utf-8", "replace"),
                                headers=dict(resp.headers),
                                elapsed=time.perf_counter() - t0)
        except urllib.error.HTTPError as e:          # 4xx / 5xx 也走这里
            text = ""
            try:
                text = e.read().decode("utf-8", "replace")
            except Exception:                         # pragma: no cover
                pass
            return Response(status=e.code, text=text,
                            headers=dict(e.headers or {}),
                            elapsed=time.perf_counter() - t0)
        except socket.timeout as e:
            raise TransientError(f"请求超时（{timeout}s）：{url}",
                                 hint="调大 read_timeout，或检查网络与对方服务状态") from e
        except urllib.error.URLError as e:
            reason = getattr(e, "reason", e)
            if isinstance(reason, (TimeoutError, socket.timeout)):
                raise TransientError(f"连接超时：{url}") from e
            raise TransientError(f"连接失败：{reason}") from e


class MockTransport(Transport):
    """离线传输层：不发网络请求，按路径返回预置数据。

    三个能力对应三种真实故障：

    - ``routes``       —— 正常返回；
    - ``fail_paths``   —— 永久失败的路径（映射到状态码或异常）；
    - ``fail_times``   —— **前 N 次失败、之后成功**，用来验证重试逻辑。
    """

    def __init__(self, routes: Mapping[str, Any] | None = None,
                 fail_paths: Mapping[str, Any] | None = None,
                 fail_times: Mapping[str, int] | None = None,
                 default_status: int = 404) -> None:
        self.routes: dict[str, Any] = dict(routes or {})
        self.fail_paths: dict[str, Any] = dict(fail_paths or {})
        self.fail_times: dict[str, int] = dict(fail_times or {})
        self.default_status = default_status
        self.calls: list[dict] = []          # 记录每次请求，便于断言"重试了几次"

    def request(self, method: str, url: str, *,
                headers: Mapping[str, str] | None = None,
                params: Mapping[str, Any] | None = None,
                json_body: Any = None,
                timeout: float = 30.0) -> Response:
        path = urllib.parse.urlparse(url).path
        self.calls.append({"method": method, "path": path,
                           "params": dict(params or {}),
                           "json": json_body, "headers": dict(headers or {})})

        # 先看"前 N 次失败"：命中且还没超过 N 次 → 失败（用 fail_paths 指定的方式）
        # 注意：命中 fail_times 的路径，fail_paths 只用来指定"怎么失败"，
        # 不再表示"永久失败" —— 否则永远等不到成功的那一次。
        governed: set[str] = set()
        for p, n in self.fail_times.items():
            if p in url:
                governed.add(p)
                done = sum(1 for c in self.calls if p in c["path"])
                if done <= n:
                    return self._fail(self.fail_paths.get(p, 503))
        # 再看"永久失败"
        for p, spec in self.fail_paths.items():
            if p in url and p not in governed:
                return self._fail(spec)

        for p, data in self.routes.items():
            if p in url:
                return Response(status=200, elapsed=0.01,
                                text=json.dumps(data, ensure_ascii=False))
        return Response(status=self.default_status,
                        text=json.dumps({"error": f"no mock route for {path}"}))

    @staticmethod
    def _fail(spec: Any) -> Response:
        """把 ``fail_paths`` 里写的"失败方式"变成一次响应或异常。

        支持四种写法，覆盖四种真实故障：

        * ``int``         —— 状态码，如 ``503``
        * ``Exception``   —— 直接抛，模拟连接被重置 / 读超时
        * ``Response``    —— **原样返回**，用来构造带响应头的失败，
          例如 ``429 + Retry-After``（没这一条就没法离线验证退避逻辑）
        * 其他            —— 抛 :class:`TransientError`
        """
        if isinstance(spec, int):
            return Response(status=spec, text=json.dumps({"error": f"HTTP {spec}"}))
        if isinstance(spec, Response):
            return spec
        if isinstance(spec, BaseException):
            raise spec
        raise TransientError(f"模拟失败：{spec}")


# ===========================================================================
# 4. 限流：客户端要主动（第 23.4 节）
# ===========================================================================
class RateLimiter:
    """令牌桶。避免把对方服务打挂，也避免自己上黑名单。

    ``burst`` 决定"能瞬间发几个" —— 一点突发是正常的，
    但如果突然要发 200 个，就得排队。
    """

    def __init__(self, per_sec: float = 5.0, burst: int | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        if per_sec <= 0:
            raise ValueError("per_sec 必须为正数")
        self.rate = per_sec
        self.interval = 1.0 / per_sec
        self.burst = float(burst or max(1, int(per_sec)))
        self._tokens = self.burst
        self._last = clock()
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self.waited = 0.0                    # 统计：累计等待秒数

    def acquire(self) -> float:
        """取一个令牌，必要时阻塞。返回本次等待秒数。"""
        with self._lock:
            now = self._clock()
            self._tokens = min(self.burst,
                               self._tokens + (now - self._last) / self.interval)
            self._last = now
            wait = 0.0
            if self._tokens < 1:
                wait = (1 - self._tokens) * self.interval
                self._sleep(wait)
                self._tokens = 0.0
            else:
                self._tokens -= 1
            self.waited += wait
            return wait


# ===========================================================================
# 5. 主客户端
# ===========================================================================
class HttpClient:
    """带重试、退避、限流与审计的 HTTP 客户端。

    调用方只需要关心"要什么数据"，超时/重试/限流的细节全部封装在这里。

    参数
    ----
    base_url            : 服务根地址，如 ``https://ct.example.org``
    token               : 凭证。**只放内存**，绝不进日志、不进 Agent 上下文
    connect_timeout     : 连接超时（urllib 后端下与读取超时共用一个 socket 超时）
    read_timeout        : 读取超时
    max_retries         : 可重试错误的额外尝试次数
    rate_limit_per_sec  : 客户端侧限流
    audit_path          : 指定后，每次请求自动落一行（已脱敏）到该 JSONL
    transport           : 传输层实现；默认 :class:`UrllibTransport`
    """

    def __init__(self, base_url: str, token: str | None = None,
                 connect_timeout: float = 5.0, read_timeout: float = 30.0,
                 max_retries: int = 3, backoff_base: float = 1.5,
                 rate_limit_per_sec: float = 5.0,
                 audit_path: str | Path | None = None,
                 transport: Transport | None = None,
                 api_version: str | None = None,
                 user_agent: str = "clinical-python-roadmap/1.0",
                 max_backoff: float = 30.0,
                 sleep: Callable[[float], None] = time.sleep,
                 rand: Callable[[], float] = random.random) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.max_retries = max(0, max_retries)
        self.backoff_base = backoff_base
        self.max_backoff = max_backoff
        self.api_version = api_version
        self.user_agent = user_agent
        self.transport = transport or UrllibTransport()
        self.limiter = RateLimiter(rate_limit_per_sec)
        self.audit_path = Path(audit_path) if audit_path else None
        self._sleep = sleep                  # 可注入 → 测试里不真的睡
        self._rand = rand
        self.audit: list[dict] = []          # 内存里的审计记录
        self.backoff_log: list[float] = []   # 每次实际等待的秒数（便于断言）

    # ------------------------------------------------------------- 对外接口
    def get(self, path: str, params: Mapping[str, Any] | None = None,
            headers: Mapping[str, str] | None = None) -> Response:
        return self.request("GET", path, params=params, headers=headers)

    def post(self, path: str, payload: Any,
             headers: Mapping[str, str] | None = None,
             idempotent: bool = True) -> Response:
        """POST。默认带幂等键 —— 除非你确定重复创建是可接受的。"""
        extra = dict(headers or {})
        if idempotent and "Idempotency-Key" not in extra:
            extra["Idempotency-Key"] = stable_key(payload, scope=path)
        return self.request("POST", path, json_body=payload, headers=extra)

    def request(self, method: str, path: str, *,
                params: Mapping[str, Any] | None = None,
                json_body: Any = None,
                headers: Mapping[str, str] | None = None) -> Response:
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        attempt = 0
        last: HttpError | None = None

        while attempt <= self.max_retries:
            self.limiter.acquire()
            t0 = time.perf_counter()
            try:
                resp = self.transport.request(
                    method, url,
                    headers=self._headers(headers),
                    params=params, json_body=json_body,
                    timeout=self.read_timeout)
            except BaseException as e:               # noqa: BLE001 —— 统一分类
                last = self._as_error(None, e, url)
                self._record(method, path, None, last, time.perf_counter() - t0)
                if not last.retryable:
                    raise last from e
                attempt = self._wait(attempt, None, url, str(last))
                continue

            if resp.ok:
                self._record(method, path, resp, None, time.perf_counter() - t0)
                return resp

            last = self._as_error(resp.status, None, url, resp.text)
            self._record(method, path, resp, last, time.perf_counter() - t0)
            if not last.retryable:
                # ★ 401/403 立即终止：拿错误 token 硬试会锁账号
                raise last
            attempt = self._wait(attempt, self._retry_after(resp),
                                 url, f"HTTP {resp.status}")

        assert last is not None
        raise last

    # ------------------------------------------------------------ 内部实现
    def preview_headers(self, extra: Mapping[str, str] | None = None) -> dict:
        """返回**脱敏后**的请求头 —— 你可以安全地把它打进日志或展示出来。"""
        return redact(self._headers(extra))

    def _headers(self, extra: Mapping[str, str] | None) -> dict[str, str]:
        h = {"Accept": "application/json",
             "User-Agent": self.user_agent}
        if self.api_version:
            # ★ 锁 API 版本：不锁的接口，对方一升级你的脚本就崩
            h["API-Version"] = self.api_version
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        h.update(extra or {})
        return h

    def _as_error(self, status: int | None, exc: BaseException | None,
                  url: str, text: str = "") -> HttpError:
        etype, retryable = classify(status, exc)
        detail = f"HTTP {status}" if status else type(exc).__name__
        msg = f"{detail}：{url}"
        if status in AUTH_STATUS:
            return AuthError(msg, status or 401)
        if retryable:
            body = f"，响应：{text[:160]}" if text else ""
            return TransientError(msg + body, status,
                                  hint="已按指数退避重试；持续失败请检查对方服务状态")
        if status == 404:
            return HttpError(msg, "not_found", False, 404,
                             hint="确认路径与 API 版本是否正确")
        if status in (400, 422):
            return HttpError(msg + (f"，响应：{text[:160]}" if text else ""),
                             "validation", False, status,
                             hint="请求体不符合对方契约，重试无用")
        return HttpError(msg, etype, retryable, status)

    @staticmethod
    def _retry_after(resp: Response) -> float | None:
        """解析 ``Retry-After``。对方明确说要等多久时，自己算退避没意义。"""
        raw = None
        for k, v in (resp.headers or {}).items():
            if k.lower() == "retry-after":
                raw = v
                break
        if not raw:
            return None
        try:
            return max(0.0, float(raw))          # 秒数形式
        except ValueError:
            return None                          # HTTP-date 形式：不解析，走自家退避

    def backoff(self, attempt: int,
                retry_after: float | None = None) -> float:
        """计算第 ``attempt`` 次重试前的等待秒数（指数退避 + 抖动）。"""
        if retry_after is not None:
            return min(retry_after, 60.0)                 # 服务端说了算
        # ★ 注意 attempt 从 0 开始：第一次重试等 ~1 秒（不是 1.5 秒），
        #   之后 1.5、2.25、3.375… 指数增长。
        #   "第一次就退避很久"是常见错误 —— 抖动类故障往往重试一次就过了。
        base = self.backoff_base ** attempt               # 1, 1.5, 2.25, 3.375…
        jitter = self._rand() * base * 0.3                # ★ 抖动：错开重试
        return min(base + jitter, self.max_backoff)

    def _wait(self, attempt: int, retry_after: float | None,
              url: str, why: str) -> int:
        attempt += 1
        if attempt > self.max_retries:
            return attempt
        delay = self.backoff(attempt - 1, retry_after)
        self.backoff_log.append(round(delay, 3))
        self._sleep(delay)
        return attempt

    def _record(self, method: str, path: str, resp: Response | None,
                err: HttpError | None, elapsed: float) -> None:
        rec = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "method": method, "path": path,
            "status": resp.status if resp else None,
            "ok": bool(resp and resp.ok),
            "error_type": err.error_type if err else None,
            "elapsed": round(elapsed, 3),
        }
        self.audit.append(rec)
        if self.audit_path:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            with self.audit_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(redact(rec), ensure_ascii=False) + "\n")

    # ---------------------------------------------------------------- 审计
    def dump_audit(self, path: str | Path | None = None) -> Path:
        """把内存中的审计记录写成 **JSON 数组**快照文件。

        ⚠️ 注意它与 ``audit_path`` 的区别，两者**不能写进同一个文件**：

        ``audit_path``      流式追加，**一行一条 JSON**（JSONL）——
                            适合直接喂给日志采集系统，只增不改
        ``dump_audit(path)`` 批量快照，**一个 JSON 数组** ——
                            适合"把这批请求的审计打包交给别人"

        混在同一个文件里，结果就是**谁也解析不了**：
        第一行是 ``[``，后面的行又各自是完整 JSON。
        这类问题排查起来很费时间，因为它看起来只是"文件格式怪怪的"。
        """
        p = Path(path or self.audit_path or "outputs/audit/http_snapshot.json")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(redact(self.audit), ensure_ascii=False, indent=2),
                     encoding="utf-8")
        return p

    def stats(self) -> dict:
        total = len(self.audit)
        bad = [r for r in self.audit if not r["ok"]]
        return {
            "请求数": total,
            "失败数": len(bad),
            "重试等待合计(秒)": round(sum(self.backoff_log), 3),
            "限流等待合计(秒)": round(self.limiter.waited, 3),
            "按错误类型": _count_by(bad, "error_type"),
        }


def _count_by(rows: Sequence[dict], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        k = str(r.get(key))
        out[k] = out.get(k, 0) + 1
    return out


# ===========================================================================
# 6. 拉取模式：水位线（第 23.7 节）
# ===========================================================================
@dataclass
class Watermark:
    """记录上次成功拉取到哪，避免重复拉、避免漏拉。

    ⚠️ **水位线必须在"数据落盘成功后"才更新**。
    先更新再落盘，中途崩溃就会丢数据，而且永远不会被再次拉到 ——
    这是数据集成里最经典的静默丢数事故。
    """

    last_id: str | None = None
    last_ts: str | None = None
    updated_at: float = 0.0

    def mark(self, last_id: str | None, last_ts: str | None) -> None:
        self.last_id = last_id
        self.last_ts = last_ts
        self.updated_at = time.time()

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2),
                     encoding="utf-8")
        return p

    @classmethod
    def load(cls, path: str | Path) -> "Watermark":
        p = Path(path)
        if not p.exists():
            return cls()
        return cls(**json.loads(p.read_text(encoding="utf-8")))

    def verify(self, earliest_available: str | None) -> str | None:
        """返回告警文本（无问题时返回 ``None``）。

        如果本地水位线比对方"最早可提供的数据"还早，说明
        **中间有数据缺口** —— 必须报警，而不是继续增量拉。
        """
        if not self.last_ts or not earliest_available:
            return None
        if str(self.last_ts) < str(earliest_available):
            return (f"⚠️ 数据缺口：本地水位线 {self.last_ts} 早于服务端最早可用 "
                    f"{earliest_available}，中间的数据需要人工补齐或全量重拉")
        return None


class CachedFallback:
    """外部数据取不到时，回退到上次的缓存，**并明确标注数据时间**。

    ⚠️ 降级时必须把"这是缓存"写进结果。临床场景里，一份"用 3 天前的
    CT 版本做的核查"如果不标注，会被当成"用最新版本核实过"。
    """

    def __init__(self, client: HttpClient, cache_path: str | Path | None = None
                 ) -> None:
        self.client = client
        self.cache: dict[str, tuple[Any, float]] = {}
        self.cache_path = Path(cache_path) if cache_path else None
        if self.cache_path and self.cache_path.exists():
            raw = json.loads(self.cache_path.read_text(encoding="utf-8"))
            self.cache = {k: (v[0], v[1]) for k, v in raw.items()}

    def get(self, path: str) -> tuple[Any, str]:
        try:
            resp = self.client.get(path)
            data = resp.json()
            self.cache[path] = (data, time.time())
            self._persist()
            return data, f"实时获取（{time.strftime('%Y-%m-%d %H:%M:%S')}）"
        except TransientError:
            if path in self.cache:
                data, ts = self.cache[path]
                return data, f"⚠️ 使用缓存数据（{_age_text(time.time() - ts)}）"
            raise

    def _persist(self) -> None:
        if not self.cache_path:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(
            json.dumps(self.cache, ensure_ascii=False, default=str),
            encoding="utf-8")


def _age_text(seconds: float) -> str:
    """把"多久之前"写成人看得懂的量级。

    ★ 别小看这一行：在降级场景里，"0.0 小时前"和"6 分钟前"对读者的
      判断完全不同 —— 前者会让人以为缓存是刚更新的、可以放心用。
      既然降级的全部意义就是**让陈旧被看见**，那时间就必须说到点子上。
    """
    if seconds < 60:
        return f"{seconds:.0f} 秒前获取"
    if seconds < 3600:
        return f"{seconds / 60:.1f} 分钟前获取"
    if seconds < 86400:
        return f"{seconds / 3600:.1f} 小时前获取"
    return f"⚠️ {seconds / 86400:.1f} 天前获取（陈旧风险高）"


# ===========================================================================
# 7. 对方返回的数据不可信（第 23.6 节）
# ===========================================================================
def parse_subjects(payload: Any) -> list[dict]:
    """把外部返回的受试者列表校验成**受控结构**。

    错误信息必须说清"哪里不对、实际是什么" —— 这样 Agent 自己才能修错
    （第 20 章：结构化错误）。
    """
    if not isinstance(payload, dict):
        raise ContractError(f"顶层应为对象，实际为 {type(payload).__name__}")
    if "results" not in payload:
        raise ContractError(
            f"响应缺少 results 字段。实际顶层键：{list(payload)[:10]}",
            hint="确认 API 版本；老版本接口可能直接返回数组")
    rows = payload["results"]
    if not isinstance(rows, list):
        raise ContractError(f"results 应为数组，实际为 {type(rows).__name__}")

    out: list[dict] = []
    for i, r in enumerate(rows):
        if not isinstance(r, dict) or "subjectId" not in r:
            raise ContractError(f"第 {i} 条记录缺少 subjectId：{str(r)[:120]}")
        out.append({
            "subject_id": str(r["subjectId"]).strip(),
            "age": _to_num(r.get("age")),                  # 宽容转数值：'N/A' → None
            "sex": str(r.get("sex", "")).strip().upper() or None,
        })
    return out


def _to_num(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def build_llm_payload(df, need: Iterable[str]) -> str:
    """只把**需要的列**发给模型。多一列就多一分风险，也多一分 token。"""
    need = list(need)
    missing = [c for c in need if c not in df.columns]
    if missing:
        raise ValueError(f"缺少必需列：{missing}；现有列：{list(df.columns)[:15]}")
    return df[need].to_csv(index=False)


# ===========================================================================
# 8. 合规：临床场景的红线（第 23.8 节）
# ===========================================================================
# 直接标识符的列名模式。宁可误报，不可漏报 —— 误报的成本是"多看一眼"。
DIRECT_ID_PATTERNS = (
    r"name", r"dob", r"birth", r"phone", r"mobile", r"tel", r"id_card",
    r"idcard", r"ssn", r"passport", r"addr", r"email", r"wechat",
    r"patient_name", r"subject_name", r"contact", r"initials", r"occupation",
)
_DATE_LIKE = re.compile(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}$")
_NAME_LIKE = re.compile(r"^[\u4e00-\u9fff]{2,4}$|^[A-Z][a-z]+\s+[A-Z][a-z]+$")


def check_deidentification(df, sample: int = 200,
                           extra_patterns: Sequence[str] = ()) -> list[str]:
    """扫描 DataFrame，返回**疑似直接标识符**的列与原因。

    判据有两层，缺一不可：
    1. **列名**命中 :data:`DIRECT_ID_PATTERNS`；
    2. 或者**取值**看起来像人名 / 精确日期。

    第 2 条很关键：`VAR1` 这种列名完全看不出问题，
    但里面装着 `"张伟"` —— 只有看值才拦得住。
    """
    pats = tuple(DIRECT_ID_PATTERNS) + tuple(extra_patterns)
    problems: list[str] = []
    head = df.head(sample)

    for col in df.columns:
        col_low = str(col).lower()
        hit = [p for p in pats if re.search(p, col_low)]
        if hit:
            problems.append(f"{col}：列名命中直接标识符模式 {hit}")
            continue

        vals = [str(v).strip() for v in head[col].dropna().unique()[:20]]
        if not vals:
            continue
        n_date = sum(1 for v in vals if _DATE_LIKE.match(v))
        n_name = sum(1 for v in vals if _NAME_LIKE.match(v))
        if n_date >= max(1, len(vals) // 2):
            problems.append(f"{col}：取值呈精确日期形态（{n_date}/{len(vals)}），"
                            f"如 {vals[0]!r} —— 出生日期属直接标识符")
        elif n_name >= max(1, len(vals) // 2):
            problems.append(f"{col}：取值呈人名形态（{n_name}/{len(vals)}），"
                            f"如 {vals[0]!r} —— 请确认是否为姓名")
    return problems


def log_llm_call(prompt: str, response: str, model: str,
                 audit_dir: str | Path = "outputs/audit") -> Path:
    """每次模型调用落一行。这是"可追溯"的最小实现。

    存的是 **sha256 摘要**而不是全文：既能证明"发的是哪一份"，
    又不会把完整数据留在日志里。
    """
    d = Path(audit_dir)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"llm_calls_{time.strftime('%Y%m')}.jsonl"
    rec = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "model": model,
        "prompt_chars": len(prompt),
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16],
        "response_chars": len(response),
        "prompt_preview": prompt[:200],     # 只留预览，不存全文
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return path


# ===========================================================================
# 9. 离线演示
# ===========================================================================
_LINE = "-" * 68


def _demo() -> None:
    eq = {"version": "2024-06-01",
          "items": [{"code": "HEADACHE", "term": "Headache"},
                    {"code": "NAUSEA", "term": "Nausea"}]}

    print("=" * 68)
    print("clinic.agent_http 演示（全程离线，不联网、不真实睡眠）")
    print("=" * 68)

    # ---------------------------------------------------- 场景 1：正常拉取
    print(f"\n【场景 1】正常拉取 CDISC 受控术语\n{_LINE}")
    client = HttpClient(
        base_url="https://ct.example.org", token="demo-token-abcdef",
        api_version="2024-06-01",
        transport=MockTransport(routes={"/api/v2/codelists/AE": eq}),
        sleep=lambda _s: None, rand=lambda: 0.0)
    resp = client.get("/api/v2/codelists/AE")
    data = resp.json()
    print(f"取到 {len(data['items'])} 条术语，版本 {data['version']}")
    print(f"请求头（脱敏后）：{client.preview_headers()}")

    # ------------------------------------------------ 场景 2：前两次超时
    print(f"\n【场景 2】前 2 次超时、第 3 次成功（验证重试 + 退避）\n{_LINE}")
    flaky = MockTransport(routes={"/api/v2/codelists/AE": eq},
                          fail_paths={"/api/v2/codelists/AE": TransientError("模拟超时")},
                          fail_times={"/api/v2/codelists/AE": 2})
    c2 = HttpClient(base_url="https://ct.example.org", token="t",
                    max_retries=3, transport=flaky, sleep=lambda _s: None,
                    rand=lambda: 0.0)
    r2 = c2.get("/api/v2/codelists/AE")
    print(f"结果：HTTP {r2.status}，实际发出 {len(flaky.calls)} 次请求")
    print(f"退避序列（秒）：{c2.backoff_log}   ← 抖动设为 0 便于观察纯指数增长")

    # ------------------------------------------------- 场景 3：尊重 Retry-After
    print(f"\n【场景 3】429 限流 + 尊重服务端 Retry-After\n{_LINE}")
    limited = MockTransport(routes={"/api/v2/x": {"ok": True}},
                            fail_paths={"/api/v2/x": 429},
                            fail_times={"/api/v2/x": 1})
    c3 = HttpClient(base_url="https://ct.example.org", max_retries=2,
                    transport=limited, sleep=lambda _s: None, rand=lambda: 0.0)
    # 给第一次失败响应塞一个 Retry-After: 7
    orig = limited.request

    def patched(method, url, **kw):
        r = orig(method, url, **kw)
        if r.status == 429:
            r.headers["Retry-After"] = "7"
        return r

    limited.request = patched                                # type: ignore[method-assign]
    c3.get("/api/v2/x")
    print(f"退避序列：{c3.backoff_log}  ← 7 秒来自服务端，而不是自家 base^attempt")

    # ------------------------------------------------ 场景 4：401 不重试
    print(f"\n【场景 4】401 鉴权失败 —— 绝不自动重试\n{_LINE}")
    denied = MockTransport(fail_paths={"/api/v2/secure": 401})
    c4 = HttpClient(base_url="https://ct.example.org", token="expired",
                    max_retries=5, transport=denied, sleep=lambda _s: None)
    try:
        c4.get("/api/v2/secure")
    except AuthError as e:
        print(f"抛出 {type(e).__name__}：{e}")
        print(f"error_type={e.error_type} retryable={e.retryable} "
              f"hint={e.hint}")
        print(f"实际请求次数：{len(denied.calls)}  ← max_retries=5 但一次都没重试")

    # ------------------------------------------------ 场景 5：契约校验
    print(f"\n【场景 5】对方返回的 JSON 不合预期（显式校验）\n{_LINE}")
    bad = MockTransport(routes={"/api/v2/subjects": {"total": 3, "rows": []}})
    c5 = HttpClient(base_url="https://edc.example.org", transport=bad)
    try:
        parse_subjects(c5.get("/api/v2/subjects").json())
    except ContractError as e:
        print(f"抛出 ContractError：{e}")
        print(f"提示：{e.hint}")
    good = MockTransport(routes={"/api/v2/subjects": {"results": [
        {"subjectId": " 01-701-1015 ", "age": "63", "sex": "f"},
        {"subjectId": "01-701-1023", "age": "N/A", "sex": "M"}]}})
    rows = parse_subjects(HttpClient(base_url="https://edc.example.org",
                                    transport=good).get("/api/v2/subjects").json())
    print(f"校验通过：{rows}")

    # ------------------------------------------------ 场景 6：降级 + 水位线
    print(f"\n【场景 6】降级到缓存（必须标注数据时间）+ 水位线缺口检测\n{_LINE}")
    ok_transport = MockTransport(routes={"/api/v2/snapshots": {"n": 254}})
    fb = CachedFallback(HttpClient(base_url="https://edc.example.org",
                                  transport=ok_transport))
    d1, src1 = fb.get("/api/v2/snapshots")
    print(f"第一次：{d1} ← {src1}")
    fb.client.transport = MockTransport(fail_paths={"/api/v2/snapshots": 503})
    fb.client.max_retries = 0
    d2, src2 = fb.get("/api/v2/snapshots")
    print(f"服务挂了：{d2} ← {src2}")

    wm = Watermark().load("outputs/audit/_demo_watermark.json")
    print(f"水位线（首次）：{wm}")
    wm.mark(last_id="SUBJ-0254", last_ts="2026-09-10T00:00:00")
    gap = wm.verify(earliest_available="2026-09-13T00:00:00")
    print(f"缺口检测：{gap}")

    # ------------------------------------------------ 场景 7：合规扫描
    print(f"\n【场景 7】外发前的直接标识符扫描（红线一）\n{_LINE}")
    import pandas as pd                                     # 延迟导入：本模块本身不依赖 pandas
    df = pd.DataFrame({
        "USUBJID": ["01-701-1015", "01-701-1023"],
        "SUBJ_NAME": ["张伟", "李娜"],
        "BRTHDTC": ["1949-03-12", "1952-08-01"],
        "AGE": [63, 61],
        "VAR1": ["王强", "赵敏"],                            # 列名无害，取值是人名
    })
    problems = check_deidentification(df)
    for p in problems:
        print(f"  ✗ {p}")
    safe = build_llm_payload(df, ["USUBJID", "AGE"])
    print(f"  可外发字段：\n{safe}")

    # ------------------------------------------------ 场景 8：审计落盘
    print(f"\n【场景 8】审计日志（自动脱敏）与统计\n{_LINE}")
    p = client.dump_audit("outputs/audit/_demo_http.jsonl")
    print(f"审计写入：{p.relative_to(Path.cwd()) if p.is_absolute() else p}")
    print(f"统计：{client.stats()}")
    print(f"\n{_LINE}\n还可以试试：把传输层换成 HttpxTransport（接口同 Transport），"
          f"\n或把 token 换成环境变量 CDISC_API_TOKEN（见 docs/23.3）。")
    print("=" * 68)


if __name__ == "__main__":
    _demo()
