#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
案例 03 · 不良事件汇总表（按 MedDRA SOC / PT）
================================================

**目标**：生成 CSR 中最核心的安全性表——治疗中出现的不良事件（TEAE）按
系统器官分类（SOC）与首选术语（PT）的受试者数与百分比。

**对应 SAS**：``PROC FREQ`` + ``PROC TRANSPOSE`` + ``PROC REPORT`` 组合，
但本案例用 pandas 的 ``pivot_table`` + 受试者层级去重一次完成。

**覆盖教程章节**：第 09 章（重塑）、第 13 章（TFL 报表）

★ 这是最容易做错的一张表，重点在于三个"隐形陷阱" ★
----------------------------------------------------
1. **分子是受试者数，不是事件数。**
   同一受试者发生 3 次 Nausea，分子只能是 1。
   → 用 ``drop_duplicates(["USUBJID","AEBODSYS","AEDECOD"])`` 做受试者层级去重
2. **分母是该治疗组的分析人群人数**（ADSL 的 SAFFL='Y'），
   而不是 AE 数据集里的记录数。
3. **没发生 AE 的受试者要计入分母（分子为 0）。**
   CDISC 试点数据中 254 人里有 29 人没有任何 AE —— 他们不能被悄悄丢掉。

运行
----
    python cases/case03_不良事件汇总表.py
    python cases/case03_不良事件汇总表.py --all-events   # 不限定 TEAE
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from clinic import io as cio                        # noqa: E402
from clinic import report as rpt                    # noqa: E402
from clinic.derive import to_categorical            # noqa: E402

TRT_LEVELS = ["Placebo", "Xanomeline Low Dose", "Xanomeline High Dose"]

pd.set_option("display.width", 220)
pd.set_option("display.max_colwidth", 60)


def section(t: str) -> None:
    print("\n" + "=" * 86)
    print(f"  {t}")
    print("=" * 86)


def build(data_dir: Path, treatment_only: bool = True):
    """准备数据：人群、分母、受试者层级的 SOC/PT 计数。"""
    adsl = cio.read_any(data_dir / "adsl.csv")
    adae = cio.read_any(data_dir / "adae.csv")

    # ---- Step 1: 分析人群（分母）----
    saf = adsl[adsl["SAFFL"].astype(str).str.strip() == "Y"].copy()
    saf["TRT01P"] = saf["TRT01P"].astype(str).str.strip()
    denom = saf.groupby("TRT01P")["USUBJID"].nunique().to_dict()

    # ---- Step 2: 限定分析事件 ----
    ae = adae.copy()
    if treatment_only and "TRTEMFL" in ae.columns:
        n_before = len(ae)
        ae = ae[ae["TRTEMFL"].astype(str).str.strip() == "Y"]
        print(f"限定治疗中出现的不良事件（TRTEMFL='Y'）：{n_before} → {len(ae)} 条记录")

    # ---- Step 3: 只保留分析人群内的受试者（关键！）----
    n0 = ae["USUBJID"].nunique()
    ae = ae[ae["USUBJID"].isin(set(saf["USUBJID"]))].copy()
    print(f"限定在安全性人群内：{n0} → {ae['USUBJID'].nunique()} 位受试者")

    # ---- Step 3b: 取治疗组变量 ----
    # ★ 注意：ADAE 里通常只有 TRTA（实际治疗），没有 TRT01P（计划治疗）。
    #   CSR 报表惯例使用 **计划治疗 TRT01P**，所以要从 ADSL 带过来。
    #   这既保证了分组口径与 Table 1 一致，也避免了"实际治疗"因
    #   提前退出而与计划治疗不一致带来的口径混乱。
    if "TRT01P" not in ae.columns:
        ae = ae.merge(saf[["USUBJID", "TRT01P"]], on="USUBJID", how="left")
        print("已从 ADSL 引入 TRT01P（ADAE 原生只有 TRTA）")
    ae["TRT01P"] = ae["TRT01P"].astype(str).str.strip()

    # ---- Step 4: ★受试者层级去重★ ----
    # 同一受试者同一 SOC 发生多次 → 只计 1 人
    subj_soc = ae.drop_duplicates(["USUBJID", "TRT01P", "AEBODSYS"])
    # 同一受试者同一 PT 发生多次 → 只计 1 人
    subj_pt = ae.drop_duplicates(["USUBJID", "TRT01P", "AEBODSYS", "AEDECOD"])
    # 有任一 TEAE 的受试者
    subj_any = ae.drop_duplicates(["USUBJID", "TRT01P"])

    n_any = subj_any.groupby("TRT01P")["USUBJID"].nunique().to_dict()
    return saf, denom, subj_soc, subj_pt, n_any, len(ae)


def pivot_counts(df_uniq: pd.DataFrame, keys: list[str],
                 denom: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """按 keys 分组统计各治疗组受试者数，返回 (计数表, n(%) 格式表)。"""
    cnt = (df_uniq.groupby(keys + ["TRT01P"])["USUBJID"]
                  .nunique().rename("N").reset_index())
    wide = cnt.pivot_table(index=keys, columns="TRT01P", values="N",
                           aggfunc="sum", fill_value=0, observed=False)
    # ★ 保证所有治疗组列都存在（即使某组为 0）
    for t in TRT_LEVELS:
        if t not in wide.columns:
            wide[t] = 0
    wide = wide[TRT_LEVELS].reset_index()

    fmt = wide[keys].copy()
    for t in TRT_LEVELS:
        fmt[t] = [rpt.pct_format(int(n), denom.get(t, 0)) for n in wide[t]]
    return wide, fmt


def main() -> None:
    ap = argparse.ArgumentParser(description="AE 按 SOC/PT 汇总表")
    ap.add_argument("--all-events", action="store_true",
                    help="统计全部 AE（默认只统计治疗中出现的 TEAE）")
    args = ap.parse_args()

    data_dir = BASE / "data" / "samples"
    out_dir = BASE / "outputs"
    out_dir.mkdir(exist_ok=True)

    treatment_only = not args.all_events
    label = "治疗中出现的不良事件（TEAE）" if treatment_only else "全部不良事件"

    print("=" * 86)
    print(f"  案例 03 · {label} 汇总表（按 SOC / PT，受试者层级）")
    print("=" * 86)

    saf, denom, subj_soc, subj_pt, n_any, n_records = build(data_dir, treatment_only)

    section("1. 分母核对（这是整张表的基础）")
    print(f"{'治疗组':26s}{'安全性人群人数':>12s}")
    for t in TRT_LEVELS:
        print(f"{t:26s}{denom.get(t, 0):>12d}")
    print(f"{'合计':26s}{sum(denom.values()):>12d}")
    print(f"\nAE 记录数（未去重）：{n_records}")
    print(f"有任一 AE 的受试者：{sum(n_any.values())} / {sum(denom.values())}")

    # ------------------------------------------------------------------
    section("2. 总体行")
    print(f"{'':56s}" + "".join(f"{t[:18]:>22s}" for t in TRT_LEVELS))
    line = f"{'有任一' + ('TEAE' if treatment_only else 'AE') + '的受试者':56s}"
    for t in TRT_LEVELS:
        line += rpt.pct_format(n_any.get(t, 0), denom.get(t, 0)).rjust(22)
    print(line)

    # ------------------------------------------------------------------
    section("3. 按 SOC 汇总")
    soc_w, soc_fmt = pivot_counts(subj_soc, ["AEBODSYS"], denom)
    soc_w["_tot"] = soc_w[TRT_LEVELS].sum(axis=1)
    order = soc_w.sort_values("_tot", ascending=False).index
    soc_fmt = soc_fmt.loc[order].reset_index(drop=True)

    print(f"{'SOC':56s}" + "".join(f"{t[:18]:>22s}" for t in TRT_LEVELS))
    print("-" * 122)
    for _, r in soc_fmt.iterrows():
        print(f"{str(r['AEBODSYS'])[:54]:56s}"
              + "".join(str(r[t]).rjust(22) for t in TRT_LEVELS))

    # ------------------------------------------------------------------
    section("4. 按 SOC → PT 汇总（完整表，前 6 个 SOC）")
    pt_w, pt_fmt = pivot_counts(subj_pt, ["AEBODSYS", "AEDECOD"], denom)
    pt_w["_tot"] = pt_w[TRT_LEVELS].sum(axis=1)

    print(f"{'SOC / PT':56s}" + "".join(f"{t[:18]:>22s}" for t in TRT_LEVELS))
    print("-" * 122)
    for soc_name in soc_fmt["AEBODSYS"].head(6):
        soc_row = soc_fmt[soc_fmt["AEBODSYS"] == soc_name].iloc[0]
        print(f"{str(soc_name)[:54]:56s}"
              + "".join(str(soc_row[t]).rjust(22) for t in TRT_LEVELS))
        pts = pt_w[pt_w["AEBODSYS"] == soc_name].sort_values("_tot", ascending=False)
        for _, p in pts.iterrows():
            n = int(p["_tot"])
            # CSR 惯例：只显示发生频率 >= 一定阈值的 PT（这里用 >=1）
            if n == 0:
                continue
            pct_row = pt_fmt[(pt_fmt["AEBODSYS"] == soc_name)
                             & (pt_fmt["AEDECOD"] == p["AEDECOD"])].iloc[0]
            print(f"{'  ' + str(p['AEDECOD'])[:52]:56s}"
                  + "".join(str(pct_row[t]).rjust(22) for t in TRT_LEVELS))
        print()

    # ------------------------------------------------------------------
    # 输出
    section("5. 输出文件")
    soc_fmt.to_csv(out_dir / "table02_teae_soc.csv", index=False, encoding="utf-8-sig")
    pt_fmt.sort_values("AEBODSYS").to_csv(out_dir / "table02_teae_pt.csv",
                                          index=False, encoding="utf-8-sig")

    # 拼装成一张分层表（SOC 行 + PT 行，符合 CSR 排版惯例）
    combined = []
    for soc_name in soc_fmt["AEBODSYS"]:
        sr = soc_fmt[soc_fmt["AEBODSYS"] == soc_name].iloc[0]
        combined.append({"层级": str(soc_name), "级别": "SOC",
                         **{t: sr[t] for t in TRT_LEVELS}})
        pts = pt_fmt[pt_fmt["AEBODSYS"] == soc_name]
        for _, pr in pts.iterrows():
            cnt = pt_w[(pt_w["AEBODSYS"] == soc_name)
                       & (pt_w["AEDECOD"] == pr["AEDECOD"])]["_tot"].iloc[0]
            if cnt == 0:
                continue
            combined.append({"层级": "  " + str(pr["AEDECOD"]), "级别": "PT",
                             **{t: pr[t] for t in TRT_LEVELS}})
    combined_df = pd.DataFrame(combined)
    combined_df.to_csv(out_dir / "table02_teae_hierarchical.csv",
                       index=False, encoding="utf-8-sig")
    rpt.to_html(combined_df, out_dir / "table02_teae.html",
                title=f"表 2. {label}（按 SOC / PT）",
                subtitle="安全性人群，受试者层级计数",
                footnote=("分子为受试者数（同一受试者同一事件只计一次）。"
                          "分母为该治疗组的安全性人群人数。"
                          "数据来源：CDISC 试点项目 CDISCPILOT01（公开数据）。"),
                label_cols=("层级", "级别"))

    print(f"已输出到 {out_dir}：")
    print("  table02_teae_soc.csv            SOC 层级")
    print("  table02_teae_pt.csv             PT 层级")
    print("  table02_teae_hierarchical.csv   分层合并表（推荐）")
    print("  table02_teae.html               带排版的 HTML")

    # ------------------------------------------------------------------
    section("6. QC 提示（迁移到 Python 时最容易搞错的三处）")
    print("  ① 分子：必须做受试者层级去重。")
    print(f"     未去重的事件数 = {n_records}；去重后的受试者数 = {sum(n_any.values())}")
    print("     如果用事件数当分子，所有百分比都会偏高。")
    print("  ② 分母：必须是 ADSL 的安全性人群人数，不是 AE 记录数。")
    print("  ③ 空组：某治疗组在某 SOC 下无事件时，必须显示 '0' 而不是不显示该行。")


if __name__ == "__main__":
    main()
