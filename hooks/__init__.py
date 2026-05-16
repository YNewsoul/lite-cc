"""Hooks system: 根据智能体生命周期事件，执行外部命令。"""
from .dispatcher import (  # noqa: F401
    fire_pre_tool,
    fire_post_tool,
    fire_stop,
    fire_notification,
    fire_pre_compact,
)
