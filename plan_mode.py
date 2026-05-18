"""计划模式状态管理，与 permission_mode 相互独立。

plan_mode 是一个运行时叠加层，只允许向计划文件写入内容。
它与 permission_mode（'auto' | 'manual' | 'accept-all'）完全分离。

核心原则：
  - config["permission_mode"] 只负责权限策略本身。
  - config["_plan_mode_active"] 表示计划模式叠加层是否启用。
  - 进入或退出计划模式都不会修改 permission_mode。
"""
from __future__ import annotations

import os
from pathlib import Path


def is_plan_mode(config: dict) -> bool:
    """返回当前是否已启用计划模式叠加层。"""
    return bool(config.get("_plan_mode_active"))


def get_plan_file(config: dict) -> str:
    """返回当前计划文件路径；如果不存在则返回空字符串。"""
    return config.get("_plan_file", "")


def is_plan_file_target(config: dict, target: str) -> bool:
    """判断目标路径在规范化后是否与当前计划文件一致。"""
    plan_file = get_plan_file(config)
    if not plan_file or not target:
        return False
    return os.path.normpath(target) == os.path.normpath(plan_file)


def enter_plan_mode(config: dict, task_description: str = "") -> tuple[str, str]:
    """启用计划模式叠加层。

    - 不会修改 config["permission_mode"]。
    - 若计划文件不存在，则创建 .nano_claude/plans/<session_id>.md。
    - 返回 (message, plan_file_path)。
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

    # 记录进入计划模式前的 permission_mode，仅用于提示展示；
    # 实际上不会改动 permission_mode 本身。
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
    """停用计划模式叠加层。

    - 不会恢复 permission_mode，因为它从未被修改过。
    - 清除 _plan_mode_active，但保留 _plan_file，方便 /plan 查看历史。
    - 返回 (message, plan_content)。
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

    # 停用计划模式，但保持 permission_mode 完全不变。
    config["_plan_mode_active"] = False
    config.pop("_plan_task", None)
    # 有意保留 _plan_file，这样 /plan（无参数）仍能展示该文件。

    message = (
        f"计划限制层已停用。\n"
        f"基础权限策略仍为：{prev_perm}\n"
        f"计划文件：{plan_file}\n\n"
        f"计划已准备好供用户审核，请等待用户批准后开始实施。\n\n"
        f"--- 计划内容 ---\n{plan_content}"
    )
    return message, plan_content
