#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
案例 11 · 双编程 Agent 对（多 Agent 协作的正确用法）
====================================================

**目标**：用两个**互不通信**的独立实现去验证同一份衍生逻辑，
并让差异分级、根因、受影响记录全部自动化 —— 也就是把
临床统计里的"双编程（double programming）"变成一个可复跑的流程。

**覆盖章节**：第 22 章（多 Agent 协作）、第 12/13 章（ADaM 衍生与 TFL）

先说一个反直觉的结论
--------------------
多 Agent 的价值**不在数量，而在独立性**。

    两个互相看得见对方代码的 Agent，得到"完全一致"的结论 —— 这个结论一文不值。
    两个完全独立的 Agent，得到 3 处差异 —— 这 3 处才是真正的验证产出。

所以本案例里最重要的代码不是"怎么让两个 Agent 协作"，
而是 :class:`IndependenceGuard` 那 40 行：**它把"独立性"变成一条可以断言的规则**。

五个场景
--------
a) **两个独立实现** —— 同一份 AGEGR1 派生 + AE 汇总，写法完全不同
b) **机器比对与差异分级** —— critical / major / minor / explainable
c) **迭代收敛** —— 修一轮、再比对一轮，直到只剩口径差异
d) **独立性被破坏会怎样** —— 零差异反而不可信（本案例的关键一击）
e) **另两种拓扑** —— Supervisor（契约化分派）与 Pipeline（阶段契约检查）

运行
----
    python cases/case11_双编程Agent对.py
    python cases/case11_双编程Agent对.py --only d

离线可跑：两个实现都是确定性代码（真实数据处理），比对器也不是 LLM。
差异出在**口径**上，不是在幻觉上 —— 这正是双编程要抓的东西。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from clinic import agent_role as role                            # noqa: E402

OUT = BASE / "outputs"
PAIR_DIR = OUT / "pair"
ADSL = BASE / "data" / "samples" / "adsl.csv"
AE = BASE / "data" / "samples" / "ae.csv"

# ---------------------------------------------------------------------------
# 独立性审计要登记的输入清单。
# ★ 这些不是"写好看"用的：守卫只会检查它被告知的东西，
#   漏登记会让审计结论从"已核实"退化成"没查"（coverage=0 时报告会明确标出）。
# ---------------------------------------------------------------------------
SHARED_INPUTS = [str(ADSL), str(AE)]

# 每一对实现**如实**登记各自读过的来源（守卫只检查它被告知的东西）
READS_AGEGR1_A = [str(ADSL)]                  # AGEGR1 只用到 ADSL
READS_AGEGR1_B = [str(ADSL)]
READS_AE_A = [str(ADSL), str(AE)]             # AE 汇总要先从 ADSL 取 TEAE 人群
READS_AE_B = [str(ADSL), str(AE)]


def _read(p: Path) -> pd.DataFrame:
    return pd.read_csv(p, dtype=str, low_memory=False)


# ==========================================================================
# 场景 a 的两个实现：同一目标，两种写法，两套口径
# ==========================================================================
# ★ 注意看：A 与 B 的**写法故意完全不同**
#   （A 用 pd.cut + groupby，B 用逐行 apply），这不是为了好看——
#   如果两份实现连结构都一样，那它们的错误也会一样，"独立"就失去意义了。

AGE_LABELS = ["<65", "65-80", ">80"]


def impl_a_age_groups() -> dict:
    """实现 A：AGEGR1 = <65 / 65-80 / >80（区间左闭右开）。"""
    adsl = _read(ADSL)
    age = pd.to_numeric(adsl["AGE"], errors="coerce")
    grp = pd.cut(age, [-np.inf, 65, 81, np.inf], labels=AGE_LABELS,
                 right=False)                     # ← 65 落在 "65-80"
    out = grp.value_counts().reindex(AGE_LABELS).fillna(0).astype(int)
    return {"实现": "A：左闭右开（65 属 65-80）",
            "分组人数": {k: int(v) for k, v in out.items()},
            "分组明细": {lab: sorted(adsl.loc[grp == lab, "USUBJID"].tolist())
                         for lab in AGE_LABELS}}


def impl_b_age_groups() -> dict:
    """实现 B：逐行判断（等价于区间**左开右闭**）。

    ``age <= 65`` 这类写法在真实报告里非常常见 ——
    它把 AGE=65 的受试者放进了 "<65"，与 SAP 的边界定义相反。
    这就是双编程最该抓的一类问题：**不是崩溃，而是悄悄错 4 个人**。
    """
    adsl = _read(ADSL)
    buckets = {k: [] for k in AGE_LABELS}
    for _, row in adsl.iterrows():
        try:
            age = float(row["AGE"])
        except (TypeError, ValueError):
            continue
        if age <= 65:
            buckets["<65"].append(row["USUBJID"])
        elif age <= 80:
            buckets["65-80"].append(row["USUBJID"])
        else:
            buckets[">80"].append(row["USUBJID"])
    return {"实现": "B：左开右闭（65 属 <65）",
            "分组人数": {k: len(v) for k, v in buckets.items()},
            "分组明细": {k: sorted(v) for k, v in buckets.items()}}


def _ae_teae(df: pd.DataFrame, adsl: pd.DataFrame) -> pd.DataFrame:
    """取出 TEAE 记录：AESTDTC ≥ 首次给药日（TRTSDT）。"""
    m = df.merge(adsl[["USUBJID", "TRTSDT", "TRT01P"]], on="USUBJID", how="left")
    ok = m["AESTDTC"].astype(str) >= m["TRTSDT"].astype(str)
    return m[ok]


def impl_a_ae_summary() -> dict:
    """实现 A：分子 = **不重复受试者数**（受试者层级，TFL 的标准口径）。"""
    adsl, ae = _read(ADSL), _read(AE)
    tea = _ae_teae(ae, adsl)
    per_soc = (tea.groupby("AESOC")["USUBJID"].nunique()
               .sort_values(ascending=False))
    return {"实现": "A：分子 = 不重复受试者数",
            "受试者总数": int(tea["USUBJID"].nunique()),
            "记录总数": int(len(tea)),
            "SOC受试者数": {k: int(v) for k, v in per_soc.items()},
            "顺序": list(per_soc.index)}


def impl_b_ae_summary() -> dict:
    """实现 B：分子 = **记录数**（事件层级）。

    ``groupby().size()`` 与 ``nunique()`` 只差一个词，
    在 AE 表上就变成"事件数"和"受试者数"的差别 ——
    这是临床统计最经典的错误之一（第 13 章专门提过）。
    """
    adsl, ae = _read(ADSL), _read(AE)
    tea = _ae_teae(ae, adsl)
    per_soc = tea.groupby("AESOC").size().sort_values(ascending=False)
    return {"实现": "B：分子 = 记录（事件）数",
            "受试者总数": int(tea["USUBJID"].nunique()),
            "记录总数": int(len(tea)),
            "SOC受试者数": {k: int(v) for k, v in per_soc.items()},
            "顺序": sorted(per_soc.index)}          # ← 另外：按字母序排


# ==========================================================================
# 比对器：确定性代码，不是 LLM
# ==========================================================================
def compare_age_groups(a: dict, b: dict) -> list[role.Discrepancy]:
    """逐分组比对人数；受影响记录 = 落在不同分组的 USUBJID。"""
    out: list[role.Discrepancy] = []
    for lab in AGE_LABELS:
        na, nb = a["分组人数"][lab], b["分组人数"][lab]
        if na != nb:
            sa, sb = set(a["分组明细"][lab]), set(b["分组明细"][lab])
            affected = sorted(sa ^ sb)          # 对称差：进/出该组的人
            out.append(role.Discrepancy(
                key=f"AGEGR1[{lab}]", a=na, b=nb, affected=tuple(affected)))
    return out


def compare_ae_summary(a: dict, b: dict) -> list[role.Discrepancy]:
    """比对每个 SOC 的分子，以及排序口径。"""
    out: list[role.Discrepancy] = []
    total_a = sum(a["SOC受试者数"].values())
    for soc, na in a["SOC受试者数"].items():
        nb = b["SOC受试者数"].get(soc, 0)
        if na != nb:
            out.append(role.Discrepancy(
                key=f"AESOC[{soc[:34]}]", a=na, b=nb,
                root_cause="分子口径不同：受试者数（去重）vs 事件数（记录数）"))
    # 排序口径：数字一致，只是呈现顺序不同 → 属于"可解释差异"
    if a["顺序"] != b["顺序"]:
        out.append(role.Discrepancy(
            key="SOC 呈现顺序", a="按受试者数降序",
            b="按字母序", level="explainable"))
    return out


# 白名单：两种口径都成立、只需记录进 ADRG 的差异
EXPLAINABLE = {
    "SOC 呈现顺序": "两种排序都常见（频数降序 / 字母序），不影响数字；"
                    "本项目采用频数降序，记录进 ADRG。",
}


def classify_all(disps: list[role.Discrepancy],
                 denom: int = 254) -> list[role.Discrepancy]:
    """给每条差异定级。**定级靠影响面，不靠感觉。**"""
    for d in disps:
        ratio = None
        if isinstance(d.a, (int, float)) and isinstance(d.b, (int, float)) \
                and isinstance(d.a, int):
            base = max(1, int(d.a))
            ratio = abs(int(d.a) - int(d.b)) / base
            if d.affected:
                ratio = max(ratio, len(d.affected) / denom)
        role.classify_discrepancy(d, EXPLAINABLE, ratio)
    # critical 优先展示
    order = {"critical": 0, "major": 1, "minor": 2, "explainable": 3}
    disps.sort(key=lambda x: order.get(x.level, 9))
    return disps


def _h(title: str) -> None:
    print("\n" + "=" * 74)
    print(title)
    print("=" * 74)


def _show(report: role.PairReport) -> None:
    for d in report.discrepancies:
        print("   " + d.render().replace("\n", "\n   "))
    print(f"   → 结论：{report.verdict}")
    print(f"   → 独立性：{report.independence_label}"
          f"（审计覆盖 {report.coverage} 个来源）")


# ==========================================================================
# 场景 a · 两个独立实现
# ==========================================================================
def scenario_a() -> tuple[dict, dict, dict, dict]:
    _h("场景 a · 两个独立实现：同一目标，两种写法")

    a1, b1 = impl_a_age_groups(), impl_b_age_groups()
    a2, b2 = impl_a_ae_summary(), impl_b_ae_summary()

    print("【AGEGR1 年龄分组】")
    print(f"  {a1['实现']}\n  {b1['实现']}")
    print(f"\n  {'分组':10s} {'A 人数':>8s} {'B 人数':>8s}   差")
    for lab in AGE_LABELS:
        na, nb = a1["分组人数"][lab], b1["分组人数"][lab]
        print(f"  {lab:10s} {na:>8d} {nb:>8d}   {nb - na:+d}")

    print("\n【AE 按 SOC 汇总（TEAE，前 5 个 SOC）】")
    print(f"  {a2['实现']}\n  {b2['实现']}")
    print(f"\n  A 的 TEAE 受试者总数 {a2['受试者总数']}，记录总数 {a2['记录总数']}")
    print(f"  {'SOC（截断）':38s} {'A 受试者数':>10s} {'B 记录数':>9s}")
    for soc, n in list(a2["SOC受试者数"].items())[:5]:
        print(f"  {soc[:38]:38s} {n:>10d} {b2['SOC受试者数'][soc]:>9d}")

    print("""
两份实现的**结构故意写得不一样**（A 用 pd.cut + groupby，B 用逐行 apply）。

为什么要这样？
  双编程的价值来自"两条独立的思考路径同时犯错"的概率很低。
  如果两个人用同一个模板、照着同一份伪代码写，
  他们的**盲区也会重合** —— 那就不叫双编程，叫"抄一遍"。

  反过来说：本案例里 A 和 B 的差异都是真实的**口径分歧**：
    ① AGE=65 归哪一组（<65 还是 65-80）——边界定义
    ② AE 表的分子是受试者数还是事件数 —— 分子口径
    ③ SOC 的排列顺序（频数降序 / 字母序）—— 呈现口径
  这三个都是临床统计里真实存在的分歧，也都真实造成过递交问题。""")
    return a1, b1, a2, b2


# ==========================================================================
# 场景 b · 机器比对与差异分级
# ==========================================================================
def scenario_b() -> list[role.PairReport]:
    _h("场景 b · 机器比对与差异分级")

    reports: list[role.PairReport] = []

    # ---- 第一对：AGEGR1 ----
    pair = role.DualProgrammingPair(
        name="agegr1",
        impl_a=impl_a_age_groups, impl_b=impl_b_age_groups,
        comparator=lambda a, b: classify_all(compare_age_groups(a, b)),
        summarize_a=lambda r: r["实现"], summarize_b=lambda r: r["实现"],
        shared_inputs=SHARED_INPUTS)
    rep = pair.run(PAIR_DIR, reads_a=READS_AGEGR1_A, reads_b=READS_AGEGR1_B)
    print("【比对 1 · AGEGR1 派生】")
    _show(rep)
    reports.append(rep)

    # ---- 第二对：AE 汇总 ----
    pair2 = role.DualProgrammingPair(
        name="ae_summary",
        impl_a=impl_a_ae_summary, impl_b=impl_b_ae_summary,
        comparator=lambda a, b: classify_all(compare_ae_summary(a, b)),
        summarize_a=lambda r: r["实现"], summarize_b=lambda r: r["实现"],
        shared_inputs=SHARED_INPUTS)
    rep2 = pair2.run(PAIR_DIR, reads_a=READS_AE_A, reads_b=READS_AE_B)
    print("\n【比对 2 · AE 按 SOC 汇总】（只展示前 3 处差异）")
    for d in rep2.discrepancies[:3]:
        print("   " + d.render().replace("\n", "\n   "))
    print(f"   …… 共 {len(rep2.discrepancies)} 处差异")
    print(f"   → 结论：{rep2.verdict}")
    reports.append(rep2)

    print(f"""
差异分四级（第 22.6 节），定级依据是**影响面**，不是"看着严不严重"：

  critical     影响 ≥ 5% 的受试者 → 必须人工审查后才能用
  major        影响 1%~5%        → 建议人工审查
  minor        影响 < 1%         → 可接受，记录即可
  explainable  两种口径都成立     → **不改，记录进 ADRG**

  ⚠️ 最后一级最容易被忽略，也最重要：
     "差异"不等于"有人写错了"。像 SOC 的排列顺序，
     两种口径都能自圆其说 —— 硬要"消灭"它，只会把时间浪费在
     一个没有对错的问题上。正确的动作是**记录选择与理由**。

  而本案例里的 ① 和 ② 不是口径问题，是真错：
     ① AGE=65 的 4 个人被放错组（与 SAP 的边界定义相反）；
     ② AE 表报出的是事件数而不是受试者数 —— 数字会整体偏大，
        而且**不会有任何报错**，表看起来完全正常。
   这就是为什么双编程不能省。""")

    (PAIR_DIR / "case11_report.md").write_text(
        "# 双编程比对报告汇总\n\n" +
        "\n\n---\n\n".join(r.render() for r in reports) + "\n",
        encoding="utf-8")
    print(f"\n两份报告已落盘：{(PAIR_DIR / 'case11_report.md').relative_to(BASE)}"
          f"\n（另有每个实现的中间产物 JSON 与独立性审计 MD，便于事后复核）")
    return reports


# ==========================================================================
# 场景 c · 迭代收敛
# ==========================================================================
def impl_b_ae_fixed() -> dict:
    """修好分子口径后的 B（改 nunique，顺序仍用字母序）。"""
    adsl, ae = _read(ADSL), _read(AE)
    tea = _ae_teae(ae, adsl)
    per_soc = tea.groupby("AESOC")["USUBJID"].nunique().sort_values(ascending=False)
    return {"实现": "B′：分子改为不重复受试者数",
            "受试者总数": int(tea["USUBJID"].nunique()),
            "记录总数": int(len(tea)),
            "SOC受试者数": {k: int(v) for k, v in per_soc.items()},
            "顺序": sorted(per_soc.index)}          # 仍按字母序 → 保留一条可解释差异


def impl_b_age_fixed() -> dict:
    """修好边界后的 B（65 归 65-80）。"""
    adsl = _read(ADSL)
    age = pd.to_numeric(adsl["AGE"], errors="coerce")
    buckets = {k: [] for k in AGE_LABELS}
    for usubjid, a in zip(adsl["USUBJID"], age):
        if pd.isna(a):
            continue
        if a < 65:                                  # ← 改成严格小于
            buckets["<65"].append(usubjid)
        elif a <= 80:
            buckets["65-80"].append(usubjid)
        else:
            buckets[">80"].append(usubjid)
    return {"实现": "B″：边界与 SAP 对齐（65 属 65-80）",
            "分组人数": {k: len(v) for k, v in buckets.items()},
            "分组明细": {k: sorted(v) for k, v in buckets.items()}}


def scenario_c() -> None:
    _h("场景 c · 迭代收敛：每修一轮，重比对一轮")

    rounds = [
        ("第 1 轮（原始 B）", impl_b_age_groups, impl_b_ae_summary),
        ("第 2 轮（B 修正分子口径）", impl_b_age_groups, impl_b_ae_fixed),
        ("第 3 轮（B 再修正边界）", impl_b_age_fixed, impl_b_ae_fixed),
    ]
    for label, age_fn, ae_fn in rounds:
        p1 = role.DualProgrammingPair(
            "agegr1", impl_a_age_groups, age_fn,
            lambda a, b: classify_all(compare_age_groups(a, b)),
            shared_inputs=SHARED_INPUTS)
        p2 = role.DualProgrammingPair(
            "ae_summary", impl_a_ae_summary, ae_fn,
            lambda a, b: classify_all(compare_ae_summary(a, b)),
            shared_inputs=SHARED_INPUTS)
        r1 = p1.run(PAIR_DIR, reads_a=READS_AGEGR1_A, reads_b=READS_AGEGR1_B)
        r2 = p2.run(PAIR_DIR, reads_a=READS_AE_A, reads_b=READS_AE_B)
        lv = [d.level for d in r1.discrepancies + r2.discrepancies]
        print(f"\n【{label}】")
        print(f"   AGEGR1 ：{len(r1.discrepancies)} 处差异 {[d.level for d in r1.discrepancies]}")
        print(f"   AE 汇总：{len(r2.discrepancies)} 处差异 "
              f"{[d.level for d in r2.discrepancies]}")
        print(f"   剩余最高级别：{min(lv, key=lambda x: {'critical': 0, 'major': 1, 'minor': 2, 'explainable': 3}.get(x, 9)) if lv else '无'}")

    print("""
三轮之后只剩一条 explainable（SOC 排列顺序）—— 这就是**收敛**：

    差异 → 定级 → 修 → 重比对 → 差异减少 → …… → 只剩口径差异 → 收工

两个必须写进流程的纪律：

  ① **每修一轮都要重新跑完整比对**。
     修改一个口径常常会带出新的差异（比如改了边界，某个分组的人数又变了）。
     "我改好了"必须由机器重新验证，不能由修改者自己声明 ——
     修改者恰恰是最容易有盲区的人。

  ② **"只剩 explainable"才算完成**。
     如果剩下的差异里有任何一条是 major 以上，"收敛"就没发生。
     这时候的正确动作不是"再看看"，而是**找 SAP 裁决**：
     口径分歧的最终裁判是方案，不是程序员的偏好。

  ⚠️ 现实中的坑：`explainable` 必须写进 ADRG（分析数据说明文件）
     并说明选了哪一个、为什么。否则下一个项目的人会再吵一遍。""")


# ==========================================================================
# 场景 d · 独立性被破坏会怎样
# ==========================================================================
COPIED_NAME = "agegr1_copied"
COPIED_ARTIFACT = PAIR_DIR / f"{COPIED_NAME}_impl_a.json"


def impl_b_copied() -> dict:
    """实现 B'：**读 A 的产物**再原样交回来（"抄"在工程上的真实形态）。

    注意：抄作业很少表现为复制粘贴代码。更常见的是 ——
    B 从共享目录里读到了 A 的中间结果，然后"参考"着往下写。
    所以这里就让 B' 真的去读 A 落盘的 JSON。

    ★ 这个函数会被 :class:`IndependenceGuard` 抓到，不是靠我们手工标记，
      而是因为守卫同时记录了"谁读了什么"和"谁产出了什么"。
    """
    src = COPIED_ARTIFACT
    if src.exists():
        raw = json.loads(src.read_text(encoding="utf-8"))
        people = raw.get("分组明细", {})
        return {"实现": "B'：读取了 A 的产物（独立性已破坏）",
                "分组人数": {k: len(v) for k, v in people.items()},
                "分组明细": people}
    # 拿不到 A 的产物就只能老实自己算 —— 但那时它就不是"抄"了
    return impl_b_age_groups()


def scenario_d() -> None:
    _h("场景 d · 独立性被破坏：零差异反而不可信 ★")

    # ---- 反例：B' 直接读 A 的产物 ----
    reads_b_bad = READS_AGEGR1_B + [str(COPIED_ARTIFACT)]
    pair_bad = role.DualProgrammingPair(
        COPIED_NAME, impl_a_age_groups, impl_b_copied,
        lambda a, b: classify_all(compare_age_groups(a, b)),
        shared_inputs=SHARED_INPUTS)
    rep_bad = pair_bad.run(PAIR_DIR, reads_a=READS_AGEGR1_A, reads_b=reads_b_bad)

    print("【独立性审计 · B' 读了 A 的产物】")
    print(pair_bad.guard.report())
    try:
        pair_bad.guard.assert_independent()
    except role.IndependenceError as e:
        print(f"\n抛出 IndependenceError"
              f"（守卫把「独立性」变成了一条可失败的断言）：\n  {e}")

    print(f"\n【比对结果】差异 {len(rep_bad.discrepancies)} 处")
    print(f"   verdict = {rep_bad.verdict}")
    print(f"   独立性  = {rep_bad.independence_label}")

    # ---- 正例：真正独立的两个实现 ----
    pair_ok = role.DualProgrammingPair(
        "agegr1", impl_a_age_groups, impl_b_age_groups,
        lambda a, b: classify_all(compare_age_groups(a, b)),
        shared_inputs=SHARED_INPUTS)
    rep_ok = pair_ok.run(PAIR_DIR, reads_a=READS_AGEGR1_A, reads_b=READS_AGEGR1_B)
    print(f"\n【对照：真正独立的两个实现】差异 {len(rep_ok.discrepancies)} 处，"
          f"独立性 {rep_ok.independence_label}（覆盖 {rep_ok.coverage} 个来源）")

    # ---- 第三种情况：根本没有登记任何来源 ----
    pair_gap = role.DualProgrammingPair(
        "agegr1_unaudited", impl_a_age_groups, impl_b_age_groups,
        lambda a, b: classify_all(compare_age_groups(a, b)))
    rep_gap = pair_gap.run(PAIR_DIR)          # ← 故意不传 reads
    print(f"【反面教训：审计没登记任何来源】差异 {len(rep_gap.discrepancies)} 处，"
          f"独立性 {rep_gap.independence_label}")
    print("   ↑ 这份报告一个字也不能信：它没查过，只是没记账而已。")

    print(f"""
这三组结果的对比，就是本案例想说清的**唯一一件事**：

  ┌────────────────────────────┬────────┬────────────────────┬──────────────────┐
  │ 情形                       │ 差异数 │ 独立性             │ 结论的可信度     │
  ├────────────────────────────┼────────┼────────────────────┼──────────────────┤
  │ B' 读 A 的产物             │  0 处  │ ✗ 已破坏           │ **零价值**       │
  │ 真正独立的 A、B            │  2 处  │ ✓ 通过             │ 找到 2 个真问题  │
  │ 审计没登记任何来源         │  2 处  │ 未审计（覆盖 0）   │ 数字对，结论存疑 │
  └────────────────────────────┴────────┴────────────────────┴──────────────────┘

  ① "零差异"只有在**独立性成立**时才是一个好结果。
     独立性不成立时，零差异只说明"同一份错误被复制了一遍"。
     —— 这正是本案例把 :meth:`PairReport.verdict` 写成"独立性优先于差异数量"
     的原因：独立性一旦破坏，报告的第一行就是"不可采信"，不管差异有几处。

  ② **审计有覆盖范围，覆盖率 0 的"通过"等于没查。**
     守卫只检查你告知它的路径；漏登记不会报错，只会让结论静默失效。
     所以 :meth:`PairReport.independence_label` 有三种状态而不是两种：
     通过 / 已破坏 / 未审计。

  所以真实系统里，独立性**必须靠架构强制**，不能靠自觉：
    · 两个 Agent 的对话上下文物理隔离（不共用 messages）；
    · A 的产物对 B 不可读（不同工作目录 / 不同命名空间）；
    · 比对由**第三方**（确定性代码）执行，而不是让 A 或 B 自己比；
    · 独立性审计要落盘（就是上面那份 independence.md），并且**覆盖率要够**。

  ⚠️ 最危险的破坏方式不是"抄代码"，而是**不经意地共享上下文**：
     在同一个对话里先让 A 做，再让 B 做 —— B 能看到 A 的全部输出。
     这不是"双编程"，是"同一个 Agent 说了两遍"。

多 Agent 什么时候值得用？（第 22.2 节的判据）
  ① 任务存在**可验证的多种正确路径**（衍生口径、检验方法）；
  ② 单次错误的**代价很高**（递交物、关键 TFL）；
  ③ 能**机械比对**结果（数字、表格、数据集）。
  三条同时成立 → 用。缺一条 → 用单 Agent + 单元测试更划算。
  本案例三条都成立，所以值得。

最后一个判断题：**先证明单 Agent 不够用，再上多 Agent。**
  多 Agent 的成本是通信开销、上下文开销、以及"冲突处理"的复杂度。
  如果你的任务可以用一个脚本 + 一个单元测试解决 —— 那就那么做。""")

    (PAIR_DIR / "case11_independence_contrast.md").write_text(
        "# 独立性对照（三种结果）\n\n"
        "| 情形 | 差异数 | 独立性 | 结论 |\n"
        "| --- | --- | --- | --- |\n"
        f"| B' 读了 A 的产物 | {len(rep_bad.discrepancies)} | "
        f"{rep_bad.independence_label} | 不可采信 |\n"
        f"| 真正独立 | {len(rep_ok.discrepancies)} | "
        f"{rep_ok.independence_label}（覆盖 {rep_ok.coverage}） | 可采信 |\n"
        f"| 未登记来源 | {len(rep_gap.discrepancies)} | "
        f"{rep_gap.independence_label} | 存疑 |\n",
        encoding="utf-8")


# ==========================================================================
# 场景 e · 另两种拓扑
# ==========================================================================
def scenario_e() -> None:
    _h("场景 e · 另两种拓扑：Supervisor 与 Pipeline")

    # ---------------- Supervisor：协调者 + 契约化角色 ----------------
    print("① Supervisor（监督者）—— 每个角色是一份**契约**，不是一句人设\n")
    cards = [
        role.RoleCard(
            name="data-qc",
            goal="确认源数据的完整性与可用性",
            system_prompt="检查各域记录数与关键变量的缺失情况。",
            tools=("list_datasets", "describe_dataset", "run_qc_checks"),
            inputs=(),
            outputs=("记录数", "高优先级问题数"),
            forbidden=("不要做任何衍生计算", "不要修改数据")),
        role.RoleCard(
            name="table-dev",
            goal="按 SAP 生成 TFL 数据",
            system_prompt="严格按 SAP 的分子分母口径生成表。",
            tools=("demo_table", "ae_table", "shift_table"),
            inputs=("记录数",),
            outputs=("表1", "表2"),
            forbidden=("不要自行改变分母口径", "不要修改原始数据")),
        role.RoleCard(
            name="reviewer",
            goal="独立复核并有权提出结构化质疑",
            system_prompt="只做复核，不做实现。发现不一致必须给 expected/actual。",
            tools=("run_qc_checks", "compare_datasets"),
            inputs=("表1", "表2"),
            outputs=("复核结论",),
            forbidden=("不要直接改实现", "不要在无证据时下结论")),
    ]
    for c in cards:
        print(f"   · {c.scope()}")
    print(f"   角色 {cards[2].name} 的禁用清单：{list(cards[2].forbidden)}")

    sup = role.Supervisor(cards)
    sup.dispatch(["data-qc"], {"stage": "kickoff", "study": "CDISCPILOT01"})
    # ---- 契约校验：少产出就报错，不让"半成品"往下流 ----
    try:
        sup.collect("data-qc", {"记录数": 306})          # 少了"高优先级问题数"
    except ValueError as e:
        print(f"\n   契约校验拦住了：{e}")
    sup.collect("data-qc", {"记录数": 306, "高优先级问题数": 0},
                evidence=("data/samples/", "outputs/audit/case11.jsonl"))
    sup.dispatch(["table-dev"], {"stage": "tables", "sap": "v2.1"})
    sup.collect("table-dev", {"表1": "ok", "表2": "ok"})
    # ---- 结构化质疑：必须带 expected / actual ----
    msg = sup.challenge(target="表2.TEAE 受试者数", reason="分子口径与 SAP 6.2 节不符",
                        expected="不重复受试者数", actual="记录数",
                        affected=("01-701-1015", "01-701-1023"),
                        severity="critical")
    print(f"\n   质疑消息：{msg.render()}")
    print(f"\n   消息总数 {len(sup.messages)} 条；"
          f"已汇总产出键 {sorted(sup.results)}")

    print("""
   为什么要强制"结构化 payload + 契约"？
     · ``collect()`` 会检查角色是否**按 card.outputs 交齐了产出**。
       少交一个键就报错 —— 而不是让协调者拿到半成品继续往下做。
     · ``Challenge`` 必须带 expected / actual / affected。
       "我觉得不对" 无法核查；"我期望 X、实际是 Y、影响这 2 条记录" 可以。
     · ``forbidden`` 的作用是防止角色越权：
       复核者不能改实现（否则它就不再是独立复核），
       实现者不能改分母口径（否则口径分歧会被悄悄消灭）。

   ⚠️ Supervisor 拓扑的两个真实风险：
     ① 协调者成为瓶颈/单点 —— 它的上下文会累积所有子任务的返回；
     ② 协调者"误解"子任务返回 —— 所以 payload 必须结构化，
        且子任务必须给出 refs（产物路径），让协调者可以核对原始产物。""")

    # ---------------- Pipeline：阶段契约检查 ----------------
    print("\n② Pipeline（流水线）—— 每一环都要有输入契约检查\n")
    stages = ["读数据", "衍生", "出表", "QC"]
    handlers = {
        "读数据": lambda _: {"adsl": 254, "adae": 1191},
        "衍生": lambda d: {**d, "ADSL": {"SAFFL": 254, "EFFFL": 240}},
        "出表": lambda d: {**d, "表1": "Table 1（254 行）"},
        "QC": lambda d: {**d, "QC": "通过"},
    }
    validators = {
        "读数据": lambda d: None if d.get("adsl", 0) > 0 else "adsl 记录数为 0",
        # ★ 这个校验故意会失败：衍生结果缺少必需的 AGEGR1
        "衍生": lambda d: None if "AGEGR1" in d.get("ADSL", {})
        else "ADSL 缺少必需变量 AGEGR1（SAP 6.1 节要求）",
    }
    pipe = role.Pipeline(stages)
    try:
        pipe.run({}, handlers, validators)
    except ValueError as e:
        print(f"   流水线在阶段中断：{e}")
    print()
    for line in pipe.report().splitlines():
        print("   " + line)

    print("""
   注意 Pipeline 失败时的行为：**在阶段边界抛出**，而不是让错误数据流到最后一环。

     · 如果"衍生"产出缺 AGEGR1 却继续出表 → 表里会静默少一个分组，
       而这张表看起来完全正常。这就是第 19 章说过的"看起来完整"的危险。
     · 契约检查写在哪一环，就在哪一环拦住 —— **越早拦住，排查成本越低**。

   三种拓扑的选用（第 22.3 节的总结）：

     双编程（对等）  两个人/两个 Agent 做同一件事，靠比对验证 → 本案例主角
     监督者          任务可拆分且**互不重叠**，需要协调与汇总 → 多角色核查
     流水线          任务有**严格先后**，每一步都改变数据结构 → 数据加工链

     ⚠️ 不要为了"用多 Agent"而用多 Agent。
        能用一个脚本 + 一个单元测试解决的，就别上多 Agent ——
        通信开销和冲突处理成本会超过收益。""")


# ==========================================================================
def main() -> None:
    ap = argparse.ArgumentParser(
        description="案例 11 · 双编程 Agent 对（多 Agent 协作的正确用法）",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", default="all",
                    choices=["all", "a", "b", "c", "d", "e"])
    args = ap.parse_args()

    if args.only in ("all", "a"):
        scenario_a()
    if args.only in ("all", "b"):
        scenario_b()
    if args.only in ("all", "c"):
        scenario_c()
    if args.only in ("all", "d"):
        scenario_d()
    if args.only in ("all", "e"):
        scenario_e()

    print("\n" + "=" * 74)
    print("案例 11 结束。下一步：")
    print("  · 案例 12 —— 把 QC Agent 做成服务：接口、指标、日志、评估集")
    print("=" * 74)


if __name__ == "__main__":
    main()
