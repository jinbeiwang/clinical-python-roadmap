# 第 13 章 · ADaM 衍生与 TFL 报表生成

> **本章目标**：从 ADaM 数据生成**真正能交付的 TFL**（Tables, Figures, Listings）。
> 这是临床程序员的核心交付物，也是 Python 相对 SAS 最需要"补课"的部分——
> **因为 SAS 有 `PROC REPORT` / `PROC TABULATE` / ODS，而 pandas 没有。**

---

## 13.1 现实评估：Python 做 TFL 的优劣势

先说实话，避免期望错位。

| 环节 | SAS 优势 | Python 优势 |
|---|---|---|
| 数据整理（SDTM→ADaM）| 成熟，但样板代码多 | ✅ 表达力强，向量化快 |
| 统计建模 | ✅ 验证充分，监管熟悉度高 | 部分方法需自行确认算法 |
| **统计量计算** | `PROC MEANS`/`FREQ` | ✅ 灵活、可组合 |
| **表格排版** | ✅ **`PROC REPORT`/`TABULATE` 强大** | ❌ 需自己实现 |
| 图形 | `SGPLOT` 系列 | ✅ matplotlib/plotly 更灵活美观 |
| 输出格式（RTF/PDF） | ✅ ODS 直接出 | 需第三方（`great_tables`、`python-docx`、LaTeX、HTML） |
| 自动化/批量 | 宏能用但难维护 | ✅ 明显更好 |

**结论**：**Python 做"计算"很强，做"排版"要自己搭**。
本节的重点就是教你把排版那部分搭起来。

> 💡 PharmaSUG 2026 的 ET-223 论文（Python 做临床统计编程）
> 演示的正是完整链路：读 ADaM → 核心运算 → 格式化 TFL 输出。
> 见 [`resources/02-PharmaSUG论文资料.md`](../resources/02-PharmaSUG论文资料.md)

---

## 13.2 复刻 SAS 的舍入规则（最容易出错的地方）

**这是 Python 与 SAS 结果不一致的头号原因。**

| | SAS `ROUND(x, 0.1)` | Python `round(x, 1)` |
|---|---|---|
| 语义 | 第 2 参数是**舍入单位** | 第 2 参数是**小数位数** |
| 算法 | **四舍五入**（远离零） | **银行家舍入**（四舍六入五成双）|
| `ROUND(2.5, 1)` | `3` | `round(2.5)` → `2` |
| `ROUND(1.5, 1)` | `2` | `round(1.5)` → `2` |
| `ROUND(2.675, 0.01)` | `2.68` | `round(2.675, 2)` → `2.67`（浮点误差）|

```python
import decimal
from decimal import Decimal, ROUND_HALF_UP

def sas_round(x, unit=1):
    """复刻 SAS 的 ROUND(x, unit)：四舍五入（远离零）。

    sas_round(2.5, 1)    -> 3.0
    sas_round(2.675, 0.01) -> 2.68
    sas_round(1234, 10)  -> 1230
    """
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return x
    unit = Decimal(str(unit))
    d = Decimal(str(x)) / unit
    # SAS 用"远离零"的舍入
    rounded = d.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return float(rounded * unit)


def sas_round_series(s: pd.Series, decimals: int = 2) -> pd.Series:
    """整列按 SAS 规则舍入到 N 位小数。向量化实现（比 apply 快）。"""
    factor = 10 ** decimals
    return (np.sign(s) * np.floor(np.abs(s) * factor + 0.5) / factor)


# 验证
import numpy as np, pandas as pd
print(sas_round(2.5, 1))          # 3.0      （Python 内置 round(2.5) 给 2）
print(sas_round(2.675, 0.01))     # 2.68
print(sas_round(1234, 10))        # 1230.0
print(sas_round(-2.5, 1))         # -3.0     （SAS 也是 -3）

s = pd.Series([2.5, 1.5, 0.5, -2.5, 2.675])
print(sas_round_series(s, 0).tolist())   # [3.0, 2.0, 1.0, -3.0, 3.0]
```

> 🔥 **重要提醒**：
> 1. **浮点数的表示误差无法完全消除**。`2.675` 在二进制里其实是
>    `2.67499999999...`，所以"精确舍入"依赖数据的实际存储形式。
>    **绝对严谨的做法**是把数值转成 `Decimal(str(x))`（如上面的 `sas_round`）。
> 2. **监管环境下的黄金法则**：**舍入规则必须在 SAP 中明确定义，
>    并在代码里集中实现（一个函数），绝不能散落在各处用不同的方法。**
> 3. 统计量（均值/标准差）**先算完再舍入**，不要先对原始数据舍入。

---

## 13.3 构造 "n (%)" —— CSR 表格的基本单元

临床表格里 80% 的单元格是 `n (%)` 形式。做一个通用函数：

```python
"""clinic/report.py —— TFL 报表构造工具"""

import numpy as np
import pandas as pd


def pct_format(n: int, denom: int, decimals: int = 1,
               zero_as: str = "0", min_n: int = 0) -> str:
    """生成 "n (x.x%)" 格式的单元格。

    Parameters
    ----------
    n : 分子（受试者数）
    denom : 分母（该治疗组的安全性人群人数）
    decimals : 百分比小数位
    zero_as : 分子为 0 时显示什么（CSR 惯例通常显示 "0"）
    min_n : 只显示 ">0" 的单元格（用于"按频率降序只显示前 N 个"场景）
    """
    if denom == 0 or pd.isna(denom):
        return "-"
    if n == 0:
        return zero_as
    p = n / denom * 100
    # 用 SAS 规则舍入
    factor = 10 ** decimals
    p = np.sign(p) * np.floor(np.abs(p) * factor + 0.5) / factor
    return f"{int(n)} ({p:.{decimals}f}%)"


def summarize_continuous(s: pd.Series, decimals: int = 1) -> dict:
    """连续变量的标准描述统计（复刻 PROC MEANS 的常用输出）。

    返回 n / mean / sd / median / min / max，均按 SAS 规则舍入。
    """
    s = s.dropna()
    n = len(s)
    if n == 0:
        return {k: None for k in ("n", "mean", "sd", "median", "min", "max")}
    r = lambda v: float(np.sign(v) * np.floor(abs(v) * 10**decimals + 0.5) / 10**decimals)
    return {
        "n": n,
        "mean": r(s.mean()),
        "sd": r(s.std(ddof=1)) if n > 1 else None,   # ★ ddof=1 = SAS 的 STD
        "median": r(s.median()),
        "min": r(s.min()),
        "max": r(s.max()),
    }
```

> ⚠️ **`ddof=1` 必须记住**：
> pandas 的 `.std()` **默认 `ddof=1`**（样本标准差），与 SAS 的 `STD` 一致。
> 而 **numpy 的 `np.std()` 默认 `ddof=0`**（总体标准差），与 SAS 不一致！
> 混用 pandas 和 numpy 的 std 会得到不同的结果——**这是 QC 常抓到的差异**。

```python
# 演示这个坑
import numpy as np, pandas as pd
s = pd.Series([63, 64, 71, 74, 55])
s.std()          # 7.60    ← 与 SAS 的 STD 一致 ✅
np.std(s)        # 6.80    ← 与 SAS 不一致 ❌
np.std(s, ddof=1)  # 7.60  ← 必须显式指定
```

---

## 13.4 完整案例：Table 1 — 人口学与基线特征表

这是每个 CSR 的第一张表。我们用 ADSL 完整生成它。

```python
"""case02_人口学表.py —— 生成 CSR 标准的人口学与基线特征表（Table 1）"""
from pathlib import Path
import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent.parent
SAMPLES = BASE / "data" / "samples"

# ★ 治疗组顺序必须显式声明（绝不能依赖字母序！）
TRT_LEVELS = ["Placebo", "Xanomeline Low Dose", "Xanomeline High Dose"]

adsl = pd.read_csv(SAMPLES / "adsl.csv")
adsl["TRT01P"] = pd.Categorical(adsl["TRT01P"], categories=TRT_LEVELS, ordered=True)

# 分析人群：安全性人群
adsl = adsl.query("SAFFL == 'Y'").copy()

# ---- 分母：各治疗组人数 ----
denom = adsl.groupby("TRT01P", observed=False)["USUBJID"].nunique()
# Placebo 86, Xanomeline Low Dose 84, Xanomeline High Dose 84  (+ 合计 254)


def cont_row(label, var, df=adsl, decimals=1):
    """连续变量行：Mean (SD) / Median / Min-Max"""
    out = {"变量": label, "类别": ""}
    for trt in TRT_LEVELS:
        s = df.loc[df["TRT01P"] == trt, var].dropna()
        if len(s) == 0:
            out[trt] = "-"
            continue
        mean = s.mean(); sd = s.std(ddof=1); med = s.median()
        r = lambda v, d=decimals: f"{v:.{d}f}"
        out[trt] = f"{r(mean)} ({r(sd)})"
    out["合计"] = _cont_total(df[var].dropna(), decimals)
    return out


def _cont_total(s, decimals=1):
    if len(s) == 0:
        return "-"
    return f"{s.mean():.{decimals}f} ({s.std(ddof=1):.{decimals}f})"


def cat_row(label, var, level, df=adsl, decimals=1, indent="  "):
    """分类变量行：n (%)"""
    out = {"变量": label, "类别": indent + str(level)}
    total_n = int((df[var] == level).sum())
    for trt in TRT_LEVELS:
        sub = df[df["TRT01P"] == trt]
        n = int((sub[var] == level).sum())
        out[trt] = _fmt_pct(n, int(denom[trt]), decimals)
    out["合计"] = _fmt_pct(total_n, int(denom.sum()), decimals)
    return out


def _fmt_pct(n, d, decimals):
    if d == 0:
        return "-"
    if n == 0:
        return "0"
    return f"{n} ({n / d * 100:.{decimals}f}%)"


def n_row(label, var, df=adsl):
    """计数行：n"""
    out = {"变量": label, "类别": ""}
    for trt in TRT_LEVELS:
        out[trt] = str(int(df.loc[df["TRT01P"] == trt, var].nunique()))
    out["合计"] = str(int(df[var].nunique()))
    return out


# ---- 组装表格 ----
rows = []
rows.append({"变量": "受试者数", "类别": "", **{t: str(int(denom[t])) for t in TRT_LEVELS},
             "合计": str(int(denom.sum()))})
rows.append(cont_row("年龄 (岁)", "AGE"))
rows.append({"变量": "", "类别": "", **{t: "" for t in TRT_LEVELS}, "合计": ""})

rows.append({"变量": "年龄分组, n (%)", "类别": "", **{t: "" for t in TRT_LEVELS}, "合计": ""})
for lvl in ["<65", "65-80", ">80"]:
    rows.append(cat_row("", "AGEGR1", lvl))

rows.append({"变量": "", "类别": "", **{t: "" for t in TRT_LEVELS}, "合计": ""})
rows.append({"变量": "性别, n (%)", "类别": "", **{t: "" for t in TRT_LEVELS}, "合计": ""})
for lvl, lab in [("F", "女"), ("M", "男")]:
    rows.append(cat_row("", "SEX", lvl))

rows.append({"变量": "", "类别": "", **{t: "" for t in TRT_LEVELS}, "合计": ""})
rows.append({"变量": "种族, n (%)", "类别": "", **{t: "" for t in TRT_LEVELS}, "合计": ""})
for lvl in adsl["RACE"].value_counts().index:
    rows.append(cat_row("", "RACE", lvl))

table = pd.DataFrame(rows)

# ---- 输出 ----
pd.set_option("display.width", 220)
pd.set_option("display.max_colwidth", 45)
print("表 1. 人口学与基线特征（安全性人群）")
print(table.to_string(index=False))

table.to_csv(BASE / "outputs" / "table01_demographics.csv", index=False, encoding="utf-8-sig")
```

**预期输出**（基于 CDISC 试点数据的真实结果）：

```
变量              类别            Placebo   Xanomeline Low Dose  Xanomeline High Dose   合计
受试者数                          86        84                    84                     254
年龄 (岁)                          75.1 (7.6)  75.5 (8.3)           74.6 (8.8)             75.1 (8.2)

年龄分组, n (%)
                 <65              13 (15.1%)  9 (10.7%)             11 (13.1%)             33 (13.0%)
                 65-80            47 (54.7%)  48 (57.1%)            49 (58.3%)             144 (56.7%)
                 >80              26 (30.2%)  27 (32.1%)            24 (28.6%)             77 (30.3%)

性别, n (%)
                 女               47 (54.7%)  50 (59.5%)            46 (54.8%)             143 (56.3%)
                 男               39 (45.3%)  34 (40.5%)            38 (45.2%)             111 (43.7%)
```

> 🧠 **这张表体现了 5 个临床编程原则**：
> 1. **分母是各组的分析人群人数**，不是事件数，也不是总人数。
> 2. **治疗组顺序固定**（`pd.Categorical`），与 SAP 一致。
> 3. **连续变量用 `Mean (SD)` 格式**，注意 `ddof=1`。
> 4. **分类变量的分母互不相同**（这里各组 86/84/84），百分比必须用**组内分母**。
> 5. **空类别要么显示 `0`，要么显示空**——这由 SAP 规定，且**整个项目必须一致**。

---

## 13.5 完整案例：AE 按 SOC/PT 汇总表

这是安全性分析的核心表。**核心难点：分子是"受试者数"而不是"事件数"。**

```python
"""case03_不良事件汇总表.py —— TEAE 按 SOC / PT 的受试者数与百分比"""

adae = pd.read_csv(SAMPLES / "adae.csv")
adsl = pd.read_csv(SAMPLES / "adsl.csv")

TRT_LEVELS = ["Placebo", "Xanomeline Low Dose", "Xanomeline High Dose"]

# ---- Step 1: 确定分母（安全性人群）----
saf = adsl.query("SAFFL == 'Y'")[["USUBJID", "TRT01P"]].copy()
saf["TRT01P"] = pd.Categorical(saf["TRT01P"], categories=TRT_LEVELS, ordered=True)
denom = saf.groupby("TRT01P", observed=False)["USUBJID"].nunique()

# ---- Step 2: 限定分析事件（TEAE）----
# TRTEMFL='Y' 表示"治疗中出现的不良事件"
teae = adae.query("TRTEMFL == 'Y'").copy()
# 只保留安全性人群中的记录
teae = teae.merge(saf[["USUBJID", "TRT01P"]], on="USUBJID", how="inner")
# 有没有治疗组信息？ADAE 自带 TRTA，这里用 ADSL 的 TRT01P 更严谨
print(f"TEAE 记录数: {len(teae)}，涉及受试者: {teae['USUBJID'].nunique()}")

# ---- Step 3: ★核心★ 受试者层级去重 ----
# 同一受试者同一 SOC 发生多次 → 只计 1 人
subj_soc = teae.drop_duplicates(["USUBJID", "TRT01P", "AEBODSYS"])
subj_pt = teae.drop_duplicates(["USUBJID", "TRT01P", "AEBODSYS", "AEDECOD"])

n_any = teae.drop_duplicates(["USUBJID", "TRT01P"]).groupby(
    "TRT01P", observed=False)["USUBJID"].nunique()      # 有任一 TEAE 的人数


def count_table(df_uniq, keys):
    """按 keys 分组统计各治疗组的受试者数，返回长表。"""
    return (df_uniq.groupby(keys + ["TRT01P"], observed=False)["USUBJID"]
                   .nunique().rename("N").reset_index())


def pivot_counts(df_uniq, keys):
    c = count_table(df_uniq, keys)
    w = c.pivot_table(index=keys, columns="TRT01P", values="N",
                      aggfunc="sum", fill_value=0, observed=False)
    # ★ 确保所有治疗组列都存在（即使某组为 0）
    for t in TRT_LEVELS:
        if t not in w.columns:
            w[t] = 0
    return w[TRT_LEVELS].reset_index()


def fmt_cells(w):
    """把计数转成 n (%) 字符串。"""
    out = pd.DataFrame(w[["AEBODSYS", "AEDECOD"]])
    for t in TRT_LEVELS:
        out[t] = [
            "0" if n == 0 else f"{n} ({n / denom[t] * 100:.1f}%)"
            for n in w[t]
        ]
    return out


# ---- Step 4: SOC 层级汇总 ----
soc_w = pivot_counts(subj_soc, ["AEBODSYS"])
soc_pct = fmt_cells(soc_w)
# 按总计数降序（CSR 惯例：按发生频率降序）
soc_pct["_tot"] = soc_w[TRT_LEVELS].sum(axis=1)
soc_pct = soc_pct.sort_values("_tot", ascending=False).drop(columns="_tot")

# ---- Step 5: PT 层级汇总（按 SOC 分组）----
pt_w = pivot_counts(subj_pt, ["AEBODSYS", "AEDECOD"])
pt_pct = pd.DataFrame(pt_w[["AEBODSYS", "AEDECOD"]])
for t in TRT_LEVELS:
    pt_pct[t] = ["0" if n == 0 else f"{n} ({n / denom[t] * 100:.1f}%)" for n in pt_w[t]]

# ---- Step 6: 拼装成 CSR 形态 ----
print("表 2. 治疗中出现的不良事件（按 MedDRA SOC / PT）")
print(f"{'':52s}" + "".join(f"{t[:16]:>20s}" for t in TRT_LEVELS))
print("-" * 120)

# 总行
print(f"{'有任一 TEAE 的受试者':52s}" +
      "".join(f"{int(n_any[t])} ({n_any[t]/denom[t]*100:.1f}%)".rjust(20) for t in TRT_LEVELS))
print()

for _, soc in soc_pct.iterrows():
    name = soc["AEBODSYS"]
    cells = "".join(str(soc[t]).rjust(20) for t in TRT_LEVELS)
    print(f"{name[:50]:52s}{cells}")
    pts = pt_pct[pt_pct["AEBODSYS"] == name]
    for _, pt in pts.iterrows():
        cells = "".join(str(pt[t]).rjust(20) for t in TRT_LEVELS)
        print(f"{'  ' + str(pt['AEDECOD'])[:48]:52s}{cells}")
```

**预期输出的开头**：

```
有任一 TEAE 的受试者                              64 (74.4%)   71 (84.5%)   70 (83.3%)

GENERAL DISORDERS AND ADMINISTRATION S...          24 (27.9%)   37 (44.0%)   38 (45.2%)
  FALL                                             5 (5.8%)     10 (11.9%)   12 (14.3%)
  ...
SKIN AND SUBCUTANEOUS TISSUE DISORDERS             22 (25.6%)   36 (42.9%)   35 (41.7%)
  ...
```

> 🔥 **这段代码里最值得记住的是 Step 3**：
> `drop_duplicates` 在**受试者层级**做去重。
> 这是 AE 表与"事件数表"的根本区别。
> **如果忘了这一步，所有计数都会偏大**——而且是悄无声息地偏大，
> 只有把结果和 SAS 的老程序对照才会发现。

---

## 13.6 表格排版：三种现实可用的方案

pandas 算完数据后，怎么输出一张"能看"的表？

### 方案 A：CSV / Excel（最常见，够用）

```python
def export_table(df, path, sheet="Table", col_widths=None):
    """导出到 Excel，做基本的排版美化。"""
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name=sheet, index=False)
        ws = writer.sheets[sheet]

        # 表头加粗 + 灰底 + 自动换行
        for c in range(1, len(df.columns) + 1):
            cell = ws.cell(row=1, column=c)
            cell.font = Font(bold=True)
            cell.fill = PatternFill("solid", fgColor="D9D9D9")
            cell.alignment = Alignment(horizontal="center", vertical="center",
                                      wrap_text=True)
        # 冻结表头 + 列宽
        ws.freeze_panes = "A2"
        for i, col in enumerate(df.columns, start=1):
            width = col_widths.get(col, 18) if col_widths else max(
                12, min(45, int(df[col].astype(str).str.len().max() or 12) + 2))
            ws.column_dimensions[get_column_letter(i)].width = width
```

### 方案 B：HTML（推荐，团队评审用）

```python
def to_html_table(df, path, title="", footnote=""):
    """输出带样式的 HTML 表格，可直接贴进报告或邮件。"""
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{title}</title>
<style>
  body {{ font-family: "Segoe UI", "Microsoft YaHei", sans-serif; margin: 24px; }}
  table {{ border-collapse: collapse; font-size: 13px; }}
  th, td {{ border: 1px solid #bbb; padding: 4px 10px; }}
  th {{ background: #f0f2f5; text-align: center; }}
  td.num {{ text-align: right; }}
  caption {{ text-align: left; font-weight: bold; margin-bottom: 8px; }}
  tfoot td {{ font-size: 12px; color: #666; border: none; padding-top: 10px; }}
</style></head><body>
<table>
<caption>{title}</caption>
{df.to_html(index=False, escape=False, border=0)}
<tfoot><tr><td colspan="{len(df.columns)}">{footnote}</td></tr></tfoot>
</table></body></html>"""
    Path(path).write_text(html, encoding="utf-8")
```

### 方案 C：`great_tables`（专业报表排版，最接近 `PROC REPORT`）

```python
# pip install great-tables
from great_tables import GT

gt_tbl = (GT(df)
          .tab_header(title="表 1. 人口学与基线特征",
                      subtitle="安全性人群")
          .cols_align(align="center", columns=[c for c in df.columns if c not in ("变量", "类别")])
          .tab_source_note("数据来源：CDISC 试点项目 CDISCPILOT01（公开数据）")
          .tab_options(table_font_size="12px"))
gt_tbl.save("outputs/table01.html")
```

> 💡 **`great_tables` 是目前 Python 生态里最接近 `PROC REPORT` 的库**
> （由 Posit 开发，理念来自 R 的 `gt` 包）。它支持分组表头、
> 单元格条件着色、脚注、分页——**做交付级报表值得投入学习**。

---

## 13.7 Listings 与 Figures

### Listing（病例列表）

```python
def make_listing(adae, out_csv, vars_=None, sort_by=("USUBJID", "ASTDT")):
    """生成受试者级的不良事件列表（对应 CSR 的 Listing）。"""
    vars_ = vars_ or ["USUBJID", "TRTA", "AEDECOD", "AESEV", "AESER",
                      "ASTDT", "AENDT", "AEREL", "AEOUT"]
    lis = adae[vars_].sort_values(list(sort_by))
    # 日期格式化（对应 SAS 的 yymmdd10.）
    for c in [c for c in vars_ if c.endswith("DT")]:
        lis[c] = pd.to_datetime(lis[c], errors="coerce").dt.strftime("%Y-%m-%d")
    lis.to_csv(out_csv, index=False, encoding="utf-8-sig")
    return lis
```

### Figure（图形）—— 见第 14 章

---

## 13.8 完整交付链路：把 TFL 串起来

```python
"""一个典型的 TFL 生产脚本骨架（对应公司里的 'TLF 驱动程序'）"""

def main():
    out_dir = BASE / "outputs"
    out_dir.mkdir(exist_ok=True)

    # 1) 载入
    adsl = pd.read_csv(SAMPLES / "adsl.csv")
    adae = pd.read_csv(SAMPLES / "adae.csv")

    # 2) 数据准备（人群、治疗组顺序、派生变量）
    adsl, ae_saf = prepare(adsl, adae)

    # 3) 生产表
    t1 = make_table01(adsl)                  # 人口学
    t2 = make_table02(ae_saf, adsl)          # TEAE SOC/PT
    l1 = make_listing(adae)                  # AE 列表

    # 4) 输出
    t1.to_csv(out_dir / "t01_demographics.csv", index=False, encoding="utf-8-sig")
    t2.to_csv(out_dir / "t02_teae.csv", index=False, encoding="utf-8-sig")
    l1.to_csv(out_dir / "l01_ae_listing.csv", index=False, encoding="utf-8-sig")

    # 5) 日志（对应 SAS 日志，必须留痕）
    logging.info("TFL 生产完成，共 3 个输出")


if __name__ == "__main__":
    main()
```

> 🧠 **和 SAS 世界对照**：这个 `main()` 相当于你的
> "驱动 SAS 程序 + `%include` 一堆宏"的流程。
> 差别在于：Python 版本里**每一步的输出都可以是 DataFrame**，
> 可以随时 `print` 出来核对，不需要写中间数据集再打开看。

---

## 13.9 动手练习

1. **舍入验证**：用 `sas_round()` 与 Python 内置 `round()` 对
   100 个随机数（含 .5 结尾）做对比，统计不一致的比例。

2. **`ddof` 陷阱**：对 ADSL 的 AGE 分别用 `s.std()` 和 `np.std()` 计算，
   与 `PROC MEANS` 的结果对照（提示：置信区间用的是 `ddof=1`）。

3. **Table 1 扩展**：在 13.4 节的表中加入 `BMIBL`（基线 BMI）
   和 `RACE` 的完整分布。

4. **AE 表核对**：运行 13.5 节的代码，验证"有任一 TEAE 的受试者"
   合计是否为 225（提示：254 人中应有 225 人有 AE；
   注意 TRTEMFL='N' 的 65 条记录要被排除）。

5. **排版输出**：把 Table 1 分别用 CSV、HTML、`great_tables` 三种方式输出，
   比较效果。

6. **（进阶）** 把 13.5 节的 AE 表改成**事件数版本**
   （不按受试者去重），对比两张表的差异，并说明 SAP 里应该用哪一种。

---

**上一章 ←** [第 12 章 · SDTM 数据处理实战](12-SDTM数据处理实战.md)
**下一章 →** [第 14 章 · 统计分析与可视化](14-统计分析与可视化.md)
