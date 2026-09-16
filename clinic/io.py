"""
clinic.io —— 临床数据读取工具

对应 SAS 世界里 ``LIBNAME`` + ``SET`` 的那一层。
重点解决三件事：
1. 读取 SAS 格式（.xpt / .sas7bdat）而不需要安装 SAS；
2. **保留变量标签（label）与值标签（format）** —— pandas 原生会丢弃它们；
3. 把 CDISC 数据里各种"伪缺失"（空串、'NA'、'.' 等）统一成缺失值。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

# CDISC / 常见 EDC 导出里的"伪缺失"标记。
# SAS 里空串即缺失；pandas 里空串是合法值，必须显式清洗。
NA_TOKENS = {
    "", " ", ".", "NA", "N/A", "N.A.", "NULL", "null", "NaN", "nan",
    "MISSING", "Missing", "UNKNOWN", "Unknown", "UNK", "-",
}


# --------------------------------------------------------------------------
# 读取
# --------------------------------------------------------------------------
def read_xpt(path: str | Path, keep_labels: bool = True,
             as_datetime: bool = False) -> pd.DataFrame:
    """读取 SAS XPORT (.xpt) 文件。

    Parameters
    ----------
    path : 文件路径
    keep_labels : 是否把变量标签/值标签挂到 ``df.attrs``（推荐 True）
    as_datetime : 是否自动把以 DTC/DT 结尾的字符列转成 datetime。
        **默认 False**，因为临床数据常有"部分日期"（只有年月），
        自动转换会把 ``2014-01`` 变成 ``2014-01-01`` 从而引入虚假精度。

    Notes
    -----
    需要 ``pyreadstat``。若未安装：``pip install pyreadstat``
    """
    import pyreadstat

    path = Path(path)
    df, meta = pyreadstat.read_xport(str(path))

    if keep_labels:
        df.attrs["labels"] = meta.column_names_to_labels
        df.attrs["value_labels"] = getattr(meta, "variable_value_labels", {}) or {}
        df.attrs["sas_types"] = getattr(meta, "original_variable_types", {}) or {}
        df.attrs["source"] = str(path)

    if as_datetime:
        for col in df.columns:
            if col.endswith(("DTC", "DT")) and df[col].dtype == "object":
                df[col] = pd.to_datetime(df[col], errors="coerce")

    log.info("读取 %-18s %7d 行 x %2d 列", path.name, *df.shape)
    return df


def read_sas7bdat(path: str | Path, catalog_file: str | Path | None = None,
                  keep_labels: bool = True) -> pd.DataFrame:
    """读取 SAS 数据集 (.sas7bdat)。``catalog_file`` 用于加载自定义格式 (.sas7bcat)。"""
    import pyreadstat

    df, meta = pyreadstat.read_sas7bdat(
        str(path), catalog_file=str(catalog_file) if catalog_file else None
    )
    if keep_labels:
        df.attrs["labels"] = meta.column_names_to_labels
        df.attrs["value_labels"] = getattr(meta, "variable_value_labels", {}) or {}
    return df


def read_any(path: str | Path, clean: bool = True, **kw) -> pd.DataFrame:
    """按扩展名自动选择读取方式。

    支持 ``.xpt`` / ``.sas7bdat`` / ``.csv`` / ``.xlsx`` / ``.parquet``。
    """
    p = Path(path)
    suffix = p.suffix.lower()

    if suffix == ".xpt":
        df = read_xpt(p, **kw)
    elif suffix == ".sas7bdat":
        df = read_sas7bdat(p, **kw)
    elif suffix == ".csv":
        df = pd.read_csv(p, low_memory=False)
    elif suffix in (".xlsx", ".xls"):
        df = pd.read_excel(p)
    elif suffix in (".parquet", ".pq"):
        df = pd.read_parquet(p)
    else:
        raise ValueError(f"不支持的文件类型: {suffix}")

    return clean_missing(df) if clean else df


def load_domains(data_dir: str | Path, domains: list[str],
                 ext: str = "auto") -> dict[str, pd.DataFrame]:
    """批量加载多个域；**单个域失败不影响其他**。

    对应 SAS 里"一个程序跑一批数据集"的场景，
    但比 SAS 更好：SAS 遇到错误会中断，这里只会 WARNING 并继续。

    Parameters
    ----------
    data_dir : 数据目录
    domains : 域名列表，如 ['dm', 'ae', 'adsl']
    ext : 'auto' 按 csv -> xpt -> sas7bdat 优先级查找；
          也可指定 'csv' / 'xpt' / 'sas7bdat'
    """
    data_dir = Path(data_dir)
    suffixes = {"csv": [".csv"], "xpt": [".xpt"],
                "sas7bdat": [".sas7bdat"]}.get(ext, [".csv", ".xpt", ".sas7bdat"])

    out: dict[str, pd.DataFrame] = {}
    for dom in domains:
        found = False
        for suffix in suffixes:
            f = data_dir / f"{dom}{suffix}"
            if f.exists():
                try:
                    out[dom] = read_any(f)
                    found = True
                    break
                except Exception as exc:  # noqa: BLE001
                    log.error("读取 %s 失败: %s", f.name, exc)
        if not found:
            log.warning("未找到 %s 的数据文件（已尝试 %s）", dom, suffixes)

    log.info("共加载 %d/%d 个域", len(out), len(domains))
    return out


# --------------------------------------------------------------------------
# 清洗
# --------------------------------------------------------------------------
def clean_missing(df: pd.DataFrame, na_tokens: set[str] | None = None,
                  strip: bool = True) -> pd.DataFrame:
    """把各种"伪缺失"统一成 ``NaN``，并去掉字符列首尾空格。

    SAS 里 ``''`` 就是缺失，所以这段逻辑在 SAS 世界里是隐式的；
    在 pandas 里必须显式做，否则会得到错误的分组与统计结果。
    """
    tokens = {t.strip() for t in (na_tokens or NA_TOKENS)}
    df = df.copy()
    for col in df.columns:
        if pd.api.types.is_numeric_dtype(df[col]) or pd.api.types.is_datetime64_any_dtype(df[col]):
            continue
        s = df[col].astype("string")
        if strip:
            s = s.str.strip()
        df[col] = s.mask(s.isin(tokens))
    return df


def get_labels(df: pd.DataFrame) -> dict[str, str]:
    """取变量标签；没有则返回 ``{变量: 变量}`` 的恒等映射。"""
    return df.attrs.get("labels") or {c: c for c in df.columns}


def save_labels(df: pd.DataFrame, path: str | Path) -> None:
    """把变量标签导出成 JSON 侧车文件（pandas 不原生保存 label）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            "labels": df.attrs.get("labels", {}),
            "value_labels": df.attrs.get("value_labels", {}),
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# --------------------------------------------------------------------------
# 写出
# --------------------------------------------------------------------------
def write_xpt(df: pd.DataFrame, path: str | Path, file_label: str = "",
              column_labels: list[str] | None = None) -> None:
    """写出 SAS XPORT (.xpt) 文件 —— Python 与 SAS 之间的桥梁。

    当你需要把 Python 生成的数据集交给 SAS 用户、或进入 CDISC 提交包时使用。
    """
    import pyreadstat

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pyreadstat.write_xport(
        df, str(path),
        file_label=file_label,
        column_labels=column_labels,
    )
    log.info("写出 %s（%d 行 x %d 列）", path.name, *df.shape)


def write_table(df: pd.DataFrame, path: str | Path,
                sheet: str = "Table", title: str = "") -> None:
    """导出表格到 Excel 或 CSV（按扩展名判断），带基础排版。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() in (".xlsx", ".xls"):
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            df.to_excel(writer, sheet_name=sheet, index=False)
            ws = writer.sheets[sheet]
            for c in range(1, len(df.columns) + 1):
                cell = ws.cell(row=1, column=c)
                cell.font = cell.font.copy(bold=True)
            ws.freeze_panes = "A2"
    else:
        header = f"# {title}\n" if title else ""
        path.write_text(header + df.to_csv(index=False), encoding="utf-8-sig")
    log.info("已输出 %s", path)
