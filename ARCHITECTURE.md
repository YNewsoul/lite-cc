# litecc 项目整体架构说明

## 1. 项目定位

`litecc` 是一个运行在终端中的 AI Coding Assistant。它不是单纯的聊天壳，而是一个带有：

- 多模型统一适配层
- 工具调用执行链路
- 会话持久化与长期记忆
- 技能系统与多智能体扩展
- 上下文压缩与安全权限控制

的完整本地代理框架。

从职责上看，它更像一个“可扩展的本地 AI 代理运行时”，而不是只封装单个模型 SDK 的小工具。

---

## 2. 顶层目录结构

| 路径 | 作用 |
|---|---|
| `litecc.py` | 主入口，负责 REPL 启动、命令调度、会话主循环和交互流程 |
| `agent.py` | Agent 核心循环，负责调用模型、处理流式事件、执行工具、写回消息历史 |
| `providers.py` | 多模型统一适配层，屏蔽 Anthropic 与 OpenAI-compatible 协议差异 |
| `context.py` | 系统提示词构建器，注入环境、Git、`CLAUDE.md`、记忆索引等上下文 |
| `config.py` | 全局配置、默认值、配置文件和项目级 secrets 加载 |
| `tools.py` | 内置工具 schema 与实现注册入口 |
| `tool_registry.py` | 统一工具注册中心、schema 选择、执行分发、结果治理 |
| `compaction.py` | 长对话上下文治理：渐进式压缩、读时折叠、摘要压缩 |
| `litecc_sessions.py` | 会话保存、恢复、历史聚合 |
| `litecc_commands.py` | 斜杠命令分发与命令表 |
| `litecc_ui.py` | 终端展示、差异输出、spinner、交互展示 |
| `litecc_background.py` | 会话结束后的后台任务触发器 |
| `plan_mode.py` | 计划模式限制层，负责只读分析阶段的写入约束 |
| `memory/` | 长期记忆系统：存储、检索、自动提取、整合 |
| `skill/` | Skill 系统：Markdown prompt 模板加载、执行、注册 |
| `multi_agent/` | 多智能体子任务派发、子 agent 工具注册 |
| `mcp/` | MCP 服务端接入、工具映射与动态注册 |
| `task/` | 任务系统：任务持久化、工具化管理 |
| `security/` | Bash 风险分级与安全判断 |
| `hooks/` | 钩子系统，用于 pre-tool / post-tool / notification 等外部扩展 |
| `eval/` | 评测脚本与评测流程 |
| `tests/` | 单测与端到端测试 |

---

## 3. 整体运行主链路

项目的主执行路径可以概括为：

```text
用户输入
  -> litecc.py
  -> context.py 构建 system prompt
  -> agent.py 进入模型/工具循环
  -> providers.py 调用具体模型并统一流式输出
  -> tool_registry.py / tools.py 执行工具
  -> state.messages 回写历史
  -> compaction.py 管理长上下文
  -> litecc_sessions.py / memory/ 做落盘与长期沉淀
```

如果展开成更细一点的运行时顺序，大致如下：

1. `litecc.py`
   读取配置、初始化状态、进入 REPL 或 `--print` 单次执行模式。
2. `context.py`
   按当前目录动态构建系统提示词，注入环境、Git、`CLAUDE.md`、记忆索引、计划模式说明等。
3. `agent.py`
   进入核心 loop，向 provider 发请求，消费流式事件，判断是否要继续调用工具。
4. `providers.py`
   根据模型名识别 provider，完成协议转换，统一输出 `TextChunk / ThinkingChunk / Response`。
5. `tool_registry.py`
   根据模型返回的 `tool_calls` 查找工具定义，做权限检查、schema 校验并执行。
6. `agent.py`
   把 assistant 消息、工具调用和工具结果写回 `state.messages`，形成下一轮上下文。
7. `compaction.py`
   在消息过长时裁剪、折叠、摘要，避免上下文窗口溢出。
8. `litecc_sessions.py` 与 `memory/`
   会话结束时保存历史，并异步触发长期记忆提取与整合。

---

## 4. 核心架构分层

### 4.1 交互与入口层

这一层负责“用户如何与系统对话”。

主要模块：

- `litecc.py`
- `litecc_ui.py`
- `litecc_commands.py`
- `litecc_sessions.py`

职责：

- 终端启动与参数解析
- REPL 输入输出
- 斜杠命令处理
- 会话保存、加载、恢复
- 权限确认和交互式审批

这一层更偏“产品壳”和“控制台体验”，不直接处理模型协议细节。

### 4.2 上下文构建层

这一层负责“调用模型之前应该给它什么上下文”。

主要模块：

- `context.py`
- `memory/context.py`
- `plan_mode.py`

职责：

- 构建 system prompt
- 注入当前工作目录、日期、平台、Git 状态
- 读取全局与项目级 `CLAUDE.md`
- 注入记忆索引与动态检索记忆
- 在计划模式下附加限制说明

这一层决定了模型“看到什么背景”，是模型行为质量的关键。

### 4.3 Agent 执行层

这一层是整个系统的中枢。

主要模块：

- `agent.py`

职责：

- 驱动“模型回复 -> 工具调用 -> 工具执行 -> 再次请求模型”的闭环
- 统一接收 provider 的流式事件
- 统一处理权限审批、hook、plan mode 限制
- 把本轮结果写回 `state.messages`

它不关心底层是哪个厂商模型，也不关心工具具体实现在哪个包里，只消费统一抽象。

### 4.4 模型适配层

这一层负责“如何把内部统一消息结构转成具体模型 API 协议”。

主要模块：

- `providers.py`

职责：

- 维护内置 provider 注册表
- 支持用户级与项目级 `models.json` 覆盖
- 识别模型属于哪个 provider
- 对 Anthropic 与 OpenAI-compatible 两类协议做消息转换
- 统一流式输出格式
- 统一工具调用与思考内容解析

这是项目里最核心的“统一多模型适配层”。

### 4.5 工具系统层

这一层负责“模型如何真正读文件、改文件、跑命令、查诊断”。

主要模块：

- `tools.py`
- `tool_registry.py`
- `security/bash_analyzer.py`

职责：

- 定义工具 schema
- 注册内置与扩展工具
- 执行前做 schema 校验与权限校验
- 执行后做结果落盘和上下文治理
- 对 Bash 命令做风险分级

这里是模型和真实文件系统/终端之间的桥梁。

### 4.6 扩展系统层

这一层负责让项目不局限于内置工具。

主要模块：

- `skill/`
- `multi_agent/`
- `mcp/`
- `task/`
- `hooks/`

职责：

- Skill：把 Markdown prompt 模板变成可复用能力
- Multi-agent：把复杂任务拆给子 agent
- MCP：接入外部工具服务器
- Task：管理结构化任务状态
- Hooks：允许外部策略或通知系统插入执行链路

这一层决定了项目的可扩展性。

### 4.7 记忆与持久化层

这一层负责“跨轮次、跨会话的状态延续”。

主要模块：

- `litecc_sessions.py`
- `memory/store.py`
- `memory/retriever.py`
- `memory/auto_extractor.py`
- `memory/dream.py`

职责：

- 保存完整会话历史
- 存储长期记忆文件
- 相关记忆检索
- 会话结束后的自动记忆提取
- 周期性的记忆整合、去重和清理

### 4.8 长上下文治理层

这一层负责“对话太长时怎么不中断工作”。

主要模块：

- `compaction.py`
- `tool_registry.py`

职责：

- 超大工具结果落盘
- 移除更早轮次
- 清空可重建的旧工具结果
- 读时折叠
- 完整 LLM 摘要压缩

---

## 5. 关键模块详解

### 5.1 `litecc.py`

项目主入口，承担的职责很多：

- 启动 REPL
- 处理 CLI 参数
- 调用 `build_system_prompt()`
- 驱动 `run_query()`
- 管理 `/model`、`/config`、`/memory`、`/skills`、`/plan` 等命令
- 会话结束时触发保存和后台记忆任务

可以把它理解为“终端应用层控制器”。

### 5.2 `agent.py`

整个系统的核心调度器。

它主要完成 4 件事：

1. 请求模型
2. 接收流式输出
3. 执行工具
4. 把结果重新写回消息历史

它是“模型”和“工具系统”之间的主循环胶水层。

### 5.3 `providers.py`

项目里最有技术含量的单文件之一。

它负责：

- 识别当前模型的 provider
- 加载内置和本地文件模型配置
- 把统一消息转成厂商协议
- 把流式响应重新归一化
- 把工具调用和 reasoning 内容解析回内部结构

上层只看到统一事件，下层厂商差异都封装在这里。

### 5.4 `tool_registry.py`

这是工具系统的“中央交换机”。

职责包括：

- 保存全部工具定义
- 按当前场景选择可暴露的 tool schema
- 执行前做 JSON Schema 风格参数校验
- 处理工具结果过大时的磁盘卸载
- 记录最近访问文件，给压缩恢复阶段使用

### 5.5 `tools.py`

这里主要放：

- 内置工具 schema
- 内置工具实现绑定
- 扩展工具的导入注册入口

换句话说，`tools.py` 更像“内置工具目录 + 注册启动器”。

### 5.6 `context.py`

负责把系统静态规则和运行时动态信息拼成完整 system prompt。

它会自动注入：

- 当前目录
- 平台和 shell
- Git 状态
- `CLAUDE.md`
- 记忆索引
- 已检索到的完整记忆
- 工具范围限制
- 计划模式说明

### 5.7 `compaction.py`

长对话治理核心。

它实现的是一个五层策略，而不是单次“直接摘要”：

1. 大工具结果落盘
2. 删除早期完整轮次
3. 清理可重建的旧工具结果
4. 读时折叠
5. 结构化摘要压缩

其中“读时折叠”是本项目很重要的一个设计点：只对本次 API 调用生成压缩视图，不改真实历史。

---

## 6. 扩展模块说明

### 6.1 `memory/`

长期记忆系统，分成几个子能力：

- `store.py`：记忆 Markdown 文件存取与索引重建
- `retriever.py`：扫描 header，AI 选择相关记忆，按需加载全文
- `auto_extractor.py`：会话结束后异步提取 durable facts
- `dream.py`：周期性整合、去重、清理旧记忆
- `tools.py`：把记忆能力暴露成工具

### 6.2 `skill/`

Skill 系统用于复用 prompt 模板。

- `loader.py`：把 Markdown skill 解析成 `SkillDef`
- `tools.py`：把 `Skill` / `SkillList` 注册为模型可调用工具
- `builtin.py`：内置 skill
- `executor.py`：执行相关辅助逻辑

### 6.3 `multi_agent/`

多智能体系统用于拆分复杂任务。

- `subagent.py`：子 agent 生命周期与消息隔离
- `tools.py`：Agent / SendMessage / CheckAgentResult 等工具注册

### 6.4 `mcp/`

MCP 集成层用于接入外部能力。

- `config.py`：MCP 配置解析
- `client.py`：连接 MCP server
- `types.py`：MCP 工具/资源类型
- `tools.py`：把 MCP 工具包装成统一 `ToolDef`

### 6.5 `task/`

任务系统用于把多步工作结构化。

- `store.py`：`.litecc/tasks.json` 持久化
- `tools.py`：TaskCreate / Update / Get / List 工具
- `types.py`：任务数据结构

### 6.6 `hooks/`

钩子系统用于插入外部逻辑。

- pre-tool 审批
- post-tool 通知
- 用户提醒与通知派发

这让项目可以接审计、企业策略、外部消息系统。

---

## 7. 数据与文件落盘位置

项目的数据分成“全局级”和“项目级”两类。

### 7.1 全局级

通常位于 `~/.litecc/`：

| 路径 | 作用 |
|---|---|
| `config.json` | 全局默认配置 |
| `models.json` | 用户级模型/provider 扩展 |
| `memory/` | 用户级长期记忆 |
| `skills/` | 用户级 Skill |
| `sessions/` | 会话历史与快照 |
| `mcp.json` | 用户级 MCP 配置 |

### 7.2 项目级

通常位于当前仓库内：

| 路径 | 作用 |
|---|---|
| `CLAUDE.md` | 项目级持久说明文档 |
| `.litecc/models.json` | 项目级模型配置覆盖 |
| `.litecc/secrets.json` | 项目级 API Key / provider secrets |
| `.litecc/memory/` | 项目级长期记忆 |
| `.litecc/skills/` | 项目级 Skill |
| `.litecc/tasks.json` | 当前仓库任务状态 |
| `.mcp.json` | 项目级 MCP 配置 |
| `.nano_claude/plans/` | 计划模式计划文件 |
| `.nano_claude/exports/` | 会话导出文件 |

这意味着 litecc 切换到另一个仓库时，会天然带着“新的仓库上下文”工作。

---

## 8. 四条最关键的运行链路

### 8.1 模型请求链路

```text
litecc.py
  -> context.py 构建 system prompt
  -> agent.py 调用 providers.stream()
  -> providers.py 根据模型名识别 provider
  -> provider 协议转换
  -> 流式返回 TextChunk / ThinkingChunk / Response
```

### 8.2 工具调用链路

```text
模型返回 tool_calls
  -> agent.py 权限判断 / plan mode 限制 / hook
  -> tool_registry.py 执行前 schema 校验
  -> tools.py 或扩展模块中的具体实现
  -> 结果写回 role=tool 消息
  -> 下一轮模型继续消费结果
```

### 8.3 记忆链路

```text
会话进行中
  -> context.py 注入 MEMORY.md 索引
  -> run_query() 后台启动记忆检索
  -> 下一轮注入相关完整记忆

会话结束后
  -> litecc_background.py 触发自动提取
  -> auto_extractor.py 调轻量模型提炼 durable facts
  -> store.py 落盘成 memory markdown 文件
```

### 8.4 上下文治理链路

```text
工具输出过大
  -> tool_registry.py 先落盘

会话过长
  -> compaction.py 先删旧轮次 / 清旧结果
  -> API 调用前 apply_context_collapse()
  -> 仍不够时 compact_messages() 真正摘要压缩
```

---

## 9. 项目的设计亮点

### 9.1 统一多模型适配

不是为每家模型写一套 agent，而是统一抽象内部消息和流式事件，把差异压到 `providers.py`。

### 9.2 工具系统与执行链路清晰

工具被统一抽象为 schema + func + metadata，内置工具、MCP 工具、记忆工具、任务工具都能走同一套注册与执行机制。

### 9.3 会话、记忆、压缩分层明确

项目把：

- 完整会话历史
- 长期记忆
- 临时读时折叠
- 永久摘要压缩

这几件事明确区分开了，边界比较成熟。

### 9.4 项目级扩展能力强

通过 `CLAUDE.md`、`.litecc/skills/`、`.litecc/memory/`、`.mcp.json`，可以让同一套运行时快速适配到不同仓库。

---

## 10. 当前架构上的真实边界

为了使用和维护时更有预期，也需要知道当前的一些边界：

- `litecc.py` 仍然比较重，REPL、命令、主流程还未完全拆干净
- `config` 被用作运行时总线，内部状态字段较多，耦合偏高
- Anthropic 与 OpenAI-compatible 的 reasoning 支持深度不完全一致
- Skill 的 `allowed-tools`、`model` 等字段已建模，但部分约束还未完全落到强执行层
- 动态记忆检索通常是“下一轮生效”，而不是当前轮即时增强

这些不影响它作为本地 AI 代理运行时使用，但属于后续可继续优化的架构点。

---

## 11. 一句话总结

`litecc` 的整体架构可以概括为：

**以 `agent.py` 为调度核心，以 `providers.py` 为统一模型适配层，以 `tool_registry.py + tools.py` 为执行层，以 `memory/ + compaction.py` 为长期上下文管理层，再通过 `skill/`、`multi_agent/`、`mcp/`、`task/` 等扩展模块把能力做成可组合的本地 AI Coding 平台。**
