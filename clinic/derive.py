"""
clinic.derive —— 派生逻辑（与 SAS 结果对齐）

本模块集中实现"跨语言最容易出现差异"的派生逻辑，
所有函数都以**与 SAS 结果一致**为第一优先级。

包含
----
- ``sas_round`` / ``sas_round_series`` : 复刻 SAS ``ROUND`` 的舍入语义
- ``derive_agegr1``      : 年龄分组（含边界归属说明）
- ``parse_partial_date`` : 处理 ISO 8601 部分日期（2014 / 2014-01 / 2014-01-05）
- ``sas_date_to_datetime`` / ``datetime_to_sas_date`` : 与 SAS 日期数字互换
- ``derive_baseline_flag`` : 派生基线记录标记
- ``to_categorical``     : 用 Categorical 固定分组顺序（替代 SAS 的 CLASS 顺序）
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# SAS 日期基准日（SAS 里 '01JAN1960'd = 0）
SAS_EPOCH = pd.Timestamp("1960-01-01")


# --------------------------------------------------------------------------
# 舍入：Python 与 SAS 的头号差异来源
# --------------------------------------------------------------------------
def sas_round(x, unit: float = 1):
    """复刻 SAS 的 ``ROUND(x, unit)``。

    SAS 语义：
    - 第二个参数是**舍入单位**（不是小数位数）
    - 采用**四舍五入**（round half away from zero）

    Python 的 ``round()`` 采用**银行家舍入**（round half to even），
    且第二个参数是小数位数 —— 两者对临床数值都不可直接替换。

    Examples
    --------
    >>> sas_round(2.5, 1)
    3.0
    >>> sas_round(2.675, 0.01)      # Python round(2.675, 2) 会给 2.67
    2.68
    >>> sas_round(1234, 10)
    1230.0
    >>> sas_round(-2.5, 1)
    -3.0
    """
    import math
    from decimal import Decimal, ROUND_HALF_UP

    if x is None:
        return None
    try:
        if math.isnan(float(x)):
            return np.nan
    except (TypeError, ValueError):
        return None

    unit_d = Decimal(str(unit))
    if unit_d == 0:
        return float(x)
    # 用 Decimal 避免二进制浮点误差（2.675 实际存储为 2.67499999...）
    q = (Decimal(str(x)) / unit_d).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return float(q * unit_d)


def sas_round_series(s: pd.Series, decimals: int = 0) -> pd.Series:
    """向量化版本的 SAS 风格舍入（四舍五入）。

    比 ``s.apply(sas_round)`` 快得多，适合整列处理。
    ``decimals`` 是**小数位数**（与 SAS 的 unit 语义不同，这里是常用写法）。
    """
    factor = 10 ** decimals
    s = pd.to_numeric(s, errors="coerce")
    return np.sign(s) * np.floor(np.abs(s) * factor + 0.5) / factor


def sas_round_to(s: pd.Series, unit: float) -> pd.Series:
    """按 SAS 的"舍入单位"语义整列舍入（如把 1234 舍入到最近的 10）。"""
    return sas_round_series(s / unit, 0) * unit


# --------------------------------------------------------------------------
# 分组派生：边界必须显式
# --------------------------------------------------------------------------
def derive_agegr1(df: pd.DataFrame, age_var: str = "AGE",
                  out_var: str = "AGEGR1",
                  num_var: str = "AGEGR1N") -> pd.DataFrame:
    """派生年龄分组。

    ★ 边界规则（与 CDISC 试点项目 ADSL 一致，也是最常见的惯例）★

    ==========  =====================
    区间        归属
    ==========  =====================
    AGE < 65    ``'<65'``    （不含 65）
    65 ≤ AGE ≤ 80  ``'65-80'`` （**含 65、含 80**）
    AGE > 80    ``'>80'``    （不含 80）
    ==========  =====================

    .. warning::
       不要用 ``pd.cut(bins=[0, 65, 80, 200], right=False)`` 来实现！
       那样产生的区间是 ``[0,65) / [65,80) / [80,200)``，
       **AGE = 80 会落到 ``>80`` 组**，与临床惯例冲突。
       这是分组派生最隐蔽的错误之一。

    >>> import pandas as pd
    >>> derive_agegr1(pd.DataFrame({"AGE": [64, 65, 80, 81]}))["AGEGR1"].tolist()
    ['<65', '65-80', '65-80', '>80']
    """
    df = df.copy()
    a = pd.to_numeric(df[age_var], errors="coerce")
    df[out_var] = np.select([a < 65, a <= 80], ["<65", "65-80"], default=">80")
    df.loc[a.isna(), out_var] = np.nan
    if num_var:
        df[num_var] = df[out_var].map({"<65": 1, "65-80": 2, ">80": 3})
    return df


def to_categorical(s: pd.Series, levels: list, ordered: bool = True) -> pd.Series:
    """把列转成有序 Categorical —— 用来**固定分组输出顺序**。

    这是复刻 SAS ``CLASS`` / 自定义 format 输出顺序的关键：
    SAS 报表里治疗组按 SAP 指定的顺序出现，而 pandas 默认按字母序。
    不做这一步，报表的行序就会错。

    >>> to_categorical(pd.Series(["B", "A"]), ["A", "B"]).tolist()
    ['B', 'A']           # categories 顺序为 A, B，导致 groupby 按 A, B 输出
    """
    return pd.Categorical(s, categories=levels, ordered=ordered)


# --------------------------------------------------------------------------
# 日期
# --------------------------------------------------------------------------
def sas_date_to_datetime(value):
    """SAS 日期数字 → pandas Timestamp（SAS 以 1960-01-01 为 0）。"""
    return SAS_EPOCH + pd.to_timedelta(value, unit="D")


def datetime_to_sas_date(value):
    """pandas Timestamp / datetime → SAS 日期数字。"""
    return (pd.Timestamp(value) - SAS_EPOCH).days


def parse_partial_date(series: pd.Series) -> pd.DataFrame:
    """解析 ISO 8601 日期并保留"精度"信息。

    临床数据里 ``--DTC`` 常有部分日期（只知道年月）。

    ==============  ===============  ========
    输入            解析结果         精度
    ==============  ===============  ========
    ``2014-01-05``  2014-01-05       DAY
    ``2014-01``     2014-01-01       MONTH
    ``2014``        2014-01-01       YEAR
    ==============  ===============  ========

    调用方应根据精度决定是否参与计算 —— **不要直接用补全后的日期做运算**，
    否则会引入虚假精度。
    """
    s = series.astype("string").str.strip()
    precision = np.select(
        [s.str.len() >= 10, s.str.len() == 7], ["DAY", "MONTH"], default="YEAR"
    )
    filled = s.where(
        s.str.len() >= 10,
        s.str.pad(10, side="right", fillchar="0")
         .str.replace(r"^(\d{4})$", r"\1-01-01", regex=True)
         .str.replace(r"^(\d{4}-\d{2})$", r"\1-01", regex=True),
    )
    return pd.DataFrame(
        {"date": pd.to_datetime(filled, errors="coerce"), "precision": precision}
    )


def add_datetime(df: pd.DataFrame, dtc_var: str,
                 out_var: str | None = None) -> pd.DataFrame:
    """把 ISO 8601 字符日期列转成 datetime，**保留原始列**。

    保留原始值是 CDISC 的要求（原始值必须可追溯），
    同时也是 QC 时对照"原始 vs 派生"的依据。
    """
    out_var = out_var or f"{dtc_var}_DT"
    df = df.copy()
    df[out_var] = pd.to_datetime(df[dtc_var], errors="coerce")
    return df


def derive_baseline_flag(df: pd.DataFrame, date_var: str, ref_var: str,
                         by: list[str], out_var: str = "_BLFL_NEW") -> pd.DataFrame:
    """派生基线标记：**首次给药日当天或之前的最后一次测量**。

    用于数据集没有原生 ``--BLFL`` 的情况。
    ``ref_var`` 通常是 ``TRTSDT``；若为空则视为""受试者尚未给药"，
    其所有记录都参与基线候选。
    """
    df = df.copy()
    d = pd.to_datetime(df[date_var], errors="coerce")
    ref = pd.to_datetime(df[ref_var], errors="coerce") if ref_var in df.columns else pd.NaT

    cand = df[(ref.isna()) | (d <= ref)].copy()
    cand["_d"] = d[cand.index]
    idx = cand.dropna(subset=["_d"]).sort_values("_d").groupby(by)["_d"].idxmax()

    df[out_var] = "N"
    df.loc[idx.dropna(), out_var] = "Y"
    return df


# --------------------------------------------------------------------------
# 受试者标识
# --------------------------------------------------------------------------
def split_usubjid(series: pd.Series, names: list[str] | None = None) -> pd.DataFrame:
    """拆解 USUBJID（如 ``01-701-1015`` → study/site/subjid）。

    .. note::
       USUBJID 的拼接规则**因公司/项目而异**（分隔符可能是 ``-`` / ``_``，
       段数也可能是 2~4 段）。生产代码里应先做正则校验，不要盲目 split。
    """
    import re
    names = names or ["STUDY_PART", "SITE_PART", "SUBJ_PART"]
    ext = series.astype(str).str.extract(r"^(?P<a>[^-_]+)[-_](?P<b>[^-_]+)[-_](?P<c>.+)$")
    ext.columns = names
    ok = series.astype(str).str.match(r"^[^-_]+[-_][^-_]+[-_].+$")
    return ext.where(ok, np.nan)


# --------------------------------------------------------------------------
# 人群标记
# --------------------------------------------------------------------------
def derive_saffl(df: pd.DataFrame, trtsdt_var: str = "TRTSDT_DT",
                 arm_var: str = "ARM",
                 excluded_arms: tuple[str, ...] = (
                     "Screen Failure", "Not Assigned", "Not Randomized"),
                 out_var: str = "SAFFL") -> pd.DataFrame:
    """派生安全性人群标记：接受了至少一次研究药物，且不属于排除组。

    这对应 SAS 里最常见的 ``if not missing(trtsdt) and arm not in (...) then SAFFL='Y';``
    """
    df = df.copy()
    has_trt = pd.to_datetime(df[trtsdt_var], errors="coerce").notna() \
        if trtsdt_var in df.columns else pd.Series(False, index=df.index)
    bad_arm = df[arm_var].isin(excluded_arms) if arm_var in df.columns \
        else pd.Series(False, index=df.index)
    df[out_var] = np.where(has_trt & ~bad_arm, "Y", "N")
    return df
