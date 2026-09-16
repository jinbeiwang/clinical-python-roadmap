#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
案例 01 · 数据体检（Data Profiling）
=====================================

**目标**：拿到一批 CDISC 数据后，30 秒内摸清它的底细。

**对应 SAS**：``PROC CONTENTS`` + ``PROC MEANS nmiss`` + ``PROC FREQ``
的组合，但**一次跑完、结果结构化、可重复**。

**覆盖教程章节**：第 10 章（数据质量）、第 11 章（数据结构）、第 12 章（SDTM）

运行
----
    python cases/case01_数据体检.py
    python cases/case01_数据体检.py --data data/raw      # 用下载的 XPT
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

# 让脚本无论从哪里运行都能 import 到项目根目录下的 clinic 包
BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from clinic import io as cio          # noqa: E402
from clinic import qc                 # noqa: E402

pd.set_option("display.width", 200)
pd.set_option("display.max_colwidth", 42)

# 要体检的域（SDTM + ADaM）
DOMAINS = ["dm", "ae", "ex", "ds", "adsl", "adae", "adtte"]


def section(title: str) -> None:
    print("\n" + "=" * 78)
    print(f"  {title}")
    print("=" * 78)


def main() -> None:
    ap = argparse.ArgumentParser(description="CDISC 数据集体检")
    ap.add_argument("--data", default=None,
                    help="数据目录（默认优先 data/raw，其次 data/samples）")
    ap.add_argument("--out", default=None, help="输出目录（默认 outputs/）")
    args = ap.parse_args()

    if args.data:
        data_dir = Path(args.data)
    else:
        raw, samples = BASE / "data" / "raw", BASE / "data" / "samples"
        data_dir = raw if (raw / "dm.xpt").exists() or (raw / "dm.csv").exists() else samples

    out_dir = Path(args.out) if args.out else BASE / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"数据目录：{data_dir.resolve()}")

    # ------------------------------------------------------------------
    section("1. 载入各域（单个域失败不影响其他）")
    data = cio.load_domains(data_dir, DOMAINS)
    if not data:
        print("没有加载到任何数据。")
        print("请先运行：python scripts/download_data.py --core")
        print("    然后：  python scripts/make_samples.py")
        return

    sizes = pd.DataFrame([
        {"域": k.upper(), "行数": len(v), "列数": v.shape[1],
         "内存MB": round(v.memory_usage(deep=True).sum() / 1024**2, 2)}
        for k, v in data.items()
    ])
    print(sizes.to_string(index=False))

    # ------------------------------------------------------------------
    section("2. 逐域体检（变量级缺失与分布概况）")
    profiles = {}
    for name, df in data.items():
        print(f"\n--- {name.upper()} ---")
        prof = qc.profile(df, name)
        profiles[name] = prof
        # 只显示有缺失或低基数的变量，避免刷屏
        interesting = prof[(prof["缺失数"] > 0) | (prof["唯一值数"] <= 8)]
        print(interesting.to_string(index=False))
        if len(interesting) < len(prof):
            print(f"    （另有 {len(prof) - len(interesting)} 个变量无缺失且取值丰富，已省略）")
        prof.to_csv(out_dir / f"profile_{name}.csv", index=False, encoding="utf-8-sig")

    # ------------------------------------------------------------------
    section("3. 质量检查（按 CDISC 规则）")
    all_issues: list[dict] = []
    for name, df in data.items():
        dtype_ = "adam" if name.startswith("ad") else "sdtm"
        issues = qc.check_dataset(df, name, dtype_)
        all_issues.extend(issues)
        print(qc.report(issues, name.upper()))

    # ------------------------------------------------------------------
    section("4. 受试者一致性（以 DM 为基准）")
    if "dm" in data:
        others = {k: v for k, v in data.items() if k != "dm" and "USUBJID" in v.columns}
        cons = qc.check_subject_consistency(data["dm"], others)
        print(qc.report(cons, "跨域一致性"))

        print("\n【重点解读】")
        dm_n = data["dm"]["USUBJID"].nunique()
        adsl_n = data["adsl"]["USUBJID"].nunique() if "adsl" in data else None
        print(f"  DM   受试者数 = {dm_n}   （全部被筛选/入组者）")
        if adsl_n is not None:
            print(f"  ADSL 受试者数 = {adsl_n}   （仅随机化并接受治疗者）")
            print(f"  差 {dm_n - adsl_n} 人：这些是筛选失败/未随机化者。")
            print("  ★ 任何分析的基数都应以 ADSL 为准；用 DM 的 306 当分母会算错百分比。")
    else:
        print("（未加载 dm，跳过）")

    # ------------------------------------------------------------------
    section("5. 治疗组分布（核对分母）")
    if "adsl" in data:
        adsl = data["adsl"]
        for col in ["TRT01P", "TRT01A"]:
            if col in adsl.columns:
                vc = adsl[col].astype(str).str.strip().value_counts(dropna=False)
                print(f"\n{col}:")
                for k, v in vc.items():
                    print(f"    {k:26s} {v:>4}  ({v / len(adsl) * 100:.1f}%)")
        if "SAFFL" in adsl.columns:
            print(f"\nSAFFL: {adsl['SAFFL'].astype(str).str.strip().value_counts().to_dict()}")
            print("  ★ SAFFL='Y' 的受试者数就是所有安全性报表的分母。")

    # ------------------------------------------------------------------
    total_high = [i for i in all_issues if i["严重性"] == "高"]
    print("\n" + "=" * 78)
    print(f"体检完成：共检查 {len(data)} 个域，发现 {len(total_high)} 个高优先级问题")
    print(f"变量级报告已输出到：{out_dir}")
    print("=" * 78)


if __name__ == "__main__":
    main()
