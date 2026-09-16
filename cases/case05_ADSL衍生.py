#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
案例 05 · 从 SDTM 衍生 ADSL（受试者水平分析数据集）
====================================================

**目标**：把多个 SDTM 域（DM / EX / DS / VS / ADAE）合并成一张
**受试者水平（one record per subject）** 的分析数据集 ADSL ——
这是所有 TFL 报表的分母来源，也是 ADaM 生产的第一道工序。

**覆盖教程章节**：第 09 章（合并重塑）、第 12 章（SDTM）、第 13 章（ADaM）

为什么这是 SAS 程序员转 Python 的"分水岭"
------------------------------------------
SAS 里做 ADSL 是一长串 ``DATA`` 步 + ``MERGE`` + ``RETAIN`` + ``BY``，
Python 里对应的是 ``groupby().agg()`` + ``merge()``。
表面看是语法差异，真正的坑全在**口径**上 —— 本案例会把这些坑一个个挖出来：

1. **治疗结束日怎么定？** EX 里最后一条记录的 ``EXENDTC`` 经常是空的
   （治疗还在进行 / 记录未闭合）。三种合理口径给出 **248 / 254 / 127** 三个不同答案。
2. **TRT01A（实际治疗组）到底按什么定？** 抄 ``DM.ACTARM`` 会错 12 个人 ——
   他们随机化到高剂量，但在滴定期就退出了，从没吃到 81 mg。
3. **完成 8/16/24 周的标记靠什么判？** 按"治疗时长 ≥ 56 天"和按
   "完成第 8 周访视"是完全不同的两件事。
4. **精度丢失的顺序会影响结果。** BMI 用未舍入的体重算得 28.2，
   用舍入到 1 位的体重算得 28.1 —— 参考 ADSL 用的是后者。
5. **DM 里根本没有身高体重。** 它们来自 VS 域。
   ADSL 从来不是"从 DM 一步得来"的，而是多域合并的产物。

最后一步是**双编程验证（Double Programming）**：
把自己派生的 ADSL 与已知的参考 ADSL 逐变量比对，输出匹配率。
这不是"多此一举"，而是 ADaM 生产流程的核心环节 ——
**你的派生结果必须能被第二套独立实现复现。**

运行
----
    python cases/case05_ADSL衍生.py
    python cases/case05_ADSL衍生.py --end-rule ex        # 只看 EX 的直接口径
    python cases/case05_ADSL衍生.py --end-rule ref       # 放弃 EX，直接用参考结束日
    python cases/case05_ADSL衍生.py --trt01a-rule actarm # 实际治疗组改抄 DM.ACTARM
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from clinic import io as cio              # noqa: E402
from clinic import report as rpt          # noqa: E402
from clinic.derive import (               # noqa: E402
    derive_agegr1,
    sas_round_series,
    to_categorical,
)

pd.set_option("display.width", 220)
pd.set_option("display.max_columns", 90)

# 治疗组顺序 & 编码（**按 SAP 指定，绝不能依赖字母序**）
TRT_LEVELS = ["Placebo", "Xanomeline Low Dose", "Xanomeline High Dose"]
TRT_CODE = {"Placebo": 0, "Xanomeline Low Dose": 54, "Xanomeline High Dose": 81}
EXCLUDED_ARMS = {"Screen Failure", "Not Assigned", "Not Randomized"}
RACEN_MAP = {
    "WHITE": 1,
    "BLACK OR AFRICAN AMERICAN": 2,
    "AMERICAN INDIAN OR ALASKA NATIVE": 6,
    "ASIAN": 3,
    "NATIVE HAWAIIAN OR OTHER PACIFIC ISLANDER": 4,
}

# 完成 8/16/24 周治疗 ↔ 最后一次疗效访视号（VISITNUM）
# 对照访视计划：4=WEEK2, 5=WEEK4, 7=WEEK6, 8=WEEK8, 9=WEEK12, 10=WEEK16,
#               11=WEEK20, 12=WEEK24, 13=WEEK26（研究结束随访）
COMP_VISIT = {"COMP8FL": 8, "COMP16FL": 10, "COMP24FL": 12}


def section(t: str) -> None:
    print("\n" + "=" * 88)
    print(f"  {t}")
    print("=" * 88)


# ==========================================================================
# 1. 载入 SDTM 源数据
# ==========================================================================
def load_sources(data_dir: Path) -> dict[str, pd.DataFrame]:
    """载入 ADSL 派生所需的全部源域。

    注意 ``vs_anthrop``（VS 的身高/体重子集）与 ``vs_bp`` 都是**必需**的：
    DM 里没有身高体重，BMI 必须从 VS 派生；访视号也要从 VS 取。
    """
    need = ["dm", "ex", "ds", "adae", "vs_anthrop", "vs_bp"]
    src: dict[str, pd.DataFrame] = {}
    for name in need:
        p = data_dir / f"{name}.csv"
        if not p.exists():
            raise FileNotFoundError(
                f"找不到 {p.name}。请先运行：\n"
                "    python scripts/download_data.py --core\n"
                "    python scripts/make_samples.py"
            )
        src[name] = cio.read_any(p)
    return src


# ==========================================================================
# 2. 暴露（Exposure）：治疗起始日 / 结束日 / 时长
# ==========================================================================
def derive_exposure(ex: pd.DataFrame, end_rule: str,
                    ref_end: pd.Series | None = None) -> pd.DataFrame:
    """从 EX 域派生 TRTSDT / TRTEDT / TRTDUR。

    Parameters
    ----------
    ex : EX 域（一次给药记录一行）
    end_rule : 治疗结束日口径

        =========  =====================================================
        ``'ex'``   ``TRTEDT = max(EXENDTC)`` —— 最"字面"的做法
        ``'imp'``  缺失的 ``EXENDTC`` 用「下一条记录的起始日 − 1 天」补全；
                   **末次记录仍未闭合**（治疗仍在进行）时取参考结束日
                   ``RFENDTC`` —— 这是本项目的实际口径
        ``'ref'``  ``TRTEDT = RFENDTC``（完全放弃 EX 信息）
        =========  =====================================================

    ref_end : ``Series``，index 为 USUBJID，值为参考结束日

    Notes
    -----
    ``TRTDUR`` 的计算是 ``(TRTEDT - TRTSDT) + 1``：**首尾都算**。
    这与 SAS 里 ``TRTDUR = TRTEDT - TRTSDT + 1;`` 完全一致 ——
    少写那个 ``+1`` 是极常见的 diff 来源。
    """
    x = ex.copy()
    x["_STDTC"] = pd.to_datetime(x["EXSTDTC"], errors="coerce")
    x["_ENDTC"] = pd.to_datetime(x["EXENDTC"], errors="coerce")
    x["_SEQ"] = pd.to_numeric(x.get("EXSEQ"), errors="coerce")
    x = x.sort_values(["USUBJID", "_SEQ"], kind="stable")

    if end_rule == "ref":
        g = x.groupby("USUBJID").agg(TRTSDT=("_STDTC", "min"),
                                     n_ex=("_SEQ", "size"))
        g["TRTEDT"] = ref_end.reindex(g.index) if ref_end is not None else pd.NaT
    else:
        if end_rule == "ex":
            x["_END_C"] = x["_ENDTC"]
        else:  # imp
            # 下一条给药记录的起始日 − 1 天，就是本条的结束日
            nxt = x.groupby("USUBJID")["_STDTC"].shift(-1)
            x["_END_C"] = x["_ENDTC"].fillna(nxt - pd.Timedelta(days=1))

        g = x.groupby("USUBJID").agg(
            TRTSDT=("_STDTC", "min"),
            TRTEDT=("_END_C", "max"),
            n_ex=("_SEQ", "size"),
        )
        if end_rule == "imp":
            # ★ 关键一步：末次给药记录**没有结束日**时，"最后一次给药"是
            #   max(_END_C) 算不出来的（它只会给出倒数第二条的结束日）。
            #   这种人意味着"治疗仍在进行"，正确做法是取参考结束日。
            last = x.groupby("USUBJID").tail(1).set_index("USUBJID")
            still_open = last["_ENDTC"].isna()
            g.loc[still_open.reindex(g.index).fillna(False), "TRTEDT"] = \
                ref_end.reindex(g.index)[still_open.reindex(g.index).fillna(False)]

    g["TRTDUR"] = (g["TRTEDT"] - g["TRTSDT"]).dt.days + 1
    return g


def derive_actual_trt(ex: pd.DataFrame, rule: str) -> pd.Series:
    """派生 TRT01A（实际治疗组）。

    ============  ==================================================
    ``'arm'``     直接取随机化臂（DM.ARM）—— 本项目 ADSL 的口径
    ``'actarm'``  取 DM.ACTARM（申办方已按实际给药派生好的值）
    ``'maxdose'`` 按受试者实际接受的**最高剂量**判断
    ============  ==================================================

    ★ 三者会给出**不同答案**，本案例的 12 位受试者就是活教材：
      随机化到 Xanomeline High Dose，但在滴定期（第 1–14 天）就退出了，
      全程只吃到 54 mg。于是：

      - 随机化臂 / 我们的 'arm' 口径 → High Dose（254/254 与参考一致）
      - DM.ACTARM                  → Low Dose（242/254）
      - 最高剂量                   → Low Dose

      **两种口径都说得通**，但必须与 SAP 一致，并写进 ADRG。
      这也是"实际治疗组"这个变量在不同项目里含义不同的原因。
    """
    if rule == "maxdose":
        e = ex.copy()
        e["_D"] = pd.to_numeric(e["EXDOSE"], errors="coerce")
        mx = e.groupby("USUBJID")["_D"].max()
        return mx.map({0.0: "Placebo", 54.0: "Xanomeline Low Dose",
                       81.0: "Xanomeline High Dose"})
    return None                    # 'arm' / 'actarm' 走主流程里的直接映射


# ==========================================================================
# 3. 处置（Disposition）：停药原因 / 死亡
# ==========================================================================
def derive_disposition(ds: pd.DataFrame) -> pd.DataFrame:
    """从 DS 域派生 DCDECOD / DSRAEFL。

    关键点一：DS 里同时有 ``DISPOSITION EVENT``（处置事件）和
    ``OTHER EVENT``（访视记录）。**只取处置事件**才算得对，
    否则会把 "FINAL LAB VISIT" 之类当成停药原因。

    关键点二：``DSRAEFL`` 不是"有没有停药原因"，而是
    **"是否因不良事件而停药"**。本项目口径是 ``DCDECOD == 'ADVERSE EVENT'``
    （92 人）。若按"DCDECOD 非缺失就置 Y"来写会得到 144 人 ——
    这不是"差一点"，而是**口径完全错了**。

    关键点三（pandas 专属陷阱）：``out.loc[mask, col] = value`` 里
    如果 mask 定位到的行**根本不在 out 的索引里**（比如"完成研究"的受试者
    压根没进非完成子集），这条赋值会**静默失效**，不报任何错。
    SAS 里你至少会看到一个 note。所以这里先 ``reindex`` 补齐索引。
    """
    disp = ds[ds["DSCAT"].astype(str).str.strip() == "DISPOSITION EVENT"].copy()
    non_dc = {"COMPLETED", "SCREEN FAILURE"}          # 非"停药事件"
    d = disp[~disp["DSDECOD"].isin(non_dc)].copy()

    out = d.groupby("USUBJID").agg(DCDECOD=("DSDECOD", "first"),
                                  _DCDTC=("DSDTC", "max"))
    # ★ 先补全索引，否则下面的 .loc 赋值会静默丢失
    out = out.reindex(pd.Index(sorted(set(ds["USUBJID"]))).rename("USUBJID"))

    comp = disp.loc[disp["DSDECOD"] == "COMPLETED", "USUBJID"].unique()
    out.loc[out.index.isin(comp), "DCDECOD"] = "COMPLETED"

    out["DSRAEFL"] = pd.NA
    out.loc[out["DCDECOD"] == "ADVERSE EVENT", "DSRAEFL"] = "Y"
    return out


def derive_death(dm: pd.DataFrame, ds: pd.DataFrame) -> pd.DataFrame:
    """DTHFL：DS 里出现 DEATH 处置事件即为 'Y'。

    DM 里也有 ``DTHFL``，但**权威来源是 DS 的处置事件**；
    两者不一致时应以 DS 为准并记录 QC 问题。
    """
    disp = ds[ds["DSCAT"].astype(str).str.strip() == "DISPOSITION EVENT"]
    died = set(disp.loc[disp["DSDECOD"] == "DEATH", "USUBJID"])
    out = pd.DataFrame(index=pd.Index(sorted(set(dm["USUBJID"])), name="USUBJID"))
    out["DTHFL"] = pd.NA
    out.loc[out.index.isin(died), "DTHFL"] = "Y"
    # 与 DM 的声明值交叉核对（QC）
    out["_DM_DTHFL"] = dm.set_index("USUBJID")["DTHFL"].reindex(out.index)
    return out


# ==========================================================================
# 4. 访视派生：VISNUMEN / 完成周标记
# ==========================================================================
def derive_visits(vs_bp: pd.DataFrame, subj: pd.Index) -> pd.DataFrame:
    """从访视数据派生 ``VISNUMEN``（最后一次计划访视的编号）。

    ★ 这里要说清一件很要紧的事：
      **ADSL 里的 VISNUMEN 不是"最后一次访视的编号"那么简单。**
      本项目的定义是"最后一次**疗效评估**访视的编号"，
      它依赖疗效数据集（ADAS-Cog），而那份数据**不在本案例可用的域里**。
      所以这里只能做**最佳努力的近似**：取计划访视（VISITNUM 1–12）的最大值。
      结果 234/254 与参考一致 —— 另外 20 位是提前终止的受试者，
      他们的 ADSL 值比访视数据大 1（终止访视单独计数）。

      ★ 结论：当你需要的数据不在手上时，正确做法是**去要数据**，
        而不是用一个"看起来像"的近似值悄悄顶上。
        本案例会把这个差异**明确报告出来**，而不是掩盖它。
    """
    v = vs_bp.copy()
    v["_V"] = pd.to_numeric(v["VISITNUM"], errors="coerce")
    v = v[v["_V"].between(1, 12)]                     # 只看在治疗期内的计划访视
    mx = v.groupby("USUBJID")["_V"].max()
    return pd.DataFrame({"VISNUMEN": mx.reindex(subj)})


# ==========================================================================
# 5. 主派生流程
# ==========================================================================
def build_adsl(src: dict[str, pd.DataFrame], end_rule: str = "imp",
               trt01a_rule: str = "arm", verbose: bool = True) -> pd.DataFrame:
    dm, ex, ds, adae = src["dm"], src["ex"], src["ds"], src["adae"]

    # ---- 第一步：确定分析人群 -------------------------------------------
    # ADSL 只放"随机化"的受试者（Screen Failure 不进分析人群）
    base = dm[~dm["ARM"].astype(str).str.strip().isin(EXCLUDED_ARMS)].copy()
    base = base.sort_values("USUBJID").reset_index(drop=True)
    if verbose:
        print(f"  DM 共 {len(dm)} 位受试者；排除 Screen Failure 后 "
              f"进入 ADSL 的：{len(base)} 位")
        print("  （对照：参考 ADSL 共 254 位 —— 数字一致说明人群口径对了）")

    adsl = base[["STUDYID", "USUBJID", "SUBJID", "SITEID", "AGE", "AGEU",
                 "SEX", "RACE", "ETHNIC", "COUNTRY",
                 "RFSTDTC", "RFENDTC", "ACTARM", "ARM"]].copy()

    # ---- 第二步：治疗组（计划 vs 实际）---------------------------------
    # ★ 报表里**通常用 TRT01P**（分母是随机化人群，用实际治疗会自相矛盾）。
    adsl["TRT01P"] = adsl["ARM"]
    adsl["TRT01PN"] = adsl["TRT01P"].map(TRT_CODE)

    if trt01a_rule == "arm":
        adsl["TRT01A"] = adsl["ARM"]
    elif trt01a_rule == "actarm":
        adsl["TRT01A"] = adsl["ACTARM"]
    else:                                    # maxdose
        md = derive_actual_trt(ex, "maxdose")
        adsl["TRT01A"] = md.reindex(adsl["USUBJID"].to_numpy()).to_numpy()
    adsl["TRT01AN"] = adsl["TRT01A"].map(TRT_CODE)
    adsl["TRT01P"] = to_categorical(adsl["TRT01P"], TRT_LEVELS)

    # ---- 第三步：暴露 ---------------------------------------------------
    ref_end = pd.to_datetime(base.set_index("USUBJID")["RFENDTC"], errors="coerce")
    expo = derive_exposure(ex, end_rule, ref_end)
    adsl = adsl.merge(expo, left_on="USUBJID", right_index=True, how="left")

    # ---- 第四步：人群标记 ----------------------------------------------
    adsl["SAFFL"] = np.where(adsl["TRTSDT"].notna(), "Y", "N")
    # ITTFL：随机化即入组（本项目里与 SAFFL 完全一致，但要分开写 ——
    # 一旦出现"随机化后未给药"的受试者，两者立刻分叉）
    adsl["ITTFL"] = np.where(
        pd.Categorical(adsl["TRT01P"]).isin(TRT_LEVELS), "Y", "N")
    # EFFFL：需要"基线 + 至少一次基线后疗效评估"，依赖 ADAS-Cog 数据。
    # ★ 不在可用域里 → 显式置为缺失，并在验证环节标注"不可派生"。
    adsl["EFFFL"] = pd.Series(pd.NA, index=adsl.index, dtype="object")

    # ---- 第五步：处置 / 死亡 -------------------------------------------
    disp = derive_disposition(ds)
    adsl = adsl.merge(disp[["DCDECOD", "DSRAEFL"]],
                      left_on="USUBJID", right_index=True, how="left")
    death = derive_death(dm, ds)
    adsl = adsl.merge(death[["DTHFL", "_DM_DTHFL"]],
                      left_on="USUBJID", right_index=True, how="left")

    # ---- 第六步：访视 → VISNUMEN → 完成周标记 ---------------------------
    vis = derive_visits(src["vs_bp"], pd.Index(adsl["USUBJID"].to_numpy()))
    vis.index = adsl.index
    adsl["VISNUMEN"] = vis["VISNUMEN"]
    for flag, vnum in COMP_VISIT.items():
        adsl[flag] = np.where(adsl["VISNUMEN"] >= vnum, "Y", "N")

    # ---- 第七步：基线值（身高 / 体重 → BMI）----------------------------
    # ★ DM 里根本没有身高体重 —— 它们来自 VS 域。
    # ★ 另一个坑：VS.VSSTRESN 是"标准单位下的全精度值"，
    #   体重 120 LB → 54.43 kg；而 ADSL 的 WEIGHTBL 是 ROUND(., 0.1) 的 54.4。
    #   直接拿 VSSTRESN 去比会 250/254 不一致 —— **先舍入再比**。
    anth = src.get("vs_anthrop")
    if anth is not None:
        a = anth.copy()
        a["_N"] = pd.to_numeric(a["VSSTRESN"], errors="coerce")
        a["_CODE"] = a["VSTESTCD"].astype(str).str.strip()
        a["_VISITN"] = pd.to_numeric(a["VISITNUM"], errors="coerce")
        blfl = a["VSBLFL"].astype("string").str.strip()
        a["_BLFL"] = blfl.mask(blfl.isin(["", "nan", "None", "<NA>"]))

        # 身高：本研究只在筛选期测一次 → 取该受试者的唯一（最后一次）记录
        h = (a.loc[a["_CODE"] == "HEIGHT"].sort_values("_VISITN")
               .drop_duplicates("USUBJID", keep="last").set_index("USUBJID")["_N"])
        # 体重：优先取基线访视（VSBLFL='Y'）；没有该标记时退回最早访视
        w_bl = a[(a["_CODE"] == "WEIGHT") & (a["_BLFL"] == "Y")]
        if len(w_bl):
            w = w_bl.drop_duplicates("USUBJID").set_index("USUBJID")["_N"]
        else:
            w = (a.loc[a["_CODE"] == "WEIGHT"].sort_values("_VISITN")
                   .drop_duplicates("USUBJID").set_index("USUBJID")["_N"])

        anc = pd.DataFrame({"_H": h, "_W": w}).reindex(adsl["USUBJID"].to_numpy())
        anc.index = adsl.index
        # 报表里这两列按 1 位小数呈现（与 ADSL 的口径一致）
        adsl["HEIGHTBL"] = sas_round_series(anc["_H"], 1)
        adsl["WEIGHTBL"] = sas_round_series(anc["_W"], 1)
        # ★ BMI 的精度顺序：
        #   本项目参考 ADSL 是「先舍入到 1 位的 W/H，再算 BMI，最后再舍入」，
        #   与"用全精度 W/H 算完再舍入"会差 25 个人。
        #   两种都"没错"，但结果不同 —— 这就是为什么口径要写死在 ADRG 里。
        adsl["BMIBL"] = sas_round_series(
            adsl["WEIGHTBL"] / (adsl["HEIGHTBL"] / 100) ** 2, 1)
    else:
        for c in ("HEIGHTBL", "WEIGHTBL", "BMIBL"):
            adsl[c] = pd.NA

    adsl["BMIBLGR1"] = np.select(
        [adsl["BMIBL"] < 25, adsl["BMIBL"] < 30], ["<25", "25-<30"], default=">=30")
    adsl.loc[adsl["BMIBL"].isna(), "BMIBLGR1"] = pd.NA

    # ---- 第八步：年龄分组（复用 clinic.derive 里显式写死边界的实现）------
    adsl = derive_agegr1(adsl, age_var="AGE", out_var="AGEGR1", num_var="AGEGR1N")
    adsl["AGEGR1"] = to_categorical(adsl["AGEGR1"], ["<65", "65-80", ">80"])
    adsl["RACEN"] = adsl["RACE"].map(RACEN_MAP)

    # ---- 第九步：安全性补充（首次 TEAE = 治疗中出现的不良事件）----------
    if "TRTEMFL" in adae.columns:
        teae = adae[adae["TRTEMFL"].astype(str).str.strip() == "Y"].copy()
        teae["_ASTDT"] = pd.to_datetime(teae["ASTDT"], errors="coerce")
        first = teae.groupby("USUBJID").agg(
            TRTEMFL_first=("_ASTDT", "min"), TRTEMFL_n=("_ASTDT", "size"))
        adsl = adsl.merge(first, left_on="USUBJID", right_index=True, how="left")
        adsl["TRTEMFL"] = np.where(adsl["TRTEMFL_first"].notna(), "Y", "N")

    # ---- 排序列（模拟 SAS 的 KEEP 顺序）--------------------------------
    order = [
        "STUDYID", "USUBJID", "SUBJID", "SITEID", "COUNTRY",
        "AGE", "AGEU", "AGEGR1", "AGEGR1N", "SEX", "RACE", "RACEN", "ETHNIC",
        "ARM", "ACTARM", "TRT01P", "TRT01PN", "TRT01A", "TRT01AN",
        "TRTSDT", "TRTEDT", "TRTDUR",
        "SAFFL", "ITTFL", "EFFFL", "COMP8FL", "COMP16FL", "COMP24FL",
        "DCDECOD", "DSRAEFL", "DTHFL",
        "RFSTDTC", "RFENDTC", "VISNUMEN",
        "WEIGHTBL", "HEIGHTBL", "BMIBL", "BMIBLGR1",
        "TRTEMFL", "TRTEMFL_n", "TRTEMFL_first",
    ]
    return adsl[[c for c in order if c in adsl.columns]].copy()


# ==========================================================================
# 6. 双编程验证：与参考 ADSL 逐变量比对
# ==========================================================================
def _norm(s: pd.Series) -> pd.Series:
    """把值标准化后再比 —— 否则 ``'2013-07-14'`` 与 ``Timestamp`` 会被判为不等。

    ★ 这里埋着一个很典型的坑 ★

    ``clinic.io.clean_missing()`` 会把所有字符列转成 pandas 的
    **StringDtype**，而**不是** ``object``。如果判断 dtype 时只写
    ``s.dtype == "object"``，这些列就会掉进 ``pd.to_numeric`` 分支 ——
    整列变成 NaN，比对报告显示"100% 不一致"，但数据其实完全正确。

    教训：**比对工具本身也需要被验证**。看到"全表 0% 匹配"时，
    第一反应应该是"我的比对代码有问题"，而不是"我的派生逻辑有问题"。
    """
    if pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s):
        return pd.to_numeric(s, errors="coerce")
    if pd.api.types.is_datetime64_any_dtype(s):
        return s.dt.strftime("%Y-%m-%d")
    return s.astype("string").str.strip().replace({"": pd.NA})


# 这些变量**无法**从本案例可用的域派生出来，必须单独标注，
# 否则报告里会显示成"0% 匹配"，看起来像是实现错了。
NON_DERIVABLE = {
    "EFFFL": "需疗效数据（ADAS-Cog）：基线 + 至少一次基线后评估，本案例无此数据",
}

# 口径正确、但**所需源数据不完整**，只能做最佳努力近似。
# ★ 这类差异必须显式报告出来，绝不能悄悄放过。
LIMITED = {
    "VISNUMEN": "需疗效评估访视数据（ADAS-Cog）；本案例用计划访视 VISITNUM 1–12 近似",
}


def validate(derived: pd.DataFrame, reference: pd.DataFrame,
             check_vars: list[str]) -> pd.DataFrame:
    """逐变量比对派生结果与参考实现，返回匹配率表。

    这是**双编程（Double Programming）**的最小可用版本：
    独立实现 → 比对 → 逐变量报告匹配率 → 定位差异根因。

    判定分四档，**不要把所有不一致都当成 bug**：

    ==================  ====================================================
    ``一致``            完全一致
    ``近似``            匹配率 ≥ 95%，口径正确但有边界差异，需记录
    ``近似·数据受限``   口径正确，但所需源数据不在手上，只能逼近
    ``不可派生``        所需源数据完全缺失，**不应给出任何值**
    ``不一致``          必须定位根因并修复
    ==================  ====================================================
    """
    d = derived.set_index("USUBJID")
    r = reference.set_index("USUBJID")
    idx = d.index.union(r.index)
    d, r = d.reindex(idx), r.reindex(idx)

    rows = []
    for var in check_vars:
        if var not in d.columns or var not in r.columns:
            rows.append({"变量": var, "匹配数": 0, "总数": len(idx),
                         "匹配率": np.nan, "判定": "不可比", "说明": "变量缺失"})
            continue
        a, b = _norm(d[var]), _norm(r[var])
        both_na = a.isna() & b.isna()          # 两边同为缺失 → 算一致
        n_eq, n_tot = int(((a == b) | both_na).sum()), len(idx)
        rate = n_eq / n_tot * 100
        if var in NON_DERIVABLE:
            verdict, note = "不可派生", NON_DERIVABLE[var]
        elif var in LIMITED:
            verdict, note = "近似·数据受限", LIMITED[var]
        elif n_eq == n_tot:
            verdict, note = "一致", ""
        elif rate >= 95:
            verdict, note = "近似", f"{n_tot - n_eq} 条不一致（见根因分析）"
        else:
            verdict, note = "不一致", f"{n_tot - n_eq} 条不一致（见根因分析）"
        rows.append({"变量": var, "匹配数": n_eq, "总数": n_tot,
                     "匹配率": rate, "判定": verdict, "说明": note})
    return pd.DataFrame(rows)


def show_mismatch(derived: pd.DataFrame, reference: pd.DataFrame,
                  var: str, limit: int = 6) -> None:
    """把不一致的记录打出来 —— 定位差异根因的唯一办法。"""
    d, r = derived.set_index("USUBJID"), reference.set_index("USUBJID")
    idx = d.index.union(r.index)
    a, b = _norm(d.reindex(idx)[var]), _norm(r.reindex(idx)[var])
    bad = ~((a == b) | (a.isna() & b.isna()))
    sub = pd.DataFrame({"USUBJID": idx[bad],
                        "派生值": a[bad].astype(object).to_numpy(),
                        "参考值": b[bad].astype(object).to_numpy()})
    print(sub.head(limit).to_string(index=False))
    if len(sub) > limit:
        print(f"  … 另有 {len(sub) - limit} 条")


# ==========================================================================
# main
# ==========================================================================
def main() -> None:
    ap = argparse.ArgumentParser(description="从 SDTM 衍生 ADSL 并做双编程验证")
    ap.add_argument("--end-rule", default="imp", choices=["ex", "imp", "ref"],
                    help="治疗结束日口径：ex=直接用 EXENDTC / imp=补全缺失（默认）/ ref=参考结束日")
    ap.add_argument("--trt01a-rule", default="arm", choices=["arm", "actarm", "maxdose"],
                    help="实际治疗组口径：arm=随机化臂（默认）/ actarm=DM.ACTARM / maxdose=最高剂量")
    ap.add_argument("--no-validate", action="store_true", help="跳过双编程验证")
    args = ap.parse_args()

    data_dir = BASE / "data" / "samples"
    out_dir = BASE / "outputs"
    out_dir.mkdir(exist_ok=True)

    print("=" * 88)
    print("  案例 05 · 从 SDTM 衍生 ADSL（受试者水平分析数据集）")
    print("=" * 88)

    section("1. 载入源数据")
    src = load_sources(data_dir)
    for k, v in src.items():
        print(f"  {k.upper():12s} {v.shape[0]:6d} 行 × {v.shape[1]:3d} 列")
    print("\n  ★ 注意 DM 有 306 行，但 ADSL 只应该有 254 行 —— 因为 ADSL 是")
    print("    **分析人群**，Screen Failure 不进分析人群。")
    print("  ★ 还要注意：DM 里**没有**身高体重 —— BMI 得从 VS 域派生。")

    section(f"2. 派生 ADSL（结束日口径={args.end_rule}，实际治疗组口径={args.trt01a_rule}）")
    adsl = build_adsl(src, args.end_rule, args.trt01a_rule)
    print(f"\n  派生完成：{adsl.shape[0]} 行 × {adsl.shape[1]} 列")
    print("\n  治疗组分布（TRT01P）：")
    for k, v in adsl["TRT01P"].value_counts().reindex(TRT_LEVELS).items():
        print(f"    {k:24s} {v:4d}")
    dur = pd.to_numeric(adsl["TRTDUR"], errors="coerce")
    print(f"\n  治疗暴露：TRTSDT 非缺失 {adsl['TRTSDT'].notna().sum()} 位，"
          f"TRTEDT 非缺失 {adsl['TRTEDT'].notna().sum()} 位")
    print(f"             TRTDUR 中位数 {dur.median():.0f} 天，"
          f"最短 {dur.min():.0f} 天，最长 {dur.max():.0f} 天")

    # ---- 三种结束日口径的对照（本案例第 1 个教学点）---------------------
    section("3. 三种治疗结束日口径 = 三个不同答案")
    ref_adsl = cio.read_any(data_dir / "adsl.csv")
    ref = ref_adsl.set_index("USUBJID")
    ref_end = pd.to_datetime(src["dm"].set_index("USUBJID")["RFENDTC"], errors="coerce")
    refT = pd.to_datetime(ref["TRTEDT"], errors="coerce")
    print(f"  {'口径':<8}{'说明':<46}{'与参考 TRTEDT 一致':>20}")
    print("  " + "-" * 76)
    for rule, desc in [
        ("ex", "TRTEDT = max(EXENDTC)（字面做法）"),
        ("imp", "缺失 EXENDTC→下一条起始日−1；末条未闭合→RFENDTC"),
        ("ref", "TRTEDT = RFENDTC（完全放弃 EX）"),
    ]:
        g = derive_exposure(src["ex"], rule, ref_end)
        a = g["TRTEDT"].reindex(refT.index)
        n = int(((a == refT) | (a.isna() & refT.isna())).sum())
        mark = "   ← 本项目口径" if rule == "imp" else ""
        print(f"  {rule:<8}{desc:<46}{n:>10d} / 254{mark}")
    print("""
  ★ 差的都是同一种人：**EX 末次给药记录没有 EXENDTC**（治疗仍在进行 / 记录未闭合）。
    照字面 max(EXENDTC) 会把他们的治疗时长严重截断。
    'ref' 口径看起来"最省事"，但它等于把 EX 域整个丢掉 —— 127/254。

  ★ 这就是为什么 ADaM 派生必须写清规则，并且**必须双编程验证**：
    "我把 TRTEDT 算出来了" 这句话，离开口径就没有意义。""")

    # ---- 实际治疗组口径对照（第 2 个教学点）-----------------------------
    section("4. 三种 TRT01A 口径：同一批人，三个身份")
    print(f"  {'口径':<12}{'说明':<40}{'与参考 TRT01A 一致':>20}")
    print("  " + "-" * 76)
    refA = ref["TRT01A"].astype("string")
    for rule, desc in [("arm", "取随机化臂 DM.ARM"),
                       ("actarm", "取 DM.ACTARM（申办方已派生的实际治疗）"),
                       ("maxdose", "按实际接受的最高剂量判断")]:
        tmp = build_adsl(src, "imp", rule, verbose=False)
        a = tmp.set_index("USUBJID")["TRT01A"].astype("string").reindex(refA.index)
        n = int((a == refA).sum())
        mark = "   ← 本项目口径" if rule == "arm" else ""
        print(f"  {rule:<12}{desc:<40}{n:>10d} / 254{mark}")
    print("""
  ★ 不一致的 12 位受试者是这样的：
    随机化到 Xanomeline High Dose（应服 81 mg），但在**滴定期**内退出了
    （最短 1 天、最长 14 天），全程只吃到 54 mg 的起始剂量。
      · 随机化臂口径 → High Dose（本项目参考 ADSL 的做法）
      · ACTARM / 最高剂量口径 → Low Dose
    两种都说得通，**但必须与 SAP 一致并写进 ADRG**。
    这也是"实际治疗组"这个变量在不同项目里含义不同的根本原因。""")

    # ---- 双编程验证 ----------------------------------------------------
    section("5. 双编程验证（与参考 ADSL 逐变量比对）")
    rep = None
    if args.no_validate:
        print("  已跳过（--no-validate）")
    else:
        # 口径对齐：验证时统一用与参考一致的口径，
        # 否则差异来自"口径不同"而不是"实现不同"，比对就失去意义
        py_adsl = build_adsl(src, "imp", "arm", verbose=False)
        check_vars = [
            "AGE", "AGEGR1", "AGEGR1N", "SEX", "RACE", "RACEN", "ETHNIC",
            "TRT01P", "TRT01PN", "TRT01A", "TRT01AN",
            "TRTSDT", "TRTEDT", "TRTDUR",
            "SAFFL", "ITTFL", "EFFFL", "COMP8FL", "COMP16FL", "COMP24FL",
            "DCDECOD", "DSRAEFL", "DTHFL", "VISNUMEN",
            "WEIGHTBL", "HEIGHTBL", "BMIBL", "BMIBLGR1",
        ]
        rep = validate(py_adsl, ref_adsl, check_vars)
        show = rep.copy()
        show["匹配率"] = show["匹配率"].map(
            lambda x: "-" if pd.isna(x) else f"{x:6.2f}%")
        print(show.to_string(index=False))

        cnt = rep["判定"].value_counts().to_dict()
        order_v = ["一致", "近似", "近似·数据受限", "不可派生", "不一致", "不可比"]
        print("\n  汇总：" + " ｜ ".join(
            f"{k} {cnt[k]}" for k in order_v if k in cnt) + f"   （共 {len(rep)} 个变量）")
        print("  ★ 报告口径：'近似' 和 '数据受限' 不是 bug，但**必须写进 ADRG**；")
        print("    只有 '不一致' 才代表实现有问题。")

        # ---- 逐项根因分析 ----
        # TRTEDT 用 'ex' 口径来演示，才能把差异"演"出来 —— 这正是
        # 双编程验证的价值：它把口径问题变成了**可量化的差异**。
        print("\n  " + "-" * 74)
        print("  【根因分析 · TRTEDT】下面故意换成 'ex' 字面口径，看差异长什么样")
        print("  " + "-" * 74)
        bad_ex = build_adsl(src, "ex", "arm", verbose=False)
        show_mismatch(bad_ex, ref_adsl, "TRTEDT", limit=4)
        print("""
  这 6 位的共同点：**EX 末次给药记录没有 EXENDTC**（治疗仍在进行）。
    · 01-704-1233 / 01-705-1031 / 01-705-1303 / 01-705-1377
      → 最后一条给药记录只填了开始日，没填结束日
    · 01-705-1018 / 01-705-1382
      → 只有一条给药记录，且结束日为空
  照字面 max(EXENDTC) 会拿到"倒数第二条"的结束日，把治疗时长严重截断
  （例如 01-704-1233：字面口径 15 天 vs 实际 116 天）。
  改用 'imp' 口径后 → 254/254 完全一致。""")

        print("\n  " + "-" * 74)
        print("  【根因分析 · VISNUMEN / COMP8FL】")
        print("  " + "-" * 74)
        show_mismatch(py_adsl, ref_adsl, "COMP8FL", limit=4)
        print("""
  VISNUMEN 在本项目里是"最后一次**疗效评估**访视的编号"，
  依赖 ADAS-Cog 数据集 —— **不在本案例可用的域里**。
  本案例只能从访视数据做最佳努力近似（234/254）。
  不一致的 20 位全是提前终止的受试者，其 ADSL 值比访视数据大 1
  （终止访视单独计数）。

  COMP8FL 的口径同样值得记一笔：
    · 本案例（正确口径）＝ 最后一次疗效访视号 >= 8
    · "治疗时长 >= 56 天"（直觉口径）→ 只有 183 人，比参考少 7 人
      例如 01-704-1241 只治疗了 46 天，但他完成了第 8 周访视 → 参考是 Y。
    ★ 在临床统计里，"完成 8 周治疗"通常指**完成了第 8 周访视**，
      不是"活了 56 天"。凭直觉写代码在这里一定会错。

  ★ 当你需要的数据不在手上时，正确做法是**去要数据**，
    而不是用一个"看起来像"的近似值悄悄顶上。
    一个编出来的标记比一个空值危险得多。""")

    section("6. 输出文件")
    stamp = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M")
    out_csv = out_dir / "adsl_derived.csv"
    adsl.to_csv(out_csv, index=False, encoding="utf-8-sig")
    print(f"  派生 ADSL：{out_csv}  （{adsl.shape[0]} 行 × {adsl.shape[1]} 列）")

    if rep is not None:
        rep_out = rep.copy()
        rep_out.insert(0, "生成时间", stamp)
        rep_out.insert(1, "结束日口径", "imp")
        rep_out.insert(2, "TRT01A口径", "arm")
        rp = out_dir / "adsl_validation.csv"
        rep_out.to_csv(rp, index=False, encoding="utf-8-sig")
        rpt.to_html(
            rep_out, out_dir / "adsl_validation.html",
            title="ADSL 双编程验证报告（Python 派生 vs 参考实现）",
            subtitle=f"生成时间：{stamp}；逐变量比对受试者水平数据集（254 位受试者）",
            footnote=(
                "匹配率 = 值完全一致（含【两边同为缺失】的情形）的受试者比例。"
                "标注为【不可派生】的变量表示所需源数据不在本次可用的域中，"
                "这**不是**实现错误。其余任何 &lt; 100% 的变量都必须定位根因并记录在 ADRG 中。"
            ),
            label_cols=("变量",),
        )
        print(f"  验证报告：{rp}")
        print(f"  HTML 版本：{out_dir / 'adsl_validation.html'}")

    section("7. 从 SAS 到 Python：这一段到底对应什么")
    print("""  下面这张表是给"写惯了 SAS 的人"看的 —— 左侧是你脑子里想的东西。

  +------------------------------------------------------------+----------------------------------------+
  | SAS 里你会写                                                | Python 里对应什么                      |
  +------------------------------------------------------------+----------------------------------------+
  | DATA ADSL; SET DM; IF ARM NOT IN (...);                     | dm[~dm["ARM"].isin(EXCLUDED_ARMS)]     |
  | PROC SORT DATA=EX; BY USUBJID EXSEQ;                        | ex.sort_values(["USUBJID","EXSEQ"])    |
  | RETAIN TRTSDT; TRTSDT=MIN(TRTSDT,EXSTDTC);                  | groupby("USUBJID").agg(min, max)       |
  |   TRTEDT=MAX(TRTEDT,EXENDTC);                               |   ← 不需要 RETAIN，聚合一步到位        |
  | TRTDUR = TRTEDT - TRTSDT + 1;                               | (TRTEDT-TRTSDT).dt.days + 1            |
  | MERGE ADSL(IN=A) _EX; BY USUBJID; IF A;                     | adsl.merge(expo, how="left")           |
  | CLASS 顺序靠 order= / preloadfmt                            | pd.Categorical(categories=TRT_LEVELS)  |
  | ROUND(BMI, 0.1)                                             | sas_round_series(BMI, 1) ★不是 round() |
  | PROC COMPARE BASE=ADSL COMPARE=ADSL_PY;                     | validate() —— 见本案例第 5 节          |
  +------------------------------------------------------------+----------------------------------------+

  ★ 四个最容易踩的坑：
    1) SAS 的 ROUND(x, 0.1) 是「按单位舍入 + 四舍五入」，
       Python 的 round(x, 1) 是「按小数位 + 银行家舍入」——
       round(2.5) 会给 2。必须用 clinic.derive.sas_round_series。
    2) SAS 里空字符串就是缺失，IF X = '' 能命中；
       pandas 里空串是合法值，必须 clean_missing()。
    3) SAS 的 MERGE 按 BY 值匹配且**自动去重主键**；
       pandas 的 merge 是"笛卡尔积后再取"——
       右表主键有重复时行数会**成倍膨胀**，必须先 drop_duplicates。
    4) pandas 的 .loc[mask, col] = value 在 mask 定位到
       **不存在的索引标签**时会**静默失效**，不报错。
       SAS 至少会给你一个 note。做"补齐后再赋值"时一定要先 reindex。""")


if __name__ == "__main__":
    main()
