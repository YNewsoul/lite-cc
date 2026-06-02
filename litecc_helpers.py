"""Small helpers for litecc's interactive runtime."""

from __future__ import annotations

from tools import ask_input_interactive
import litecc_ui as ui


def ask_permission_interactive(desc: str, config: dict) -> bool:
    """Prompt the user to approve a sensitive tool action."""
    text = ask_input_interactive(
        f"  允许: {desc}  [y/N/a(全部允许)] ",
        config,
    ).strip().lower()

    if text in ("a", "accept all", "accept-all"):
        config["permission_mode"] = "accept-all"
        ui.ok("  本次会话权限模式已设为全部允许。")
        return True

    return text in ("y", "yes")
