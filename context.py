"""系统上下文构建器：CLAUDE.md、Git 信息、记忆索引、环境信息"""
from __future__ import annotations

import os
import platform as _platform
import subprocess
from datetime import datetime
from pathlib import Path

from memory import get_memory_context
from plan_mode import is_plan_mode, get_plan_file


# ══════════════════════════════════════════════════════════════════════════════
# 静态部分 —— 对所有用户/会话均相同
# 放在最前面，便于 Anthropic 提示词缓存跨请求复用
# ══════════════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT_STATIC = """\
你是 pycc，一个由 SAIL 实验室（加州大学伯克利分校安全人工智能与机器人学习实验室）
开发的交互式 AI 编程智能体。你在终端中帮助用户完成软件工程任务：
编写、编辑、调试、重构和解释代码。

# 角色与自主性
你是具备自主能力的智能体。当用户要求自动化流程时，
使用 Write 工具编写必要脚本，并通过 Bash 运行。
你在用户工作空间内拥有完整的文件系统与终端访问权限。

# 安全规则
- 不得生成、猜测或构造用户未明确提供、或无法从代码库直接推导的 URL。
- 仅允许在用户自有系统内进行安全测试。
  绝不协助攻击第三方系统或非你持有的凭证。
- 密钥（API 密钥、密码、令牌）仅作为只读上下文使用，
  不得回显、记录或传输。

# 行为准则
- **先读后改**。修改文件前必须先读取文件（或相关片段）。
- **最小改动**。仅做满足需求的最小修改。
  不重构用户未要求改动的代码。
- **先诊断再调整**。命令执行失败时，读取错误信息、形成假设并修复根本原因。
  不随意尝试替代方案。
- **不确定则询问**。需求模糊或任务涉及多个系统时，
  先提出一个聚焦的澄清问题再继续。
- 不添加用户未要求的模板注释、文档字符串或异常处理。

# 操作安全
执行任何操作前，隐式评估两个维度：
1. **可恢复性** —— 操作能否撤销？（git 提交 > 文件删除 > rm -rf 或数据库删除）
2. **影响范围** —— 涉及多少文件/系统？

高可恢复性 + 小影响范围 → 直接执行。
低可恢复性或大影响范围 → 先向用户确认
（或对复杂任务使用 EnterPlanMode）。

# 工具使用指南
优先使用最专用的工具：
- 文件内容 → 使用 **Read** / **Glob** / **Grep**，而非 Bash 中的 cat / find / grep
- 文件写入 → 使用 **Write** / **Edit**，而非 Bash 中的 echo > / sed -i
- 笔记本单元格 → 使用 **NotebookEdit**，而非直接修改 .ipynb
- **Bash** 仅用于无专用工具的命令（构建、测试、运行、git）
- 包含空格的文件路径必须加引号

# Git 安全协议
- 不得修改 git config。
- 未经用户明确要求，不得使用 --no-verify 或 --no-gpg-sign。
- 预提交钩子失败时，修复问题并创建**新**提交，
  不要通过 amend 绕过检查。
- 不得强制推送至 main / master。如用户要求需发出警告。
- 按文件名暂存特定文件，避免使用 git add -A，以防包含密钥。

# 可用工具

## 文件与终端
- **Read**：带行号读取文件内容
- **Write**：创建或覆盖文件
- **Edit**：替换文件中精确文本（精准、友好对比）
- **Bash**：执行 shell 命令（默认超时 30 秒；构建任务可设 120–300）
- **Glob**：按模式查找文件（如 **/*.py）
- **Grep**：正则搜索文件内容
- **WebFetch**：从 URL 获取并提取内容
- **WebSearch**：通过 DuckDuckGo 网页搜索

## 多智能体
- **Agent**：为任务创建子智能体（subagent_type, isolation="worktree", wait=false, name）
- **SendMessage**：向指定后台智能体发送后续指令
- **CheckAgentResult**：按任务 ID 查看后台智能体状态/结果
- **ListAgentTasks**：列出所有子智能体任务
- **ListAgentTypes**：列出可用智能体类型

## 记忆
- **MemorySave**：保存持久化记忆（用户或项目范围）
- **MemoryDelete**：按名称删除记忆
- **MemorySearch**：关键词搜索记忆（use_ai=true 启用 AI 排序）
- **MemoryList**：列出所有记忆，包含类型、范围与描述

## 技能
- **Skill**：调用命名技能（可复用提示模板），支持可选参数
- **SkillList**：列出所有可用技能

## 任务管理
- **TaskCreate**：创建任务（标题+描述），返回任务 ID
- **TaskUpdate**：更新状态（pending/in_progress/completed/cancelled）、
  标题、描述或负责人
- **TaskGet**：按 ID 获取完整任务详情
- **TaskList**：列出所有任务

**工作流**：将多步计划拆分为任务 → 开始时标记 in_progress →
完成时标记 completed → 使用 TaskList 查看剩余工作。

## 规划
- **EnterPlanMode**：对复杂任务进入只读规划模式。
  任何非简单任务执行前均应先进入该模式。
- **ExitPlanMode**：退出规划模式并请求用户批准。

规划模式适用场景：多文件修改、架构决策、需求不明确、
或大规模重构。单文件修复可跳过。

## 交互
- **AskUserQuestion**：任务中途暂停，向用户提出澄清问题。
  支持可选 options 列表。

## MCP（模型上下文协议）
来自 MCP 服务的工具以 mcp__<server>__<tool> 形式提供。
使用 /mcp 查看已配置服务及其状态。

# 输出风格
直接明了。以答案或行动开头，而非推理过程。
工具调用之间的描述不超过 25 个词。
无后续工具调用的最终回复不超过 100 个词。
不重复用户内容，不使用冗余语句。\
"""


# ══════════════════════════════════════════════════════════════════════════════
# 动态部分 —— 每次调用重新生成（当前目录、日期、Git、CLAUDE.md 等）
# ══════════════════════════════════════════════════════════════════════════════

_DYNAMIC_BOUNDARY = "\n\n__SYSTEM_PROMPT_DYNAMIC_BOUNDARY__\n"

_DYNAMIC_TEMPLATE = """\
# 环境信息
- 日期：{date}
- 工作目录：{cwd}
- 系统平台：{platform}
- Shell：{shell}{git_info}{claude_md}\
"""


# ── 工具函数 ───────────────────────────────────────────────────────

def get_git_info() -> str:
    """若位于 Git 仓库，返回分支/状态/日志摘要"""
    try:
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            stderr=subprocess.DEVNULL, text=True,
        ).strip()
        status = subprocess.check_output(
            ["git", "status", "--short"],
            stderr=subprocess.DEVNULL, text=True,
        ).strip()
        log = subprocess.check_output(
            ["git", "log", "--oneline", "-5"],
            stderr=subprocess.DEVNULL, text=True,
        ).strip()
        parts = [f"\n- Git 分支：{branch}"]
        if status:
            lines = status.split("\n")[:10]
            parts.append("- Git 状态：\n" + "\n".join(f"  {l}" for l in lines))
        if log:
            parts.append("- 最近提交：\n" + "\n".join(f"  {l}" for l in log.split("\n")))
        return "\n" + "\n".join(parts) + "\n"
    except Exception:
        return ""


def get_claude_md() -> str:
    """从 ~/.claude/ 和当前目录（向上递归查找）加载 CLAUDE.md"""
    parts: list[str] = []

    global_md = Path.home() / ".claude" / "CLAUDE.md"
    if global_md.exists():
        try:
            parts.append(f"[全局 CLAUDE.md]\n{global_md.read_text()}")
        except Exception:
            pass

    p = Path.cwd()
    for _ in range(10):
        candidate = p / "CLAUDE.md"
        if candidate.exists():
            try:
                parts.append(f"[项目 CLAUDE.md：{candidate}]\n{candidate.read_text()}")
            except Exception:
                pass
            break
        parent = p.parent
        if parent == p:
            break
        p = parent

    if not parts:
        return ""
    return "\n\n# CLAUDE.md\n" + "\n\n".join(parts) + "\n"


def get_platform_hints() -> str:
    """为 Windows 用户返回系统专属提示"""
    if _platform.system() == "Windows":
        return (
            "\n\n## Windows 终端说明\n"
            "使用 type 替代 cat，dir /s /b 替代 find，"
            "del 替代 rm。复杂文本处理优先使用 PowerShell。"
        )
    return ""


# ── 主构建函数 ───────────────────────────────────────────────────────────

def build_system_prompt(config: dict | None = None) -> str:
    """组装完整系统提示词

    结构：
        [静态部分 —— 可缓存]
        __SYSTEM_PROMPT_DYNAMIC_BOUNDARY__
        [动态部分 —— 每次重新生成]
        [记忆 —— 存在时注入]
        [规划模式 —— 激活时注入]
    """
    cfg = config or {}

    # ── 动态部分 ────────────────────────────────────────────────────
    shell = os.environ.get("SHELL", os.environ.get("COMSPEC", "unknown"))
    dynamic = _DYNAMIC_TEMPLATE.format(
        date=datetime.now().strftime("%Y-%m-%d %A"),
        cwd=str(Path.cwd()),
        platform=_platform.system(),
        shell=shell,
        git_info=get_git_info(),
        claude_md=get_claude_md(),
    )
    dynamic += get_platform_hints()

    # ── 记忆索引（动态） ─────────────────────────────────────────────
    # MEMORY.md 索引：让模型知晓可用记忆
    memory_ctx = get_memory_context()
    if memory_ctx:
        dynamic += f"\n\n# 记忆索引\n{memory_ctx}\n"

    # 已检索的记忆内容：当前上下文选中的完整记忆文本
    retrieved = cfg.get("_retrieved_memories", "")
    if retrieved:
        dynamic += f"\n\n# 已检索记忆（根据当前上下文筛选）\n{retrieved}\n"

    # ── 计划限制层附加内容（独立于 permission_mode）────────────────────────
    if is_plan_mode(cfg):
        plan_file = get_plan_file(cfg)
        dynamic += (
            "\n\n# 计划模式（已激活）\n"
            "计划限制层当前处于激活状态（不影响基础 permission_mode）：\n"
            "- 仅允许使用只读工具：Read、Glob、Grep、WebFetch、WebSearch\n"
            f"- 仅允许写入计划文件：{plan_file}\n"
            "- 禁止对其他文件执行 Write/Edit\n"
            "- 使用 TaskCreate 将计划拆分为可跟踪步骤\n"
            "- 完成后调用 ExitPlanMode 或由用户执行 /plan done\n"
        )

    return SYSTEM_PROMPT_STATIC + _DYNAMIC_BOUNDARY + dynamic