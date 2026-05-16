"""Plan mode state management — independent of permission_mode.

plan_mode is a runtime overlay that restricts writes to the plan file only.
It is entirely separate from permission_mode ('auto' | 'manual' | 'accept-all').

Key principles:
  - config["permission_mode"] governs permission policy only.
  - config["_plan_mode_active"] indicates whether the plan overlay is active.
  - Entering/exiting plan mode NEVER modifies permission_mode.
"""
from __future__ import annotations

import os
from pathlib import Path


def is_plan_mode(config: dict) -> bool:
    """Return True if the plan mode overlay is currently active."""
    return bool(config.get("_plan_mode_active"))


def get_plan_file(config: dict) -> str:
    """Return the current plan file path (empty string if none)."""
    return config.get("_plan_file", "")


def is_plan_file_target(config: dict, target: str) -> bool:
    """Return True if *target* path matches the active plan file (path-normalised)."""
    plan_file = get_plan_file(config)
    if not plan_file or not target:
        return False
    return os.path.normpath(target) == os.path.normpath(plan_file)


def enter_plan_mode(config: dict, task_description: str = "") -> tuple[str, str]:
    """Activate the plan mode overlay.

    - Does NOT modify config["permission_mode"].
    - Creates .nano_claude/plans/<session_id>.md when it does not yet exist.
    - Returns (message, plan_file_path).
    """
    if is_plan_mode(config):
        return (
            "已处于计划模式。将计划写入文件后调用 ExitPlanMode。",
            get_plan_file(config),
        )

    session_id = config.get("_session_id", "default")
    plans_dir = Path.cwd() / ".nano_claude" / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    plan_path = plans_dir / f"{session_id}.md"

    if not plan_path.exists() or plan_path.stat().st_size == 0:
        header = f"# 计划：{task_description}\n\n" if task_description else "# 计划\n\n"
        plan_path.write_text(header, encoding="utf-8")

    # Remember previous permission mode for display purposes only —
    # we do NOT change permission_mode itself.
    config["_plan_prev_permission_mode"] = config.get("permission_mode", "auto")
    config["_plan_mode_active"] = True
    config["_plan_file"] = str(plan_path)
    config["_plan_task"] = task_description

    perm_mode = config.get("permission_mode", "auto")
    message = (
        f"计划限制层已激活。\n"
        f"当前基础权限策略保持为：{perm_mode}\n"
        f"计划文件：{plan_path}\n\n"
        f"使用说明：\n"
        f"1. 使用 Read、Glob、Grep、WebSearch 分析项目\n"
        f"2. 使用 Write 或 Edit 将详细计划写入计划文件\n"
        f"3. 完成后调用 ExitPlanMode 提交计划供用户审核\n"
        f"4. 其他文件的写入操作将被拦截"
    )
    return message, str(plan_path)


def exit_plan_mode(config: dict, require_nonempty: bool = True) -> tuple[str, str]:
    """Deactivate the plan mode overlay.

    - Does NOT restore permission_mode (it was never changed).
    - Clears _plan_mode_active; keeps _plan_file so /plan can show history.
    - Returns (message, plan_content).
    """
    if not is_plan_mode(config):
        return "未处于计划模式。请先调用 EnterPlanMode。", ""

    plan_file = get_plan_file(config)
    plan_content = ""
    if plan_file:
        p = Path(plan_file)
        if p.exists():
            plan_content = p.read_text(encoding="utf-8").strip()

    if require_nonempty and (not plan_content or plan_content in ("# 计划", "# Plan")):
        return "计划文件为空。请先将计划写入文件，然后再退出。", ""

    prev_perm = config.get("_plan_prev_permission_mode", config.get("permission_mode", "auto"))

    # Deactivate — permission_mode stays exactly as-is
    config["_plan_mode_active"] = False
    config.pop("_plan_task", None)
    # Intentionally keep _plan_file so /plan (no args) can still show the file

    message = (
        f"计划限制层已停用。\n"
        f"基础权限策略仍为：{prev_perm}\n"
        f"计划文件：{plan_file}\n\n"
        f"计划已准备好供用户审核，请等待用户批准后开始实施。\n\n"
        f"--- 计划内容 ---\n{plan_content}"
    )
    return message, plan_content
