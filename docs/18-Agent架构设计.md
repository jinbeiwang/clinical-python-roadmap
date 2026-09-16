# 第 18 章 · Agent 架构设计：从 200 行循环到可维护的系统

> **本章目标**：把第 16 章那个"能跑"的 while 循环，拆成在真实项目里能维护、
> 能审计、能扩展的分层架构 —— 同时明确**什么时候根本不该用 Agent**。
> **配套模块**：[`clinic/agent_core.py`](../clinic/agent_core.py)
> **配套案例**：[`cases/case08_分层架构QC_Agent.py`](../cases/case08_分层架构QC_Agent.py)

---

## 18.1 先承认：第 16 章那个循环有四块天花板

第 16 章的 `run_agent()` 只有几十行，教学上够用。但把它放进一个要在
真实项目里跑三年的工具，会撞到四堵墙：

| 天花板 | 现象 | 在临床场景里的后果 |
|---|---|---|
| **状态在内存里** | 所有对话都在 `messages` 列表里，进程一断全没 | 跑了 20 步的核查任务，网络抖一下就得重来 |
| **工具没有契约** | 工具是普通函数，参数靠 LLM"猜对" | LLM 传了 `USUBJID="01-701-1015"` 而工具期待列表 → 报错堆栈直接抛给模型 |
| **副作用不可分级** | 读数据的工具和写文件的工具长得一样 | LLM 可能自己决定覆盖一份输出文件 |
| **过程不可重放** | 只留下最终答案 | 监管问"这个结论怎么来的"，你只有一段自然语言 |

这四件事**都不是"提示词写得不够好"能解决的**。它们必须在架构层面处理。

---

## 18.2 五种主流架构模式

在动手拆之前，先看清有哪些成熟套路。它们不是互斥的，真实系统通常是组合。

| 模式 | 控制流 | 适合什么 | 代价 |
|---|---|---|---|
| **ReAct** | 想一步 → 做一步 → 看结果 → 再想 | 探索型任务，路径未知 | 步数不可预测，可能绕圈子 |
| **Plan-and-Execute** | 先出完整计划 → 再逐步执行 | 步骤可枚举、要求可复现 | 计划错则全错，需要重规划机制 |
| **Reflection** | 产出 → 自我批评 → 修订 | 写报告、写代码、找自己的错 | token 翻倍，可能"越改越差" |
| **Router** | 先分类 → 分派给专门的处理器 | 入口杂、子任务差异大 | 分类错了后面全错 |
| **Graph / 状态机** | 节点 + 边 + 条件跳转，**流程写死在代码里** | 受监管、要求可审计 | 灵活性最低，但确定性最高 |

```text
ReAct              Plan-Execute          Graph（显式状态机）

  ┌───┐              ┌──────┐              ┌──────┐
  │想 │              │ 规划 │              │ 读取 │
  └─┬─┘              └──┬───┘              └──┬───┘
    ▼                   ▼                     ▼
  ┌───┐              ┌──────┐              ┌──────┐
  │做 │              │步骤 1│──────────────▶│ 校验 │──┐ 不通过
  └─┬─┘              └──┬───┘              └──┬───┘◀─┘
    ▼                   ▼                     ▼
  ┌───┐              ┌──────┐              ┌──────┐
  │看 │──┐           │步骤 2│              │ 汇总 │
  └───┘  │           └──┬───┘              └──────┘
         │              ▼
         └──再想      ┌──────┐
                     │ 汇总 │
                     └──────┘
  自由度最高          自由度中等           自由度最低
  可复现性最低        可复现性中等         可复现性最高
```

> 🔥 **给临床程序员的判断法则**：把"可复现性"当成一个要买的属性。
> 越靠近**监管递交物**（SDTM/ADaM 数据集、TLF、define.xml），
> 越应该往 Graph/显式流程靠；越靠近**探索与辅助**（数据体检、代码迁移、
> 文档检索），越可以放开用 ReAct。
>
> 一句话：**Agent 只应该用在"步骤事先说不清"的地方。**

### 用 SAS 类比

| SAS 里的东西 | 对应的 Agent 架构 |
|---|---|
| 一段写死的 `DATA` 步 | Graph / 显式流程 —— 确定性，可复现 |
| `%MACRO` 带参数，内部还是固定步骤 | Plan-and-Execute —— 结构固定，参数变 |
| 一个交互式的 EG 会话，分析师边看边改 | ReAct —— 路径由人现场决定 |
| 先跑一遍看结果，再回头改程序 | Reflection |

你绝不会把递交用的 ADaM 程序写成"每次跑的形状都不太一样"。
Agent 的架构选择，遵循的是同一个直觉。

---

## 18.3 分层架构：让 LLM 只负责"决策"这一件事

这是本章最重要的一句话：

> **确定性的部分用代码写死，不确定性的部分才交给 LLM。**
> LLM 的职责范围只有一个 —— **决定下一步做什么**。除此之外全是普通 Python。

```text
┌─────────────────────────────────────────────────────────────┐
│  ①  编排层 Orchestration                                     │
│     · 主循环：想 → 调用 → 观察 → 再想                        │
│     · 状态外置：AgentState 可序列化 / 可恢复 / 可重放         │
│     · 预算控制：步数 / token / 时间三个硬闸门                 │
│     · 人工确认点（HITL）                                     │
└───────────────────────────┬─────────────────────────────────┘
                            │ 只用 ToolSpec 定义的契约说话
┌───────────────────────────▼─────────────────────────────────┐
│  ②  工具层 Tools                                            │
│     · ToolSpec：名字 + 描述 + JSON Schema + 副作用等级        │
│     · ToolRegistry：注册、查找、统一执行、统一记日志          │
│     · 参数校验 / 权限校验 / 超时 / 错误分类                   │
└───────────────────────────┬─────────────────────────────────┘
                            │ 调用普通函数
┌───────────────────────────▼─────────────────────────────────┐
│  ③  领域层 Domain（复用第 1–15 章的成果）                    │
│     clinic.io / derive / report / qc  ——  纯 pandas，无 LLM  │
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│  ④  基础设施层 Infrastructure                                │
│     LLM 客户端（真实 / Mock）· 日志追踪 · 用量统计 · 存储      │
└─────────────────────────────────────────────────────────────┘
```

四层的**依赖方向是单向的**：上层依赖下层，下层**永远不知道上面有 LLM**。

这一条纪律带来三个直接好处：

1. **领域层可以单独测试** —— `clinic/qc.py` 不需要任何 API Key 就能跑单元测试
2. **换模型不用改业务代码** —— 换成国产模型、本地模型、Mock，只动第 ④ 层
3. **Mock 是架构的自然产物，不是补丁** —— 第 ② 层的工具本来就是纯函数

> 🔥 **检验架构好坏的一个土办法**：把 LLM 客户端换成"总是返回固定答案的假货"，
> 你的系统还能不能跑完整个流程？
> 能跑完 —— 说明确定性部分真的独立了。
> 跑不完 —— 说明 LLM 被塞进了不该去的位置。

---

## 18.4 状态外置：让 Agent 可中断、可恢复、可重放

### 反例：状态藏在闭包里

第 16 章的写法是这样：

```python
def run_agent(client, max_steps=25):
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for i in range(max_steps):
        reply = client.chat(messages, TOOLS)      # ← 状态只活在这个函数里
        ...
    return report, messages
```

进程一断，`messages` 就没了。更麻烦的是：**你无法回答"第 7 步当时看到的是什么"**。

### 正例：状态是一个可序列化的对象

```python
# clinic/agent_core.py（节选）
@dataclass
class AgentState:
    """Agent 的全部可变状态。刻意做成可 JSON 序列化的普通数据。"""
    goal: str
    step_index: int = 0
    status: str = "running"          # running / waiting_confirm / done / failed
    steps: list[Step] = field(default_factory=list)
    messages: list[Message] = field(default_factory=list)
    artifacts: dict[str, Any] = field(default_factory=dict)   # 中间产物
    budget: Budget = field(default_factory=Budget)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, default=str)

    @classmethod
    def from_json(cls, s: str) -> "AgentState":
        ...
```

有了它，三个能力顺手就有了：

```python
# 1) 断点恢复
state = AgentState.from_json(open("checkpoint.json").read())
agent.run(goal, state=state)          # 从第 8 步继续

# 2) 事后复盘（监管最关心这个）
for s in state.steps:
    print(s.index, s.kind, s.tool, s.args, "→", s.result)

# 3) 回归测试：把线上真实跑过的状态当固定用例
def test_replay():
    state = AgentState.from_json(FIXTURE.read_text())
    assert replay(state)["结论"] == EXPECTED
```

> ⚠️ **踩坑提醒**：`json.dumps` 遇到 `Timestamp` / `numpy.int64` / `DataFrame`
> 会直接抛 `TypeError`。所以上面的 `to_json` 里加了 `default=str`。
> 更稳妥的做法是在**写入状态前**就把值转成基本类型（这正是工具层该做的事，
> 见第 20 章"工具结果必须可序列化"）。

`Step` 的字段设计也值得说一句 —— **它决定了你的审计日志长什么样**：

```python
@dataclass
class Step:
    index: int
    kind: str                        # think / tool / answer / error
    tool: str | None = None
    args: dict | None = None
    result: str | None = None
    error: str | None = None
    elapsed: float = 0.0
    tokens: int = 0
```

在临床环境里，`args` 和 `result` 就是你的**稽查追踪（audit trail）**。
监管要的不是"模型说它检查过了"，而是"它在 14:32:07 调用了 `run_qc_checks`，
参数是 `{"dataset": "adsl", "checks": ["required_vars"]}`，
返回了 3 条中度问题"。这两者是完全不同量级的东西。

---

## 18.5 副作用分级：把"能做什么"写进工具契约

Agent 出事的方式和实习生出事的方式一模一样：**不是态度问题，是权限问题**。

所以工具的第一个属性不是"功能"，而是"危险程度"：

| 等级 | 含义 | 例子 | 策略 |
|---|---|---|---|
| `read` | 只读，无副作用 | `describe_dataset`、`run_qc_checks` | 可自由调用 |
| `write` | 写文件 / 写数据库，可覆盖 | `write_report`、`write_xpt` | 需确认或限制目录 |
| `irreversible` | 不可撤销（发邮件、提交、删除） | 推送数据、删除数据集 | **必须人工确认** |

```python
@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict                    # JSON Schema
    func: Callable[[dict], ToolResult]
    side_effect: str = "read"           # read / write / irreversible
    timeout: float = 30.0
    tags: tuple[str, ...] = ()          # 便于做策略匹配，如 ("clinical", "qc")
```

执行时统一拦截，**而不是靠提示词哀求模型别乱来**：

```python
def execute(self, name: str, args: dict, policy: Policy, state: AgentState) -> ToolResult:
    spec = self.registry.get(name)
    if spec is None:
        return ToolResult.fail(f"未知工具 {name}", error_type="validation")
    if spec.side_effect != "read" and policy.confirm_side_effects:
        state.status = "waiting_confirm"          # ← 交还控制权给人类
        return ToolResult.pending(f"{name} 需要人工确认：{args}")
    ...
```

> 🔥 **第 16 章那条"安全边界必须在代码里，不能靠提示词"，
> 在架构上的落地形式就是这个 `side_effect` 字段。**
> 提示词是"建议"，`if spec.side_effect != "read"` 是"物理隔离"。

再配一条白名单（第 16 章已出现，这里升级为策略对象）：

```python
@dataclass
class Policy:
    max_steps: int = 25
    max_tokens: int = 120_000
    deadline_sec: float = 300.0
    tool_timeout: float = 30.0
    confirm_side_effects: bool = True
    allowed_datasets: frozenset[str] = frozenset({"dm", "adsl", "adae", "ae", "vs", "adlbc"})
```

---

## 18.6 三个硬闸门：步数、token、时间

Agent 最常见的两种死法：**绕圈子**（同样的信息反复查）和**烧钱**（一轮对话几万 token）。

三个闸门缺一不可，因为它们的失败模式不同：

| 闸门 | 拦住什么 | 不设的后果 |
|---|---|---|
| `max_steps` | 逻辑死循环 | 循环到 API 额度耗尽 |
| `max_tokens` | 成本失控（上下文越滚越大） | 一次任务烧掉几十元 |
| `deadline_sec` | 单个工具卡死 | 任务挂着不返回，用户以为崩了 |

```python
@dataclass
class Budget:
    max_steps: int = 25
    max_tokens: int = 120_000
    deadline_sec: float = 300.0
    steps_used: int = 0
    tokens_used: int = 0
    started_at: float = field(default_factory=time.time)

    def exceeded(self) -> str | None:
        """返回超限原因，None 表示还正常。"""
        if self.steps_used >= self.max_steps:
            return f"步数超限（{self.steps_used}/{self.max_steps}）"
        if self.tokens_used >= self.max_tokens:
            return f"token 超限（{self.tokens_used}/{self.max_tokens}）"
        if time.time() - self.started_at > self.deadline_sec:
            return f"时间超限（{time.time() - self.started_at:.0f}s）"
        return None
```

> ⚠️ **注意"超限"不等于"失败"**。超限时应该返回**已经拿到的部分结论**，
> 并明确标注"因预算用尽而提前结束"。直接抛异常会让用户丢掉前面 20 步的成果 ——
> 这在实际使用中非常恼人。

---

## 18.7 决策树：这个任务到底该不该用 Agent

```text
                    需要处理一个临床数据任务
                              │
                 步骤能不能事先完整写下来？
                    │                     │
                   能                     不能
                    │                     │
             写普通脚本/流水线        结果需要 100% 可复现吗？
             （不要用 Agent）         │              │
                                    需要            不需要
                                     │              │
                              Graph/状态机    任务是"取信息"还是"做判断"？
                              + LLM 只做       │            │
                                局部辅助      取信息        做判断
                                              │            │
                                        RAG/检索       ReAct Agent
                                        （不是 Agent）  （可以放开）
```

把这个决策树翻译成临床场景的实例：

| 任务 | 选择 | 理由 |
|---|---|---|
| 生成 ADaM 数据集 | **纯脚本** | 步骤完全确定，可复现性是硬要求 |
| 生成 TLF | **纯脚本 + 参数** | 上面那条的推论 |
| 批量数据体检（几十个域） | **脚本 + LLM 解读报告** | 检查项确定，结论表述可以交给 LLM |
| "这批数据有什么异常？" | **ReAct Agent** | 异常类型事先说不清 |
| "从 SAP 里找出这条规则" | **RAG 检索** | 是取信息，不是做决策 |
| SDTM 映射（CRF 字段 → SDTM 变量） | **LLM 建议 + 人工确认** | 涉及判断，但必须留痕 |
| 递交前的 200 项自动检查 | **脚本 + 显式清单** | 检查项必须可枚举、可签字 |
| 代码审查（SAS→Python 迁移） | **Reflection** | 产出 + 自我批评，天然适合 |

> 🔥 **一句话总结**：**Agent 是把"说不清的步骤"变成"可执行的探索"，
> 而不是把"说得清的步骤"变得更花哨。**

---

## 18.8 动手：把 case07 重构成分层架构

重构前后对比：

```text
case07（教学版，单文件）               case08（工程版，分层）

case07_临床数据QC_Agent.py            clinic/agent_core.py
├── SYSTEM_PROMPT                     ├── Message / ToolCall / Step
├── MockLLMClient                     ├── ToolSpec / ToolRegistry / ToolResult
├── OpenAIClient                      ├── AgentState / Budget（可序列化）
├── TOOLS（JSON Schema）              ├── Policy（安全边界）
├── TOOL_IMPL（函数字典）             └── Agent（主循环）
├── execute_tool()                    
├── run_agent()  ← 一个 80 行大函数   clinic/agent_tools.py
└── main()                            └── build_registry() → ToolRegistry

                                      cases/case08_分层架构QC_Agent.py
                                      ├── 组装：注册工具 + 选策略 + 选客户端
                                      ├── 演示：正常跑完一个核查任务
                                      ├── 演示：断点保存 → 恢复 → 继续
                                      ├── 演示：命中"需人工确认"的写操作
                                      └── 演示：token 预算耗尽时的优雅收尾
```

重构后的主循环长这样 —— 注意它有多"薄"：

```python
# clinic/agent_core.py 中 Agent.run() 的骨架
def run(self, goal: str, state: AgentState | None = None) -> AgentState:
    state = state or AgentState(goal=goal, budget=Budget(**asdict(self.policy.budget)))
    state.messages.insert(0, Message.system(self.system_prompt))

    while True:
        if (why := state.budget.exceeded()):
            return self._finish(state, f"因预算限制提前结束：{why}")

        reply = self.llm.chat(state.messages, self.registry.schemas())
        state.budget.tokens_used += reply.tokens
        state.messages.append(reply)

        if not reply.tool_calls:                       # 模型给出最终答案
            return self._finish(state, reply.content)

        for call in reply.tool_calls:
            result = self.registry.execute(call.name, call.arguments,
                                           self.policy, state)
            if state.status == "waiting_confirm":       # 交还控制权
                return state
            state.messages.append(Message.tool(call.id, result.to_llm_text()))
            state.steps.append(Step(...))
            state.budget.steps_used += 1
```

**主循环里没有一行业务逻辑** —— 业务全在工具里，安全全在策略里，
可观测性全在 Observer 里。这就是分层架构的样子。

运行：

```bash
python cases/case08_分层架构QC_Agent.py                 # 离线 Mock，直接跑通
python cases/case08_分层架构QC_Agent.py --resume out.json   # 演示断点恢复
```

---

## 18.9 常见误区

| 误区 | 为什么错 | 正确做法 |
|---|---|---|
| "把工具拆得越细越好" | LLM 要在 40 个工具里选，选择错误率飙升 | 一个工具=一个**业务动作**，控制在 5–15 个 |
| "把 System Prompt 写长一点就能约束住" | 提示词是概率性的，压力/诱导/幻觉下会失效 | 边界写成 `if`，不写成祈使句 |
| "加个 `try/except` 就够了" | 错误信息直接喂给模型，它会"编一个"参数再试 | 结构化错误（第 20 章），明确告诉它怎么改 |
| "让它自己决定要不要重试" | 幂等性没保证，可能重复写 | 工具层标记 `retryable`，由框架决定 |
| "多 Agent 一定比单 Agent 强" | 通信开销与冲突成本常常超过收益 | 见第 22 章：先证明单 Agent 不够用 |
| "先跑起来，日志以后再加" | 出问题时你连"它当时看到了什么"都不知道 | `Step` 从第一天就记全 |

---

## 18.10 本章小结

1. **架构的核心是把确定性从不确定性里剥出来** —— LLM 只决定"下一步做什么"
2. **状态必须外置** —— 可序列化才能断点恢复、事后复盘、回归测试
3. **副作用必须分级** —— `read / write / irreversible`，用代码而不是提示词拦截
4. **预算必须有三道闸门** —— 步数、token、时间，且超限要优雅收尾
5. **能用脚本写的，不要用 Agent** —— 这是最重要的一条

**下一章**：Agent 拿到目标后，怎么把它拆成可执行、可并行、可重试的任务图。

---

## 18.11 动手练习

1. 打开 `cases/case08_分层架构QC_Agent.py`，把 `Policy.max_steps` 改成 3，
   观察 Agent 是被"截断"还是"优雅收尾"。改 `Budget` 的实现，让它返回部分结论。
2. 给 `ToolRegistry.execute()` 加一条规则：某个工具在**同一轮任务里被调用超过 3 次**
   时，返回结构化错误提示模型"换个思路"。（提示：在 `AgentState` 里记调用计数）
3. 把 `clinic/agent_core.py` 里的 `Observer` 换成写 JSONL 文件的实现，
   让每次工具调用落一行到 `outputs/audit/trace.jsonl`，字段自定但**必须包含时间戳、
   工具名、参数、结果摘要、耗时**。
4. **思考题**：如果监管要求"每次运行的结论必须完全一致"，
   你会如何改造 `case08`？（提示：温度参数、Mock 重放、缓存）
