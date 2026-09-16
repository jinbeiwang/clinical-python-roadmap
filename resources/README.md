# 资料索引

> **精选的、经过验证的**临床统计编程学习资源。
> 不做"链接大全" —— 每条都写明**为什么值得看**、**什么时候用**。

---

## 五份索引

| 文件 | 内容 | 什么时候看 |
|---|---|---|
| [01-GitHub权威仓库](01-GitHub权威仓库.md) | CDISC / PHUSE / Atorus 等组织的核心开源仓库（含实测星标数） | 想找现成的工具、想看别人怎么写生产代码时 |
| [02-标准与规范-CDisc-PHUSE](02-标准与规范-CDisc-PHUSE.md) | CDISC 标准、受控术语、试点数据集、PHUSE 工作组 | **查口径、查定义、查合法取值**时（最常用） |
| [03-论文与技术资料](03-论文与技术资料.md) | 会议论文去哪找、怎么搜、该读哪些主题 | 遇到具体技术问题、想了解行业实践时 |
| [04-Python学习资源与工具链](04-Python学习资源与工具链.md) | 官方文档、开发环境、包管理、8 周学习路径 | 刚开始学、或卡在某个工具上时 |
| [05-Agent开发与LLM资料](05-Agent开发与LLM资料.md) | Agent 工程化、评估与测试、临床/受监管场景的 AI 官方材料 | ★ 学完第 18–24 章，要把 Agent 用到真实项目时 |

---

## 如果你只有 10 分钟

按这个顺序打开 3 个链接：

1. 🟢 **[CDISC 试点项目数据](https://github.com/cdisc-org/sdtm-adam-pilot-project)**
   —— 本项目全部案例的数据来源。**唯一被 FDA 审阅过的公开完整数据集。**
   它可以让你在不接触真实患者数据的前提下，走完 SDTM → ADaM → TFL 的全流程。

2. 🟢 **[PHUSE OSTCDA](https://github.com/phuse-org/OSTCDA)**
   —— Open Source Technology in Clinical Data Analysis。
   用 SAS / R / Python 实现**同一张表**并比对结果。
   这是"用 Python 做临床统计到底靠不靠谱"最权威的回答。

3. 🟢 **[CDISC CORE 规则引擎](https://github.com/cdisc-org/cdisc-rules-engine)**
   —— 官方开源的合规性校验引擎（Python 实现）。
   想理解"CDISC 合规到底在检查什么"，直接读它的规则集最快。

---

## 一条学习建议

> 这个行业的知识有个特点：**标准是公开的，实践是口口相传的。**
>
> - **标准**（SDTM/ADaM 的变量定义、CT 取值）→ 去
>   [CDISC 官方](https://www.cdisc.org/standards) 查原文，**不要靠记忆**。
> - **实践**（这张表该用哪个分母、这个缺失值怎么补）→ 去
>   PHUSE 的工作组交付物与 PharmaSUG 论文里找共识，
>   但**最终以你项目的 SAP 和公司 SOP 为准**。
> - **代码**（具体怎么写）→ 去看
>   [phuse-org/phuse-scripts](https://github.com/phuse-org/phuse-scripts)
>   和 [atorus-research](https://github.com/atorus-research) 的仓库。
>
> 三者不可互相替代。**把"标准要求的"、"行业惯例的"、"你项目规定的"分清楚，
> 是临床统计程序员最重要的能力之一。**

---

**返回** → [项目主页](../README.md)
