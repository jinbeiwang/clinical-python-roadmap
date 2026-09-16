#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
案例 04 · 实验室检查移位表（Shift Table）
==========================================

**目标**：生成"基线 → 基线后"的实验室分级迁移表。
这是每个 CSR 都有的安全性表，用来展示治疗是否导致实验室指标恶化。

**覆盖教程章节**：第 09 章（透视/重塑）、第 13 章（报表）、第 14 章（统计）

移位表最容易搞错的四件事（本案例逐一显式处理）
----------------------------------------------
1. **分级阈值从哪里来？**
   优先用数据集自带的 ``ANRLO`` / ``ANRHI``（参考范围下限/上限）
   和 ``ANRIND`` / ``BNRIND``（分析/基线时的正常性指示符）。
   **不要自己硬编码阈值** —— 那会与 define.xml 的声明不一致。
2. **百分比的分母是什么？**
   行百分比（分母 = 该基线分类的合计）还是列百分比（分母 = 该基线后分类的合计）？
   **两者都有，必须按 SAP 规定**。本案例默认行百分比，并提供参数切换。
3. **用哪一次"基线后"访视？**
   末次访视？END OF TREATMENT？固定访视？
   本案例默认用 **End of Treatment**，并演示如何改用其他访视。
4. **同一受试者同一次访视有多条记录怎么办？**
   必须先定规则（取最后一次 / 取均值 / 取最差）。

运行
----
    python cases/case04_实验室移位表.py
    python cases/case04_实验室移位表.py --param ALB        # 只看白蛋白
    python cases/case04_实验室移位表.py --pct col          # 列百分比
    python cases/case04_实验室移位表.py --visit "Week 24"  # 改用其他访视
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from clinic import io as cio          # noqa: E402
from clinic import report as rpt      # noqa: E402

TRT_LEVELS = ["Placebo", "Xanomeline Low Dose", "Xanomeline High Dose"]

# 正常性指示符的取值（CDISC 惯例）
# N = Normal / L = Low / H = High / A = Abnormal（部分项目会用）
RIND_ORDER = ["L", "N", "H"]
RIND_LABEL = {"L": "低于正常范围", "N": "正常范围", "H": "高于正常范围"}

pd.set_option("display.width", 200)


def section(t: str) -> None:
    print("\n" + "=" * 84)
    print(f"  {t}")
    print("=" * 84)


def load_lab(data_dir: Path) -> pd.DataFrame:
    """载入实验室数据（优先用 ADLBC，退回到裁剪样本）。"""
    for name in ("adlbc.csv", "adlbc_shift.csv"):
        p = data_dir / name
        if p.exists():
            df = cio.read_any(p)
            print(f"已载入 {name}：{len(df)} 行 × {df.shape[1]} 列")
            return df
    raise FileNotFoundError(
        "找不到实验室数据。请先运行：\n"
        "    python scripts/download_data.py --core\n"
        "    python scripts/make_samples.py"
    )


def pick_post_visit(df: pd.DataFrame, visit: str | None) -> pd.DataFrame:
    """选择"基线后"的访视。

    默认规则：每位受试者取 **End of Treatment**；
    若某受试者没有 EOT，则退回到其最后一次有记录的访视（末次观测结转 LOCF 的简化版）。
    """
    d = df.copy()
    d["AVISIT"] = d["AVISIT"].astype(str).str.strip()
    if visit:
        return d[d["AVISIT"] == visit].copy()

    eot = d[d["AVISIT"] == "End of Treatment"].copy()
    missing = set(d["USUBJID"]) - set(eot["USUBJID"])
    if missing:
        # 对这些受试者，取最后一次访视（按 AVISITN 最大）
        d["AVISITN"] = pd.to_numeric(d["AVISITN"], errors="coerce")
        d = d[d["AVISIT"] != "Baseline"]
        last_idx = d.groupby("USUBJID")["AVISITN"].idxmax()
        fallback = d.loc[last_idx]
        fallback = fallback[fallback["USUBJID"].isin(missing)]
        eot = pd.concat([eot, fallback], ignore_index=True)
        print(f"  {len(missing)} 位受试者无 End of Treatment 记录，"
              f"改取其末次可用访视（LOCF 简化版）")
    return eot


def build_shift(lab: pd.DataFrame, param: str, post_visit: str | None) -> pd.DataFrame:
    """为单个实验室参数构造"受试者 × (基线, 基线后)"的配对表。"""
    d = lab[lab["PARAMCD"].astype(str).str.strip() == param].copy()

    # ---- 基线：优先用 ABLFL='Y'；否则用 AVISIT='Baseline' ----
    d["ABLFL"] = d.get("ABLFL", pd.Series(index=d.index, dtype=object)).astype(str).str.strip()
    d["AVISIT"] = d["AVISIT"].astype(str).str.strip()

    bl = d[(d["ABLFL"] == "Y") | (d["AVISIT"] == "Baseline")].copy()
    # 同一受试者多条基线记录 → 取最后一次（规则必须写死）
    bl = bl.sort_values("AVISITN" if "AVISITN" in bl.columns else "AVAL")
    bl = bl.drop_duplicates("USUBJID", keep="last")

    # 基线分类：优先用数据集自带的 BNRIND，否则用 ANRIND
    bl_ind_col = "BNRIND" if "BNRIND" in bl.columns and bl["BNRIND"].notna().any() else "ANRIND"
    bl = bl[["USUBJID", "TRTP", bl_ind_col]].rename(
        columns={bl_ind_col: "IND_BL"}).copy()
    bl["IND_BL"] = bl["IND_BL"].astype(str).str.strip().str[:1].str.upper()

    # ---- 基线后 ----
    post = pick_post_visit(d, post_visit)
    post = post[["USUBJID", "ANRIND"]].copy()
    post["IND_POST"] = post["ANRIND"].astype(str).str.strip().str[:1].str.upper()
    # 同一受试者多条 → 取最差的（L/H 优先于 N）—— 安全性分析的保守做法
    post["_rank"] = post["IND_POST"].map({"N": 0, "L": 1, "H": 1}).fillna(-1)
    post = post.sort_values("_rank").drop_duplicates("USUBJID", keep="last")

    # ---- 配对 ----
    paired = bl.merge(post[["USUBJID", "IND_POST"]], on="USUBJID", how="inner")
    valid = paired["IND_BL"].isin(RIND_ORDER) & paired["IND_POST"].isin(RIND_ORDER)
    n_drop = int((~valid).sum())
    if n_drop:
        print(f"  排除 {n_drop} 条基线或基线后分级缺失的记录")
    return paired[valid].copy()


def main() -> None:
    ap = argparse.ArgumentParser(description="实验室检查移位表")
    ap.add_argument("--param", default=None, help="只分析某个 PARAMCD，如 ALB / ALT / CREAT")
    ap.add_argument("--pct", default="row", choices=["row", "col", "total"],
                    help="百分比口径：row=行百分比（默认）/ col=列百分比 / total=总计")
    ap.add_argument("--visit", default=None, help="指定基线后访视，如 'Week 24'")
    ap.add_argument("--top", type=int, default=6, help="默认分析多少个参数（按记录数）")
    args = ap.parse_args()

    data_dir = BASE / "data" / "samples"
    out_dir = BASE / "outputs"
    out_dir.mkdir(exist_ok=True)

    print("=" * 84)
    print("  案例 04 · 实验室检查移位表")
    print("=" * 84)

    lab = load_lab(data_dir)

    section("1. 确定分析参数")
    counts = lab["PARAMCD"].astype(str).str.strip().value_counts()
    params = [args.param] if args.param else list(counts.head(args.top).index)
    print(f"共 {counts.nunique() if False else len(counts)} 个参数，"
          f"本案例分析 {len(params)} 个：{params}")

    section(f"2. 移位表（百分比口径：{args.pct}）"
            + (f"，访视：{args.visit}" if args.visit else "，访视：End of Treatment"))

    all_tables = {}
    for param in params:
        print(f"\n{'-' * 84}")
        print(f"参数 {param}")
        print(f"{'-' * 84}")

        paired = build_shift(lab, param, args.visit)
        if len(paired) == 0:
            print("  无可配对的记录，跳过")
            continue

        # 按治疗组分别出表
        rows = []
        for trt in TRT_LEVELS:
            sub = paired[paired["TRTP"].astype(str).str.strip() == trt]
            if len(sub) == 0:
                continue
            tbl = rpt.crosstab_shift(sub["IND_BL"], sub["IND_POST"], RIND_ORDER, pct=args.pct)
            for bl_ind in RIND_ORDER:
                if bl_ind not in tbl.index:
                    continue
                row = {"治疗组": trt if bl_ind == RIND_ORDER[0] else "",
                       "基线": f"{bl_ind} ({RIND_LABEL[bl_ind]})"}
                for col in RIND_ORDER + ["合计"]:
                    key = (f"{col} ({RIND_LABEL[col]})" if col in RIND_LABEL
                           else col)
                    row[key] = tbl.loc[bl_ind, col]
                rows.append(row)

        out = pd.DataFrame(rows)
        print(out.to_string(index=False))
        all_tables[param] = out

        # 恶化率汇总（临床最关心的一列）
        print(f"\n  【恶化率】基线正常(N) → 基线后异常(L/H) 的比例：")
        for trt in TRT_LEVELS:
            sub = paired[(paired["TRTP"].astype(str).str.strip() == trt)
                         & (paired["IND_BL"] == "N")]
            if len(sub) == 0:
                continue
            n_worse = int((sub["IND_POST"].isin(["L", "H"])).sum())
            print(f"    {trt:26s} {n_worse:>4} / {len(sub):>4}"
                  f"  ({n_worse / len(sub) * 100:5.1f}%)")

    # ------------------------------------------------------------------
    section("3. 输出文件")
    if all_tables:
        combined = pd.concat(
            [t.assign(参数=p) for p, t in all_tables.items()], ignore_index=True
        )
        combined.to_csv(out_dir / "table03_lab_shift.csv", index=False, encoding="utf-8-sig")
        rpt.to_html(combined, out_dir / "table03_lab_shift.html",
                    title="表 3. 实验室检查分级移位表",
                    subtitle=("基线 vs 治疗结束（End of Treatment）；"
                              f"单元格为 n ({'行' if args.pct == 'row' else '列'}百分比)"),
                    footnote=("分级依据数据集自带的分析正常性指示符（ANRIND / BNRIND）。"
                              "N=正常范围，L=低于正常范围，H=高于正常范围。"
                              "数据来源：CDISC 试点项目 CDISCPILOT01（公开数据）。"),
                    label_cols=("参数", "治疗组", "基线"))
        print(f"已输出：\n  {out_dir / 'table03_lab_shift.csv'}"
              f"\n  {out_dir / 'table03_lab_shift.html'}")

    # ------------------------------------------------------------------
    section("4. QC 提示")
    print("  ① 分母口径：本案例用行百分比（分母=该基线分类合计）。")
    print("     若 SAP 要求列百分比（分母=该基线后分类合计），用 --pct col。")
    print("  ② 基线定义：优先用 ABLFL='Y'；没有该标记时才用 AVISIT='Baseline'。")
    print("  ③ 基线后访视：默认 End of Treatment；无 EOT 的受试者取末次可用访视。")
    print("  ④ 同一受试者多次记录：基线取最后一次；基线后取最差等级（保守做法）。")
    print("     这些规则都必须与 SAP 一致，并在 ADRG 中说明。")


if __name__ == "__main__":
    main()
