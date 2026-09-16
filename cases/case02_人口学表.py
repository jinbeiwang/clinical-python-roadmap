#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
案例 02 · 人口学与基线特征表（Table 1）
==========================================

**目标**：从 ADSL 生成 CSR 的第一张标准表。

**对应 SAS**：``PROC MEANS`` + ``PROC FREQ`` + ``PROC REPORT`` / ``PROC TABULATE``
拼装出来的 Table 1。本案例演示如何用 pandas 完成同样的构造。

**覆盖教程章节**：第 09 章（分组汇总）、第 13 章（TFL 报表）

CSR 关键惯例（本案例全部遵守）
------------------------------
1. 分母 = 各治疗组的**分析人群人数**（不是总人数、不是事件数）
2. 治疗组顺序**必须在代码里显式声明**，绝不能依赖字母序
3. 连续变量输出 ``Mean (SD)``，SD 用 ``ddof=1``（= SAS 的 ``STD``）
4. 分类变量的百分比用**组内分母**
5. 分子为 0 时显示 ``0``（由 SAP 规定，全项目保持一致）

运行
----
    python cases/case02_人口学表.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from clinic import io as cio                      # noqa: E402
from clinic import report as rpt                  # noqa: E402
from clinic.derive import derive_agegr1, to_categorical   # noqa: E402

# ★ 治疗组顺序（来自 SAP；顺序错了整张表就是错的）
TRT_LEVELS = ["Placebo", "Xanomeline Low Dose", "Xanomeline High Dose"]

pd.set_option("display.width", 200)


# --------------------------------------------------------------------------
# 数据准备
# --------------------------------------------------------------------------
def prepare(data_dir: Path, pop_flag: str = "SAFFL") -> tuple[pd.DataFrame, pd.Series]:
    """载入 ADSL 并准备分析人群与分母。"""
    adsl = cio.read_any(data_dir / "adsl.csv")

    # 1) 固定治疗组顺序（这是"复刻 SAS 输出顺序"的关键一步）
    adsl["TRT01P"] = to_categorical(
        adsl["TRT01P"].astype(str).str.strip(), TRT_LEVELS
    )

    # 2) 派生 AGEGR1（边界：65 与 80 都归入 '65-80'）
    if "AGEGR1" not in adsl.columns:
        adsl = derive_agegr1(adsl)
    else:
        # 已有 AGEGR1 时仍然重算，用于与原始列做一致性核对（见下）
        # num_var=None 避免覆盖原有的 AGEGR1N
        adsl = derive_agegr1(adsl, out_var="_AGEGR1_CHECK", num_var=None)

    # 3) 限定分析人群
    n_all = len(adsl)
    adsl = adsl[adsl[pop_flag].astype(str).str.strip() == "Y"].copy()
    print(f"ADSL 总记录 {n_all} 行；{pop_flag}='Y' 的分析人群 {len(adsl)} 行")

    # 4) 分母：各治疗组人数
    denom = adsl.groupby("TRT01P", observed=False)["USUBJID"].nunique()
    print("\n各治疗组人数（分母）：")
    for t in TRT_LEVELS:
        print(f"    {t:26s} {int(denom.get(t, 0)):>4}")
    print(f"    {'合计':26s} {int(denom.sum()):>4}")
    return adsl, denom


# --------------------------------------------------------------------------
# 行构造器
# --------------------------------------------------------------------------
def row_n(label: str, var: str, df: pd.DataFrame, denom: pd.Series) -> dict:
    """计数行（受试者数）。"""
    r = {"变量": label, "类别": ""}
    for t in TRT_LEVELS:
        r[t] = str(int(df.loc[df["TRT01P"] == t, var].nunique()))
    r["合计"] = str(int(df[var].nunique()))
    return r


def row_continuous(label: str, var: str, df: pd.DataFrame,
                   decimals: int = 1) -> dict:
    """连续变量行：Mean (SD)。"""
    r = {"变量": label, "类别": ""}
    for t in TRT_LEVELS:
        r[t] = rpt.mean_sd(df.loc[df["TRT01P"] == t, var], decimals)
    r["合计"] = rpt.mean_sd(df[var], decimals)
    return r


def row_continuous_extra(label: str, var: str, df: pd.DataFrame,
                         decimals: int = 1) -> list[dict]:
    """连续变量的补充行：Median (Min - Max)。"""
    r = {"变量": "", "类别": "Median (Min - Max)"}
    for t in TRT_LEVELS:
        r[t] = rpt.median_range(df.loc[df["TRT01P"] == t, var], decimals)
    r["合计"] = rpt.median_range(df[var], decimals)
    return [r]


def row_cat(label: str, var: str, level: str, df: pd.DataFrame,
            denom: pd.Series, decimals: int = 1, indent: bool = True) -> dict:
    """分类变量行：n (%)。"""
    r = {"变量": label, "类别": ("  " if indent else "") + str(level)}
    total_n = int((df[var].astype(str) == str(level)).sum())
    for t in TRT_LEVELS:
        sub = df[df["TRT01P"] == t]
        n = int((sub[var].astype(str) == str(level)).sum())
        r[t] = rpt.pct_format(n, int(denom.get(t, 0)), decimals)
    r["合计"] = rpt.pct_format(total_n, int(denom.sum()), decimals)
    return r


def blank(label: str = "", cat: str = "") -> dict:
    r = {"变量": label, "类别": cat}
    for t in TRT_LEVELS:
        r[t] = ""
    r["合计"] = ""
    return r


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def main() -> None:
    data_dir = BASE / "data" / "samples"
    out_dir = BASE / "outputs"
    out_dir.mkdir(exist_ok=True)

    print("=" * 78)
    print("  案例 02 · 人口学与基线特征表（Table 1，安全性人群）")
    print("=" * 78)

    adsl, denom = prepare(data_dir)

    rows: list[dict] = []
    # 表头行
    rows.append(row_n("受试者数", "USUBJID", adsl, denom))
    rows.append(row_continuous("年龄 (岁)", "AGE", adsl))
    rows.extend(row_continuous_extra("", "AGE", adsl))
    rows.append(blank())

    # 年龄分组
    rows.append(blank("年龄分组, n (%)"))
    for lvl in ["<65", "65-80", ">80"]:
        rows.append(row_cat("", "AGEGR1", lvl, adsl, denom))

    # 性别
    rows.append(blank())
    rows.append(blank("性别, n (%)"))
    for lvl, lab in [("F", "女"), ("M", "男")]:
        rows.append(row_cat("", "SEX", lvl, adsl, denom))

    # 种族
    rows.append(blank())
    rows.append(blank("种族, n (%)"))
    for lvl in adsl["RACE"].astype(str).value_counts().index:
        rows.append(row_cat("", "RACE", lvl, adsl, denom))

    # 基线 BMI（如果有）
    if "BMIBL" in adsl.columns:
        rows.append(blank())
        rows.append(row_continuous("基线 BMI (kg/m²)", "BMIBL", adsl))
        rows.extend(row_continuous_extra("", "BMIBL", adsl))

    # 基线体重、身高
    for var, lab in [("WEIGHTBL", "基线体重 (kg)"), ("HEIGHTBL", "基线身高 (cm)")]:
        if var in adsl.columns:
            rows.append(row_continuous(lab, var, adsl))

    # 治疗时长
    if "TRTDUR" in adsl.columns:
        rows.append(row_continuous("治疗天数", "TRTDUR", adsl, decimals=0))

    table = pd.DataFrame(rows)

    # ------------------------------------------------------------------
    print("\n" + "-" * 78)
    print("表 1. 人口学与基线特征（安全性人群）")
    print("-" * 78)
    print(table.to_string(index=False))
    print("-" * 78)
    print("注：连续变量为 Mean (SD)，SD 采用 ddof=1；分类变量为 n (%)，"
          "分母为该治疗组的分析人群人数。")
    print("    数据来源：CDISC 试点项目 CDISCPILOT01（公开数据）。")

    # ------------------------------------------------------------------
    # 一致性核对：派生的 AGEGR1 与数据集原有的 AGEGR1 是否一致
    if "_AGEGR1_CHECK" in adsl.columns and "AGEGR1" in adsl.columns:
        raw = adsl["AGEGR1"].astype(str).str.strip()
        new = adsl["_AGEGR1_CHECK"].astype(str).str.strip()
        n_diff = int((raw != new).sum())
        print(f"\n【核对】派生 AGEGR1 与数据集原有 AGEGR1 不一致的记录数：{n_diff}"
              f"  {'（一致，派生逻辑正确）' if n_diff == 0 else '（不一致，需检查边界规则！）'}")

    # ------------------------------------------------------------------
    csv_path = out_dir / "table01_demographics.csv"
    html_path = rpt.to_html(
        table, out_dir / "table01_demographics.html",
        title="表 1. 人口学与基线特征",
        subtitle="安全性人群（SAFFL='Y'）",
        footnote=("数据来源：CDISC 试点项目 CDISCPILOT01（公开数据）。"
                  "连续变量为 Mean (SD)，分类变量为 n (%)。"),
    )
    table.to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"\n已输出：\n  {csv_path}\n  {html_path}")


if __name__ == "__main__":
    main()
