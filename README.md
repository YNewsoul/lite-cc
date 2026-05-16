# pycc

**pycc** 是一个轻量、可自由扩展的 Python AI 编程助手，灵感来自 Claude Code。它运行一个带工具调用的流式智能体循环，开箱支持 10+ 个模型厂商，并提供简洁的 REPL，可通过自定义技能（Skill）和 MCP 服务器随意扩展。

```bash
pip install anthropic          # 或 openai，或任意支持的厂商 SDK
export ANTHROPIC_API_KEY=...
python pycc.py                 # 启动 REPL
```

要求 Python ≥ 3.10。

---

## 功能特性

| 功能 | 说明 |
|---|---|
| 多厂商支持 | Anthropic · OpenAI · Gemini · Kimi · Qwen · 智谱 · DeepSeek · MiniMax · 自定义端点 |
| 交互式 REPL | readline 历史记录，Tab 补全斜杠命令及子命令提示；Bracketed Paste 模式支持可靠的多行粘贴 |
| 智能体循环 | 流式 API + 自动工具调用循环 |
| 27 个内置工具 | Read · Write · Edit · Bash · Glob · Grep · WebFetch · WebSearch · **NotebookEdit** · **GetDiagnostics** · MemorySave · MemoryDelete · MemorySearch · MemoryList · Agent · SendMessage · CheckAgentResult · ListAgentTasks · ListAgentTypes · Skill · SkillList · AskUserQuestion · TaskCreate/Update/Get/List · **SleepTimer** · **EnterPlanMode** · **ExitPlanMode** · *（MCP 工具在启动时自动注册）* |
| MCP 集成 | 接入任意 MCP 服务器（stdio/SSE/HTTP），工具自动注册，Claude 可直接调用 |
| AskUserQuestion | Claude 可在任务中途暂停并向用户提问，支持编号选项 |
| 任务管理 | TaskCreate/Update/Get/List 工具；顺序 ID；元数据；持久化至 `.pycc/tasks.json`；`/tasks` REPL 命令 |
| 差异视图 | Edit 和 Write 操作后显示 git 风格的红绿差异 |
| 上下文压缩 | 五层渐进式压缩策略（磁盘卸载→旧轮次移除→可复现工具结果清除→读时投影→LLM摘要），按内容可恢复性决定驱逐优先级；读时投影在 API 调用瞬间生成压缩视图而不修改原始历史 |
| 持久记忆 | 三层管道：每轮后台检索（flash 模型并行选 ≤5 条注入）→ 会话结束自动提取（Layer 2，条件触发）→ AutoDream 周期整合去重（Layer 3，每 5 次会话）；双作用域（user + project），4 种类型，严格代码事实豁免 |
| 多智能体 | 派发类型化子智能体（coder/reviewer/researcher/…），git worktree 隔离，后台模式 |
| 技能系统 | 内置 `/commit` · `/review` + 支持参数替换的自定义 Markdown 技能 |
| 权限系统 | `auto` / `accept-all` / `manual` 三种模式 |
| 计划模式 | 两阶段规划-执行切换；`EnterPlanMode` 激活独立限制层（不修改 `permission_mode`），plan 阶段通过代码层权限拦截强制只读（仅计划文件可写），`ExitPlanMode` 输出方案后暂停等待用户确认，用权限机制而非 prompt 约束保证隔离 |
| 视觉输入 | `/image`（或 `/img`）截取剪贴板图片并发送给任意视觉模型 |
| 强制退出 | 2 秒内连按 3 次 Ctrl+C 触发 `os._exit(1)`，立即终止进程 |
| Rich 流式渲染 | 安装 `rich` 后，响应以实时更新的 Markdown 原地渲染 |
| 上下文注入 | 自动加载 `CLAUDE.md`、git 状态、当前目录、持久记忆 |
| 会话持久化 | 退出时自动保存到 `daily/YYYY-MM-DD/` + `history.json` + `session_latest.json` |
| Extended Thinking | Claude 模型可开/关 |
| 费用追踪 | Token 用量 + 估算 USD 费用 |
| 非交互模式 | `--print` 标志用于脚本/CI |
| SWE-bench 评测 | 完整评测管道（`eval/`）；30 实例子集官方 Docker 评测 70% resolve rate（deepseek-v4-pro，存在数据污染风险，全量待测） |

---

## 支持的模型

### 闭源模型（API）

| 厂商 | 模型 | 上下文 | 优势 | API Key 环境变量 |
|---|---|---|---|---|
| **Anthropic** | `claude-opus-4-6` | 200k | 最强，复杂推理首选 | `ANTHROPIC_API_KEY` |
| **Anthropic** | `claude-sonnet-4-6` | 200k | 速度与质量均衡 | `ANTHROPIC_API_KEY` |
| **Anthropic** | `claude-haiku-4-5-20251001` | 200k | 快速，成本低 | `ANTHROPIC_API_KEY` |
| **OpenAI** | `gpt-4o` | 128k | 多模态与编码能力强 | `OPENAI_API_KEY` |
| **OpenAI** | `gpt-4o-mini` | 128k | 快速，廉价 | `OPENAI_API_KEY` |
| **OpenAI** | `o3-mini` | 200k | 推理能力强 | `OPENAI_API_KEY` |
| **OpenAI** | `o1` | 200k | 高级推理 | `OPENAI_API_KEY` |
| **Google** | `gemini-2.5-pro-preview-03-25` | 1M | 长上下文，多模态 | `GEMINI_API_KEY` |
| **Google** | `gemini-2.0-flash` | 1M | 快速，大上下文 | `GEMINI_API_KEY` |
| **Google** | `gemini-1.5-pro` | 2M | 最大上下文窗口 | `GEMINI_API_KEY` |
| **Moonshot (Kimi)** | `moonshot-v1-8k` | 8k | 中英双语 | `MOONSHOT_API_KEY` |
| **Moonshot (Kimi)** | `moonshot-v1-32k` | 32k | 中英双语 | `MOONSHOT_API_KEY` |
| **Moonshot (Kimi)** | `moonshot-v1-128k` | 128k | 长上下文 | `MOONSHOT_API_KEY` |
| **阿里（Qwen）** | `qwen-max` | 32k | Qwen 最强质量 | `DASHSCOPE_API_KEY` |
| **阿里（Qwen）** | `qwen-plus` | 128k | 均衡 | `DASHSCOPE_API_KEY` |
| **阿里（Qwen）** | `qwen-turbo` | 1M | 快速，廉价 | `DASHSCOPE_API_KEY` |
| **阿里（Qwen）** | `qwq-32b` | 32k | 推理能力强 | `DASHSCOPE_API_KEY` |
| **智谱（GLM）** | `glm-4-plus` | 128k | GLM 最强质量 | `ZHIPU_API_KEY` |
| **智谱（GLM）** | `glm-4` | 128k | 通用 | `ZHIPU_API_KEY` |
| **智谱（GLM）** | `glm-4-flash` | 128k | 有免费额度 | `ZHIPU_API_KEY` |
| **DeepSeek** | `deepseek-chat` | 64k | 编码能力强 | `DEEPSEEK_API_KEY` |
| **DeepSeek** | `deepseek-reasoner` | 64k | 思维链推理 | `DEEPSEEK_API_KEY` |
| **MiniMax** | `MiniMax-Text-01` | 1M | 长上下文，推理强 | `MINIMAX_API_KEY` |
| **MiniMax** | `MiniMax-VL-01` | 1M | 视觉 + 语言 | `MINIMAX_API_KEY` |
| **MiniMax** | `abab6.5s-chat` | 256k | 快速，成本低 | `MINIMAX_API_KEY` |
| **MiniMax** | `abab6.5-chat` | 256k | 质量均衡 | `MINIMAX_API_KEY` |

---

## 安装

### 推荐：使用 `uv` 安装为全局命令

[uv](https://docs.astral.sh/uv/) 将 `pycc` 安装到隔离环境并添加到 PATH，可在任意位置运行：

```bash
# 安装
cd pycc
uv tool install .
```

安装完成后，`pycc` 即为全局命令：

```bash
pycc                        # 启动 REPL
pycc --model gpt-4o         # 选择模型
pycc -p "explain this"      # 非交互模式
```

拉取新代码后更新：

```bash
uv tool install . --reinstall
```

卸载：

```bash
uv tool uninstall pycc
```

### 备选：直接从仓库运行

```bash
git clone https://github.com/SafeRL-Lab/clawspring
cd pycc

pip install -r requirements.txt
# 或手动安装：
pip install anthropic openai httpx rich

python pycc.py
```

---

## 用法：闭源 API 模型

### Anthropic Claude

在 [console.anthropic.com](https://console.anthropic.com) 获取 API Key。

```bash
export ANTHROPIC_API_KEY=sk-ant-api03-...

# 默认模型（claude-opus-4-6）
pycc

# 指定模型
pycc --model claude-sonnet-4-6
pycc --model claude-haiku-4-5-20251001

# 开启 Extended Thinking
pycc --model claude-opus-4-6 --thinking --verbose
```

### OpenAI GPT

在 [platform.openai.com](https://platform.openai.com) 获取 API Key。

```bash
export OPENAI_API_KEY=sk-...

pycc --model gpt-4o
pycc --model gpt-4o-mini
pycc --model gpt-4.1-mini
pycc --model o3-mini
```

### Google Gemini

在 [aistudio.google.com](https://aistudio.google.com) 获取 API Key。

```bash
export GEMINI_API_KEY=AIza...

pycc --model gemini/gemini-2.0-flash
pycc --model gemini/gemini-1.5-pro
pycc --model gemini/gemini-2.5-pro-preview-03-25
```

### Kimi（Moonshot AI）

在 [platform.moonshot.cn](https://platform.moonshot.cn) 获取 API Key。

```bash
export MOONSHOT_API_KEY=sk-...

pycc --model kimi/moonshot-v1-32k
pycc --model kimi/moonshot-v1-128k
```

### Qwen（阿里云 DashScope）

在 [dashscope.aliyun.com](https://dashscope.aliyun.com) 获取 API Key。

```bash
export DASHSCOPE_API_KEY=sk-...

pycc --model qwen/Qwen3.5-Plus
pycc --model qwen/Qwen3-MAX
pycc --model qwen/Qwen3.5-Flash
```

### 智谱 GLM

在 [open.bigmodel.cn](https://open.bigmodel.cn) 获取 API Key。

```bash
export ZHIPU_API_KEY=...

pycc --model zhipu/glm-4-plus
pycc --model zhipu/glm-4-flash   # 免费额度
```

### DeepSeek

在 [platform.deepseek.com](https://platform.deepseek.com) 获取 API Key。

```bash
export DEEPSEEK_API_KEY=sk-...

pycc --model deepseek/deepseek-chat
pycc --model deepseek/deepseek-reasoner
```

### MiniMax

在 [platform.minimaxi.chat](https://platform.minimaxi.chat) 获取 API Key。

```bash
export MINIMAX_API_KEY=...

pycc --model minimax/MiniMax-Text-01
pycc --model minimax/MiniMax-VL-01
pycc --model minimax/abab6.5s-chat
```

---

## 模型名称格式

支持三种等价格式：

```bash
# 1. 按前缀自动检测（适用于知名模型）
pycc --model gpt-4o
pycc --model gemini-2.0-flash
pycc --model deepseek-chat

# 2. 斜杠显式指定厂商前缀
pycc --model deepseek/deepseek-v4-pro
pycc --model kimi/moonshot-v1-128k

# 3. 冒号显式指定厂商前缀（同样有效）
pycc --model kimi:moonshot-v1-32k
pycc --model qwen:qwen-max
```

**自动检测规则：**

| 模型前缀 | 检测到的厂商 |
|---|---|
| `claude-` | anthropic |
| `gpt-`、`o1`、`o3` | openai |
| `gemini-` | gemini |
| `moonshot-`、`kimi-` | kimi |
| `qwen`、`qwq-` | qwen |
| `glm-` | zhipu |
| `deepseek-` | deepseek |
| `MiniMax-`、`minimax-`、`abab` | minimax |

---

## CLI 参考

```
pycc [OPTIONS] [PROMPT]
# 或：python pycc.py [OPTIONS] [PROMPT]

Options:
  -p, --print          非交互模式：运行提示词后退出
  -m, --model MODEL    覆盖模型（如 gpt-4o、deepseek/deepseek-v4-pro）
  --accept-all         自动批准所有操作（无权限提示）
  --verbose            显示思考块和每轮 token 数量
  --thinking           开启 Extended Thinking（仅 Claude）
  --version            打印版本后退出
  -h, --help           显示帮助
```

**示例：**

```bash
# 交互式 REPL，使用默认模型
pycc

# 启动时切换模型
pycc --model gpt-4o
pycc -m deepseek/deepseek-v4-pro

# 非交互 / 脚本
pycc --print "Write a Python fibonacci function"
pycc -p "Explain the Rust borrow checker in 3 sentences" -m gemini/gemini-2.0-flash

# CI / 自动化（无权限提示）
pycc --accept-all --print "Initialize a Python project with pyproject.toml"

# 调试模式（显示 token + 思考过程）
pycc --thinking --verbose
```

---

## 斜杠命令（REPL）

输入 `/` 后按 **Tab** 查看所有命令及说明。继续输入可过滤，再次 Tab 自动补全。命令名后再按 **Tab** 可查看子命令（如 `/mcp ` → `reload`、`add`、`remove`……）。

| 命令 | 说明 |
|---|---|
| `/help` | 显示所有命令 |
| `/clear` | 清除对话历史 |
| `/model` | 显示当前模型 + 列出所有可用模型 |
| `/model <name>` | 切换模型（立即生效） |
| `/config` | 显示所有当前配置项 |
| `/config key=value` | 设置配置项（持久化到磁盘） |
| `/save` | 保存会话（按时间戳自动命名） |
| `/save <filename>` | 保存会话到指定文件名 |
| `/load` | 按日期分组的交互式列表；输入编号、`1,2,3` 合并，或 `H` 查看完整历史 |
| `/load <filename>` | 按文件名加载已保存会话 |
| `/resume` | 恢复最后一次自动保存的会话（`mr_sessions/session_latest.json`） |
| `/resume <filename>` | 从 `mr_sessions/` 加载特定文件（或绝对路径） |
| `/history` | 打印完整对话历史 |
| `/context` | 显示消息数量和 token 估算 |
| `/cost` | 显示 token 用量和估算 USD 费用 |
| `/verbose` | 切换详细模式（token + 思考过程） |
| `/thinking` | 切换 Extended Thinking（仅 Claude） |
| `/permissions` | 显示当前权限模式 |
| `/permissions <mode>` | 设置权限模式：`auto` / `accept-all` / `manual` |
| `/cwd` | 显示当前工作目录 |
| `/cwd <path>` | 更改工作目录 |
| `/memory` | 列出所有持久记忆（按修改时间排序） |
| `/memory <query>` | 按关键词搜索记忆 |
| `/memory consolidate` | 立即触发 Layer 3 整合：去重、合并、清理过时记忆 |
| `/skills` | 列出可用技能 |
| `/agents` | 显示子智能体任务状态 |
| `/mcp` | 列出已配置的 MCP 服务器及其工具 |
| `/mcp reload` | 重连所有 MCP 服务器并刷新工具列表 |
| `/mcp reload <name>` | 重连单个 MCP 服务器 |
| `/mcp add <name> <cmd> [args]` | 向用户配置添加 stdio MCP 服务器 |
| `/mcp remove <name>` | 从用户配置中移除服务器 |
| `/image [prompt]` | 截取剪贴板图片并发送给视觉模型（可附加提示词） |
| `/img [prompt]` | `/image` 的别名 |
| `/plan <description>` | 激活计划限制层：只读分析，仅写入计划文件 |
| `/plan` | 显示当前计划文件内容 |
| `/plan done` | 停用计划限制层 |
| `/plan status` | 显示计划模式状态及基础权限模式 |
| `/compact` | 手动压缩对话（与自动压缩相同，但由用户触发） |
| `/compact <focus>` | 带焦点指令压缩（如 `/compact keep the auth refactor context`） |
| `/init` | 在当前工作目录创建 `CLAUDE.md` 模板 |
| `/export` | 将对话导出为 Markdown 文件到 `.nano_claude/exports/` |
| `/export <filename>` | 导出为 Markdown 或 JSON（根据 `.json` 扩展名判断） |
| `/copy` | 将最后一条助手回复复制到剪贴板 |
| `/status` | 显示版本、模型、厂商、权限、会话 ID、token 用量和上下文占用率 |
| `/doctor` | 诊断安装状态：Python、git、API Key、可选依赖、CLAUDE.md |
| `/exit` / `/quit` | 退出 |

**在会话中切换模型：**

```
[myproject] ❯ /model
  当前模型：deepseek/deepseek-v4-pro  (厂商: deepseek)

  按厂商列出的可用模型：
    anthropic     claude-opus-4-6, claude-sonnet-4-6, ...
    openai        gpt-4o, gpt-4o-mini, o3-mini, ...
    deepseek      deepseek-v4-pro, deepseek-v4-flash, deepseek-chat, ...
    qwen          qwen-max, qwen-plus, ...
    ...

[myproject] ❯ /model gpt-4o
  模型已切换为 gpt-4o  (厂商: openai)

[myproject] ❯ /model qwen/qwen-max
  模型已切换为 qwen/qwen-max  (厂商: qwen)
```

---

## 配置 API Key

### 方法一：环境变量（推荐）

```bash
# 添加到 ~/.bashrc 或 ~/.zshrc
export ANTHROPIC_API_KEY=sk-ant-...
export OPENAI_API_KEY=sk-...
export GEMINI_API_KEY=AIza...
export MOONSHOT_API_KEY=sk-...       # Kimi
export DASHSCOPE_API_KEY=sk-...      # Qwen
export ZHIPU_API_KEY=...             # 智谱 GLM
export DEEPSEEK_API_KEY=sk-...       # DeepSeek
export MINIMAX_API_KEY=...           # MiniMax
```

### 方法二：在 REPL 内设置（持久化）

```
/config anthropic_api_key=sk-ant-...
/config openai_api_key=sk-...
/config gemini_api_key=AIza...
/config kimi_api_key=sk-...
/config qwen_api_key=sk-...
/config zhipu_api_key=...
/config deepseek_api_key=sk-...
/config minimax_api_key=...
```

Key 保存到 `~/.pycc/config.json`，下次启动自动加载。

### 方法三：直接编辑配置文件

```json
// ~/.pycc/config.json
{
  "model": "qwen/qwen-max",
  "max_tokens": 8192,
  "permission_mode": "auto",
  "verbose": false,
  "thinking": false,
  "qwen_api_key": "sk-...",
  "kimi_api_key": "sk-...",
  "deepseek_api_key": "sk-...",
  "minimax_api_key": "..."
}
```

---

## 权限系统

| 模式 | 行为 |
|---|---|
| `auto`（默认）| 只读操作始终允许。Bash 命令和文件写入前提示确认。 |
| `accept-all` | 从不提示，所有操作自动执行。 |
| `manual` | 每个操作前都提示，包括读取操作。 |

> **计划模式**是独立于 `permission_mode` 之外的运行时限制层，通过 `/plan <描述>` 或 `EnterPlanMode` 工具激活，激活后仅计划文件可写，不修改基础权限策略。通过 `/plan done` 或 `ExitPlanMode` 停用。

**被提示时：**

```
  Allow: Run: git commit -am "fix bug"  [y/N/a(ccept-all)]
```

- `y` — 批准本次操作
- `n` 或 Enter — 拒绝
- `a` — 批准并将本次会话切换为 `accept-all`

**在 `auto` 模式下始终自动批准的命令：**
`ls`、`cat`、`head`、`tail`、`wc`、`pwd`、`echo`、`git status`、`git log`、`git diff`、`git show`、`find`、`grep`、`rg`、`python`、`node`、`pip show`、`npm list` 以及其他只读 Shell 命令。

---

## 内置工具

### 核心工具

| 工具 | 说明 | 主要参数 |
|---|---|---|
| `Read` | 带行号读取文件 | `file_path`、`limit`、`offset` |
| `Write` | 创建或覆写文件（显示差异） | `file_path`、`content` |
| `Edit` | 精确字符串替换（显示差异） | `file_path`、`old_string`、`new_string`、`replace_all` |
| `Bash` | 执行 Shell 命令 | `command`、`timeout`（默认 30s） |
| `Glob` | 按 glob 模式查找文件 | `pattern`（如 `**/*.py`）、`path` |
| `Grep` | 正则搜索文件内容（有 ripgrep 则使用） | `pattern`、`path`、`glob`、`output_mode` |
| `WebFetch` | 抓取 URL 并提取文本 | `url`、`prompt` |
| `WebSearch` | 通过 DuckDuckGo 搜索网页 | `query` |

### Notebook 与诊断工具

| 工具 | 说明 | 主要参数 |
|---|---|---|
| `NotebookEdit` | 编辑 Jupyter notebook（`.ipynb`）单元格 | `notebook_path`、`new_source`、`cell_id`、`cell_type`、`edit_mode`（`replace`/`insert`/`delete`） |
| `GetDiagnostics` | 获取源文件的 LSP 风格诊断（Python 用 pyright/mypy/flake8；JS/TS 用 tsc/eslint；Shell 用 shellcheck） | `file_path`、`language`（可选覆盖） |

### 记忆工具

| 工具 | 说明 | 主要参数 |
|---|---|---|
| `MemorySave` | 保存或更新一条持久记忆 | `name`、`type`、`description`、`content`、`scope` |
| `MemoryDelete` | 按名称删除记忆 | `name`、`scope` |
| `MemorySearch` | 按关键词搜索记忆（或 AI 排序） | `query`、`scope`、`use_ai`、`max_results` |
| `MemoryList` | 列出所有记忆及其年龄和元数据 | `scope` |

### 子智能体工具

| 工具 | 说明 | 主要参数 |
|---|---|---|
| `Agent` | 为任务派发子智能体 | `prompt`、`subagent_type`、`isolation`、`name`、`model`、`wait` |
| `SendMessage` | 向后台命名智能体发送消息 | `name`、`message` |
| `CheckAgentResult` | 查询后台智能体的状态/结果 | `task_id` |
| `ListAgentTasks` | 列出所有活跃和已完成的智能体任务 | — |
| `ListAgentTypes` | 列出可用的智能体类型定义 | — |

### 后台与自主工具

| 工具 | 说明 | 主要参数 |
|---|---|---|
| `SleepTimer` | 安排一个无声后台计时器；触发时注入自动唤醒提示，让智能体可以恢复监控或延迟任务 | `seconds` |

### 技能工具

| 工具 | 说明 | 主要参数 |
|---|---|---|
| `Skill` | 在对话中按名称调用技能 | `name`、`args` |
| `SkillList` | 列出所有可用技能及其触发器和元数据 | — |

### MCP 工具

MCP 工具从已配置的服务器自动发现，以 `mcp__<server>__<tool>` 格式注册。Claude 可像内置工具一样调用它们。

| 工具名称示例 | 来源 |
|---|---|
| `mcp__git__git_status` | `git` 服务器的 `git_status` 工具 |
| `mcp__filesystem__read_file` | `filesystem` 服务器的 `read_file` 工具 |
| `mcp__myserver__my_action` | 你配置的自定义服务器 |

---

## 记忆系统

pycc 内置三层记忆管道，让助手能够跨对话积累上下文。所有层均异步运行，不阻塞主交互循环。

### 存储结构

记忆以独立 Markdown 文件形式保存，每条记忆一个文件：

| 作用域 | 路径 | 可见性 |
|---|---|---|
| **User**（默认）| `~/.pycc/memory/` | 跨所有项目共享 |
| **Project** | `.pycc/memory/`（当前目录）| 仅限当前仓库 |

每次写入或删除后，`MEMORY.md` 索引自动重建（≤ 200 行 / 25 KB），并注入系统提示，让模型始终有记忆概览。

### 记忆类型

| 类型 | 保存什么 |
|---|---|
| `user` | 你的角色、技能水平、工作风格、明确的偏好 |
| `feedback` | 纠正或确认模型做事方式的指令（"如何做"，而非"做什么代码"） |
| `project` | 进行中的目标、截止日期、git 历史中没有的决策 |
| `reference` | 指向外部系统的指针（Linear、Grafana、Slack 频道等） |

**代码事实豁免（严格执行）：** 文件路径、函数名、代码架构、调试方案、git 历史——任何可以通过 `grep`/`git` 从代码库直接读取的内容均**不保存**为记忆。

### 记忆文件格式

```markdown
---
name: prefers_direct_answers
description: 用户希望回答简洁，不要冗余总结
type: feedback
created: 2026-05-01
---
不要在每次回复末尾添加"总结"段落。
**Why:** 用户说可以自己看 diff，不需要重复叙述。
**How to apply:** 回复结束时直接停止，不加总结句。
```

### 三层记忆管道

```
会话进行中        每次查询前 → 后台检索线程（flash 模型）→ 注入系统提示
                                     ↓
会话结束时        Layer 2：自动提取（条件触发，flash 模型，后台线程）
                  Layer 3：AutoDream（每 5 次会话 + 24h 冷却，后台线程）
```

**检索（每轮查询）**

每次用户输入时，后台线程并行扫描所有记忆文件头，用 `subagent_model`（默认 `deepseek-v4-flash`）选出最相关的 ≤ 5 条，注入当轮系统提示。整个过程与主 API 调用并行，零等待。

**Layer 2 — 自动提取（会话结束时）**

触发条件（全部满足）：
- 会话时长 ≥ 5 分钟
- 对话轮次 ≥ 10 轮
- 距上次提取 ≥ 30 分钟

满足条件时，启动后台线程，将会话摘要发送给 flash 模型，严格执行代码事实豁免，将识别出的 user / feedback / project / reference 记忆写入磁盘。用户无感知，不延迟退出。

**Layer 3 — AutoDream（周期整合）**

触发条件：距上次整合 ≥ 24 小时，且已积累 ≥ 5 次会话。

后台线程让 flash 模型扫描全部记忆，执行：去重（合并内容重复的条目）、删除超过 90 天的时效性记忆（通用偏好不受影响）。

### 手动操作

**REPL 命令：**

```
/memory                  列出所有记忆（按修改时间排序）
/memory <关键词>          按关键词搜索
/memory consolidate      立即触发 Layer 3 整合（同步，会显示结果）
```

**工具（模型可直接调用）：**

| 工具 | 作用 |
|---|---|
| `MemorySave` | 保存或更新一条记忆（name、type、description、content、scope） |
| `MemoryDelete` | 按名称删除记忆 |
| `MemorySearch` | 关键词搜索，可选 AI 排序 |
| `MemoryList` | 列出所有记忆及元数据 |

### 配置

`subagent_model` 控制所有记忆操作使用的轻量模型（检索、提取、整合），默认 `deepseek/deepseek-v4-flash`，可独立于主模型配置：

```
/config subagent_model=deepseek/deepseek-v4-flash
```

---

## 技能系统

技能是可复用的提示模板，为模型提供专项能力。两个内置技能开箱即用，无需任何配置。

**内置技能：**

| 触发器 | 说明 |
|---|---|
| `/commit` | 审查暂存变更并创建规范的 git commit |
| `/review [PR]` | 对代码或 PR diff 进行结构化审查 |

**快速上手——自定义技能：**

```bash
mkdir -p ~/.pycc/skills
```

创建 `~/.pycc/skills/deploy.md`：

```markdown
---
name: deploy
description: 部署到某个环境
triggers: [/deploy]
allowed-tools: [Bash, Read]
when_to_use: 当用户想要将某个版本部署到某个环境时使用。
argument-hint: [env] [version]
arguments: [env, version]
---

将 $VERSION 部署到 $ENV 环境。
完整参数：$ARGUMENTS
```

使用：

```
You: /deploy staging 2.1.0
AI: [将 2.1.0 版本部署到 staging 环境]
```

**参数替换：**
- `$ARGUMENTS` — 完整原始参数字符串
- `$ARG_NAME` — 按命名参数的位置替换
- 缺失的参数变为空字符串

**优先级**（高 → 低）：项目级 > 用户级 > 内置

**列出技能：** `/skills`

**技能搜索路径：**

```
./.pycc/skills/     # 项目级（覆盖用户级）
~/.pycc/skills/     # 用户级
```

---

## 子智能体

模型可以派发独立的子智能体并行处理任务。

**内置的专项智能体类型：**

| 类型 | 优化方向 |
|---|---|
| `general-purpose` | 研究、探索、多步骤任务 |
| `coder` | 编写、阅读和修改代码 |
| `reviewer` | 安全性、正确性和代码质量分析 |
| `researcher` | 网页搜索和文档查找 |
| `tester` | 编写和运行测试 |

**基本用法：**
```
You: 搜索代码库中所有 TODO 注释并汇总。
AI: [调用 Agent(prompt="...", subagent_type="researcher")]
    子智能体读取文件，grep TODO...
    结果：在 5 个文件中发现 12 个 TODO...
```

**后台模式** — 无需等待，稍后收集结果：
```
AI: [调用 Agent(prompt="run all tests", name="test-runner", wait=false)]
AI: [继续其他工作...]
AI: [调用 CheckAgentResult / SendMessage 跟进]
```

**git worktree 隔离** — 智能体在独立分支上工作，无冲突：
```
Agent(prompt="重构 auth 模块", isolation="worktree")
```

**自定义智能体类型** — 创建 `~/.pycc/agents/myagent.md`：
```markdown
---
name: myagent
description: 专门处理 X
model: claude-haiku-4-5-20251001
tools: [Read, Grep, Bash]
---
此智能体类型的额外系统提示。
```

**列出运行中的智能体：** `/agents`

子智能体拥有独立对话历史，共享文件系统，最多嵌套 3 层。

---

## MCP（模型上下文协议）

MCP 允许你接入任意外部工具服务器——本地子进程或远程 HTTP——Claude 可自动使用其工具。

### 支持的传输方式

| 传输 | 配置 `type` | 说明 |
|---|---|---|
| **stdio** | `"stdio"` | 派生本地子进程（最常见） |
| **SSE** | `"sse"` | HTTP Server-Sent Events 流 |
| **HTTP** | `"http"` | 可流式 HTTP POST（较新的服务器） |

### 配置

在项目目录放置 `.mcp.json` 文件，**或**编辑 `~/.pycc/mcp.json` 配置全局服务器。

```json
{
  "mcpServers": {
    "git": {
      "type": "stdio",
      "command": "uvx",
      "args": ["mcp-server-git"]
    },
    "filesystem": {
      "type": "stdio",
      "command": "uvx",
      "args": ["mcp-server-filesystem", "/tmp"]
    },
    "my-remote": {
      "type": "sse",
      "url": "http://localhost:8080/sse",
      "headers": {"Authorization": "Bearer my-token"}
    }
  }
}
```

配置优先级：`.mcp.json`（项目）按服务器名覆盖 `~/.pycc/mcp.json`（用户）。

### 快速上手

```bash
pip install uv
uvx mcp-server-git --help

/mcp add git uvx mcp-server-git
/mcp reload
```

### REPL 命令

```
/mcp                          # 列出服务器 + 工具 + 连接状态
/mcp reload                   # 重连所有服务器，刷新工具列表
/mcp reload git               # 重连单个服务器
/mcp add myserver uvx mcp-server-x
/mcp remove myserver
```

### 热门 MCP 服务器

| 服务器 | 安装 | 提供功能 |
|---|---|---|
| `mcp-server-git` | `uvx mcp-server-git` | git 操作 |
| `mcp-server-filesystem` | `uvx mcp-server-filesystem <path>` | 文件读写 |
| `mcp-server-fetch` | `uvx mcp-server-fetch` | HTTP 抓取 |
| `mcp-server-postgres` | `uvx mcp-server-postgres <conn-str>` | PostgreSQL 查询 |
| `mcp-server-sqlite` | `uvx mcp-server-sqlite --db-path x.db` | SQLite 查询 |
| `mcp-server-brave-search` | `uvx mcp-server-brave-search` | Brave 网页搜索 |

> 浏览完整服务器列表：[modelcontextprotocol.io/servers](https://modelcontextprotocol.io/servers)

---

## AskUserQuestion 工具

Claude 可以在任务中途暂停，交互式地向你提问后再继续。

**终端显示：**
```
❓ 来自助手的问题：
   应该使用哪个数据库？

  [1] SQLite — 简单，基于文件
  [2] PostgreSQL — 功能完整，需要服务器
  [0] 输入自定义答案

你的选择（数字或文本）：
```

- 输入编号或直接输入文本
- 5 分钟超时（无响应则返回 "(no answer — timeout)"）

---

## 任务管理

`task/` 包让 Claude（和你）拥有结构化任务列表，用于追踪会话内的多步骤工作。

### Claude 可用的工具

| 工具 | 参数 | 作用 |
|------|------|------|
| `TaskCreate` | `subject`、`description`、`active_form?`、`metadata?` | 创建任务 |
| `TaskUpdate` | `task_id`、`subject?`、`description?`、`status?`、`owner?`、`metadata?` | 更新任意字段；`status='deleted'` 删除任务 |
| `TaskGet` | `task_id` | 返回单个任务的完整详情 |
| `TaskList` | _（无）_ | 列出所有任务及状态图标 |

**有效状态：** `pending` → `in_progress` → `completed` / `cancelled` / `deleted`

### 持久化

每次变更后任务保存到当前工作目录的 `.pycc/tasks.json`，首次访问时重新加载。

### REPL 命令

```
/tasks                    列出所有任务
/tasks create <subject>   快速创建任务
/tasks start <id>         标记为进行中
/tasks done <id>          标记为已完成
/tasks cancel <id>        标记为已取消
/tasks delete <id>        删除任务
/tasks get <id>           显示完整详情
/tasks clear              删除所有任务
```

---

## 计划模式

计划模式通过 `EnterPlanMode` / `ExitPlanMode` 工具实现**两阶段规划-执行切换**：plan 阶段强制只读分析、输出方案后暂停，用户确认后进入执行阶段，**用代码层权限拦截而非 prompt 约束保证隔离**。

**架构设计**：计划模式是独立于 `permission_mode` 的运行时限制层（Planning Overlay），激活/停用不会修改 `permission_mode`（`auto`/`manual`/`accept-all`），两者正交运作。权限判断在 `agent.py::_check_permission()` 的代码层完成，模型无法绕过。

**两阶段工作流：**

| 阶段 | 触发 | 限制 | 可写文件 |
|---|---|---|---|
| Plan 阶段 | `EnterPlanMode` | 仅读取工具 + 安全 Bash | 仅 `.nano_claude/plans/<session>.md` |
| 执行阶段 | 用户确认后 | 由基础 `permission_mode` 决定 | 正常权限 |

在计划模式下：
- **写入被拦截**（代码层，非 prompt），仅**专属计划文件**例外。
- 压缩后计划文件上下文自动恢复。

### 斜杠命令工作流

```
[myproject] ❯ /plan 添加 WebSocket 支持
  计划限制层已激活(仅计划文件可写入)。

[myproject] ❯ /plan status
  计划模式: 已激活
  基础权限模式: auto
  计划文件: .../.nano_claude/plans/xxx.md

[myproject] ❯ /plan
  # 计划：添加 WebSocket 支持
  ## 阶段 1：创建 ws_handler.py
  ...

[myproject] ❯ /plan done
  计划限制层已停用。
```

### 命令

| 命令 | 说明 |
|---|---|
| `/plan <description>` | 激活计划限制层 |
| `/plan` | 打印当前计划文件内容 |
| `/plan done` | 停用计划限制层（不改变 permission_mode） |
| `/plan status` | 显示计划模式状态及基础权限模式 |

---

## 上下文压缩

长对话超过模型上下文窗口时自动触发，采用**五层渐进式压缩策略**，按内容可恢复性决定驱逐优先级——可重新获取的内容（工具执行结果）优先清除，不可复现的对话语义最后处理。

### 五层压缩机制

| 层级 | 触发时机 | 机制 | 信息损失 |
|---|---|---|---|
| **Layer 1** 磁盘卸载 | 工具结果生成时 | 超过阈值的大型工具输出写入磁盘，context 中保留摘要引用；Read 可随时重取 | 无 |
| **Layer 2** 旧轮次移除 | 超过 70% 上下文 | 移除最早的完整对话轮次（user+assistant+tool），保留最近 N 轮 | 有（旧轮次） |
| **Layer 3** 可复现结果清除 | 空闲超过 60 分钟 | 清除 Read/Bash/Glob/Grep 等工具的旧结果（可重新执行获取），保留最近 5 条 | 极低（可重取） |
| **Layer 4** 读时投影 | 每次 API 调用前 | **不修改原始 messages**；在 API 调用瞬间生成压缩视图（90% 阈值保留 40% token，95% 阈值保留 25%） | 无（原始历史完整保留） |
| **Layer 5** LLM 摘要 | 超过 70% 且前几层不足 | 调用模型将旧消息压缩为语义摘要，压缩后恢复计划文件/技能上下文 | 有（细节） |

### 手动压缩

```
[myproject] ❯ /compact
  已压缩：~12400 → ~3200 tokens（节省 ~9200）

[myproject] ❯ /compact keep the WebSocket implementation details
  已压缩：~11800 → ~3100 tokens（节省 ~8700）
```

---

## 差异视图

模型编辑或覆写文件时，你会看到 git 风格的差异：

```diff
--- a/config.py
+++ b/config.py
@@ -12,7 +12,7 @@
-    "max_tokens": 8192,
+    "max_tokens": 16384,
```

绿色行 = 新增，红色行 = 删除。

---

## CLAUDE.md 支持

在项目中放置 `CLAUDE.md` 文件，为模型提供代码库的持久上下文。pycc 自动查找并注入到系统提示中。

```
~/.claude/CLAUDE.md          # 全局——适用于所有项目
/your/project/CLAUDE.md      # 项目级——从 cwd 向上查找
```

---

## 会话管理

每次退出自动保存到三个位置：

```
~/.pycc/sessions/
├── history.json                    ← 主记录：所有会话
├── mr_sessions/
│   └── session_latest.json        ← 最近一次（/resume）
└── daily/
    └── 2026-04-05/
        └── session_110523_a3f9.json
```

**快速恢复：**

```bash
pycc
[myproject] ❯ /resume
✓  已加载会话（42 条消息）
```

**手动保存 / 加载：**

```bash
/save                    # 自动命名
/save debug_auth_bug     # 命名保存
/load                    # 交互式列表
/load debug_auth_bug     # 按文件名加载
```

---

## 项目结构

```
pycc/
├── pycc.py                # 入口：REPL + 斜杠命令 + 差异渲染 + Rich Live 流式输出
├── agent.py              # 智能体循环：流式输出、工具分发、压缩
├── providers.py          # 多厂商：Anthropic、OpenAI 兼容流式
├── tools.py              # 核心工具 + 注册连接
├── tool_registry.py      # 工具插件注册：注册、查找、执行
├── compaction.py         # 上下文压缩：截断 + 自动摘要
├── context.py            # 系统提示构建：CLAUDE.md + git + 记忆
├── config.py             # 配置加载/保存/默认值
│
├── multi_agent/          # 多智能体包
├── memory/               # 记忆包
├── skill/                # 技能包
├── mcp/                  # MCP 包
├── hooks/                # Hook 系统：工具执行前/后回调
├── security/             # 安全分析器：权限检查，Bash 风险级别
│
└── tests/                # 单元测试
```

> **开发者说明：** 通过在任何被 `tools.py` 导入的模块中调用 `register_tool(ToolDef(...))` 即可添加自定义工具。

---

## 常见问题

**Q：如何添加 MCP 服务器？**

```
/mcp add git uvx mcp-server-git
```

或在项目中创建 `.mcp.json`，然后运行 `/mcp reload`。

**Q：如何连接到运行 vLLM 的远程 GPU 服务器？**

```
/config custom_base_url=http://your-server-ip:8000/v1
/config custom_api_key=your-token
/model custom/your-model-name
```

**Q：如何查看 API 费用？**

```
/cost
  输入 token：  3,421
  输出 token：    892
  估算费用：   $0.0648 USD
```

**Q：能通过管道向 pycc 输入内容吗？**

```bash
echo "解释这个文件" | pycc --print --accept-all
cat error.log | pycc -p "这个错误是什么原因导致的？"
```

---

## SWE-bench Lite 评测

`eval/` 目录提供完整的评测管道，可在 SWE-bench Lite（300 个真实 GitHub issue）上系统测试 pycc 的 bug 修复能力。

### 评测流程

1. 从 HuggingFace 加载数据集，每个实例包含：repo、base commit、原始 issue 正文（`problem_statement`）、FAIL_TO_PASS 测试用例
2. 将仓库 clone 到 bug 发生时的 commit
3. 将 issue 正文直接作为 prompt 传给 pycc（`--print --accept-all`），模型自主探索代码库并写修复
4. `git diff` 提取 patch
5. 打分：启发式代理（无需 Docker）或官方 Docker 评测

```bash
# 运行 30 个实例
python eval/batch_eval.py --n 30 --workers 2 --workdir /tmp/pycc_swe \
    --model deepseek/deepseek-v4-pro --timeout 600

# 启发式打分（快速）
python eval/score.py --workdir /tmp/pycc_swe

# 官方 Docker 打分（精确）
python eval/score.py --workdir /tmp/pycc_swe --official
```

### 当前结果（30 实例子集，2026-05-06）

| 指标 | 数值 |
|---|---|
| 运行实例数 | 30 |
| 生成 patch | 28 / 30 |
| 超时（900s）| 2 |
| patch 格式错误 | 2 |
| **官方 Docker resolve rate** | **21 / 30（70%）** |

模型：`deepseek/deepseek-v4-pro`，per-instance timeout：600-900s

### 局限性

**样本偏差**：30 个实例全部来自 astropy 和 django，均为极主流的 Python 项目，代表性有限。完整 300 实例还包括 sympy、matplotlib、requests 等分布更广的仓库，全量结果预计会显著下降。

**数据污染风险**：SWE-bench Lite 实例对应的 gold patch 均已在 GitHub 公开，deepseek-v4-pro 训练数据可能覆盖了这些修复 commit，导致模型部分依赖"记忆"而非推理。

**无 max_turns 限制**：当前 agent 循环没有轮次上限，极端情况下可能陷入长时间探索而不收敛，只能依赖外层 subprocess timeout 兜底。

**全量评测**：尚未完成全部 300 实例的官方评测，70% 不应作为最终基准。
