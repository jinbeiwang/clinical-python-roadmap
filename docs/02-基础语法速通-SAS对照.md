# 第 02 章 · 基础语法速通（SAS 对照）

> **本章目标**：用 2 小时补齐 Python 的语法肌肉。
> 你不需要"重新学编程"，只需要把已有概念换一套符号系统。
> **读法建议**：不要背诵，把所有代码敲一遍，遇到疑问回查本章。

---

## 2.1 语法骨架：三个第一眼差异

```sas
/* ============ SAS ============ */
data want;                      /* 语句以分号结束 */
    set have;                   /* 大小写不敏感 */
    x = age * 2;
    if x > 100 then flag = 'Y';
    else flag = 'N';
run;

%macro hi(name);                /* 宏定义 */
    %put Hello &name;
%mend;
```

```python
# ============ Python ============
# 语句以换行结束，缩进决定层级（没有 run; 也没有分号）
want = have.copy()              # 大小写敏感！have ≠ Have
want["x"] = want["age"] * 2
want["flag"] = ["Y" if v > 100 else "N" for v in want["x"]]

def hi(name):                   # 函数定义用 def
    print(f"Hello {name}")
```

| 差异 | SAS | Python |
|---|---|---|
| 语句结束 | `;` | 换行 |
| 代码块 | `if ... then do; ... end;` | `if ...:` + **缩进**（4 空格） |
| 大小写 | 不敏感（`AGE` = `age`） | **敏感**（`AGE` ≠ `age`） |
| 注释 | `* 注释;` 或 `/* 注释 */` | `# 单行` 或 `"""多行"""` |
| 运行单位 | `run;` 提交整个步 | 逐行执行到文件末尾 |
| 变量赋值 | `x = 1;` | `x = 1` |

> 🔥 **大小写敏感是临床程序员最容易犯的错**。
> SDTM 变量名习惯全大写（`USUBJID`），但 Python 里 `df["usubjid"]` 会直接报错
> `KeyError`。写代码时建议复制变量名，或统一改名（见第 07 章）。

---

## 2.2 变量与数据类型

**SAS 只有两种类型**（字符、数值），Python 的类型丰富得多：

| 类型 | 写法 | SAS 对应 | 说明 |
|---|---|---|---|
| `int` | `n = 42` | 数值（无小数） | 整数 |
| `float` | `x = 3.14` | 数值 | 浮点数，含 `nan` |
| `str` | `s = "ABC"` | 字符 | 字符串 |
| `bool` | `t = True` | 数值 0/1 | `True` / `False`（首字母大写） |
| `None` | `v = None` | `.` / `''` | "无值"，**唯一**的空值对象 |
| `list` | `[1, 2, 3]` | 数组 / 多值 | 有序可变 |
| `dict` | `{"a": 1}` | **Hash Object** | 键值对 |
| `tuple` | `(1, 2)` | — | 有序不可变 |
| `set` | `{1, 2}` | `PROC SORT nodupkey` | 去重集合 |

```python
# 检查类型 —— 等价于 SAS 的 PROC CONTENTS 看 type
print(type(42))         # <class 'int'>
print(type(3.14))       # <class 'float'>
print(type("A"))        # <class 'str'>
print(type(True))       # <class 'bool'>
print(type(None))       # <class 'NoneType'>

isinstance(42, int)     # True —— 类似 SAS 的 vtype() / vartype()
```

### 数值运算

```python
7 / 2      # 3.5   ← 真除法！与 SAS 一致（SAS 的 / 也是真除）
7 // 2     # 3     ← 整除（SAS 里对应 floor(7/2)）
7 % 2      # 1     ← 取余（SAS 的 MOD(7,2)）
2 ** 10    # 1024  ← 幂（SAS 的 2**10 也支持）
abs(-5)    # 5     ← SAS 的 ABS(-5)
round(3.14159, 2)  # 3.14  ← SAS 的 round(x, 0.01)，注意参数含义不同！
```

> ⚠️ **`round()` 的重要差异**：
> SAS 的 `ROUND(x, 0.01)` 第二个参数是**舍入单位**；
> Python 的 `round(x, 2)` 第二个参数是**小数位数**。
> 而且 Python 用的是**银行家舍入**（四舍六入五成双）：
> `round(0.5) == 0`，`round(1.5) == 2`。
> 临床报表对舍入极其敏感，**第 13 章会专门讲怎么复刻 SAS 的舍入规则**。

### 类型转换（`input` / `put` 的对应）

```sas
/* SAS */
x = input("123", best.);      /* 字符 -> 数值 */
y = put(123, 8.);             /* 数值 -> 字符 */
```

```python
int("123")        # 123      字符 -> 整数
float("3.14")     # 3.14     字符 -> 浮点
str(123)          # '123'    数值 -> 字符
bool(0)           # False    任意 -> 布尔（0/''/None 为 False）

# 批量转换 + 容错（对应 SAS 的 input(..., ??best.) 抑制日志）
import pandas as pd
pd.to_numeric("abc", errors="coerce")   # nan（无法转换时返回缺失，不报错）
```

> 💡 `errors="coerce"` 是 pandas 里极其常用的参数：
> **转换失败就变成缺失值**，等价于 SAS 的 `input(var, ??best.)`。
> 数据清洗场景几乎每处都要用。（`coerce` = 强制、胁迫）

---

## 2.3 字符串：与 SAS 字符函数对照

这是你最熟也最容易迁移的部分——**几乎所有 SAS 字符函数都有对应**。

| SAS 函数 | Python 写法 | 说明 |
|---|---|---|
| `length(s)` | `len(s)` | 长度（**注意**：SAS 返回存储长度含尾部空格，Python 不含）|
| `strip(s)` | `s.strip()` | 去首尾空格（**SAS 的 `strip` = Python 的 `strip`**）|
| `trim(s)` | `s.rstrip()` | 去尾部空格 |
| `left(s)` | `s.lstrip()` | 去首部空格 |
| `upcase(s)` | `s.upper()` | 转大写 |
| `lowcase(s)` | `s.lower()` | 转小写 |
| `propcase(s)` | `s.title()` | 首字母大写 |
| `substr(s, 1, 3)` | `s[0:3]` | 取子串（**索引从 0 开始！**）|
| `substr(s, 1)` | `s[0]` | 取第一个字符（**注意 SAS 从 1 开始，Python 从 0**）|
| `scan(s, 2, ',')` | `s.split(",")[1]` | 按分隔符取第 n 个（第 2 个 → 索引 1）|
| `cat(a, b)` | `a + b` 或 `f"{a}{b}"` | 拼接 |
| `cats(a, b)` | `"".join([a.strip(), b.strip()])` | 拼接并去空格 |
| `catx('-', a, b)` | `"-".join([a, b])` | 带分隔符拼接 |
| `tranwrd(s, 'X', 'Y')` | `s.replace("X", "Y")` | 替换全部 |
| `index(s, 'X')` | `s.find("X")` | 查找位置（**找不到返回 -1，SAS 返回 0**）|
| `find(s, 'X')` | `s.find("X")` | 查找 |
| `compress(s)` | `"".join(s.split())` | 去所有空格 |
| `compress(s, '0123456789')` | `re.sub(r"\D", "", s)` | 只保留数字 |
| `verify` / `anyalpha` | 正则表达式 `re` | 见下 |
| `repeat(s, 2)` | `s * 2` | 重复 |
| `reverse(s)` | `s[::-1]` | 反转 |
| `prxmatch(p, s)` | `re.search(p, s)` | 正则匹配 |

### 索引从 0 开始——请立刻记住

```python
s = "CDISCPILOT01"

s[0]        # 'C'      第 1 个字符（SAS 是 substr(s,1,1)）
s[1]        # 'D'      第 2 个字符
s[-1]       # '1'      最后一个字符 ← Python 独有，非常好用
s[0:4]      # 'CDIS'   第 1~4 个字符（左闭右开：含 0，不含 4）
s[4:]       # 'CPILOT01'  从第 5 个到结尾
s[:4]       # 'CDIS'   从开头到第 4 个

# 对应 SAS：substr(s, 1, 4)  →  s[0:4]
#          substr(s, 5)     →  s[4:]
```

> 🔥 **记住这个换算公式**：`Python 切片起点 = SAS substr 起点 − 1`；
> 且 Python 的结束位置是**不包含**的，而 SAS 的长度是**包含**的。
> `substr(s, 1, 4)` → `s[0:0+4]` → `s[0:4]`。

### 字符串方法速查（贯穿日常）

```python
s = "  Nausea  "

s.strip()                 # 'Nausea'
s.lstrip()                # 'Nausea  '
s.strip().upper()         # 'NAUSEA'
s.strip().lower()         # 'nausea'
"NAUSEA".istitle()        # False
"ab" in "abc"             # True    ← 子串判断（比 index()>0 更 Pythonic）
"abc".startswith("a")     # True
"abc".endswith("c")       # True
"a,b,,c".split(",")       # ['a', 'b', '', 'c']  ← 注意空元素会被保留
"-".join(["a", "b"])      # 'a-b'
"AEDECOD".replace("_", "")  # 'AEDECOD'
"  a  b  ".split()        # ['a', 'b']  ← 不传参时按任意空白切分，自动去空

# 对应 SAS 的 compress(s)：去掉所有空白
"".join("01 701 1015".split())   # '017011015'
```

### f-string：`put()` 的现代替代品

```python
subjid, age, sex = "1015", 63, "F"

# SAS: %let line = Subject &subjid. is &age. years old, sex &sex.;
line = f"Subject {subjid} is {age} years old, sex {sex}"
print(line)   # Subject 1015 is 63 years old, sex F

# 格式化数字（对应 SAS 的 put(3.14159, 8.2)）
print(f"{3.14159:.2f}")     # 3.14    保留 2 位小数
print(f"{1234567:,}")       # 1,234,567   千分位
print(f"{0.1234:.1%}")      # 12.3%   百分比
print(f"{'N':>5}|{'N':<5}") # 右对齐 | 左对齐（做文本报表时有用）

# 表格加宽（临床报表常见需求，对应 SAS 的 $10. 格式）
print(f"{'TRT01P':<25}{'N':>6}{'Mean':>10}")
print(f"{'Placebo':<25}{79:>6}{75.2:>10.1f}")
```

### 正则表达式：SAS `prx*` 函数的替代

```python
import re

ae = "MODERATE, SEVERE"

re.search(r"SEVERE", ae)              # <re.Match>  对应 prxmatch
re.sub(r"\s+", " ", ae)               # 'MODERATE, SEVERE'  对应 prxchange
re.findall(r"\b[A-Z]{4,}\b", ae)      # ['MODERATE', 'SEVERE']

# 实用例：从 USUBJID 提取 SITEID 和 SUBJID（01-701-1015）
usubjid = "01-701-1015"
m = re.match(r"^(\d+)-(\d+)-(\d+)$", usubjid)
if m:
    study, site, subj = m.groups()     # ('01', '701', '1015')

# 或者更简单的 split（USUBJID 结构固定时更推荐）
study, site, subj = usubjid.split("-")
```

> 💡 **优先级建议**：能不用正则就不用。
> `split()` / `startswith()` / `strip()` 可读性远好于正则，
> 而临床代码的可读性 = 可 QC 性。

---

## 2.4 布尔与比较

```python
# 比较运算（与 SAS 基本一致）
2 > 1        # True
"a" == "a"   # True   ← 相等是 ==，不是 =
2 != 3       # True   ← 不等是 !=（SAS 是 ^= 或 ne）
2 <= 3       # True

# 逻辑运算：SAS 用 and/or/not 或 &/|/^，Python 必须用关键字
True and False    # False    ← SAS: True & False 或 True and False
True or False     # True
not True          # False    ← SAS: not True 或 ^True

# 链式比较（Python 独有，很好用）
0 <= age <= 100      # 等价于 age >= 0 and age <= 100
```

> ⚠️ **`&` / `|` 在 pandas 里的坑**：
> 对 Series 做逐元素逻辑运算时**必须用 `&` / `|`，不能用 `and` / `or`**，
> 且**每个条件都要加括号**：
> ```python
> df[(df["AGE"] >= 65) & (df["SEX"] == "F")]   # ✅ 正确
> df[df["AGE"] >= 65 and df["SEX"] == "F"]     # ❌ ValueError
> df[df["AGE"] >= 65 & df["SEX"] == "F"]       # ❌ 运算符优先级错误
> ```
> 这是 pandas 新手最高频的报错，**第 08 章会详细展开**。

### 真值判断（SAS 里不存在的概念）

Python 里很多东西可以当布尔用：

```python
# 以下都是 False（"假值"）
bool(0), bool(0.0), bool(""), bool([]), bool({}), bool(None), bool(float("nan"))
# 其余都是 True

# 实用：判断"有没有内容"
name = ""
if not name:
    print("名字为空")
```

---

## 2.5 缺失值：Python 的 `.` 是怎么表示的

```python
import numpy as np

# 三种"空"的表示，用途不同
v1 = None          # Python 原生空值，用于对象（字符串、日期列表元组）
v2 = np.nan        # 浮点缺失，用于数值（pandas 数值列的缺失值）
v3 = pd.NA         # pandas 的通用缺失值（可空整型/布尔）

# 判断：永远用 pd.isna()，绝不用 ==
pd.isna(v1)        # True
pd.isna(v2)        # True
v2 == v2           # False  ← 注意！NaN 不等于自己
```

### 与 SAS 的关键差异表

| 场景 | SAS | Python / pandas |
|---|---|---|
| 数值缺失 | `.` | `np.nan` |
| 字符缺失 | `''`（空串即缺失） | `None` / `np.nan`；**`''` 不算缺失** |
| 判断 | `if x = . then` | `if pd.isna(x):` |
| `if x = '' then` | 捕获字符缺失 | **不适用**，需 `if x == "" or pd.isna(x):` |
| 排序 | `.` 视为最小值排在最前 | `NaN` 默认排在**最后**（`sort_values(na_position="first")` 可改） |
| 计算 | `.` 参与运算结果仍为 `.` | `NaN + 1 = NaN`（一致） |
| 分组 | `PROC MEANS` 自动排除 `.` | `groupby().mean()` 默认 `skipna=True`（一致） |

```python
import pandas as pd

# SAS 的这段：
#   if nmiss(age, weight) > 0 then flag = 'Y';
# 在 pandas 中：
df["has_missing"] = df[["AGE", "WEIGHTBL"]].isna().any(axis=1)

# 数缺失（对应 PROC MEANS 的 NMISS）
df.isna().sum()            # 每列缺失数

# 用 0 填充（对应 if x = . then x = 0;）
df["AVAL"] = df["AVAL"].fillna(0)
```

---

## 2.6 输出与调试：`%put` 的替代

```python
# 1) 最直接：print
subjid, age = "1015", 63
print("SUBJID =", subjid, "AGE =", age)     # 逗号分隔，自动加空格
print(f"SUBJID = {subjid}, AGE = {age}")    # 推荐：f-string

# 2) 看变量"是什么"（调试必用，对应 PROC CONTENTS）
x = [1, 2, 3]
print(type(x), len(x), x)

# 3) 结构化调试（推荐用于正式脚本）
import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
logging.info("读取 dm.xpt 完成，%d 行", 306)
logging.warning("ADSL 中 %d 条记录 TRT01P 为空", 3)
# 输出：INFO | 读取 dm.xpt 完成，306 行
```

> 💡 **日志优于 print**：正式脚本（尤其是要 QC 的代码）请用 `logging`——
> 它能分级（DEBUG/INFO/WARNING/ERROR）、能写文件、能由调用方控制，
> 这正是 SAS 日志分级（NOTE/WARNING/ERROR）的思路。

---

## 2.7 动手练习

1. **字符串处理**（对比 SAS 写法）：
   给定 `usubjid = "01-701-1015"`，分别取出 study、site、subjid 三段。
   再给定 `aedecod = "  nausea  "`，输出全大写且无空格的形式。

2. **类型转换**：
   给定字符串列表 `["63", "71", "abc", "58"]`，转成数值列表，
   无法转换的变成缺失值，并统计有几个缺失。

3. **格式化输出**：
   输出这样一张表（列名左对齐 20 字符、数字右对齐 8 字符）：
   ```
   Treatment               N     Mean
   Placebo                79     75.2
   Xanomeline Low Dose    81     76.4
   Xanomeline High Dose   84     74.1
   ```

4. **缺失值判断**：
   `SAS 的 if age = . then flag = 'Y';` 和
   `if name = '' then flag = 'Y';` 分别怎么用 Python/pandas 表达？

---

**上一章 ←** [第 01 章 · 环境搭建与思维转换](01-环境搭建与思维转换.md)
**下一章 →** [第 03 章 · 核心数据结构](03-核心数据结构-list-dict-str.md)
