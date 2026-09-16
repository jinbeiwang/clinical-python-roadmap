# SAS → Python 速查表（临床统计编程版）

> 面向**有 SAS 经验、正在转 Python** 的临床统计程序员。
> 不是通用 Python 教程 —— 只收录**临床数据操作中真正会用到的对照**，
> 以及那些"看起来一样、结果不同"的坑。
>
> 验证环境：Python 3.13 / pandas 3.0 / numpy 2.5（但写法对 pandas 2.x 同样适用）。

**目录**

- [0. 心智模型：先转换脑子，再转换代码](#0-心智模型先转换脑子再转换代码)
- [1. 数据类型与缺失值](#1-数据类型与缺失值)
- [2. 读入与写出](#2-读入与写出)
- [3. 变量操作（DATA 步对照）](#3-变量操作data-步对照)
- [4. 条件逻辑（IF-THEN-ELSE）](#4-条件逻辑if-then-else)
- [5. 排序与去重](#5-排序与去重)
- [6. 合并（MERGE）](#6-合并merge)
- [7. 分组汇总（BY 组处理 / PROC MEANS / FREQ / TABULATE）](#7-分组汇总by-组处理--proc-means--freq--tabulate)
- [8. 转置与重塑](#8-转置与重塑)
- [9. 日期与时间](#9-日期与时间)
- [10. 字符串函数](#10-字符串函数)
- [11. 数学、舍入与统计](#11-数学舍入与统计)
- [12. 格式与标签（FORMAT / LABEL）](#12-格式与标签format--label)
- [13. 宏、循环与批处理](#13-宏循环与批处理)
- [14. 常见坑 Top 20](#14-常见坑-top-20)

---

## 0. 心智模型：先转换脑子，再转换代码

这是**唯一**比你记语法更重要的一节。

| SAS 概念 | pandas 对应 | 关键差异 |
|---|---|---|
| 数据集（dataset） | `DataFrame` | 结构类似，但 pandas 有**索引（index）**，SAS 没有 |
| 观测（observation） | 行（row） | —— |
| 变量（variable） | 列（column） | pandas 一列只有一个 dtype，SAS 也一样 |
| 变量标签 / 格式 | `df.attrs["labels"]` | **不会自动保留**，读 XPT 时才拿到，存 CSV 会丢 |
| `WORK` 库 | 内存里的变量 | 没有"库"的概念，`import` 进来就是对象 |
| `LIBNAME` | 文件路径 | 路径就是路径，没有逻辑库映射 |
| `DATA` 步 | 向量化表达式 / `assign` | ★ **不要再写"逐行循环"** |
| `PROC SORT` | `sort_values` / `groupby` | ★ 很多 SAS 步骤在 pandas 里是**隐含**的 |
| `BY` 组处理 + `RETAIN` | `groupby().agg()/transform()` | ★ 不需要 RETAIN，聚合一步到位 |
| `PROC SQL` | `merge` / `query` / `groupby` | pandas 的 `merge` 更像 SQL 的 JOIN |
| `%macro` | 函数 + `for` 循环 | 不需要宏语言，写真正的函数 |
| `ODS` | `to_csv` / `to_excel` / `to_html` | 输出是显式调用，没有"全局开关" |

**三句话总结心智转变：**

1. **SAS 想"一行一行怎么处理"，pandas 想"整列怎么变换"。**
   `if x > 5 then y = 1; else y = 0;` → `df["y"] = (df["x"] > 5).astype(int)`
2. **SAS 里很多动作是"过程（PROC）"，pandas 里是"方法（.method）"。**
   排序不是 `PROC SORT`，是 `df.sort_values()`。
3. **SAS 里缺失值是一种值（`.`），pandas 里缺失值是"没有值"（`NaN`）。**
   这是绝大多数静默错误的根源。

---

## 1. 数据类型与缺失值

| SAS | Python / pandas | 注意 |
|---|---|---|
| 数值（numeric，8 字节） | `float64` / `int64` | SAS 只有一种数值类型 |
| 字符（character） | `object` / `string` | pandas 3 起推荐 `string` dtype |
| 缺失数值 `.` | `np.nan`（float）或 `pd.NA` | ★ 整列一旦有缺失，int 就变 float |
| 缺失字符 `''` | `pd.NA`（需显式清洗） | ★ pandas 里空串是**合法值**，不是缺失 |
| `IF x = .` | `df["x"].isna()` | 用 `.isna()` / `.notna()`，**不要**用 `== np.nan` |
| `IF x = ''` | `df["x"].isna()`（先 `clean_missing`） | 见下 |
| `COALESCE(a, b)` | `a.fillna(b)` | —— |
| `IFN(x, a, b)` | `np.where(x, a, b)` | ★ 见第 14 节第 3 条 |
| `CALL MISSING(x)` | `df["x"] = np.nan` | —— |
| 数值缺失参与运算 | 结果也是缺失 | SAS 与 pandas 一致 |
| `MAX(of a b)`（忽略缺失） | `s.max(skipna=True)` | 默认就是忽略 |
| `NMISS(of a b)` | `df[cols].isna().sum(axis=1)` | —— |

### 清洗"伪缺失"

CDISC 数据里 `''`、`'NA'`、`'.'`、`'UNKNOWN'` 都可能表示缺失。
SAS 里 `''` 天然是缺失，pandas 里必须显式处理：

```python
from clinic import io as cio
df = cio.read_any("data/samples/dm.csv")   # 内部已调用 clean_missing()
```

自己写的话：

```python
NA_TOKENS = {"", " ", ".", "NA", "N/A", "NULL", "NaN", "UNKNOWN", "UNK", "-"}
for col in df.columns:
    if not pd.api.types.is_numeric_dtype(df[col]):
        s = df[col].astype("string").str.strip()
        df[col] = s.mask(s.isin(NA_TOKENS))
```

### ★★ 缺失值判断：`&`/`|` 的优先级陷阱

```python
# ❌ 错：& 的优先级高于 ==，实际是 a == (b & c)
df[df["A"] == "Y" & df["B"] == "N"]

# ✅ 对：每个条件都加括号
df[(df["A"] == "Y") & (df["B"] == "N")]
```

SAS 里 `if a='Y' and b='N'` 是关键字，不会踩这个坑；
Python 里 `and` / `or` 对 Series **不能**用（会报 `ValueError`），
必须用 `&` / `|` / `~`，于是就有了优先级问题。

---

## 2. 读入与写出

| SAS | Python | 说明 |
|---|---|---|
| `libname x xport 'a.xpt';` | `pyreadstat.read_xport()` | 或用 `clinic.io.read_xpt()` |
| `set lib.a;` | `pd.read_sas()` / `pyreadstat.read_sas7bdat()` | `.sas7bdat` 读取 |
| `proc import file='a.csv'` | `pd.read_csv(..., low_memory=False)` | —— |
| `proc export` | `df.to_csv(idx=False, encoding="utf-8-sig")` | ★ `encoding="utf-8-sig"` 让 Excel 不乱码 |
| `proc contents` | `df.info()` / `df.dtypes` | 变量标签在 `df.attrs["labels"]` |
| `ods excel file=...` | `pd.ExcelWriter` | 多 sheet 见 case06 |
| `libname x '路径';` | `Path("路径")` | 用 `pathlib`，跨平台 |
| `FILENAME` + `%sysfunc(fileexist)` | `Path(p).exists()` | —— |
| `INFILE` + `INPUT`（定长） | `pd.read_fwf()` | 定宽文本 |
| `PROC DATASETS`（删数据集） | `del df` | 或 `df = None` 后等 GC |

```python
import pandas as pd
from pathlib import Path

df = pd.read_csv("data/samples/adsl.csv", low_memory=False)   # 全部当字符串读：dtype=str
df.to_csv("outputs/out.csv", index=False, encoding="utf-8-sig")
df.to_excel("outputs/out.xlsx", index=False, sheet_name="表1")
```

> 💡 **`dtype=str` 的用途**：做 QC 时把所有列都当字符串读，
> 可以避免"`'1.0'` 和 `'1'` 被读成同一个数"这类问题，
> 也避免长数字（如 `AESEQ`）被读成浮点后出现 `.0`。

### 内存与性能

| 场景 | 做法 |
|---|---|
| 文件很大 | `pd.read_csv(..., chunksize=500_000)` 分块处理 |
| 只想看前几行 | `pd.read_csv(..., nrows=10)` |
| 只想要某些列 | `pd.read_csv(..., usecols=[...])` |
| 想加速 | `engine="pyarrow"` 或先转 `parquet` |
| 别这么做 | ❌ 用 `for i in range(len(df))` 遍历行 |

---

## 3. 变量操作（DATA 步对照）

| SAS | pandas |
|---|---|
| `y = x;` | `df["y"] = df["x"]` |
| `y = x * 2;` | `df["y"] = df["x"] * 2` |
| `y = x + z;` | `df["y"] = df["x"] + df["z"]` |
| `y = SUM(x, z);` | `df[["x","z"]].sum(axis=1)`（★ 缺失当 0） |
| `y = MIN(of x z);` | `df[["x","z"]].min(axis=1)` |
| `DROP x;` | `df.drop(columns=["x"])` |
| `KEEP x y;` | `df[["x","y"]]` |
| `RENAME x=y;` | `df.rename(columns={"x":"y"})` |
| `PROC SQL; SELECT ... AS y` | `df.assign(y=...)` 或 `df.rename(...)` |
| `x = LAG(y);` | `df["y"].shift(1)`（组内：`groupby().shift()`） |
| `RETAIN total 0; total + x;` | `df["x"].cumsum()` |
| `x = _N_;` | `df.reset_index(drop=True).index + 1` |
| `IF FIRST.pt then ...` | `df.groupby("pt").cumcount() == 0` |
| `IF LAST.pt then ...` | `df.groupby("pt").cumcount(ascending=False) == 0` |
| `_ERROR_` / `_N_` 调试 | `df.head()`、`df.info()`、`df.describe()` |

```python
# SAS:  data want; set have; y = x*2; keep y x; run;
df["y"] = df["x"] * 2

# SAS:  if first.usubjid then seq = 1; else seq + 1;
df["seq"] = df.groupby("USUBJID").cumcount() + 1

# SAS:  lag 用法（取上一次的 AE 起始日）
df["prev_dt"] = df.groupby("USUBJID")["ASTDT"].shift(1)
```

---

## 4. 条件逻辑（IF-THEN-ELSE）

| SAS | pandas |
|---|---|
| `IF x > 5 THEN y=1; ELSE y=0;` | `np.where(df["x"]>5, 1, 0)` |
| 多分支 if-else | `np.select(conds, choices, default=...)` |
| `y = IFN(x>5, 'H', 'L');` | `np.where(...)`（★ 见坑 3） |
| `SELECT (x); WHEN (1) ...` | `np.select` / `df["x"].map({...})` |
| `IF x IN ('A','B')` | `df["x"].isin(["A","B"])` |
| `IF x BETWEEN a AND b` | `df["x"].between(a, b)`（★ 含端点，与 SAS 一致） |
| `y = x > 5;`（0/1） | `(df["x"]>5).astype(int)` |
| 只保留满足条件的行 | `df[cond]` |

```python
import numpy as np

# 单分支
df["FLAG"] = np.where(df["AGE"] >= 65, "Y", "N")

# 多分支：★ 顺序很重要 —— 前面的条件先匹配（等价于 IF/ELSE IF）
cond = [df["AESEV"] == "MILD",
        df["AESEV"] == "MODERATE",
        df["AESEV"] == "SEVERE"]
df["SEVN"] = np.select(cond, [1, 2, 3], default=np.nan)   # 注意 default 的类型

# 分类映射（等价于 PROC FORMAT 的应用）
df["SEX2"] = df["SEX"].map({"M": "Male", "F": "Female"})
df["RACEN"] = df["RACE"].map({"WHITE": 1, "BLACK OR AFRICAN AMERICAN": 2})
```

> ⚠️ **`np.select` 的 `default` 类型**：如果 `choices` 是字符串、
> `default` 是 `np.nan`，在 numpy 2 上会直接报
> `DTypePromotionError`（不能把 str 和 float 提升到同一 dtype）。
> 解决：先用字符串 `""` 或 `"-"` 做 default，再把空串改成 `pd.NA`。

---

## 5. 排序与去重

| SAS | pandas |
|---|---|
| `PROC SORT DATA=a; BY x y;` | `df.sort_values(["x","y"])` |
| `BY DESCENDING y;` | `df.sort_values("y", ascending=False)` |
| 多列不同方向 | `ascending=[True, False]` |
| `PROC SORT NODUPKEY;` | `df.drop_duplicates(subset=["x"], keep="first")` |
| `PROC SORT NODUP;`（整行） | `df.drop_duplicates()` |
| 保留重复中的第一条 | `keep="first"`（与 SAS 默认一致） |
| 按某列最大/最小取一条 | `df.sort_values("v").drop_duplicates("k", keep="last")` |
| `PROC SQL` 取最大行 | `df.loc[df.groupby("k")["v"].idxmax()]` |

```python
# SAS: PROC SORT DATA=ex OUT=ex2; BY usubjid descending exseq; run;
ex2 = ex.sort_values(["USUBJID", "EXSEQ"], ascending=[True, False])

# SAS: 每个受试者取最后一次记录
last = df.sort_values("VISITNUM").drop_duplicates("USUBJID", keep="last")

# 每个受试者取最差等级（L/H 优先于 N）—— 安全性分析的常见做法
rank = df["ANRIND"].map({"N": 0, "L": 1, "H": 1}).fillna(-1)
worst = df.assign(_r=rank).sort_values("_r").drop_duplicates("USUBJID", keep="last")
```

> 💡 **`sort_values` 的稳定性**：默认 `kind="quicksort"`（不稳定）。
> 需要"相同键保持原顺序"时显式写 `kind="stable"` ——
> 这一点在处理有序列（如 `AESEQ`）时很关键。

---

## 6. 合并（MERGE）

⚠️ **这是最容易出错的一块。** SAS 的 `MERGE` 与 pandas 的 `merge` 语义**不同**。

| SAS | pandas |
|---|---|
| `MERGE a b; BY usubjid;` | `a.merge(b, on="USUBJID")` ← 默认 `how="inner"` |
| `MERGE a(IN=x) b; ... IF x;` | `a.merge(b, how="left")` |
| `IF x AND y;`（仅共有） | `how="inner"` |
| `IF x OR y;`（全集） | `how="outer"` |
| 右表有一方独有的行 | `how="right"` |
| 不同变量名连接 | `left_on="a", right_on="b"` |
| 多变量连接 | `on=["USUBJID","AESEQ"]` |
| 只保留左表全部列 | 加 `suffixes=("", "_b")` 或先 `b[["key","val"]]` |
| 笛卡尔积 | `how="cross"` |
| `UPDATE` 语句 | `df.update()` 或 `combine_first()` |

### ★★ 三个致命差异

```python
# 差异 1：默认 how 不同
#   SAS:  data both; merge a b; by k; run;   → 只会保留"两边都有"的行（像 inner）
#   但注意：SAS 的 merge 是"按 BY 组配对"，不是 SQL JOIN。
a.merge(b, on="k")             # inner —— 与 SAS merge 最接近
a.merge(b, on="k", how="left") # 保留左表全部

# 差异 2：右表主键重复 → 行数成倍膨胀
#   SAS 里右表有重复 BY 键会报 NOTE 并可能出错；
#   pandas 里会静默做"笛卡尔积"，行数悄悄变多。
print(len(a), len(b), len(a.merge(b, on="k")))
# ★ 防御：合并前先检查右表主键唯一性
assert not b.duplicated(subset=["USUBJID"]).any(), "右表主键不唯一！"

# 差异 3：pandas 有索引，SAS 没有
#   merge 之后索引会重置，别依赖原来的索引做事
```

### 合并后 QC 必查

```python
before, after = len(a), len(a.merge(b, on="k", how="left"))
print(f"行数变化：{before} → {after}")     # 用 left join 时不应该变多
```

---

## 7. 分组汇总（BY 组处理 / PROC MEANS / FREQ / TABULATE）

| SAS | pandas |
|---|---|
| `PROC MEANS; CLASS trt; VAR age;` | `df.groupby("TRT01P")["AGE"].agg(["count","mean","std"])` |
| `PROC FREQ; TABLES sex*trt;` | `pd.crosstab(df["SEX"], df["TRT01P"])` |
| `PROC FREQ`（含缺失） | `df["SEX"].value_counts(dropna=False)` |
| `PROC SUMMARY; OUTPUT OUT=` | `.agg(...).reset_index()` |
| `CLASS` + `WAY` 组合 | `df.groupby(["A","B"]).agg(...)` |
| `PROC TABULATE` | `pd.pivot_table(...)` + 手工排版 |
| `BY trt;` + `RETAIN` 累计 | `df.groupby("TRT01P")["X"].transform("sum")` |
| `count(distinct usubjid)` | `df.groupby("TRT01P")["USUBJID"].nunique()` |
| `PROC RANK` | `df["x"].rank()` |
| 每组行数 | `df.groupby("g").size()` |

```python
# SAS: PROC MEANS N MEAN STD MIN MAX; CLASS trt01p; VAR age;
out = (df.groupby("TRT01P")["AGE"]
         .agg(N="count", Mean="mean", SD="std", Min="min", Max="max")
         .round(2).reset_index())

# ★ SD 的 ddof：pandas 与 SAS 都是样本标准差（ddof=1），默认就一致
#   （numpy 的 np.std 默认 ddof=0，与 SAS 不一致，别混用）

# 受试者层级计数（AE 表的标准做法：同一受试者同一事件只计一次）
n_subj = (df.drop_duplicates(["USUBJID", "AEBODSYS"])
            .groupby("AEBODSYS")["USUBJID"].nunique())

# 组内占比（对应 SAS 的 PROC FREQ 列百分比）
ct = pd.crosstab(df["AESEV"], df["TRT01P"])
pct = ct / ct.sum(axis=0) * 100          # 列百分比
pct_row = ct.div(ct.sum(axis=1), axis=0) * 100   # 行百分比
```

### ★ 保留空分组（SAS 的 `preloadfmt` / `COMPLETEFYPES`）

pandas 默认会把**没有数据的组直接省略**，而 SAS 报表里它们应该显示为 0。
解决办法是用**有序 Categorical** 固定分类水平：

```python
TRT = ["Placebo", "Xanomeline Low Dose", "Xanomeline High Dose"]
df["TRT01P"] = pd.Categorical(df["TRT01P"], categories=TRT, ordered=True)

# 这样 groupby 会按 TRT 的顺序输出，且空组保留（配合 observed=False）
g = df.groupby("TRT01P", observed=False)["AGE"].agg(["count","mean"])
```

> ⚠️ 不做这一步会有**两个后果**：
> 1. 行序按字母序排（`High Dose` 会跑到 `Low Dose` 前面）；
> 2. 没有数据的组整行消失，报表少一行。

---

## 8. 转置与重塑

| SAS | pandas |
|---|---|
| `PROC TRANSPOSE` | `df.pivot()` / `df.melt()` |
| 长 → 宽 | `df.pivot(index=..., columns=..., values=...)` |
| 长 → 宽（聚合重复值） | `df.pivot_table(index, columns, values, aggfunc="mean")` |
| 宽 → 长 | `df.melt(id_vars=[...], value_vars=[...])` |
| `PROC TRANSPOSE` 多变量 | `pd.wide_to_long()` |
| 堆叠/合并列 | `pd.concat([df1, df2], axis=0)` |
| 横向合并列 | `pd.concat([df1, df2], axis=1)` |
| `SET a b c;` | `pd.concat([a, b, c], ignore_index=True)` |

```python
# SAS: PROC TRANSPOSE DATA=vs OUT=wide; BY usubjid; ID visit; VAR aval;
wide = vs.pivot_table(index="USUBJID", columns="AVISIT",
                      values="AVAL", aggfunc="mean").reset_index()

# 宽 → 长（对应 SAS 里"一堆 PROC TRANSPOSE 反过来"的活）
long = wide.melt(id_vars=["USUBJID"], var_name="AVISIT", value_name="AVAL")
```

---

## 9. 日期与时间

| SAS | pandas |
|---|---|
| `'01JAN2020'd` | `pd.Timestamp("2020-01-01")` |
| 日期 = 自 1960-01-01 起的天数 | `(ts - pd.Timestamp("1960-01-01")).days` |
| `TODAY()` | `pd.Timestamp.today().normalize()` |
| `datepart(dt)` / `timepart(dt)` | `.dt.normalize()` / `.dt.time` |
| `intck('day', a, b)` | `(b - a).dt.days` |
| `intck('month', a, b)` | 需自己算（SAS 的 intck 是"跨越的月份分界数"） |
| `intnx('month', d, 1)` | `d + pd.DateOffset(months=1)` |
| `mdy(m, d, y)` | `pd.Timestamp(year=y, month=m, day=d)` |
| `year(d)` / `month(d)` / `day(d)` | `d.year` / `d.month` / `d.day` |
| `put(d, yymmdd10.)` | `d.strftime("%Y-%m-%d")` |
| `input(s, yymmdd10.)` | `pd.to_datetime(s, format="%Y-%m-%d")` |
| ISO 8601 字符日期 `--DTC` | `pd.to_datetime(df["AESTDTC"], errors="coerce")` |
| `datdif()`（按 30 天月） | 无直接对应，需自己实现 |
| 天数 → 周数 | `days // 7`（★ ADaM 里 56 天 = 8 周） |

```python
# 核心：一次转成 datetime，之后一直用 datetime 运算
df["TRTSDT"] = pd.to_datetime(df["TRTSDT"], errors="coerce")   # errors="coerce" 关键
df["TRTEDT"] = pd.to_datetime(df["TRTEDT"], errors="coerce")

# ★ 时长必须 +1（首尾都算）—— 与 SAS 的 TRTDUR = TRTEDT - TRTSDT + 1 一致
df["TRTDUR"] = (df["TRTEDT"] - df["TRTSDT"]).dt.days + 1

# 格式化成字符串输出
df["TRTSDT_c"] = df["TRTSDT"].dt.strftime("%Y-%m-%d")
```

### ★ 部分日期（Partially Complete Date）

临床数据的 `--DTC` 常只有年月（`2014-01`）或只有年（`2014`）。
`pd.to_datetime` 会把它们补成 `2014-01-01`，**引入虚假精度**。

```python
from clinic.derive import parse_partial_date
res = parse_partial_date(df["DMDTC"])
# res["date"]      解析结果（补全后的日期，仅供排序/展示）
# res["precision"] "DAY" / "MONTH" / "YEAR"
# ★ 千万不要拿补全后的日期直接做运算，先用 precision 过滤
```

### ★ 时区

SAS 的 datetime 没有时区概念，pandas 有。
读 ISO 8601 带 `Z` 或 `+08:00` 的字符串时会带时区，
与 naive 时间相减会报错 —— 用 `dt.tz_localize(None)` 统一。

---

## 10. 字符串函数

| SAS | pandas |
|---|---|
| `strip(s)` / `trim(s)` | `s.str.strip()` |
| `upcase(s)` | `s.str.upper()` |
| `lowcase(s)` | `s.str.lower()` |
| `propcase(s)` | `s.str.title()` |
| `tranwrd(s, 'a', 'b')` | `s.str.replace("a", "b", regex=False)` ← ★ 默认已是字面量 |
| `substr(s, 1, 3)` | `s.str[:3]` |
| `substr(s, 3)` | `s.str[2:]` |
| `length(s)` | `s.str.len()` |
| `scan(s, 2, '-')` | `s.str.split("-").str[1]` |
| `index(s, 'x')` | `s.str.find("x")` |
| `find(s, 'x')` | `s.str.contains("x", regex=False)` |
| `catx('-', a, b)` | `a.str.cat(b, sep="-")`（或 `a + "-" + b`） |
| `compress(s, ' ')` | `s.str.replace(" ", "", regex=False)` |
| `prxmatch('/re/', s)` | `s.str.contains(r"re")`（默认 regex=True） |
| `prxchange` | `s.str.replace(r"re", "x")` |
| `prxparse` 捕获组 | `s.str.extract(r"(?P<name>...)")` |
| `symexist` 等宏函数 | 无对应（不需要） |
| 首字符 | `s.str[:1]` |
| 倒序取字符 | `s.str[-1]` |

```python
# ★ 正则开关（最容易搞混的地方）
s.str.replace(".", "X")                 # regex=False（pandas ≥ 2.0 默认）→ 只换真正的点
s.str.replace(r"\.", "X", regex=True)   # 显式正则
s.str.contains("A+B")                   # regex=True（默认）→ 把 + 当量词！
s.str.contains("A+B", regex=False)      # 想找字面量必须显式关掉

# 拆解 USUBJID（01-701-1015 → 三部分）
ext = df["USUBJID"].str.extract(r"^(?P<study>[^-_]+)[-_](?P<site>[^-_]+)[-_](?P<subj>.+)$")

# 取正常性指示符的首字母（'NORMAL' → 'N'，'LOW' → 'L'）
ind = df["ANRIND"].str.strip().str[:1].str.upper()
```

---

## 11. 数学、舍入与统计

### ★★ 舍入（头号差异来源）

| SAS | Python | 结果 |
|---|---|---|
| `ROUND(2.5, 1)` | `round(2.5)` | SAS: **3** ｜ Python: **2**（银行家舍入） |
| `ROUND(x, 0.1)` | `round(x, 1)` | 参数含义完全不同：SAS 是**单位**，Python 是**小数位** |
| `ROUND(1234, 10)` | —— | SAS 能按 10 舍入，Python `round` 做不到 |

```python
from clinic.derive import sas_round, sas_round_series

sas_round(2.5, 1)              # 3.0   （等价 SAS: ROUND(2.5, 1)）
sas_round(2.675, 0.01)         # 2.68  （Python round(2.675,2) 会给 2.67）
sas_round(1234, 10)            # 1230.0

# 整列：用向量化版，比 .apply 快得多
df["X"] = sas_round_series(df["X"], 1)      # 保留 1 位小数，四舍五入
```

| SAS | pandas |
|---|---|
| `abs(x)` | `np.abs` / `df["x"].abs()` |
| `ceil(x)` / `floor(x)` | `np.ceil` / `np.floor` |
| `sqrt(x)` / `exp(x)` / `log(x)` | `np.sqrt` / `np.exp` / `np.log` |
| `log10(x)` | `np.log10` |
| `mod(x, y)` | `np.mod` / `%` |
| `x ** 2` | `df["x"] ** 2`（★ `^` 在 Python 里是**按位异或**，不是乘方！） |
| `MIN(a,b)` / `MAX(a,b)` | `np.minimum(a,b)` / `np.maximum(a,b)` |
| `largest(1, of x1-x3)` | `df[["x1","x2","x3"]].max(axis=1)` |
| `ranuni(seed)` | `np.random.default_rng(seed).random(n)` |
| `int(x)` | `np.floor(x).astype(int)`（Python `int()` 是**向零取整**，负数不同） |

### 统计（PROC 对照）

| SAS | Python |
|---|---|
| `PROC MEANS` | `df["x"].describe()` / `.agg([...])` |
| `PROC UNIVARIATE` | `.describe()` + `scipy.stats.describe` |
| `PROC FREQ; TABLES a*b / CHISQ;` | `scipy.stats.chi2_contingency(pd.crosstab(a,b))` |
| `PROC TTEST` | `scipy.stats.ttest_ind(a, b, equal_var=False)` |
| `PROC NPAR1WAY WILCOXON` | `scipy.stats.mannwhitneyu(a, b)` |
| `PROC FREQ / EXACT`（Fisher） | `scipy.stats.fisher_exact(table)` |
| `PROC CORR` | `df[["x","y"]].corr(method="pearson")` |
| `PROC REG` | `statsmodels.api.OLS` |
| `PROC LOGISTIC` | `statsmodels.api.Logit` |
| `PROC PHREG`（生存） | `lifelines.CoxPHFitter` |
| `PROC LIFETEST` | `lifelines.KaplanMeierFitter` |
| `PROC MIXED` | `statsmodels.MixedLM` |
| `PROC ANOVA` | `statsmodels.ols` + `anova_lm` |

```python
# ★ p 值格式化（临床报表惯例）
from clinic.report import format_pvalue
format_pvalue(0.00003)     # '<0.0001'
format_pvalue(0.12345)     # '0.1235'
```

> ⚠️ **`scipy` 不是 pandas 的一部分**，需要单独 `pip install scipy`。
> 如果只是做描述统计，不需要 scipy。

---

## 12. 格式与标签（FORMAT / LABEL）

| SAS | pandas |
|---|---|
| `LABEL age = '年龄';` | `df.attrs["labels"]["AGE"] = "年龄"` |
| `PROC FORMAT; VALUE $sexf ...` | `df["SEX"].map({...})` 或 `pd.Categorical` |
| 应用格式 `format x sexf.;` | `df["SEX_L"] = df["SEX"].map(...)` |
| `put(x, fmt)` | `.map()` / `.astype(str)` |
| `PROC CONTENTS` 看 label | `df.attrs["labels"]` |
| 导出时带 label | 手工拼表头（见 `clinic/report.py`） |

```python
# 从 XPT 读入时标签会挂在 df.attrs 上（见 clinic/io.py）
df = cio.read_xpt("data/raw/dm.xpt")
print(df.attrs["labels"]["AGE"])        # 'Age'
from clinic.io import get_labels
labels = get_labels(df)                 # {变量: 标签}，没有则恒等映射
```

> ⚠️ **pandas 不会自动保留 label**。`df.to_csv()` 之后 label 就丢了。
> 需要保留时，用侧车文件（`clinic.io.save_labels()` 写出 JSON），
> 或者在生成报表时把 label 作为表头拼进去。

---

## 13. 宏、循环与批处理

| SAS | Python |
|---|---|
| `%macro m(a); ... %mend;` | `def m(a): ...` ← 真正的函数 |
| `%do i=1 %to 10;` | `for i in range(1, 11):` |
| `%let x = 1;` | `x = 1` |
| `%if ... %then ...;` | `if ...:` |
| `&x` 宏变量引用 | 直接用变量名 |
| `%sysfunc(countw(&list))` | `len(list)` |
| `filename d pipe "dir";` | `Path(dir).glob("*.xpt")` |
| `%include` | `import` / `exec(open(...).read())` |
| `PROC PRINTTO LOG=` | `logging.FileHandler("run.log")` |
| `OPTIONS ERRORABEND;` | `raise` / `sys.exit(1)` |
| 一个 DATA 步出错 → 程序中断 | `try/except` → 记录后继续 |
| `%abort` | `raise SystemExit(...)` |

```python
# 批处理：一个文件失败不影响其他（完整例子见 cases/case06_批处理自动化.py）
results = []
for f in sorted(Path("data/raw").glob("*.xpt")):
    try:
        df = cio.read_xpt(f)
        results.append({"文件": f.name, "状态": "OK", "行数": len(df)})
    except Exception as exc:
        results.append({"文件": f.name, "状态": "失败", "备注": str(exc)})
summary = pd.DataFrame(results)
```

---

## 14. 常见坑 Top 20

按**踩坑频率**排序。每一条我都见过真实的 diff 或错误的报表。

### 1. `round()` 不是 SAS 的 `ROUND()`

```python
round(2.5)          # 2  ← 银行家舍入
round(2.675, 2)     # 2.67（浮点误差）
sas_round(2.5, 1)   # 3.0 ✅
sas_round(2.675, 0.01)  # 2.68 ✅
```

### 2. `.std()` 和 `np.std()` 不一样

```python
s.std()        # ddof=1（样本 SD）—— 与 SAS 的 STD 一致 ✅
np.std(s)      # ddof=0（总体 SD）—— 与 SAS **不一致** ❌
```

### 3. `np.where` 混用 str 和 np.nan（numpy 2 起会报错）

```python
np.where(cond, "Y", np.nan)        # ❌ DTypePromotionError（numpy ≥ 2）
pd.Series(np.where(cond, "Y", "N"))  # ✅ 或者用 pandas 赋值
out["FLAG"] = pd.NA
out.loc[cond, "FLAG"] = "Y"        # ✅ 最稳
```

### 4. `and` / `or` 对 Series 不能用

```python
df[(df.a > 1) and (df.b < 2)]      # ❌ ValueError
df[(df.a > 1) & (df.b < 2)]        # ✅ 每个条件都加括号
```

### 5. 空字符串不是缺失值

```python
df["SEX"].isna().sum()             # 空串不算缺失 ❌
cio.clean_missing(df)["SEX"].isna().sum()   # ✅ 先统一清洗
```

### 6. `.loc` 赋值到不存在的索引会**静默失效**

```python
out.loc[out["X"].notna() & out.index.isin(keep), "FLAG"] = "Y"
# 如果 keep 里的标签不在 out.index 中 → 这条赋值什么都不做，也不报错
out = out.reindex(all_keys)        # ✅ 先补齐索引
```

SAS 至少会给你一个 NOTE；pandas 什么都不会说。

### 7. `merge` 右表主键重复会**成倍膨胀**

```python
a.merge(b, on="USUBJID")           # 右表有重复 → 行数变多
assert not b.duplicated(subset=["USUBJID"]).any()   # ✅ 先断言
```

### 8. 循环遍历 DataFrame 行

```python
for i in range(len(df)):           # ❌ 慢几百倍
    df.loc[i, "y"] = df.loc[i, "x"] * 2

df["y"] = df["x"] * 2              # ✅ 向量化
```

真的必须逐行时，用 `df.itertuples()`（比 `iterrows()` 快）。

### 9. 链式赋值（SettingWithCopyWarning）

```python
df[df.a > 1]["b"] = 0              # ❌ 改的是临时副本，原表不变
df.loc[df.a > 1, "b"] = 0          # ✅
```

### 10. 忘了 `+1` 的天数差

```python
df["dur"] = (end - start).dt.days         # ❌ 少算一天
df["dur"] = (end - start).dt.days + 1     # ✅ 与 SAS 一致
```

### 11. `pd.cut` 的默认区间是**左开右闭**

```python
pd.cut(age, bins=[0, 65, 80, 200])
# (0,65] / (65,80] / (80,200] → AGE=65 落到第一组 ❌
# 用 np.select 显式写边界 ✅（见 clinic.derive.derive_agegr1）
np.select([age < 65, age <= 80], ["<65", "65-80"], default=">80")
```

### 12. `groupby` 默认去掉了空组，且按字母序排

```python
df.groupby("TRT01P").agg(...)                        # ❌ 行序错、空组消失
df["TRT01P"] = pd.Categorical(df["TRT01P"], categories=TRT, ordered=True)
df.groupby("TRT01P", observed=False).agg(...)        # ✅
```

### 13. `str.replace` / `str.contains` 的正则开关不一致

```python
s.str.replace(".", "X")            # regex=False（默认）→ 只换真正的点
s.str.contains("A.B")              # regex=True（默认）→ 把 . 当通配符！
s.str.contains("A.B", regex=False) # ✅ 想找字面量要显式关掉
```

### 14. `^` 不是乘方

```python
df["x"] ** 2       # ✅ 平方
df["x"] ^ 2        # ❌ 按位异或！
```

### 15. merge 之后忘了检查行数

```python
n0 = len(a)
a = a.merge(b, how="left", on="k")
assert len(a) == n0, f"合并后行数变了：{n0} → {len(a)}"
```

### 16. 读 CSV 时 `low_memory` / dtype 推断出错

```python
pd.read_csv(f)                            # ❌ 混合类型列会分块推断，结果不一致
pd.read_csv(f, low_memory=False)          # ✅
pd.read_csv(f, dtype=str)                 # ✅ QC 场景：全部当字符串
```

### 17. `inplace=True` 其实不该用

```python
df.sort_values("x", inplace=True)   # ❌ pandas 3 起已不推荐，且容易出错
df = df.sort_values("x")            # ✅ 明确赋值
```

### 18. 日期列没转就直接算

```python
df["TRTEDT"] - df["TRTSDT"]                        # ❌ 字符串相减 → 报错或乱来
pd.to_datetime(df["TRTEDT"]) - pd.to_datetime(df["TRTSDT"])   # ✅
```

### 19. 忘了 `errors="coerce"`

```python
pd.to_datetime(df["DTC"])                     # ❌ 有一个坏值就整列报错
pd.to_datetime(df["DTC"], errors="coerce")    # ✅ 坏值变 NaT，其他正常
pd.to_numeric(df["X"], errors="coerce")       # 同理
```

> ⚠️ 但 `errors="coerce"` 会**掩盖数据质量问题**。
> 生产代码里应该同时统计有多少个被 coerce 掉了，并报告出来。

### 20. Excel 的 sheet 名有 31 字符上限、不能含 `[]:*?/\`

```python
name = Path(f).stem[:28]
name = "".join("_" if c in '[]:*?/\\' else c for c in name)
```

---

## 附：本项目的关键工具函数

| 函数 | 位置 | 作用 |
|---|---|---|
| `sas_round(x, unit)` | `clinic/derive.py` | SAS 语义的舍入（四舍五入 + 单位参数） |
| `sas_round_series(s, decimals)` | `clinic/derive.py` | 整列向量化舍入 |
| `derive_agegr1(df)` | `clinic/derive.py` | 年龄分组（边界显式：65 和 80 都归 65-80） |
| `to_categorical(s, levels)` | `clinic/derive.py` | 用有序 Categorical 固定输出顺序 |
| `parse_partial_date(s)` | `clinic/derive.py` | 处理 ISO 8601 部分日期，返回精度 |
| `sas_date_to_datetime` / `datetime_to_sas_date` | `clinic/derive.py` | SAS 日期数字 ↔ Timestamp |
| `read_xpt` / `read_any` / `load_domains` | `clinic/io.py` | 读 XPT / 自动识别格式 / 批量加载 |
| `clean_missing(df)` | `clinic/io.py` | 伪缺失值统一清洗 |
| `pct_format` / `summarize_continuous` / `crosstab_shift` | `clinic/report.py` | TFL 报表构造件 |
| `format_pvalue` | `clinic/report.py` | p 值格式化（`<0.0001` 惯例） |
| `check_dataset` / `compare_frames` | `clinic/qc.py` | 数据质量检查 / PROC COMPARE 等价物 |

---

**相关文档**

- [`docs/02-基础语法速通-SAS对照.md`](../docs/02-基础语法速通-SAS对照.md) —— 语法层对照
- [`docs/08-数据操作对照-DATA步与PROC SQL.md`](../docs/08-数据操作对照-DATA步与PROC%20SQL.md) —— 数据操作对照
- [`docs/10-日期缺失值格式与数据质量.md`](../docs/10-日期缺失值格式与数据质量.md) —— 日期与缺失值专题
- [`cheatsheets/Agent开发速查表.md`](Agent开发速查表.md) —— ★ 转向 Agent 开发后的高频查阅页
- [`cases/`](../cases) —— 13 个可运行案例，每个坑都有可复现的代码
