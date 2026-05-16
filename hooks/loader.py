"""从 .claude/settings.json 文件加载钩子配置。

搜索顺序（优先级从低到高 —— 后加载的值会覆盖先加载的）：
  1. ~/.claude/settings.json         （用户级默认配置）
  2. <cwd>/.claude/settings.json     （项目级配置，通过向上遍历目录查找）

项目级配置优先级高于用户级配置。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from .types import HooksConfig, HookMatcher, HookCommand


# ── 配置文件搜索 ───────────────────────────────────────────────────

def _find_project_settings(cwd: str) -> Path | None:
    """从当前工作目录向上遍历，查找 .claude/settings.json 文件。"""
    current = Path(cwd).resolve()
    while True:
        candidate = current / ".claude" / "settings.json"
        if candidate.exists():
            return candidate
        parent = current.parent
        if parent == current:
            return None
        current = parent


def load_settings_json(cwd: str) -> dict:
    """加载并合并用户级和项目级的 settings.json 文件。

    返回合并后的字典；项目级键会覆盖用户级键。
    如果两个文件都不存在，返回空字典 {}。
    """
    merged: dict = {}

    # 1. 先加载用户级基础配置
    user_settings = Path.home() / ".claude" / "settings.json"
    if user_settings.exists():
        try:
            merged.update(json.loads(user_settings.read_text(encoding="utf-8")))
        except Exception:
            pass

    # 2. 再加载项目级配置，进行覆盖
    project_settings = _find_project_settings(cwd)
    if project_settings:
        try:
            proj = json.loads(project_settings.read_text(encoding="utf-8"))
            # 深度合并 'hooks' 部分；其他键直接覆盖
            if "hooks" in proj and "hooks" in merged:
                for event_key, matchers in proj["hooks"].items():
                    merged["hooks"][event_key] = matchers
            else:
                merged.update(proj)
        except Exception:
            pass

    return merged


# ── 配置解析 ─────────────────────────────────────────────────────────────────

def _parse_matchers(raw_list: list) -> list[HookMatcher]:
    """解析原始列表，生成 {matcher, hooks:[{type, command}]} 字典列表。"""
    result = []
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        matcher = item.get("matcher", "")
        raw_hooks = item.get("hooks", [])
        commands = []
        for h in raw_hooks:
            if isinstance(h, dict) and h.get("type") == "command":
                commands.append(HookCommand(type="command", command=h.get("command", "")))
        result.append(HookMatcher(matcher=matcher, hooks=commands))
    return result


def parse_hooks_config(raw: dict) -> HooksConfig:
    """将配置字典中的 'hooks' 部分解析为 HooksConfig 对象。"""
    hooks_raw = raw.get("hooks", {})
    if not isinstance(hooks_raw, dict):
        return HooksConfig()
    return HooksConfig(
        pre_tool_use=_parse_matchers(hooks_raw.get("PreToolUse", [])),
        post_tool_use=_parse_matchers(hooks_raw.get("PostToolUse", [])),
        stop=_parse_matchers(hooks_raw.get("Stop", [])),
        notification=_parse_matchers(hooks_raw.get("Notification", [])),
        pre_compact=_parse_matchers(hooks_raw.get("PreCompact", [])),
    )


# ── 带缓存的入口函数 ─────────────────────────────────────────────────────

@lru_cache(maxsize=16)
def get_hooks_config(cwd: str) -> HooksConfig:
    """返回指定工作目录对应的解析后钩子配置（结果按目录缓存）。"""
    raw = load_settings_json(cwd)
    return parse_hooks_config(raw)