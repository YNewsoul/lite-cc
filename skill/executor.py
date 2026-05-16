"""Skill execution: inline only (current conversation)."""
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
    """Execute a skill inline in the current conversation.

    Args:
        skill: SkillDef to execute
        args: raw argument string from user (after the trigger word)
        state: AgentState
        config: config dict
        system_prompt: current system prompt string
    Yields:
        agent events (TextChunk, ToolStart, ToolEnd, TurnDone, …)
    """
    rendered = substitute_arguments(skill.prompt, args, skill.arguments)
    message = f"[Skill: {skill.name}]\n\n{rendered}"
    yield from _execute_inline(message, state, config, system_prompt)


def _execute_inline(message: str, state, config: dict, system_prompt: str) -> Generator:
    """Run skill prompt inline in the current conversation."""
    import agent as _agent
    yield from _agent.run(message, state, config, system_prompt)
