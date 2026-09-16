#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
案例 10 · TLF 生成流水线（任务规划与并行调度）
==============================================

**目标**：把"生成一套 TLF"从**一串 if 和 shell 脚本**，
变成一张**可校验、可并行、可重试、可断点续跑**的任务图。

**覆盖章节**：第 19 章（任务规划与调度）

为什么"流水线"值得单独做一张图
------------------------------
传统做法是一个 `run_all.sas` / `run_all.sh` 顺序调 11 个程序。
它有三个说不出口的问题：

====================  ==============================  ============================
问题                   顺序脚本的表现                   任务图的表现
====================  ==============================  ============================
跑得慢                 11 个程序一个个跑                **没依赖的一起跑**
失败后怎么办           停在那里，人肉判断从哪接着跑      失败传播明确：下游 skipped
重跑一遍               全部重跑，浪费时间                只跑未完成的（断点续跑）
"为什么表 3 要等表 2"   没人知道，靠口头传承               `depends_on` 写在代码里
出错原因               日志里翻半天                      每个任务带 error / error_type
====================  ==============================  ============================

任务图的核心不是"并行"（那是收益），而是**显式依赖**（那是正确性）。

五个场景
--------
a) **需求 → DAG**      拆解、静态校验、拓扑排序、dry-run
b) **并行调度**        分层执行 + 真实耗时对比（含 I/O 型任务的加速比）
c) **失败与重试**      transient 重试、可选任务不阻塞、失败传播
d) **部分失败汇总**    "结论仅覆盖已完成部分" —— 这句话必须由框架生成
e) **HITL 与断点续跑** 写操作暂停 → 计划存盘 → 确认后只跑未完成的

运行
----
    python cases/case10_TLF生成流水线.py
    python cases/case10_TLF生成流水线.py --only b
    python cases/case10_TLF生成流水线.py --serial      # 强制单线程对比

离线可跑：工具真实执行，产物写到 outputs/tlf/。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import clinic.agent_tools as T                                   # noqa: E402
from clinic import agent_core as core                            # noqa: E402
from clinic import agent_planner as planmod                      # noqa: E402
from clinic import report as rpt                                 # noqa: E402

OUT = BASE / "outputs"
TLF_DIR = OUT / "tlf"
ALLOWED = frozenset(T.ALLOWED_DATASETS)


# ==========================================================================
# 工具层：TLF 生成流水线实际要调的动作
# ==========================================================================
_IO_WAIT = 0.25          # 模拟"从外部数据源取快照"的等待时间（见下方说明）
_fail_counter: dict[str, int] = {}


def build_registry() -> core.ToolRegistry:
    reg = core.ToolRegistry()

    @reg.tool("fetch_snapshot",
              "从数据源取某个域的快照元信息。这是**模拟外部接口调用**的工具，"
              "用于演示 I/O 等待型任务的并行收益。",
              {"type": "object",
               "properties": {
                   "dataset": {"type": "string", "enum": sorted(ALLOWED),
                               "description": "要取快照的数据集"},
                   "source": {"type": "string",
                              "description": "数据源标识，如 edc / cdisc-ct"}},
               "required": ["dataset"], "additionalProperties": False})
    def _snapshot(args: dict) -> dict:
        # ★ 生产里这里是 HTTP 请求 / 数据库查询 —— 时间花在"等"上，不花在 CPU 上。
        #   这正是线程池能带来加速的原因（第 19.5 节）。
        time.sleep(_IO_WAIT)
        d = T.describe_dataset(args["dataset"])
        return {"数据集": args["dataset"],
                "数据源": args.get("source") or "edc",
                "行数": d["行数"], "列数": d["列数"],
                "快照时间": time.strftime("%Y-%m-%dT%H:%M:%S")}

    @reg.tool("describe_dataset",
              "查看数据集结构（变量、缺失率、示例值）。",
              {"type": "object",
               "properties": {
                   "dataset": {"type": "string", "enum": sorted(ALLOWED)}},
               "required": ["dataset"], "additionalProperties": False})
    def _describe(args: dict) -> dict:
        return T.describe_dataset(args["dataset"])

    @reg.tool("demo_table",
              "生成人口学与基线特征表（Table 1）的数据：按治疗组的年龄描述统计"
              "与性别、种族频数。返回可直接渲染成报表的行数据。",
              {"type": "object",
               "properties": {
                   "dataset": {"type": "string", "enum": sorted(ALLOWED),
                               "description": "分析人群数据集，通常是 adsl"}},
               "required": ["dataset"], "additionalProperties": False})
    def _demo(args: dict) -> dict:
        ds = args["dataset"]
        age = T.summarize_by_group(ds, "TRT01P", "AGE")
        sex = T.frequency(ds, "SEX")
        race = T.frequency(ds, "RACE")
        return {"表": "Table 1 · 人口学与基线特征",
                "人群": ds,
                "年龄描述统计": age["结果"],
                "性别分布": sex.get("分布"),
                "种族分布": race.get("分布"),
                "注": "分母为各治疗组分析人群人数；SD 为样本标准差（ddof=1）"}

    @reg.tool("ae_table",
              "生成不良事件汇总表（Table 2）的数据：按 SOC 或 PT 的治疗中出现"
              "不良事件受试者数与百分比。",
              {"type": "object",
               "properties": {
                   "level": {"type": "string", "enum": ["soc", "pt"],
                             "description": "汇总层级"},
                   "treatment_only": {"type": "boolean",
                                      "description": "是否只统计 TEAE，默认 true"},
                   "top_n": {"type": "integer", "minimum": 1, "maximum": 50,
                             "description": "返回前多少个 SOC/PT，默认 10"}},
               "required": ["level"], "additionalProperties": False})
    def _ae(args: dict) -> dict:
        return T.count_events(level=args["level"],
                              treatment_only=args.get("treatment_only", True),
                              top_n=int(args.get("top_n") or 10))

    @reg.tool("shift_table",
              "生成实验室指标移位表（Table 3）的数据：基线正常性分类 × "
              "基线后正常性分类的交叉表，单元格为 n (%)。",
              {"type": "object",
               "properties": {
                   "param": {"type": "string",
                             "description": "实验室参数代码，如 ALT / CREAT / HGB"},
                   "pct": {"type": "string", "enum": ["row", "col", "total"],
                           "description": "百分比分母：行/列/总计，默认 row"}},
               "required": ["param"], "additionalProperties": False})
    def _shift(args: dict) -> dict:
        df = T._load("adlbc_shift")
        param = str(args["param"]).strip().upper()
        sub = df[df["PARAMCD"].astype(str).str.strip() == param].copy()
        if sub.empty:
            raise ValueError(
                f"参数 {param!r} 不存在。可选值："
                f"{sorted(df['PARAMCD'].astype(str).str.strip().unique())[:20]}")

        # 基线：ABLFL='Y' 优先，否则用 AVISIT='Baseline'
        sub["AVISIT"] = sub["AVISIT"].astype(str).str.strip()
        bl = sub[(sub["ABLFL"].astype(str).str.strip() == "Y")
                 | (sub["AVISIT"] == "Baseline")]
        bl = bl.sort_values("USUBJID").drop_duplicates("USUBJID")
        bl_ind = "BNRIND" if bl["BNRIND"].notna().any() else "ANRIND"
        base = bl.set_index("USUBJID")[bl_ind].astype(str).str.strip().str[:1].str.upper()

        # 基线后：优先末次访视（End of Treatment），否则按 AVISITN 最大
        sub["AVISITN"] = _num(sub["AVISITN"])
        post = sub[sub["AVISIT"] != "Baseline"]
        eot = post[post["AVISIT"] == "End of Treatment"]
        post = eot if not eot.empty else \
            post[post["AVISITN"] == post.groupby("USUBJID")["AVISITN"].transform("max")]
        post = post.sort_values("AVISITN").drop_duplicates("USUBJID", keep="last")
        post_s = post.set_index("USUBJID")["ANRIND"].astype(str).str.strip().str[:1].str.upper()

        common = base.index.intersection(post_s.index)
        if len(common) == 0:
            raise ValueError(f"{param}: 基线与基线后记录无法按受试者对齐")
        tbl = rpt.crosstab_shift(base.loc[common], post_s.loc[common],
                                 ["L", "N", "H"], pct=args.get("pct") or "row")
        tbl = tbl.reset_index()
        tbl.columns = [str(c) for c in tbl.columns]
        return {"表": f"Table 3 · 移位表（{param}）",
                "参数": param,
                "受试者数": int(len(common)),
                "基线分类来源": bl_ind,
                "百分比分母": args.get("pct") or "row",
                "行": tbl.to_dict(orient="records"),
                "注": "分级 L/N/H = 低/正常/高；基线取 ABLFL='Y'"}

    @reg.tool("qc_domain",
              "对数据集跑标准质量检查。TLF 生成前必须先过这一关 —— "
              "**发现高优先级问题时应中止出表**。",
              {"type": "object",
               "properties": {
                   "dataset": {"type": "string", "enum": sorted(ALLOWED)},
                   "max_issues": {"type": "integer", "minimum": 1, "maximum": 50,
                                  "description": "最多返回多少条明细，默认 10"}},
               "required": ["dataset"], "additionalProperties": False})
    def _qc(args: dict) -> dict:
        res = dict(T.run_qc_checks(args["dataset"]))
        n = int(args.get("max_issues") or 10)
        res["问题"] = res["问题"][:n]
        return res

    # ---------------------------------------------------- 故障注入用（场景 c）
    @reg.tool("flaky_tool",
              "诊断用工具：前 N 次抛 transient 错误（模拟对方服务不稳定），"
              "之后成功。用于验证调度器的重试逻辑。",
              {"type": "object",
               "properties": {
                   "key": {"type": "string", "description": "故障计数器的标识"},
                   "fail_times": {"type": "integer", "minimum": 0, "maximum": 10,
                                  "description": "前几次失败，默认 2"}},
               "required": ["key"], "additionalProperties": False})
    def _flaky(args: dict) -> dict:
        key = str(args["key"])
        n = int(args.get("fail_times") or 2)
        seen = _fail_counter.get(key, 0)
        if seen < n:
            _fail_counter[key] = seen + 1
            _raise_transient(seen)
        return {"成功": True, "第几次尝试": seen + 1, "key": key}

    @reg.tool("always_timeout",
              "诊断用工具：永远返回 transient 错误。用于验证“重试用尽后失败”"
              "如何传播到下游。",
              {"type": "object",
               "properties": {"reason": {"type": "string",
                                         "description": "错误信息"}},
               "required": [], "additionalProperties": False})
    def _timeout(args: dict) -> core.ToolResult:
        return core.ToolResult.fail(
            args.get("reason") or "对方服务 503，重试已用尽", "transient",
            hint="稍后重跑该任务；上游数据源可能正在维护")

    # ------------------------------------------------------------- 写操作
    @reg.tool("write_tlf",
              "把本批 TLF 的交付清单与关键指标写入文件。这是**写操作**，"
              "执行前需要人工确认。",
              {"type": "object",
               "properties": {
                   "name": {"type": "string",
                            "description": "输出文件名（不含路径），如 tlf_manifest"},
                   "content": {"type": "string", "description": "文件内容"}},
               "required": ["name", "content"], "additionalProperties": False},
              side_effect="write", idempotent=False)
    def _write(args: dict) -> dict:
        name = Path(str(args["name"])).name
        if not name.endswith(".md"):
            name += ".md"
        TLF_DIR.mkdir(parents=True, exist_ok=True)
        p = TLF_DIR / name
        p.write_text(args["content"], encoding="utf-8")
        return {"已写入": str(p.relative_to(BASE)), "字节数": p.stat().st_size}

    return reg


def _num(s):
    import pandas as pd
    return pd.to_numeric(s, errors="coerce")


def _raise_transient(seen: int):
    raise TimeoutError(f"读取失败（第 {seen + 1} 次尝试）：对方服务无响应")


# ==========================================================================
# 计划：一张 11 个任务的 DAG
# ==========================================================================
def build_plan(flaky_ae: bool = False) -> planmod.Plan:
    """把"出一套 TLF"拆成任务图。

    拆解依据（MECE：互斥且穷尽）：
      取数（每域一个）→ 出表（每张表一个）→ 质检（每个源域一个）→ 汇总 → 写交付物

    ★ 注意：**每个任务只依赖它真正需要的东西**。
      表 2（AE）不需要等表 1（人口学）—— 这一点如果写错，
      并行度就从 5 掉到 1，而没人会立刻发现。
    """
    p = planmod.Plan(goal="为 CDISCPILOT01 生成一批 TLF 并输出交付清单",
                     meta={"study": "CDISCPILOT01", "batch": "2026-09-Q3"})

    # ---- L0：取数（4 个互不依赖）----
    for ds, src in (("adsl", "edc"), ("adae", "edc"),
                    ("adlbc_shift", "central-lab")):
        p.add(planmod.Task(
            id=f"fetch_{ds}", title=f"取 {ds} 快照",
            tool="fetch_snapshot", args={"dataset": ds, "source": src},
            depends_on=()))
    p.add(planmod.Task(
        id="fetch_ct", title="取 CDISC 受控术语版本",
        tool="fetch_snapshot", args={"dataset": "dm", "source": "cdisc-ct"},
        depends_on=(), optional=True))          # ★ 可选：取不到也不该卡住出表

    # ---- L1：出表与质检（5 个并行）----
    p.add(planmod.Task(id="t1_demo", title="Table 1 人口学",
                       tool="demo_table", args={"dataset": "adsl"},
                       depends_on=("fetch_adsl",)))
    p.add(planmod.Task(id="t2_ae", title="Table 2 TEAE 汇总",
                       tool="ae_table", args={"level": "soc", "top_n": 10},
                       depends_on=("fetch_adae",)))
    p.add(planmod.Task(id="t3_shift", title="Table 3 移位表",
                       tool="shift_table", args={"param": "ALT", "pct": "row"},
                       depends_on=("fetch_adlbc_shift",)))
    p.add(planmod.Task(id="qc_adsl", title="ADSL 质量检查",
                       tool="qc_domain", args={"dataset": "adsl", "max_issues": 5},
                       depends_on=("fetch_adsl",)))
    p.add(planmod.Task(id="qc_adae", title="ADAE 质量检查",
                       tool="qc_domain", args={"dataset": "adae", "max_issues": 5},
                       depends_on=("fetch_adae",)))

    # ---- L2：汇总（纯编排节点，无工具调用）----
    p.add(planmod.Task(
        id="assemble", title="汇总交付清单",
        tool="", depends_on=("t1_demo", "t2_ae", "t3_shift", "qc_adsl", "qc_adae")))

    # ---- L3：写交付物（写操作，需人工确认）----
    p.add(planmod.Task(id="deliver", title="写入交付清单",
                       tool="write_tlf",
                       args={"name": "tlf_manifest",
                             "content": _manifest_text(p)},
                       depends_on=("assemble",)))
    return p


def _manifest_text(p: planmod.Plan) -> str:
    """按"计划定义"渲染交付清单。

    ⚠️ 注意这里**取的是计划定义，不是任务的执行结果** ——
    因为 ``Task.args`` 是静态的（计划要能序列化）。真实项目里
    应该由上游任务把结果落盘，下游按路径读后再渲染（见场景 e 末尾说明）。
    """
    lines = [
        "# TLF 交付清单",
        "",
        f"- 研究：{p.meta.get('study')}",
        f"- 批次：{p.meta.get('batch')}",
        f"- 任务总数：{len(p.tasks) + 1}",        # +1 = 本清单任务本身
        "",
        "| 任务 | 工具 | 依赖 | 产物 |",
        "|---|---|---|---|",
    ]
    art = {"t1_demo": "Table 1 人口学与基线特征表",
           "t2_ae": "Table 2 TEAE 按 SOC 汇总表",
           "t3_shift": "Table 3 实验室移位表（ALT）",
           "qc_adsl": "ADSL 质量检查记录",
           "qc_adae": "ADAE 质量检查记录"}
    for t in p.tasks:
        lines.append(f"| {t.id} | {t.tool or '（编排节点）'} | "
                     f"{', '.join(t.depends_on) or '—'} | "
                     f"{art.get(t.id, '—')} |")
    lines += ["", "> 本清单由任务图自动生成；未完成项必须显式列出。", ""]
    return "\n".join(lines)


def _h(title: str) -> None:
    print("\n" + "=" * 74)
    print(title)
    print("=" * 74)


def _table(plan: planmod.Plan) -> None:
    print(f"{'任务':16s} {'工具':16s} {'状态':9s} {'尝试':>4s} {'耗时':>7s}  说明")
    for t in plan.tasks:
        note = t.title
        if t.error:
            note = f"✗ {(t.error_type or '')}: {str(t.error)[:44]}"
        print(f"{t.id:16s} {t.tool or '（编排）':16s} {t.status:9s} "
              f"{t.attempts:>4d} {t.elapsed:>6.2f}s  {note}")


def make_progress_printer() -> "callable":
    """任务完成回调 —— 生产里这里就是"实时进度"的来源。

    注意它是**在任务完成的当下**被调用的（可能来自不同线程），
    所以回调里只做打印/落日志这类轻量且线程安全的事，
    不要在回调里再去调工具。
    """
    marks = {"done": "✓", "failed": "✗", "skipped": "－", "waiting": "⏸"}

    def _on_task(t: planmod.Task) -> None:
        mark = marks.get(t.status, "?")
        extra = f"  {t.error[:50]}" if t.error and t.status != "done" else ""
        print(f"    [{mark}] {t.id:16s} {t.elapsed:>5.2f}s "
              f"(第 {t.attempts} 次){extra}")

    return _on_task


# ==========================================================================
# 场景 a · 需求 → DAG
# ==========================================================================
def scenario_a() -> None:
    _h("场景 a · 需求 → DAG：先校验，再执行")

    p = build_plan()
    print(f"目标：{p.goal}")
    print(f"任务数：{len(p.tasks)}\n")

    print("【分层结果】（同层可并行，层间有依赖）")
    print(planmod.render_plan(p.tasks))

    print("\n【拓扑序】（Kahn 算法；这是“该按什么顺序跑”的权威答案）")
    print("  " + " → ".join(t.id for t in planmod.topological_order(p.tasks)))

    print(f"\n【静态校验】{'✓ 通过' if not p.validate() else p.validate()}")

    # ---- 故意造三种坏图，看校验能不能在**执行前**抓住 ----
    print("\n--- 故意造坏图，验证“执行前就报错” ---")

    bad1 = planmod.Plan(goal="依赖不存在的任务")
    bad1.add(planmod.Task(id="a", title="A", tool="qc_domain",
                          args={"dataset": "adsl"}, depends_on=("ghost",)))
    print(f"① 依赖不存在的任务 → {bad1.validate()}")

    bad2 = planmod.Plan(goal="循环依赖")
    bad2.add(planmod.Task(id="t3", title="表3", tool="ae_table",
                          args={"level": "soc"}, depends_on=("t2",)))
    bad2.add(planmod.Task(id="t2", title="表2", tool="ae_table",
                          args={"level": "pt"}, depends_on=("t1",)))
    bad2.add(planmod.Task(id="t1", title="表1", tool="demo_table",
                          args={"dataset": "adsl"}, depends_on=("t3",)))
    print(f"② 循环依赖 → {bad2.validate()}")

    bad3 = planmod.Plan(goal="id 重复")
    for _ in range(2):
        bad3.add(planmod.Task(id="dup", title="重复", tool="qc_domain",
                              args={"dataset": "adsl"}))
    print(f"③ 重复 id → {bad3.validate()}")

    print("""
为什么必须在执行前校验？
  循环依赖在 TLF 场景里**藏在业务逻辑里**，写代码时看不出来：
    · 表 3 需要表 2 算出的一个分母；
    · 表 2 的分母又需要表 3 里的某个汇总数。
  顺序脚本跑到一半才发现"要往回改上一张表"，只能整批重跑。
  拓扑排序会在**第一秒**就把它抛出来，并告诉你涉及哪几个任务。

  ⚠️ 循环依赖的错误信息要写清"涉及哪些节点 + 依赖关系"，
     否则你会盯着 11 个任务发懵。看上面 ② 的输出 —— 它把三个任务
     和它们的依赖全列出来了，这就是"可修复的错误信息"。""")

    print("\n【dry-run：只校验不执行】（上线前先跑一遍，确认图没问题）")
    planmod.execute_plan(p, build_registry(),
                         ppolicy=planmod.PlanPolicy(dry_run=True))
    print(f"  {p.summary()}")
    print("  → dry-run 让每个任务返回 skipped 而不真的执行，"
          "用来在**不产生任何副作用**的前提下验证任务图与参数。")


# ==========================================================================
# 场景 b · 并行调度
# ==========================================================================
def scenario_b(force_serial: bool) -> None:
    _h("场景 b · 并行调度：同层并行、层间串行")

    registry = build_registry()
    # 只为观察调度耗时：临时关掉写确认闸门（写操作的闸门见场景 e）
    policy = core.Policy(confirm_side_effects=False, allowed_datasets=ALLOWED)
    results = {}
    for label, workers in (("单线程 max_workers=1", 1),
                           ("4 线程 max_workers=4", 4)):
        p = build_plan()
        print(f"\n【{label}】实时进度（任务完成回调）：")
        t0 = time.perf_counter()
        planmod.execute_plan(p, registry, policy,
                             ppolicy=planmod.PlanPolicy(max_workers=workers,
                                                        max_retries=0),
                             on_task=make_progress_printer())
        results[label] = time.perf_counter() - t0
        print(f"  总耗时 {results[label]:.2f}s")
        for i, layer in enumerate(planmod.levels(p.tasks)):
            spent = sum(t.elapsed for t in layer)
            print(f"   L{i}: {len(layer)} 个任务，串行累计 {spent:.2f}s "
                  f"→ {', '.join(t.id for t in layer)}")

    s, pr = results["单线程 max_workers=1"], results["4 线程 max_workers=4"]
    print(f"\n加速比：{s / pr:.2f}×（{s:.2f}s → {pr:.2f}s）")

    print(f"""
为什么不是"越多线程越快"？看各层的性质：

  · **L0 是 I/O 型**（fetch_snapshot 里有 {_IO_WAIT:.2f}s 的等待，
    模拟读 EDC / 中心实验室接口）→ 4 个任务并行时，
    墙上时间 ≈ max(单个) 而不是 sum(全部) → **加速比接近 4×**。
  · **L1 是 CPU 型**（出表、跑 QC 都在本地算）→ 每个只有几十毫秒，
    线程池的调度开销几乎抵消收益，甚至更慢。

所以第 19.5 节的结论是：

  · 线程池适合 **I/O 等待**（读文件、调接口、等数据库）——
    临床流水线里 90% 的时间花在这里；
  · 真正吃 CPU 的任务（大数据的重算），要么用进程池，
    要么**先解决算法问题**（用向量化把 10 分钟压到 10 秒，比加 8 个核更值）；
  · **别把 DataFrame 在线程间传来传去** —— 传路径/文件名，让每个任务自己读。

  上面这个 `fetch_snapshot` 用 `time.sleep` 模拟等待，是**故意的**：
  它让"并行收益来自 I/O 而不是 CPU"这件事变得可测量。""")


# ==========================================================================
# 场景 c · 失败与重试
# ==========================================================================
def scenario_c() -> None:
    _h("场景 c · 失败、重试与传播：错误要按类型处理")

    reg = build_registry()
    _fail_counter.clear()
    p = planmod.Plan(goal="演示重试、可选任务与失败传播")

    # 1) transient 错误 → 重试后成功
    p.add(planmod.Task(id="flaky", title="不稳定的接口（前 2 次超时）",
                       tool="flaky_tool",
                       args={"key": "case10", "fail_times": 2}))
    # 2) 永远超时的**必选**任务 → 重试用尽后失败，阻塞下游
    p.add(planmod.Task(id="broken_required", title="必选的失败任务",
                       tool="always_timeout",
                       args={"reason": "读不到中央实验室数据"}))
    p.add(planmod.Task(id="child_of_required", title="它的下游（应被跳过）",
                       tool="qc_domain", args={"dataset": "adsl"},
                       depends_on=("broken_required",)))
    # 3) 永远超时的**可选**任务 → 失败但**不阻塞下游**
    #    真实例子：查 CDISC 受控术语版本失败，不该卡住出表
    p.add(planmod.Task(id="broken_optional", title="可选的失败任务（外部 CT 服务）",
                       tool="always_timeout",
                       args={"reason": "CT 服务维护中，用本地版本兜底"},
                       optional=True))                     # ★ optional
    p.add(planmod.Task(id="child_of_optional", title="它的下游（照常执行）",
                       tool="qc_domain", args={"dataset": "dm"},
                       depends_on=("broken_optional",)))

    pp = planmod.PlanPolicy(max_retries=2, backoff_base=1.2)
    t0 = time.perf_counter()
    planmod.execute_plan(p, reg, ppolicy=pp)
    print(f"（含退避等待，总耗时 {time.perf_counter() - t0:.2f}s）\n")
    _table(p)

    print("\n【逐条解读】")
    flaky = p.by_id("flaky")
    print(f"① flaky：status={flaky.status}，attempts={flaky.attempts} "
          f"→ transient 错误被**自动重试**，第 3 次成功")
    br = p.by_id("broken_required")
    print(f"② broken_required：status={br.status}，attempts={br.attempts}，"
          f"error_type={br.error_type}")
    print("   → 重试 2 次后仍失败：记 error + error_type，**不抛异常**"
          "（异常会把整批任务的中间结果全丢掉）")
    print(f"③ child_of_required：status={p.by_id('child_of_required').status}，"
          f"error={p.by_id('child_of_required').error}")
    print("   → 依赖失败 → skipped。**注意是 skipped 不是 failed**："
          "它没被执行过，不该被算成“执行失败”，也不该占用重试次数")
    bo = p.by_id("broken_optional")
    co = p.by_id("child_of_optional")
    print(f"④ broken_optional：status={bo.status}（optional=True）")
    print(f"   child_of_optional：status={co.status} "
          f"→ **前置失败但下游照跑**，这就是 optional 的用途")

    print("""
`optional=True` 什么时候用？（在 TLF 流水线里特别常见）

  · 查外部 CT 版本 / 实验室参考范围 —— 取不到就用本地版本兜底，
    **不该卡住整批出表**；
  · 某个"锦上添花"的 QC（如额外的离群值扫描）—— 失败只记一笔，
    不该让交付清单出不来；
  · ⚠️ 反过来：**安全性与有效性人群的核对、主键唯一性检查、
    关键派生变量的核对，绝不能标 optional**。
    判断标准只有一条：**这个任务失败，结论还成立吗？**
    成立 → optional；不成立 → 必选，让它把下游一起挡住。""")

    print("""
错误分类决定了调度行为（与第 20 章的 error_type 同一套词表）：

  transient    → 可重试，指数退避          （网络、5xx、429）
  validation   → 不可重试，**参数错了重试 100 次也没用**
  permission   → 不可重试，且要告警        （401 重试会锁账号）
  internal     → 不可重试，是程序缺陷
  not_found    → 不可重试，检查路径/版本

  ⚠️ 最容易被忽略的是 validation 与 permission：
     如果调度器"一视同仁地重试"，一个拼错的参数会白等 3 轮退避，
     一个过期的 token 会被反复拿去撞墙（很多系统会因此锁账号）。
     重试策略必须**按 error_type 分支**，不能写成"失败就再来一次"。""")


# ==========================================================================
# 场景 d · 部分失败汇总
# ==========================================================================
def scenario_d() -> None:
    _h("场景 d · 部分失败汇总：让“未完成”变得显式")

    reg = build_registry()
    summaries = {}
    for label, stop in (("stop_on_failure=False（默认：能跑的都跑完）", False),
                        ("stop_on_failure=True（一失败就整体中止）", True)):
        _fail_counter.clear()
        p = planmod.Plan(goal=f"演示 {label}")
        p.add(planmod.Task(id="pre", title="前置准备", tool="qc_domain",
                           args={"dataset": "dm", "max_issues": 3}))
        # 与失败任务**同层但互不依赖**
        p.add(planmod.Task(id="boom", title="失败任务",
                           tool="always_timeout",
                           args={"reason": "读不到中央实验室数据"},
                           depends_on=("pre",)))
        p.add(planmod.Task(id="a1", title="正常任务 A1", tool="qc_domain",
                           args={"dataset": "adsl", "max_issues": 3},
                           depends_on=("pre",)))
        # 在**更靠后的层**，且与 boom 无依赖关系 —— 这才是 stop_on_failure 的判别点
        p.add(planmod.Task(id="a2", title="正常任务 A2（与失败无依赖）",
                           tool="qc_domain",
                           args={"dataset": "adae", "max_issues": 3},
                           depends_on=("a1",)))
        planmod.execute_plan(p, reg, ppolicy=planmod.PlanPolicy(
            max_retries=0, stop_on_failure=stop))
        print(f"\n【{label}】")
        _table(p)
        summaries[label] = p.summary()
        print(f"  summary → {p.summary()}")

    print("""
两种策略的差别只有一个：**要不要继续跑"其他还能跑的"任务**。

  · stop_on_failure=False：a2 与失败任务没有依赖关系，所以照跑 → 完成 3/4；
  · stop_on_failure=True ：一旦出现失败就中止整批 → a2 被跳过 → 完成 2/4。

  什么时候选哪个？
    · 生成一份**交付物**（TLF、核查报告）：用默认（False）。
      能出的表先出，人可以先看已完成的部分。
    · **中间数据被污染**时（比如上游衍生逻辑被证明是错的）：
      用 True。这时候继续跑只会产生一堆基于错误数据的表，
      而且这些表看起来"正常"，反而危险。
    · 判断依据同样是那一句：**继续跑，结论还成立吗？**""")

    print("""
关键在 summary() 结尾那一句：

  "⚠️ 结论仅覆盖已完成的部分，未完成项需单独处理。"

为什么一句话这么重要？
  在临床场景里，一份"看起来完整"的报告如果漏了一张表，
  后果比"报告没出来"严重得多 —— 后者会被立刻发现，
  前者可能一路走到递交。

  所以进度汇总必须满足三点：
    · **数字**：完成 x/y，而不是"基本完成"
    · **点名**：哪几个任务失败/跳过，不是只说"有失败"
    · **免责**：显式写出"结论仅覆盖已完成部分"

  这三条不能靠人记得写，必须由框架在每次执行后自动生成 ——
  因为人一定会忘。""")


# ==========================================================================
# 场景 e · HITL 与断点续跑
# ==========================================================================
def scenario_e() -> None:
    _h("场景 e · 人工确认点与断点续跑")

    plan_file = OUT / "tlf_plan.json"
    reg = build_registry()
    manifest = TLF_DIR / "tlf_manifest.md"
    if manifest.exists():
        manifest.unlink()                      # 从"文件不存在"开始，结果才可断言

    print("\n第一步：跑完整张图，但**在写交付物前暂停**（hitl_before）\n")
    p = build_plan()
    pp = planmod.PlanPolicy(max_workers=4, max_retries=1,
                            hitl_before=("write_tlf",))     # ★ 人工确认点
    print("实时进度：")
    planmod.execute_plan(p, reg, ppolicy=pp, on_task=make_progress_printer())
    print()
    _table(p)
    print(f"\n  summary → {p.summary()}")

    deliver = p.by_id("deliver")
    print(f"\n  写操作任务状态：{deliver.status}   ← 是 waiting，不是 failed")
    print(f"  原因：{deliver.error}")
    print(f"  文件是否被写出：{manifest.exists()}")

    p.save(plan_file)
    print(f"\n第二步：把计划（含每个任务的状态与结果）存盘 → {plan_file.name}")
    print(f"  文件大小 {plan_file.stat().st_size} 字节")

    # ---- 人工确认后：加载计划，只跑未完成的 ----
    print("\n第三步：人工确认通过后，重新加载计划并**只跑未完成的**\n")
    # ---- 人工确认后：加载计划，只跑未完成的 ----
    print("\n第三步：人工确认通过后，重新加载计划并**只跑未完成的**\n")
    p2 = planmod.Plan.load(plan_file)
    todo = [t for t in p2.tasks if not t.settled]
    done = [t for t in p2.tasks if t.settled]
    print(f"  计划里的任务：{len(p2.tasks)} 个")
    print(f"  已到终态（从 JSON 读回状态，**不重跑**）：{len(done)} 个 "
          f"→ {[t.id for t in done]}")
    print(f"  仍待执行：{len(todo)} 个 → {[t.id for t in todo]}   "
          f"← 注意只有这一个，其余 10 个的结果都是从盘上读回来的")

    # ★ 放行方式是"逐任务确认"，不是"全局关掉闸门"
    approver, when = "reviewer@example.com", time.strftime("%Y-%m-%d %H:%M:%S")
    p2.meta["approvals"] = {t.id: {"by": approver, "at": when} for t in todo}
    print(f"\n  人工确认记录（会随计划一起存盘，作为审计证据）：")
    print(f"    {approver} 于 {when} 确认放行 {[t.id for t in todo]}")

    pp2 = planmod.PlanPolicy(max_workers=4, max_retries=1,
                             hitl_before=(),                  # 撤掉计划层闸门
                             confirmed=tuple(t.id for t in todo))  # 逐任务放行
    t0 = time.perf_counter()
    planmod.execute_plan(p2, reg, ppolicy=pp2)
    print(f"\n  续跑耗时 {time.perf_counter() - t0:.2f}s（重跑整张图要 "
          f"{_IO_WAIT * 3 + 0.2:.1f}s 以上）")
    _table(p2)
    print(f"\n  summary → {p2.summary()}")
    if manifest.exists():
        print(f"\n  交付物：{manifest.relative_to(BASE)}"
              f"（{manifest.stat().st_size} 字节）")
        print("  " + "─" * 60)
        for line in manifest.read_text(encoding="utf-8").splitlines()[:16]:
            print("  " + line)
        print("  " + "─" * 60)
    else:
        print("\n  交付物未生成")

    print("""
这里有两个**独立的**闸门，容易搞混，说清一下：

  ① 计划层：``PlanPolicy.hitl_before=("write_tlf",)``
     "跑到这一步就停下来等人。" —— 它管的是**流程**。
  ② 工具层：``ToolSpec.side_effect="write"`` + ``Policy.confirm_side_effects``
     "这个工具会改外部世界，默认不许执行。" —— 它管的是**副作用**。

  两道都要过。放行时必须用 ``PlanPolicy.confirmed=("deliver",)``
  **逐任务放行**，而不是把 ``confirm_side_effects`` 全局关掉 ——
  全局关掉等于"确认过一次之后，之后所有写操作都不再需要确认"，
  这正是审批流最容易被绕过的地方。

  ⚠️ 还有一条铁律：**确认必须留痕**。上面把"谁、什么时候、确认了哪个任务"
  写进了 ``plan.meta`` 并随计划落盘。没有这条记录，
  事后没人能证明"这份交付物是经过人工确认的"。""")


# ==========================================================================
def main() -> None:

    print("""
断点续跑为什么能成立？因为**计划本身就是可序列化的数据**：

    Plan(tasks=[Task(id, tool, args, depends_on, status, result, attempts), ...])

  · status / result / attempts 都在任务对象上，跟着 JSON 一起落盘；
  · 恢复时"只跑未完成的"，已完成的**不重跑**（省时间，也避免重复副作用）；
  · 但要注意一个坑：**恢复后重跑的任务必须幂等**。
    如果 write_tlf 已经写成功、只是状态没存下来，重跑会覆盖一遍 ——
    对"整份覆盖写"的文件没问题，对"追加"或"POST 创建"就危险了。
    这就是第 23 章里幂等键要解决的问题。

  生产里更常见的形态是：计划存进数据库 + 定时任务扫"未完成"的计划。
  这里用 JSON 文件，是为了让你看清**最核心的那部分是什么**。

【顺带说清一个限制：任务之间怎么传数据？】
  你可能已经注意到：``assemble`` 是"编排节点"，它**不会把上游结果传给**
  ``deliver`` —— 因为 ``Task.args`` 是**静态的**（这是刻意的：
  计划要是纯数据，才能存盘、才能发给同事审阅、才能断点续跑）。

  真实项目的两种做法：

  ① **中间产物落盘 + 下游按路径读**（临床里最推荐）
     每个任务把结果写成 ``outputs/tlf/<task_id>.json``，
     下游任务的参数里写 ``{"source": "outputs/tlf/t1_demo.json"}``。
     · 好处：每一步都能单独重跑、单独核对；中间产物本身就是审计证据；
       失败重试不必重算上游（不用在内存里传 DataFrame）。
     · 代价：多一次落盘 I/O —— 相比"重算一遍"，这点成本可以忽略。

  ② **参数模板**（框架层注入）
     允许 ``args={"dataset": "$t1_demo.result.人群"}``，执行前解析成实际值。
     · 好处：写起来短。
     · 代价：引入**隐式依赖** —— 依赖关系藏在字符串里，
       拓扑排序看不见它，计划也不再是纯数据。要谨慎。

  本案例用"编排节点 + 静态参数"是为了把注意力留在调度本身；
  你自己项目里请走 ①。""")


# ==========================================================================
def main() -> None:
    ap = argparse.ArgumentParser(
        description="案例 10 · TLF 生成流水线（任务规划与并行调度）",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", default="all",
                    choices=["all", "a", "b", "c", "d", "e"])
    ap.add_argument("--serial", action="store_true",
                    help="场景 b 额外打印单线程结果（默认已包含）")
    args = ap.parse_args()

    if args.only in ("all", "a"):
        scenario_a()
    if args.only in ("all", "b"):
        scenario_b(args.serial)
    if args.only in ("all", "c"):
        scenario_c()
    if args.only in ("all", "d"):
        scenario_d()
    if args.only in ("all", "e"):
        scenario_e()

    print("\n" + "=" * 74)
    print("案例 10 结束。下一步：")
    print("  · 案例 11 —— 双编程：两个 Agent 独立实现同一张表，再机器比对差异")
    print("  · 案例 12 —— 把这张图做成服务：接口、指标、日志、评估集")
    print("=" * 74)


if __name__ == "__main__":
    main()
