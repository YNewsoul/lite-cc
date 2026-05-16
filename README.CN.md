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
| 多厂商支持 | Anthropic · OpenAI · Gemini · Kimi · Qwen · 智谱 · DeepSeek · MiniMax · Ollama · LM Studio · 自定义端点 |
| 交互式 REPL | readline 历史记录，Tab 补全斜杠命令及子命令提示；Bracketed Paste 模式支持可靠的多行粘贴 |
| 智能体循环 | 流式 API + 自动工具调用循环 |
| 27 个内置工具 | Read · Write · Edit · Bash · Glob · Grep · WebFetch · WebSearch · **NotebookEdit** · **GetDiagnostics** · MemorySave · MemoryDelete · MemorySearch · MemoryList · Agent · SendMessage · CheckAgentResult · ListAgentTasks · ListAgentTypes · Skill · SkillList · AskUserQuestion · TaskCreate/Update/Get/List · **SleepTimer** · **EnterPlanMode** · **ExitPlanMode** · *（MCP 工具在启动时自动注册）* |
| MCP 集成 | 接入任意 MCP 服务器（stdio/SSE/HTTP），工具自动注册，Claude 可直接调用 |
| AskUserQuestion | Claude 可在任务中途暂停并向用户提问，支持编号选项 |
| 任务管理 | TaskCreate/Update/Get/List 工具；顺序 ID；元数据；持久化至 `.pycc/tasks.json`；`/tasks` REPL 命令 |
| 差异视图 | Edit 和 Write 操作后显示 git 风格的红绿差异 |
| 上下文压缩 | 自动压缩长对话以保持在模型上下文限制内 |
| 持久记忆 | 双作用域记忆（user + project），4 种类型，置信度/来源元数据，冲突检测，按使用频率加权搜索，`last_used_at` 追踪，以及 `/memory consolidate` 自动提炼 |
| 多智能体 | 派发类型化子智能体（coder/reviewer/researcher/…），git worktree 隔离，后台模式 |
| 技能系统 | 内置 `/commit` · `/review` + 支持参数替换的自定义 Markdown 技能 |
| 权限系统 | `auto` / `accept-all` / `manual` / `plan` 四种模式 |
| 计划模式 | `/plan <描述>` 进入只读分析模式；Claude 仅写入计划文件；`EnterPlanMode` / `ExitPlanMode` 工具支持自主规划 |
| 视觉输入 | `/image`（或 `/img`）截取剪贴板图片并发送给任意视觉模型 |
| 强制退出 | 2 秒内连按 3 次 Ctrl+C 触发 `os._exit(1)`，立即终止进程 |
| Rich 流式渲染 | 安装 `rich` 后，响应以实时更新的 Markdown 原地渲染 |
| 上下文注入 | 自动加载 `CLAUDE.md`、git 状态、当前目录、持久记忆 |
| 会话持久化 | 退出时自动保存到 `daily/YYYY-MM-DD/` + `history.json` + `session_latest.json` |
| Extended Thinking | Claude 模型可开/关；本地 Ollama 推理模型（deepseek-r1、qwen3、gemma4）支持原生 `<think>` 块流式输出 |
| 费用追踪 | Token 用量 + 估算 USD 费用 |
| 非交互模式 | `--print` 标志用于脚本/CI |

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

### 开源模型（本地，通过 Ollama）

| 模型 | 参数量 | 优势 | 拉取命令 |
|---|---|---|---|
| `llama3.3` | 70B | 通用，推理强 | `ollama pull llama3.3` |
| `llama3.2` | 3B / 11B | 轻量 | `ollama pull llama3.2` |
| `qwen2.5-coder` | 7B / 32B | **编码任务首选** | `ollama pull qwen2.5-coder` |
| `qwen2.5` | 7B / 72B | 中英双语 | `ollama pull qwen2.5` |
| `deepseek-r1` | 7B–70B | 推理，数学 | `ollama pull deepseek-r1` |
| `deepseek-coder-v2` | 16B | 编码 | `ollama pull deepseek-coder-v2` |
| `mistral` | 7B | 快速，高效 | `ollama pull mistral` |
| `mixtral` | 8x7B | 强 MoE 模型 | `ollama pull mixtral` |
| `phi4` | 14B | 微软出品，推理强 | `ollama pull phi4` |
| `gemma3` | 4B / 12B / 27B | Google 开源模型 | `ollama pull gemma3` |
| `codellama` | 7B / 34B | 代码生成 | `ollama pull codellama` |
| `llava` | 7B / 13B | **视觉** — 图像理解 | `ollama pull llava` |
| `llama3.2-vision` | 11B | **视觉** — 多模态推理 | `ollama pull llama3.2-vision` |

> **注意：** 工具调用需要模型支持 function calling。推荐本地模型：`qwen2.5-coder`、`llama3.3`、`mistral`、`phi4`。

> **推理模型：** `deepseek-r1`、`qwen3`、`gemma4` 支持原生 `<think>` 块流式输出。开启 `/verbose` 和 `/thinking` 可在终端看到思考过程。注意：接收大型系统提示（如 pycc 的 25 个工具 schema）的模型可能会压缩思考阶段以避免破坏预期的 JSON 格式——这是模型行为，不是 bug。

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

## 用法：开源本地模型

### 方案 A — Ollama（推荐）

Ollama 零配置本地运行模型，无需 API Key。

**第一步：安装 Ollama**

```bash
# macOS / Linux
curl -fsSL https://ollama.com/install.sh | sh

# 或从 https://ollama.com/download 下载
```

**第二步：拉取模型**

```bash
# 编码首选
ollama pull qwen2.5-coder          # 4.7 GB（7B）
ollama pull qwen2.5-coder:32b      # 19 GB（32B）

# 通用
ollama pull llama3.3               # 42 GB（70B）
ollama pull llama3.2               # 2.0 GB（3B）

# 推理
ollama pull deepseek-r1            # 4.7 GB（7B）
ollama pull deepseek-r1:32b        # 19 GB（32B）

# 其他
ollama pull phi4                   # 9.1 GB（14B）
ollama pull mistral                # 4.1 GB（7B）
```

**第三步：启动 Ollama 服务**（macOS 自动启动；Linux 需手动运行）

```bash
ollama serve     # 监听 http://localhost:11434
```

**第四步：运行 pycc**

```bash
pycc --model ollama/qwen2.5-coder
pycc --model ollama/llama3.3
pycc --model ollama/deepseek-r1
```

或：

```bash
python pycc.py --model ollama/qwen2.5-coder
python pycc.py --model ollama/llama3.3
python pycc.py --model ollama/deepseek-r1
```

**列出本地已有模型：**

```bash
ollama list
```

然后使用列表中的任意模型：

```bash
pycc --model ollama/<model-name>
```

---

### 方案 B — LM Studio

LM Studio 提供图形界面下载和运行模型，内置 OpenAI 兼容服务器。

**第一步：** 下载并安装 [LM Studio](https://lmstudio.ai)。

**第二步：** 在 LM Studio 内搜索并下载模型（GGUF 格式）。

**第三步：** 进入 **Local Server** 标签页 → 点击 **Start Server**（默认端口：1234）。

**第四步：**

```bash
pycc --model lmstudio/<model-name>
# 例如：
pycc --model lmstudio/phi-4-GGUF
pycc --model lmstudio/qwen2.5-coder-7b
```

模型名称应与 LM Studio 服务器状态栏显示的一致。

---

### 方案 C — vLLM / 自建 OpenAI 兼容服务器

适用于自建推理服务器（vLLM、TGI、llama.cpp server 等）暴露 OpenAI 兼容 API 的情况：

**第一步：启动 vLLM：**

```
CUDA_VISIBLE_DEVICES=7 python -m vllm.entrypoints.openai.api_server \
      --model Qwen/Qwen2.5-Coder-7B-Instruct \
      --host 0.0.0.0 \
      --port 8000 \
      --enable-auto-tool-choice \
      --tool-call-parser hermes
```

**第二步：启动 pycc：**

```
export CUSTOM_BASE_URL=http://localhost:8000/v1
export CUSTOM_API_KEY=none
pycc --model custom/Qwen/Qwen2.5-Coder-7B-Instruct
```

在 REPL 内配置：

```
/config custom_base_url=http://localhost:8000/v1
/config custom_api_key=token-abc123    # 无鉴权则跳过
/model custom/Qwen2.5-Coder-32B-Instruct
```

远程 GPU 服务器：

```bash
/config custom_base_url=http://192.168.1.100:8000/v1
/model custom/your-model-name
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
pycc --model ollama/qwen2.5-coder
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
| `llama`、`mistral`、`phi`、`gemma`、`mixtral`、`codellama` | ollama |

---

## CLI 参考

```
pycc [OPTIONS] [PROMPT]
# 或：python pycc.py [OPTIONS] [PROMPT]

Options:
  -p, --print          非交互模式：运行提示词后退出
  -m, --model MODEL    覆盖模型（如 gpt-4o、ollama/llama3.3）
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
pycc -m ollama/deepseek-r1:32b

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
| `/memory` | 列出所有持久记忆 |
| `/memory <query>` | 按关键词搜索记忆（按置信度 × 近期度排序） |
| `/memory consolidate` | AI 从当前会话中提炼最多 3 条长期洞察 |
| `/skills` | 列出可用技能 |
| `/agents` | 显示子智能体任务状态 |
| `/mcp` | 列出已配置的 MCP 服务器及其工具 |
| `/mcp reload` | 重连所有 MCP 服务器并刷新工具列表 |
| `/mcp reload <name>` | 重连单个 MCP 服务器 |
| `/mcp add <name> <cmd> [args]` | 向用户配置添加 stdio MCP 服务器 |
| `/mcp remove <name>` | 从用户配置中移除服务器 |
| `/image [prompt]` | 截取剪贴板图片并发送给视觉模型（可附加提示词） |
| `/img [prompt]` | `/image` 的别名 |
| `/plan <description>` | 进入计划模式：只读分析，仅写入计划文件 |
| `/plan` | 显示当前计划文件内容 |
| `/plan done` | 退出计划模式，恢复原始权限 |
| `/plan status` | 显示计划模式是否激活 |
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
  当前模型：claude-opus-4-6  (厂商: anthropic)

  按厂商列出的可用模型：
    anthropic     claude-opus-4-6, claude-sonnet-4-6, ...
    openai        gpt-4o, gpt-4o-mini, o3-mini, ...
    ollama        llama3.3, llama3.2, phi4, mistral, ...
    ...

[myproject] ❯ /model gpt-4o
  模型已切换为 gpt-4o  (厂商: openai)

[myproject] ❯ /model ollama/qwen2.5-coder
  模型已切换为 ollama/qwen2.5-coder  (厂商: ollama)
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
| `plan` | 只读分析模式。仅计划文件（`.pycc/plans/`）可写。通过 `/plan <desc>` 或 `EnterPlanMode` 工具进入。 |

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

模型可以通过内置记忆系统跨对话记住信息。

### 存储

记忆以独立 Markdown 文件形式保存在两个作用域中：

| 作用域 | 路径 | 可见性 |
|---|---|---|
| **User**（默认）| `~/.pycc/memory/` | 跨所有项目共享 |
| **Project** | 当前目录下的 `.pycc/memory/` | 仅限当前仓库 |

每次保存或删除后自动重建 `MEMORY.md` 索引（≤ 200 行 / 25 KB），并注入系统提示，让模型始终有记忆概览。

### 记忆类型

| 类型 | 适用于 |
|---|---|
| `user` | 你的角色、偏好、背景 |
| `feedback` | 你希望模型如何表现（纠正与确认） |
| `project` | 进行中的工作、截止日期、git 历史中没有的决策 |
| `reference` | 指向外部系统的链接（Linear、Grafana、Slack 等） |

### 记忆文件格式

每条记忆是一个带 YAML 前置元数据的 Markdown 文件：

```markdown
---
name: coding_style
description: Python 格式偏好
type: feedback
created: 2026-04-02
confidence: 0.95
source: user
last_used_at: 2026-04-05
conflict_group: coding_style
---
Python 代码一律使用 4 空格缩进和完整类型标注。
**Why:** 用户明确声明了此偏好。
**How to apply:** 对每个写入或编辑的 Python 文件应用。
```

**元数据字段**（自动管理）：

| 字段 | 默认值 | 说明 |
|---|---|---|
| `confidence` | `1.0` | 可靠性评分 0–1。用户明确陈述 = 1.0；推断偏好 ≈ 0.8；自动提炼 ≈ 0.8 |
| `source` | `user` | 来源：`user` / `model` / `tool` / `consolidator` |
| `last_used_at` | — | 每次被 MemorySearch 返回时自动更新 |
| `conflict_group` | — | 对相关记忆分组（如 `writing_style`）以追踪冲突 |

### 冲突检测

当 `MemorySave` 被调用且名称已存在但内容不同时，系统在覆盖前报告冲突：

```
Memory saved: 'writing_style' [feedback/user]
⚠ Replaced conflicting memory (was user-sourced, 100% confidence, written 2026-04-01).
  Old content: Prefer formal, academic style...
```

### 排序检索

`MemorySearch` 按**置信度 × 近期度**（30 天指数衰减）排序，而非简单关键词顺序。长期未使用的记忆优先级降低。每次搜索命中还会更新 `last_used_at`，让常用记忆保持靠前。

### `/memory consolidate` — 自动提炼长期洞察

有意义的会话结束后，运行：

```
[myproject] ❯ /memory consolidate
  正在从会话中分析长期记忆...
  ✓ 已提炼 2 条记忆：user_prefers_direct_answers, avoid_trailing_summaries
```

该命令将精简版会话记录发送给模型，要求它识别最多 **3** 条值得长期保留的洞察。提炼的记忆以 `confidence: 0.80` 和 `source: consolidator` 保存——**绝不覆盖**已有更高置信度的记忆。

**陈旧警告：** 超过 1 天的记忆会显示 `⚠ stale` 提示——关于文件行号或代码状态的声明可能已过时，行动前请核实。

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

计划模式是一种处理复杂多文件任务的结构化工作流：Claude 先在只读阶段分析代码库并撰写明确计划，用户批准后再开始实施。

在计划模式下：
- **只允许读取**（`Read`、`Glob`、`Grep`、`WebFetch`、`WebSearch`、安全的 `Bash` 命令）。
- **写入被阻止**，仅限**专属计划文件**（`.nano_claude/plans/<session_id>.md`）。
- 压缩后计划文件上下文自动恢复。

### 斜杠命令工作流

```
[myproject] ❯ /plan 添加 WebSocket 支持
  已进入计划模式。

[myproject] ❯ /plan
  # 计划：添加 WebSocket 支持
  ## 阶段 1：创建 ws_handler.py
  ...

[myproject] ❯ /plan done
  已退出计划模式。权限模式已恢复为：auto
```

### 命令

| 命令 | 说明 |
|---|---|
| `/plan <description>` | 进入计划模式 |
| `/plan` | 打印当前计划文件内容 |
| `/plan done` | 退出计划模式，恢复之前的权限 |
| `/plan status` | 显示计划模式是否激活 |

---

## 上下文压缩

长对话会自动压缩以保持在模型上下文窗口内。

**两个层次：**

1. **截断** — 旧的工具输出被截断。速度快，无 API 费用。
2. **自动压缩** — 当 token 用量超过上下文限制的 70% 时，旧消息由模型摘要为简洁回顾。

**手动压缩：**

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

**Q：本地 Ollama 模型无法调用工具。**

使用支持 function calling 的模型：`qwen2.5-coder`、`llama3.3`、`mistral` 或 `phi4`。

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
