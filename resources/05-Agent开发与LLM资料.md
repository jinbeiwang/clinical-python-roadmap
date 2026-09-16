# Agent 开发与 LLM 工程资料

> 目标读者：**会写 Python、要做出能上线、能审计的 Agent** 的临床统计程序员。
>
> 这一份**刻意不收录**"最新最强的模型榜单""提示词技巧合集"这类内容 ——
> 它们半年就过时。只收录那些**方法层面的、几年后仍然成立**的东西，
> 以及**与受监管环境相关的**官方材料。
>
> ⚠️ 外部链接会长草、会搬家。看到链接打不开时，用标题当关键词搜一次即可。

---

## 0. 先建立判断力：四份必读

| 资源 | 为什么值得看 |
|---|---|
| 🟢 **[Building Effective Agents](https://www.anthropic.com/engineering/building-effective-agents)** | 目前把"什么时候该用 Agent、什么时候用工作流就够了"讲得最清楚的一份。**核心结论是"能用简单方案就别上 Agent"** —— 这与本项目第 22 章"先证明单 Agent 不够用"完全一致 |
| 🟢 **[Model Context Protocol](https://modelcontextprotocol.io/)** | 工具/上下文接入的开放标准。理解"工具契约 + 传输层解耦"这件事，比记某个框架的 API 有用得多 |
| 🟢 **[Twelve-Factor App](https://12factor.net/)** | 第 24 章配置管理那一节的理论来源。**环境变量优先、配置与代码分离** —— 二十年前的原则，今天一样管用 |
| 🟢 **[OWASP Top 10 for LLM Applications](https://genai.owasp.org/llm-top-10/)** | 把"提示词注入、过度授权、敏感信息泄漏"等风险系统化列出。做安全评审时直接当检查表用 |

> 🔥 阅读顺序建议：先读第一份。**它会让你少写很多不必要的 Agent。**

---

## 1. Agent 工程化（对应第 18–24 章）

| 资源 | 对应章节 | 怎么用 |
|---|---|---|
| 🟢 [OpenAI 结构化输出 / Function Calling 文档](https://platform.openai.com/docs/guides/function-calling) | 20 | 看"工具定义长什么样"的官方范本。★ 注意 **strict 模式与 JSON Schema 的约束**，与第 20 章的契约自检清单对照 |
| 🟢 [Pydantic 文档](https://docs.pydantic.dev/) | 20 / 24 | 接口校验与配置管理的事实标准。本仓库 `clinic/config.py` 的接口就是照它的 `BaseSettings` 设计的 |
| 🟢 [LangGraph 文档](https://langchain-ai.github.io/langgraph/) | 19 | 把 Agent 表达成**图/状态机**的主流实现。★ 重点看"状态、中断、恢复"三节 —— 这正是第 19 章 DAG 与断点恢复要解决的问题 |
| 🔵 [OpenTelemetry · GenAI 语义约定](https://opentelemetry.io/docs/specs/semconv/gen-ai/) | 24 | 想把 `request_id`、token、工具调用做成**跨系统标准遥测**时看它 |
| 🔵 [OpenTelemetry Python](https://opentelemetry-python.readthedocs.io/) | 24 | 从"进程内指标"升级到"可查询的集中监控"时看 |
| 🔵 [Redis 文档](https://redis.io/docs/latest/) | 24 | 长任务状态为什么不能放内存字典（第 24.2 节）。看它的 TTL 与持久化策略 |

---

## 2. 评估与测试（最容易被跳过的一步）

| 资源 | 怎么用 |
|---|---|
| 🟢 [promptfoo](https://www.promptfoo.dev/docs/) | 把评估集接进 CI 的现成工具，支持"断言 + 对比不同版本"。**本仓库第 24 章评估集的工业版** |
| 🔵 [Ragas](https://docs.ragas.io/) | 检索增强（RAG）场景的专项指标。★ 注意它有些指标依赖 LLM 裁判 —— 回到第 24.5 节的取舍：**能用断言的用断言** |
| 🔵 [DeepEval](https://docs.confident-ai.com/) | 另一套 LLM 评估框架，指标覆盖更广 |
| 🔵 [pytest 文档](https://docs.pytest.org/) | 领域层的单元测试（第 17 章）。**Agent 的评估集不能替代工具函数的单元测试**，两者都要有 |

> 💡 一个务实的顺序：**先用纯断言把评估集跑起来**（哪怕只有 5 个用例），
> 再考虑引入框架。反过来做，通常会在"选框架"上耗掉全部热情。

---

## 3. 临床 / 受监管场景的 AI

这一块**变化最快**，也**最不能靠二手信息**。请直接看官方原文。

| 资源 | 说明 |
|---|---|
| 🟢 [FDA · 药品与生物制品开发中 AI 的使用](https://www.fda.gov/science-research/science-and-research-special-topics/artificial-intelligence-drug-development) | FDA 关于 AI 用于监管决策的官方页面与指南入口。**以"可信度框架"（context of use + risk-based credibility assessment）为核心思路** |
| 🟢 [CDISC 官网](https://www.cdisc.org/) | 关注其在 AI / 机器学习方向的倡议与白皮书。**标准怎么定，决定工具怎么写** |
| 🟢 [PHUSE 工作组](https://phuse.global/) | 找"行业共识"最实用的地方。看 **AI/ML、Data Transparency、自动化** 相关工作组的最新交付物 |
| 🔵 [NIST AI Risk Management Framework](https://www.nist.gov/itl/ai-risk-management-framework) | 风险管理的通用框架。做内部风险评估文档时的参考骨架 |
| 🔵 [EU AI Act 官方页面](https://digital-strategy.ec.europa.eu/en/policies/regulatory-framework-ai) | 若涉及欧洲递交，需了解其风险分级思路 |

> ⚠️ **务必记住的边界**（与本项目 README 的合规提醒一致）：
> 这些材料告诉你"监管在想什么"，**不构成合规建议**。
> 真正落地要看你项目的 SAP、公司 SOP 与所在辖区的最新要求。
>
> 而且最重要的那条不会写在任何指南里：
> **无论自动化到什么程度，最终对递交物负责的仍然是人。**

---

## 4. 参照实现：看别人怎么写生产级 Agent

| 资源 | 怎么用 |
|---|---|
| 🔵 [CDISC CORE（规则引擎）](https://github.com/cdisc-org/cdisc-rules-engine) | 官方开源的一致性规则引擎（Python）。★ **看它的规则集怎么组织** —— 这是"把 SOP 变成可执行代码"的现实范例 |
| 🔵 [phuse-org/OSTCDA](https://github.com/phuse-org/OSTCDA) | SAS / R / Python 实现同一张表并比对。**"双编程"思路的行业实现**（对应第 22 章） |
| 🔵 [atorus-research](https://github.com/atorus-research) | 多个生产级临床统计 R/Python 包。看它们的**测试组织方式**与**文档结构** |
| 🔵 [phuse-org/phuse-scripts](https://github.com/phuse-org/phuse-scripts) | 大量可直接读的临床数据处理脚本 |

---

## 5. 一句话总结这一份资料

> **Agent 的难点不在模型，在工程。**
>
> 你在第 18–24 章学到的那些东西 ——
> 分层与契约、预算与收尾、状态与恢复、独立性与分级、
> 超时与水位线、配置自检与评估集 ——
> 没有一条是"提示词技巧"，但每一条都决定它能不能被**放心使用**。
>
> 上面这些外部资源的用处，是帮你把这些原则**放回行业语境里**，
> 看看成熟的团队是怎么落实的。

---

## 与本仓库的对应关系

| 章 | 主题 | 本文件中的对应资源 |
|---|---|---|
| [18](../docs/18-Agent架构设计.md) | Agent 架构设计 | 0. 四份必读 |
| [19](../docs/19-任务规划与调度.md) | 任务规划与调度 | 1. LangGraph（状态 / 中断 / 恢复） |
| [20](../docs/20-工具调用进阶.md) | 工具调用进阶 | 1. Function Calling / Pydantic |
| [21](../docs/21-记忆与上下文管理.md) | 记忆与上下文管理 | 2. Ragas（注意 LLM 裁判的取舍） |
| [22](../docs/22-多Agent协作.md) | 多 Agent 协作 | 4. OSTCDA 的双编程实现 |
| [23](../docs/23-外部API与服务集成.md) | 外部 API 与服务集成 | 0. MCP / OWASP LLM Top 10 |
| [24](../docs/24-部署与监控.md) | 部署与监控 | 1. OTel GenAI / Redis；2. promptfoo |
| 合规 | 受监管场景 | 3. FDA / CDISC / PHUSE |

---

**返回** → [资料索引总览](README.md) · [项目主页](../README.md)
