"""litecc 的终端界面辅助模块。

这个模块集中处理：
- ANSI 颜色包装
- Rich 实时流式渲染
- 工具执行时的加载动画
- 工具开始/结束事件的终端展示

这样主入口 `litecc.py` 就可以更专注在交互流程编排上，
而不是同时承担渲染细节。
"""

from __future__ import annotations

import json
import sys
import threading

try:
    from rich.console import Console
    from rich.live import Live
    from rich.markdown import Markdown

    RICH_AVAILABLE = True
    console = Console()
except ImportError:
    RICH_AVAILABLE = False
    console = None


# 统一的 ANSI 颜色表。
C = {
    "cyan": "\033[36m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "red": "\033[31m",
    "blue": "\033[34m",
    "magenta": "\033[35m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "reset": "\033[0m",
}


def clr(text: str, *keys: str) -> str:
    """给文本套上 ANSI 颜色样式。"""
    return "".join(C[k] for k in keys) + str(text) + C["reset"]


def info(msg: str) -> None:
    """输出普通提示信息。"""
    print(clr(msg, "cyan"))


def ok(msg: str) -> None:
    """输出成功信息。"""
    print(clr(msg, "green"))


def warn(msg: str) -> None:
    """输出警告信息。"""
    print(clr(f"警告: {msg}", "yellow"))


def err(msg: str) -> None:
    """输出错误信息。"""
    print(clr(f"错误: {msg}", "red"), file=sys.stderr)


def print_welcome_banner(
    version: str,
    model: str,
    provider_name: str,
    permission_mode: str,
    plan_active: bool,
    verbose_enabled: bool,
    thinking_enabled: bool,
) -> None:
    """Print the interactive welcome banner and active runtime flags."""
    model_clr = clr(model, "cyan", "bold")
    prov_clr = clr(f"({provider_name})", "dim")
    pmode = clr(permission_mode, "yellow")
    ver_clr = clr(f"v{version}", "green")
    plan_suffix = clr(" [计划模式]", "magenta", "bold") if plan_active else ""

    print(
        clr("  ╭─ ", "dim")
        + clr("litecc ", "cyan", "bold")
        + ver_clr
        + clr(" ─────────────────────────────────╮", "dim")
    )
    print(clr("  │", "dim") + clr("  模型: ", "dim") + model_clr + " " + prov_clr)
    print(clr("  │", "dim") + clr("  权限: ", "dim") + pmode + plan_suffix)
    print(clr("  │", "dim") + clr("  /model 切换模型 · /help 查看命令", "dim"))
    print(clr("  ╰──────────────────────────────────────────────────────╯", "dim"))

    active_flags: list[str] = []
    if verbose_enabled:
        active_flags.append("详细模式")
    if thinking_enabled:
        active_flags.append("扩展思考")
    if active_flags:
        flags_str = " · ".join(clr(flag, "green") for flag in active_flags)
        info(f"已激活: {flags_str}")
    print()


def render_diff(text: str) -> None:
    """把 unified diff 按颜色打印到终端。"""
    for line in text.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            print(C["bold"] + line + C["reset"])
        elif line.startswith("+"):
            print(C["green"] + line + C["reset"])
        elif line.startswith("-"):
            print(C["red"] + line + C["reset"])
        elif line.startswith("@@"):
            print(C["cyan"] + line + C["reset"])
        else:
            print(line)


def has_diff(text: str) -> bool:
    """粗略判断工具结果里是否包含标准 diff。"""
    return "--- a/" in text and "+++ b/" in text


# Rich 流式渲染的运行时状态。
_accumulated_text: list[str] = []
_current_live: Live | None = None
_rich_live_enabled = True


def set_rich_live(enabled: bool) -> None:
    """设置是否启用 Rich 原地实时渲染。"""
    global _rich_live_enabled
    _rich_live_enabled = enabled


def is_rich_live_enabled() -> bool:
    """读取当前的 Rich 原地实时渲染开关。"""
    return _rich_live_enabled


def _make_renderable(text: str):
    """把文本转换成 Rich 可渲染对象。

    如果文本里看起来包含 Markdown 标记，就交给 `Markdown` 渲染；
    否则直接按普通文本显示。
    """
    if any(c in text for c in ("#", "*", "`", "_", "[")):
        return Markdown(text)
    return text


def _start_live() -> None:
    """启动 Rich 的 Live 渲染会话。"""
    global _current_live
    if RICH_AVAILABLE and _rich_live_enabled and _current_live is None:
        _current_live = Live(console=console, auto_refresh=False, vertical_overflow="visible")
        _current_live.start()


def stream_text(chunk: str) -> None:
    """流式输出普通文本。

    如果启用了 Rich，就原地刷新整段响应；
    否则直接把分片打印到 stdout。
    """
    _accumulated_text.append(chunk)
    if RICH_AVAILABLE and _rich_live_enabled:
        if _current_live is None:
            _start_live()
        assert _current_live is not None
        _current_live.update(_make_renderable("".join(_accumulated_text)), refresh=True)
    else:
        print(chunk, end="", flush=True)


def stream_thinking(chunk: str, verbose: bool) -> None:
    """流式输出思考内容。

    这里只在详细模式下显示，并把分片中的换行压平成空格，
    避免逐 token 输出时把终端打乱。
    """
    if verbose:
        clean_chunk = chunk.replace("\n", " ")
        if clean_chunk:
            print(f"{C['dim']}{clean_chunk}", end="", flush=True)


def flush_response() -> None:
    """结束当前响应的流式渲染并收尾。"""
    global _current_live
    full = "".join(_accumulated_text)
    _accumulated_text.clear()
    if _current_live is not None:
        _current_live.stop()
        _current_live = None
    elif RICH_AVAILABLE and _rich_live_enabled and full.strip():
        console.print(_make_renderable(full))
    else:
        print()


# 工具执行期间轮播的文案。
_TOOL_SPINNER_PHRASES = [
    "正在分析代码上下文...",
    "正在整理工具结果...",
    "正在同步终端输出...",
    "正在拼装下一轮上下文...",
    "正在刷新代理状态...",
    "正在推进当前任务...",
]

_tool_spinner_thread = None
_tool_spinner_stop = threading.Event()
_spinner_phrase = ""
_spinner_lock = threading.Lock()


def _run_tool_spinner() -> None:
    """后台线程：循环刷新终端中的加载动画。"""
    chars = "|/-\\"
    i = 0
    while not _tool_spinner_stop.is_set():
        with _spinner_lock:
            phrase = _spinner_phrase
        frame = chars[i % len(chars)]
        sys.stdout.write(f"\r  {frame} {clr(phrase, 'dim')}   ")
        sys.stdout.flush()
        i += 1
        _tool_spinner_stop.wait(0.1)


def start_tool_spinner() -> None:
    """启动工具执行动画。"""
    global _tool_spinner_thread
    if _tool_spinner_thread and _tool_spinner_thread.is_alive():
        return

    import random

    with _spinner_lock:
        global _spinner_phrase
        _spinner_phrase = random.choice(_TOOL_SPINNER_PHRASES)
    _tool_spinner_stop.clear()
    _tool_spinner_thread = threading.Thread(target=_run_tool_spinner, daemon=True)
    _tool_spinner_thread.start()


def change_spinner_phrase() -> None:
    """在不停止动画的情况下切换文案。"""
    import random

    with _spinner_lock:
        global _spinner_phrase
        _spinner_phrase = random.choice(_TOOL_SPINNER_PHRASES)


def stop_tool_spinner() -> None:
    """停止工具执行动画并清空当前终端行。"""
    global _tool_spinner_thread
    if not _tool_spinner_thread:
        return
    _tool_spinner_stop.set()
    _tool_spinner_thread.join(timeout=1)
    _tool_spinner_thread = None
    sys.stdout.write(f"\r{' ' * 50}\r")
    sys.stdout.flush()


def print_tool_start(name: str, inputs: dict, verbose: bool) -> None:
    """打印工具开始执行时的提示。"""
    desc = _tool_desc(name, inputs)
    print(clr(f"  -> {desc}", "dim", "cyan"), flush=True)
    if verbose:
        preview = json.dumps(inputs, ensure_ascii=False)[:200]
        print(clr(f"     输入参数: {preview}", "dim"))


def print_tool_end(name: str, result: str, verbose: bool) -> None:
    """打印工具结束后的摘要结果。"""
    lines = result.count("\n") + 1
    size = len(result)
    summary = f"-> {lines} 行 ({size} 字符)"
    if not result.startswith("Error") and not result.startswith("Denied"):
        print(clr(f"  OK {summary}", "dim", "green"), flush=True)
        if name in ("Edit", "Write") and has_diff(result):
            parts = result.split("\n\n", 1)
            if len(parts) == 2:
                print(clr(f"  {parts[0]}", "dim"))
                render_diff(parts[1])
    else:
        print(clr(f"  XX {result[:120]}", "dim", "red"), flush=True)

    if verbose and not result.startswith("Denied"):
        preview = result[:500] + ("..." if len(result) > 500 else "")
        print(clr(f"     {preview.replace(chr(10), chr(10) + '     ')}", "dim"))


def _tool_desc(name: str, inputs: dict) -> str:
    """把工具名和参数转换成适合终端展示的短描述。"""
    if name == "Read":
        return f"读取({inputs.get('file_path', '')})"
    if name == "Write":
        return f"写入({inputs.get('file_path', '')})"
    if name == "Edit":
        return f"编辑({inputs.get('file_path', '')})"
    if name == "Bash":
        return f"执行命令({inputs.get('command', '')[:80]})"
    if name == "Glob":
        return f"文件匹配({inputs.get('pattern', '')})"
    if name == "Grep":
        return f"文本搜索({inputs.get('pattern', '')})"
    if name == "WebFetch":
        return f"网页获取({inputs.get('url', '')[:60]})"
    if name == "WebSearch":
        return f"网页搜索({inputs.get('query', '')})"
    if name == "Agent":
        atype = inputs.get("subagent_type", "")
        aname = inputs.get("name", "")
        iso = inputs.get("isolation", "")
        bg = not inputs.get("wait", True)
        parts = []
        if atype:
            parts.append(atype)
        if aname:
            parts.append(f"名称={aname}")
        if iso:
            parts.append(f"隔离={iso}")
        if bg:
            parts.append("后台")
        suffix = f"({', '.join(parts)})" if parts else ""
        prompt_short = inputs.get("prompt", "")[:60]
        return f"代理{suffix}: {prompt_short}"
    if name == "SendMessage":
        return f"发送消息(接收方={inputs.get('to', '')}: {inputs.get('message', '')[:50]})"
    if name == "CheckAgentResult":
        return f"检查代理结果({inputs.get('task_id', '')})"
    if name == "ListAgentTasks":
        return "列出代理任务()"
    if name == "ListAgentTypes":
        return "列出代理类型()"
    return f"{name}({list(inputs.values())[:1]})"
