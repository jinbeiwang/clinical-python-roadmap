# Python 学习资源与工具链

> 只收录**真正有用的**。
> 对临床统计程序员来说，"学 Python"的难点从来不是语法，
> 而是**建立"向量化 + DataFrame"的思维方式**。这一份围绕这个目标组织。

---

## 0. 学习路径（如果你只有 8 周）

| 周 | 目标 | 做什么 |
|---|---|---|
| 1 | 环境能跑 | 装 Python + VS Code，跑通 [Python 官方教程](https://docs.python.org/zh-cn/3/tutorial/)前 6 节 |
| 2 | 语法速通 | 本项目 [第 02 章](../docs/02-基础语法速通-SAS对照.md)，边看边改 `case01` |
| 3 | **思维转换** | 本项目 [第 06 章](../docs/06-NumPy与向量化思维.md)、[第 07 章](../docs/07-pandas入门-DataFrame就是数据集.md)。★ **这是最关键的一周** |
| 4 | 数据操作 | [第 08 章](../docs/08-数据操作对照-DATA步与PROC%20SQL.md)、[第 09 章](../docs/09-合并重塑与分组汇总.md)，把一张老 SAS 程序改写成 pandas |
| 5 | 真实数据 | 跑通 `cases/case01`~`case05`。**每一步都问自己"这对应 SAS 的哪一句"** |
| 6 | 工程化 | Git、虚拟环境、`pytest`（[第 17 章](../docs/17-工程化与后续进阶.md)） |
| 7 | AI 辅助 | [第 15 章](../docs/15-AI辅助编程与代码迁移.md)：用 LLM 迁移代码，但**你必须能判断它写的对不对** |
| 8 | Agent | [第 16 章](../docs/16-Agent开发入门.md) + `cases/case07` |

> 💡 **一周不够就拉长。** 上面每一周的内容，实际做扎实都要 10-20 小时。
> **不要跳过第 3 周** —— 跳过它的人，写出来的代码会是"用 Python 语法的 SAS 程序"，
> 又慢又难维护，然后得出"Python 不如 SAS"的错误结论。

---

## 1. 官方文档（最好的学习材料，没有之一）

| 资源 | 链接 | 怎么用 |
|---|---|---|
| 🟢 **Python 官方教程（中文）** | [docs.python.org/zh-cn/3/tutorial](https://docs.python.org/zh-cn/3/tutorial/) | 通读一遍。中文翻译质量很好 |
| 🟢 **Python 官方文档（英文）** | [docs.python.org/3](https://docs.python.org/3/) | 查标准库。**比任何教程都准** |
| 🟢 **pandas 官方文档** | [pandas.pydata.org/docs](https://pandas.pydata.org/docs/) | ★ 你要反复回来查的地方。特别是 **User Guide** 和 **Cookbook** |
| 🟢 **pandas 十分钟入门** | [pandas.pydata.org/docs/user_guide/10min.html](https://pandas.pydata.org/docs/user_guide/10min.html) | 第一份该读的 pandas 材料 |
| 🟢 **NumPy 广播机制** | [numpy.org/doc/stable/user/basics.broadcasting.html](https://numpy.org/doc/stable/user/basics.broadcasting.html) | ★ **理解向量化的钥匙**。看懂这个，你就不会再写行循环了 |
| 🔵 **pyreadstat 文档** | [pyreadstat.readthedocs.io](https://pyreadstat.readthedocs.io/) | 读 XPT / sas7bdat 的参数说明 |
| 🔵 **openpyxl 文档** | [openpyxl.readthedocs.io](https://openpyxl.readthedocs.io/) | Excel 读写（含格式、公式） |
| 🔵 **SciPy 文档** | [docs.scipy.org](https://docs.scipy.org/) | 统计检验函数 |
| 🔵 **statsmodels 文档** | [statsmodels.org](https://www.statsmodels.org/) | 回归 / Logistic / Mixed Model |
| 🔵 **Matplotlib 文档** | [matplotlib.org](https://matplotlib.org/) | 绘图（对应 `PROC SGPLOT`） |

### pandas 里最该先读的 6 个页面

1. **10 minutes to pandas** —— 全貌
2. **User Guide → Indexing and selecting data** —— `loc` / `iloc` / 布尔索引。**这里不搞清，后面全是错**
3. **User Guide → Group by: split-apply-combine** —— 对应 SAS 的 `BY` 组处理
4. **User Guide → Merge, join, concatenate** —— 对应 `MERGE`
5. **User Guide → Reshaping and pivot tables** —— 对应 `PROC TRANSPOSE` / `PROC TABULATE`
6. **User Guide → Working with missing data** —— ★ 与 SAS 差异最大的一块

---

## 2. 开发环境

| 方案 | 适合谁 | 说明 |
|---|---|---|
| 🟢 **VS Code + Python 扩展** | 大多数人 | 免费、跨平台、Git 集成好。**推荐** |
| 🟢 **PyCharm Community** | 喜欢重 IDE 的人 | 提示与重构更强。JetBrains 系用户上手快 |
| 🔵 **Jupyter Lab / Notebook** | 探索阶段 | 对应 SAS Studio 的交互体验。★ 但**不要把生产程序写成 notebook** |
| 🔵 **Anaconda / Miniconda** | 不想折腾环境的人 | 一次装齐科学计算栈。缺点是体积大 |
| ⚪ **uv** | 追求速度的人 | 极快的 Python 包管理器，近年很流行 |

### 必装的 VS Code 扩展

- **Python**（微软官方）
- **Pylance**（类型提示与补全）
- **Jupyter**（跑 notebook）
- **Rainbow CSV**（看 CSV 时按列着色）
- **GitLens**（看每行代码是谁改的、什么时候改的 —— **审计场景很有用**）
- **Data Wrangler**（微软出的数据探索插件，可视化看 DataFrame）

### 虚拟环境（★ 必须养成习惯）

```bash
# 每个项目一个独立环境 —— 对应 SAS 里"每个项目有自己的库"
python -m venv .venv

# Windows PowerShell
.venv\Scripts\Activate.ps1
# Windows CMD
.venv\Scripts\activate.bat
# Linux / macOS
source .venv/bin/activate

pip install pandas numpy pyreadstat openpyxl

# 把依赖固定下来（对应 SAS 里"记录用了哪个版本的工具"）
pip freeze > requirements.txt
```

> ⚠️ **为什么虚拟环境在临床场景特别重要？**
> 因为**可复现性**。半年后你要复现一个结果，
> 如果依赖版本变了（比如 pandas 2 → 3 的行为变化），结果可能就不一样。
> `requirements.txt` 是审计材料的一部分。

---

## 3. 包管理速查

| 需求 | 命令 |
|---|---|
| 安装 | `pip install pandas` |
| 指定版本 | `pip install "pandas==2.2.3"` |
| 升级 | `pip install --upgrade pandas` |
| 卸载 | `pip uninstall pandas` |
| 看已装什么 | `pip list` |
| 导出依赖 | `pip freeze > requirements.txt` |
| 按依赖恢复 | `pip install -r requirements.txt` |
| 国内加速（临时） | `pip install pandas -i https://pypi.tuna.tsinghua.edu.cn/simple` |
| 国内加速（永久） | `pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple` |

> 💡 本项目的依赖见根目录 [`requirements.txt`](../requirements.txt)。

---

## 4. 临床数据专用工具（本项目用到与推荐的）

| 工具 | 位置 | 作用 |
|---|---|---|
| **clinic.io** | 本项目 [`clinic/io.py`](../clinic/io.py) | 读 XPT/sas7bdat/CSV，保留标签，清洗伪缺失 |
| **clinic.derive** | 本项目 [`clinic/derive.py`](../clinic/derive.py) | SAS 语义的舍入、年龄分组、日期转换 |
| **clinic.report** | 本项目 [`clinic/report.py`](../clinic/report.py) | TFL 报表构造件（`n (%)`、Mean (SD)、移位表） |
| **clinic.qc** | 本项目 [`clinic/qc.py`](../clinic/qc.py) | 数据质量检查、跨域一致性、PROC COMPARE 等价物 |
| **clinic.agent_tools** | 本项目 [`clinic/agent_tools.py`](../clinic/agent_tools.py) | 暴露给 LLM Agent 的临床数据工具集 |
| [pyreadstat](https://github.com/Roche/pyreadstat) | 第三方 | 底层 XPT 读取 |
| [cdisc-rules-engine](https://github.com/cdisc-org/cdisc-rules-engine) | CDISC 官方 | 一致性规则引擎（CORE） |
| [odmlib](https://github.com/swhume/odmlib) | 第三方 | Define-XML / ODM 读写 |
| [duckdb-read-stat](https://github.com/dylanmeysmans/duckdb-read-stat) | 第三方 | 大 SAS 文件的 SQL 直查 |

---

## 5. 通用技能（决定你能走多远）

| 技能 | 为什么临床程序员也该学 |
|---|---|
| 🟢 **Git** | 版本管理是**审计要求**，不是"程序员的爱好"。见 [phuse-org/git-in-statistical-programming](https://github.com/phuse-org/git-in-statistical-programming) |
| 🟢 **日志（logging）** | 对应 `PROC PRINTTO`，但能做级别控制、能接调度系统 |
| 🔵 **测试（pytest）** | 对应"双编程验证"的自动化版本。**把 QC 从人工变成自动** |
| 🔵 **CI/CD（GitHub Actions 等）** | 每次提交自动跑测试与 QC —— 这是质量的真正保障 |
| 🔵 **容器（Docker）** | 环境可复现的终极方案。在"换台机器结果就不一样"的场景下是唯一解法 |
| ⚪ **正则表达式（regex）** | 对应 SAS 的 PRX 函数族。处理 `--DTC`、解析文件名、清洗数据都要用 |
| ⚪ **SQL** | 不只 `PROC SQL`。真实世界数据（RWD）场景几乎全是 SQL |

---

## 6. 免费的公开课程（挑一个就够）

| 课程 | 链接 | 特点 |
|---|---|---|
| **Python 官方教程** | [docs.python.org/zh-cn/3/tutorial](https://docs.python.org/zh-cn/3/tutorial/) | 最准，不要钱 |
| **Kaggle Learn: Python / Pandas** | [kaggle.com/learn](https://www.kaggle.com/learn) | 短、有练习、直接能跑。**pandas 那份特别适合入门** |
| **Python for Data Analysis（书）** | 作者 Wes McKinney 就是 pandas 的作者 | 免费在线版在 [wesmckinney.com/book](https://wesmckinney.com/book/) |
| **Real Python** | [realpython.com](https://realpython.com/) | 文章质量高，覆盖实际场景 |
| **pharmaverse 教学材料** | [pharmaverse.org](https://pharmaverse.org/) | R 为主，但**临床数据处理的思路完全通用** |

> ⚠️ **避坑**：不要一上来学 Django / Flask / 爬虫 / 深度学习。
> 那些和你的工作无关。**你的主线是 pandas + 文件处理 + 一点点统计。**
> 学完主线再去扩展。

---

## 7. 遇到问题怎么办（按顺序）

1. **看报错信息的最后一行**（Python 的报错信息比 SAS 的 log 精确得多，
   它直接告诉你文件和行号）
2. **读官方文档对应的那一页** —— 90% 的问题文档里都写了
3. **搜索引擎**（把报错信息原样贴进去，去掉你自己的变量名）
4. **Stack Overflow** —— 加 `pandas` 标签
5. **SAS Communities / PHUSE** —— 业务口径类问题去这里
6. **问 AI** —— ★ 但**必须自己验证**。
   LLM 在 pandas API 细节上经常记错版本差异（如 `observed` 参数、
   `str.replace` 的 regex 默认值、numpy 2 的类型提升规则）。
   **把 AI 给的答案当成"待验证的假设"，不是"结论"。**

---

## 8. 一个务实的提醒

> 你在 SAS 里积累的**领域知识**（什么是 SAFFL、TEAE 怎么判、
> 移位表看什么、双编程怎么比）—— 那些才是你真正的资产，
> 值钱得多。
>
> Python 只是换了个工具来表达同样的知识。
> **不要因为不熟悉工具，就怀疑自己的专业能力；
> 也不要因为会用工具，就以为自己懂了业务。**
>
> 本项目存在的意义就是：**用你已经懂的业务，把新工具学会。**

---

**返回** → [项目主页](../README.md) ｜ [资料索引总览](README.md)
