# 第 03 章 · 核心数据结构：list / dict / str

> **本章目标**：掌握 Python 的三种"容器"思维。
> 这是 Python 区别于 SAS 的地方——SAS 只有数据集，Python 有很多种装东西的方式。
> **重要**：`dict` 就是 SAS 的 **Hash Object**，你其实已经会了。

---

## 3.1 为什么需要容器：SAS 与 Python 的差异

SAS 世界里"数据"几乎只有一种形态：**数据集（行 × 列）**。
需要装别的东西时，你用 `ARRAY`、宏变量或者干脆硬编码。

Python 世界里，**不同类型的容器适合不同的任务**，选错了会让代码又长又慢：

| 容器 | 形状 | 适合 | SAS 对应 |
|---|---|---|---|
| `list` 列表 | 有序、可变、可重复 | 一串同质的东西（文件清单、变量名、结果） | `ARRAY` / 多值 |
| `tuple` 元组 | 有序、**不可变** | 不想被改动的固定组合（坐标、配置项） | — |
| `dict` 字典 | 键→值映射 | 查找表、配置、计数、变量标签 | **Hash Object** |
| `set` 集合 | 无序、**自动去重** | 去重、集合运算（找差异） | `PROC SORT nodupkey` |

---

## 3.2 list：最常用，对应 SAS 的 ARRAY

```python
# 创建
trts = ["Placebo", "Xanomeline Low Dose", "Xanomeline High Dose"]
ages = [63, 64, 71, 74]
mixed = ["A", 1, 3.14, True, None]        # 可以混装（但通常不建议）

# 索引（从 0 开始，与字符串一致）
trts[0]        # 'Placebo'
trts[-1]       # 'Xanomeline High Dose'
trts[0:2]      # ['Placebo', 'Xanomeline Low Dose']

# 长度
len(trts)      # 3

# 增删改
trts.append("Screen Failure")     # 追加到末尾（对应 SAS 数组扩容）
trts.remove("Screen Failure")     # 按值删除
popped = trts.pop()               # 弹出末尾元素
trts[0] = "PLACEBO"               # 直接改
trts.insert(0, "All")             # 插入到位置 0

# 查找
"Placebo" in trts                 # 成员判断（SAS: if 'Placebo' in trts then）
trts.index("Placebo")             # 位置（找不到会抛 ValueError）

# 排序
ages_sorted = sorted(ages)                    # 返回新列表，原列表不变
ages_sorted_desc = sorted(ages, reverse=True)
trts.sort()                                   # 原地排序，返回 None（注意！）

# 合并与重复
["a"] + ["b"]      # ['a', 'b']
["a"] * 3          # ['a', 'a', 'a']
```

### 列表推导式：最实用的 Python 特性

这是 Python 最值得掌握的一个语法糖，**它对应 SAS 的"DATA 步 + output"**：

```sas
/* SAS：给每个受试者算 BMI，输出到新数据集 */
data want;
    set have;
    bmi = weight / (height/100)**2;
    output;
run;
```

```python
# Python：一行搞定
bmi_list = [w / (h / 100) ** 2 for w, h in zip(weights, heights)]

# 带条件（对应 SAS 的 where）
adult_ages = [a for a in ages if a >= 65]

# 带分支（对应 SAS 的 if/else）
grp = ["<65" if a < 65 else ">=65" for a in ages]

# 嵌套（两层循环）
pairs = [(x, y) for x in [1, 2] for y in ["A", "B"]]
# [(1, 'A'), (1, 'B'), (2, 'A'), (2, 'B')]
```

**配合临床场景的真实例子**——为每个受试者派生 AGEGR1：

```python
ages = [63, 64, 71, 74, 55]
agegr = ["<65" if a < 65 else ("65-80" if a <= 80 else ">80") for a in ages]
# ['<65', '<65', '65-80', '65-80', '<65']
```

### `zip()`：并行遍历多个列表

```python
subjids = ["1015", "1023", "1028"]
ages    = [63, 64, 71]

for sid, age in zip(subjids, ages):
    print(f"{sid}: {age}")

# 转成字典
subj_age = dict(zip(subjids, ages))    # {'1015': 63, '1023': 64, '1028': 71}

# 转成"成对"的列表
list(zip(subjids, ages))               # [('1015', 63), ('1023', 64), ('1028', 71)]
```

> 💡 `zip` 在处理"数据集的一行"时特别有用：把几列取出来一起遍历。
> 但在 pandas 里通常不需要它——**向量化更快**（第 06 章）。

---

## 3.3 dict：这就是 SAS 的 Hash Object

如果你用过 `DECLARE HASH`，那么 dict 会让你觉得"这不就是哈希对象吗"。**是的。**

```sas
/* SAS Hash Object：按 USUBJID 查 TRT01P */
data _null_;
    if _n_ = 1 then do;
        declare hash h (dataset:'adsl');
        h.definekey('USUBJID');
        h.definedata('TRT01P');
        h.definedone();
    end;
    set ae;
    if h.find(key:USUBJID) = 0 then output;
run;
```

```python
# Python dict：同样的键→值映射
trt_map = {
    "01-701-1015": "Placebo",
    "01-701-1023": "Placebo",
    "01-701-1028": "Xanomeline High Dose",
}

trt_map["01-701-1015"]                   # 取值（键不存在会 KeyError）
trt_map.get("01-999-9999")               # None —— 安全取值（对应 h.find()返回非0）
trt_map.get("01-999-9999", "UNKNOWN")    # 给默认值

# 增删改
trt_map["01-701-1033"] = "Xanomeline Low Dose"    # 新增
del trt_map["01-701-1033"]                        # 删除
"01-701-1015" in trt_map                          # 存在性判断

# 遍历
for sid, trt in trt_map.items():
    print(sid, trt)
list(trt_map.keys())      # 所有键
list(trt_map.values())    # 所有值
```

> ⚠️ **性能提醒**：dict 是 O(1) 查找，而 `.get()` 比 `[]` 安全。
> **不要用 `for` 循环+`if` 去"查找"，那正是 dict 存在的意义。**

### 实战：用 dict 做变量标签（替代 SAS label）

```python
# SAS: label AGE = 'Age';  SEX = 'Sex';
var_labels = {
    "USUBJID": "Unique Subject Identifier",
    "AGE":     "Age",
    "SEX":     "Sex",
    "RACE":    "Race",
    "TRT01P":  "Planned Treatment for Period 01",
}

# 生成表头（对应 SAS 报表里用 label 替换变量名）
columns = ["USUBJID", "AGE", "SEX"]
header = [var_labels.get(c, c) for c in columns]
# ['Unique Subject Identifier', 'Age', 'Sex']
```

### 实战：用 dict 计数（对应 PROC FREQ）

```python
from collections import Counter

trts = ["Placebo", "Placebo", "Xanomeline High Dose", "Placebo", "Xanomeline Low Dose"]

cnt = Counter(trts)
# Counter({'Placebo': 3, 'Xanomeline High Dose': 1, 'Xanomeline Low Dose': 1})

cnt["Placebo"]              # 3
cnt.most_common(2)          # [('Placebo', 3), ('Xanomeline High Dose', 1)]

# 手写版本（理解原理）
manual = {}
for t in trts:
    manual[t] = manual.get(t, 0) + 1
```

### 嵌套 dict：表达层级结构

```python
# 表达"每个受试者的每个访视的结果"——这是数据集之外的新能力
data = {
    "01-701-1015": {"VISIT1": 24, "VISIT2": 22, "VISIT3": None},
    "01-701-1023": {"VISIT1": 27, "VISIT2": 25},
}
data["01-701-1015"]["VISIT2"]      # 22
```

---

## 3.4 set：去重与集合运算

```python
trts = {"Placebo", "Xanomeline High Dose", "Placebo"}     # 自动去重
# {'Placebo', 'Xanomeline High Dose'}

len(trts)              # 2
"Placebo" in trts      # True —— 比 list 快得多

# 从列表建集合（对应 PROC SORT nodupkey）
unique_sites = set(["701", "701", "702", "703", "702"])   # {'701', '702', '703'}

# 集合运算 —— 做数据比对时极其好用
sdtm_subjects = {"1015", "1023", "1028", "1033"}
adam_subjects = {"1015", "1023", "1028"}

adam_subjects & sdtm_subjects     # 交集 {'1015', '1023', '1028'}
adam_subjects - sdtm_subjects     # 差集 set()  （ADaM 里有但 SDTM 里没有的）
sdtm_subjects - adam_subjects     # {'1033'}  （SDTM 有但 ADaM 漏掉的！QC 重点）
```

> 🔥 **这是 QC 场景的杀手级应用**：比对两个数据集的受试者清单，
> 一行代码找出"A 有 B 没有"的记录。等价于 SAS 的
> `PROC SQL; select USUBJID from a except select USUBJID from b; quit;`

### 一个必知的坑：set 是无序的

```python
{s for s in "abc"}        # 顺序不保证！
# 需要有序 + 去重时：用 dict.fromkeys（Python 3.7+ 保序）
ordered_unique = list(dict.fromkeys(["b", "a", "b", "c"]))   # ['b', 'a', 'c']
```

---

## 3.5 tuple：不可变的"固定组合"

```python
# 常用于函数返回多个值、坐标、配置
point = (1, 2)

# 拆包（unpacking）—— 高频实用
site, subject = ("701", "1015")

# 这个特性在读取数据/遍历时非常好用
for key, value in {"a": 1, "b": 2}.items():
    print(key, value)

# 交换变量（SAS 里要写临时变量）
a, b = 1, 2
a, b = b, a        # a=2, b=1
```

---

## 3.6 三种容器的选择指南

| 你的需求 | 用 | 例子 |
|---|---|---|
| 一串有顺序的东西 | `list` | 文件路径清单、变量名清单 |
| 查表 / 映射 / 计数 | `dict` | USUBJID → 治疗组、变量 → 标签 |
| 去重 / 找差异 | `set` | 受试者清单比对 |
| 固定的多个值 | `tuple` | 函数的多个返回值 |
| **表格数据（行×列）** | **pandas DataFrame** | **第 07 章** |

> ⚠️ **重要提醒**：真正的临床数据（几千行、几十列）**不要用 list/dict 手搓**——
> 那是 pandas 的工作。本章的容器主要用于：
> - 配置信息（变量标签、治疗组顺序）
> - 中间结果（计数、清单）
> - 文件与路径的批量处理（第 05 章）
> - 函数内部的小数据

---

## 3.7 动手练习

1. **list 基础**：给定治疗组列表
   `["Placebo", "Xanomeline High Dose", "Xanomeline Low Dose"]`，
   分别输出它的第 1 个、最后一个元素，以及"包含 Placebo 吗"。

2. **列表推导式**：给定 `ages = [63, 64, 71, 74, 55, 82]`，
   用一行推导式生成 `AGEGR1`：`<65` / `65-80` / `>80`。

3. **dict 计数**：给定不良事件严重程度列表
   `["MILD", "MILD", "MODERATE", "SEVERE", "MILD"]`，
   统计每个等级的例数（用 `Counter` 和手写两种方式）。

4. **set 做 QC**：给定两个受试者清单（一个 SDTM DM 的、一个 ADaM ADSL 的），
   找出"在 SDTM 但不在 ADaM"的受试者（这是真实的 QC 场景）。

5. **综合**：把 `dict` 形式的变量标签用在输出上——
   给定列名列表，输出对应的中文/英文标签表头。

---

**上一章 ←** [第 02 章 · 基础语法速通](02-基础语法速通-SAS对照.md)
**下一章 →** [第 04 章 · 控制流、函数、模块与异常](04-控制流函数模块与异常.md)
