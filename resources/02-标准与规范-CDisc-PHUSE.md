# CDISC / PHUSE 标准与官方资源

> 这一份是**查口径、查定义、查术语**时要用的地方。
> 临床统计编程的每一个数字背后都有一个标准依据 —— 找到它，比记住它更重要。

---

## 1. CDISC 官方（最权威）

**主站**：https://www.cdisc.org/

| 资源 | 链接 | 什么时候用 |
|---|---|---|
| 🟢 **SDTMIG**（SDTM 实施指南） | [cdisc.org/standards/foundational/sdtmig](https://www.cdisc.org/standards/foundational/sdtmig) | 查"这个变量该叫什么、该放哪个域"。SDTM 的宪法 |
| 🟢 **SDTM**（模型本体） | [cdisc.org/standards/foundational/sdtm](https://www.cdisc.org/standards/foundational/sdtm) | 域与变量的通用规则（不限于某一版 IG） |
| 🟢 **ADaM** | [cdisc.org/standards/foundational/adam](https://www.cdisc.org/standards/foundational/adam) | **ADSL/ADAE/ADTTE 的变量与派生要求。做 ADaM 必查** |
| 🟢 **受控术语（CT）** | [cdisc.org/standards/terminology/controlled-terminology](https://www.cdisc.org/standards/terminology/controlled-terminology) | 查 `AESEV`、`AEOUT`、`SEX` 等变量的**合法取值列表**。CT 违规是最常见的一致性错误 |
| 🔵 **标准总览** | [cdisc.org/standards](https://www.cdisc.org/standards) | 看看还有哪些标准（ODM、Define-XML、SEND、USDM…） |
| 🔵 **教育与培训** | [cdisc.org/education](https://www.cdisc.org/education) | 官方公开课、网络研讨会（部分免费） |
| ⚪ **Define-XML** | 见 standards 页下的 Data Exchange 部分 | 元数据交换："这个变量是什么、从哪来、怎么算的" |

### 免费但常被忽略的：CDISC Library

CDISC Library 是标准的**机器可读版本**。用它的好处是：
写代码时不用人肉抄 CT 列表，可以直接拉一份下来做校验。

- 官方 Python 客户端：[cdisc-org/CDISC-library-client](https://github.com/cdisc-org/CDISC-library-client)
- 社区 Python 工具：[scassells/cdisclibrarytools](https://github.com/scassells/cdisclibrarytools)

```python
# 思路示意（需申请 API key）
# 1) 从 CDISC Library 拉取 SEX 的受控术语 → 存成本地 JSON
# 2) QC 时用这份 JSON 校验数据里的 SEX 取值
# ★ 好处：CT 版本升级时只改一处，不用改程序
```

---

## 2. 试点项目：唯一被 FDA 审阅过的公开数据集

**CDISC SDTM/ADaM Pilot Project** — **本项目全部案例的数据来源**

- 仓库：[cdisc-org/sdtm-adam-pilot-project](https://github.com/cdisc-org/sdtm-adam-pilot-project)
- 内容：CDISCPILOT01（2007 年阿尔茨海默症试验）的 SDTM + ADaM + **原始 TFL 输出**

| 你能从中得到 | 为什么珍贵 |
|---|---|
| SDTM 原始域（DM/AE/EX/DS/VS/LB/MH/CM/SC/SV…） | 不用编造数据，全部真实 |
| ADaM 数据集（ADSL/ADAE/ADLBC/ADTTE/ADVS） | 可用来验证你自己的派生实现 |
| **原始 TFL 输出（PDF/RTF）** | ★ **可以拿自己的结果去对**。这是学习"口径"最直接的方式 |
| define.xml | 看真实的元数据长什么样 |

> 💡 **强烈建议的练习方式**：
> 不要只看数据。**拿一张原始 TFL 输出，自己用 Python 做一遍，
> 再和它逐个数字对。** 对不上的地方就是你要学的地方 ——
> 本项目 `cases/case02`~`case05` 全都是这么做的。

---

## 3. PHUSE（临床统计编程最有价值的社区）

**主站**：https://phuse.global/

| 资源 | 链接 | 为什么值得看 |
|---|---|---|
| 🟢 **PHUSE 资源库** | [phuse.global/resources](https://phuse.global/resources) | 白皮书、交付物、最佳实践文档的入口 |
| 🟢 **PHUSE Advance Hub** | [advance.phuse.global](https://advance.phuse.global/) | 工作组的成果集中地。**开源在临床落地的第一手资料** |
| 🔵 **PHUSE 活动** | [phuse.global/events](https://phuse.global/events) | 年会（EU/US/China）与单日研讨会，大量免费材料 |
| 🟢 **PHUSE 脚本库** | [phuse-org/phuse-scripts](https://github.com/phuse-org/phuse-scripts) | 行业标准分析脚本，按 CDISC 标准交付 |
| 🟢 **OSTCDA** | [phuse-org/OSTCDA](https://github.com/phuse-org/OSTCDA) | **Open Source Technology in Clinical Data Analysis**：SAS/R/Python 方法学对照。这是"用 Python 做临床统计到底行不行"最权威的回答 |
| 🔵 **valtools** | [phuse-org/valtools](https://github.com/phuse-org/valtools) | 开源工具在 GxP 下的验证框架 |

### PHUSE 的几个关键工作组（关注这些方向）

- **Open Source Technology in Clinical Data Analysis (OSTCDA)**
  —— 开源技术的方法学与合规性论证
- **Analysis & Display Standards** —— TFL 的标准化呈现
- **Data Transparency** —— 数据共享与去标识
- **Semantic Technology** —— CDISC 标准的知识图谱化（[rdf.cdisc.org](https://github.com/phuse-org/rdf.cdisc.org)）
- **Test Data Factory (TDF)** —— 测试数据生成（[仓库](https://github.com/phuse-org/TestDataFactory)）

> 💡 **PHUSE 与 PharmaSUG 的区别**：
> - **PHUSE** 更偏**项目制协作**（有工作组、有交付物、有开源仓库）
> - **PharmaSUG** 更偏**论文与经验分享**（大量实操技巧，见下一篇）
>
> 两个都要看。PHUSE 告诉你"应该怎么做"，PharmaSUG 告诉你"实际怎么做"。

---

## 4. 其他常被引用的标准组织

| 组织 / 标准 | 链接 | 说明 |
|---|---|---|
| **MedDRA** | [meddra.org](https://www.meddra.org/) | 不良事件编码字典。★ **需要授权**，Agent/程序不得绕过许可使用 |
| **WHODrug** | [who-umc.org](https://www.who-umc.org/) | 合并用药编码字典。同样需授权 |
| **ICH E3 / E9** | [ich.org](https://www.ich.org/) | 临床研究报告结构（E3）、统计原则（E9）。TFL 的"为什么要有这么多表"的依据 |
| **FDA 生物统计** | [fda.gov/.../biostatistics](https://www.fda.gov/about-fda/center-drug-evaluation-and-research-cder/office-biostatistics) | 审评相关的指南文件（Guidance for Industry） |
| **HL7 FHIR** | [hl7.org/fhir](https://hl7.org/fhir/) | 医疗数据交换标准。做 RWD（真实世界数据）方向会遇到 |
| **UCUM** | [ucum.org](https://ucum.org/) | 计量单位标准 —— SDTM 单位转换的依据。工具见 [stomioka/ucum](https://github.com/stomioka/ucum) |

---

## 5. 受控术语（CT）到底该怎么用

这是**最高频的 QC 问题来源**（本项目的 `clinic/qc.py` 就有 CT 检查）。

**三个版本层次，必须分清：**

| 层次 | 内容 | 谁定义 |
|---|---|---|
| **CDISC CT（SDTM CT）** | `SEX`、`AESEV`、`AEOUT`、`NY`（Y/N/U）等 | CDISC，每季度更新 |
| **申办方扩展 CT** | 在 CDISC CT 基础上补充项目特有取值 | 申办方，须写进 define.xml |
| **编码字典** | MedDRA（AE）、WHODrug/ATC（CM） | 商业授权方 |

**实操要点：**

1. **不要硬编码 CT 列表**。CT 每季度更新，硬编码意味着每季度改程序。
2. **QC 时要检查"数据里的取值 ⊆ 声明允许的取值"**，而不是"⊆ 我记得的取值"。
3. **CT 版本必须记录**（在 define.xml 与 ADRG 里）。同一份数据用不同版本 CT
   校验，结果会不同 —— 这是审计时的必查项。
4. **`Y`/`N` 的坑**：有些公司写 `YES`/`NO`，有些写 `Y`/`N`。
   这是**项目级约定**，不是标准 —— 但必须与 CT 一致。

```python
# 思路示意：把 CT 检查做成配置驱动，而不是硬编码
CONTROLLED_TERMS = {
    "dm": {"SEX": ["M", "F", "U"], "DTHFL": ["Y", "N"]},
    "ae": {"AESEV": ["MILD", "MODERATE", "SEVERE"], "AESER": ["Y", "N"]},
}

def check_ct(df, domain, terms):
    issues = []
    for var, allowed in terms.get(domain, {}).items():
        if var not in df.columns:
            continue
        actual = set(df[var].dropna().astype(str).str.strip().unique())
        illegal = sorted(actual - set(allowed))
        if illegal:
            issues.append((var, illegal))
    return issues
```

> ⚠️ **`Y`/`N` 缺失的陷阱**：
> 本项目里 `DTHFL` 只有死亡受试者才填 `Y`，其余为缺失 ——
> 这与 CT 里"允许取 `Y`/`N`"并不矛盾（CT 说明"允许"什么，不要求"必须填"）。
> **不要因为"缺失多"就判定数据有问题。** 本案例的 `case01` 特意演示了这一点。

---

## 6. 一页速查：遇到问题该去哪查

| 问题 | 去哪儿 |
|---|---|
| `AESEV` 可以取哪些值？ | CDISC CT 页面 |
| `ADSL` 必须有哪些变量？ | ADaM IG（`cdisc.org/standards/foundational/adam`） |
| `TRT01P` 和 `TRT01A` 的区别？ | ADaM IG 的 ADSL 章节 + 本项目 `case05` |
| "完成 8 周治疗"怎么算？ | SAP / define.xml / 本项目 `case05`（三种口径实测对比） |
| 这张表该用什么分母？ | SAP。**没有 SAP 就不要猜** |
| 我的结果和别人不一样怎么办？ | 先比口径（denominator / population / visit），再比实现 |
| 开源工具能用于注册申报吗？ | [cdisc-org/COSMoS](https://github.com/cdisc-org/COSMoS) + 公司内部验证流程 |
| 别人怎么用 Python 做这个？ | [phuse-org/OSTCDA](https://github.com/phuse-org/OSTCDA) |
| 有没有现成的示例输出可以对？ | [cdisc-org/sdtm-adam-pilot-project](https://github.com/cdisc-org/sdtm-adam-pilot-project) + [atorus-research/CDISC_pilot_replication](https://github.com/atorus-research/CDISC_pilot_replication) |

---

**返回** → [项目主页](../README.md) ｜ [资料索引总览](README.md)
