# 第 06 章 · NumPy 与向量化思维

> **本章目标**：完成**最重要的一次思维升级**。
> SAS 的 DATA 步天然逐行处理；Python 的正道是**整列一起算**。
> 理解这一点，你的 Python 代码才会从"能跑"变成"专业"。

---

## 6.1 为什么不能"用 Python 写 SAS"

先看一段真实的性能对比。任务：对 58,700 行的 ADLBC（实验室检查）
计算每个值的相对基线变化百分比。

```python
import time
import numpy as np
import pandas as pd

df = pd.read_csv("data/samples/adlbc_sample.csv")     # 假设有 AVAL, BASE 两列
n = len(df)

# ---- 写法 A：SAS 思维（逐行循环）----
t0 = time.perf_counter()
result_a = []
for i in range(n):
    base = df.loc[i, "BASE"]
    aval = df.loc[i, "AVAL"]
    result_a.append((aval - base) / base * 100 if base else np.nan)
t_a = time.perf_counter() - t0

# ---- 写法 B：向量化 ----
t0 = time.perf_counter()
result_b = (df["AVAL"] - df["BASE"]) / df["BASE"] * 100
t_b = time.perf_counter() - t0

print(f"逐行循环: {t_a:.3f} 秒")
print(f"向量化  : {t_b:.4f} 秒")
print(f"加速比  : {t_a / t_b:.0f} 倍")
```

在 6 万行的真实数据上，差距通常是 **100–1000 倍**。
而且数据越大差距越大——因为循环是 Python 逐条解释执行，
向量化是**下层 C 代码一次算完整个数组**。

> 🔥 **记住这条铁律**：
> **在 pandas/numpy 里看到 `for` 循环遍历行，就是代码有问题的信号。**
> （少数例外：需要跨行状态、调用外部 API、写文件。）

---

## 6.2 NumPy 是什么，为什么要先学它

NumPy 提供 `ndarray`（N 维数组）——**同类型、定长的连续内存块**。
pandas 的 `DataFrame` 本质是"若干个 ndarray 组成的字典"，
所以**理解了 NumPy 就理解了 pandas 的底层行为**。

| | SAS | NumPy |
|---|---|---|
| 数值容器 | 数据集里的数值列 | `ndarray`（一维/多维） |
| 元素类型 | 统一为数值（无 int/float 之分） | **必须统一**（int64/float64/…） |
| 缺失值 | `.` | `np.nan`（仅 float 支持）|
| 运算 | 逐行（DATA 步）| **整数组**（向量化）|
| 数组声明 | `array x{10};` | `np.zeros(10)`、`np.arange(10)` |
| 索引 | `x{1}`（从 1 开始）| `x[0]`（从 0 开始）|

```python
import numpy as np

# 创建
a = np.array([63, 64, 71, 74, 55])          # 从列表
b = np.zeros(5)                              # [0. 0. 0. 0. 0.]
c = np.ones(5)                               # [1. 1. 1. 1. 1.]
d = np.arange(5)                             # [0 1 2 3 4]
e = np.linspace(0, 1, 5)                     # [0. 0.25 0.5 0.75 1.]
f = np.full(5, 3.14)                         # 全部 3.14
g = np.random.default_rng(42).normal(75, 8, 5)   # 正态随机（等价 SAS RAND('NORMAL')）

# 关键属性
a.dtype        # dtype('int64')    元素类型（SAS 里没有这个概念）
a.shape        # (5,)              形状
a.ndim         # 1                 维度
a.size         # 5                 元素个数
len(a)         # 5
```

### 索引与切片：与 SAS 数组对照

```python
a = np.array([10, 20, 30, 40, 50])

# SAS 数组 x{1}=10, x{2}=20 ...
a[0]        # 10      第 1 个元素（对应 SAS x{1}）
a[-1]       # 50      最后一个（SAS 没有）
a[1:4]      # [20 30 40]   第 2~4 个（含头不含尾）
a[:3]       # [10 20 30]
a[2:]       # [30 40 50]

# 布尔索引（对应 SAS 的 where 子句）
a[a > 25]              # [30 40 50]
a[(a > 20) & (a < 50)] # [30 40]

# 花式索引（按位置列表取）
a[[0, 2, 4]]           # [10 30 50]
```

---

## 6.3 向量化：一次算一整列

### 算术运算：与 SAS 的逐元素运算结果一致

```python
age = np.array([63, 64, 71, 74, 55])

age + 1        # [64 65 72 75 56]
age * 12       # 月龄
age / 10
age ** 2
np.sqrt(age)          # 对应 SAS SQRT()
np.log(age)           # 对应 SAS LOG()
np.exp(age)           # 对应 SAS EXP()
np.abs(age - 70)      # 对应 SAS ABS()
```

### 聚合函数：对应 PROC MEANS

```python
age = np.array([63, 64, 71, 74, 55])

age.sum()      # 327      Sum
age.mean()     # 65.4     Mean
age.std(ddof=1)  # 样本标准差（对应 SAS STD，SAS 默认 ddof=1）
age.min()      # 55       Minimum
age.max()      # 74       Maximum
np.median(age) # 64.5     Median
np.percentile(age, [25, 50, 75])   # 四分位数（对应 PROC UNIVARIATE 的 Q1/Q2/Q3）
np.quantile(age, 0.5)

# 缺失值处理
vals = np.array([63, np.nan, 71])
vals.sum()             # nan   ← 默认不忽略缺失！
np.nansum(vals)        # 134   ← 忽略缺失（对应 SAS 的默认行为）
np.nanmean(vals)       # 67.0
np.isnan(vals)         # [False  True False]
np.isnan(vals).sum()   # 1     缺失个数（对应 NMISS）
```

> ⚠️ **重要差异**：`SAS 的 PROC MEANS` 默认排除缺失值；
> `numpy` 的 `sum()` / `mean()` **默认不排除**（会得到 nan）。用 `nan*` 函数族。
> pandas 的 `mean()` 默认 `skipna=True`（与 SAS 一致），所以 pandas 里反而不用操心。

### 广播（Broadcasting）：Python 独有的能力

"不同形状的数组做运算"——这是 SAS 里没有的概念，用好了极省代码：

```python
# 场景：每个受试者的年龄按"中心"的调整值校准
ages = np.array([63, 64, 71, 74, 55])              # 5 个受试者
site_adj = np.array([0.5, -0.5])                    # 2 个中心的调整值

# 标量广播（最简单，SAS 也能做）
ages - 65

# 数组广播（SAS 需要 MERGE 才能做）
adjusted = ages.reshape(5, 1) - site_adj.reshape(1, 2)
# 结果 5×2 矩阵：每个年龄减去 2 个中心的调整值

# 实用例：BMI 标准化（减均值除标准差——Z-score）
z = (ages - ages.mean()) / ages.std(ddof=1)
print(np.round(z, 3))
```

---

## 6.4 条件逻辑的向量化：`np.where` / `np.select`

这是**替换 DATA 步 if-then-else 的主力**。

### 单条件 → `np.where`

```sas
if age >= 65 then agegr = '>=65';
else agegr = '<65';
```

```python
age = np.array([63, 64, 71, 74, 55])

# np.where(条件, 真值, 假值)  —— 完全对应 if/else
agegr = np.where(age >= 65, ">=65", "<65")
# array(['<65', '<65', '>=65', '>=65', '<65'])
```

### 多条件 → `np.select`

```sas
if age < 65 then agegr = '<65';
else if age <= 80 then agegr = '65-80';
else agegr = '>80';
```

```python
conditions = [age < 65, age <= 80]          # 按顺序判断（先匹配先生效）
choices    = ["<65", "65-80"]
agegr = np.select(conditions, choices, default=">80")
# array(['<65', '<65', '65-80', '65-80', '<65'])
```

> 📌 **`np.select` 的顺序语义和 SAS 完全一致**：
> 条件按列表顺序求值，**第一个为真的生效**，都不满足用 `default`。
> 这正好对应 `if / else if / else`。

### 嵌套 where

```python
# 对应嵌套 if / 或 SAS 的 ifc()
label = np.where(age >= 65, "老年", np.where(age >= 18, "成年", "未成年"))
```

### 连续变量分箱 → `pd.cut` / `pd.qcut`（比 SAS 的 format 更直观）

```python
import pandas as pd

age = pd.Series([63, 64, 71, 74, 55, 82, 90])

# 按自定义区间分箱（对应 SAS 自定义 format 的区间映射）
agegr = pd.cut(age, bins=[0, 65, 80, 200], labels=["<65", "65-80", ">80"],
               right=False)          # right=False → 左闭右开：[0,65)
print(agegr.value_counts().sort_index())

# 按分位数等频分箱（对应 PROC RANK groups=）
quart = pd.qcut(age, q=4, labels=["Q1", "Q2", "Q3", "Q4"])
```

> 🔥 **临床编程提示**：ADaM 里 `AGEGR1` 这类派生，用 `pd.cut` 最安全，
> 因为**边界规则写在参数里，一眼可读、便于 QC**；
> 而 `np.select` 嵌套多层时容易漏掉边界。两者都要注意"边界取闭还是取开"——
> 这决定了 65 岁的人归到 `<65` 还是 `65-80`。

---

## 6.5 向量化常用函数清单

| 需求 | SAS | NumPy / pandas |
|---|---|---|
| 条件赋值 | `if/then/else` | `np.where`、`np.select` |
| 分箱 | 自定义 format | `pd.cut`、`pd.qcut` |
| 取整 | `INT()`、`FLOOR()`、`CEIL()` | `np.floor`、`np.ceil`、`np.trunc`、`np.round` |
| 最大值/最小值 | `MAX()`、`MIN()`（SAS 的这两个是逐行取最大） | `np.maximum(a,b)`、`np.minimum(a,b)` |
| 累加/累乘 | `SUM` 语句 | `np.cumsum`、`np.cumprod` |
| 排序 | `PROC SORT` | `np.sort`、`df.sort_values` |
| 排名 | `PROC RANK` | `df.rank()` |
| 去重 | `nodupkey` | `np.unique`、`df.drop_duplicates` |
| 查找匹配 | `WHICHN()`、`whichc()` | `np.isin`、`df.isin` |
| 缺失判断 | `MISSING()`、`NMISS()` | `pd.isna`、`df.isna().sum()` |
| 字符串拼接 | `CATX()` | `np.char.add` 或 pandas `str` 方法 |
| 百分位 | `PCTL` | `np.percentile`、`np.quantile` |

```python
import numpy as np

# 逐行取最大（SAS 的 max(a,b,c) 是行级的！）
a = np.array([1, 5, 3])
b = np.array([4, 2, 6])
np.maximum(a, b)        # array([4, 5, 6])   ← 对应 SAS: m = max(a, b)

# numpy 的 max 是"整体最大"，容易混淆：
np.max(a)               # 5  ← 对应 SAS: max_a = max(of a[*]);
```

---

## 6.6 性能实操：什么时候循环是可以接受的

```python
# ✅ 可以循环：需要跨行状态（上一行的值）
# SAS: retain prev;
def forward_fill(series):
    """用上一个非缺失值填充（对应 SAS 的 retain + if missing then 保持）"""
    out, last = [], None
    for v in series:
        if pd.notna(v):
            last = v
        out.append(last)
    return pd.Series(out)

# pandas 内置更快：series.ffill()

# ✅ 可以循环：调用外部 API / 写多个文件 / 处理多个数据集
for dom in ["dm", "ae", "cm"]:
    process(dom)

# ❌ 不该循环：任何"对每一行做相同的算术/条件变换"
for i in range(len(df)):
    df.loc[i, "x"] = df.loc[i, "a"] + df.loc[i, "b"]     # 用 df["a"] + df["b"]
```

### 分组内计算（`groupby().transform()`）：SAS 的 BY 组处理

这是"看起来必须用循环"但其实不该用的经典场景。

```sas
/* SAS：按受试者计算基线，再看每次访视相对基线的变化 */
proc sort data=lb; by usubjid visitnum; run;
data lb2;
    set lb;
    by usubjid;
    retain base;
    if first.usubjid then base = aval;
    chg = aval - base;
run;
```

```python
# pandas：完全无需循环
lb = lb.sort_values(["USUBJID", "VISITNUM"])
lb["BASE"] = lb.groupby("USUBJID")["AVAL"].transform("first")   # 每组第一个值
lb["CHG"]  = lb["AVAL"] - lb["BASE"]

# 其他常用的组内变换
df["n_in_group"]  = df.groupby("TRT01P")["USUBJID"].transform("count")
df["group_mean"]  = df.groupby("TRT01P")["AGE"].transform("mean")
df["age_diff"]    = df["AGE"] - df["group_mean"]     # 组内去均值（中心化）
df["rank_in_grp"] = df.groupby("TRT01P")["AGE"].rank(ascending=False)
```

> 🔥 **`transform` 是"组内计算后广播回原行数"**，正好对应 SAS 的
> `BY 组 + retain + first.xxx` 模式，且不需要 `PROC SORT`（但排序后语义更清晰）。
> **掌握 `groupby().transform()` 是 pandas 从入门到进阶的标志。**

---

## 6.7 动手练习

1. **向量化改写**：把下面 SAS 用一行 pandas 写出来。
   ```sas
   data want; set have;
       bmi = weight / (height/100)**2;
       if bmi < 18.5 then bmigr = 'Underweight';
       else if bmi < 25 then bmigr = 'Normal';
       else bmigr = 'Overweight';
   run;
   ```

2. **分位数**：用 `np.percentile` 计算 ADSL 中 AGE 的 Q1/Q2/Q3 和 5%/95% 分位。

3. **广播**：给定 3 个受试者的多次测量值（2 维数组 3×4），
   计算每个受试者内心自己的值（减自己的均值）。

4. **性能对比**：用 `time.perf_counter()` 实测 6.1 节的两种写法，
   记录加速比。（如果本地还没大文件，可用 `np.random` 造 10 万个数据）

5. **组内变换**：读 `data/samples/advs.csv`（如无则用 adae），
   用 `groupby().transform()` 增加一列"该受试者的最大 AVAL"。

---

**上一章 ←** [第 05 章 · 文件与批处理自动化](05-文件与批处理自动化.md)
**下一章 →** [第 07 章 · pandas 入门：DataFrame 就是数据集](07-pandas入门-DataFrame就是数据集.md)
