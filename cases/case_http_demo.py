#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
案例 · 带审计的外部数据源客户端（第 23 章配套）
================================================

**目标**：把"调用外部 API"这件事从 ``requests.get(url)`` 变成
**能在受监管环境里活下来**的工程实现 —— 超时会分类、失败会退避、
权限错不重试、返回值不轻信、发出去的数据先去标识化、每一步都留审计。

服务端：本案例**一个真实网络请求都不发**。全部走
:class:`clinic.agent_http.MockTransport`（可注入的传输层）。

**覆盖章节**：第 23 章（外部 API 与服务集成）

为什么"本地能跑"和"上线能用"差这么远
--------------------------------------
因为真实世界里：网络会抖、对方会限流、token 会过期、返回的 JSON
会少字段、服务会挂。这些都不是"异常情况"，而是**常态**。

所以本章的每一条规则背后都有一个具体的失败现场：

    不设 timeout      →  任务永久挂起，值班的人只能重启进程
    401 自动重试      →  **账号被锁**，全组停摆
    POST 失败就重试   →  重复创建，数据里多出一批脏记录
    重试不加抖动      →  对方刚恢复就被你的重试风暴再打垮一次
    轻信返回值        →  KeyError 在凌晨两点炸在报表生成脚本里
    先更新水位线再落盘→  **静默丢数据**，而且永远补不回来
    token 写进日志    →  密钥泄漏，且没人知道泄漏了多久

六个场景
--------
a) **基本取数 + 审计脱敏** —— 请求头里有什么、日志里能看到什么
b) **超时重试与退避** —— 指数退避 + 抖动 + 尊重 ``Retry-After``
c) **401 立即终止** —— 为什么不重试比重试更"健壮"
d) **返回值不可信** —— 契约校验与"不猜、不吞"的错误
e) **水位线与降级兜底** —— 落盘成功才推进；挂了要有缓存可退
f) **合规红线** —— 去标识化、最小必要、可追溯

运行
----
    python cases/case_http_demo.py
    python cases/case_http_demo.py --only b

离线可跑，零依赖（标准库 + clinic 包）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import pandas as pd                                              # noqa: E402
from clinic import agent_http as H                               # noqa: E402

OUT = BASE / "outputs"
HTTP_DIR = OUT / "http"
AUDIT_DIR = OUT / "audit"
CACHE_DIR = OUT / "cache"
AUDIT_JSONL = AUDIT_DIR / "http.jsonl"            # 流式追加，一行一条
AUDIT_SNAPSHOT = HTTP_DIR / "audit_snapshot.json"  # 批量快照，一个 JSON 数组
ADSL = BASE / "data" / "samples" / "adsl.csv"

BASE_URL = "https://ct.example.org"

# 预置的"服务端"回包。真实项目里这些来自 CDISC / MedDRA / 内部 CT 服务。
CODELIST_AE: dict[str, Any] = {
    "version": "2024-06-01",
    "items": [
        {"code": "HEADACHE", "term": "Headache"},
        {"code": "NAUSEA", "term": "Nausea"},
        {"code": "DIZZINESS", "term": "Dizziness"},
        {"code": "FATIGUE", "term": "Fatigue"},
    ],
}
SUBJECTS_P1: dict[str, Any] = {
    "results": [
        {"subjectId": "01-701-1015", "age": "63", "sex": "M"},
        {"subjectId": "01-701-1023", "age": "70", "sex": "F"},
        {"subjectId": "01-701-1028", "age": "81", "sex": "M"},
    ],
    "next_cursor": "c2",
    "server_earliest_ts": "2026-09-01T00:00:00",
}
SUBJECTS_P2: dict[str, Any] = {
    "results": [
        {"subjectId": "01-701-1033", "age": "77", "sex": "F"},
        {"subjectId": "01-701-1034", "age": "74", "sex": "M"},
    ],
    "next_cursor": None,
}


def _h(title: str) -> None:
    print("\n" + "=" * 74)
    print(title)
    print("=" * 74)


def _p(label: str, msg: str) -> None:
    print(f"  {label} {msg}")


def _make_client(**kw: Any) -> H.HttpClient:
    """建一个**不真的睡**、**抖动固定**的客户端。

    ★ ``sleep`` 与 ``rand`` 可注入，是本章最实用的一处设计：
      · 测试/演示里不真的等 → 跑得快
      · 抖动值可控 → 断言能写死（"等待序列必须是 1.5 / 2.25 / 3.375"）

    如果只能靠 ``time.sleep`` 和真随机，重试逻辑就**没法被自动验证** ——
    而"没法验证的逻辑"在生产里等于"没写"。
    """
    sleeps: list[float] = []
    kw.setdefault("transport", H.MockTransport(routes={
        "/api/v2/codelists/AE": CODELIST_AE,
    }))
    kw.setdefault("rate_limit_per_sec", 1000.0)   # 演示里不触发真实限流
    kw.setdefault("sleep", sleeps.append)
    kw.setdefault("rand", lambda: 0.5)            # 抖动系数固定为 0.5
    kw.setdefault("audit_path", AUDIT_JSONL)
    client = H.HttpClient(BASE_URL, **kw)
    client._sleeps = sleeps                        # type: ignore[attr-defined]
    return client


# ==========================================================================
# 场景 a · 基本取数 + 审计脱敏
# ==========================================================================
def scenario_a() -> None:
    _h("场景 a · 取数与审计：token 绝不能出现在任何输出里")
    print("""
第 23.2 节的"三不进"：**凭证不进代码、不进日志、不进 Agent 上下文。**

这三条听起来像常识，但泄漏几乎都发生在"只是想看一眼"的时候 ——
打一行日志、贴一段调试输出、把请求头塞进异常信息，
密钥就进了日志系统，然后被采集、被索引、被留存。
""")
    client = _make_client(token="sk-ct-demo-9f3a7c1e5b2d4a6f")

    print("【请求头预览（脱敏后可以安全打印/落日志）】")
    for k, v in client.preview_headers().items():
        _p("·", f"{k}: {v}")
    print("""
  ↑ ``Authorization: Bearer sk-c***`` —— 打码后**还留了凭证的前 4 位**。
    保留前几位不是为了"好看"，而是为了**确认用的是哪把钥匙**
    （多环境多密钥时，这一点能救你一次排查）。

  ⚠️ 一个很容易踩的坑：如果打码逻辑只是简单地"保留前 4 个字符"，
     那 ``Bearer sk-xxx`` 会被打成 ``Bear***`` —— 等于什么都没保留。
     "方案 + 凭证"这种写法必须单独处理（见 clinic/agent_http.py 的 _mask）。
""")

    print("【取数】")
    resp = client.get("/api/v2/codelists/AE")
    data = resp.json()
    _p("·", f"GET /api/v2/codelists/AE → HTTP {resp.status}，"
            f"{len(data['items'])} 条术语，版本 {data['version']}")
    for it in data["items"][:3]:
        _p("  ", f"{it['code']:10s} {it['term']}")
    print("""
  注意这里取的是 **CT 版本号**：术语表按版本变，核查结论必须写明
  "基于哪一版 CT"。不锁版本，"同样一份数据今天跑和半年前跑结论不同"，
  而没有任何人会想到是这个原因。
""")

    print("【审计日志】")
    snapshot = client.dump_audit(AUDIT_SNAPSHOT)
    rows = json.loads(snapshot.read_text(encoding="utf-8"))
    _p("·", f"快照（JSON 数组）{len(rows)} 条 → {snapshot.relative_to(BASE)}")
    print("  " + json.dumps(rows[-1], ensure_ascii=False)[:170] + "……")
    lines = [json.loads(ln) for ln
             in AUDIT_JSONL.read_text(encoding="utf-8").splitlines() if ln.strip()]
    _p("·", f"流式 JSONL {len(lines)} 行 → {AUDIT_JSONL.relative_to(BASE)}")
    print(f"""
  ★ 这两种审计产物**不能写进同一个文件**：

      audit_path（JSONL）    一行一条，只增不改 → 适合喂给日志采集系统
      dump_audit（JSON 数组）批量快照          → 适合打包交给别人

    混在一起的结果是**谁也解析不了** —— 第一行是 ``[``，
    后面的行又各自是完整 JSON。这类问题的排查成本很高，
    因为它看起来只是"文件格式怪怪的"。本案例把它们分开写。

  在整份审计文件里搜密钥片段：
""")
    secrets = ("sk-ct-demo", "9f3a7c1e")
    for p in (AUDIT_JSONL, snapshot):
        raw = p.read_text(encoding="utf-8")
        leaked = [s for s in secrets if s in raw]
        _p("·", f"{p.name:24s} → "
                f"{'⚠️ 找到了！' if leaked else '未找到 ✓（脱敏生效）'}")
    mem = json.dumps(client.audit, ensure_ascii=False)
    _p("·", f"{'内存审计记录':22s} → "
            f"{'⚠️ 找到了！' if any(s in mem for s in secrets) else '未找到 ✓'}")
    _p("·", f"{'请求头预览':22s} → {client.preview_headers()['Authorization']}")
    print("""
  ★ 这里要分清两件事，它们的作用不一样：

    ① **审计记录里干脆不写凭证**（``_record`` 的字段里没有 Authorization）
       —— "不写"永远比"写了再脱敏"可靠。少一个字段，就少一条泄漏路径。

    ② 而**必须让凭证经过的地方**（请求头预览、异常信息、调试输出）
       才需要 :func:`redact` 兜底。

    ★ 脱敏必须在**入库前**做，而不是事后清理日志文件：
      事后清理必然漏 —— 日志在写下的那一刻就已经被采集系统拉走了，
      你清理的只是本地副本。
""")


# ==========================================================================
# 场景 b · 重试、指数退避与抖动
# ==========================================================================
def scenario_b() -> None:
    _h("场景 b · 网络抖了：指数退避 + 抖动 + 尊重 Retry-After")
    print("""
两种情况要分开：

  · **瞬时故障**（连接被重置、502/503、读超时）→ 重试
  · **确定性失败**（400 参数错、404 不存在、401 权限）→ **立即终止**

把后者也去重试，就是在拿"本可以立刻发现的错"换"更晚才发现的错"，
而且会浪费配额、刷爆对方的日志。
""")

    # ---- 前两次超时，第三次成功 ----
    print("【① 前两次读超时、第三次成功：验证「重试真的会发生，且只发生必要次数」】")
    transport = H.MockTransport(
        routes={"/api/v2/codelists/AE": CODELIST_AE},
        # fail_times 的语义：**前 N 次失败，之后成功** —— 这样才能验证重试
        fail_times={"/api/v2/codelists/AE": 2},
        fail_paths={"/api/v2/codelists/AE": TimeoutError("模拟读超时")},
    )
    client = _make_client(transport=transport, token="sk-demo",
                          max_retries=3)
    resp = client.get("/api/v2/codelists/AE")
    _p("·", f"最终 HTTP {resp.status} ✓（前两次失败）")
    _p("·", f"实际发出的请求次数：{len(transport.calls)}（= 2 次失败 + 1 次成功）")
    _p("·", f"退避等待序列：{client.backoff_log} 秒")
    print("""
     1.15 → 1.725 是**几何增长**（比例 = ``backoff_base`` = 1.5），
     每个间隔再叠加 ±30% 的抖动（本演示把抖动系数固定为 0.5，便于断言）。

  ⚠️ 注意第一次重试只等了约 1 秒，**不是 1.5 秒**：
     ``backoff_base ** attempt`` 里 ``attempt`` 从 0 开始。
     这个细节值得较真 —— "第一次就退避很久"是常见写法错误，
     而抖动类故障（丢包、瞬间限流）往往重试一次就过了，
     让用户白等几秒毫无收益。

  ★ 为什么要抖动：如果 1000 个客户端都用**同样的间隔**重试，
    对方服务刚恢复的瞬间就会被同一波重试再打垮 —— 这叫重试风暴。
    抖动把这 1000 个客户端错开，恢复才真的能稳住。
    教科书上常写"指数退避"，但漏掉抖动那一半的人，往往在
    第一次真实故障里才学到这一课。
""")

    # ---- Retry-After ----
    print("【② 服务端说「等一下」：必须听它的】")
    # MockTransport 的 fail_paths 支持直接写一个 Response ——
    # 这样就能离线构造"429 + Retry-After"这种带响应头的失败。
    transport2 = H.MockTransport(
        routes={"/api/v2/subjects": SUBJECTS_P1},
        fail_times={"/api/v2/subjects": 1},
        fail_paths={"/api/v2/subjects": H.Response(
            status=429, text='{"error":"too many requests"}',
            headers={"Retry-After": "7"})},
    )
    client2 = _make_client(transport=transport2, token="sk-demo")
    resp2 = client2.get("/api/v2/subjects")
    _p("·", f"最终 HTTP {resp2.status} ✓（第一次被 429 挡住）")
    _p("·", f"实际请求次数：{len(transport2.calls)}")
    _p("·", f"退避等待序列：{client2.backoff_log} 秒（服务端要求 7 秒）")
    print("""
  ★ ``Retry-After`` **优先级高于**我们自己的退避公式。
    对方说 7 秒就等 7 秒 —— 你算出来的 1.5 秒不但没用，还会让它
    把你当攻击者。所以 :meth:`HttpClient.backoff` 里第一件事就是
    判断 ``retry_after is not None``。
""")


# ==========================================================================
# 场景 c · 401 立即终止
# ==========================================================================
def scenario_c() -> None:
    _h("场景 c · 401 绝不重试：为什么「更少的重试」等于「更健壮」")
    print("""
这是本章最反直觉、也最容易写错的一条。

    "重试"看起来是健壮性的表现。但对 **401 / 403** 来说，
    重试的每一次都是在**拿错误凭证敲门** —— 很多网关的账号锁定策略
    就是按"短时间内认证失败次数"触发的。

后果不是"这次请求失败"，而是**整个组的账号被锁**，
所有并行任务一起停摆，而根因只是某个人把 token 配错了。
""")
    transport = H.MockTransport(
        routes={"/api/v2/codelists/AE": CODELIST_AE},
        fail_paths={"/api/v2/codelists/AE": 401},
    )
    client = _make_client(transport=transport, token="sk-过期了", max_retries=5)
    try:
        client.get("/api/v2/codelists/AE")
        _p("✗", "竟然成功了（不该）")
    except H.AuthError as e:
        _p("✓", f"抛出 AuthError：{str(e).splitlines()[0]}")
    _p("·", f"实际请求次数：{len(transport.calls)}"
            f"（max_retries=5，但**一次都没重试**）")
    _p("·", f"退避等待序列：{client.backoff_log}（空 = 没有等待）")
    print("""
  → 判断依据在 :func:`clinic.agent_http.classify` 里：
    ``retryable=False`` 的错误，主循环直接 ``raise``，不进入退避分支。

  ★ 这条规则的正确表述是：**重试只适用于"再试一次可能就好了"的错。**
    权限错、参数错、不存在 —— 再试一万次也是同样的结果。
    把这类错误重试，只是在把"早发现"改成"晚发现"，
    顺便多烧一份配额、多留一百行无用日志。

  ⚠️ 现实里正确的动作是：AuthError 触发**告警**（而不是重试），
     让值班的人去看"是 token 过期了，还是配置写错了"。
""")


# ==========================================================================
# 场景 d · 返回值不可信
# ==========================================================================
def scenario_d() -> None:
    _h("场景 d · 对方返回的 JSON 不可信：显式校验，不猜、不吞")
    print("""
"对方是标准接口，返回格式不会变" —— 这个假设在凌晨两点会被打破。

对方可能：升级 API 改了字段名、错误时返回 HTML 页面、
限流时返回一个半截 JSON、字段类型从 str 变成 int。
如果你的代码直接 ``data["results"][0]["subject_id"]``，
得到的是 ``KeyError`` / ``TypeError`` —— **在报表生成脚本里，
在最不容易复现的时间点**。

正确做法：在边界处**显式校验**，并给出能自己修错的错误信息。
""")

    # ① 缺字段
    print("【① 对方把顶层字段改名了：results → items】")
    try:
        H.parse_subjects({"items": [{"subjectId": "01-701-1015"}]})
    except H.ContractError as e:
        _p("✓", f"ContractError：{str(e).splitlines()[0]}")
    print("""
   ↑ 错误信息里带上了**实际的顶层键** —— 这是关键。
     Agent 或人都能立刻看出"对方把 results 改叫 items 了"，
     而不是收到一句 "KeyError: 'results'" 然后去翻对方文档。
     这就是第 20 章讲的"结构化错误"：**错误信息要能驱动下一步动作。**

  ⚠️ 注意这里**不能"猜"**：看到 items 就直接当 results 用，
     看起来"兼容性更好"，实际上是**静默接受了一份来源不明的结构**。
     如果对方返回的 items 是另一种含义（比如"待处理项"），
     你的报表会安静地错到底。宁可失败，也不要猜。
""")

    # ② 单条记录字段改名
    print("【② 单条记录里的字段改名了：subjectId → id】")
    try:
        H.parse_subjects({"results": [{"id": "01-701-1015", "age": "63"}]})
        _p("·", "（被接受了（不该））")
    except H.ContractError as e:
        _p("✓", f"ContractError：{str(e).splitlines()[0]}")

    # ③ 正常
    print("【③ 正常响应】")
    rows = H.parse_subjects(SUBJECTS_P1)
    _p("·", f"校验通过，{len(rows)} 条受试者；"
            f"下页游标 {SUBJECTS_P1['next_cursor']!r}")
    for r in rows:
        _p("  ", f"{r['subject_id']}  age={r['age']}  sex={r['sex']}")
    print("""
  ★ 注意校验后的结构是**我们自己的**（``subject_id`` / ``age`` / ``sex``），
    而不是对方的原样。这层"防腐层"的价值在于：
    对方改字段名时，只需要改这一处的映射，业务代码一行不动。
    同时注意 ``age`` 被宽容地转成了数值、``'N/A'`` 会变成 ``None`` ——
    **宽进严出**：入口容错，出口受控。

  ★ 还有"游标"这个概念：**不要自己拼页码**。
    外部服务的分页规则会变（offset → cursor → 时间戳），
    跟着它给的 ``next_cursor`` 走，是最省心的做法。
    同时记住：**翻页期间数据可能在变**，所以增量同步要靠
    "水位线 + 幂等写入"，而不是靠"页码算得对"。
""")

    # ④ 返回 HTML
    print("【④ 对方错误时返回了 HTML 页面（而不是 JSON）】")
    bad = H.Response(status=200, text="<html><body>506 Gateway Timeout</body></html>")
    try:
        bad.json()
    except H.ContractError as e:
        _p("✓", f"ContractError：{str(e).splitlines()[0][:88]}）")
    print("""
  ⚠️ 特别注意：这个响应 **HTTP 状态码是 200**。
     只看状态码的代码会以为成功，然后拿着 HTML 去解析。
     "200 就一定是好数据"是外部集成里最常见的错误假设之一。
""")


# ==========================================================================
# 场景 e · 水位线与降级兜底
# ==========================================================================
def scenario_e() -> None:
    _h("场景 e · 水位线：落盘成功之后才能推进")
    print("""
本案例最贵的一张教训表：

    先落盘、后更新水位线  →  崩溃时最多"重复拉一次"，**幂等写入能兜住**
    先更新水位线、后落盘  →  崩溃时**这段数据永远不会被再拉到**

后者是**静默丢数据**：报表少了一批受试者，而所有日志都是绿的。
临床统计里"少了几个人"和"多了几个人"一样致命，
而且"少了"更难发现 —— 因为没有任何报错。
""")
    WM = HTTP_DIR / "watermark.json"
    if WM.exists():
        WM.unlink()

    print("【① 第一轮增量拉取：拉完 → 落盘 → 才 mark()】")
    wm = H.Watermark.load(WM)
    _p("·", f"起始水位线：last_ts={wm.last_ts}（第一次同步，从零开始）")

    data_path = HTTP_DIR / "subjects_batch1.json"
    data_path.parent.mkdir(parents=True, exist_ok=True)
    first = H.parse_subjects(SUBJECTS_P1)
    data_path.write_text(json.dumps(first, ensure_ascii=False), encoding="utf-8")
    _p("·", f"落盘 {len(first)} 条 → {data_path.relative_to(BASE)}")
    wm.mark(last_id=first[-1]["subject_id"], last_ts="2026-09-10T00:00:00")
    wm.save(WM)
    _p("·", f"落盘**成功之后**才推进水位线：last_ts={wm.last_ts}")
    _p("·", f"（顺序反了就是静默丢数：崩溃点之后的数据再也不会被拉到）")

    print("\n【② 第二轮：水位线继续往前推】")
    wm2 = H.Watermark.load(WM)
    second = H.parse_subjects(SUBJECTS_P2)
    _p("·", f"从磁盘恢复水位线 last_ts={wm2.last_ts} 继续拉，拿到 {len(second)} 条")
    wm2.mark(last_id=second[-1]["subject_id"], last_ts="2026-09-15T00:00:00")
    wm2.save(WM)
    _p("·", f"新水位线 last_ts={wm2.last_ts}")

    print("\n【③ 缺口检测：本地水位线早于「服务端最早可提供」】")
    warn = wm2.verify(SUBJECTS_P1["server_earliest_ts"])
    if warn:
        _p("⚠️", warn)
    print("""
  ↑ 这一步是**必须有**的：增量同步最危险的失败不是"拉不到"，
    而是"拉到了，但中间缺了一段"。
    对方做了归档、只保留最近 N 天 —— 你的水位线只要老过那个边界，
    就已经漏数据了。此时正确动作是**报警 + 全量重拉**，不是继续增量。
""")

    print("【④ 降级兜底：服务挂了，用缓存顶上，并明确标注「这是缓存」】")
    cache_path = CACHE_DIR / "codelist_AE.json"
    live = H.CachedFallback(_make_client(token="sk-demo"), cache_path)
    d1, src1 = live.get("/api/v2/codelists/AE")
    _p("·", f"第一次：{src1} → {len(d1['items'])} 条")

    dead = H.CachedFallback(
        _make_client(token="sk-demo", transport=H.MockTransport(
            fail_paths={"/api/v2/codelists/AE": TimeoutError("服务不可用")})),
        cache_path)
    d2, src2 = dead.get("/api/v2/codelists/AE")
    _p("·", f"服务挂掉后：{src2} → {len(d2['items'])} 条（与缓存一致：{d2 == d1}）")
    print("""
  ⚠️ 降级的铁律：**必须把"这是缓存"写进结果本身**，而不是只写进日志。

     "用 3 天前的 CT 版本做的核查" —— 如果不标注，交付时会被当成
     "用最新版本核实过"。这两句话在监管眼里是完全不同的东西。
     所以 ``CachedFallback.get()`` 的返回值是 ``(数据, 来源说明)`` 两元组，
     逼着调用方**没法忽略**这件事。
""")


# ==========================================================================
# 场景 f · 合规红线
# ==========================================================================
def scenario_f() -> None:
    _h("场景 f · 合规：把数据发给外部模型之前的四道检查")
    print("""
第 23.8 节的自查表，落到代码上就是四步：

    ① 直接标识符剔除   ② 只发必需字段   ③ 记录审计   ④ 结论可追溯

第 ① 步最难，因为**列名看不出来**。下面这个例子就是真实情况：
两列有明显问题（列名直接命中），还有一列列名是 ``VAR1``、里面装的是人名 ——
只看列名永远发现不了。
""")
    df = pd.read_csv(ADSL, dtype=str, low_memory=False).head(40)
    df = df[["USUBJID", "SITEID", "AGE", "SEX", "TRT01P"]].copy()
    # 人为构造三种"不该外发"的列（真实数据里它们常常是导出时顺手带进来的）
    df["PATIENT_NAME"] = ["张伟", "李娜", "王强", "刘洋", "陈静"] * 8
    df["BIRTHDT"] = "1950-03-17"
    df["VAR1"] = ["Michael Smith", "Anna Jones", "David Brown",
                  "Sarah Lee", "Tom King"] * 8

    print("【扫描 1：列名 + 取值双层检查】")
    problems = H.check_deidentification(df)
    if not problems:
        _p("·", "未发现疑似直接标识符")
    for p in problems:
        _p("⚠️", p)
    print("""
  ★ 注意前两条是**列名**命中的（``PATIENT_NAME`` / ``BIRTHDT``），
    第三条 ``VAR1`` 靠的是**取值形态** —— 只看列名的检查会放过它，
    而它恰恰是最容易真实发生的：ETL 里加了个中间列，名字毫无信息量。

  ⚠️ 这类检查宁可误报不可漏报：
     误报的成本是"多看一眼"，漏报的成本是"患者信息进了第三方服务"。
""")

    print("【扫描 2：剔除后复检】")
    safe = df.drop(columns=["PATIENT_NAME", "BIRTHDT", "VAR1"])
    left = H.check_deidentification(safe)
    _p("·", f"剔除三列后剩余问题：{left if left else '无 ✓'}")

    print("\n【最小必要：只发需要的列】")
    need = ["USUBJID", "AGE", "SEX", "TRT01P"]
    payload = H.build_llm_payload(safe, need)
    _p("·", f"只发 {len(need)} 列，{len(payload)} 字符")
    print("  " + payload.replace("\n", "\n  ").rstrip())
    try:
        H.build_llm_payload(safe, ["USUBJID", "RACE"])
    except ValueError as e:
        _p("✓", f"要求不存在的列时立刻报错：{str(e).splitlines()[0]}")
    print("""
  ★ "最小必要"有两个好处，而且第二个常被忽略：

      合规：少一列就少一分泄漏面
      成本：少一列就少一分 token —— 40 列的表全发和发 4 列，
            费用差一个量级，而结论质量几乎不受影响
""")

    print("【审计：每次模型调用落一行（存摘要，不存全文）】")
    path = H.log_llm_call(prompt=payload, response="（演示用空响应）",
                          model="内部网关/医学模型-v2", audit_dir=AUDIT_DIR)
    rec = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
    _p("·", f"{path.relative_to(BASE)}")
    _p("·", f"prompt_sha256={rec['prompt_sha256']} "
            f"chars={rec['prompt_chars']} model={rec['model']}")
    print("""
  ★ 存 **sha256 摘要**而不是全文，是一个刻意的取舍：

      能证明：这次发给模型的，就是这一份内容（可复算、可对账）
      不能：把完整数据留在日志里 —— 日志的访问控制通常比数据库松得多

    但注意这里的短板：``prompt_preview`` 里**前 200 字符是明文**。
    真实项目里 200 字符也可能含标识符，所以更严的做法是
    预览也做二次脱敏，或者只留字段名不留值。
""")

    print(f"""【一张自查表（第 23.8 节）】

  ┌────────────────────────────┬──────────────────────────────────────────┐
  │ 检查项                     │ 本案例对应的代码                         │
  ├────────────────────────────┼──────────────────────────────────────────┤
  │ 外发数据里有直接标识符吗   │ check_deidentification()  双层判据       │
  │ 只发了必需字段吗           │ build_llm_payload(df, need)  必须显式列  │
  │ 有数据出境问题吗           │ 由 base_url + DPA 决定，代码管不了 ——    │
  │                            │ 所以它必须是**上线检查清单**的一项       │
  │ 有 DPA / 授权吗            │ 同上，流程问题                           │
  │ 审计日志记了吗             │ log_llm_call() + HttpClient.dump_audit() │
  │ 能人工复核吗               │ 结论带 source/版本；见第 21 章引用机制    │
  └────────────────────────────┴──────────────────────────────────────────┘

  ⚠️ 最后一行"能人工复核吗"是临床统计的**根本**：

     无论 Agent 多自动化，**最终对递交物负责的仍然是人**。
     所以系统设计的目标不是"全自动出结果"，而是
     "**让人的复核效率更高、更可靠**"。

     一个把结论写得像"系统说的"的 Agent，反而降低了复核质量 ——
     因为它让人失去了质疑的入口。
""")


# ==========================================================================
def main() -> None:
    ap = argparse.ArgumentParser(description="案例 · 带审计的外部数据源客户端")
    ap.add_argument("--only", default="all",
                    help="只跑某个场景：a/b/c/d/e/f/all")
    args = ap.parse_args()

    HTTP_DIR.mkdir(parents=True, exist_ok=True)
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    # 每次从干净的审计文件开始，这样计数与"有没有泄漏"的结论才可信
    if AUDIT_JSONL.exists():
        AUDIT_JSONL.unlink()

    print("=" * 74)
    print("案例 · 带审计的外部数据源客户端（第 23 章配套）")
    print("=" * 74)
    print(f"""
一个真实网络请求都不发 —— 全部走 MockTransport（可注入的传输层）。
这正是本章最核心的工程手法：**把外部依赖变成可注入的接口**，
使系统能在离线环境下被完整验证。

产出目录：
  {HTTP_DIR.relative_to(BASE)}      水位线、批次数据
  {AUDIT_DIR.relative_to(BASE)}     审计（已脱敏）
  {CACHE_DIR.relative_to(BASE)}     降级用的缓存
""")

    picked = {s.strip().lower() for s in args.only.split(",") if s.strip()}
    want = (lambda k: "all" in picked or k in picked)

    if want("a"):
        scenario_a()
    if want("b"):
        scenario_b()
    if want("c"):
        scenario_c()
    if want("d"):
        scenario_d()
    if want("e"):
        scenario_e()
    if want("f"):
        scenario_f()

    print("\n" + "=" * 74)
    print("案例结束。把第 23 章的规则压缩成四句话：")
    print("  ① 超时、重试、退避、限流 —— 一个都不能省，且必须能被自动断言")
    print("  ② 401/403 立即终止：重试「看起来健壮」的错，代价可能是全组账号被锁")
    print("  ③ 先落盘、后推水位线 —— 顺序反了就是永远补不回来的静默丢数")
    print("  ④ 外发数据先去标识化；结论必须能追溯到人、到版本、到源数据")
    print("=" * 74)


if __name__ == "__main__":
    main()
