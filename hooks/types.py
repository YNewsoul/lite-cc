"""钩子系统使用的数据类。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class HookCommand:
    """单个钩子动作 —— 目前仅支持 'command' 类型。"""
    type: str       # "command"
    command: str


@dataclass
class HookMatcher:
    """用于一个钩子事件条目的匹配规则 + 钩子命令列表。"""
    matcher: str                       # "" 或 "*" = 匹配所有，否则为工具名前缀
    hooks: list[HookCommand] = field(default_factory=list)


@dataclass
class HooksConfig:
    """从 settings.json 解析出的钩子配置。"""
    pre_tool_use:  list[HookMatcher] = field(default_factory=list)
    post_tool_use: list[HookMatcher] = field(default_factory=list)
    stop:          list[HookMatcher] = field(default_factory=list)
    notification:  list[HookMatcher] = field(default_factory=list)
    pre_compact:   list[HookMatcher] = field(default_factory=list)


@dataclass
class HookDecision:
    """工具执行前钩子返回的决策结果。"""
    decision: Literal["block", "approve", "ask"] = "ask"
    reason: str = ""