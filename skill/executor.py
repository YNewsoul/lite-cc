"""skill 执行：仅以内联方式运行。"""
from __future__ import annotations

from typing import Generator

from .loader import SkillDef, substitute_arguments


def execute_skill(
    skill: SkillDef,
    args: str,
    state,
    config: dict,
    system_prompt: str,
) -> Generator:
    """在当前对话中以内联方式执行一个 skill。

    参数：
        skill: 要执行的 SkillDef
        args: 用户传入的原始参数字符串（去掉 trigger 之后的部分）
        state: AgentState
        config: 配置字典
        system_prompt: 当前系统提示词
    产出：
        agent 事件（TextChunk、ToolStart、ToolEnd、TurnDone 等）
    """
    # skill 不会直接修改 agent 的系统提示词。
    # 它的实现方式是：把 skill 模板渲染成一条新的“伪用户消息”，
    # 然后继续复用现有 agent.run() 流程。
    rendered = substitute_arguments(skill.prompt, args, skill.arguments)
    message = f"[Skill: {skill.name}]\n\n{rendered}"
    yield from _execute_inline(message, state, config, system_prompt)


def _execute_inline(message: str, state, config: dict, system_prompt: str) -> Generator:
    """在当前对话里以内联方式执行渲染后的 skill 消息。"""
    import agent as _agent

    # 直接复用当前会话的 state，因此执行结果会进入同一段对话历史。
    yield from _agent.run(message, state, config, system_prompt)
