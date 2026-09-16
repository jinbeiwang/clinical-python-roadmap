# 第 11 章 · 读取 XPT / SAS7BDAT 与临床数据结构

> **本章目标**：搞清"不装 SAS 也能读写 SAS 数据"的完整方法，
> 并理解 CDISC 数据集的通用结构——**这是后续所有实战的基础**。

---

## 11.1 三种 SAS 文件格式与对应读法

| 格式 | 扩展名 | 用途 | Python 读法 |
|---|---|---|---|
| SAS 传输格式 v5 | `.xpt` | **CDISC 提交的标准格式** | `pyreadstat.read_xport()` / `pd.read_sas(format="xport")` |
| SAS 传输格式 v8/v9 | `.xpt` | 支持长变量名、UTF-8 | `pyreadstat.read_xport()`（自动识别）|
| SAS 数据集 | `.sas7bdat` | SAS 的"原生"数据集 | `pyreadstat.read_sas7bdat()` / `pd.read_sas(format="sas7bdat")` |
| SAS 数据集目录 | `.sas7bcat` | 存自定义 format | `pyreadstat.read_sas7bcat()` |

```python
import pandas as pd
import pyreadstat

# ---- 方式 A：pandas（简单，但不带变量标签）----
df = pd.read_sas("data/raw/dm.xpt", format="xport", encoding="utf-8")

# ---- 方式 B：pyreadstat（推荐，带标签 + 值标签）----
df, meta = pyreadstat.read_xport("data/raw/dm.xpt")
df, meta = pyreadstat.read_sas7bdat("adsl.sas7bdat")

# 读永久数据集的目录（自定义 format）
df, meta = pyreadstat.read_sas7bdat("adsl.sas7bdat", catalog_file="formats.sas7bcat")
```

### `pandas.read_sas` vs `pyreadstat`：该用哪个

| | `pandas.read_sas` | `pyreadstat` |
|---|---|---|
| 依赖 | 内置 | 需 `pip install pyreadstat` |
| 变量标签 | **丢失** | **保留**（`meta.column_names_to_labels`）|
| 值标签（format） | **丢失** | **保留**（`meta.variable_value_labels`）|
| 原始类型/长度 | 无 | `meta.original_variable_types` |
| 日期时间类型 | 时间戳会转 datetime | 可控（`dates_as_pandas_datetime`）|
| 写入 XPT | 不支持 | **支持** `write_xport()` |
| 速度 | 稍快 | 稍慢 |

> 🔥 **结论**：
> **只要涉及变量标签（临床场景几乎总是涉及），就用 `pyreadstat`。**
> 需要写回 XPT 时，`pyreadstat` 是唯一选择。

---

## 11.2 一个坑：XPT 里的日期是字符串

这是从 SAS 转 pandas 时**第一个会踩的坑**。

在 SAS 里 `TRTSDT` 是数值日期（format 为 `yymmdd10.`）；
但导出成 XPT 后，**CDISC 要求日期以 ISO 8601 字符串形式存储**
（SDTM 规范：`--DTC` 变量是字符型）。

所以你读进来会是：

```python
df["TRTSDT"].dtype            # dtype('O')  —— 字符串！
df["TRTSDT"].head(3)
# 0    2014-01-02
# 1    2012-08-05
```

**必须先转换**：

```python
df["TRTSDT_DT"] = pd.to_datetime(df["TRTSDT"], errors="coerce")
df["TRTSDT_DT"].dtype         # datetime64[ns]

# 数值型变量（如 TRTDUR / AGE）如果含缺失，也会变成 float64
df.dtypes.value_counts()
# float64    28
# object     18
# int64       2
```

> 💡 **不要覆盖原变量名**。保留原始的 `TRTSDT`（字符串，对应 SDTM 的原始值），
> 另建 `TRTSDT_DT`（datetime，用于计算）。
> 这样 QC 时可以对比"原始值"和"派生值"，也符合 CDISC 的变量命名惯例。

### 为什么 `AGE` 变成 63.0 而不是 63

```python
df["AGE"].head(3)     # 63.0, 64.0, 71.0
```

原因：整列里只要有**一个缺失值**，pandas 就会把整列提升为 `float64`。
（NumPy 的 `int64` 不支持 NaN，而 `float64` 支持。）

```python
# 需要整数显示时
df["AGE"] = df["AGE"].astype("Int64")            # 大写 I = 可空整型
df["AGE"].head(3)                                 # 63, 64, 71

# 或者只在输出时报表格时格式化
print(df["AGE"].describe())
```

---

## 11.3 CDISC 数据集的通用结构

CDISC 数据集有很强的**结构性规律**，理解这些规律后，
你可以写出"通用工具函数"而不是为每个域写一套代码。

### 三张"骨架表"：DM / ADSL / ADTTE 的角色

| 数据集 | 粒度 | 作用 |
|---|---|---|
| **DM**（SDTM） | 1 行 = 1 受试者 | 人口学与关键日期（受试者层级的主表）|
| **ADSL**（ADaM） | 1 行 = 1 受试者 | **所有分析的数据基础**：人群标记 + 治疗组 + 基线值 |
| **BDS 类**（ADAE/ADVS/ADLBC/ADTTE） | 1 行 = 1 测量/事件 | 各种分析结果 |

> 🔥 **ADSL 是一切的起点**。任何分析的正确姿势都是：
> **先确定分析人群（从 ADSL 筛），再与 BDS 数据集连接。**

### 变量命名规律（记住了就能"猜"出变量含义）

| 前缀/后缀 | 含义 | 例子 |
|---|---|---|
| `USUBJID` | 唯一受试者标识（跨研究唯一）| `01-701-1015` |
| `SUBJID` | 研究内受试者编号 | `1015` |
| `STUDYID` | 研究标识 | `CDISCPILOT01` |
| `DOMAIN` | 域缩写（2 字符）| `DM`、`AE`、`VS` |
| `--SEQ` | 域内序号 | `AESEQ`、`VSSEQ` |
| `--DTC` | 日期/时间（**字符型 ISO 8601**）| `AESTDTC`、`VSDTC` |
| `--DY` | 相对研究参考起始日的天数 | `AESTDY`、`VSDY` |
| `--TESTCD` | 检查项目编码 | `VSTESTCD='SYSBP'` |
| `--ORRES` | 原始结果（字符）| `VSORRES` |
| `--STRESC` | 标准化结果（字符）| `VSSTRESC` |
| `--STRESN` | 标准化结果（数值）| `VSSTRESN` |
| `--BLFL` | 基线记录标记（`Y`/空）| `VSBLFL`、`ABLFL` |
| `TRT01P` | 计划治疗（Period 1）| `Placebo` |
| `TRT01A` | 实际治疗 | `Xanomeline High Dose` |
| `TRTP` / `TRTA` | ADaM 里的计划/实际治疗 | 同上 |
| `SAFFL` / `ITTFL` / `EFFFL` | 人群标记（`Y`/`N`）| `SAFFL='Y'` |
| `AVAL` | 分析值（数值）| |
| `PARAMCD` / `PARAM` | 分析参数编码 / 名称 | `SYSBP` / `Systolic Blood Pressure` |
| `CNSR` | 删失标记（生存分析，**1=删失**）| `ADTTE` |
| `AGEGR1` / `AGEGR1N` | 年龄分组（字符版 / 数值版）| `<65` / `1` |
| `TRTEMFL` | 治疗中出现标记 | `ADAE` |

```python
# 利用命名规律写出"通用域迭代器"
BDS_PATTERNS = {
    "subject":   "USUBJID",
    "param":     lambda c: [x for x in c if x.startswith("PARAM")],
    "result":    lambda c: [x for x in c if x in ("AVAL", "AVALC")],
    "baseline":  lambda c: [x for x in c if x in ("BASE", "ABLFL")],
    "change":    lambda c: [x for x in c if x in ("CHG", "PCHG")],
    "visit":     lambda c: [x for x in c if x.startswith("VISIT") or x == "AVISIT"],
    "flag":      lambda c: [x for x in c if x.endswith("FL")],
    "timing":    lambda c: [x for x in c if x.endswith(("DT", "DTM", "DY"))],
}
```

### USUBJID 的解剖

`01-701-1015` 是一个典型的 USUBJID，由三段组成：

```python
USUBJID_STRUCT = {
    "01":   "研究编号后缀（CDISCPILOT01 → 01）",
    "701":  "中心编号（SITEID）",
    "1015": "受试者编号（SUBJID）",
}
```

```python
# 拆解 USUBJID（用正则更安全，因为分隔符可能变化）
import re
m = re.match(r"^(?P<study>[^-]+)-(?P<site>[^-]+)-(?P<subj>.+)$", "01-701-1015")
m.groupdict()   # {'study': '01', 'site': '701', 'subj': '1015'}

# 或直接 split（结构固定时更简单）
df[["STUDY", "SITEID2", "SUBJID2"]] = df["USUBJID"].str.split("-", expand=True)
```

> ⚠️ **不要假设 USUBJID 总是三段**。不同公司的拼接规则不同
> （有的是 `STUDY-SITE-SUBJ`，有的是 `STUDY_SITE_SUBJ`，有的还有国家码）。
> **生产代码里应该用正则 + 校验**，而不是硬编码 `split("-")`。

---

## 11.4 通用数据读取工具（可直接用）

把这一节的内容固化成你自己的 `clinic/io.py`：

```python
"""clinic/io.py —— 临床数据读取工具。"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd
import pyreadstat

log = logging.getLogger(__name__)

# CDISC 里常见的"伪缺失"标记
NA_TOKENS = {"", " ", "NA", "N/A", "NULL", "null", ".", "UNKNOWN", "Unknown"}


def read_xpt(path: str | Path, keep_labels: bool = True,
             as_datetime: bool = False) -> pd.DataFrame:
    """读取 SAS XPORT (.xpt)，可选保留变量标签。

    Parameters
    ----------
    path : 文件路径
    keep_labels : 是否把变量标签存到 df.attrs['labels']
    as_datetime : 是否自动把 --DTC / *DT 结尾的字符列转成 datetime
                  （默认 False，避免误转部分日期）
    """
    path = Path(path)
    df, meta = pyreadstat.read_xport(str(path))

    if keep_labels:
        df.attrs["labels"] = meta.column_names_to_labels
        df.attrs["source"] = str(path)

    if as_datetime:
        for c in df.columns:
            if c.endswith(("DTC", "DT")) and df[c].dtype == "object":
                df[c] = pd.to_datetime(df[c], errors="coerce")

    log.info("读取 %-16s %7d 行 x %2d 列", path.name, *df.shape)
    return df


def read_sas7bdat(path, catalog=None, keep_labels=True):
    """读取 SAS 数据集（.sas7bdat）。"""
    df, meta = pyreadstat.read_sas7bdat(str(path), catalog_file=catalog)
    if keep_labels:
        df.attrs["labels"] = meta.column_names_to_labels
        df.attrs["value_labels"] = getattr(meta, "variable_value_labels", {}) or {}
    return df


def read_any(path, **kw) -> pd.DataFrame:
    """按扩展名自动选择读取方式。"""
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix == ".xpt":
        return read_xpt(p, **kw)
    if suffix == ".sas7bdat":
        return read_sas7bdat(p, **kw)
    if suffix == ".csv":
        return pd.read_csv(p, na_values=list(NA_TOKENS), keep_default_na=True)
    if suffix in (".xlsx", ".xls"):
        return pd.read_excel(p)
    if suffix in (".parquet", ".pq"):
        return pd.read_parquet(p)
    raise ValueError(f"不支持的文件类型: {suffix}")


def load_domains(data_dir, domains: list[str], ext: str = "auto") -> dict[str, pd.DataFrame]:
    """批量加载多个域；单个失败不影响其他（见第 04 章 的容错模式）。"""
    data_dir = Path(data_dir)
    out: dict[str, pd.DataFrame] = {}
    for dom in domains:
        # 按优先级找文件：csv -> xpt -> sas7bdat
        for suffix in ([".csv"] if ext == "csv" else
                       [".xpt"] if ext == "xpt" else
                       [".csv", ".xpt", ".sas7bdat"]):
            f = data_dir / f"{dom}{suffix}"
            if f.exists():
                try:
                    out[dom] = read_any(f)
                    break
                except Exception as e:  # noqa: BLE001
                    log.error("读取 %s 失败: %s", f.name, e)
        else:
            log.warning("未找到 %s 的数据文件（已尝试 csv/xpt/sas7bdat）", dom)
    return out


def get_labels(df: pd.DataFrame) -> dict[str, str]:
    """取变量标签；没有则返回 {变量: 变量} 的恒等映射。"""
    return df.attrs.get("labels") or {c: c for c in df.columns}
```

### 用法

```python
from clinic.io import load_domains, read_xpt, get_labels

# 一次性加载多个域
src = load_domains("data/raw", ["dm", "ae", "adsl", "adae", "ex", "ds"])

# 或者从 CSV 样本（更快，无需下载）
src = load_domains("data/samples", ["dm", "ae", "adsl", "adae"], ext="csv")

adsl = src["adsl"]
labels = get_labels(adsl)
print(labels["AGE"])    # 'Age'  —— SAS 的 label 被保留了
```

---

## 11.5 本项目的实际数据规模（建立数字敏感度）

以下是 CDISC 试点项目 CDISCPILOT01 的真实规模。
**记住这些数字**，写代码时可以快速判断"读进来要多久 / 内存够不够"。

| 数据集 | 行数 | 列数 | XPT 大小 | 说明 |
|---|---:|---:|---:|---|
| DM | 306 | 25 | 111 KB | 全部受试者（含筛选失败）|
| ADSL | **254** | 48 | 115 KB | **随机化人群**（比 DM 少 52 人）|
| AE | 1,191 | 35 | 1.5 MB | 不良事件，涉及 **225** 个受试者 |
| ADAE | 1,191 | 55 | 714 KB | AE 的 ADaM 版本 |
| ADTTE | 254 | 26 | 92 KB | 时间到事件 |
| EX | 591 | 17 | 87 KB | 暴露/给药记录 |
| DS | 596 | 13 | 147 KB | 处置 |
| CM | 7,510 | 21 | 3.8 MB | 合并用药 |
| MH | 1,818 | 19 | 1.6 MB | 病史 |
| SV | 3,559 | 8 | 287 KB | 访视 |
| VS | 29,643 | 24 | 24 MB | 生命体征 |
| LB | ~58,700 | – | 34 MB | 实验室检查 |
| QS | – | – | 34 MB | 问卷（ADAS-Cog 等）|

> 🔥 **DM(306) ≠ ADSL(254) 是本项目里最重要的一课**：
> DM 包含所有被筛选/入组的受试者，而 ADSL 只包含**随机化并接受治疗**的。
> **任何分析的基数都应以 ADSL 为准**——直接拿 DM 的 306 去算分母，
> 会得到错误的百分比。这是新手（以及不细心的老手）常犯的错。

```python
# 快速验证这个差异
dm = read_xpt("data/raw/dm.xpt")
adsl = read_xpt("data/raw/adsl.xpt")

only_dm = set(dm["USUBJID"]) - set(adsl["USUBJID"])
print(f"DM {len(dm)} 人，ADSL {len(adsl)} 人，只在 DM 中的 {len(only_dm)} 人")
# DM 306 人，ADSL 254 人，只在 DM 中的 52 人

# 看看这些人为什么不在 ADSL 里（通常是筛选失败）
print(dm.loc[dm["USUBJID"].isin(only_dm),
             ["USUBJID", "ARMCD", "ARM", "RFSTDTC"]].head())
```

---

## 11.6 内存与性能：数据规模多大要小心

```python
# 查看 DataFrame 的真实内存占用（含字符串）
df.memory_usage(deep=True).sum() / 1024**2      # MB

# 优化技巧 1：把低基数的字符串列转成 category（临床数据里效果极好）
for c in ["DOMAIN", "SEX", "RACE", "AESEV", "AESER", "TRT01P"]:
    if c in df.columns:
        df[c] = df[c].astype("category")
# 通常能省 50-80% 内存

# 优化技巧 2：数值降位（如果范围允许）
df["AESEQ"] = df["AESEQ"].astype("int16")

# 优化技巧 3：大文件用分块读取（对应 SAS 的 data _null_ 扫描）
for chunk in pd.read_csv("huge.csv", chunksize=100_000):
    process(chunk)

# 优化技巧 4：Parquet 代替 CSV（体积小 3-5 倍，读取快 5-10 倍）
df.to_parquet("adsl.parquet", index=False)
df = pd.read_parquet("adsl.parquet")
```

> 💡 **Parquet 的额外好处**：它**保留数据类型**（datetime 不会退化成字符串）、
> 支持列式读取（只读需要的列）。对于反复使用的中间数据集，
> **建议在 CSV 之外同时缓存一份 Parquet**。

---

## 11.7 动手练习

1. **读取对比**：分别用 `pd.read_sas()` 和 `pyreadstat.read_xport()`
   读 `data/raw/adsl.xpt`，比较两者的列名、dtype，以及后者多出来的元数据。

2. **日期类型**：读 ADSL 后，统计有多少列是 `object`（字符串）类型，
   其中哪些是日期（以 `DT` / `DTC` 结尾）？把它们转成 datetime。

3. **USUBJID 拆解**：用正则把 `USUBJID` 拆成 study / site / subject 三列，
   并检查 ADSL 中 `SITEID` 列与拆出的 site 是否一致。

4. **DM vs ADSL**：找出"在 DM 但不在 ADSL"的受试者，
   打印他们的 `ARM` / `ARMCD`，并推断原因。

5. **内存优化**：读 `data/samples/vs_bp.csv`，记录初始内存占用；
   把 `VSTESTCD` / `VSTEST` / `VISIT` / `VSBLFL` 转成 `category`，
   再记录一次，计算节省比例。

---

**上一章 ←** [第 10 章 · 日期、缺失值、格式与数据质量](10-日期缺失值格式与数据质量.md)
**下一章 →** [第 12 章 · SDTM 数据处理实战](12-SDTM数据处理实战.md)
