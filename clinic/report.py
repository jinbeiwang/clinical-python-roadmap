"""
clinic.report —— TFL 报表构造工具

Python 没有 ``PROC REPORT`` / ``PROC TABULATE``，
所以报表排版这一层需要自己搭。本模块提供 CSR 报表最常见的构造件：

- ``pct_format``          : "n (x.x%)" 单元格
- ``summarize_continuous``: 连续变量描述统计（N/Mean/SD/Median/Min/Max）
- ``count_subjects``      : 受试者层级计数（对应 ``count(distinct USUBJID)``）
- ``crosstab_shift``      : 移位表（基线 × 基线后）
- ``format_pvalue``       : p 值格式化（<0.0001 等惯例）
- ``to_html``             : 输出带样式的 HTML 表格
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .derive import sas_round_series

# 治疗组的默认顺序（按项目/SAP 调整；**绝不能依赖字母序**）
TRT_LEVELS = ["Placebo", "Xanomeline Low Dose", "Xanomeline High Dose"]


# --------------------------------------------------------------------------
# 单元格格式化
# --------------------------------------------------------------------------
def pct_format(n, denom, decimals: int = 1, zero_as: str = "0",
               na_as: str = "-") -> str:
    """生成 ``"n (x.x%)"`` 格式的单元格。

    Parameters
    ----------
    n : 分子（通常是受试者数）
    denom : 分母（该治疗组的分析人群人数）
    decimals : 百分比小数位
    zero_as : 分子为 0 时显示的内容（CSR 惯例多为 ``"0"``）
    na_as : 分母无效/缺失时显示的内容

    Examples
    --------
    >>> pct_format(26, 86)
    '26 (30.2%)'
    >>> pct_format(0, 86)
    '0'
    >>> pct_format(5, 0)
    '-'
    """
    if denom is None or pd.isna(denom) or denom == 0:
        return na_as
    if n is None or pd.isna(n) or n == 0:
        return zero_as
    p = sas_round_series(pd.Series([n / denom * 100.0]), decimals).iloc[0]
    return f"{int(n)} ({p:.{decimals}f}%)"


def format_pvalue(p, decimals: int = 4) -> str:
    """p 值格式化（临床报表惯例）。

    - ``p < 0.0001`` 时显示 ``"<0.0001"``（而不是 0.0000）
    - 其余保留指定小数位

    Examples
    --------
    >>> format_pvalue(0.00003)
    '<0.0001'
    >>> format_pvalue(0.12345)
    '0.1235'
    """
    if p is None or pd.isna(p):
        return "-"
    threshold = 10.0 ** (-decimals)
    if float(p) < threshold:
        return f"<{threshold:.{decimals}f}"
    return f"{float(p):.{decimals}f}"


def mean_sd(s: pd.Series, decimals: int = 1, empty: str = "-") -> str:
    """``"Mean (SD)"`` 格式。SD 用 ``ddof=1``，与 SAS 的 ``STD`` 一致。"""
    s = pd.to_numeric(s, errors="coerce").dropna()
    if len(s) == 0:
        return empty
    if len(s) == 1:
        return f"{sas_round_series(pd.Series([s.mean()]), decimals).iloc[0]:.{decimals}f} (NE)"
    m = sas_round_series(pd.Series([s.mean()]), decimals).iloc[0]
    sd = sas_round_series(pd.Series([s.std(ddof=1)]), decimals).iloc[0]
    return f"{m:.{decimals}f} ({sd:.{decimals}f})"


def min_max(s: pd.Series, decimals: int = 1, empty: str = "-") -> str:
    """``"Min - Max"`` 格式。"""
    s = pd.to_numeric(s, errors="coerce").dropna()
    if len(s) == 0:
        return empty
    lo = sas_round_series(pd.Series([s.min()]), decimals).iloc[0]
    hi = sas_round_series(pd.Series([s.max()]), decimals).iloc[0]
    return f"{lo:.{decimals}f} - {hi:.{decimals}f}"


def median_range(s: pd.Series, decimals: int = 1, empty: str = "-") -> str:
    """``"Median (Min - Max)"`` 格式。"""
    s = pd.to_numeric(s, errors="coerce").dropna()
    if len(s) == 0:
        return empty
    med = sas_round_series(pd.Series([s.median()]), decimals).iloc[0]
    return f"{med:.{decimals}f} ({min_max(s, decimals)})"


# --------------------------------------------------------------------------
# 统计
# --------------------------------------------------------------------------
def summarize_continuous(s: pd.Series, decimals: int = 1) -> dict:
    """连续变量的标准描述统计，全部按 SAS 舍入规则。

    Returns
    -------
    dict
        键：``n``, ``nmiss``, ``mean``, ``sd``, ``median``, ``min``, ``max``
        （``sd`` 用 ``ddof=1``，与 SAS ``STD`` 一致；n=1 时 sd 为 None）
    """
    s = pd.to_numeric(s, errors="coerce")
    valid = s.dropna()
    n, n_miss = len(valid), int(s.isna().sum())
    if n == 0:
        return {"n": 0, "nmiss": n_miss, "mean": None, "sd": None,
                "median": None, "min": None, "max": None}

    r = lambda v: float(sas_round_series(pd.Series([v]), decimals).iloc[0])  # noqa: E731
    return {
        "n": n,
        "nmiss": n_miss,
        "mean": r(valid.mean()),
        "sd": r(valid.std(ddof=1)) if n > 1 else None,   # ★ ddof=1 = SAS STD
        "median": r(valid.median()),
        "min": r(valid.min()),
        "max": r(valid.max()),
    }


def count_subjects(df: pd.DataFrame, by, subject: str = "USUBJID",
                   observed: bool = False) -> pd.Series:
    """按组统计**不重复受试者数**（对应 SQL 的 ``count(distinct USUBJID)``）。

    ★ 与 ``groupby().size()`` 的区别：size 数的是**记录数**。
      AE 表要求的是受试者数，用错会导致计数偏大。
    """
    return df.groupby(by, observed=observed)[subject].nunique()


def denominators(adsl: pd.DataFrame, trt_var: str = "TRT01P",
                 pop_flag: str = "SAFFL", observed: bool = False) -> pd.Series:
    """计算各治疗组的分析人群人数（作为所有 "n (%)" 的分母）。

    >>> denominators(adsl).to_dict()
    {'Placebo': 86, 'Xanomeline Low Dose': 84, 'Xanomeline High Dose': 84}
    """
    pop = adsl.query(f"{pop_flag} == 'Y'") if pop_flag in adsl.columns else adsl
    return pop.groupby(trt_var, observed=observed)["USUBJID"].nunique()


# --------------------------------------------------------------------------
# 移位表
# --------------------------------------------------------------------------
def crosstab_shift(baseline: pd.Series, post: pd.Series,
                   levels: list[str], pct: str = "row") -> pd.DataFrame:
    """构造移位表（如实验室指标的 LOW/NORMAL/HIGH 基线 vs 基线后）。

    Parameters
    ----------
    baseline, post : 已按受试者对齐的两个 Series（同长度、同顺序）
    levels : 分类水平顺序，如 ``['LOW', 'NORMAL', 'HIGH']``
    pct : ``'row'`` 行百分比（分母=该基线分类合计）/
          ``'col'`` 列百分比（分母=该基线后分类合计）/
          ``'total'`` 总百分比

    Returns
    -------
    DataFrame
        单元格为 ``"n (x.x%)"``；index 为基线分类，columns 为基线后分类，
        另加 ``"合计"`` 列。
    """
    ct = pd.crosstab(
        pd.Categorical(baseline, categories=levels),
        pd.Categorical(post, categories=levels),
        dropna=False,
    ).reindex(index=levels, columns=levels, fill_value=0)

    if pct == "row" or pct == "total":
        denom = ct.sum(axis=1) if pct == "row" else ct.to_numpy().sum()
    else:
        denom = ct.sum(axis=0)

    out = ct.copy().astype(object)
    for i in ct.index:
        for j in ct.columns:
            n = int(ct.loc[i, j])
            if pct == "row":
                d = int(ct.loc[i].sum())
            elif pct == "col":
                d = int(ct[j].sum())
            else:
                d = int(ct.to_numpy().sum())
            out.loc[i, j] = pct_format(n, d)
        out.loc[i, "合计"] = str(int(ct.loc[i].sum())) if pct != "col" else ""
    out.index.name = "基线"
    return out


# --------------------------------------------------------------------------
# 输出
# --------------------------------------------------------------------------
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>{title}</title>
<style>
  body {{ font-family: "Segoe UI", "Microsoft YaHei", sans-serif;
          margin: 28px; color: #1a1a1a; }}
  h1 {{ font-size: 18px; margin: 0 0 4px 0; }}
  .sub {{ color: #666; font-size: 13px; margin-bottom: 16px; }}
  table {{ border-collapse: collapse; font-size: 13px; }}
  th, td {{ border: 1px solid #c8c8c8; padding: 5px 12px; }}
  th {{ background: #eef1f5; text-align: center; font-weight: 600; }}
  td.lbl {{ text-align: left; }}
  td.num {{ text-align: center; white-space: nowrap; }}
  tr:hover td {{ background: #f7f9fb; }}
  .foot {{ margin-top: 12px; font-size: 12px; color: #666; }}
</style></head><body>
<h1>{title}</h1>
<div class="sub">{subtitle}</div>
{table}
<div class="foot">{footnote}</div>
</body></html>
"""


def to_html(df: pd.DataFrame, path: str | Path, title: str = "",
            subtitle: str = "", footnote: str = "",
            label_cols: tuple[str, ...] = ("变量", "类别", "基线", "层级")) -> Path:
    """输出带样式的 HTML 表格（适合团队评审 / 邮件汇报）。"""
    html_tbl = df.to_html(index=False, escape=False, border=0, classes="tbl")
    # 给文本列与数字列加不同的对齐样式
    for col in df.columns:
        cls = "lbl" if col in label_cols else "num"
        html_tbl = html_tbl.replace(f'<th>{col}</th>', f'<th class="{cls}">{col}</th>')
        html_tbl = html_tbl.replace(f'<td>{col}</td>', f'<td class="{cls}">{col}</td>')

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        HTML_TEMPLATE.format(title=title, subtitle=subtitle,
                             table=html_tbl, footnote=footnote),
        encoding="utf-8",
    )
    return path
