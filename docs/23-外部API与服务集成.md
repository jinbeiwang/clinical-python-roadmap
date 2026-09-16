# 第 23 章 · 与外部 API 及服务集成

> **本章目标**：让 Agent 安全地和 EDC、统计平台、外部 API 打交道 ——
> 包括超时重试、密钥管理、以及临床场景**必须守住的合规底线**。
> **配套模块**：[`clinic/agent_http.py`](../clinic/agent_http.py)

---

## 23.1 为什么这一章对临床程序员格外重要

前面 22 章的数据都在你本地硬盘上。真实工作里不是这样：

```text
                    ┌──────────────────┐
                    │   EDC 系统        │  ← 临床数据源
                    │  (Medidata/RAVE)  │
                    └────────┬─────────┘
                             │ ① 导出/接口
                             ▼
┌───────────────┐    ┌──────────────────┐    ┌──────────────────┐
│  CTMS 系统     │───▶│   统计编程环境     │───▶│  统计平台/TLF 交付 │
│ (试验管理)     │    │  SAS / Python     │    │                  │
└───────────────┘    └────────┬─────────┘    └──────────────────┘
                              │ ② 取术语/规则/外部数据
                              ▼
                    ┌──────────────────┐
                    │  CDISC CT 服务    │
                    │  外部 LLM API     │
                    │  实验室正常值范围  │
                    └──────────────────┘
```

只要开始走"网络"，就同时引入了**四类新的失败模式**和**一条合规红线**：

| 新问题 | 后果 |
|---|---|
| 网络不可靠 | 请求超时、连接重置 |
| 对方服务不稳定 | 5xx、限流（429） |
| 鉴权会过期 | token 失效导致批量任务中途全挂 |
| **数据会离开你的环境** | **★ 合规风险** |

最后一条是临床场景独有的、也是最需要慎重对待的。

---

## 23.2 HTTP 基础：那个必须写的参数

先看一个真实事故：

```python
# ❌ 这一行可能永久挂住
import requests
resp = requests.get("https://api.example.com/data")
```

`requests` 的**默认超时是"永不超时"**。对方服务半死不活时，
这个请求会一直挂着 —— 你的 Agent 任务卡在那里，看起来像"正在工作"。

```python
# ✅ 超时必须显式指定，而且分两段
import httpx

resp = httpx.get(
    "https://api.example.com/data",
    timeout=httpx.Timeout(connect=5.0, read=30.0),   # 连接 5s，读取 30s
)
```

两段超时的区别很重要：

| 超时类型 | 含义 | 建议值 |
|---|---|---|
| `connect` | 建立 TCP 连接 | 3–5 秒（连不上说明网络或服务有问题） |
| `read` | 等待响应数据 | 10–60 秒（取决于对方处理时间） |

`httpx` 比 `requests` 多两个好处：**支持 HTTP/2**、**同一套 API 支持异步**。
本仓库的封装用 `httpx`（在 `requirements.txt` 里），但如果你只用 `requests`，
所有概念都一样。

### 一个够用的封装

```python
# clinic/agent_http.py（节选）
class HttpClient:
    """带重试、退避、限流与审计的 HTTP 客户端。

    设计原则：调用方只需要关心"要什么数据"，
    超时/重试/限流的细节全部封装在这里。
    """

    def __init__(self, base_url: str, token: str | None = None,
                 connect_timeout: float = 5.0, read_timeout: float = 30.0,
                 max_retries: int = 3, backoff_base: float = 1.5,
                 rate_limit_per_sec: float = 5.0,
                 audit_path: Path | None = None):
        ...
```

---

## 23.3 鉴权：密钥永远不进代码

### 三种常见方式

| 方式 | 头部写法 | 适用 |
|---|---|---|
| API Key | `X-API-Key: <key>` 或 `Authorization: Bearer <key>` | 内部服务、简单场景 |
| OAuth2 Client Credentials | `Authorization: Bearer <access_token>` | 企业系统（EDC/CTMS 常见） |
| 签名（HMAC） | `X-Signature: <hmac>` | 高安全要求 |

### 密钥管理：三条铁律

```python
# ❌ 铁律 1：绝不硬编码
API_KEY = "sk-abc123..."                     # 提交到 git 就完了

# ❌ 铁律 2：绝不写进日志
logger.info(f"调用 API，token={token}")       # 日志会被收集、会被看到

# ❌ 铁律 3：绝不放进 Agent 的上下文
messages.append({"role": "system", "content": f"你的 token 是 {token}"})
#    ↑ 灾难：token 会进入模型 API 请求，也进入 state 序列化文件
```

```python
# ✅ 正确做法
import os

def get_token() -> str:
    """从环境变量读取。本地用 .env（已 gitignore），生产用密钥管理服务。"""
    tok = os.environ.get("CDISC_API_TOKEN")
    if not tok:
        raise RuntimeError(
            "未设置环境变量 CDISC_API_TOKEN。\n"
            "本地开发：在 .env 里写 CDISC_API_TOKEN=xxx（.env 已在 .gitignore）\n"
            "生产环境：使用密钥管理服务注入环境变量"
        )
    return tok
```

`clinic/agent_http.py` 里做了一个**自动脱敏**：

```python
SENSITIVE_KEYS = ("token", "key", "secret", "password", "authorization", "cookie")

def _mask(v: Any, prefix_keep: int = 4) -> str:
    """打码。保留前几位是为了**认出这是哪把钥匙**，不是为了好看。"""
    if v is None or v == "":
        return "***"
    s = str(v)
    scheme, sep, cred = s.partition(" ")
    if sep and cred and len(scheme) <= 12:          # ★ 形如 "Bearer <credential>"
        return f"{scheme} {cred[:prefix_keep]}***"
    return f"{s[:prefix_keep]}***" if len(s) > prefix_keep else "***"
```

> ⚠️ 那个 `Bearer` 分支不是多余的：如果只是简单地"保留前 4 个字符"，
> `Authorization: Bearer sk-xxx` 会被打成 **`Bear***`** ——
> 等于什么都没保留。多环境多密钥时，你还是分不清用的是哪一把。
>
> 脱敏规则**误伤**的代价同样要考虑：本项目 `clinic/config.py` 的
> `is_sensitive()` 是按字段名**分词精确匹配**的，因为子串匹配
> 会把 `max_tokens`（配额，不是密钥）一起打码，让日志里的预算永远显示不出来。
> **被日志骗，比少记一条更阴险。**

> 🔥 **审计日志和密钥泄漏是一对矛盾**。你想记全请求细节以便追溯，
> 但请求头里就有 token。解决方案不是"少记点"，
> 而是**"记之前先脱敏"** —— 这是个必须写进客户端的固定动作，不能靠自觉。

---

## 23.4 重试、退避与限流

### 哪些请求能重试

```python
RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}
```

**但状态码只是第一关，更关键的是幂等性**：

| 方法 | 幂等？ | 能重试吗 |
|---|---|---|
| `GET` | 是 | 可以 |
| `PUT` | 是 | 可以 |
| `DELETE` | 是 | 可以（但要确认语义） |
| `POST` | **通常不是** | **只有在带幂等键时才可以** |

```python
# ❌ 危险：POST 失败就重试 → 可能创建两份
for _ in range(3):
    try:
        post("/api/submissions", json=payload)
        break
    except TimeoutError:
        continue        # 第一次其实成功了，只是响应超时 → 现在有两份提交

# ✅ 带幂等键，服务端负责去重
post("/api/submissions", json=payload,
     headers={"Idempotency-Key": stable_key(payload)})
```

`stable_key()` 要能根据**内容**算出稳定值：

```python
def stable_key(payload: dict) -> str:
    body = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(body.encode()).hexdigest()[:32]
```

同一份内容重试，key 相同 → 服务端识别为重复 → 不重复创建。

### 指数退避 + 抖动

```python
def _backoff(self, attempt: int, retry_after: float | None = None) -> float:
    """计算等待秒数。尊重服务端的 Retry-After。"""
    if retry_after is not None:
        return min(retry_after, 60.0)                 # 服务端说了算
    base = self.backoff_base ** attempt               # 1.5, 2.25, 3.375...
    jitter = random.uniform(0, base * 0.3)            # ★ 抖动
    return min(base + jitter, 30.0)
```

> 🔥 **抖动（jitter）不是装饰**。批量任务里 50 个并发请求同时失败、
> 同时重试，会把刚恢复的服务再打垮一次。加随机抖动让重试错开。

### 限流：客户端要主动

```python
class RateLimiter:
    """简单的令牌桶。避免把对方服务打挂，也避免自己上黑名单。"""

    def __init__(self, per_sec: float, burst: int | None = None):
        self.interval = 1.0 / per_sec
        self.burst = burst or max(1, int(per_sec))
        self._tokens = float(self.burst)
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            self._tokens = min(self.burst, self._tokens + (now - self._last) / self.interval)
            self._last = now
            if self._tokens < 1:
                time.sleep((1 - self._tokens) * self.interval)
                self._tokens = 0
            else:
                self._tokens -= 1
```

同时**必须尊重 `Retry-After`** —— 对方明确告诉你要等多久时，
自己算退避时间是没意义的。

---

## 23.5 错误分类与降级

```python
def classify(status: int | None, exc: Exception | None) -> tuple[str, bool]:
    """返回 (错误类型, 是否可重试)。"""
    if exc is not None:
        if isinstance(exc, (httpx.ConnectTimeout, httpx.ReadTimeout,
                            httpx.ConnectError, httpx.ReadError)):
            return "transient", True
        return "internal", False
    if status in (400, 422):
        return "validation", False        # 请求本身有问题，重试无用
    if status in (401, 403):
        return "permission", False        # ★ 终止并告警，不要自动重试
    if status == 404:
        return "not_found", False
    if status == 429:
        return "transient", True          # 限流，退避后重试
    if 500 <= status < 600:
        return "transient", True
    return "internal", False
```

注意 `401/403` 的处理：**绝不能自动重试**。
拿错误 token 狂试 50 次，在很多系统里会**触发账号锁定**。

### 降级：拿不到就明说

```python
class CachedFallback:
    """外部数据取不到时，回退到上次的缓存，并明确标注数据时间。"""

    def get(self, key: str) -> tuple[Any, str]:
        try:
            data = self.client.fetch(key)
            self.cache[key] = (data, time.time())
            return data, f"实时获取（{_now()})"
        except TransientError:
            if key in self.cache:
                data, ts = self.cache[key]
                age = (time.time() - ts) / 3600
                return data, f"⚠️ 使用缓存数据（获取于 {age:.1f} 小时前）"
            raise
```

> ⚠️ **降级时必须把"这是缓存"写进结果**。在临床场景里，
> 一份"用 3 天前的 CT 版本做的核查"如果不标注，
> 会被当成"用最新版本核实过" —— 这是不可接受的。

---

## 23.6 对方返回的数据不可信

即使对方是正经的临床系统，返回的 JSON 也可能不合预期：
字段缺失、类型变了、枚举值新增、版本升级改了字段名。

```python
# ❌ 直接信任
data = resp.json()
df = pd.DataFrame(data["results"])          # KeyError: 'results'
df["age"] = df["age"].astype(int)           # ValueError: 有 "N/A"

# ✅ 显式校验
def parse_subjects(payload: dict) -> list[dict]:
    if not isinstance(payload, dict) or "results" not in payload:
        raise ContractError(
            f"响应缺少 results 字段。实际顶层键：{list(payload)[:10]}"
        )
    rows = payload["results"]
    if not isinstance(rows, list):
        raise ContractError(f"results 应为数组，实际为 {type(rows).__name__}")

    out = []
    for i, r in enumerate(rows):
        if not isinstance(r, dict) or "subjectId" not in r:
            raise ContractError(f"第 {i} 条记录缺少 subjectId：{str(r)[:120]}")
        out.append({
            "subject_id": str(r["subjectId"]).strip(),
            "age": pd.to_numeric(r.get("age"), errors="coerce"),   # 宽容转数值
            "sex": str(r.get("sex", "")).strip().upper() or None,
        })
    return out
```

用 Pydantic 会更简洁（`pip install pydantic`），但**手写校验的价值是
错误信息完全可控** —— 你能把"哪里不对、实际是什么"写清楚，
这对 Agent 自己修错很重要（见第 20 章）。

### API 版本要锁

```python
resp = client.get("/api/v2/subjects", headers={"API-Version": "2024-06-01"})
```

不锁版本的接口，对方一升级你的脚本就崩。**在请求头里写死日期版本**
是很多临床平台的惯例。

---

## 23.7 三种集成模式

| 模式 | 机制 | 临床场景 | 注意 |
|---|---|---|---|
| **拉（Poll）** | 定时调接口问"有没有新数据" | EDC 数据导出、快照 | 要记录"上次拉到哪"（水位线） |
| **推（Webhook）** | 对方主动 POST 给你 | 数据变更通知 | 需要验签、防重放、幂等 |
| **流（Stream）** | 长连接持续收 | 实时监控 | 连接管理、断线重连 |

临床数据集成**绝大多数是"拉"**，因为：
- 数据更新是批量的（夜间跑批）
- 需要可重放的完整快照，而不是增量事件
- 审计要求"某时刻的数据状态"可复现

"拉"模式的关键是**水位线（watermark）**：

```python
@dataclass
class Watermark:
    """记录上次成功拉取到哪，避免重复拉、避免漏拉。"""
    last_id: str | None = None
    last_ts: str | None = None
    updated_at: float = 0.0

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Watermark":
        if not path.exists():
            return cls()
        return cls(**json.loads(path.read_text(encoding="utf-8")))
```

> ⚠️ **水位线要在"数据落盘成功后"才更新**。
> 先更新水位线再落盘，中途崩溃就会丢数据 —— 而且**永远不会被再次拉到**。
> 这是数据集成里最经典的静默丢数事故。

---

## 23.8 合规：临床场景的红线

这一节的内容，比前面所有技术细节都重要。

### 红线一：受试者标识不能外发

```python
# ❌ 灾难：把可识别信息发给了外部 LLM
prompt = f"""
请分析受试者 {subj.subject_name}（出生日期 {subj.dob}，
身份证号 {subj.id_number}）的实验室结果：
{lab_results}
"""
```

```python
# ✅ 只发研究编号 + 必要的分析字段
prompt = f"""
请分析受试者 {row.USUBJID} 在访视 {row.VISIT} 的实验室结果：
{deidentified_labs}
"""
```

`USUBJID`（研究编号）通常是**假名化（pseudonymized）**标识 ——
它在研究内唯一，但脱离研究上下文无法关联到自然人。
而姓名、身份证、精确出生日期、联系方式属于**直接标识符**，绝不能外发。

> 🔥 **假名化 ≠ 匿名化**。假名化的数据仍然属于个人数据
> （因为研究方持有对应表），受 GDPR / 个人信息保护法约束。
> "已经用编号了所以随便发"是常见的误解。

### 红线二：最小必要

只发送完成当前任务**必需的字段**：

```python
def build_llm_payload(df: pd.DataFrame, need: list[str]) -> str:
    """只把需要的列发给模型。多一列就多一分风险，也多一分 token。"""
    missing = [c for c in need if c not in df.columns]
    if missing:
        raise ValueError(f"缺少必需列：{missing}")
    return df[need].to_csv(index=False)
```

### 红线三：审计日志要能回答"发出去过什么"

```python
def log_llm_call(prompt: str, response: str, model: str,
                 audit_dir: Path = Path("outputs/audit")) -> None:
    """每次模型调用落一行。这是"可追溯"的最小实现。"""
    audit_dir.mkdir(parents=True, exist_ok=True)
    rec = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "model": model,
        "prompt_chars": len(prompt),
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest()[:16],
        "response_chars": len(response),
        "prompt_preview": prompt[:200],          # 只留预览，不存全文
    }
    with (audit_dir / f"llm_calls_{date.today():%Y%m}.jsonl").open(
            "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
```

注意存的是 **sha256 摘要**而不是全文：既能证明"发的是哪一份"，
又不会把完整数据留在日志里。

### 一张自查表

| 检查项 | 要求 |
|---|---|
| 外发数据里有直接标识符吗 | 姓名/身份证/电话/精确生日 —— **必须剔除** |
| 只发了必需字段吗 | 逐列确认用途 |
| 有数据出境问题吗 | 模型服务商在哪个司法辖区 |
| 有 DPA / 授权吗 | 与供应商签的数据处理协议 |
| 审计日志记了吗 | 谁、何时、发了什么（摘要） |
| 能人工复核吗 | 关键结论可追溯到源数据 |

> ⚠️ **最后一条是临床统计的根本**：无论 Agent 多自动化，
> **最终对递交物负责的仍然是人**。所以系统设计的目标不是
> "全自动出结果"，而是"让人的复核效率更高、更可靠"。

---

## 23.9 完整示例：带审计的外部数据源客户端

```python
# cases/case_http_demo.py（本章配套，见 clinic/agent_http.py）
from clinic.agent_http import HttpClient, MockTransport

# 离线演示：用 MockTransport，不真的发网络请求
client = HttpClient(
    base_url="https://ct.example.org",
    token="demo-token",
    transport=MockTransport({                      # ★ 可注入的传输层
        "/api/v2/codelists/AE": {
            "version": "2024-06-01",
            "items": [{"code": "HEADACHE", "term": "Headache"}, ...],
        },
    }),
    # ★ 让"等多久"可断言：不真的睡，也不真的随机
    sleep=lambda s: waits.append(s),
    rand=lambda: 0.5,
)

resp = client.get("/api/v2/codelists/AE")      # 返回统一封装的 Response
data = resp.json()                             # 显式解析，非法 JSON 会抛 ContractError
print(f"取到 {len(data['items'])} 条术语，版本 {data['version']}")

# 审计日志（自动脱敏）
client.dump_audit("outputs/http/audit_snapshot.json")
```

**`transport` 可注入**是这里的关键设计 —— 它让"外部依赖"
在测试和演示里变成可控的：

```python
class MockTransport:
    """离线传输层：不打网络，按路径返回预置数据。"""
    def __init__(self, routes=None, fail_paths=None, fail_times=None):
        # routes     正常返回
        # fail_paths 永久失败（int 状态码 / Exception / 带响应头的 Response）
        # fail_times 前 N 次失败、之后成功 —— 专门用来验证重试
        ...

    def request(self, method, url, **kw) -> Response:
        ...
```

三种失败方式对应三种真实故障，缺一个都测不全：

| 参数 | 模拟的现实 | 验证什么 |
|---|---|---|
| `fail_paths={p: 503}` | 服务端持续报错 | 最终会失败，且**错误分类正确** |
| `fail_paths={p: TimeoutError(...)}` | 网络读超时 | 会重试，退避序列符合预期 |
| `fail_paths={p: Response(429, headers={"Retry-After": "7"})}` | 被限流 | **听服务端的 `Retry-After`** 而不是自己的公式 |
| `fail_times={p: 2}` | 抖了两下就好 | 重试**真的会发生，且只发生必要次数** |

这和 `clinic/io.py` 用 CSV 代替 XPT、`case07` 用 MockLLM 代替真实 API
是同一种思路：**把外部依赖变成可注入的接口，使系统可在离线环境下完整验证**。

---

## 23.10 常见误区

| 误区 | 后果 | 正确做法 |
|---|---|---|
| 不设 timeout | 任务永久挂起 | 显式 connect/read 两段超时 |
| 硬编码密钥 | 泄漏 + 无法轮换 | 环境变量 / 密钥服务 |
| 把 token 写进日志 | 泄漏 | 落日志前统一脱敏 |
| POST 失败就重试 | 重复创建 | 幂等键 |
| 重试不加抖动 | 恢复瞬间把对方再打垮 | 指数退避 + 随机抖动 |
| 401 自动重试 | **账号被锁** | 权限错误立即终止告警 |
| 信任对方返回的 JSON | KeyError / 类型错误 | 显式校验 + 清晰报错 |
| 不锁 API 版本 | 对方升级即崩 | 请求头写版本 |
| 先更新水位线再落盘 | **静默丢数据** | 落盘成功后才更新 |
| 把受试者标识发给外部模型 | **合规事故** | 假名化 + 最小必要 + 审计 |

---

## 23.11 本章小结

1. **超时是必填项** —— `requests` 默认永不超时
2. **密钥三不进** —— 不进代码、不进日志、不进上下文
3. **重试要看幂等性** —— POST 需要幂等键；401 绝不能重试
4. **退避要加抖动** —— 否则重试风暴
5. **外部数据不可信** —— 显式校验，报错要说清哪里不对
6. **水位线在落盘后更新** —— 否则静默丢数
7. **合规三条红线** —— 无直接标识符外发、最小必要、可审计

**下一章**：把 Agent 从脚本变成服务 —— 部署、监控、评估与成本控制。

---

## 23.12 动手练习

1. 用 `MockTransport` 构造一个"前两次超时、第三次成功"的场景，
   验证 `HttpClient` 的重试逻辑与退避时间。
2. 给 `_redact()` 加一条规则：任何长度 ≥ 32 的十六进制字符串都替换为 `***`。
   想想这会误伤什么。
3. 写一个 `check_deidentification(df) -> list[str]`，
   扫描 DataFrame 的列名，返回疑似直接标识符的列
   （提示：列名匹配 `name|dob|birth|phone|id_card|addr` 等模式，
   并且检查取值是否是"看起来像人名/日期"的字符串）。
4. 给 `Watermark` 加一个 `.verify()` 方法：拉取时如果发现
   `last_ts` 比服务端的 `earliest_available` 还早，说明**数据有缺口**，报警。
5. **思考题**：如果公司要求"所有 LLM 调用必须经过内部网关"，
   你会怎么改 `HttpClient`？（提示：`base_url` + 统一 header + 审计上报）
