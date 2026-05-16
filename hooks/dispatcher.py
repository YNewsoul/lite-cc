"""将钩子事件分发给配置的 Shell 命令。

钩子事件函数：
  fire_pre_tool(tool_name, tool_input, session_id, cwd) -> HookDecision
  fire_post_tool(tool_name, tool_input, tool_response, session_id, cwd) -> None
  fire_stop(stop_reason, session_id, cwd) -> None
  fire_notification(message, session_id, cwd) -> None
  fire_pre_compact(messages_count, token_count, session_id, cwd) -> None
"""
from __future__ import annotations

from .loader import get_hooks_config
from .executor import run_hook
from .types import HookDecision, HookMatcher


# ── 匹配器 ────────────────────────────────────────────────────────────────

def _matches(matcher: str, tool_name: str) -> bool:
    """判断匹配规则是否适用于当前工具名。

    规则：
      - "" 或 "*" → 匹配所有内容
      - 其他情况 → 完全相等 或 工具名以匹配规则开头
    """
    if not matcher or matcher == "*":
        return True
    return tool_name == matcher or tool_name.startswith(matcher)


# ── 工具执行前钩子 ──────────────────────────────────────────────────────────

def fire_pre_tool(
    tool_name: str,     # 要调用的工具名字
    tool_input: dict,   # 工具的参数
    session_id: str,    # 会话ID
    cwd: str,           # 当前目录
) -> HookDecision:
    """运行所有匹配的 PreToolUse 钩子，并返回最终决策结果。

    决策优先级：
      - 第一个 'block' 响应 → 立即返回阻止（中止工具调用）
      - 任意 'approve' 响应 → 返回批准（跳过权限提示）
      - 无决策或 'ask' → 返回询问（执行正常权限流程）
    """
    cfg = get_hooks_config(cwd)
    stdin_data = {
        "session_id": session_id,
        "tool_name": tool_name,
        "tool_input": tool_input,
        "cwd": cwd,
    }

    approved = False
    for hm in cfg.pre_tool_use:
        if not _matches(hm.matcher, tool_name):
            continue
        for hcmd in hm.hooks:
            result = run_hook(hcmd.command, stdin_data)
            if result is None:
                continue
            decision = result.get("decision", "ask")
            if decision == "block":
                return HookDecision(decision="block", reason=result.get("reason", ""))
            if decision == "approve":
                approved = True

    if approved:
        return HookDecision(decision="approve")
    return HookDecision(decision="ask")


# ── 工具执行后钩子 ─────────────────────────────────────────────────────────

def fire_post_tool(
    tool_name: str,
    tool_input: dict,
    tool_response: dict,
    session_id: str,
    cwd: str,
) -> None:
    """运行所有匹配的 PostToolUse 钩子（触发后无需等待，不影响决策）。"""
    cfg = get_hooks_config(cwd) # 读取当前目录下的钩子配置文件（哪些工具要触发后置操作）
    stdin_data = {
        "session_id": session_id,
        "tool_name": tool_name,
        "tool_input": tool_input,
        "tool_response": tool_response,
        "cwd": cwd,
    }
    for hm in cfg.post_tool_use:
        if not _matches(hm.matcher, tool_name):
            continue
        for hcmd in hm.hooks:
            run_hook(hcmd.command, stdin_data)


# ── 停止事件钩子 ──────────────────────────────────────────────────────────────

def fire_stop(stop_reason: str, session_id: str, cwd: str) -> None:
    """在智能体回合完成后，运行所有 Stop 钩子。"""
    cfg = get_hooks_config(cwd)
    stdin_data = {
        "session_id": session_id,
        "stop_reason": stop_reason,
        "cwd": cwd,
    }
    for hm in cfg.stop:
        for hcmd in hm.hooks:
            run_hook(hcmd.command, stdin_data)


# ── 通知事件钩子 ──────────────────────────────────────────────────────

def fire_notification(message: str, session_id: str, cwd: str) -> None:
    """运行所有 Notification 钩子（例如用于权限提示）。"""
    cfg = get_hooks_config(cwd)
    stdin_data = {
        "session_id": session_id,
        "message": message,
        "cwd": cwd,
    }
    for hm in cfg.notification:
        for hcmd in hm.hooks:
            run_hook(hcmd.command, stdin_data)


# ── 上下文压缩前钩子 ───────────────────────────────────────────────────────

def fire_pre_compact(
    messages_count: int,
    token_count: int,
    session_id: str,
    cwd: str,
) -> None:
    """在上下文压缩之前，运行所有 PreCompact 钩子。"""
    cfg = get_hooks_config(cwd)
    stdin_data = {
        "session_id": session_id,
        "messages_count": messages_count,
        "token_count": token_count,
        "cwd": cwd,
    }
    for hm in cfg.pre_compact:
        for hcmd in hm.hooks:
            run_hook(hcmd.command, stdin_data)