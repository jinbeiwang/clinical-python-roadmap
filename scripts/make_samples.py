#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
make_samples.py —— 从 data/raw/*.xpt 生成轻量 CSV 样本到 data/samples/

为什么需要这一步
----------------
原始的 CDISC 试点数据是 SAS XPT 格式（且部分文件达 30 MB）。
为了让**克隆仓库后无需联网下载即可直接跑通所有案例**，
本脚本把案例用到的数据集转成小体积 CSV，并对超大表做**有原则的裁剪**：

- 大数据集（如 VS）只保留案例实际用到的变量与访视，并在文件名/README 中标注；
- 小数据集（DM/ADSL/AE/ADAE/ADTTE/EX/DS）完整导出，不做裁剪；
- 同时导出变量标签 JSON，供教程演示"如何保留 SAS 的 label 信息"。

用法
----
    python scripts/make_samples.py
    python scripts/make_samples.py --raw data/raw --out data/samples
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import pandas as pd
import pyreadstat

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

# 完整导出（不裁剪）
FULL_DATASETS = ["dm", "adsl", "adae", "ae", "adtte", "ex", "ds"]

# VS 裁剪规格：案例 04（生命体征移位表）只需血压与脉搏，且限定在分析访视
VS_KEEP_COLS = ["USUBJID", "VSTESTCD", "VSTEST", "VSSTRESN", "VSSTRESU",
                "VSBLFL", "VISITNUM", "VISIT", "VSDTC"]
VS_KEEP_TESTS = ["SYSBP", "DIABP", "PULSE"]
VS_VISIT_RANGE = (1, 13)          # 含端点；筛掉非计划访视（3.1 / 3.5）

# VS 人体测量子集：案例 05（ADSL 衍生）需要身高体重算 BMI
VS_ANTHROP_TESTS = ["HEIGHT", "WEIGHT"]

# ADLBC 裁剪规格：案例 04（实验室移位表）用基线 + 治疗结束两个访视
ADLBC_KEEP_COLS = ["USUBJID", "TRTP", "PARAMCD", "PARAM", "PARCAT1",
                   "AVAL", "BASE", "CHG", "ANRLO", "ANRHI",
                   "ANRIND", "BNRIND", "ABLFL", "AVISIT", "AVISITN", "VISITNUM"]
ADLBC_KEEP_VISITS = ["Baseline", "End of Treatment"]


def strip_chars(df: pd.DataFrame) -> pd.DataFrame:
    """去掉 XPT 里字符列的首尾空格（XPT 定长存储会补空格）。

    注意两点：

    1. 不依赖 ``select_dtypes("object")`` —— pandas 3 起字符串 dtype 已独立，
       用 ``object`` 过滤会**漏掉** StringDtype 的列。
    2. 用 ``astype("string")`` 而不是 ``astype(str)``：
       后者会把真正的缺失值 ``NaN`` 变成字符串 ``"nan"``，
       写出 CSV 后就再也分不清"缺失"和"值等于 nan 的字符串"了。
    """
    for c in df.columns:
        if (not pd.api.types.is_numeric_dtype(df[c])
                and not pd.api.types.is_datetime64_any_dtype(df[c])):
            df[c] = df[c].astype("string").str.strip()
    return df


def label_json(meta, out_dir: Path, name: str) -> None:
    """导出变量标签与值标签，演示如何把 SAS label 带进 pandas 工作流。"""
    payload = {
        "column_labels": meta.column_names_to_labels,
        "value_labels": getattr(meta, "variable_value_labels", {}) or {},
        "variable_types": getattr(meta, "original_variable_types", {}) or {},
    }
    path = out_dir / f"{name}_meta.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.info("  标签 -> %s", path.name)


def export_full(raw_dir: Path, out_dir: Path, name: str) -> None:
    src = raw_dir / f"{name}.xpt"
    if not src.exists():
        logging.warning("  跳过 %s（原始文件不存在，请先运行 download_data.py）", name)
        return
    df, meta = pyreadstat.read_xport(str(src))
    df = strip_chars(df)
    dst = out_dir / f"{name}.csv"
    df.to_csv(dst, index=False, encoding="utf-8-sig")
    logging.info("  %-8s -> %-12s %7d 行 x %2d 列  (%s)",
                 f"{name}.xpt", dst.name, *df.shape, f"{dst.stat().st_size / 1024:.0f} KB")
    label_json(meta, out_dir, name)


def export_vs_subset(raw_dir: Path, out_dir: Path) -> None:
    src = raw_dir / "vs.xpt"
    if not src.exists():
        logging.warning("  跳过 vs（原始文件不存在）")
        return
    df, meta = pyreadstat.read_xport(str(src))
    n0 = len(df)
    cols = [c for c in VS_KEEP_COLS if c in df.columns]
    lo, hi = VS_VISIT_RANGE
    sub = df.loc[
        df["VSTESTCD"].isin(VS_KEEP_TESTS) & df["VISITNUM"].between(lo, hi),
        cols,
    ].copy()
    sub = strip_chars(sub)
    dst = out_dir / "vs_bp.csv"
    sub.to_csv(dst, index=False, encoding="utf-8-sig")
    logging.info("  %-8s -> %-12s %7d 行 x %2d 列  (%s)  [已裁剪: %d -> %d 行]",
                 "vs.xpt", dst.name, *sub.shape, f"{dst.stat().st_size / 1024:.0f} KB", n0, len(sub))


def export_vs_anthrop(raw_dir: Path, out_dir: Path) -> None:
    """导出身高/体重记录（案例 05 用它派生 HEIGHTBL / WEIGHTBL / BMIBL）。

    ★ 这里有个很容易踩的坑：``VSSTRESN`` 是"标准单位"的**全精度**值，
      例如体重 120 LB → 54.43 kg，而 ADSL 的 ``WEIGHTBL`` 是
      ``ROUND(., 0.1)`` 之后的 54.4。
      直接拿 VSSTRESN 去比会 253/254 不一致 —— 必须先舍入再比。
    """
    src = raw_dir / "vs.xpt"
    if not src.exists():
        logging.warning("  跳过 vs（原始文件不存在）")
        return
    df, meta = pyreadstat.read_xport(str(src))
    n0 = len(df)
    cols = [c for c in ["USUBJID", "VSTESTCD", "VSTEST", "VSORRES", "VSORRESU",
                        "VSSTRESN", "VSSTRESU", "VSBLFL",
                        "VISITNUM", "VISIT", "VSDTC"] if c in df.columns]
    sub = df.loc[df["VSTESTCD"].isin(VS_ANTHROP_TESTS), cols].copy()
    sub = strip_chars(sub)
    dst = out_dir / "vs_anthrop.csv"
    sub.to_csv(dst, index=False, encoding="utf-8-sig")
    logging.info("  %-8s -> %-12s %7d 行 x %2d 列  (%s)  [已裁剪: %d -> %d 行]",
                 "vs.xpt", dst.name, *sub.shape, f"{dst.stat().st_size / 1024:.0f} KB", n0, len(sub))


def export_adlbc_subset(raw_dir: Path, out_dir: Path) -> None:
    """导出实验室检查（ADLBC）的移位表用子集：基线 + 治疗结束。"""
    src = raw_dir / "adlbc.xpt"
    if not src.exists():
        logging.warning("  跳过 adlbc（原始文件不存在）")
        return
    df, meta = pyreadstat.read_xport(str(src))
    n0 = len(df)
    cols = [c for c in ADLBC_KEEP_COLS if c in df.columns]
    sub = df.loc[df["AVISIT"].astype(str).str.strip().isin(ADLBC_KEEP_VISITS), cols].copy()
    sub = strip_chars(sub)
    dst = out_dir / "adlbc_shift.csv"
    sub.to_csv(dst, index=False, encoding="utf-8-sig")
    logging.info("  %-8s -> %-12s %7d 行 x %2d 列  (%s)  [已裁剪: %d -> %d 行]",
                 "adlbc.xpt", dst.name, *sub.shape,
                 f"{dst.stat().st_size / 1024:.0f} KB", n0, len(sub))


def write_manifest(out_dir: Path) -> None:
    """生成样本清单，说明每个 CSV 的来源与是否裁剪。"""
    TRIM_NOTE = {
        "vs_bp": "是（仅 SYSBP/DIABP/PULSE，访视 1-13）",
        "vs_anthrop": "是（仅 HEIGHT/WEIGHT）",
        "adlbc_shift": "是（仅 Baseline / End of Treatment 两个访视）",
    }
    rows = []
    for p in sorted(out_dir.glob("*.csv")):
        if p.name == "MANIFEST.csv":
            continue                      # 不把自己列进清单
        try:
            head = pd.read_csv(p, nrows=0)
            n_rows = sum(1 for _ in p.open(encoding="utf-8-sig")) - 1
        except Exception:  # noqa: BLE001
            n_rows = -1
            head = pd.DataFrame()
        rows.append({
            "文件": p.name,
            "行数": n_rows,
            "列数": len(head.columns),
            "大小(KB)": round(p.stat().st_size / 1024),
            "来源": "CDISCPILOT01 " + p.stem.split("_")[0].upper(),
            "是否裁剪": TRIM_NOTE.get(p.stem, "否"),
        })
    mf = pd.DataFrame(rows)
    mf.to_csv(out_dir / "MANIFEST.csv", index=False, encoding="utf-8-sig")
    logging.info("\n样本清单：")
    print(mf.to_string(index=False))


def main() -> None:
    ap = argparse.ArgumentParser(description="从 XPT 生成案例用 CSV 样本")
    ap.add_argument("--raw", default="data/raw")
    ap.add_argument("--out", default="data/samples")
    args = ap.parse_args()

    raw_dir, out_dir = Path(args.raw), Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    logging.info("从 %s 生成 CSV 样本到 %s", raw_dir, out_dir)
    for name in FULL_DATASETS:
        export_full(raw_dir, out_dir, name)
    export_vs_subset(raw_dir, out_dir)
    export_vs_anthrop(raw_dir, out_dir)
    export_adlbc_subset(raw_dir, out_dir)
    write_manifest(out_dir)


if __name__ == "__main__":
    main()
