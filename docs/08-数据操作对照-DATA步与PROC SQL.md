# 第 08 章 · 数据操作对照：DATA 步 / PROC SQL → pandas

> **本章目标**：这是全教程**最核心的一章**。
> 目标是让你达到"看到一段 SAS 程序，能在脑子里完成翻译"的程度。
> **建议做法**：读完一遍后，找一段你手头的真实 SAS 程序，逐句重写。

---

## 8.1 总览对照表（建议打印/置顶）

| # | SAS 操作 | pandas 写法 | 章节 |
|---|---|---|---|
| 1 | `where 条件` | `df[条件]` / `df.query()` | 8.2 |
| 2 | `if/then/else` 派生 | `np.where` / `np.select` | 8.3 |
| 3 | `keep` / `drop` | `df[[...]]` / `df.drop(columns=...)` | 8.4 |
| 4 | `rename` | `df.rename(columns={})` | 8.4 |
| 5 | `set`（纵向堆叠） | `pd.concat([...])` | 8.5 |
| 6 | `merge`（横向连接） | `pd.merge()` | 8.5 |
| 7 | `PROC SORT` | `df.sort_values()` | 8.6 |
| 8 | `nodupkey` 去重 | `df.drop_duplicates()` | 8.6 |
| 9 | `PROC MEANS` | `df.groupby().agg()` | 8.7 |
| 10 | `PROC FREQ` | `value_counts()` / `crosstab()` | 8.8 |
| 11 | `PROC TRANSPOSE` | `pivot()` / `pivot_table()` / `melt()` | 第 09 章 |
| 12 | `PROC SQL` 子查询 | `df.query()` / 链式筛选 | 8.9 |
| 13 | `first.` / `last.` | `groupby().transform()` / `drop_duplicates(keep=)` | 8.10 |
| 14 | `retain` | `ffill()` / `groupby().cumsum()` | 8.10 |
| 15 | `lag()` / `dif()` | `df.shift()` / `df.diff()` | 8.10 |
| 16 | `PROC RANK` | `df.rank()` | 8.7 |
| 17 | `PROC FORMAT` 自定义 | `pd.cut` / `map` / `replace` | 8.11 |
| 18 | `nobs` / `_N_` | `len(df)` / `df.index` | 8.4 |

---

## 8.2 筛选（WHERE）

```sas
data adsl2;
    set adsl;
    where SAFFL = 'Y' and AGE >= 65;
run;
```

```python
adsl2 = adsl[(adsl["SAFFL"] == "Y") & (adsl["AGE"] >= 65)]
# 或（推荐，可读性更好）
adsl2 = adsl.query("SAFFL == 'Y' and AGE >= 65")
```

### 常用条件对照表

| SAS WHERE | pandas |
|---|---|
| `x = 1` | `df["x"] == 1` |
| `x ^= 1` / `x ne 1` | `df["x"] != 1` |
| `x in (1, 2)` | `df["x"].isin([1, 2])` |
| `x not in (1, 2)` | `~df["x"].isin([1, 2])` |
| `x > 1 and y < 2` | `(df["x"] > 1) & (df["y"] < 2)` |
| `x > 1 or y < 2` | `(df["x"] > 1) \| (df["y"] < 2)` |
| `x is missing` | `df["x"].isna()` |
| `x is not missing` | `df["x"].notna()` |
| `x between 1 and 2` | `df["x"].between(1, 2)` |
| `x like '%abc%'` | `df["x"].str.contains("abc", na=False)` |
| `x =: 'AB'`（前缀） | `df["x"].str.startswith("AB", na=False)` |
| `index(x, 'AB') > 0` | `df["x"].str.contains("AB", na=False)` |
| `upcase(x) = 'AB'` | `df["x"].str.upper() == "AB"` |
| `prxmatch('/^A/', x)` | `df["x"].str.match(r"^A", na=False)` |
| `x = .` （数值） | `df["x"].isna()` |
| `x = ''` （字符） | `df["x"] == ""` |

> ⚠️ **`na=False` 是必须的**：`.str.contains()` 遇到缺失值返回 `NaN`，
> 而 `NaN` 在布尔索引里会报错。加 `na=False` 表示"缺失值视为不匹配"。

---

## 8.3 派生变量（IF/THEN/ELSE）

```sas
data adsl2; set adsl;
    if AGE < 65 then AGEGR1 = '<65';
    else if AGE <= 80 then AGEGR1 = '65-80';
    else AGEGR1 = '>80';

    /* 数值派生 */
    BMI = WEIGHTBL / (HEIGHTBL/100)**2;

    /* 字符函数 */
    SITEID2 = substr(USUBJID, 4, 3);
run;
```

```python
adsl2 = adsl.copy()

# 多条件分支
adsl2["AGEGR1"] = np.select(
    [adsl2["AGE"] < 65, adsl2["AGE"] <= 80],
    ["<65", "65-80"],
    default=">80",
)

# 数值派生
adsl2["BMI"] = adsl2["WEIGHTBL"] / (adsl2["HEIGHTBL"] / 100) ** 2

# 字符函数（pandas 的 .str 访问器 = SAS 字符函数）
adsl2["SITEID2"] = adsl2["USUBJID"].str.slice(3, 6)      # substr(x, 4, 3)
```

> 📌 **`substr` → `.str.slice(start-1, end)`** 的换算：
> `substr(x, 4, 3)` = 从第 4 个字符起取 3 个 = Python 索引 3 到 6 = `.str.slice(3, 6)`

### SAS 字符函数 → pandas `.str` 方法对照

| SAS | pandas `.str` |
|---|---|
| `substr(x, 1, 3)` | `.str.slice(0, 3)` 或 `.str[:3]` |
| `upcase(x)` | `.str.upper()` |
| `lowcase(x)` | `.str.lower()` |
| `propcase(x)` | `.str.title()` |
| `strip(x)` | `.str.strip()` |
| `trim(x)` | `.str.rstrip()` |
| `left(x)` | `.str.lstrip()` |
| `length(x)` | `.str.len()` |
| `scan(x, 2, '-')` | `.str.split("-").str[1]` |
| `catx('-', a, b)` | `a.str.cat(b, sep="-")` |
| `tranwrd(x, 'A', 'B')` | `.str.replace("A", "B")` |
| `index(x, 'A')` | `.str.find("A")` |
| `compress(x)` | `.str.replace(r"\s", "", regex=True)` |
| `compress(x, '0123456789')` | `.str.replace(r"\D", "", regex=True)` |
| `repeat(x, 2)` | `.str.repeat(2)` |
| `reverse(x)` | `.str[::-1]` |
| `prxchange('s/a/b/', -1, x)` | `.str.replace(r"a", "b", regex=True)` |
| `put(x, $10.)` | `.str.pad(10)` |

> 💡 **`regex=True` 很关键**：pandas 的 `.str.replace()` 默认是**字面替换**
> （这点比 SAS 的 `tranwrd` 还安全），要用正则必须显式声明。

---

## 8.4 列操作（KEEP / DROP / RENAME / 长度）

```sas
data want;
    set have (keep=USUBJID AGE SEX rename=(AGE=AGEBL));
    length AGEGR1 $10;
run;
```

```python
# keep + rename 一步完成
want = have[["USUBJID", "AGE", "SEX"]].rename(columns={"AGE": "AGEBL"})

# 用列表管理（临床程序推荐：把变量清单集中声明，便于 QC）
KEEP_VARS = ["USUBJID", "AGE", "SEX"]
RENAME = {"AGE": "AGEBL"}
want = have[KEEP_VARS].rename(columns=RENAME)

# 删除若干列
df = df.drop(columns=["TEMP", "X1"])

# 按模式删除
df = df.drop(columns=[c for c in df.columns if c.startswith("_")])

# 长度语义：Python 字符串没有固定长度
#   要补空格 → .str.pad(n)；要去尾空格 → .str.rstrip()
```

### 行数（NOBS / `_N_`）

```python
len(df)               # nobs
df.shape[0]           # nobs
df.index              # 行号（对应 _N_-1）
df["rowno"] = range(1, len(df) + 1)      # 显式加 _N_
```

---

## 8.5 纵向堆叠与横向连接（SET / MERGE）

### SET = `pd.concat()`

```sas
data all;
    set ae_ds ae_drug;      /* 纵向堆叠，按列名对齐 */
run;
```

```python
# axis=0 表示纵向堆叠；ignore_index 重新编号（否则行号会重复）
all_df = pd.concat([ae_ds, ae_drug], axis=0, ignore_index=True)

# 保留来源标记（对应 SAS 的 in= 选项）—— QC 常用
all_df = pd.concat(
    [ae_ds.assign(_SOURCE="ae"), ae_drug.assign(_SOURCE="drug")],
    axis=0, ignore_index=True,
)
```

### MERGE = `pd.merge()`

```sas
/* SAS：必须先排序 */
proc sort data=adsl; by USUBJID; run;
proc sort data=adex;  by USUBJID; run;

data adam;
    merge adsl (in=a) adex;
    by USUBJID;
    if a;                    /* 左连接：只保留 adsl 中的受试者 */
run;
```

```python
# pandas：不需要预排序
adam = pd.merge(adsl, adex, on="USUBJID", how="left")

# 保留来源标记（对应 in=a / in=b）
adam = pd.merge(adsl, adex, on="USUBJID", how="left", indicator=True)
adam["_merge"].value_counts()
# both          300
# left_only       6      ← 这些是 adsl 有、adex 没有的（QC 关注点）
```

### 连接类型对照（重要！）

| SAS 写法 | pandas `how=` | 语义 |
|---|---|---|
| `merge a(in=a) b; if a;` | `how="left"` | 保留 A 全部 |
| `merge a b(in=b); if b;` | `how="right"` | 保留 B 全部 |
| `merge a(in=a) b(in=b); if a and b;` | `how="inner"` | 只保留匹配上的 |
| `merge a(in=a) b(in=b); if a or b;` | `how="outer"` | 全部保留 |

```python
# 连接键名称不同时（对应 SAS 的 rename=）
merged = pd.merge(a, b, left_on="USUBJID", right_on="SUBJID", how="left")

# 多键连接（对应 by USUBJID VISITNUM）
merged = pd.merge(a, b, on=["USUBJID", "VISITNUM"], how="left")

# 避免列名冲突（对应 SAS 的变量覆盖警告）
merged = pd.merge(a, b, on="USUBJID", suffixes=("_ADSL", "_ADEX"), how="left")
```

> 🔥 **必须校验"一对多"关系**。SAS 在 M:1 时会静默覆盖，
> pandas 会**直接报错**（这其实是好事）：
> ```
> MergeError: Merge keys are not unique in right dataset
> ```
> 出现这个错时，先确认是不是真的需要 `how="left"` + 多对多，
> 通常意味着**你的连接键选错了**（漏了 VISITNUM / PARAMCD 之类的维度）。
> 这个报错往往帮你发现了真实的逻辑 bug。

### 如果确实需要"多对多 + 笛卡尔积"（SAS 的行为）

```python
merged = pd.merge(a, b, on="USUBJID", how="cross")   # 或 how="inner" 但先 validate=None
# 显式声明允许的关系（推荐，相当于写文档）
merged = pd.merge(a, b, on="USUBJID", how="inner",
                  validate="many_to_many")
```

---

## 8.6 排序与去重（PROC SORT）

```sas
proc sort data=ae out=ae2 nodupkey; by USUBJID AEDECOD; run;
```

```python
# 排序
ae2 = ae.sort_values(["USUBJID", "AEDECOD"])

# 混合升降序（对应 by USUBJID descending AEDECOD）
ae2 = ae.sort_values(["USUBJID", "AEDECOD"], ascending=[True, False])

# 去重（保留第一条）—— 对应 nodupkey
ae2 = ae.drop_duplicates(subset=["USUBJID", "AEDECOD"], keep="first")

# 保留最后一条（对应 SAS 的 if last.xxx）
ae2 = ae.drop_duplicates(subset=["USUBJID", "AEDECOD"], keep="last")

# 找出重复记录（QC 用）
dups = ae[ae.duplicated(subset=["USUBJID", "AEDECOD"], keep=False)]

# 保留每组某列最大/最小的那条（SAS 里要 sort + first.，pandas 一行）
latest = ae.sort_values("VISITNUM").drop_duplicates("USUBJID", keep="last")
```

> 💡 **`drop_duplicates` 的行为受排序影响**。
> SAS 的 `nodupkey` 也是"排序后保留第一条"。
> 所以：**先 `sort_values` 再 `drop_duplicates`**，语义才明确、可复现。

---

## 8.7 分组汇总（PROC MEANS / SUMMARY）

```sas
proc means data=adsl n mean std min max median maxdec=2;
    class TRT01P AGEGR1;
    var AGE;
run;
```

```python
# 基础版
adsl.groupby(["TRT01P", "AGEGR1"])["AGE"].agg(["count", "mean", "std", "min", "max", "median"])

# 自定义聚合（推荐：用命名聚合，输出列名可控，便于后续报表）
summary = adsl.groupby("TRT01P")["AGE"].agg(
    N="count",
    Mean="mean",
    SD="std",
    Min="min",
    Max="max",
    Median="median",
)

# 多变量 + 多统计量
summary = adsl.groupby("TRT01P").agg(
    N=("USUBJID", "nunique"),
    AGE_MEAN=("AGE", "mean"),
    AGE_SD=("AGE", "std"),
    BMI_MEAN=("BMI", "mean"),
)

# SAS 的 n / nmiss 对照
adsl.groupby("TRT01P")["AGE"].agg(
    N="count",                       # 非缺失个数
    NMISS=lambda s: s.isna().sum(),
)
```

### SAS `CLASS` vs pandas `groupby` 的关键差异

| | SAS `CLASS` | pandas `groupby` |
|---|---|---|
| 缺失值分组 | 默认**单独成组**（显示为 `.`） | 默认**排除**（`dropna=True`）|
| 组内缺失值 | 默认**排除**（除非加 `missing` 选项） | 默认排除（`skipna=True`） |
| 保留分组列 | 是 | `as_index=False` 或 `reset_index()` |
| 不存在的组合 | `CLASSDATA` / `COMPLETETYPES` | `reindex` |

```python
# 让缺失值单独成组（对应 SAS 的 class 显示 .）
df.groupby("TRT01P", dropna=False)["AGE"].mean()

# 输出成"扁平"表格（不把分组键变索引）—— 报表场景几乎都要
summary = adsl.groupby("TRT01P", as_index=False)["AGE"].mean()

# 让所有组合都出现（补 0）——对应 SAS 的 COMPLETETYPES
pivot = df.pivot_table(index="AGEGR1", columns="TRT01P", values="USUBJID",
                       aggfunc="count", fill_value=0)
```

### `PROC RANK` → `rank()`

```python
adsl["AGE_RANK"] = adsl["AGE"].rank(ascending=False, method="min")
adsl["AGE_RANK_GRP"]  = adsl["AGE"].rank(pct=True)              # 百分位
adsl["AGE_QUARTILE"]  = pd.qcut(adsl["AGE"], 4, labels=["Q1","Q2","Q3","Q4"])
adsl["GRP_RANK"]      = adsl.groupby("TRT01P")["AGE"].rank()     # 组内排名
```

---

## 8.8 频数统计（PROC FREQ）

```sas
proc freq data=adsl;
    tables TRT01P * AGEGR1 / nocol norow nopercent missing;
run;
```

```python
# 单变量频数
adsl["TRT01P"].value_counts(dropna=False)
adsl["TRT01P"].value_counts(normalize=True).round(4)      # 占比

# 交叉表（对应 tables A * B）
pd.crosstab(adsl["TRT01P"], adsl["AGEGR1"])

# 含缺失值 + 加合计
pd.crosstab(adsl["TRT01P"], adsl["AGEGR1"], dropna=False, margins=True)

# 长格式输出（更适合后续拼装报表）
freq = (adsl.groupby(["TRT01P", "AGEGR1"], dropna=False)
             .size().reset_index(name="N"))
```

> 💡 **`margins=True` 就是 SAS 的 `tables A*B / ... expected` 里没有的那个合计行/列**。
> 做 CSR 报表时非常常用。

---

## 8.9 PROC SQL → pandas

SAS 的 PROC SQL 和 pandas 的对应关系比较直接：

```sas
proc sql;
    create table want as
    select  USUBJID,
            TRT01P,
            AGE,
            case when AGE >= 65 then 'Y' else 'N' end as ELDERLY,
            count(distinct AEDECOD) as N_AE
    from    adsl as a
            left join adae as b
              on a.USUBJID = b.USUBJID
    where   a.SAFFL = 'Y'
    group by USUBJID, TRT01P, AGE
    having  count(distinct AEDECOD) > 0
    order by TRT01P, AGE desc;
quit;
```

```python
# SQL 风格的等长链式写法（pandas 推荐）
want = (
    pd.merge(adsl, adae, on="USUBJID", how="left")
      .query("SAFFL == 'Y'")
      .assign(ELDERLY=lambda d: np.where(d["AGE"] >= 65, "Y", "N"))
      .groupby(["USUBJID", "TRT01P", "AGE"], as_index=False)
      .agg(N_AE=("AEDECOD", "nunique"))
      .query("N_AE > 0")
      .sort_values(["TRT01P", "AGE"], ascending=[True, False])
      [["USUBJID", "TRT01P", "AGE", "ELDERLY", "N_AE"]]
)
```

**逐条对照**：

| SQL 子句 | pandas |
|---|---|
| `select 列` | `df[[...]]` 或 `.agg()` |
| `where` | `.query()` |
| `case when` | `.assign(x=lambda d: np.where(...))` |
| `group by` | `.groupby()` |
| `having` | `.query()`（在 groupby 之后）|
| `order by` | `.sort_values()` |
| `left join` | `pd.merge(..., how="left")` |
| `distinct` | `.drop_duplicates()` |
| `count(distinct x)` | `.agg(x="nunique")` |
| `limit 10` | `.head(10)` |

> 💬 **另一个选择：直接写 SQL**。如果你（或 QC 同事）觉得 SQL 更顺手，
> pandas 里可以接 **DuckDB** 直接在 DataFrame 上跑 SQL：
> ```python
> import duckdb
> duckdb.sql("""
>     SELECT TRT01P, COUNT(*) AS N, AVG(AGE) AS MEAN_AGE
>     FROM adsl WHERE SAFFL = 'Y' GROUP BY TRT01P
> """).df()
> ```
> 这在"从 SAS 迁移"的过渡期非常好用——**先用 SQL 把逻辑跑对，
> 再逐步改写成 pandas**。第 17 章会展开。

### chain 式的可读性技巧：分步 vs 链式

```python
# 分步写（更好调试，推荐给临床程序——每一步可 print 核对）
step1 = pd.merge(adsl, adae, on="USUBJID", how="left")
step2 = step1.query("SAFFL == 'Y'")
step3 = step2.groupby("TRT01P", as_index=False).agg(N=("USUBJID", "nunique"))

# 链式写（更紧凑，但出错时难定位）
result = (pd.merge(adsl, adae, on="USUBJID", how="left")
            .query("SAFFL == 'Y'")
            .groupby("TRT01P", as_index=False)
            .agg(N=("USUBJID", "nunique")))
```

> 🧠 **临床编程建议**：**优先分步写**。
> 因为临床程序的核心要求是**可核对（QC-able）**——
> 每一步的中间结果都能取出来看，QC 才能逐环节比对。
> 链式写法适合你已完全掌握、且逻辑很短的场景。

---

## 8.10 组内处理：FIRST. / LAST. / RETAIN / LAG

这是 SAS 里必须"靠排序 + 自动变量"才能做的事，pandas 有更直接的表达。

### FIRST / LAST

```sas
proc sort data=lb; by USUBJID VISITNUM; run;
data lb2; set lb;
    by USUBJID;
    if first.USUBJID then FLAG = 'Y';   /* 每个受试者的第一条 */
    if last.USUBJID  then ENDF = 'Y';   /* 每个受试者的最后一条 */
run;
```

```python
lb2 = lb.sort_values(["USUBJID", "VISITNUM"])

# 方法 1：cumcount（推荐，直观）
lb2["_rn"] = lb2.groupby("USUBJID").cumcount()          # 组内序号，0 开始
lb2["FLAG"] = lb2["_rn"] == 0                           # 第一条
n_per_subj = lb2.groupby("USUBJID")["USUBJID"].transform("size")
lb2["ENDF"] = lb2["_rn"] == n_per_subj - 1               # 最后一条

# 方法 2：保留每组第一条/最后一条记录（更常用）
first_rec = lb2.drop_duplicates("USUBJID", keep="first")
last_rec  = lb2.drop_duplicates("USUBJID", keep="last")
```

### RETAIN（保持上一行的值）

```sas
data lb2; set lb;
    retain BASE;
    if FIRST.VISITNUM then BASE = AVAL;      /* 首次访视作为基线 */
run;
```

```python
# 取每组第一个值并广播回所有行（不是循环！）
lb["BASE"] = lb.groupby("USUBJID")["AVAL"].transform("first")

# 用基线访视的值（更严谨：按 ABLFL='Y' 标记）
base = lb.loc[lb["ABLFL"] == "Y", ["USUBJID", "AVAL"]].rename(columns={"AVAL": "BASE"})
lb = pd.merge(lb, base, on="USUBJID", how="left")

# 向下填充（真正的 retain 语义：用上一个非缺失值）
df["LASTFILLED"] = df["VALUE"].ffill()

# 组内向下填充
df["LASTFILLED"] = df.groupby("USUBJID")["VALUE"].ffill()

# 组内累计（对应 retain + sum 语句）
df["CUMSUM"] = df.groupby("USUBJID")["DOSE"].cumsum()
df["CUMMAX"] = df.groupby("USUBJID")["AVAL"].cummax()
```

### LAG / DIF

```sas
data lb2; set lb;
    prev = lag(AVAL);
    chg  = dif(AVAL);
run;
```

```python
df["PREV"] = df["AVAL"].shift(1)                 # lag(AVAL)
df["CHG"]  = df["AVAL"].diff()                   # dif(AVAL)
df["CHG2"] = df["AVAL"] - df["AVAL"].shift(2)     # lag2

# ⚠️ 关键差异：pandas 的 shift 不考虑 BY 组！
# 组内 shift 必须先排序，再用 groupby().shift()
df = df.sort_values(["USUBJID", "VISITNUM"])
df["PREV"] = df.groupby("USUBJID")["AVAL"].shift(1)
```

> 🔥 **这是最容易出错的地方**：SAS 的 `lag()` 在 DATA 步里天然按当前
> BY 组的顺序作用；pandas 的 `.shift()` 是**整列位移**，
> 不分组会在受试者交界处"串行"（把上一个受试者的最后一条当成本受试者的前一条）。
> **必须 `groupby().shift()`。**

---

## 8.11 自定义格式映射（PROC FORMAT）

```sas
proc format;
    value $sexf
        'F' = 'Female'
        'M' = 'Male'
        other = 'Unknown';
    value agegrp
        low -< 65 = '<65'
        65 - 80   = '65-80'
        80 <- high= '>80';
run;
```

```python
# 离散映射 → dict + map（对应 value $sexf）
SEX_FMT = {"F": "Female", "M": "Male"}
df["SEX_DECODE"] = df["SEX"].map(SEX_FMT).fillna("Unknown")

# 或用 replace（保留未匹配的原值）
df["SEX_DECODE"] = df["SEX"].replace(SEX_FMT)

# 区间映射 → pd.cut（对应 value agegrp）
df["AGEGR1"] = pd.cut(df["AGE"], bins=[-np.inf, 65, 80, np.inf],
                      labels=["<65", "65-80", ">80"], right=False)

# 保留为"有顺序的分类"（对应 SAS format 的排序输出顺序）
TRT_ORDER = ["Placebo", "Xanomeline Low Dose", "Xanomeline High Dose"]
df["TRT01P"] = pd.Categorical(df["TRT01P"], categories=TRT_ORDER, ordered=True)
df.groupby("TRT01P", observed=False).size()      # 输出顺序 = TRT_ORDER
```

> 🔥 **`pd.Categorical` 是复刻 SAS 输出的关键**：
> 临床报表要求治疗组按固定顺序（而不是字母序）输出，
> SAS 靠 `CLASS` 的顺序或 format；pandas 靠 `Categorical` 的 `categories` 顺序。
> **这是报表现场最常见的"顺序不对"问题的根因。**
>
> 注意：pandas 2.x 起 `groupby` 对 Categorical 需要 `observed=False`
> 才能显示所有类别（即使计数为 0）。这在做"空治疗组也要出现在表里"时很重要。

---

## 8.12 综合实战：一段完整 SAS 程序的重写

**原始 SAS**（典型的安全性人口学汇总）：

```sas
proc sort data=adsl; by USUBJID; run;
proc sort data=adae;  by USUBJID; run;

data want;
    merge adsl (in=a) adae (in=b);
    by USUBJID;
    if a;

    if AGE < 65 then AGEGR1 = '<65';
    else if AGE <= 80 then AGEGR1 = '65-80';
    else AGEGR1 = '>80';

    if not missing(AEDECOD) then HAS_AE = 'Y';
    else HAS_AE = 'N';
run;

proc means data=want n mean std maxdec=1;
    class TRT01P AGEGR1;
    var AGE;
run;
```

**pandas 重写**：

```python
import numpy as np
import pandas as pd

# 1) 连接（不需要预先排序）
want = pd.merge(adsl, adae[["USUBJID", "AEDECOD"]], on="USUBJID", how="left")

# 2) 派生 AGEGR1
want["AGEGR1"] = np.select(
    [want["AGE"] < 65, want["AGE"] <= 80], ["<65", "65-80"], default=">80"
)

# 3) 派生 HAS_AE（注意 pandas 里字符缺失是 NaN，不是 ''）
want["HAS_AE"] = np.where(want["AEDECOD"].notna(), "Y", "N")

# 4) 分组汇总
summary = (want.groupby(["TRT01P", "AGEGR1"], as_index=False)
                .agg(N=("AGE", "count"), Mean=("AGE", "mean"), SD=("AGE", "std"))
                .round(1))

# 5) 核对
print(summary.to_string(index=False))
```

**核对清单（迁移时必做）**：
- [ ] 行数是否一致？（连接类型选对了吗）
- [ ] 分组计数是否一致？（缺失值分组差异）
- [ ] 数值统计量是否一致？（舍入方式差异，见第 13 章）
- [ ] 输出顺序是否一致？（`Categorical` vs `CLASS` 顺序）

---

## 8.13 动手练习

1. **筛选+派生**：从 `data/samples/adsl.csv` 中取出 SAFFL='Y' 且 AGE>=65 的受试者，
   派生 `AGEGR1`（用 `pd.cut`），输出人数。

2. **连接**：把 ADSL 和 ADAE 左连接，统计每个受试者的不良事件条数
   （提示：`groupby` + `size`，注意没有 AE 的受试者应为 0 而不是缺失）。

3. **去重**：找出 ADAE 中每个受试者、每个 `AEDECOD` 的**首次发生**记录
   （按 `ASTDT` 排序后取第一条）。

4. **组内处理**：在 ADAE 中，用 `groupby().shift()` 计算每个受试者
   **相邻两次不良事件的天数间隔**。

5. **完整重写**：找一段你自己写过的 SAS 程序（不需要很长，20 行以内），
   用 Python 重写，并用 8.12 节的核对清单逐项验证结果一致。

---

**上一章 ←** [第 07 章 · pandas 入门](07-pandas入门-DataFrame就是数据集.md)
**下一章 →** [第 09 章 · 合并、重塑与分组汇总](09-合并重塑与分组汇总.md)
