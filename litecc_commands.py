"""litecc 的命令层辅助模块。

这个模块负责：
- 维护 `/命令` 到处理函数的注册表
- 统一处理斜杠命令分发
- 提供 readline Tab 补全

这样主入口只需要关心“何时调用命令层”，
而不用在 `litecc.py` 里再维护一大段补全和匹配逻辑。
"""

from __future__ import annotations

import atexit
from pathlib import Path
from typing import Callable

try:
    import readline
except ImportError:
    readline = None

from skill import find_skill
from litecc_ui import clr


# 命令处理函数签名：接收参数字符串、状态对象和配置字典。
Handler = Callable[[str, object, dict], object]


class CommandRegistry:
    """斜杠命令注册表。"""

    def __init__(self) -> None:
        # `_handlers` 保存命令名到处理函数的映射。
        self._handlers: dict[str, Handler] = {}
        # `_meta` 保存补全和帮助展示所需的描述信息。
        self._meta: dict[str, tuple[str, list[str]]] = {}

    def register(
        self,
        name: str,
        handler: Handler,
        description: str = "",
        subcommands: list[str] | None = None,
    ) -> None:
        """注册一个斜杠命令。"""
        self._handlers[name] = handler
        self._meta[name] = (description, subcommands or [])

    def handler_names(self) -> list[str]:
        """返回当前已注册的命令名列表。"""
        return list(self._handlers.keys())

    def handle_slash(self, line: str, state, config):
        """处理 `/命令 参数` 这一类输入。

        返回约定：
        - `False`：不是斜杠命令
        - `None`：是斜杠命令，但没有找到处理器
        - 其他：由具体处理器返回
        """
        if not line.startswith("/"):
            return False
        parts = line[1:].split(None, 1)
        if not parts:
            return False

        cmd = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""
        handler = self._handlers.get(cmd)
        if handler:
            return handler(args, state, config)

        # 没有命中普通命令时，再尝试按 skill trigger 匹配。
        skill = find_skill(line)
        if skill:
            cmd_parts = line.strip().split(maxsplit=1)
            skill_args = cmd_parts[1] if len(cmd_parts) > 1 else ""
            return (skill, skill_args)

        return None

    def setup_readline(self, history_file: Path) -> None:
        """初始化 readline 历史和 Tab 补全。"""
        if readline is None:
            return
        try:
            readline.read_history_file(str(history_file))
        except FileNotFoundError:
            pass
        except OSError:
            # 某些平台的 libedit 对非 ASCII 历史文件兼容不好，失败时重建空文件。
            try:
                history_file.write_text("", encoding="utf-8")
            except Exception:
                pass

        readline.set_history_length(1000)
        atexit.register(readline.write_history_file, str(history_file))

        # 允许 `/model` 这种形式整体参与补全。
        delims = readline.get_completer_delims().replace("/", "")
        readline.set_completer_delims(delims)

        def completer(text: str, state: int):
            line = readline.get_line_buffer()

            # 第一层补全：补全命令名。
            if "/" in line and " " not in line:
                matches = sorted(f"/{c}" for c in self._meta if f"/{c}".startswith(text))
                return matches[state] if state < len(matches) else None

            # 第二层补全：补全子命令。
            if line.startswith("/") and " " in line:
                cmd = line.split()[0][1:]
                if cmd in self._meta:
                    subs = self._meta[cmd][1]
                    matches = sorted(s for s in subs if s.startswith(text))
                    return matches[state] if state < len(matches) else None

            return None

        def display_matches(substitution: str, matches: list[str], longest: int) -> None:
            """自定义补全候选的展示格式。"""
            sys_stdout = __import__("sys").stdout
            sys_stdout.write("\n")
            line = readline.get_line_buffer()
            is_cmd = "/" in line and " " not in line

            if is_cmd:
                col_w = max(len(m) for m in matches) + 2
                for m in sorted(matches):
                    cmd = m[1:]
                    desc, subs = self._meta.get(cmd, ("", []))
                    sub_hint = (
                        "  [" + ", ".join(subs[:4]) + ("..." if len(subs) > 4 else "") + "]"
                    ) if subs else ""
                    sys_stdout.write(f"  {clr(f'{m:<{col_w}}', 'cyan')}  {desc}{sub_hint}\n")
            else:
                for m in sorted(matches):
                    sys_stdout.write(f"  {m}\n")
            sys_stdout.flush()

        readline.set_completion_display_matches_hook(display_matches)
        readline.set_completer(completer)
        readline.parse_and_bind("tab: complete")
