# 临床统计程序员的 Python 进阶路线图

> **给"熟 SAS、Python 只会一点点"的临床统计程序员。**
> 从语法速通到能开发 AI Agent —— 全程用**真实的公开临床数据**，
> 所有代码**实测可跑通**，所有数字**来自真实数据而非编造**。

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![pandas](https://img.shields.io/badge/pandas-2.x%20%7C%203.x-150458?logo=pandas&logoColor=white)
![CDISC](https://img.shields.io/badge/CDISC-SDTM%20%7C%20ADaM-005A9C)
![cases](https://img.shields.io/badge/%E5%AE%9E%E6%88%98%E6%A1%88%E4%BE%8B-13%20%E4%B8%AA-brightgreen)
![chapters](https://img.shields.io/badge/%E6%95%99%E7%A8%8B-25%20%E7%AB%A0-blue)
![license](https://img.shields.io/badge/license-MIT-blue)

### 📖 [在线阅读（GitHub Pages）](https://jinbeiwang.github.io/clinical-python-roadmap/)

带侧边栏导航、全文搜索、代码高亮与深浅色主题的完整文档站，
比在 GitHub 上逐页点 Markdown 舒服得多 —— 25 章正文、速查表、资料索引、
以及全部案例与工具包的**源码阅读页**都在里面。

---

## 这个项目解决什么问题

SAS 程序员的 Python 学习材料到处都是，但**几乎都对不上你的工作**：

| 常见教材 | 你的实际需求 |
|---|---|
| 教你写爬虫、Django、机器学习 | 你要的是"把跑了一年的 SAS 程序用 Python 重写" |
| 用 Titanic / 鸢尾花数据集 | 你要的是 SDTM / ADaM 数据集 |
| 讲 `list` / `dict` / 面向对象 | 你要的是 `PROC MEANS` 的对应写法 |
| 不讲缺失值、不讲舍入、不讲分组顺序 | **这些恰好就是差异最大的地方** |

**本项目反过来：从你已经懂的业务出发，用真实数据把 Python 讲透。**

---

## 适合谁

✅ **临床统计程序员 / 生物统计师**，SAS 用得熟，Python 学过但半忘
✅ **数据管理 / 统计编程相关岗位**，想转向 Python 技术栈
✅ **想了解 AI Agent 在临床数据场景怎么落地**的人
✅ 有编程基础、想了解 CDISC 数据结构的开发者

❌ 不适合：完全零编程基础的人（建议先补编程入门）

---

## 快速开始（3 条命令）

```bash
git clone https://github.com/jinbeiwang/clinical-python-roadmap.git
cd clinical-python-roadmap
pip install -r requirements.txt

# 直接跑（data/samples/ 里已有约 5MB 的样本数据，无需联网）
python cases/case01_数据体检.py
```

**不需要装 SAS，不需要下载几十 MB 的 XPT，不需要 API Key。**

想用**完整原始数据**（含 254 位受试者的全部 SDTM/ADaM 域）：

```bash
python scripts/download_data.py --core    # 从 CDISC 公开仓库下载 XPT
python scripts/make_samples.py            # 重新生成样本 CSV
```

> 💡 **Agent 案例全部能离线跑**：`case07`–`case12` 与 `case_http_demo`
> 默认都是 **Mock 模式**，不联网、不需要 API Key，但工具执行的是**真实逻辑**，
> 结论里的数字也来自真实数据。设置 `OPENAI_API_KEY` 即自动切到真实 LLM 模式。
>
> ```bash
> python cases/case08_分层架构QC_Agent.py     # 分层架构 + 预算 + 断点恢复
> python cases/case10_TLF生成流水线.py        # DAG 规划 + 并行 + 失败传播
> python cases/case12_QC_Agent服务化.py       # 起一个真能发请求的 HTTP 服务
> ```

---

## 目录结构

```
clinical-python-roadmap/
├── docs/                     # 25 章教程正文（00-24）
│   ├── *.md                  #   教程源文件（Markdown，GitHub 上可直接读）
│   ├── index.html            #   ★ 在线文档站首页（由 scripts/build_site.py 生成）
│   ├── guide/  code/         #   生成的章节页与源码阅读页
│   ├── assets/               #   站点样式、脚本与搜索索引
│   └── .nojekyll             #   关闭 Jekyll（文档里有 Liquid 会误解析的代码块）
├── cases/                    # 13 个可运行实战案例 ★ 核心
├── clinic/                   # 可复用的临床数据工具包
│   ├── io.py                 #   读 XPT/sas7bdat，保留标签，清洗伪缺失
│   ├── derive.py             #   SAS 语义的舍入、年龄分组、日期处理
│   ├── report.py             #   TFL 报表构造件（n (%)、Mean (SD)、移位表）
│   ├── qc.py                 #   数据质量检查、跨域一致性、PROC COMPARE 等价物
│   ├── agent_tools.py        #   暴露给 LLM Agent 的工具集
│   ├── agent_core.py         #   Agent 主循环、工具契约、预算闸门、观察者、指标
│   ├── agent_planner.py      #   任务 DAG、拓扑排序、并行调度、续跑
│   ├── agent_memory.py       #   分层记忆、结构化事实表、检索与引用
│   ├── agent_role.py         #   多 Agent 拓扑、角色契约、独立性守卫
│   ├── agent_http.py         #   带重试/退避/限流/脱敏审计的 HTTP 客户端
│   ├── config.py             #   12-Factor 配置、启动自检、密钥脱敏
│   └── agent_eval.py         #   确定性评估集（正向 + 否定式安全断言）
├── cheatsheets/              # 速查表 ★ 高频查阅（SAS→Python / Agent 开发）
├── resources/                # 精选资料索引（GitHub / CDISC / PHUSE / 论文）
├── ci/                       # GitHub Actions 工作流模板（见 ci/README.md）
├── scripts/                  # 数据下载、样本生成、文档站构建
│   ├── download_data.py      #   多通道下载 CDISC 公开 XPT
│   ├── make_samples.py       #   裁剪成入库的小体积样本 CSV
│   └── build_site.py         #   把 Markdown 与源码构建成 docs/ 下的静态站点
├── data/samples/             # 约 5MB 样本数据（已提交，离线可用）
└── outputs/                  # 运行结果（每次运行重新生成）
```

### 本地预览文档站

站点是**纯静态、零外部依赖**的（不引任何 CDN），克隆后用任意静态服务器打开即可：

```bash
python scripts/build_site.py --clean     # 重新生成 docs/
python -m http.server -d docs 8000       # 然后访问 http://localhost:8000
```

`build_site.py` 做三件事：Markdown → HTML（表格、代码块、脚注）、
仓库内相对链接重写（能指向站内的就指向站内，否则指向 GitHub）、
以及生成侧边栏与搜索索引（497 条小节级条目，按 `/` 或 `Ctrl+K` 唤起）。

> 为什么不直接用 Jekyll：教程正文的代码块里有 `${{ matrix.python-version }}`
> 这类 GitHub Actions 语法，会被 Jekyll 当 Liquid 标签解析而**构建失败**；
> 且仓库里全是中文文件名，Jekyll 的 permalink 会生成一串百分号编码的可分享 URL。
> 自己生成 HTML 更简单也更可控。

---

## 25 章学习路线

**阶段一：打通基础（你已经有编程底子，这部分要快）**

| 章 | 标题 | 关键收获 |
|---|---|---|
| [00](docs/00-学习路线图与如何使用本项目.md) | 学习路线图与如何使用本项目 | 怎么用这个仓库，别从头读到尾 |
| [01](docs/01-环境搭建与思维转换.md) | 环境搭建与思维转换 | ★ **"SAS 逐行思考" → "pandas 整列思考"** |
| [02](docs/02-基础语法速通-SAS对照.md) | 基础语法速通（SAS 对照） | 用 SAS 作锚点，最快过一遍语法 |
| [03](docs/03-核心数据结构-list-dict-str.md) | 核心数据结构 list/dict/str | 什么时候该用哪个 |
| [04](docs/04-控制流函数模块与异常.md) | 控制流、函数、模块与异常 | 函数取代 `%macro`；异常取代 `%abort` |
| [05](docs/05-文件与批处理自动化.md) | 文件与批处理自动化 | `pathlib` 取代 `filename pipe` |

**阶段二：数据操作（核心中的核心）**

| 章 | 标题 | 关键收获 |
|---|---|---|
| [06](docs/06-NumPy与向量化思维.md) | NumPy 与向量化思维 | ★ **理解广播，就再也不会写行循环** |
| [07](docs/07-pandas入门-DataFrame就是数据集.md) | pandas 入门：DataFrame 就是数据集 | `DataFrame` ↔ dataset 的完整映射 |
| [08](docs/08-数据操作对照-DATA步与PROC%20SQL.md) | 数据操作对照：DATA 步与 PROC SQL | 逐句对照表 |
| [09](docs/09-合并重塑与分组汇总.md) | 合并、重塑与分组汇总 | `merge` 的三个致命差异 |
| [10](docs/10-日期缺失值格式与数据质量.md) | 日期、缺失值、格式与数据质量 | ★ **差异最集中的一章** |

**阶段三：临床数据实战**

| 章 | 标题 | 关键收获 |
|---|---|---|
| [11](docs/11-读取XPT与临床数据结构.md) | 读取 XPT 与临床数据结构 | 不装 SAS 也能读 `.xpt` / `.sas7bdat` |
| [12](docs/12-SDTM数据处理实战.md) | SDTM 数据处理实战 | 真实 SDTM 域的读取与检查 |
| [13](docs/13-ADaM衍生与TFL报表生成.md) | ADaM 衍生与 TFL 报表生成 | ★ **ADSL 派生 + 双编程验证** |
| [14](docs/14-统计分析与可视化.md) | 统计分析与可视化 | `PROC TTEST/REG/LOGISTIC` 的对应 |

**阶段四：AI 与工程化**

| 章 | 标题 | 关键收获 |
|---|---|---|
| [15](docs/15-AI辅助编程与代码迁移.md) | AI 辅助编程与代码迁移 | ★ 用 LLM 迁移 SAS 代码，**但你要能判断对错** |
| [16](docs/16-Agent开发入门.md) | Agent 开发入门 | ★ **LLM + 工具 + 循环**，含离线兜底 |
| [17](docs/17-工程化与后续进阶.md) | 工程化与后续进阶 | Git、虚拟环境、pytest、CI；合规红线 |

**阶段五：Agent 核心能力（从"能跑"到"可维护"）**

| 章 | 标题 | 关键收获 |
|---|---|---|
| [18](docs/18-Agent架构设计.md) | Agent 架构设计 | ★ 分层架构（编排/工具/领域/基础设施）；**副作用分级**；三道预算闸；可序列化状态 → 断点恢复 |
| [19](docs/19-任务规划与调度.md) | 任务规划与调度 | DAG + 拓扑排序 + 分层并行；**部分失败要显式汇报**；人工确认点；从计划续跑 |
| [20](docs/20-工具调用进阶.md) | 工具调用进阶：契约、校验与边界 | ★ JSON Schema 契约与自检；路径白名单**用映射不用拼接**；结构化错误让模型自己改对 |
| [21](docs/21-记忆与上下文管理.md) | 记忆与上下文管理 | 全量/窗口/摘要三种策略；结构化事实表；检索**零命中就说零命中**（红线） |

**阶段六：Agent 工程化（从"我电脑上能跑"到"团队能放心用"）**

| 章 | 标题 | 关键收获 |
|---|---|---|
| [22](docs/22-多Agent协作.md) | 多 Agent 协作 | ★ **价值在独立性，不在数量**；三种拓扑；差异四级分级；独立性必须靠架构强制 |
| [23](docs/23-外部API与服务集成.md) | 与外部 API 及服务集成 | 超时/重试/退避+抖动/限流/幂等键；**401 绝不重试**；水位线要在落盘之后推 |
| [24](docs/24-部署与监控.md) | 部署与监控 | 启动 **fail fast**；`request_id` 贯穿日志；缓存键**必须含数据版本**；确定性评估集进 CI |

---

## 13 个实战案例（全部实测可跑通）

每个案例都对应**你工作里真实存在的表或任务**，
并且**刻意把最容易出错的口径问题摆在明面上**。

### 数据与报表（case01–case06）

| 案例 | 做什么 | 你会学到（以及会踩的坑） |
|---|---|---|
| [case01 数据体检](cases/case01_数据体检.py) | 一批域的自动体检 + 问题分级 | 为什么 `DM` 有 306 人而 `ADSL` 只有 254 人 —— **这是正常的，不是数据错误** |
| [case02 人口学表](cases/case02_人口学表.py) | 按治疗组出 Table 1 | ★ `AGEGR1` 的**边界**：65 归 `<65` 还是 `65-80`？`pd.cut` 的默认区间会给你**错误答案**；SAS 的 `ROUND` ≠ Python 的 `round` |
| [case03 不良事件汇总表](cases/case03_不良事件汇总表.py) | TEAE 按 SOC/PT 汇总 | ★ **分子是受试者数不是事件数**（事件数 1126 → 受试者数 218，差 5 倍）；空组必须显示 `0`；治疗组顺序不能靠字母序 |
| [case04 实验室移位表](cases/case04_实验室移位表.py) | 基线 → 基线的分级迁移表 | 分级阈值必须来自 `ANRLO`/`ANRHI` 而不是硬编码；行百分比 vs 列百分比；同一受试者多条记录取最差等级 |
| [case05 ADSL 衍生](cases/case05_ADSL衍生.py) | ★ 多域合并衍生 ADSL + **双编程验证** | **三种 "治疗结束日" 口径 → 248 / 254 / 127 三个不同答案**；`TRT01A` 抄 `DM.ACTARM` 会错 12 人（滴定期退出的受试者）；BMI 的**精度顺序**会改变结果；`VISNUMEN` 需要的数据不在手上时**怎么办** |
| [case06 批处理自动化](cases/case06_批处理自动化.py) | 目录清点 + 批量 XPT→CSV + 多 sheet 汇总 | 异常隔离（一个文件坏了不中断整批）；**幂等**（可反复重跑）；Excel sheet 名的 31 字符限制 |

### Agent 开发（case07–case12 + caseA）

| 案例 | 对应章节 | 你会学到（以及会踩的坑） |
|---|---|---|
| [case07 临床数据 QC Agent](cases/case07_临床数据QC_Agent.py) | 16 | ★ LLM + 工具 + 循环，8 个临床数据工具；Agent 与脚本的分水岭；**安全边界必须写在代码里而不是提示词里**；★ Mock 模式离线跑通 |
| [case08 分层架构 QC Agent](cases/case08_分层架构QC_Agent.py) | 18 / 20 | ★ **"提示词挡不住的东西用代码挡"**：写操作必须人工确认；预算超限要**优雅收尾**（给部分结论）而不是抛异常；参数写错 → 结构化错误 → 模型自己改对；断点恢复不重复已完成步骤 |
| [case09 SDTM 一致性核查 Agent](cases/case09_SDTM一致性核查Agent.py) | 21 | ★ **检索阈值必须用带标注的探针集标定，不能拍脑袋**（本案例实测 0.40：召回 8/8、误命中 0/7）；工具返回"未找到"时**必须如实说没找到**；代码块里的 `#` 会被误当标题 |
| [case10 TLF 生成流水线](cases/case10_TLF生成流水线.py) | 19 | ★ 11 个任务的 DAG：分层并行（实测加速比）；失败重试与**传播**；`optional` 语义是"本任务失败不阻塞下游"；部分失败要显式汇报；人工确认点 + **从计划续跑不重复副作用** |
| [case11 双编程 Agent 对](cases/case11_双编程Agent对.py) | 22 | ★★ **零差异 + 独立性被破坏 = 零价值**。守卫同时记录"谁读了什么/谁产出了什么"，`B'` 真去读 A 的产物会被自动抓住；差异四级分级（critical/major/minor/**explainable**）；审计覆盖率 0 的"通过"等于没查 |
| [case12 QC Agent 服务化](cases/case12_QC_Agent服务化.py) | 24 | ★★ **用标准库起一个真能发请求的 HTTP 服务**（`/health`、`/qc`、`/qc/async`、422 校验、404）；`request_id` 贯穿日志；缓存键**漏掉数据版本就静默返回旧数据**；评估集**第一次跑就抓出了 Mock 的静默替换数据集缺陷** |
| [caseA 外部数据源客户端](cases/case_http_demo.py) | 23 | 指数退避 + 抖动（可注入 sleep，所以能断言）；**401 绝不重试**（一次都没重试）；尊重 `Retry-After`；**先落盘再推水位线**；去标识化的双层判据（列名 + 取值形态） |

### 案例的输出

跑完会在 `outputs/` 生成可直接查看的产物：

- `table01_demographics.html` —— Table 1（带样式）
- `table03_lab_shift.html` —— 实验室移位表
- `adsl_derived.csv` + `adsl_validation.html` —— ★ **双编程验证报告**
- `case06_summary.xlsx` —— 多 sheet 汇总
- `agent_report.md` + `agent_transcript.json` —— Agent 的报告与可审计对话记录
- `pair/case11_report.md` —— ★ 双编程比对报告（含差异分级与受影响受试者）
- `pair/agegr1_copied_independence.md` —— ★ 独立性审计（含违规命中）
- `service/eval_report.md` —— ★ Agent 评估报告（5/5 通过）
- `audit/app.log` —— 结构化日志（可按 `request_id` 捞一次请求的完整轨迹）
- `audit/http.jsonl` —— 外部调用的审计（已脱敏）

### case05 的验证结果（真实的，不是宣称的）

派生 ADSL 与 CDISC 官方参考实现逐变量比对 28 个变量：

```
一致 23 个 ｜ 近似 3 个 ｜ 近似·数据受限 1 个 ｜ 不可派生 1 个
```

**每一个不是 100% 的变量，都定位到了具体根因并写在程序的输出里。**
例如：`TRTEDT` 的 254/254 一致，靠的是正确识别出
"EX 末次给药记录未闭合（治疗仍在进行）时，应取参考结束日 `RFENDTC`"。

---

## 这个仓库和别人的有什么不同

### 1. 所有数字都来自真实数据，没有任何编造

教程里出现的每一个计数、每一个匹配率，都是从
[CDISC SDTM/ADaM Pilot Project](https://github.com/cdisc-org/sdtm-adam-pilot-project)
（CDISCPILOT01，2007 年阿尔茨海默症试验，306 位筛选 / 254 位随机化）
实际跑出来的。**你可以自己复现每一个数字。**

### 2. 刻意暴露"口径问题"，而不是藏起来

绝大多数教程只会说"这样做就对了"。本项目会告诉你：

- 同一个"治疗结束日"，三种合理口径给出 **248 / 254 / 127** 三个答案
- 同一个"实际治疗组"，抄 `DM.ACTARM` 会错 12 个人
- "完成 8 周治疗"按"治疗时长 ≥ 56 天"算会少 7 个人

**这些不是 bug，这就是临床统计编程的本质工作。**

### 3. 有一个真实可用的工具包

`clinic/` 不是教学玩具，是能直接用到项目里的：

```python
from clinic import io as cio
from clinic.derive import sas_round_series, derive_agegr1
from clinic.report import summarize_continuous, crosstab_shift
from clinic.qc import check_dataset, compare_frames

dm = cio.read_xpt("data/raw/dm.xpt")          # 保留变量标签
df["BMIBL"] = sas_round_series(df["BMI"], 1)  # 与 SAS 一致的四舍五入
```

### 4. Agent 案例全部能离线跑

`case07`–`case12` 默认都是 Mock 模式：**不联网、不需要 API Key**，
用确定性的决策序列调用工具，但**工具执行的是真实逻辑**，
结论里的数字也来自真实数据。这解决了一个很现实的问题 ——
内网 / 合规环境没法调外部 LLM，但你又想先把流程验证通。

外部依赖同样可注入：`agent_http` 的传输层让"对面超时、限流、401、
返回缺字段的 JSON"都能在离线状态下被复现和断言。

### 5. 每条"经验之谈"都配了一个能跑给你看的证据

这是本项目最不像教程的地方：**它不满足于告诉你结论**。

| 常听到的说法 | 本项目的做法 |
|---|---|
| "检索阈值要调好" | case09 用 8 个应命中 + 7 个应落空的探针集实测，选 0.40，并说明 0.43 那条为什么是薄弱边缘 |
| "独立性很重要" | case11 让 `B'` **真的去读 A 的产物**，由守卫自动抓出违规；再对比"没登记任何来源"的第三种情况 |
| "缓存要小心数据更新" | case12 打印出 v1 / v2 两个缓存键，让你看见"键相同 → 静默返回旧数据" |
| "要有评估集" | case12 的评估集**第一次跑就挂了两个用例**，查下去是 Mock 会静默替换数据集 —— 修代码，不是删断言 |
| "别重试 401" | case_http_demo 里 `max_retries=5`，实际请求次数打印出来是 **1** |

---

## 只记 5 个坑的话，记这 5 个

如果你的时间只够记几条，就记这些（每一条都有对应的可运行代码）：

### 1. `round()` 不是 SAS 的 `ROUND()`

```python
round(2.5)          # 2   ← 银行家舍入
round(2.675, 2)     # 2.67（浮点误差）
sas_round(2.5, 1)   # 3.0  ✅ SAS 语义：四舍五入
sas_round(2.675, 0.01)  # 2.68 ✅
```

→ `clinic/derive.py` · `cases/case02`

### 2. `.std()` 和 `np.std()` 不一样

```python
s.std()      # ddof=1 —— 与 SAS 的 STD 一致 ✅
np.std(s)    # ddof=0 —— 与 SAS **不一致** ❌
```

### 3. 空字符串在 pandas 里**不是**缺失值

```python
if x = '' then ...      /* SAS：能命中 */
df["x"].isna()          # pandas：空串不算缺失 ❌
cio.clean_missing(df)   # ✅ 先统一清洗
```

→ `clinic/io.py` · `cases/case01`

### 4. `groupby` 默认按字母序排，且会**丢掉空组**

```python
df.groupby("TRT01P").agg(...)                 # ❌ High Dose 跑到 Low Dose 前面
df["TRT01P"] = pd.Categorical(df["TRT01P"], categories=TRT, ordered=True)
df.groupby("TRT01P", observed=False).agg(...) # ✅ 按 SAP 指定顺序，空组显示 0
```

→ `cases/case02` · `cases/case03`

### 5. `merge` 右表主键重复会**静默成倍膨胀**

```python
a.merge(b, on="USUBJID")   # 右表有重复 → 行数悄悄变多，不报错
assert not b.duplicated(subset=["USUBJID"]).any()   # ✅ 先断言
assert len(a) == n0        # ✅ 合并后必查行数
```

→ `cases/case05`

完整的 20 个坑见 **[SAS → Python 速查表](cheatsheets/SAS-to-Python速查表.md)**。

---

## 只记 3 个 Agent 的坑的话，记这 3 个

写 Agent 的坑和写脚本完全不同 —— 脚本错了会报错，Agent 错了会**安静地给你一个像模像样的答案**。

### 1. "静默替换"比崩溃危险一百倍

```python
# 用户问 adata，代码认不出来，于是"顺手"换成第一个可用的数据集
ds = pick_dataset(goal) or "adsl"     # ❌ 结论看起来完全正常，只是分析的是另一个数据集
```

本项目的评估集**第一次跑就抓出了这个缺陷**（`clinic/agent_core.py`）。
正确做法是**先枚举、再如实说没有**，而不是猜一个顶上。

→ `clinic/agent_core.py` · `cases/case12`

### 2. 独立性一旦破坏，"零差异"是坏消息

```
B 抄了 A        → 0 处差异，独立性 ✗  → 零价值（同一份错误被复制了一遍）
真正独立的 A、B  → 2 处差异，独立性 ✓  → 找到了 2 个真问题
```

所以独立性**必须靠架构强制**（上下文物理隔离、产物命名空间隔离、
比对由第三方代码执行），不能靠自觉。而且**审计覆盖率为 0 的"通过"等于没查**。

→ `clinic/agent_role.py` · `cases/case11`

### 3. 缓存键漏掉数据版本 = 静默返回旧数据

```python
key = f"{tool}|{args}"                    # ❌ 数据更新后仍返回旧结果
key = f"{data_version}|{tool}|{args}"     # ✅ mtime + size 参与键
```

这类事故不报错、不崩溃、表看起来完全正常，只是数字是上一版的。
同理：**水位线必须在落盘成功之后才推进**，顺序反了就是永远补不回来的丢数。

→ `clinic/config.py`（`data_version_of`）· `cases/case12` · `cases/case_http_demo`

---

## 数据来源与许可

**数据**：来自 [CDISC SDTM/ADaM Pilot Project](https://github.com/cdisc-org/sdtm-adam-pilot-project)
（CDISCPILOT01）。这是唯一被 FDA 审阅过的公开完整数据集，
包含 SDTM + ADaM + **原始 TFL 输出** ——
所以你可以拿自己的结果去和官方输出逐个数字对。

**代码**：MIT License。可以自由用于学习、公司内部培训，乃至商业项目。

---

## 合规提醒（重要）

⚠️ 这是一个**学习项目**，不是监管文件，也不构成合规建议。

在药企 / CRO 环境里使用 Python 或任何开源工具前，请务必注意：

| 关注点 | 说明 |
|---|---|
| **GxP 验证** | 开源工具通常需要走内部验证流程。见 [CDISC COSMoS](https://github.com/cdisc-org/COSMoS) |
| **数据出境** | 患者数据**不能**发给云端 LLM。Agent 场景优先用本地模型（Ollama + Qwen / DeepSeek） |
| **21 CFR Part 11** | Agent / 自动化工具**不应**直接签署或修改源数据 |
| **可追溯性** | 保存完整的执行记录 + 依赖版本 + 模型版本 |
| **口径依据** | 本项目演示的口径仅用于教学。**实际项目以 SAP + 公司 SOP + 监管指南为准** |
| **编码字典** | MedDRA / WHODrug 需要授权，不得绕过许可使用 |

> 🧠 **务实的落地路径**：
> 先把 Python / Agent 用在不接触患者数据的场景 ——
> 代码迁移、程序审查、从规范文档找答案、生成程序骨架。
> 这些**价值高、风险低**，是最现实的切入点。

---

## 精选资料索引

| 索引 | 内容 |
|---|---|
| [GitHub 权威仓库](resources/01-GitHub权威仓库.md) | CDISC / PHUSE / Atorus 核心仓库（含实测星标数） |
| [标准与规范](resources/02-标准与规范-CDisc-PHUSE.md) | SDTMIG / ADaM / 受控术语 / 试点数据集 / PHUSE 工作组 |
| [论文与技术资料](resources/03-论文与技术资料.md) | 会议论文去哪找、怎么搜、该读哪些主题 |
| [Python 学习资源与工具链](resources/04-Python学习资源与工具链.md) | 官方文档、开发环境、8 周学习路径 |
| [Agent 开发与 LLM 资料](resources/05-Agent开发与LLM资料.md) | ★ Agent 工程化、评估与测试、临床 / 受监管场景的 AI 官方材料 |

**速查表（高频查阅）**

| 速查表 | 内容 |
|---|---|
| [SAS → Python 速查表](cheatsheets/SAS-to-Python速查表.md) | 14 类语法对照 + 常见坑 Top 20 |
| [Agent 开发速查表](cheatsheets/Agent开发速查表.md) | ★ 分层 / 契约 / 重试决策表 / 独立性 / 反模式 Top 15 / 上线检查清单 |

**如果只打开 3 个链接：**

1. [CDISC 试点数据](https://github.com/cdisc-org/sdtm-adam-pilot-project) —— 本项目的数据来源
2. [PHUSE OSTCDA](https://github.com/phuse-org/OSTCDA) —— SAS/R/Python 方法学对照
3. [CDISC CORE](https://github.com/cdisc-org/cdisc-rules-engine) —— 官方开源一致性规则引擎

---

## 常见问题

**Q: 我 SAS 很熟，需要从头学 Python 语法吗？**
不用。看[第 02 章](docs/02-基础语法速通-SAS对照.md)快速过一遍，
然后直接从[第 06-07 章](docs/06-NumPy与向量化思维.md)（向量化与 DataFrame）切入。
**难点从来不在语法，在思维方式。**

**Q: 会不会学了 Python 就不用 SAS 了？**
不是替代关系。现实里 Python 和 SAS **长期共存**。
本项目的目标是让你能**用 Python 做 SAS 现在做的事**，
并在某些场景（自动化、Agent、大文件处理、元数据驱动）做得更好。

**Q: 没有 SAS 环境能跑通吗？**
可以。全部案例只需要 Python 就能跑，读 XPT 用 `pyreadstat`，不需要装 SAS。

**Q: 需要联网吗？**
不需要。`data/samples/` 里已有约 5MB 样本数据，克隆后直接就能跑。
只有想用完整原始数据时才需要 `scripts/download_data.py`。

**Q: 案例的数字我能对得上官方输出吗？**
能，而且你应该去对。
建议拿 [CDISC 试点项目的原始 TFL 输出](https://github.com/cdisc-org/sdtm-adam-pilot-project)
（仓库里有 PDF/RTF）自己比一遍 —— **对不上的地方就是你要学的地方**。

**Q: 学完能做什么？**
- 把现有的 SAS 程序用 Python 重写并做双编程验证
- 搭建自动化的数据体检 / QC 流水线
- 用元数据驱动的思路替代手写映射
- 开发辅助性的 Agent（代码审查、文档检索、程序骨架生成）
- 把 Agent 做成**团队能放心用**的服务：接口、指标、审计、评估集齐全

**Q: 第 18–24 章和前面的 Agent 内容重复吗？**
不重复。[第 16 章](docs/16-Agent开发入门.md)解决的是"Agent 是什么、
最小可用长什么样"；18–24 章解决的是"**怎么让它可维护、可上线、
可审计**" —— 分层架构、任务规划、工具契约、记忆检索、多 Agent 独立性、
外部集成、部署与监控，每一章都配一个能跑的案例。

**Q: Agent 部分要花钱吗？**
不需要。13 个案例全部可在 Mock 模式离线跑完，
包括第 24 章那个真能发请求的 HTTP 服务（它自己用标准库起服务，
再用标准库发请求，全程在回环地址上）。想验证真实 LLM 行为时，
设置 `OPENAI_API_KEY` 即可切换。

---

## 参与贡献

欢迎提 Issue 和 PR，特别是：

- 🐛 **发现的错误**：任何数字对不上、代码跑不通、口径写错了
- 💡 **补充真实案例**：你工作里的表型，脱敏后可以作为案例
- 📚 **补充资料**：新的权威仓库、有价值的论文
- 🌐 **翻译**：英文版会很有价值

提 PR 前请确认：**代码实测可跑通**，**数字能从真实数据复现**。

---

## 许可

代码：[MIT License](LICENSE)
数据：见 [LICENSE](LICENSE) 末尾的数据声明 —— 来自 CDISC 公开项目，
再分发时请遵守原始许可。

---

**如果这个项目帮你省下了摸索的时间，欢迎 Star ⭐ —— 也欢迎告诉身边的同行。**
