#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
案例 06 · 文件与批处理自动化
=============================

**目标**：把"一次处理一个数据集"变成"一次处理一批数据集"，
并且**一个文件出错不影响其他文件**。

**覆盖教程章节**：第 05 章（文件与批处理）、第 11 章（读取 XPT）、第 17 章（工程化）

为什么值得单独写一个案例
------------------------
SAS 程序员做批处理通常靠 ``%macro`` + ``%do`` 循环 + ``dictionary.tables``。
一旦某个数据集出错，SAS 会**中断整个程序** —— 你改完再跑，又从头开始。
Python 里可以做到"逐个处理、逐个记录、出错继续"，
这对"一批 50 个域文件的例行检查"是质的差别。

本案例做三件事
--------------
1. **清点（inventory）**：扫描目录，逐个读进来体检，输出一张汇总表。
2. **批量转换（convert）**：把 ``.xpt`` 批量转成 ``.csv``
   （存在则跳过，``--force`` 才覆盖）—— 幂等、可重跑。
3. **汇总导出**：把结果写成「一个域一个 sheet」的 Excel + 一份运行日志。

运行
----
    python cases/case06_批处理自动化.py                      # 清点样本目录（默认）
    python cases/case06_批处理自动化.py --action convert      # 批量 XPT → CSV
    python cases/case06_批处理自动化.py --action all --force  # 清点 + 转换（覆盖）
    python cases/case06_批处理自动化.py --dir data/raw --pattern "*.xpt"
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from clinic import io as cio              # noqa: E402

pd.set_option("display.width", 200)

# 支持的文件类型（对应 SAS 里能 SET / 能 libname 的几种）
READABLE = {".csv", ".xpt", ".sas7bdat", ".xlsx", ".parquet"}

# 清点时跳过的"巨型表"（想全量扫描时用 --no-skip-large）
LARGE_HINT = {"advs", "adlbc", "adlb", "adpc", "adpp", "sv", "vs"}


def setup_logger(log_path: Path) -> logging.Logger:
    """同时往终端和文件写日志。

    对应 SAS 里的 ``proc printto log='...';`` ——
    但 pandas 的日志是**标准 logging**，可以和任何工具链（Airflow、
    调度器、Kubernetes）对接，不用解析 SAS 的 .log 文本。
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("batch")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s",
                            datefmt="%H:%M:%S")
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger


def section(title: str) -> None:
    print("\n" + "=" * 88)
    print(f"  {title}")
    print("=" * 88)


# ==========================================================================
# 1. 目录扫描 —— 对应 SAS 的 filename pipe + data _null_ 读取目录
# ==========================================================================
def scan_dir(directory: Path, pattern: str = "*") -> list[Path]:
    """扫描目录，返回所有可读文件（按文件名排序，保证结果可复现）。

    ★ ``pathlib`` 与 SAS 的对比：

    ===================================  =============================
    SAS                                  Python
    ===================================  =============================
    ``filename d pipe "dir /b";``        ``Path(dir).glob("*.xpt")``
    ``%sysfunc(fileexist(...))``         ``p.exists()``
    ``dopen/dread`` 读目录                ``iterdir()`` / ``rglob()``
    ``scan(path,-1,'.')`` 取扩展名        ``p.suffix``
    ===================================  =============================
    """
    directory = Path(directory)
    if not directory.exists():
        raise FileNotFoundError(f"目录不存在：{directory}")
    files = sorted(p for p in directory.glob(pattern)
                   if p.is_file() and p.suffix.lower() in READABLE)
    return files


# ==========================================================================
# 2. 单文件体检 —— 对应"一个数据集一个 PROC CONTENTS + PROC FREQ"
# ==========================================================================
def profile_one(path: Path, nrows: int | None = None) -> dict:
    """读一个文件并返回体检结果（绝不抛异常，失败也返回记录）。

    ★ 这是批处理的关键设计：**把异常收在函数内部**，
      让调用方永远拿到一条可记录的、结构一致的结果。
      这样"某个文件坏了"就变成结果表里的一行，而不是整个程序崩掉。
    """
    t0 = time.perf_counter()
    rec = {"文件": path.name, "格式": path.suffix.lower().lstrip("."),
           "大小KB": round(path.stat().st_size / 1024), "状态": "", "行数": None,
           "列数": None, "缺失列数": None, "耗时秒": None, "备注": ""}
    try:
        df = cio.read_any(path)
        rec["行数"] = len(df)
        rec["列数"] = df.shape[1]
        na = df.isna().sum()
        rec["缺失列数"] = int((na > 0).sum())
        if rec["缺失列数"]:
            worst = na.sort_values(ascending=False).head(3)
            rec["备注"] = "缺失最多：" + "、".join(
                f"{c}({int(v)})" for c, v in worst.items())
        rec["状态"] = "OK"
    except Exception as exc:                      # noqa: BLE001
        rec["状态"] = "失败"
        rec["备注"] = f"{type(exc).__name__}: {str(exc)[:90]}"
    rec["耗时秒"] = round(time.perf_counter() - t0, 2)
    return rec


def inventory(directory: Path, pattern: str, logger: logging.Logger) -> pd.DataFrame:
    """批量体检整个目录。**一个文件失败不会中断整批。**"""
    files = scan_dir(directory, pattern)
    logger.info("扫描 %s → 找到 %d 个可读文件", directory, len(files))
    if not files:
        return pd.DataFrame()

    records = []
    for i, f in enumerate(files, 1):
        rec = profile_one(f)
        flag = "✓" if rec["状态"] == "OK" else "✗"
        logger.info("  [%2d/%2d] %s %-22s %s", i, len(files), flag, rec["文件"],
                    f"{rec['行数']:,} 行 × {rec['列数']} 列"
                    if rec["状态"] == "OK" else rec["备注"])
        records.append(rec)

    df = pd.DataFrame(records)
    n_fail = int((df["状态"] != "OK").sum())
    logger.info("清点完成：成功 %d 个，失败 %d 个", len(df) - n_fail, n_fail)
    return df


# ==========================================================================
# 3. 批量转换 —— 对应 SAS 的 "批量 libname 转换 / PROC EXPORT"
# ==========================================================================
def convert_batch(directory: Path, pattern: str, out_dir: Path,
                  force: bool, logger: logging.Logger) -> pd.DataFrame:
    """把 XPT / SAS7BDAT 批量转成 CSV。

    **幂等设计**：已存在的目标文件默认跳过 —— 这样脚本可以随时重跑，
    不会反复做无用功，也不会误删已有结果（除非显式 ``--force``）。
    这是"可重跑（idempotent）"在数据管线里的基本要求。
    """
    files = [f for f in scan_dir(directory, pattern)
             if f.suffix.lower() in (".xpt", ".sas7bdat")]
    logger.info("待转换 %d 个 SAS 格式文件 → %s", len(files), out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    for i, f in enumerate(files, 1):
        dst = out_dir / f"{f.stem}.csv"
        row = {"源文件": f.name, "目标": dst.name, "结果": "", "行数": None, "备注": ""}
        if dst.exists() and not force:
            row["结果"] = "跳过（已存在）"
            row["行数"] = sum(1 for _ in dst.open(encoding="utf-8-sig")) - 1
            logger.info("  [%2d/%2d] ⏭  %-20s 已存在，跳过", i, len(files), f.name)
            rows.append(row)
            continue
        try:
            df = cio.read_any(f)
            df.to_csv(dst, index=False, encoding="utf-8-sig")
            row["结果"] = "已转换"
            row["行数"] = len(df)
            logger.info("  [%2d/%2d] ✓  %-20s → %-20s %s 行",
                        i, len(files), f.name, dst.name, f"{len(df):,}")
        except Exception as exc:                  # noqa: BLE001
            row["结果"] = "失败"
            row["备注"] = f"{type(exc).__name__}: {str(exc)[:80]}"
            logger.error("  [%2d/%2d] ✗  %-20s %s", i, len(files), f.name, row["备注"])
        rows.append(row)
    return pd.DataFrame(rows)


# ==========================================================================
# 4. 汇总导出：一个域一个 sheet
# ==========================================================================
def export_workbook(summary: pd.DataFrame, out_path: Path,
                    logger: logging.Logger) -> None:
    """把清点结果写成多 sheet Excel（对应 SAS 的 ODS EXCEL + sheet_interval）。

    注意：Excel 单个 sheet 名最长 31 字符、且不能含 ``[]:*?/\\``，
    所以要对 sheet 名做清洗 —— 这类"看起来无关紧要"的限制
    在批处理里天天会撞上。
    """
    inv = summary[summary["_表"] == "清点"].drop(columns=["_表"])
    if inv.empty:
        return
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        inv.to_excel(writer, sheet_name="汇总", index=False)
        for _, r in inv.iterrows():
            name = Path(str(r["文件"])).stem[:28]
            name = "".join("_" if c in '[]:*?/\\' else c for c in name) or "sheet"
            try:
                df = cio.read_any(Path(r["_路径"]))
            except Exception:                     # noqa: BLE001
                continue
            # 只写前 200 行，避免 Excel 被大表撑爆（真实报表另存 CSV）
            df.head(200).to_excel(writer, sheet_name=name, index=False)
        # 其余表（转换记录等）
        for tag in summary["_表"].unique():
            if tag == "清点":
                continue
            sub = summary[summary["_表"] == tag].drop(columns=["_表"])
            if not sub.empty:
                sub.to_excel(writer, sheet_name=str(tag)[:28], index=False)
    logger.info("已导出多 sheet 工作簿：%s", out_path)


def main() -> None:
    ap = argparse.ArgumentParser(description="临床数据文件批处理自动化")
    ap.add_argument("--dir", default=str(BASE / "data" / "samples"),
                    help="要处理的数据目录（默认 data/samples）")
    ap.add_argument("--pattern", default="*", help="文件通配符，如 '*.xpt'")
    ap.add_argument("--outdir", default=str(BASE / "outputs"), help="输出目录")
    ap.add_argument("--action", default="inventory",
                    choices=["inventory", "convert", "all"])
    ap.add_argument("-f", "--force", action="store_true",
                    help="转换时覆盖已存在的目标文件（默认跳过）")
    args = ap.parse_args()

    src_dir = Path(args.dir)
    out_dir = Path(args.outdir)
    out_dir.mkdir(parents=True, exist_ok=True)
    logger = setup_logger(out_dir / "case06_batch.log")

    print("=" * 88)
    print("  案例 06 · 文件与批处理自动化")
    print("=" * 88)
    print(f"  源目录：{src_dir}")
    print(f"  通配符：{args.pattern}     动作：{args.action}")
    print(f"  日志：  {out_dir / 'case06_batch.log'}")

    parts: list[pd.DataFrame] = []
    t0 = time.perf_counter()

    if args.action in ("inventory", "all"):
        section("1. 清点（inventory）—— 逐个体检，出错不影响其他")
        inv = inventory(src_dir, args.pattern, logger)
        if not inv.empty:
            inv["_路径"] = [str(p) for p in scan_dir(src_dir, args.pattern)]
            inv["_表"] = "清点"
            show = inv.drop(columns=["_路径", "_表"])
            print()
            print(show.to_string(index=False))
            parts.append(inv)
            print("\n  ★ 请注意「耗时秒」这一列：批量处理时，")
            print("    **你要先知道时间花在哪里**，再决定要不要优化。")

    if args.action in ("convert", "all"):
        section("2. 批量转换（convert）—— 幂等、可重跑")
        con = convert_batch(src_dir, args.pattern, out_dir / "converted",
                            args.force, logger)
        if not con.empty:
            con["_表"] = "转换"
            print()
            print(con.drop(columns=["_表"]).to_string(index=False))
            parts.append(con)
            print(f"\n  目标目录：{out_dir / 'converted'}")
            print("  ★ 默认【跳过已存在】—— 这就是「幂等」：")
            print("    脚本可以随时重跑，不会重复劳动，也不会误删已有结果。")

    section("3. 汇总导出")
    if parts:
        summary = pd.concat(parts, ignore_index=True)
        summary.to_csv(out_dir / "case06_summary.csv", index=False, encoding="utf-8-sig")
        logger.info("已导出汇总 CSV：%s", out_dir / "case06_summary.csv")
        export_workbook(summary, out_dir / "case06_summary.xlsx", logger)
        print(f"  汇总 CSV：  {out_dir / 'case06_summary.csv'}")
        print(f"  多 sheet：  {out_dir / 'case06_summary.xlsx'}")
    print(f"  运行日志：  {out_dir / 'case06_batch.log'}")
    print(f"\n  总耗时 {time.perf_counter() - t0:.2f} 秒")

    section("4. 从 SAS 到 Python：批处理这一块到底对应什么")
    print("""  +--------------------------------------------------+------------------------------------------+
  | SAS 里你会写                                      | Python 里对应什么                        |
  +--------------------------------------------------+------------------------------------------+
  | filename d pipe "dir /b *.xpt";                   | Path(dir).glob("*.xpt")                  |
  | %macro loop(list); %do i=1 %to %sysfunc(countw);   | for f in files:  —— 不需要宏，直接循环    |
  | data _null_; set sashelp.vtable; ...; run;        | pd.DataFrame(records) 收集结果           |
  | proc printto log='run.log'; run;                  | logging.FileHandler("run.log")           |
  | 一个 DATA 步出错 → 整个程序停下                    | try/except → 记一行"失败"继续跑           |
  | proc export data=x outfile='x.csv'; run;          | df.to_csv(path, index=False)             |
  | ods excel file=... options(sheet_interval=bygroup) | pd.ExcelWriter + 多个 sheet_name         |
  +--------------------------------------------------+------------------------------------------+

  ★ 三个真正拉开差距的地方：
    1) **异常隔离**：SAS 里一个数据集坏了会中断整批；
       Python 里用 try/except 把失败变成结果表的一行，
       整批照常跑完，最后统一看哪几个失败了。
    2) **幂等**：SAS 脚本通常"从头跑到尾"，Python 脚本要设计成
       "跑几次结果都一样"（跳过已完成、结果可覆盖）。
       这是能被调度器反复执行的前提。
    3) **不依赖 SAS 环境**：这套脚本可以在没有装 SAS 的机器上、
       在 CI（GitHub Actions）、在服务器上跑。

  ★ 还要注意一个坑：Excel 的 sheet 名限制 31 字符、不能含 []:*?/\\。
    批处理时这类"格式限制"特别容易触发，务必做名字清洗。""")


if __name__ == "__main__":
    main()
