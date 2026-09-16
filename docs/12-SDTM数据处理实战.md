# 第 12 章 · SDTM 数据处理实战

> **本章目标**：用 Python 完成 SDTM 数据的日常处理——
> 体检、一致性检查、SUPP 域处理、从 SDTM 派生 ADaM 的输入。
> 对应案例：[`cases/case01_数据体检.py`](../cases/case01_数据体检.py)、
> [`cases/case05_ADSL衍生.py`](../cases/case05_ADSL衍生.py)

---

## 12.1 分析前的第一步：SDTM 数据体检

拿到一个 SDTM 包，第一件事永远是体检。**记住这个检查清单**：

| 检查项 | 目的 | SAS 对照 |
|---|---|---|
| 受试者唯一性与一致性 | 各域 USUBJID 是否都在 DM 里 | `PROC SQL` 的 except |
| 主键唯一性 | `USUBJID` + `--SEQ` 是否唯一 | `PROC SORT nodupkey` |
| 日期逻辑 | 结束日期 ≥ 开始日期 | DATA 步 `if` |
| 日期范围 | 不在随机化之前 / 不在数据截止之后 | |
| 缺失模式 | 哪些变量全缺失、哪些必需变量有缺失 | `PROC MEANS nmiss` |
| 变量长度 | 是否有超出 define.xml 声明长度的值 | `PROC CONTENTS` |
| 值域合法性 | SEX 只能是 M/F/U；AESEV 只能是 MILD/MODERATE/SEVERE | `PROC FREQ` |

### 完整实现

```python
"""case01_数据体检.py —— 对一个 SDTM 包做全面体检"""
from pathlib import Path
import pandas as pd
import numpy as np

BASE = Path(__file__).resolve().parent.parent
SAMPLES = BASE / "data" / "samples"

def load(name):
    return pd.DataFrame(pd.read_csv(SAMPLES / f"{name}.csv", dtype=str))

def section(title):
    print("\n" + "=" * 74)
    print(title)
    print("=" * 74)

# ---------- 1. 载入 ----------
domains = {d: load(d) for d in ["dm", "ae", "ex", "ds"]}
dm = domains["dm"]

section("1. 各域规模")
for name, df in domains.items():
    print(f"  {name.upper():6s} {len(df):>7,} 行 × {df.shape[1]:>2} 列")

# ---------- 2. 受试者一致性 ----------
section("2. 受试者一致性（以 DM 为基准）")
dm_subj = set(dm["USUBJID"])
print(f"  DM 中的受试者数: {len(dm_subj)}")
for name, df in domains.items():
    if name == "dm":
        continue
    subj = set(df["USUBJID"])
    orphan = subj - dm_subj
    missing = dm_subj - subj
    status = "OK" if not orphan else f"发现 {len(orphan)} 个孤儿受试者"
    print(f"  {name.upper():6s} 涉及 {len(subj):>4} 人  "
          f"| 不在 DM 中: {len(orphan):>3}  | 无记录的人: {len(missing):>4}  -> {status}")

# ---------- 3. 主键唯一性 ----------
section("3. 主键唯一性检查")
KEY_MAP = {"dm": ["USUBJID"], "ae": ["USUBJID", "AESEQ"],
           "ex": ["USUBJID", "EXSEQ"], "ds": ["USUBJID", "DSSEQ"]}
for name, keys in KEY_MAP.items():
    df = domains[name]
    dup = int(df.duplicated(subset=keys).sum())
    print(f"  {name.upper():6s} 主键 {str(keys):28s} 重复 {dup} 条  "
          f"{'OK' if dup == 0 else '<<< 需检查'}")

# ---------- 4. 日期逻辑 ----------
section("4. 日期逻辑检查（结束日期 ≥ 开始日期）")
DATE_CHECKS = [
    ("ex", "EXSTDTC", "EXENDTC", "给药"),
    ("ae", "AESTDTC", "AEENDTC", "不良事件"),
]
for name, start, end, label in DATE_CHECKS:
    df = domains[name].copy()
    s = pd.to_datetime(df[start], errors="coerce")
    e = pd.to_datetime(df[end], errors="coerce")
    bad = df[(s.notna()) & (e.notna()) & (e < s)]
    print(f"  {label:8s} {start}/{end}: 异常 {len(bad):>3} 条  "
          f"{'OK' if len(bad) == 0 else '<<< 需检查'}")
    if len(bad):
        print(bad[["USUBJID", start, end]].head(5).to_string(index=False))

# ---------- 5. 值域合法性 ----------
section("5. 受控术语（值域）检查")
CONTROLLED = {
    "dm": {"SEX": ["M", "F", "U", "UNDIFFERENTIATED"],
           "RACE": ["WHITE", "BLACK OR AFRICAN AMERICAN",
                    "AMERICAN INDIAN OR ALASKA NATIVE", "ASIAN",
                    "NATIVE HAWAIIAN OR OTHER PACIFIC ISLANDER", "OTHER", "MULTIPLE"],
           "DTHFL": ["Y", "N"]},
    "ae": {"AESEV": ["MILD", "MODERATE", "SEVERE"],
           "AESER": ["Y", "N"]},
}
for name, checks in CONTROLLED.items():
    df = domains[name]
    for var, allowed in checks.items():
        if var not in df.columns:
            continue
        vals = set(df[var].dropna().unique())
        illegal = vals - set(allowed)
        print(f"  {name.upper():6s}.{var:8s} 取值 {len(vals):>2} 种  "
              f"{'OK' if not illegal else f'非法值: {sorted(illegal)}'}")

# ---------- 6. 缺失模式 ----------
section("6. 关键变量缺失情况")
KEY_VARS = {"dm": ["USUBJID", "AGE", "SEX", "RACE", "RFSTDTC", "ARM"],
            "ae": ["USUBJID", "AESEQ", "AEDECOD", "AEBODSYS", "AESTDTC", "AESEV"]}
for name, vars_ in KEY_VARS.items():
    df = domains[name]
    print(f"  --- {name.upper()} ---")
    for v in vars_:
        if v in df.columns:
            n = int(df[v].isna().sum())
            pct = n / len(df) * 100
            flag = "  <<<" if pct > 5 else ""
            print(f"      {v:12s} 缺失 {n:>5} ({pct:5.1f}%){flag}")
```

**运行这段代码你会看到**（CDISC 试点数据的真实结果）：

```
1. 各域规模
  DM        306 行 × 25 列
  AE      1,191 行 × 35 列
  EX        591 行 × 17 列
  DS        596 行 × 13 列

2. 受试者一致性（以 DM 为基准）
  DM 中的受试者数: 306
  AE     涉及  225 人  | 不在 DM 中:   0  | 无记录的人:  81  -> OK
  EX     涉及  254 人  | 不在 DM 中:   0  | 无记录的人:  52  -> OK
  DS     涉及  306 人  | 不在 DM 中:   0  | 无记录的人:   0  -> OK
```

> 🧠 **从这组数字能读出三件事**：
> 1. **DS 覆盖了全部 306 人**（每个人都要有处置记录，包括筛选失败的）；
> 2. **EX 只有 254 人**（只有随机化并给药的人才有暴露记录）；
> 3. **81 人没有 AE 记录**——这**不代表数据缺失**，而是"未发生不良事件"。
>    做 AE 表时这 81 人必须计入分母（分母是安全性人群），分子记 0。
>    这正是第 10 章讲的"三种空"里**"未发生"**的典型例子。

---

## 12.2 SUPP 域：SDTM 的"扩展字段"机制

SUPP-- 域用来存放标准域里没有的非标准变量。**结构是"竖着的"**：

```
SUPPAE (1,191 行 × 10 列)
USUBJID         RDOMAIN  IDVAR   IDVARVAL  QNAM        QLABEL          QVAL
01-701-1015     AE       AESEQ   1         AESOSPFL    ...             Y
```

**解读**：`USUBJID=01-701-1015` 的 `AESEQ=1` 那条 AE 记录，
有一个非标准变量 `AESOSPFL`，值为 `Y`。

### 把 SUPP 域"转置"回主域（最常见的操作）

```python
def transpose_supp(main: pd.DataFrame, supp: pd.DataFrame,
                   rdomain: str, idvar: str = "USUBJID") -> pd.DataFrame:
    """把 SUPP-- 域转置成主域的附加列。

    对应 SAS 里常见的 transpose + merge 宏（如 %SUPP2MAIN）。
    难点：IDVARVAL 是字符型，需要与主域的数值型 SEQ 对齐。
    """
    s = supp[supp["RDOMAIN"] == rdomain].copy()
    if s.empty:
        return main.copy()

    # ★ 关键点 1：IDVAR 有两种形态
    #   - IDVAR = 'USUBJID' 时，用 USUBJID 做键
    #   - IDVAR = 'AESEQ'   时，用 USUBJID + AESEQ 做键
    # ★ 关键点 2：IDVARVAL 是字符，主域 SEQ 是数值 → 必须统一类型
    if idvar == "USUBJID":
        key = ["USUBJID"]
        s["_SEQ"] = None
    else:
        key = ["USUBJID", idvar]
        s["_SEQ"] = pd.to_numeric(s["IDVARVAL"], errors="coerce")

    # ★ 关键点 3：同一个 ID 可能有多个 QNAM → 先转成宽表
    wide = s.pivot_table(index=["USUBJID", "_SEQ"], columns="QNAM",
                         values="QVAL", aggfunc="first").reset_index()

    # 与主域连接
    main = main.copy()
    main["_SEQ"] = pd.to_numeric(main[idvar], errors="coerce") if idvar != "USUBJID" else None
    out = main.merge(wide, on=["USUBJID", "_SEQ"], how="left")
    return out.drop(columns=["_SEQ"])


# 用法
ae = load("ae")
suppae = load("suppae")
ae_ext = transpose_supp(ae, suppae, rdomain="AE", idvar="AESEQ")
print(f"转置前 {ae.shape[1]} 列 → 转置后 {ae_ext.shape[1]} 列")
```

> ⚠️ **三个必须注意的坑**（上面代码的 ★ 标注处）：
> 1. `IDVAR` 可能是 `USUBJID`（受试者层级）或 `--SEQ`（记录层级），
>    逻辑不同，代码必须分支处理。
> 2. `IDVARVAL` **永远是字符型**，而 `--SEQ` 是数值型。
>    直接 merge 会因为类型不匹配而**静默丢失所有匹配**（结果全是 NaN）。
>    这是 SUPP 处理最常见的 bug。
> 3. 同一记录可能有多个 `QNAM`，必须 `pivot` 成宽表后再 merge，
>    否则会产生笛卡尔积导致行数翻倍。

---

## 12.3 从 SDTM 派生 ADaM：ADSL 的构成

ADSL（Subject-Level Analysis Dataset）是**所有分析的基石**。
它的核心是三类信息：

| 类别 | 变量 | 来源 |
|---|---|---|
| **标识** | `STUDYID`, `USUBJID`, `SUBJID`, `SITEID` | DM |
| **人群标记** | `SAFFL`, `ITTFL`, `EFFFL`, `COMP24FL`, `DTHFL` | 派生（DM + EX + DS）|
| **治疗组** | `TRT01P`, `TRT01PN`, `TRT01A`, `TRT01AN` | DM 的 `ARM` / `ACTARM` |
| **关键日期** | `TRTSDT`, `TRTEDT`, `RFSTDTC`, `RFENDTC` | EX 首次/末次给药 |
| **人口学** | `AGE`, `AGEGR1`, `SEX`, `RACE`, `BMIBL` | DM + VS |
| **基线值** | `MMSETOT`, `HEIGHTBL`, `WEIGHTBL` | 各域基线记录 |

### 派生 SAFFL（安全性人群）

```python
"""case05_ADSL衍生.py（节选）—— 从 SDTM 派生 ADSL 的核心变量"""

SECTION_LABEL = "核心 ADSL 变量派生"

def derive_trtsdt(ex: pd.DataFrame) -> pd.DataFrame:
    """TRTSDT = 首次给药的日期（EXSTDTC 最小值）。

    注意：应排除安慰剂组吗？不。SAFFL/ITTFL 人群都包括安慰剂。
    但要注意 EXDOSE=0 的记录（有些研究会有 0 剂量记录）。
    """
    e = ex.copy()
    # 排除剂量为 0 的记录（若非试验设计所允许）
    e = e[e["EXDOSE"].astype(float) > 0]
    e["EXSTDTC_DT"] = pd.to_datetime(e["EXSTDTC"], errors="coerce")
    first = (e.groupby("USUBJID")["EXSTDTC_DT"].min()
              .rename("TRTSDT_DT").reset_index())
    last = (e.groupby("USUBJID")["EXENDTC"]
             .apply(lambda s: pd.to_datetime(s, errors="coerce").max())
             .rename("TRTEDT_DT").reset_index())
    return first.merge(last, on="USUBJID", how="outer")


def derive_saffl(dm: pd.DataFrame, trt: pd.DataFrame) -> pd.DataFrame:
    """SAFFL = 'Y' 当且仅当受试者接受了至少一次研究药物。

    判定规则（典型）：
      1. 有给药记录（TRTSDT 非缺失）
      2. 且 ARM 不是 'Screen Failure' / 'Not Randomized'
    """
    df = dm[["USUBJID", "ARM", "ARMCD"]].copy()
    df = df.merge(trt[["USUBJID", "TRTSDT_DT"]], on="USUBJID", how="left")

    excluded_arms = {"Screen Failure", "Not Assigned", "Not Randomized"}
    df["SAFFL"] = np.where(
        df["TRTSDT_DT"].notna() & ~df["ARM"].isin(excluded_arms), "Y", "N"
    )
    return df[["USUBJID", "SAFFL"]]


def derive_trt01p(dm: pd.DataFrame) -> pd.DataFrame:
    """TRT01P = 计划治疗（Period 1），来自 DM 的 ARM。

    ★ ★ ★ 临床编程的核心规则 ★ ★ ★
    治疗组的**命名与顺序**必须在 SAP 中明确，并贯穿所有输出。
    绝不能依赖字母序！顺序靠 pd.Categorical 强制指定。
    """
    TRT_LEVELS = ["Placebo", "Xanomeline Low Dose", "Xanomeline High Dose"]
    TRT_CODES = {"Placebo": 0, "Xanomeline Low Dose": 54, "Xanomeline High Dose": 81}

    df = dm[["USUBJID", "ARM"]].copy()
    df["TRT01P"] = df["ARM"]
    df["TRT01PN"] = df["TRT01P"].map(TRT_CODES)
    # 用 Categorical 固定顺序（等价于 SAS 里 CLASS 变量的输出顺序）
    df["TRT01P"] = pd.Categorical(df["TRT01P"], categories=TRT_LEVELS, ordered=True)
    return df
```

### 派生 AGEGR1：边界必须与 SAP 完全一致

```python
def derive_agegr1(adsl: pd.DataFrame) -> pd.DataFrame:
    """AGEGR1: '<65' / '65-80' / '>80'

    ⚠️ 边界规则示例：AGE=65 属于 '65-80'，AGE=80 也属于 '65-80'，AGE=81 才是 '>80'。
       这正是 pd.cut(bins=[0, 65, 80, 200], right=False) 的语义：
       区间为 [0,65) / [65,80) / [80,200) —— 等等，这样 80 会落到 '>80'！
       所以要用 bins=[0, 64.999...] 或者显式判断。
    """
    # ✅ 推荐：用 np.select 显式写清边界，可读性最好，QC 最容易核
    adsl = adsl.copy()
    adsl["AGEGR1"] = np.select(
        [adsl["AGE"] < 65, adsl["AGE"] <= 80],
        ["<65", "65-80"],
        default=">80",
    )
    adsl["AGEGR1N"] = adsl["AGEGR1"].map({"<65": 1, "65-80": 2, ">80": 3})
    return adsl
```

> 🔥 **关于 `pd.cut` 的边界陷阱**（重要）：
> `pd.cut(x, bins=[0, 65, 80, 200], right=False)` 产生的区间是
> `[0,65)`、`[65,80)`、`[80,200)`。
> 所以 **AGE=80 会落入第三个区间（`>80`）**，这与临床惯例（80 属于 `65-80`）不符！
>
> **结论**：临床年龄/体重分组这类"整数值边界"，
> **优先用 `np.select` 或 `pd.cut(bins=[0, 64.5, 80.5, 999])`**，
> 并在代码注释里写清"边界归属"。这类边界错误是**监管检查中最容易被发现的问题之一**。

### 用 ADSL 做"分母"的正确姿势

```python
def with_denominator(df: pd.DataFrame, adsl: pd.DataFrame,
                     trt_var: str = "TRT01P") -> pd.DataFrame:
    """给任何 BDS 数据集带上"该治疗组的安全性人群人数"作为分母。

    这是生成 "n (%)" 报表的标准步骤。
    """
    # 分母只取安全性人群
    denom = (adsl.query("SAFFL == 'Y'")
                 .groupby(trt_var, observed=False)["USUBJID"].nunique()
                 .rename("DENOM").reset_index())
    return df.merge(denom, on=trt_var, how="left")
```

---

## 12.4 SDTM 数据清洗的常见任务

### 任务 1：日期部分值处理（`--DTC` 的 ISO 8601 精度）

```python
def parse_partial_date(series: pd.Series) -> pd.DataFrame:
    """解析 ISO 8601 日期，同时输出"精度"。

    '2014-01-05' -> (2014-01-05, 'DAY')
    '2014-01'    -> (2014-01-01, 'MONTH')   ← 补 01，但精度标记为 MONTH
    '2014'       -> (2014-01-01, 'YEAR')
    """
    s = series.astype("string").str.strip()
    precision = np.select(
        [s.str.len() >= 10, s.str.len() == 7],
        ["DAY", "MONTH"], default="YEAR",
    )
    # 对部分日期补全后再解析
    filled = s.where(s.str.len() >= 10,
                     s.str.pad(10, side="right", fillchar="0")
                      .str.replace(r"^(\d{4})$", r"\1-01-01", regex=True)
                      .str.replace(r"^(\d{4}-\d{2})$", r"\1-01", regex=True))
    return pd.DataFrame({"date": pd.to_datetime(filled, errors="coerce"),
                         "precision": precision})
```

### 任务 2：数值结果与单位转换（`--ORRES` / `--STRESN` / `--STRESU`）

```python
def standardize_result(df: pd.DataFrame, orres="VSORRES", stresc="VSSTRESC",
                       stresn="VSSTRESN", unit="VSSTRESU") -> pd.DataFrame:
    """把原始结果标准化成数值（对应 SDTM 的 --STRESN）。"""
    df = df.copy()
    # 原始结果是字符型，可能含 '>', '<', 'NEG' 等非数值内容
    df[stresn] = pd.to_numeric(
        df[orres].astype(str).str.replace(r"[<>]", "", regex=True).str.strip(),
        errors="coerce",
    )
    # 检查哪些值无法转换（这些需要人工确认，可能是定性结果）
    unconvertible = df.loc[
        df[stresn].isna() & df[orres].notna() & (df[orres].astype(str).str.strip() != ""),
        orres,
    ].unique()
    if len(unconvertible):
        print(f"[注意] {orres} 中有 {len(unconvertible)} 种非数值取值: "
              f"{list(unconvertible)[:10]}")
    return df
```

> 💡 **临床要点**：`--ORRES` 保留原始值（含 `<0.01`、`NEGATIVE` 这类），
> `--STRESN` 是标准化数值。**不要直接覆盖原始值**——
> 监管要求原始值和标准化值都能追溯。

### 任务 3：按访视定位基线记录（`--BLFL`）

```python
# SDTM 用 --BLFL='Y' 标记基线记录
vs = pd.read_csv(SAMPLES / "vs_bp.csv")
baseline = vs[vs["VSBLFL"] == "Y"]

# 如果数据集没有 BLFL，需要按规则派生（常见规则：最后一次给药前的测量）
def derive_baseline_flag(df: pd.DataFrame, date_col: str, ref_date_col: str,
                         by=("USUBJID", "VSTESTCD")) -> pd.DataFrame:
    """基线 = 首次给药日期当天或之前的最后一次测量。"""
    df = df.copy()
    d = pd.to_datetime(df[date_col], errors="coerce")
    ref = pd.to_datetime(df[ref_date_col], errors="coerce")
    pre = df[(d <= ref) | ref.isna()].copy()
    pre["_d"] = d[pre.index]
    # 取日期最大的那条
    idx = pre.sort_values("_d").groupby(list(by))["_d"].idxmax()
    df["_BLFL_NEW"] = "N"
    df.loc[idx.dropna(), "_BLFL_NEW"] = "Y"
    return df.drop(columns=["_d"])
```

---

## 12.5 用 CDISC CORE 做符合性检查（进阶）

CDISC 官方提供了**开源的规则引擎 CORE**（Python 实现，MIT 协议），
可以直接对 SDTM/ADaM 数据集跑官方的符合性规则。

```bash
# 方式 1：下载可执行文件（无需 Python 环境）
# https://github.com/cdisc-org/cdisc-rules-engine/releases
core.exe validate -s sdtmig -v 3-4 -d ./data/raw

# 方式 2：从源码运行
git clone https://github.com/cdisc-org/cdisc-rules-engine
cd cdisc-rules-engine
python -m venv venv && .\venv\Scripts\activate
pip install . --group dev
python core.py update-cache          # 拉取最新规则与受控术语
python core.py validate -s sdtmig -v 3-4 -d ./data/raw
```

> 📌 **注意**：CORE 目前对 SDTMIG 与 FDA Business Rules 的验证
> 建议使用其 Verisian 分支引擎（基于 SQL），
> TIG / USDM / ADaM / SEND 用主仓库引擎（基于 pandas/dask）。
> 详见 <https://cdisc-org.github.io/cdisc-rules-engine>
>
> **这对你的意义**：CORE 本身就是**一个用 pandas 写的临床规则引擎**，
> 它的源码是学习"如何用 Python 实现临床数据校验"的极好范本。

---

## 12.6 动手练习

1. **体检**：运行 `cases/case01_数据体检.py`，把输出与本章 12.1 的示例对照，
   确认数字一致。

2. **SUPP 转置**：用 `transpose_supp()` 把 SUPPAE 合并到 AE，
   报告新增了哪些变量、有多少条记录匹配成功。

3. **孤儿受试者**：检查 CM / MH / VS 三个域里有没有不在 DM 中的 USUBJID。
   （提示：VS 有 29,643 行，注意性能）

4. **日期精度**：写一个函数，统计 `AESTDTC` 中
   DAY / MONTH / YEAR 三种精度的记录数各是多少。

5. **基线派生**：使用 VS 数据，按"每个受试者每个检查项目，
   取 VISITNUM 最小的那次作为基线"派生一个 `_BLFL_NEW`，
   并与数据里原生的 `VSBLFL` 比对一致率。

6. **（进阶）** 尝试安装并运行 CDISC CORE，对 `data/raw/` 做一次 SDTMIG 3.4 验证，
   看看试点数据（2007 年，遵循较早版本的 IG）会报出哪些规则违规。

---

**上一章 ←** [第 11 章 · 读取 XPT 与临床数据结构](11-读取XPT与临床数据结构.md)
**下一章 →** [第 13 章 · ADaM 衍生与 TFL 报表生成](13-ADaM衍生与TFL报表生成.md)
