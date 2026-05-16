"""核心智能体循环：中立消息格式，多厂商流式输出。"""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from typing import Generator

import time as _time

from tool_registry import get_tool_schemas
from tools import execute_tool
import tools as _tools_init  # 确保导入时注册内置工具
from providers import stream, Response, TextChunk, ThinkingChunk, detect_provider
from compaction import maybe_compact, apply_context_collapse
from hooks.dispatcher import fire_pre_tool, fire_post_tool, fire_stop
from plan_mode import is_plan_mode, get_plan_file, is_plan_file_target

# ── 重新导出事件类型（供 pycc.py 使用）────────────────────────
__all__ = [
    "AgentState", "run",
    "TextChunk", "ThinkingChunk",
    "ToolStart", "ToolEnd", "TurnDone", "PermissionRequest",
]


@dataclass
class AgentState:
    """可变会话状态。消息使用与厂商无关的中立格式。"""
    messages: list = field(default_factory=list)
    total_input_tokens:  int = 0
    total_output_tokens: int = 0
    turn_count: int = 0


@dataclass
class ToolStart:
    name:   str
    inputs: dict

@dataclass
class ToolEnd:
    name:      str
    result:    str
    permitted: bool = True

@dataclass
class TurnDone:
    input_tokens:  int
    output_tokens: int

@dataclass
class PermissionRequest:
    description: str
    granted: bool = False
    _config: dict = field(default_factory=dict)  # agent 内部 config 引用，用于回写 permission_mode


# ── 智能体循环 ─────────────────────────────────────────────────────────────

def run(
    user_message: str,
    state: AgentState,
    config: dict,
    system_prompt: str,
    depth: int = 0,
    cancel_check=None,
) -> Generator:
    """
    多轮智能体循环（生成器）。
    输出：TextChunk | ThinkingChunk | ToolStart | ToolEnd |
          PermissionRequest | TurnDone

    参数：
        depth: 子智能体嵌套深度，顶层为 0
        cancel_check: 可调用对象，返回 True 则提前终止循环
    """
    # 以中立格式添加用户消息
    user_msg = {"role": "user", "content": user_message}
    # 如果存在 /image 命令的待处理图片，附加到消息中
    pending_img = config.pop("_pending_image", None)
    if pending_img:
        user_msg["images"] = [pending_img]
    state.messages.append(user_msg)

    # 将运行时元数据注入配置，让工具（如 Agent）可以访问
    config = {**config, "_depth": depth, "_system_prompt": system_prompt}

    while True:
        if cancel_check and cancel_check():
            return
        state.turn_count += 1
        response: Response | None = None

        # 当接近上下文窗口限制时进行压缩
        maybe_compact(state, config)

        # 计划模式：每 5 轮注入一条简短的只读提醒
        _reminder_injected = False
        if (is_plan_mode(config)
                and state.turn_count % 5 == 0
                and state.turn_count > 0):
            state.messages.append({
                "role": "user",
                "content": (
                    "[System Reminder] You are in Plan Mode. "
                    "You may ONLY use read-only tools "
                    "(Read, Glob, Grep, WebFetch, WebSearch). "
                    "Do NOT write files or execute commands."
                ),
            })
            _reminder_injected = True

        # 记录 API 调用时间（用于微型压缩空闲计时器）
        config["_last_api_call_time"] = _time.time()

        # 读时投影
        messages_for_api = apply_context_collapse(state.messages, config)

        # 从模型厂商流式输出（根据模型名称自动检测）
        for event in stream(
            model=config["model"],
            system=system_prompt,
            messages=messages_for_api,
            tool_schemas=get_tool_schemas(),
            config=config,
        ):
            if isinstance(event, (TextChunk, ThinkingChunk)): # 实时片段 → 立刻抛出去展示
                yield event
            elif isinstance(event, Response): # 完整结果 → 暂时存起来，不展示
                response = event

        if response is None:
            break

        # 记录历史前移除临时的计划模式提醒
        if _reminder_injected:
            if state.messages and state.messages[-1].get("role") == "user":
                state.messages.pop()
            _reminder_injected = False

        # 以中立格式记录助手消息
        asst_msg: dict = {
            "role":       "assistant",
            "content":    response.text,
            "tool_calls": response.tool_calls,
        }
        if response.reasoning_content:
            asst_msg["reasoning_content"] = response.reasoning_content
        state.messages.append(asst_msg)

        state.total_input_tokens  += response.in_tokens
        state.total_output_tokens += response.out_tokens
        yield TurnDone(response.in_tokens, response.out_tokens)

        # 停止钩子（每轮完成后触发）
        _finish_reason = "tool_use" if response.tool_calls else "end_turn"
        fire_stop(_finish_reason, config.get("_session_id", ""), config.get("_cwd", "."))

        if not response.tool_calls:
            break   # 无工具调用 → 单轮对话完成

        # ── 执行工具 ────────────────────────────────────────────────
        for toolcall in response.tool_calls:
            yield ToolStart(toolcall["name"], toolcall["input"])

            # 工具执行前钩子：可阻止或自动批准
            hook_dec = fire_pre_tool(
                toolcall["name"], toolcall.get("input", {}),
                config.get("_session_id", ""), config.get("_cwd", "."),
            )
            if hook_dec.decision == "block":
                result = f"[Blocked by hook: {hook_dec.reason}]" if hook_dec.reason else "[Blocked by hook]"
                yield ToolEnd(toolcall["name"], result, False)
                state.messages.append({
                    "role":         "tool",
                    "tool_call_id": toolcall["id"],
                    "name":         toolcall["name"],
                    "content":      result,
                })
                continue

            # 权限校验（如果钩子已批准则跳过）
            if hook_dec.decision == "approve":
                permitted = True
            else:
                permitted = _check_permission(toolcall, config)
                if not permitted:
                    if is_plan_mode(config):
                        # 计划模式：静默拒绝写入操作（无需用户确认）
                        permitted = False
                    else:
                        req = PermissionRequest(description=_permission_desc(toolcall), _config=config)
                        yield req
                        permitted = req.granted

            if not permitted:
                if is_plan_mode(config):
                    plan_file = get_plan_file(config)
                    result = (
                        f"[Plan mode active] Write operations are blocked except to the plan file: {plan_file}\n"
                        "Finish your analysis and write the plan to the plan file. "
                        "Call ExitPlanMode when done."
                    )
                else:
                    result = "Denied: user rejected this operation"
            else:
                result = execute_tool(
                    toolcall["name"], toolcall["input"],
                    permission_mode="accept-all",  # 已完成权限校验
                    config=config,
                    tool_use_id=toolcall.get("id"),
                )
                # 工具执行后钩子
                fire_post_tool(
                    toolcall["name"], toolcall.get("input", {}), {"result": result},
                    config.get("_session_id", ""), config.get("_cwd", "."),
                )

            yield ToolEnd(toolcall["name"], result, permitted)

            # 以中立格式添加工具执行结果
            state.messages.append({
                "role":         "tool",
                "tool_call_id": toolcall["id"],
                "name":         toolcall["name"],
                "content":      result,
            })


# ── 辅助函数 ───────────────────────────────────────────────────────────────

def _check_permission(toolcall: dict, config: dict) -> bool:
    """如果操作自动批准，则返回 True（无需询问用户）。

    检查顺序：
      1. 计划模式工具 (EnterPlanMode/ExitPlanMode) 始终自动批准
      2. 计划限制层激活时，按只读约束规则判断
      3. 按基础 permission_mode (auto/manual/accept-all) 判断
    """
    name = toolcall["name"]

    # 计划模式工具始终自动批准（不受 permission_mode 影响）
    if name in ("EnterPlanMode", "ExitPlanMode"):
        return True

    # 计划限制层优先于基础权限模式
    if is_plan_mode(config):
        if name in ("Write", "Edit"):
            return is_plan_file_target(config, toolcall["input"].get("file_path", ""))
        if name == "NotebookEdit":
            return False
        if name == "Bash":
            from security.bash_analyzer import analyze_bash, BashRiskLevel
            risk, _ = analyze_bash(toolcall["input"].get("command", ""))
            return risk == BashRiskLevel.safe
        return True  # 读取类工具均允许

    # 基础权限模式
    perm_mode = config.get("permission_mode", "auto")
    if perm_mode == "accept-all":
        return True
    if perm_mode == "manual":
        return False   # 始终询问用户

    # auto 模式：安全的 Bash 命令自动批准；写操作 → 询问用户
    if name in ("Read", "Glob", "Grep", "WebFetch", "WebSearch"):
        return True
    if name == "Bash":
        from security.bash_analyzer import analyze_bash, BashRiskLevel
        risk, _ = analyze_bash(toolcall["input"].get("command", ""))
        return risk == BashRiskLevel.safe
    return False   # Write/Edit/NotebookEdit → 询问用户


def _permission_desc(tc: dict) -> str:
    name = tc["name"]
    inp  = tc["input"]
    if name == "Bash":
        cmd = inp.get("command", "")
        from security.bash_analyzer import analyze_bash, BashRiskLevel
        risk, reason = analyze_bash(cmd)
        if risk == BashRiskLevel.dangerous:
            return f"⚠ DANGEROUS — {reason}\nRun: {cmd}"
        if risk == BashRiskLevel.warn and reason:
            return f"Run: {cmd}\n  ({reason})"
        return f"Run: {cmd}"
    if name == "Write":  return f"Write to: {inp.get('file_path', '')}"
    if name == "Edit":   return f"Edit: {inp.get('file_path', '')}"
    return f"{name}({list(inp.values())[:1]})"