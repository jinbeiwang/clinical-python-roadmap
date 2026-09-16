# 第 07 章 · pandas 入门：DataFrame 就是数据集

> **本章目标**：建立对 pandas `DataFrame` 的准确心智模型——
> **它就是 SAS 数据集**，只是多了索引、少了自动留存。

---

## 7.1 DataFrame ↔ SAS 数据集 的一一对应

| SAS 数据集的概念 | pandas 对应物 | 说明 |
|---|---|---|
| 观测（行） | `df` 的一行 | 通过 `df.iloc[i]` 或布尔索引取 |
| 变量（列） | `df["AGE"]`（一个 Series） | **每一列单独取，不是 `AGE`** |
| 变量名 | `df.columns` | 字符串列表 |
| 变量标签 | `df.attrs` / pyreadstat 的 meta | pandas 原生不保留 label，需额外处理 |
| 变量格式 / 长度 | `df.dtypes` | 类型（不是 format），见下 |
| `PROC CONTENTS` | `df.info()` | 看结构 |
| `PROC PRINT (obs=5)` | `df.head()` | 看前几行 |
| `PROC PRINT` 全部 | `df` （在 Notebook 里） | |
| `nobs` / `nvar` | `df.shape` → `(行, 列)` | |
| `_N_`（行号） | `df.index` | 默认 0,1,2,… |
| `WORK` 库 | 内存中的 Python 变量 | **不会自动留存** |
| 永久数据集 | CSV / Parquet / Excel 文件 | |

### 数据类型对照（重要）

| SAS | pandas dtype | 备注 |
|---|---|---|
| 数值（全部） | `int64` / `float64` | pandas 区分整数与浮点（SAS 不区分）|
| 字符 | `object` 或 `string` | `object` 是"任意 Python 对象"，实际存字符串 |
| 日期 | `datetime64[ns]` | 需要显式转换（第 10 章）|
| 分类（自定义 format） | `category` | 有顺序的分类，见第 09 章 |
| 缺失 | `NaN`(float) / `pd.NA` | |

> ⚠️ **数值列的坑**：SAS 里 `AGE` 是纯数值；在 pandas 里
> 如果这一列有缺失，整列会被提升为 `float64`，于是 **63 会显示成 63.0**。
> 这是新手困惑的高频点。需要输出整数时用
> `df["AGE"].astype("Int64")`（大写 I 的可空整型）或输出时格式化。

---

## 7.2 三种创建 DataFrame 的方式

```python
import pandas as pd
import numpy as np

# 方式 1：从字典（最直观，适合小数据/测试）
df = pd.DataFrame({
    "USUBJID": ["01-701-1015", "01-701-1023", "01-701-1028"],
    "TRT01P":  ["Placebo", "Placebo", "Xanomeline High Dose"],
    "AGE":     [63, 64, 71],
    "SEX":     ["F", "M", "M"],
})

# 方式 2：从文件（真实工作场景）
df = pd.read_csv("data/samples/dm.csv")
# 方式 3：从 SAS 文件
df = pd.read_sas("data/raw/dm.xpt", format="xport", encoding="utf-8")
```

---

## 7.3 第一步永远是"体检"：认识你的数据

```python
# 1) 形状
df.shape                 # (306, 25)  → 306 行 25 列

# 2) 结构总览：列名 + 非空数量 + 类型（≈ PROC CONTENTS 的精简版）
df.info()

# 3) 前 5 行 / 后 5 行
df.head()
df.tail(3)

# 4) 列名清单
df.columns.tolist()
list(df.columns)

# 5) 描述统计（数值列，≈ PROC MEANS）
df.describe()

# 6) 全列（含字符列）的概览
df.describe(include="all")

# 7) 每列的缺失数量（QC 常看）
df.isna().sum()

# 8) 唯一值个数与示例
df.nunique()
df["RACE"].unique()
df["RACE"].value_counts(dropna=False)
```

> 💡 **把"体检"写成函数的习惯**：在临床编程里，
> 每次拿到新数据都该先体检一遍。建议写一个工具函数放在 `clinic/` 包里，
> 每次调用 2 秒出结果。

```python
def profile(df, name="数据集", max_show=12):
    """快速数据体检（≈ PROC CONTENTS + PROC MEANS 摘要）。"""
    print("=" * 72)
    print(f"{name}：{df.shape[0]:,} 行 × {df.shape[1]} 列")
    print("=" * 72)
    info = pd.DataFrame({
        "dtype":    df.dtypes.astype(str),
        "n_miss":   df.isna().sum(),
        "pct_miss": (df.isna().mean() * 100).round(1),
        "n_unique": df.nunique(),
        "example":  [df[c].dropna().iloc[0] if df[c].notna().any() else None
                     for c in df.columns],
    })
    print(info.head(max_show).to_string())
    if len(info) > max_show:
        print(f"... 还有 {len(info) - max_show} 列")
    return info
```

---

## 7.4 选列、选行：`loc` 与 `iloc`

这是 pandas 最核心的操作，**必须分清 `loc` 和 `iloc`**：

| | 按什么选 | 写法 | SAS 对应 |
|---|---|---|---|
| `df.loc[...]` | **标签**（列名、索引值） | `df.loc[:, ["AGE","SEX"]]` | `keep` 语句 |
| `df.iloc[...]` | **位置**（第几行第几列） | `df.iloc[0:5, 0:3]` | `obs=` / `firstobs=` |

```python
# ---- 选列 ----
df["AGE"]                      # 单列 → Series
df[["AGE", "SEX", "RACE"]]     # 多列 → DataFrame（注意双层方括号！）

# SAS: keep USUBJID AGE SEX;
df = df[["USUBJID", "AGE", "SEX"]]

# SAS: drop RACE;
df = df.drop(columns=["RACE"])
df = df.drop(columns=[c for c in df.columns if c.startswith("SUPP")])

# ---- 选行 ----
df.head(10)                    # SAS: (obs=10)
df.iloc[0:10]                  # 同上，位置切片
df.iloc[-5:]                   # 最后 5 行
df.loc[0:9]                    # ⚠️ loc 的切片是"含两端"的（与 iloc 不同！）

# ---- 同时选行列 ----
df.loc[0:4, ["USUBJID", "AGE"]]        # 前 5 行的两列
df.iloc[0:5, 0:2]                      # 前 5 行、前 2 列
df.at[0, "AGE"]                        # 取单个值（快）
df.iat[0, 1]                           # 按位置取单个值
```

> ⚠️ **`loc` 切片含两端**：`df.loc[0:9]` 是 10 行，`df.iloc[0:10]` 也是 10 行。
> 这是 pandas 的历史遗留设计，很容易搞错。**建议统一用 `iloc` 做位置切片。**

---

## 7.5 条件筛选：`where` 子句的替代

```sas
data adsl_f; set adsl;
    where SAFFL = 'Y' and AGE >= 65;
run;
```

```python
# 单条件
adsl_f = adsl[adsl["SAFFL"] == "Y"]

# 多条件：每个条件加括号，用 & / | 连接
adsl_f = adsl[(adsl["SAFFL"] == "Y") & (adsl["AGE"] >= 65)]

# 或关系
adsl_f = adsl[(adsl["TRT01P"] == "Placebo") | (adsl["TRT01P"] == "Xanomeline High Dose")]

# in 列表（对应 SAS 的 in (...)： where TRT01P in ('Placebo','Xanomeline High Dose')）
adsl_f = adsl[adsl["TRT01P"].isin(["Placebo", "Xanomeline High Dose"])]

# 排除（对应 where TRT01P ne 'Placebo'）
adsl_f = adsl[adsl["TRT01P"] != "Placebo"]
adsl_f = adsl[~adsl["TRT01P"].isin(["Placebo"])]         # ~ 是取反

# 缺失判断
df = adsl[adsl["AGE"].notna()]          # 对应 where AGE is not missing
df = adsl[adsl["DTHFL"].isna()]         # 对应 where DTHFL is missing

# 区间
df = adsl[adsl["AGE"].between(65, 80)]                 # 含两端
df = adsl[adsl["AGE"].between(65, 80, inclusive="left")]

# 字符串模糊匹配（对应 SAS 的 where aedecod like '%NAUSEA%' 或 index(...)>0）
df = adsl[adsl["RACE"].str.contains("WHITE", na=False)]
df = adsl[adsl["RACE"].str.startswith("W", na=False)]
df = adsl[adsl["AEDECOD"].str.upper().str.contains("NAUSEA", na=False)]   # 正则用 regex=True
```

> 🔥 **三个必踩的坑**：
> 1. **必须用 `&` / `|`，不能用 `and` / `or`**
> 2. **每个条件必须加括号**（`&` 的优先级高于比较运算符）
> 3. **`.str.contains()` 遇到缺失值会返回 NaN，必须加 `na=False`**
>
> 这三点是 pandas 新手 90% 的报错来源。

### `query()`：更接近 SAS 写法（推荐用于复杂条件）

```python
# 用 query，可读性大幅提升（不用括号，可用 and/or）
adsl_f = adsl.query("SAFFL == 'Y' and AGE >= 65")
adsl_f = adsl.query("TRT01P in ['Placebo', 'Xanomeline High Dose']")
adsl_f = adsl.query("AGE >= @min_age")          # @ 引用外部 Python 变量
```

> 💡 **临床编程建议**：条件复杂时（3 个以上）用 `query()`，
> 因为**它更接近 SAS 的 `where` 子句，QC 时更容易对照原始需求**。

---

## 7.6 增删改变量

```python
# ---- 新增列（对应 SAS 的 data 步赋值）----
adsl["AGEGR1"] = np.where(adsl["AGE"] >= 65, ">=65", "<65")

# 从现有列计算
adsl["BMI"] = adsl["WEIGHTBL"] / (adsl["HEIGHTBL"] / 100) ** 2

# 用函数派生（逐元素，等价于 SAS 调用函数）
adsl["SITEID"] = adsl["USUBJID"].str.split("-").str[1]

# ---- 修改列 ----
adsl["SEX"] = adsl["SEX"].replace({"F": "Female", "M": "Male"})
adsl["RACE"] = adsl["RACE"].str.title()                  # 'WHITE' → 'White'

# ---- 重命名列（对应 SAS 的 rename= 选项）----
adsl = adsl.rename(columns={"USUBJID": "SUBJID", "TRT01P": "TRTP"})

# 批量重命名（统一大写——处理来源混乱的变量名）
adsl.columns = [c.upper() for c in adsl.columns]

# 批量加前缀（对应 SAS 的 rename=(x=y) 批量）
ae = ae.add_prefix("AE_")

# ---- 删除列 ----
adsl = adsl.drop(columns=["TEMP1", "TEMP2"])

# ---- 调整列顺序（对应 SAS 的 retain / keep 顺序）----
order = ["USUBJID", "TRT01P", "AGE", "SEX"]
adsl = adsl[order + [c for c in adsl.columns if c not in order]]

# ---- 排序（对应 PROC SORT）----
adsl = adsl.sort_values("AGE")
adsl = adsl.sort_values(["TRT01P", "AGE"], ascending=[True, False])   # 混合升降序
adsl = adsl.reset_index(drop=True)      # 重置行号（排序后行号会乱，建议重置）
```

---

## 7.7 复制：视图 vs 副本（最容易出 bug 的地方）

```python
sub = adsl[adsl["SAFFL"] == "Y"]         # 这可能是"视图"，也可能是副本
sub["NEW"] = 1                            # ⚠️ 可能触发 SettingWithCopyWarning

# ✅ 正确做法：显式复制
sub = adsl[adsl["SAFFL"] == "Y"].copy()
sub["NEW"] = 1                            # 安全，不影响 adsl

# ✅ 或者用 .loc 一步到位（在筛选的同时赋值）
adsl.loc[adsl["SAFFL"] == "Y", "NEW"] = 1
```

> 🧠 **心智模型**：pandas 的赋值有"视图/副本"二义性，
> 是它被诟病最多的地方。**实践原则：**
> 1. 筛选后要改内容 → 一定加 `.copy()`
> 2. 要在原数据上按条件赋值 → 用 `.loc[条件, 列名] = 值`

---

## 7.8 实战：一串完整的"体检 + 筛选"

```python
from pathlib import Path
import pandas as pd
import numpy as np

BASE = Path(__file__).resolve().parent.parent
adsl = pd.read_csv(BASE / "data" / "samples" / "adsl.csv")

# 1) 体检
print(f"ADSL: {adsl.shape[0]} 行 × {adsl.shape[1]} 列")
print("关键变量缺失情况：")
print(adsl[["USUBJID", "TRT01P", "AGE", "SEX", "SAFFL"]].isna().sum())

# 2) 主键唯一性校验（临床 QC 必做）
assert adsl["USUBJID"].is_unique, "ADSL 主键 USUBJID 不唯一！"

# 3) 筛选安全性人群
saf = adsl.query("SAFFL == 'Y'").copy()

# 4) 派生 AGEGR1（与 CDISC 惯例一致的边界：<65 / 65-80 / >80）
saf["AGEGR1"] = np.select(
    [saf["AGE"] < 65, saf["AGE"] <= 80],
    ["<65", "65-80"],
    default=">80",
)

# 5) 交叉验证：派生结果与原始 AGEGR1 是否一致（如果有）
if "AGEGR1" in adsl.columns:
    mismatch = (saf["AGEGR1"] != adsl.loc[saf.index, "AGEGR1"]).sum()
    print(f"与原始 AGEGR1 不一致的记录数：{mismatch}")

# 6) 结果核对
print(saf.groupby(["TRT01P", "AGEGR1"]).size().unstack(fill_value=0))
```

---

## 7.9 动手练习

1. **体检**：读 `data/samples/adsl.csv`，输出：行数、列数、
   每个变量的缺失数、AGE 的描述统计。

2. **筛选**：取出满足以下全部条件的受试者，并统计人数：
   - SAFFL = 'Y'
   - AGE >= 65
   - 非 Placebo 组

3. **派生**：用 `pd.cut` 从 AGE 派生 `AGEGR1`，标签为 `<65` / `65-80` / `>80`，
   注意 **65 应归入 `65-80`**（左闭右开）。然后与练习 2 的结果交叉制表。

4. **列操作**：把 ADSL 的列名全部转小写，然后只保留
   `usubjid, trt01p, age, sex, race, saffl` 六列，输出前 10 行。

5. **坑的练习**：故意写一段
   `x = adsl[adsl["AGE"] >= 65 and adsl["SEX"]=="F"]`，
   看报错信息并修正；再故意写一段不带 `na=False` 的
   `.str.contains()`，观察结果差异。

---

**上一章 ←** [第 06 章 · NumPy 与向量化思维](06-NumPy与向量化思维.md)
**下一章 →** [第 08 章 · 数据操作对照：DATA 步 / PROC SQL → pandas](08-数据操作对照-DATA步与PROC%20SQL.md)
