# 权威 GitHub 仓库索引

> **面向临床统计编程的 Python / R / 开源工具链。**
> 收录标准：真实存在、在维护、对临床数据工作有直接价值。
> 星标数为 **2026-09** 的实测值（会随时间变化，仅供判断热度参考）。

**图例**：🟢 强烈推荐 ｜ 🔵 值得收藏 ｜ ⚪ 参考

> ⚠️ 部分企业内网无法直连 `github.com`。
> 如果拉取失败，优先检查是否需要配置代理；本项目的
> `scripts/download_data.py` 内置了多通道回退，可作参考。

---

## 1. 数据读取与写入（Python ↔ SAS）

| 仓库 | 星标 | 说明 |
|---|---|---|
| 🟢 [Roche/pyreadstat](https://github.com/Roche/pyreadstat) | 428 | **本项目用来读 XPT 的库**。读写 SAS/SPSS/Stata 文件，保留变量标签与值标签。Python 生态里事实标准 |
| 🔵 [sanders41/sas7bdat-converter](https://github.com/sanders41/sas7bdat-converter) | 22 | 把 `.sas7bdat` 转成 CSV/Parquet 的命令行工具，适合批量转换场景 |
| 🔵 [dylanmeysmans/duckdb-read-stat](https://github.com/dylanmeysmans/duckdb-read-stat) | 35 | 在 DuckDB 里直接查 SAS/Stata/SPSS 文件。**数据大到内存装不下时的正确解法** |
| ⚪ [asnr/sas-to-python](https://github.com/asnr/sas-to-python) | 25 | SAS DATA 步语法 → pandas 的翻译规则。做迁移时拿来对照 |
| ⚪ [swhume/odmlib](https://github.com/swhume/odmlib) | 29 | Python 处理 CDISC ODM / Define-XML。写 define.xml 时有用 |
| ⚪ [swhume/odmlib_examples](https://github.com/swhume/odmlib_examples) | 14 | odmlib 的示例程序集 |

---

## 2. CDISC 官方（`cdisc-org`）

| 仓库 | 星标 | 说明 |
|---|---|---|
| 🟢 [cdisc-org/sdtm-adam-pilot-project](https://github.com/cdisc-org/sdtm-adam-pilot-project) | 81 | **本项目的全部数据来源**。CDISCPILOT01（2007 年阿尔茨海默症试验）的 SDTM + ADaM + 原始 TFL 输出，是唯一被 FDA 审阅过的公开完整数据集 |
| 🟢 [cdisc-org/cdisc-rules-engine](https://github.com/cdisc-org/cdisc-rules-engine) | 114 | **CORE（CDISC Open Rules Engine）**。官方开源的一致性校验引擎，用 Python（pandas/dask）实现。可以直接对 SDTM/ADaM 跑官方规则集 |
| 🟢 [cdisc-org/analysis-results-standard](https://github.com/cdisc-org/analysis-results-standard) | 72 | **ARS**。新一代"分析结果元数据"标准 —— 用机器可读的方式描述 TFL 的呈现逻辑。做自动化报表必看 |
| 🔵 [cdisc-org/DataExchange-DatasetJson](https://github.com/cdisc-org/DataExchange-DatasetJson) | 41 | **Dataset-JSON**。CDISC 推出的 XPT 替代交换格式，JSON 友好、能保留元数据。未来几年会逐步替代 XPT |
| 🔵 [cdisc-org/COSMoS](https://github.com/cdisc-org/COSMoS) | 33 | Clinical Open Source Management & Operations —— 药企如何**合规地**引入开源软件的实践指南 |
| 🔵 [cdisc-org/usdm](https://github.com/cdisc-org/usdm) | 22 | USDM：机器可读的协议（Protocol）标准，让"协议 → 数据 → 报表"可追溯 |
| ⚪ [cdisc-org/DDF-RA](https://github.com/cdisc-org/DDF-RA) | 53 | Digital Data Flow / Real-world应用方向 |
| ⚪ [cdisc-org/CDISC-library-client](https://github.com/cdisc-org/cdisc-library-client) | 9 | 官方 Python 客户端，程序化访问 CDISC Library（标准原文、CT 术语） |
| ⚪ [cdisc-org/cosa](https://github.com/cdisc-org/cosa) | 18 | CDISC Open Source Alliance 相关 |

---

## 3. PHUSE 项目仓库（`phuse-org`）

PHUSE 是临床统计编程领域最有影响力的**开源协作社区**。

| 仓库 | 星标 | 说明 |
|---|---|---|
| 🟢 [phuse-org/phuse-scripts](https://github.com/phuse-org/phuse-scripts) | 129 | **PHUSE 官方脚本库**。按 CDISC 标准交付的行业标准分析脚本，覆盖 SDTM/ADaM/SEND。看别人的生产代码怎么写，这是最好的样本 |
| 🟢 [phuse-org/OSTCDA](https://github.com/phuse-org/OSTCDA) | 39 | **Open Source Technology in Clinical Data Analysis**。最有价值的资源之一：SAS/R/Python 的**方法学对照**，同一张表用三种语言实现并比对结果 |
| 🔵 [phuse-org/valtools](https://github.com/phuse-org/valtools) | 56 | 临床研究中 R 包的**验证框架**。想理解"开源工具在 GxP 环境怎么验证"，看这个 |
| 🔵 [phuse-org/TestDataFactory](https://github.com/phuse-org/TestDataFactory) | 31 | **TDF**。生成符合 CDISC 标准的测试数据 —— 开发阶段不用真实患者数据的解法 |
| ⚪ [phuse-org/rdf.cdisc.org](https://github.com/phuse-org/rdf.cdisc.org) | 36 | 语义技术工作组：把 CDISC 标准转成 RDF/知识图谱 |
| ⚪ [phuse-org/git-in-statistical-programming](https://github.com/phuse-org/git-in-statistical-programming) | 14 | **Git 在统计编程中的使用规范**。`git pull` 遇到冲突怎么办、分支怎么管，这里有行业共识 |
| ⚪ [phuse-org/aesummaries](https://github.com/phuse-org/aesummaries) | 10 | AE 汇总表的开源实现（森林图方向） |
| ⚪ [phuse-org/sendigR](https://github.com/phuse-org/sendigR) | 14 | SEND 数据集的跨研究分析（R） |

---

## 4. Atorus Research（开源 pharmaverse 的重要推手）

| 仓库 | 星标 | 说明 |
|---|---|---|
| 🟢 [atorus-research/CDISC_pilot_replication](https://github.com/atorus-research/CDISC_pilot_replication) | 53 | **用现代 R + pharmaverse 复刻 CDISC 试点项目的全部 TFL 输出。** 想验证自己的实现对不对，这是最好的参照物 |
| 🟢 [atorus-research/xportr](https://github.com/atorus-research/xportr) | 54 | 把数据框导出成**符合 CDISC 合规要求**的 XPT（长度、标签、格式都按规范处理）。★ 这些细节手写极易踩坑 |
| 🔵 [atorus-research/Tplyr](https://github.com/atorus-research/Tplyr) | 109 | 分层汇总表框架（如 AE 表、人口学表），代替 `PROC TABULATE` 的思路 |
| 🔵 [atorus-research/metacore](https://github.com/atorus-research/metacore) | 50 | 基于元数据（define.xml / spec）自动给数据集加标签与格式 |
| 🔵 [atorus-research/atorus-sas-macros](https://github.com/atorus-research/atorus-sas-macros) | 32 | 开源 SAS 宏集。**如果你还在写 SAS，这是现成的轮子** |
| ⚪ [atorus-research/datasetjson](https://github.com/atorus-research/datasetjson) | 26 | Dataset-JSON 的读写实现（R） |
| ⚪ [atorus-research/pharmaRTF](https://github.com/atorus-research/pharmaRTF) | 34 | 生成符合药企排版要求的 RTF 输出 |

---

## 5. pharmaverse（R 生态，但思路值得学）

| 仓库 | 星标 | 说明 |
|---|---|---|
| 🟢 [pharmaverse/sdtm.oak](https://github.com/pharmaverse/sdtm.oak) | 77 | **EDC 与标准无关的 SDTM 转换引擎**：从 spec（Excel/metadata）驱动生成 SDTM，而不是逐个手写映射代码。这是 SDTM 生产的趋势方向 |

> 💡 **为什么 R 生态值得 SAS 程序员看？**
> pharmaverse（R）在"元数据驱动 + 自动化"这条路上走得比 Python 生态更前面，
> 有很多设计思想（spec 驱动、`xportr` 的合规检查、`metacore` 的标签注入）
> 完全可以移植到 Python。**学的是思路，不是语言。**

---

## 6. 数据映射与 AI 辅助

| 仓库 | 星标 | 说明 |
|---|---|---|
| 🔵 [stomioka/sdtm_mapper](https://github.com/stomioka/sdtm_mapper) | 58 | AI 辅助 SDTM 映射（R 做 ML，Python/TensorFlow 做 DL） |
| ⚪ [stomioka/ucum](https://github.com/stomioka/ucum) | 5 | UCUM 单位换算与校验 —— 单位转换是 SDTM 里最容易出错的一环 |
| ⚪ [scassells/cdisclibrarytools](https://github.com/scassells/cdisclibrarytools) | 11 | 访问 CDISC Library 的 Python 工具 |

---

## 7. 通用 Python 数据栈（必装的几个）

这些不是临床专用，但你的工作 90% 靠它们。

| 包 | 文档 | 用途 |
|---|---|---|
| **pandas** | [pandas.pydata.org/docs](https://pandas.pydata.org/docs/) | DataFrame —— 取代 DATA 步 + PROC SQL |
| **numpy** | [numpy.org/doc/stable](https://numpy.org/doc/stable/) | 向量化运算的底座 |
| **pyreadstat** | [pyreadstat.readthedocs.io](https://pyreadstat.readthedocs.io/) | 读写 XPT / sas7bdat / SPSS |
| **openpyxl** | [openpyxl.readthedocs.io](https://openpyxl.readthedocs.io/) | 读写 xlsx（含单元格格式） |
| **scipy** | [scipy.org](https://scipy.org/) | 统计检验（t 检验、Fisher、卡方、Wilcoxon） |
| **statsmodels** | [statsmodels.org](https://www.statsmodels.org/) | 回归、Logistic、Mixed Model（对应 PROC REG/LOGISTIC/MIXED） |
| **matplotlib** | [matplotlib.org](https://matplotlib.org/) | 绘图（对应 PROC SGPLOT） |
| **Jupyter** | [jupyter.org](https://jupyter.org/) | 交互式探索 —— **替代 SAS Studio 的探索体验** |
| **pyarrow** | [arrow.apache.org/docs/python](https://arrow.apache.org/docs/python/) | Parquet 读写，大文件提速 10 倍以上 |

---

## 8. 怎么用这些仓库（给临床程序员的实操建议）

**如果你是纯 SAS 背景，第一次看开源项目，建议按这个顺序：**

1. **先跑通数据** —— 克隆 `sdtm-adam-pilot-project`，用本项目
   `scripts/download_data.py` 把 XPT 拉下来，用 `case01` 体检一遍。
   *目的：建立"这些数据我真的能读到"的信心。*
2. **再对照结果** —— 打开 `CDISC_pilot_replication` 或
   `phuse-org/phuse-scripts`，找一张你已经做过的表（比如 TEAE 汇总），
   看别人怎么实现。
   *目的：建立"我的口径对不对"的判断标准。*
3. **再看工程化** —— 读 `phuse-org/git-in-statistical-programming`，
   理解版本管理在受监管环境里怎么落地。
   *目的：从"写代码"进阶到"交付可审计的程序"。*
4. **最后看元数据驱动** —— 看 `sdtm.oak` 和 `analysis-results-standard`。
   *目的：理解行业的下一个十年往哪走 —— 从"手写程序"到"元数据驱动生成"。*

> ⚠️ **合规提醒**：以上开源软件在 GxP 环境使用前，通常需要走
> 内部验证流程（见 `cdisc-org/COSMoS`）。
> 开源 ≠ 可以直接用于提交。**"能跑通"和"能用于注册申报"是两件事。**

---

**返回** → [项目主页](../README.md) ｜ [资料索引总览](README.md)
