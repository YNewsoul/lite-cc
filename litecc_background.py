"""litecc 的后台任务辅助模块。

这里集中放两类“非主链路但需要在入口侧触发”的工作：
- 会话结束后的记忆提取与记忆整合
- 后台子代理完成后的通知打印
"""

from __future__ import annotations

import time as _time


# 已经提示过的后台任务 ID，避免同一任务反复打印。
_seen_task_ids: set[str] = set()


def trigger_session_end_memory(state, config: dict, start_time: float | None = None) -> None:
    """在会话结束时触发记忆相关后台工作。"""
    try:
        from memory.auto_extractor import maybe_extract_memories
        from memory.dream import increment_session_count, maybe_run_dream

        t0 = start_time or config.get("_session_start_time", _time.monotonic())
        maybe_extract_memories(
            messages=list(state.messages),
            config=config,
            session_start_time=t0,
            turn_count=getattr(state, "turn_count", 0),
        )
        increment_session_count()
        maybe_run_dream(config)
    except Exception:
        # 这里是退出链路上的辅助能力，不应该因为异常阻塞主程序退出。
        pass


def print_background_notifications(clr_fn, print_fn=print) -> None:
    """打印已完成后台代理的简要通知。"""
    try:
        from multi_agent.tools import get_agent_manager

        mgr = get_agent_manager()
    except Exception:
        return

    for task in mgr.list_tasks():
        if task.id in _seen_task_ids:
            continue
        if task.status in ("completed", "failed", "cancelled"):
            _seen_task_ids.add(task.id)
            icon = "OK" if task.status == "completed" else "XX"
            color = "green" if task.status == "completed" else "red"
            branch_info = f" [分支: {task.worktree_branch}]" if task.worktree_branch else ""
            print_fn(clr_fn(f"\n  {icon} 后台代理 '{task.name}' {task.status}{branch_info}", color, "bold"))
            if task.result:
                preview = task.result[:200] + ("..." if len(task.result) > 200 else "")
                print_fn(clr_fn(f"    {preview}", "dim"))
            print_fn("")
