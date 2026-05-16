#!/usr/bin/env python3
"""
pycc — Claude Code 的极简 Python 实现。

使用方法:
  python pycc.py [选项] [提示词]

选项:
  -p, --print          非交互模式: 执行提示词后退出 (同 --print-output)
  -m, --model MODEL    覆盖模型配置
  --accept-all         无需授权确认 (危险操作)
  --verbose            显示思考过程 + 令牌计数
  --version            打印版本信息并退出

交互模式斜杠命令:
  /help       显示帮助信息
  /clear      清空对话记录
  /model [m]  查看或设置模型
  /config     查看配置 / 设置 key=value
  /save [f]   保存会话到文件
  /load [f]   从文件加载会话
  /history    打印对话历史
  /context    显示上下文窗口使用情况
  /cost       显示本次会话的 API 费用
  /verbose    切换详细模式
  /thinking   切换扩展思考模式
  /permissions [mode]  设置权限模式
  /cwd [path] 查看或切换工作目录
  /memory [query]         查看/搜索持久化记忆
  /memory consolidate     通过 AI 从当前会话提取长期洞察
  /skills           列出可用技能
  /agents           显示子代理任务
  /mcp              列出 MCP 服务器及其工具
  /mcp reload       重新连接所有 MCP 服务器
  /mcp add <n> <cmd> [args]  添加标准输入输出 MCP 服务器
  /mcp remove <n>   从配置中移除 MCP 服务器
  /tasks            列出所有任务
  /tasks create <subject>    快速创建任务
  /tasks start/done/cancel <id>  更新任务状态
  /tasks delete <id>         删除任务
  /tasks get <id>            显示任务完整详情
  /tasks clear               删除所有任务
  /exit /quit 退出程序
"""
from __future__ import annotations

import sys
# 检查 Python 版本，低于 3.10 则退出
if sys.version_info < (3, 10):
    sys.exit(
        f"pycc 需要 Python 版本 ≥ 3.10。"
        f"当前检测版本: {sys.version}\n"
        f"提示: 尝试使用 python3.10 或更新版本运行 "
        f"(例如 /opt/miniconda3/bin/python3.13 pycc.py)"
    )

from tools import ask_input_interactive
from plan_mode import is_plan_mode, enter_plan_mode, exit_plan_mode, get_plan_file

import os
import re
import uuid
# Windows 系统下启用 ANSI 转义码支持
if sys.platform == "win32":
    os.system("")
import json
# 导入 readline 用于命令行补全，Windows 兼容处理
try:
    import readline
except ImportError:
    readline = None
import atexit
import argparse
import textwrap
from pathlib import Path
from datetime import datetime
from typing import Optional, Union
import threading

# 可选依赖：用于 Markdown 渲染的 rich 库
try:
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.live import Live
    from rich.syntax import Syntax
    from rich.panel import Panel
    from rich import print as rprint
    _RICH = True
    console = Console()
except ImportError:
    _RICH = False
    console = None

# 版本号
VERSION = "3.05.5"

# ANSI 颜色代码定义（即使使用 rich 也会用于非 Markdown 输出）
C = {
    "cyan":    "\033[36m",
    "green":   "\033[32m",
    "yellow":  "\033[33m",
    "red":     "\033[31m",
    "blue":    "\033[34m",
    "magenta": "\033[35m",
    "bold":    "\033[1m",
    "dim":     "\033[2m",
    "reset":   "\033[0m",
}

# 为文本添加颜色样式
def clr(text: str, *keys: str) -> str:
    return "".join(C[k] for k in keys) + str(text) + C["reset"]

# 日志打印工具函数
def info(msg: str):   print(clr(msg, "cyan"))
def ok(msg: str):     print(clr(msg, "green"))
def warn(msg: str):   print(clr(f"警告: {msg}", "yellow"))
def err(msg: str):    print(clr(f"错误: {msg}", "red"), file=sys.stderr)


# 渲染差异文本，红色表示删除，绿色表示新增
def render_diff(text: str):
    """打印带 ANSI 颜色的差异文本：红色删除，绿色新增。"""
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

# 检查文本是否包含标准格式的差异内容
def _has_diff(text: str) -> bool:
    """检查文本是否包含统一差异格式内容。"""
    return "--- a/" in text and "+++ b/" in text


# 对话渲染相关全局变量
_accumulated_text: list[str] = []   # 流式输出时的文本缓冲区
_current_live: "Live | None" = None  # 活跃的 Rich 实时渲染实例
_RICH_LIVE = True  # 通过配置 rich_live=false 可禁用原地实时流式输出

# 创建可渲染对象：包含标记则返回 Markdown，否则返回纯文本
def _make_renderable(text: str):
    """返回 Rich 可渲染对象：包含标记则用 Markdown，否则用纯文本。"""
    if any(c in text for c in ("#", "*", "`", "_", "[")):
        return Markdown(text)
    return text

# 启动 Rich 实时渲染块（无 Rich 则不执行）
def _start_live() -> None:
    """启动 Rich 实时块用于原地 Markdown 流式渲染（无 Rich 则空操作）。"""
    global _current_live
    if _RICH and _RICH_LIVE and _current_live is None:
        _current_live = Live(console=console, auto_refresh=False,
                             vertical_overflow="visible")
        _current_live.start()

# 流式输出文本片段
def stream_text(chunk: str) -> None:
    """缓冲文本片段；Rich 可用时原地更新实时渲染，否则直接打印。"""
    global _current_live
    _accumulated_text.append(chunk)
    if _RICH and _RICH_LIVE:
        if _current_live is None:
            _start_live()
        _current_live.update(_make_renderable("".join(_accumulated_text)), refresh=True)
    else:
        print(chunk, end="", flush=True)

# 流式输出思考过程（仅详细模式下显示）
def stream_thinking(chunk: str, verbose: bool):
    if verbose:
        # 清理模型逐令牌流式输出时的内部换行符
        clean_chunk = chunk.replace("\n", " ")
        if clean_chunk:
            # 此处不使用 clr() 包装，避免每个令牌后输出重置符导致格式异常
            print(f"{C['dim']}{clean_chunk}", end="", flush=True)

# 刷新响应内容，结束实时渲染
def flush_response() -> None:
    """提交缓冲文本到屏幕：停止实时渲染（固定 Markdown 渲染结果）。"""
    global _current_live
    full = "".join(_accumulated_text)
    _accumulated_text.clear()
    if _current_live is not None:
        _current_live.stop()
        _current_live = None
    elif _RICH and _RICH_LIVE and full.strip():
        # 备用方案：无实时渲染但 Rich 可用时直接渲染
        console.print(_make_renderable(full))
    else:
        print()

# 工具执行加载动画文案
_TOOL_SPINNER_PHRASES = [
    "⚡ 光速重构中...",
    "🏁 与光速赛跑...",
    "🤔 巴里·艾伦是谁？...",
    "🐆 超越编译器...",
    "💨 甩开电子...",
    "🌍 环绕代码库...",
    "⏱️ 突破音障...",
    "🔥 比热重载更快...",
    "🚀 达到终端速度...",
    "🐾 在栈上留下爪痕...",
    "🏎️ 切换6档...",
    "⚡ 速度之力已激活...",
    "🌪️ 闪电般遍历抽象语法树...",
    "💫 扭曲时空...",
    "🐆 Pycc 模式启动...",
]

# 辩论加载动画文案
_DEBATE_SPINNER_PHRASES = [
    "⚔️  专家各就各位...",
    "🧠  专家构建论点中...",
    "🗣️  辩论进行中...",
    "⚖️  权衡证据...",
    "💡  构建反驳论点...",
    "🔥  辩论白热化...",
    "📜  起草共识...",
    "🎯  寻找共同点...",
]

# 工具加载动画线程相关变量
_tool_spinner_thread = None
_tool_spinner_stop = threading.Event()

_spinner_phrase = ""
_spinner_lock = threading.Lock()

# 后台运行工具加载
def _run_tool_spinner():
    chars = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    i = 0
    while not _tool_spinner_stop.is_set():
        with _spinner_lock:
            phrase = _spinner_phrase
        frame = chars[i % len(chars)]
        sys.stdout.write(f"\r  {frame} {clr(phrase, 'dim')}   ")
        sys.stdout.flush()
        i += 1
        _tool_spinner_stop.wait(0.1)

# 启动工具加载动画
def _start_tool_spinner():
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

# 切换加载动画文案（不停止动画）
def _change_spinner_phrase():
    """不停止动画的情况下切换加载文案。"""
    import random
    with _spinner_lock:
        global _spinner_phrase
        _spinner_phrase = random.choice(_TOOL_SPINNER_PHRASES)

# 停止工具加载动画
def _stop_tool_spinner():
    global _tool_spinner_thread
    if not _tool_spinner_thread:
        return
    _tool_spinner_stop.set()
    _tool_spinner_thread.join(timeout=1)
    _tool_spinner_thread = None
    # 清空当前行的动画
    sys.stdout.write(f"\r{' ' * 50}\r")
    sys.stdout.flush()

# 打印工具调用开始信息
def print_tool_start(name: str, inputs: dict, verbose: bool):
    """显示工具调用信息。"""
    desc = _tool_desc(name, inputs)
    print(clr(f"  ⚙  {desc}", "dim", "cyan"), flush=True)
    if verbose:
        print(clr(f"     输入参数: {json.dumps(inputs, ensure_ascii=False)[:200]}", "dim"))

# 打印工具调用结束信息
def print_tool_end(name: str, result: str, verbose: bool):
    lines = result.count("\n") + 1
    size = len(result)
    summary = f"→ {lines} 行 ({size} 字符)"
    if not result.startswith("Error") and not result.startswith("Denied"):
        print(clr(f"  ✓ {summary}", "dim", "green"), flush=True)
        # 为编辑/写入结果渲染差异
        if name in ("Edit", "Write") and _has_diff(result):
            parts = result.split("\n\n", 1)
            if len(parts) == 2:
                print(clr(f"  {parts[0]}", "dim"))
                render_diff(parts[1])
    else:
        print(clr(f"  ✗ {result[:120]}", "dim", "red"), flush=True)
    if verbose and not result.startswith("Denied"):
        preview = result[:500] + ("…" if len(result) > 500 else "")
        print(clr(f"     {preview.replace(chr(10), chr(10)+'     ')}", "dim"))

# 生成工具调用描述
def _tool_desc(name: str, inputs: dict) -> str:
    if name == "Read":   return f"读取({inputs.get('file_path','')})"
    if name == "Write":  return f"写入({inputs.get('file_path','')})"
    if name == "Edit":   return f"编辑({inputs.get('file_path','')})"
    if name == "Bash":   return f"执行命令({inputs.get('command','')[:80]})"
    if name == "Glob":   return f"文件匹配({inputs.get('pattern','')})"
    if name == "Grep":   return f"文本搜索({inputs.get('pattern','')})"
    if name == "WebFetch":    return f"网页获取({inputs.get('url','')[:60]})"
    if name == "WebSearch":   return f"网页搜索({inputs.get('query','')})"
    if name == "Agent":
        atype = inputs.get("subagent_type", "")
        aname = inputs.get("name", "")
        iso   = inputs.get("isolation", "")
        bg    = not inputs.get("wait", True)
        parts = []
        if atype:  parts.append(atype)
        if aname:  parts.append(f"名称={aname}")
        if iso:    parts.append(f"隔离={iso}")
        if bg:     parts.append("后台")
        suffix = f"({', '.join(parts)})" if parts else ""
        prompt_short = inputs.get("prompt", "")[:60]
        return f"代理{suffix}: {prompt_short}"
    if name == "SendMessage":
        return f"发送消息(接收方={inputs.get('to','')}: {inputs.get('message','')[:50]})"
    if name == "CheckAgentResult": return f"检查代理结果({inputs.get('task_id','')})"
    if name == "ListAgentTasks":   return "列出代理任务()"
    if name == "ListAgentTypes":   return "列出代理类型()"
    return f"{name}({list(inputs.values())[:1]})"


# 交互式权限确认
def ask_permission_interactive(desc: str, config: dict) -> bool:
    text = ask_input_interactive(f"  允许: {desc}  [y/N/a(全部允许)] ", config).strip().lower()

    if text == "a" or text == "accept all" or text == "accept-all":
        config["permission_mode"] = "accept-all"
        ok("  本次会话权限模式已设为全部允许。")
        return True
    
    return text in ("y", "yes")


# 斜杠命令实现
import time
import traceback

# 显示帮助
def cmd_help(_args: str, _state, config) -> bool:
    print(__doc__)
    return True

# 管理模型
def cmd_model(args: str, _state, config) -> bool:
    from providers import PROVIDERS, detect_provider
    if not args:
        model = config["model"]
        pname = detect_provider(model)
        info(f"当前模型:    {model}  (服务提供商: {pname})")
        info("\n各提供商可用模型:")
        for pn, pdata in PROVIDERS.items():
            ms = pdata.get("models", [])
            if ms:
                info(f"  {pn:12s}  " + ", ".join(ms[:4]) + ("..." if len(ms) > 4 else ""))
        info("\n格式: '提供商/模型' 或直接输入模型名(自动检测)")
        info("  例如: /model gpt-4o")
        info("  例如: /model deepseek/deepseek-v4-pro")
        info("  例如: /model kimi:moonshot-v1-32k")
    else:
        # 兼容两种语法格式
        m = args.strip()
        if "/" not in m and ":" in m:
            left, right = m.split(":", 1)
            if left in PROVIDERS:
                m = f"{left}/{right}"
        config["model"] = m
        pname = detect_provider(m)
        ok(f"模型已设置为 {m}  (服务提供商: {pname})")
        from config import save_config
        save_config(config)
    return True

# 清空对话
def cmd_clear(_args: str, state, config) -> bool:
    state.messages.clear()
    state.turn_count = 0
    ok("对话已清空。")
    return True

# 管理配置
def cmd_config(args: str, _state, config) -> bool:
    from config import save_config
    if not args:
        # 隐藏 API 密钥后展示配置
        display = {k: v for k, v in config.items() if k != "api_key"}
        print(json.dumps(display, indent=2))
    elif "=" in args:
        key, _, val = args.partition("=")
        key, val = key.strip(), val.strip()
        # 类型自动转换
        if val.lower() in ("true", "false"):
            val = val.lower() == "true"
        elif val.isdigit():
            val = int(val)
        config[key] = val
        save_config(config)
        ok(f"已设置 {key} = {val}")
    else:
        k = args.strip()
        v = config.get(k, "(未设置)")
        info(f"{k} = {v}")
    return True

# 保存会话
def cmd_save(args: str, state, config) -> bool:
    from config import SESSIONS_DIR
    import uuid
    sid   = uuid.uuid4().hex[:8]
    ts    = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = args.strip() or f"session_{ts}_{sid}.json"
    path  = Path(fname) if "/" in fname else SESSIONS_DIR / fname
    data  = _build_session_data(state, session_id=sid)
    path.write_text(json.dumps(data, indent=2, default=str))
    ok(f"会话已保存 → {path}  (ID: {sid})"  )
    return True

# 退出时自动保存最新会话
def save_latest(args: str, state, config=None) -> bool:
    """退出时保存会话：保存最新会话+每日备份+追加到历史记录。"""
    from config import MR_SESSION_DIR, DAILY_DIR, SESSION_HIST_FILE
    if not state.messages:
        return True

    cfg = config or {}
    daily_limit   = cfg.get("session_daily_limit",   5)
    history_limit = cfg.get("session_history_limit", 100)

    import uuid
    now = datetime.now()
    sid = uuid.uuid4().hex[:8]
    ts  = now.strftime("%H%M%S")
    date_str = now.strftime("%Y-%m-%d")
    data = _build_session_data(state, session_id=sid)
    payload = json.dumps(data, indent=2, default=str)

    # 1. 保存最新会话文件，用于快速恢复
    MR_SESSION_DIR.mkdir(parents=True, exist_ok=True)
    latest_path = MR_SESSION_DIR / "session_latest.json"
    latest_path.write_text(payload)

    # 2. 每日会话备份
    day_dir = DAILY_DIR / date_str
    day_dir.mkdir(parents=True, exist_ok=True)
    daily_path = day_dir / f"session_{ts}_{sid}.json"
    daily_path.write_text(payload)

    # 清理每日文件夹：仅保留最新N个文件
    daily_files = sorted(day_dir.glob("session_*.json"))
    for old in daily_files[:-daily_limit]:
        old.unlink(missing_ok=True)

    # 3. 追加到总历史记录
    if SESSION_HIST_FILE.exists():
        try:
            hist = json.loads(SESSION_HIST_FILE.read_text())
        except Exception:
            hist = {"total_turns": 0, "sessions": []}
    else:
        hist = {"total_turns": 0, "sessions": []}

    hist["sessions"].append(data)
    hist["total_turns"] = sum(s.get("turn_count", 0) for s in hist["sessions"])

    # 清理历史记录：仅保留最新N个会话
    if len(hist["sessions"]) > history_limit:
        hist["sessions"] = hist["sessions"][-history_limit:]

    SESSION_HIST_FILE.write_text(json.dumps(hist, indent=2, default=str))

    ok(f"会话已保存 → {latest_path}")
    ok(f"             → {daily_path}  (ID: {sid})")
    ok(f"             → {SESSION_HIST_FILE}  ({len(hist['sessions'])} 个会话 / {hist['total_turns']} 总轮次)")
    return True


def _trigger_session_end_memory(state, config: dict, start_time: float | None = None) -> None:
    """Fire background auto-extraction and AutoDream at session end."""
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
        pass


# 加载会话
def cmd_load(args: str, state, config) -> bool:
    from config import SESSIONS_DIR, MR_SESSION_DIR, DAILY_DIR

    path = None
    if not args.strip():
        # 按时间倒序收集所有会话
        sessions: list[Path] = []
        if DAILY_DIR.exists():
            for day_dir in sorted(DAILY_DIR.iterdir(), reverse=True):
                if day_dir.is_dir():
                    sessions.extend(sorted(day_dir.glob("session_*.json"), reverse=True))
        # 兼容旧版会话目录
        if not sessions and MR_SESSION_DIR.exists():
            sessions = [s for s in sorted(MR_SESSION_DIR.glob("*.json"), reverse=True)
                        if s.name != "session_latest.json"]
        # 添加手动保存的会话
        sessions.extend(sorted(SESSIONS_DIR.glob("session_*.json"), reverse=True))

        if not sessions:
            info("未找到保存的会话。")
            return True

        print(clr("  选择要加载的会话:", "cyan", "bold"))
        menu_buf = clr('  选择要加载的会话:', 'cyan', 'bold')
        prev_date = None
        for i, s in enumerate(sessions):
            # 按日期分组显示
            date_label = s.parent.name if s.parent.name != "mr_sessions" else ""
            if date_label and date_label != prev_date:
                print(clr(f"\n  ── {date_label} ──", "dim"))
                menu_buf += "\n" + clr(f"\n  ── {date_label} ──", "dim")
                prev_date = date_label

            label = s.name
            try:
                meta     = json.loads(s.read_text())
                saved_at = meta.get("saved_at", "")[-8:]
                sid      = meta.get("session_id", "")
                turns    = meta.get("turn_count", "?")
                label    = f"{saved_at}  ID:{sid}  轮次:{turns}  {s.name}"
            except Exception:
                pass
            print(clr(f"  [{i+1:2d}] ", "yellow") + label)
            menu_buf += "\n" + clr(f"  [{i+1:2d}] ", "yellow") + label

        # 显示历史记录选项
        from config import SESSION_HIST_FILE
        has_history = SESSION_HIST_FILE.exists()
        if has_history:
            try:
                hist_meta = json.loads(SESSION_HIST_FILE.read_text())
                n_sess  = len(hist_meta.get("sessions", []))
                n_turns = hist_meta.get("total_turns", 0)
                print(clr(f"\n  ── 完整历史记录 ──", "dim"))
                menu_buf += "\n" + clr(f"\n  ── 完整历史记录 ──", "dim")
                hist_prt = clr("  [ H] ", "yellow") + f"加载全部历史  ({n_sess} 个会话 / {n_turns} 总轮次)  {SESSION_HIST_FILE}"
                print(hist_prt)
                menu_buf += "\n" + hist_prt
            except Exception:
                has_history = False

        print()
        ans = ask_input_interactive(clr("  输入序号(例如 1 或 1,2,3)，H 加载全部历史，回车取消 > ", "cyan"), config, menu_buf).strip().lower()

        if not ans:
            info("  已取消。")
            return True

        if ans == "h":
            if not has_history:
                err("未找到历史记录文件。")
                return True
            hist_data = json.loads(SESSION_HIST_FILE.read_text())
            all_sessions = hist_data.get("sessions", [])
            if not all_sessions:
                info("历史记录为空。")
                return True
            all_messages = []
            for s in all_sessions:
                all_messages.extend(s.get("messages", []))
            total_turns = sum(s.get("turn_count", 0) for s in all_sessions)
            est_tokens = sum(len(str(m.get("content", ""))) for m in all_messages) // 4
            print()
            print(clr(f"  {len(all_messages)} 条消息 / 预估约 {est_tokens:,} 令牌", "dim"))
            confirm = ask_input_interactive(clr("  将全部历史加载到当前会话？[y/N] > ", "yellow"), config).strip().lower()
            if confirm != "y":
                info("  已取消。")
                return True
            state.messages = all_messages
            state.turn_count = total_turns
            ok(f"已从 {SESSION_HIST_FILE} 加载全部历史 ({len(all_messages)} 条消息，{len(all_sessions)} 个会话)")
            return True

        # 解析逗号分隔的序号
        raw_parts = [p.strip() for p in ans.split(",")]
        indices = []
        for p in raw_parts:
            if not p.isdigit():
                err(f"无效输入 '{p}'，请输入数字或 H。")
                return True
            idx = int(p) - 1
            if idx < 0 or idx >= len(sessions):
                err(f"无效选择: {p} (有效范围: 1–{len(sessions)})")
                return True
            if idx not in indices:
                indices.append(idx)

        if len(indices) == 1:
            # 加载单个会话
            path = sessions[indices[0]]
        else:
            # 合并加载多个会话
            all_messages = []
            total_turns  = 0
            loaded_names = []
            for idx in indices:
                s_path = sessions[idx]
                s_data = json.loads(s_path.read_text())
                all_messages.extend(s_data.get("messages", []))
                total_turns += s_data.get("turn_count", 0)
                loaded_names.append(s_path.name)
            est_tokens = sum(len(str(m.get("content", ""))) for m in all_messages) // 4
            print()
            print(clr(f"  {len(loaded_names)} 个会话 / {len(all_messages)} 条消息 / 预估约 {est_tokens:,} 令牌", "dim"))
            confirm = ask_input_interactive(clr("  合并并加载？[y/N] > ", "yellow"), config).strip().lower()
            if confirm != "y":
                info("  已取消。")
                return True
            state.messages = all_messages
            state.turn_count = total_turns
            ok(f"已加载 {len(loaded_names)} 个会话 ({len(all_messages)} 条消息): {', '.join(loaded_names)}")
            return True

    if not path:
        fname = args.strip()
        path = Path(fname) if "/" in fname or "\\" in fname else SESSIONS_DIR / fname
        if not path.exists() and ("/" not in fname and "\\" not in fname):
            for alt in [MR_SESSION_DIR / fname,
                        *(d / fname for d in DAILY_DIR.iterdir()
                          if DAILY_DIR.exists() and d.is_dir())]:
                if alt.exists():
                    path = alt
                    break
        if not path.exists():
            err(f"文件不存在: {path}")
            return True
        
    data = json.loads(path.read_text())
    state.messages = data.get("messages", [])
    state.turn_count = data.get("turn_count", 0)
    state.total_input_tokens = data.get("total_input_tokens", 0)
    state.total_output_tokens = data.get("total_output_tokens", 0)
    ok(f"已从 {path} 加载会话 ({len(state.messages)} 条消息)")
    return True

# 恢复最近会话
def cmd_resume(args: str, state, config) -> bool:
    from config import MR_SESSION_DIR

    if not args.strip():
        path = MR_SESSION_DIR / "session_latest.json"
        if not path.exists():
            info("未找到自动保存的会话。")
            return True
    else:
        fname = args.strip()
        path = Path(fname) if "/" in fname else MR_SESSION_DIR / fname

    if not path.exists():
        err(f"文件不存在: {path}")
        return True

    data = json.loads(path.read_text())
    state.messages = data.get("messages", [])
    state.turn_count = data.get("turn_count", 0)
    state.total_input_tokens = data.get("total_input_tokens", 0)
    state.total_output_tokens = data.get("total_output_tokens", 0)
    ok(f"已从 {path} 加载会话 ({len(state.messages)} 条消息)")
    return True

# 显示对话历史
def cmd_history(_args: str, state, config) -> bool:
    if not state.messages:
        info("(对话为空)")
        return True
    for i, m in enumerate(state.messages):
        role = clr(m["role"].upper(), "bold",
                   "cyan" if m["role"] == "user" else "green")
        content = m["content"]
        if isinstance(content, str):
            print(f"[{i}] {role}: {content[:200]}")
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    btype = block.get("type", "")
                else:
                    btype = getattr(block, "type", "")
                if btype == "text":
                    text = block.get("text", "") if isinstance(block, dict) else block.text
                    print(f"[{i}] {role}: {text[:200]}")
                elif btype == "tool_use":
                    name = block.get("name", "") if isinstance(block, dict) else block.name
                    print(f"[{i}] {role}: [工具调用: {name}]")
                elif btype == "tool_result":
                    cval = block.get("content", "") if isinstance(block, dict) else block.content
                    print(f"[{i}] {role}: [工具结果: {str(cval)[:100]}]")
    return True

# 显示上下文使用情况
def cmd_context(_args: str, state, config) -> bool:
    import anthropic
    # 粗略令牌估算：4字符≈1令牌
    msg_chars = sum(
        len(str(m.get("content", ""))) for m in state.messages
    )
    est_tokens = msg_chars // 4
    info(f"消息数:         {len(state.messages)}")
    info(f"预估令牌数: ~{est_tokens:,}")
    info(f"模型:            {config['model']}")
    info(f"最大令牌数:       {config['max_tokens']:,}")
    return True

# 显示费用估算
def cmd_cost(_args: str, state, config) -> bool:
    from config import calc_cost
    cost = calc_cost(config["model"],
                     state.total_input_tokens,
                     state.total_output_tokens)
    info(f"输入令牌:  {state.total_input_tokens:,}")
    info(f"输出令牌: {state.total_output_tokens:,}")
    info(f"预估费用:     ${cost:.4f} 美元")
    return True

# 切换详细模式
def cmd_verbose(_args: str, _state, config) -> bool:
    from config import save_config
    config["verbose"] = not config.get("verbose", False)
    state_str = "开启" if config["verbose"] else "关闭"
    ok(f"详细模式: {state_str}")
    save_config(config)
    return True

# 切换扩展思考模式
def cmd_thinking(_args: str, _state, config) -> bool:
    from config import save_config
    config["thinking"] = not config.get("thinking", False)
    state_str = "开启" if config["thinking"] else "关闭"
    ok(f"扩展思考模式: {state_str}")
    save_config(config)
    return True

# 管理权限模式
def cmd_permissions(args: str, _state, config) -> bool:
    from config import save_config
    modes = ["auto", "accept-all", "manual"]
    mode_desc = {
        "auto":       "每次工具调用都询问(默认)",
        "accept-all": "静默允许所有工具调用",
        "manual":     "每次工具调用都询问(严格模式)",
    }
    if not args.strip():
        current = config.get("permission_mode", "auto")
        menu_buf = clr("\n  ── 权限模式 ──", "dim")
        for i, m in enumerate(modes):
            marker = clr("●", "green") if m == current else clr("○", "dim")
            menu_buf += f"\n  {marker} {clr(f'[{i+1}]', 'yellow')} {clr(m, 'cyan')}  {clr(mode_desc[m], 'dim')}"
        print(menu_buf)
        print()
        try:
            ans = ask_input_interactive(clr("  选择模式序号或回车取消 > ", "cyan"), config, menu_buf).strip()
        except (KeyboardInterrupt, EOFError):
            print()
            return True
        if not ans:
            return True
        if ans.isdigit() and 1 <= int(ans) <= len(modes):
            m = modes[int(ans) - 1]
            config["permission_mode"] = m
            save_config(config)
            ok(f"权限模式已设置为: {m}")
        else:
            err(f"无效选择。")
    else:
        m = args.strip()
        if m not in modes:
            err(f"未知模式: {m}，可选: {', '.join(modes)}")
        else:
            config["permission_mode"] = m
            save_config(config)
            ok(f"权限模式已设置为: {m}")
    return True

# 管理工作目录
def cmd_cwd(args: str, _state, config) -> bool:
    if not args.strip():
        info(f"当前工作目录: {os.getcwd()}")
    else:
        p = args.strip()
        try:
            os.chdir(p)
            ok(f"已切换目录至: {os.getcwd()}")
        except Exception as e:
            err(str(e))
    return True

# 构建会话数据结构
def _build_session_data(state, session_id: str | None = None) -> dict:
    """将当前对话状态序列化为可 JSON 存储的字典。"""
    import uuid
    return {
        "session_id": session_id or uuid.uuid4().hex[:8],
        "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "messages": [
            m if not isinstance(m.get("content"), list) else
            {**m, "content": [
                b if isinstance(b, dict) else b.model_dump()
                for b in m["content"]
            ]}
            for m in state.messages
        ],
        "turn_count": state.turn_count,
        "total_input_tokens": state.total_input_tokens,
        "total_output_tokens": state.total_output_tokens,
    }


# 退出程序
def cmd_exit(_args: str, _state, config) -> bool:
    if sys.stdin.isatty() and sys.platform != "win32":
        sys.stdout.write("\x1b[?2004l")  # 关闭括号粘贴模式
        sys.stdout.flush()
    ok("再见！")
    save_latest("", _state, config)
    _trigger_session_end_memory(_state, config)
    sys.exit(0)

# 管理记忆功能
def cmd_memory(args: str, _state, config) -> bool:
    from memory import search_memory, load_index
    from memory.scan import scan_all_memories, memory_freshness_text

    stripped = args.strip()

    # /memory consolidate — manual Layer 3 trigger
    if stripped == "consolidate":
        info("正在整合记忆（使用轻量模型）…")
        try:
            from memory.dream import consolidate
            result = consolidate(config)
            ok(result)
        except Exception as e:
            err(f"整合失败: {e}")
        return True

    if stripped:
        results = search_memory(stripped)
        if not results:
            info(f"未找到匹配 '{stripped}' 的记忆")
            return True
        info(f"  找到 {len(results)} 条结果匹配 '{stripped}':")
        for m in results:
            info(f"  [{m.type:9s}|{m.scope:7s}] {m.name}: {m.description}")
            info(f"    {m.content[:120]}{'...' if len(m.content) > 120 else ''}")
        return True

    # 显示记忆清单和时效性
    headers = scan_all_memories()
    if not headers:
        info("未存储任何记忆，模型将通过 MemorySave 保存记忆。")
        return True
    info(f"  共 {len(headers)} 条记忆(最新优先):")
    for h in headers:
        fresh_warn = "  ⚠ 已过期" if memory_freshness_text(h.mtime_s) else ""
        tag = f"[{h.type or '?':9s}|{h.scope:7s}]"
        info(f"  {tag} {h.filename}{fresh_warn}")
        if h.description:
            info(f"    {h.description}")
    return True

# 显示代理任务
def cmd_agents(_args: str, _state, config) -> bool:
    try:
        from multi_agent.tools import get_agent_manager
        mgr = get_agent_manager()
        tasks = mgr.list_tasks()
        if not tasks:
            info("无子代理任务。")
            return True
        info(f"  {len(tasks)} 个子代理任务:")
        for t in tasks:
            preview = t.prompt[:50] + ("..." if len(t.prompt) > 50 else "")
            wt_info = f"  分支:{t.worktree_branch}" if t.worktree_branch else ""
            info(f"  {t.id} [{t.status:9s}] 名称={t.name}{wt_info}  {preview}")
    except Exception:
        info("子代理系统未初始化。")
    return True


# 打印后台任务通知
def _print_background_notifications():
    """打印后台完成的代理任务通知。

    在每次用户输入前调用，无需轮询即可查看结果。
    """
    try:
        from multi_agent.tools import get_agent_manager
        mgr = get_agent_manager()
    except Exception:
        return

    notified_key = "_notified"
    if not hasattr(_print_background_notifications, "_seen"):
        _print_background_notifications._seen = set()

    for task in mgr.list_tasks():
        if task.id in _print_background_notifications._seen:
            continue
        if task.status in ("completed", "failed", "cancelled"):
            _print_background_notifications._seen.add(task.id)
            icon = "✓" if task.status == "completed" else "✗"
            color = "green" if task.status == "completed" else "red"
            branch_info = f" [分支: {task.worktree_branch}]" if task.worktree_branch else ""
            print(clr(
                f"\n  {icon} 后台代理 '{task.name}' {task.status}{branch_info}",
                color, "bold"
            ))
            if task.result:
                preview = task.result[:200] + ("..." if len(task.result) > 200 else "")
                print(clr(f"    {preview}", "dim"))
            print()

# 列出可用技能
def cmd_skills(_args: str, _state, config) -> bool:
    from skill import load_skills
    skills = load_skills()
    if not skills:
        info("未找到任何技能。")
        return True
    info(f"可用技能({len(skills)}):")
    for s in skills:
        triggers = ", ".join(s.triggers)
        source_label = f"[{s.source}]" if s.source != "builtin" else ""
        hint = f"  参数: {s.argument_hint}" if s.argument_hint else ""
        print(f"  {clr(s.name, 'cyan'):24s} {s.description}  {clr(triggers, 'dim')}{hint} {clr(source_label, 'yellow')}")
        if s.when_to_use:
            print(f"    {clr(s.when_to_use[:80], 'dim')}")
    return True

# 管理 MCP 服务器
def cmd_mcp(args: str, _state, config) -> bool:
    """显示 MCP 服务器状态或管理服务器。

    /mcp               — 列出所有配置的服务器及其工具
    /mcp reload        — 重新连接所有服务器并刷新工具
    /mcp reload <名称> — 重新连接单个服务器
    /mcp add <名称> <命令> [参数...] — 添加标准输入输出服务器到用户配置
    /mcp remove <名称> — 从用户配置中移除服务器
    """
    from mcp.client import get_mcp_manager
    from mcp.config import (load_mcp_configs, add_server_to_user_config,
                             remove_server_from_user_config, list_config_files)
    from mcp.tools import initialize_mcp, reload_mcp, refresh_server

    parts = args.split() if args.strip() else []
    subcmd = parts[0].lower() if parts else ""

    if subcmd == "reload":
        target = parts[1] if len(parts) > 1 else ""
        if target:
            err = refresh_server(target)
            if err:
                err(f"重新加载 '{target}' 失败: {err}")
            else:
                ok(f"已重新加载 MCP 服务器: {target}")
        else:
            errors = reload_mcp()
            for name, e in errors.items():
                if e:
                    print(f"  {clr('✗', 'red')} {name}: {e}")
                else:
                    print(f"  {clr('✓', 'green')} {name}: 已连接")
        return True

    if subcmd == "add":
        if len(parts) < 3:
            err("用法: /mcp add <名称> <命令> [参数1 参数2...]")
            return True
        name = parts[1]
        command = parts[2]
        cmd_args = parts[3:]
        raw = {"type": "stdio", "command": command}
        if cmd_args:
            raw["args"] = cmd_args
        add_server_to_user_config(name, raw)
        ok(f"已添加 MCP 服务器 '{name}'，重启或执行 /mcp reload 连接")
        return True

    if subcmd == "remove":
        if len(parts) < 2:
            err("用法: /mcp remove <名称>")
            return True
        name = parts[1]
        removed = remove_server_from_user_config(name)
        if removed:
            ok(f"已从用户配置移除 MCP 服务器 '{name}'")
        else:
            err(f"用户配置中未找到服务器 '{name}'")
        return True

    # 默认：列出服务器
    mgr = get_mcp_manager()
    servers = mgr.list_servers()

    config_files = list_config_files()
    if config_files:
        info(f"配置文件: {', '.join(str(f) for f in config_files)}")

    if not servers:
        configs = load_mcp_configs()
        if not configs:
            info("未配置任何 MCP 服务器。")
            info("可在 ~/.pycc/mcp.json 或 .mcp.json 中添加服务器")
            info("示例: /mcp add my-git uvx mcp-server-git")
        else:
            info("已配置 MCP 服务器但未连接，执行 /mcp reload")
        return True

    info(f"MCP 服务器({len(servers)}):")
    total_tools = 0
    for client in servers:
        status_color = {
            "connected":    "green",
            "connecting":   "yellow",
            "disconnected": "dim",
            "error":        "red",
        }.get(client.state.value, "dim")
        print(f"  {clr(client.status_line(), status_color)}")
        for tool in client._tools:
            print(f"      {clr(tool.qualified_name, 'cyan')}  {tool.description[:60]}")
            total_tools += 1

    if total_tools:
        info(f"总计: {total_tools} 个 MCP 工具可供 Claude 使用")
    return True


# 管理任务
def cmd_tasks(args: str, _state, config) -> bool:
    """查看和管理任务。

    /tasks                  — 列出所有任务
    /tasks create <主题> — 快速创建任务
    /tasks done <ID>        — 标记任务为已完成
    /tasks start <ID>       — 标记任务为进行中
    /tasks cancel <ID>      — 标记任务为已取消
    /tasks delete <ID>      — 删除任务
    /tasks get <ID>         — 显示任务完整详情
    /tasks clear            — 删除所有任务
    """
    from task import list_tasks, get_task, create_task, update_task, delete_task, clear_all_tasks
    from task.types import TaskStatus

    parts = args.split(None, 1)
    subcmd = parts[0].lower() if parts else ""
    rest   = parts[1].strip() if len(parts) > 1 else ""

    STATUS_MAP = {
        "done":   "completed",
        "start":  "in_progress",
        "cancel": "cancelled",
    }

    if not subcmd:
        tasks = list_tasks()
        if not tasks:
            info("暂无任务，可使用 TaskCreate 工具或 /tasks create <主题> 创建。")
            return True
        total = len(tasks)
        done  = sum(1 for t in tasks if t.status == TaskStatus.COMPLETED)
        info(f"任务({done}/{total} 已完成):")
        for t in tasks:
            owner_str = f" {clr(f'({t.owner})', 'dim')}" if t.owner else ""
            status_color = {
                TaskStatus.PENDING:     "dim",
                TaskStatus.IN_PROGRESS: "cyan",
                TaskStatus.COMPLETED:   "green",
                TaskStatus.CANCELLED:   "red",
            }.get(t.status, "dim")
            icon = t.status_icon()
            print(f"  #{t.id} {clr(icon + ' ' + t.status.value, status_color)} {t.subject}{owner_str}")
        return True

    if subcmd == "create":
        if not rest:
            err("用法: /tasks create <主题>")
            return True
        t = create_task(rest, description="(通过交互模式创建)")
        ok(f"任务 #{t.id} 已创建: {t.subject}")
        return True

    if subcmd in STATUS_MAP:
        new_status = STATUS_MAP[subcmd]
        if not rest:
            err(f"用法: /tasks {subcmd} <任务ID>")
            return True
        task, fields = update_task(rest, status=new_status)
        if task is None:
            err(f"未找到任务 #{rest}。")
        else:
            ok(f"任务 #{task.id} → {new_status}: {task.subject}")
        return True

    if subcmd == "delete":
        if not rest:
            err("用法: /tasks delete <任务ID>")
            return True
        removed = delete_task(rest)
        if removed:
            ok(f"任务 #{rest} 已删除。")
        else:
            err(f"未找到任务 #{rest}。")
        return True

    if subcmd == "get":
        if not rest:
            err("用法: /tasks get <任务ID>")
            return True
        t = get_task(rest)
        if t is None:
            err(f"未找到任务 #{rest}。")
            return True
        print(f"  #{t.id} [{t.status.value}] {t.subject}")
        print(f"  描述: {t.description}")
        if t.owner:         print(f"  负责人:       {t.owner}")
        if t.active_form:   print(f"  激活表单: {t.active_form}")
        if t.metadata:      print(f"  元数据:    {t.metadata}")
        print(f"  创建时间: {t.created_at[:19]}  更新时间: {t.updated_at[:19]}")
        return True

    if subcmd == "clear":
        clear_all_tasks()
        ok("所有任务已删除。")
        return True

    err(f"未知任务子命令: {subcmd}  (尝试 /tasks 或 /help)")
    return True


# 发送剪贴板图片
def cmd_image(args: str, state, config) -> Union[bool, tuple]:
    """从剪贴板获取图片并发送给视觉模型，支持附加提示词。"""
    import sys as _sys
    try:
        from PIL import ImageGrab
        import io, base64
    except ImportError:
        err("需要安装 Pillow 才能使用 /image，命令: pip install pycc[vision]")
        if _sys.platform == "linux":
            err("Linux 系统还需要安装 xclip: sudo apt install xclip")
        return True

    img = ImageGrab.grabclipboard()
    if img is None:
        if _sys.platform == "linux":
            err("剪贴板中未找到图片，Linux 系统需要 xclip (sudo apt install xclip)。"
                "使用 Flameshot、GNOME 截图工具复制图片，或执行: xclip -selection clipboard -t image/png -i 文件名.png")
        elif _sys.platform == "darwin":
            err("剪贴板中未找到图片，请先复制图片"
                "(Cmd+Ctrl+Shift+4 可将截图区域复制到剪贴板)。")
        else:
            err("剪贴板中未找到图片，请先复制图片"
                "(Win+Shift+S 可将截图区域复制到剪贴板)。")
        return True

    # 转换为 Base64 编码的 PNG
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    size_kb = len(buf.getvalue()) / 1024

    info(f"📷 已捕获剪贴板图片 ({size_kb:.0f} KB，{img.size[0]}x{img.size[1]})")

    # 存储到配置中供代理使用
    config["_pending_image"] = b64

    prompt = args.strip() if args.strip() else "你在这张图片中看到了什么？详细描述一下。"
    return ("__image__", prompt)


# 计划模式管理
def cmd_plan(args: str, state, config) -> bool:
    """进入/退出计划模式或查看当前计划。

    /plan <描述>  — 激活计划限制层并开始制定计划
    /plan                — 显示当前计划文件内容
    /plan done           — 停用计划限制层
    /plan status         — 显示计划模式状态
    """
    arg = args.strip()

    plan_file = get_plan_file(config)
    in_plan_mode = is_plan_mode(config)

    # 退出计划模式
    if arg == "done":
        if not in_plan_mode:
            err("未处于计划模式。")
            return True
        message, _ = exit_plan_mode(config, require_nonempty=False)
        info("计划限制层已停用。")
        if plan_file:
            info(f"计划已保存至: {plan_file}")
            info("现在可以让 Claude 执行该计划。")
        return True

    # 查看计划模式状态
    if arg == "status":
        if in_plan_mode:
            info("计划模式: 已激活")
            info(f"基础权限模式: {config.get('permission_mode', 'auto')}")
            info(f"计划文件: {plan_file}")
            info("使用 /plan done 退出。")
        else:
            info("计划模式: 未激活")
            info(f"基础权限模式: {config.get('permission_mode', 'auto')}")
        return True

    # 无参数：显示计划内容
    if not arg:
        if not plan_file:
            info("未处于计划模式，使用 /plan <描述> 开始制定计划。")
            return True
        p = Path(plan_file)
        if p.exists() and p.stat().st_size > 0:
            info(f"计划文件: {plan_file}")
            print(p.read_text(encoding="utf-8"))
        else:
            info(f"计划文件为空: {plan_file}")
        return True

    # 进入计划模式
    if in_plan_mode:
        err("已处于计划模式，先使用 /plan done 退出。")
        return True

    # 激活计划限制层
    message, plan_path = enter_plan_mode(config, arg)
    info("计划限制层已激活(仅计划文件可写入)。")
    info(f"计划文件: {plan_path}")
    info("使用 /plan done 退出并开始执行。")
    print()

    # 返回标记触发查询执行
    return ("__plan__", arg)


# 手动压缩对话历史
def cmd_compact(args: str, state, config) -> bool:
    """手动压缩对话历史。

    /compact              — 使用默认摘要压缩
    /compact <重点>      — 按指定重点压缩
    """
    from compaction import manual_compact
    focus = args.strip()

    if focus:
        info(f"按重点压缩对话: {focus}")
    else:
        info("正在压缩对话...")

    success, msg = manual_compact(state, config, focus=focus)
    if success:
        info(msg)
    else:
        err(msg)
    return True


# 初始化项目说明文件
def cmd_init(args: str, state, config) -> bool:
    """在当前目录初始化 CLAUDE.md 文件。

    /init          — 创建带模板的 CLAUDE.md
    """
    target = Path.cwd() / "CLAUDE.md"
    if target.exists():
        err(f"{target} 已存在")
        info("直接编辑或先删除该文件。")
        return True

    project_name = Path.cwd().name
    template = (
        f"# {project_name}\n\n"
        "## 项目概述\n"
        "<!-- 描述项目功能 -->\n\n"
        "## 技术栈\n"
        "<!-- 语言、框架、核心依赖 -->\n\n"
        "## 规范\n"
        "<!-- 编码风格、命名规范、遵循模式 -->\n\n"
        "## 重要文件\n"
        "<!-- 核心入口、配置文件等 -->\n\n"
        "## 测试\n"
        "<!-- 测试执行方式、测试规范 -->\n\n"
    )
    target.write_text(template, encoding="utf-8")
    info(f"已创建 {target}")
    info("编辑该文件为 Claude 提供项目上下文。")
    return True


# 导出对话历史
def cmd_export(args: str, state, config) -> bool:
    """导出对话历史到文件。

    /export              — 导出为 Markdown 到 .nano_claude/exports/
    /export <文件名>   — 导出到指定文件(.md 或 .json)
    """
    if not state.messages:
        err("无对话可导出。")
        return True

    arg = args.strip()
    if arg:
        out_path = Path(arg)
    else:
        export_dir = Path.cwd() / ".nano_claude" / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = export_dir / f"conversation_{ts}.md"

    is_json = out_path.suffix.lower() == ".json"

    if is_json:
        out_path.write_text(
            json.dumps(state.messages, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    else:
        lines = []
        for m in state.messages:
            role = m.get("role", "unknown")
            content = m.get("content", "")
            if isinstance(content, list):
                content = "(结构化内容)"
            if role == "user":
                lines.append(f"## 用户\n\n{content}\n")
            elif role == "assistant":
                lines.append(f"## 助手\n\n{content}\n")
            elif role == "tool":
                name = m.get("name", "tool")
                lines.append(f"### 工具: {name}\n\n```\n{content[:2000]}\n```\n")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text("\n".join(lines), encoding="utf-8")

    info(f"已导出 {len(state.messages)} 条消息到 {out_path}")
    return True


# 复制最后一条助手回复
def cmd_copy(args: str, state, config) -> bool:
    """将最后一条助手回复复制到剪贴板。

    /copy   — 复制最后一条助手消息到剪贴板
    """
    # 查找最后一条助手消息
    last_reply = None
    for m in reversed(state.messages):
        if m.get("role") == "assistant":
            content = m.get("content", "")
            if isinstance(content, str) and content.strip():
                last_reply = content
                break

    if not last_reply:
        err("无助手回复可复制。")
        return True

    try:
        import subprocess as _sp
        import sys as _sys
        if _sys.platform == "win32":
            proc = _sp.Popen(["clip"], stdin=_sp.PIPE)
            proc.communicate(last_reply.encode("utf-16le"))
        elif _sys.platform == "darwin":
            proc = _sp.Popen(["pbcopy"], stdin=_sp.PIPE)
            proc.communicate(last_reply.encode("utf-8"))
        else:
            # Linux 系统：尝试 xclip，再尝试 xsel
            for cmd in (["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]):
                try:
                    proc = _sp.Popen(cmd, stdin=_sp.PIPE)
                    proc.communicate(last_reply.encode("utf-8"))
                    break
                except FileNotFoundError:
                    continue
            else:
                err("未找到剪贴板工具，请安装 xclip 或 xsel。")
                return True
        info(f"已复制 {len(last_reply)} 字符到剪贴板。")
    except Exception as e:
        err(f"复制失败: {e}")
    return True


# 显示会话状态
def cmd_status(args: str, state, config) -> bool:
    """显示当前会话状态。

    /status   — 模型、提供商、权限、会话信息
    """
    from providers import detect_provider
    from compaction import estimate_tokens, get_context_limit

    model = config.get("model", "unknown")
    provider = detect_provider(model)
    perm_mode = config.get("permission_mode", "auto")
    session_id = config.get("_session_id", "N/A")
    turn_count = getattr(state, "turn_count", 0)
    msg_count = len(getattr(state, "messages", []))
    tokens_in = getattr(state, "total_input_tokens", 0)
    tokens_out = getattr(state, "total_output_tokens", 0)
    est_ctx = estimate_tokens(getattr(state, "messages", []))
    ctx_limit = get_context_limit(model)
    ctx_pct = (est_ctx / ctx_limit * 100) if ctx_limit else 0
    plan_mode = is_plan_mode(config)

    print(f"  版本:     {VERSION}")
    print(f"  模型:       {model} ({provider})")
    print(f"  权限: {perm_mode}" + (" [计划模式激活]" if plan_mode else ""))
    if plan_mode:
        print(f"  计划文件: {get_plan_file(config)}")
    print(f"  会话:     {session_id}")
    print(f"  轮次:       {turn_count}")
    print(f"  消息:    {msg_count}")
    print(f"  令牌:      ~{tokens_in} 输入 / ~{tokens_out} 输出")
    print(f"  上下文:     ~{est_ctx} / {ctx_limit} ({ctx_pct:.0f}%)")
    return True


# 诊断工具
def cmd_doctor(args: str, state, config) -> bool:
    """诊断安装环境和网络连接。

    /doctor   — 运行所有健康检查
    """
    import subprocess as _sp
    import sys as _sys
    from providers import PROVIDERS, detect_provider, get_api_key

    ok_n = warn_n = fail_n = 0

    def _print_safe(s):
        try:
            print(s)
        except UnicodeEncodeError:
            print(s.encode("ascii", errors="replace").decode())

    def ok(msg):
        nonlocal ok_n; ok_n += 1
        _print_safe(clr("  [通过] ", "green") + msg)

    def warn(msg):
        nonlocal warn_n; warn_n += 1
        _print_safe(clr("  [警告] ", "yellow") + msg)

    def fail(msg):
        nonlocal fail_n; fail_n += 1
        _print_safe(clr("  [失败] ", "red") + msg)

    info("正在运行诊断...")
    print()

    # 1. 检查 Python 版本
    v = _sys.version_info
    if v >= (3, 10):
        ok(f"Python {v.major}.{v.minor}.{v.micro}")
    else:
        fail(f"Python {v.major}.{v.minor}.{v.micro} (需要 ≥3.10)")

    # 2. 检查 Git
    try:
        r = _sp.run(["git", "--version"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            ok(f"Git: {r.stdout.strip()}")
        else:
            fail("Git: 工作异常")
    except Exception:
        fail("Git: 未找到")

    try:
        r = _sp.run(["git", "rev-parse", "--is-inside-work-tree"],
                     capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            ok("位于 Git 仓库内")
        else:
            warn("未位于 Git 仓库内")
    except Exception:
        warn("无法检查 Git 仓库状态")

    # 3. 检查当前模型和 API 密钥
    model = config.get("model", "")
    provider = detect_provider(model)
    key = get_api_key(provider, config)

    if key:
        ok(f"{provider} 的 API 密钥: 已设置 ({key[:4]}...{key[-4:]})")
    else:
        fail(f"{provider} 的 API 密钥: 未设置")

    # 4. 测试 API 连接
    if key:
        print(f"  ... 正在测试 {provider} API 连接...")
        try:
            import urllib.request, urllib.error
            prov = PROVIDERS.get(provider, {})
            ptype = prov.get("type", "openai")

            if ptype == "anthropic":
                req = urllib.request.Request(
                    "https://api.anthropic.com/v1/messages",
                    data=json.dumps({
                        "model": model,
                        "max_tokens": 1,
                        "messages": [{"role": "user", "content": "hi"}],
                    }).encode(),
                    headers={
                        "x-api-key": key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                )
                try:
                    urllib.request.urlopen(req, timeout=10)
                    ok(f"Anthropic API: 可访问，模型 {model} 正常")
                except urllib.error.HTTPError as e:
                    if e.code == 401:
                        fail("Anthropic API: 无效的 API 密钥 (401)")
                    elif e.code == 404:
                        fail(f"Anthropic API: 未找到模型 {model} (404)")
                    elif e.code == 429:
                        warn("Anthropic API: 请求频率受限 (429) — 密钥有效")
                    else:
                        warn(f"Anthropic API: HTTP {e.code}")
                except Exception as e:
                    fail(f"Anthropic API: 连接错误 — {e}")

            else:
                base = prov.get("base_url", "")
                if provider == "custom":
                    base = config.get("custom_base_url", base or "")
                if base:
                    models_url = base.rstrip("/") + "/models"
                    req = urllib.request.Request(
                        models_url,
                        headers={"Authorization": f"Bearer {key}"},
                    )
                    try:
                        urllib.request.urlopen(req, timeout=10)
                        ok(f"{provider} API: 可访问")
                    except urllib.error.HTTPError as e:
                        if e.code == 401:
                            fail(f"{provider} API: 无效的 API 密钥 (401)")
                        elif e.code == 429:
                            warn(f"{provider} API: 请求频率受限 (429) — 密钥有效")
                        else:
                            warn(f"{provider} API: HTTP {e.code}")
                    except Exception as e:
                        fail(f"{provider} API: 连接错误 — {e}")
                else:
                    warn(f"{provider}: 未配置基础地址")
        except Exception as e:
            warn(f"API 测试跳过: {e}")

    # 5. 检查其他配置的 API 密钥
    print()
    for pname, pdata in PROVIDERS.items():
        if pname == provider:
            continue
        env_var = pdata.get("api_key_env")
        if env_var and os.environ.get(env_var, ""):
            ok(f"{pname} 密钥 ({env_var}): 已设置")

    # 6. 检查可选依赖
    print()
    for mod, desc in [
        ("rich", "Rich (实时 Markdown 渲染)"),
        ("PIL", "Pillow (剪贴板图片 /image)"),
        ("sounddevice", "sounddevice (语音录制)"),
        ("faster_whisper", "faster-whisper (本地语音识别)"),
    ]:
        try:
            __import__(mod)
            ok(desc)
        except ImportError:
            warn(f"{desc}: 未安装")

    # 7. 检查项目说明文件
    print()
    claude_md = Path.cwd() / "CLAUDE.md"
    global_md = Path.home() / ".claude" / "CLAUDE.md"
    if claude_md.exists():
        ok(f"项目 CLAUDE.md: {claude_md}")
    else:
        warn("无项目 CLAUDE.md (执行 /init 创建)")
    if global_md.exists():
        ok(f"全局 CLAUDE.md: {global_md}")

    # 8. 检查权限模式
    perm = config.get("permission_mode", "auto")
    if perm == "accept-all":
        warn(f"权限模式: {perm} (所有操作自动允许)")
    else:
        ok(f"权限模式: {perm}")

    # 诊断总结
    print()
    total = ok_n + warn_n + fail_n
    summary = f"  {ok_n} 项通过, {warn_n} 项警告, {fail_n} 项失败 ({total} 项检查)"
    if fail_n:
        _print_safe(clr(summary, "red"))
    elif warn_n:
        _print_safe(clr(summary, "yellow"))
    else:
        _print_safe(clr(summary, "green"))

    return True

# 命令映射表
COMMANDS = {
    "help":        cmd_help,
    "clear":       cmd_clear,
    "model":       cmd_model,
    "config":      cmd_config,
    "save":        cmd_save,
    "load":        cmd_load,
    "history":     cmd_history,
    "context":     cmd_context,
    "cost":        cmd_cost,
    "verbose":     cmd_verbose,
    "thinking":    cmd_thinking,
    "permissions": cmd_permissions,
    "cwd":         cmd_cwd,
    "skills":      cmd_skills,
    "memory":      cmd_memory,
    "agents":      cmd_agents,
    "mcp":         cmd_mcp,
    "tasks":       cmd_tasks,
    "task":        cmd_tasks,
    "image":       cmd_image,
    "img":         cmd_image,
    "plan":        cmd_plan,
    "compact":     cmd_compact,
    "init":        cmd_init,
    "export":      cmd_export,
    "copy":        cmd_copy,
    "status":      cmd_status,
    "doctor":      cmd_doctor,
    "exit":        cmd_exit,
    "quit":        cmd_exit,
    "resume":      cmd_resume
}

# 处理斜杠命令
def handle_slash(line: str, state, config) -> Union[bool, tuple]:
    """处理 /命令 [参数]。处理成功返回 True，技能匹配返回元组(技能,参数)。"""
    if not line.startswith("/"):
        return False
    parts = line[1:].split(None, 1)
    if not parts:
        return False
    cmd = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""
    handler = COMMANDS.get(cmd)
    if handler:
        result = handler(args, state, config)
        # 图片/计划命令返回标记，让交互循环执行查询
        if isinstance(result, tuple) and result[0] in ("__image__", "__plan__"):
            return result
        return True

    # 技能查找
    from skill import find_skill
    skill = find_skill(line)
    if skill:
        cmd_parts = line.strip().split(maxsplit=1)
        skill_args = cmd_parts[1] if len(cmd_parts) > 1 else ""
        return (skill, skill_args)

    err(f"未知命令: /{cmd}  (输入 /help 查看命令列表)")
    return True


# 命令行历史设置
# 每个斜杠命令的描述和子命令（用于 Tab 补全）
_CMD_META: dict[str, tuple[str, list[str]]] = {
    "help":        ("显示帮助",                          []),
    "clear":       ("清空对话历史",         []),
    "model":       ("查看/设置模型",                   []),
    "config":      ("查看/设置配置 key=value",        []),
    "save":        ("保存会话到文件",               []),
    "load":        ("加载保存的会话",               []),
    "history":     ("显示对话历史",          []),
    "context":     ("显示令牌上下文使用",           []),
    "cost":        ("显示费用估算",                 []),
    "verbose":     ("切换详细输出",              []),
    "thinking":    ("切换扩展思考",              []),
    "permissions": ("设置权限模式",                ["auto", "accept-all", "manual"]),
    "cwd":         ("查看/切换工作目录",    []),
    "skills":      ("列出可用技能",              []),
    "memory":      ("搜索/列出记忆", []),
    "agents":      ("显示后台代理",             []),
    "mcp":         ("管理 MCP 服务器",                 ["reload", "add", "remove"]),
    "tasks":       ("管理任务",                       ["create", "delete", "get", "clear",
                                                           "todo", "in-progress", "done", "blocked"]),
    "task":        ("管理任务(别名)",               ["create", "delete", "get", "clear",
                                                           "todo", "in-progress", "done", "blocked"]),
    "image":       ("发送剪贴板图片给模型",      []),
    "img":         ("发送剪贴板图片(别名)",       []),
    "plan":        ("进入/退出计划模式",                ["done", "status"]),
    "compact":     ("压缩对话历史",         []),
    "init":        ("初始化 CLAUDE.md 模板",        []),
    "export":      ("导出对话到文件",          []),
    "copy":        ("复制最后回复到剪贴板",      []),
    "status":      ("显示会话状态和模型信息",   []),
    "doctor":      ("诊断安装环境",         []),
    "exit":        ("退出 pycc",              []),
    "quit":        ("退出(别名 /exit)",             []),
    "resume":      ("恢复最近会话",                []),
}

# 设置命令行补全
def setup_readline(history_file: Path):
    if readline is None:
        return
    try:
        readline.read_history_file(str(history_file))
    except FileNotFoundError:
        pass
    except OSError:
        # libedit (macOS) 无法读取含非 ASCII 字符的历史文件，清空重建
        try:
            history_file.write_text("", encoding="utf-8")
        except Exception:
            pass
    readline.set_history_length(1000)
    atexit.register(readline.write_history_file, str(history_file))

    # 允许 "/" 作为补全标记，使 "/model" 作为一个单词
    delims = readline.get_completer_delims().replace("/", "")
    readline.set_completer_delims(delims)

    # 补全器
    def completer(text: str, state: int):
        line = readline.get_line_buffer()

        # 补全命令名：包含 / 但无空格
        if "/" in line and " " not in line:
            matches = sorted(f"/{c}" for c in _CMD_META if f"/{c}".startswith(text))
            return matches[state] if state < len(matches) else None

        # 补全子命令："/命令 部分内容"
        if line.startswith("/") and " " in line:
            cmd = line.split()[0][1:]
            if cmd in _CMD_META:
                subs = _CMD_META[cmd][1]
                matches = sorted(s for s in subs if s.startswith(text))
                return matches[state] if state < len(matches) else None

        return None

    # 自定义补全结果展示
    def display_matches(substitution: str, matches: list, longest: int):
        sys.stdout.write("\n")
        line = readline.get_line_buffer()
        is_cmd = "/" in line and " " not in line

        if is_cmd:
            col_w = max(len(m) for m in matches) + 2
            for m in sorted(matches):
                cmd = m[1:]
                desc = _CMD_META.get(cmd, ("", []))[0]
                subs = _CMD_META.get(cmd, ("", []))[1]
                sub_hint = ("  [" + ", ".join(subs[:4])
                            + ("…" if len(subs) > 4 else "") + "]") if subs else ""
                sys.stdout.write(f"  \033[36m{m:<{col_w}}\033[0m  {desc}{sub_hint}\n")
        else:
            for m in sorted(matches):
                sys.stdout.write(f"  {m}\n")
        sys.stdout.flush()

    readline.set_completion_display_matches_hook(display_matches)
    readline.set_completer(completer)
    readline.parse_and_bind("tab: complete")


# 主交互循环
def repl(config: dict, initial_prompt: str = None):
    from config import HISTORY_FILE
    from context import build_system_prompt
    from agent import AgentState, run, TextChunk, ThinkingChunk, ToolStart, ToolEnd, TurnDone, PermissionRequest

    setup_readline(HISTORY_FILE)
    state = AgentState()
    verbose = config.get("verbose", False)

    # 注入会话标识
    import uuid as _uuid
    config.setdefault("_session_id", str(_uuid.uuid4()))
    config.setdefault("_cwd", str(Path.cwd()))
    _session_start_time = time.monotonic()
    config["_session_start_time"] = _session_start_time
    # 欢迎横幅
    if not initial_prompt:
        from providers import detect_provider
        
        model    = config["model"]
        pname    = detect_provider(model)
        model_clr = clr(model, "cyan", "bold")
        prov_clr  = clr(f"({pname})", "dim")
        pmode     = clr(config.get("permission_mode", "auto"), "yellow")
        ver_clr   = clr(f"v{VERSION}", "green")

        plan_active = is_plan_mode(config)
        plan_suffix = clr(" [计划模式]", "magenta", "bold") if plan_active else ""
        print(clr("  ╭─ ", "dim") + clr("pycc ", "cyan", "bold") + ver_clr + clr(" ─────────────────────────────────╮", "dim"))
        print(clr("  │", "dim") + clr("  模型: ", "dim") + model_clr + " " + prov_clr)
        print(clr("  │", "dim") + clr("  权限: ", "dim") + pmode + plan_suffix)
        print(clr("  │", "dim") + clr("  /model 切换模型 · /help 查看命令", "dim"))
        print(clr("  ╰──────────────────────────────────────────────────────╯", "dim"))

        # 显示非默认的激活配置
        active_flags = []
        if config.get("verbose"):
            active_flags.append("详细模式")
        if config.get("thinking"):
            active_flags.append("扩展思考")
        if active_flags:
            flags_str = " · ".join(clr(f, "green") for f in active_flags)
            info(f"已激活: {flags_str}")
        print()

    query_lock = threading.RLock()

    # 应用实时渲染配置：自动检测 SSH 和哑终端
    import os as _os
    _in_ssh = bool(_os.environ.get("SSH_CLIENT") or _os.environ.get("SSH_TTY"))
    _is_dumb = (console is not None and getattr(console, "is_dumb_terminal", False))
    _rich_live_default = not _in_ssh and not _is_dumb
    global _RICH_LIVE
    _RICH_LIVE = _RICH and config.get("rich_live", _rich_live_default)

    # 执行用户查询
    def run_query(user_input: str, is_background: bool = False):
        nonlocal verbose

        with query_lock:
            verbose = config.get("verbose", False)

            # 后台记忆检索（与 API 调用并行执行）
            _mem_result: dict = {"content": ""}

            def _memory_retrieval_worker() -> None:
                try:
                    from memory.retriever import retrieve_for_query
                    _mem_result["content"] = retrieve_for_query(user_input, config)
                except Exception:
                    pass

            _mem_thread = threading.Thread(
                target=_memory_retrieval_worker, daemon=True, name="mem-retrieval"
            )
            _mem_thread.start()

            # 重建系统提示词
            system_prompt = build_system_prompt(config)

            print(clr("\n╭─ pycc ", "dim") + clr("●", "green") + clr(" ─────────────────────────", "dim"))

            thinking_started = False
            spinner_shown = True
            _start_tool_spinner()
            _pre_tool_text = []
            _post_tool = False
            _post_tool_buf = []
            _duplicate_suppressed = False

            try:
                for event in run(user_input, state, config, system_prompt):
                    # 有输出时停止加载动画
                    if spinner_shown:
                        show_thinking = isinstance(event, ThinkingChunk) and verbose
                        if isinstance(event, TextChunk) or show_thinking or isinstance(event, ToolStart):
                            _stop_tool_spinner()
                            spinner_shown = False
                            if isinstance(event, TextChunk) and not _RICH and not _post_tool:
                                print(clr("│ ", "dim"), end="", flush=True)

                    if isinstance(event, TextChunk):
                        if thinking_started:
                            print("\033[0m\n")
                            thinking_started = False

                        if _post_tool and not _duplicate_suppressed:
                            _post_tool_buf.append(event.text)
                            post_so_far = "".join(_post_tool_buf).strip()
                            pre_text = "".join(_pre_tool_text).strip()
                            # 去重重复内容
                            if pre_text and pre_text.startswith(post_so_far):
                                if len(post_so_far) >= len(pre_text):
                                    _duplicate_suppressed = True
                                    _post_tool_buf.clear()
                                continue
                            elif post_so_far and not pre_text.startswith(post_so_far):
                                for chunk in _post_tool_buf:
                                    stream_text(chunk)
                                _post_tool_buf.clear()
                                _duplicate_suppressed = True
                                continue

                        if not _post_tool:
                            _pre_tool_text.append(event.text)
                        stream_text(event.text)

                    elif isinstance(event, ThinkingChunk):
                        if verbose:
                            if not thinking_started:
                                flush_response()
                                print(clr("  [思考中]", "dim"))
                                thinking_started = True
                            stream_thinking(event.text, verbose)

                    elif isinstance(event, ToolStart):
                        flush_response()
                        print_tool_start(event.name, event.inputs, verbose)

                    elif isinstance(event, PermissionRequest):
                        _stop_tool_spinner()
                        flush_response()
                        from hooks.dispatcher import fire_notification as _fire_notification
                        _fire_notification(
                            event.description,
                            config.get("_session_id", ""),
                            config.get("_cwd", "."),
                        )
                        # 用 event._config（agent 内部副本）而非外层 config，
                        # 确保按 'a' 后 accept-all 在当前 agent 循环内立即生效
                        _perm_cfg = event._config if event._config else config
                        event.granted = ask_permission_interactive(event.description, _perm_cfg)
                        # 同步回外层 config，保持一致
                        if _perm_cfg is not config:
                            config["permission_mode"] = _perm_cfg.get("permission_mode", config.get("permission_mode"))

                    elif isinstance(event, ToolEnd):
                        print_tool_end(event.name, event.result, verbose)
                        _post_tool = True
                        _post_tool_buf.clear()
                        _duplicate_suppressed = False
                        if not _RICH:
                            print(clr("│ ", "dim"), end="", flush=True)
                        # 重启加载动画
                        _change_spinner_phrase()
                        _start_tool_spinner()
                        spinner_shown = True

                    elif isinstance(event, TurnDone):
                        _stop_tool_spinner()
                        spinner_shown = False
                        if verbose:
                            flush_response()
                            print(clr(
                                f"\n  [令牌: +{event.input_tokens} 输入 / "
                                f"+{event.output_tokens} 输出]", "dim"
                            ))
            except KeyboardInterrupt:
                _stop_tool_spinner()
                flush_response()
                raise
            except Exception as e:
                _stop_tool_spinner()
                raise e

            _stop_tool_spinner()
            flush_response()
            print(clr("╰──────────────────────────────────────────────", "dim"))
            print()

            # 等待记忆检索完成
            _mem_thread.join(timeout=5.0)
            if _mem_result["content"]:
                config["_retrieved_memories"] = _mem_result["content"]

            # 后台任务完成后重绘提示符
            if is_background:
                print(clr(f"\n[{Path.cwd().name}] » ", "yellow"), end="", flush=True)

        # 处理待处理的用户问题
        from tools import drain_pending_questions
        drain_pending_questions(config)


    # 快速强制退出：2秒内按3次Ctrl+C
    _ctrl_c_times = []

    def _track_ctrl_c():
        now = time.time()
        _ctrl_c_times.append(now)
        # 仅保留2秒内的按键记录
        _ctrl_c_times[:] = [t for t in _ctrl_c_times if now - t <= 2.0]
        if len(_ctrl_c_times) >= 3:
            _stop_tool_spinner()
            print(clr("\n\n  强制退出 (3次Ctrl+C)。", "red", "bold"))
            os._exit(1)
        return False

    # 主循环
    if initial_prompt:
        try:
            run_query(initial_prompt)
        except KeyboardInterrupt:
            _track_ctrl_c()
            print()
        return

    # 括号粘贴模式支持
    _PASTE_START = "\x1b[200~"
    _PASTE_END   = "\x1b[201~"
    _bpm_active  = sys.stdin.isatty() and sys.platform != "win32"

    if _bpm_active:
        sys.stdout.write("\x1b[?2004h")
        sys.stdout.flush()

    # 读取用户输入（支持多行粘贴）
    def _read_input(prompt: str) -> str:
        import select as _sel

        # 读取第一行
        first = input(prompt)

        # 括号粘贴模式处理
        if _PASTE_START in first:
            body = first.replace(_PASTE_START, "")
            if _PASTE_END in body:
                return body.replace(_PASTE_END, "").strip()

            lines = [body]
            while True:
                ready = _sel.select([sys.stdin], [], [], 2.0)[0]
                if not ready:
                    break
                raw = sys.stdin.readline()
                if not raw:
                    break
                raw = raw.rstrip("\n")
                if _PASTE_END in raw:
                    tail = raw.replace(_PASTE_END, "")
                    if tail:
                        lines.append(tail)
                    break
                lines.append(raw)

            result = "\n".join(lines).strip()
            n = result.count("\n") + 1
            info(f"  (已粘贴 {n} 行)")
            return result

        # 时序备用方案
        if sys.stdin.isatty():
            lines = [first]
            import time as _time

            if sys.platform == "win32":
                import msvcrt
                deadline = 0.12
                chunk_to = 0.03
                t0 = _time.monotonic()
                while (_time.monotonic() - t0) < deadline:
                    _time.sleep(chunk_to)
                    if not msvcrt.kbhit():
                        break
                    raw = sys.stdin.readline()
                    if not raw:
                        break
                    stripped = raw.rstrip("\n").rstrip("\r")
                    lines.append(stripped)
                    t0 = _time.monotonic()
            else:
                deadline = 0.06
                chunk_to = 0.025
                t0 = _time.monotonic()
                while (_time.monotonic() - t0) < deadline:
                    ready = _sel.select([sys.stdin], [], [], chunk_to)[0]
                    if not ready:
                        break
                    raw = sys.stdin.readline()
                    if not raw:
                        break
                    stripped = raw.rstrip("\n")
                    if _PASTE_END in stripped:
                        break
                    lines.append(stripped)
                    t0 = _time.monotonic()

            if len(lines) > 1:
                result = "\n".join(lines).strip()
                info(f"  (已粘贴 {len(lines)} 行)")
                return result

        return first

    while True:
        # 显示后台任务通知
        _print_background_notifications()
        try:
            cwd_short = Path.cwd().name
            prompt = clr(f"\n[{cwd_short}] ", "dim") + clr("» ", "cyan", "bold")
            user_input = _read_input(prompt)
        except (EOFError, KeyboardInterrupt):
            print()
            try:
                save_latest("", state, config)
            except Exception as e:
                warn(f"退出时自动保存失败: {e}")
            _trigger_session_end_memory(state, config, _session_start_time)
            if _bpm_active:
                sys.stdout.write("\x1b[?2004l")
                sys.stdout.flush()
            ok("再见！")
            sys.exit(0)

        if not user_input:
            continue

        result = handle_slash(user_input, state, config)
        # 处理标记命令
        while isinstance(result, tuple):
            # 图片标记
            if result[0] == "__image__":
                _, image_prompt = result
                try:
                    run_query(image_prompt)
                except KeyboardInterrupt:
                    _track_ctrl_c()
                    print(clr("\n  (已中断)", "yellow"))
                break

            # 计划标记
            if result[0] == "__plan__":
                _, plan_desc = result
                try:
                    _plan_file_path = get_plan_file(config)
                    _plan_note = (
                        f"\n\n你已通过 EnterPlanMode 进入计划限制阶段。"
                        f"请把完整计划写入计划文件（{_plan_file_path}），"
                        f"完成后调用 ExitPlanMode。"
                    ) if _plan_file_path else ""
                    run_query(f"请分析代码库并为以下需求制定详细的执行计划: {plan_desc}{_plan_note}")
                except KeyboardInterrupt:
                    _track_ctrl_c()
                    print(clr("\n  (已中断)", "yellow"))
                break

            # 技能匹配
            skill, skill_args = result
            info(f"正在执行技能: {skill.name}")
            try:
                from skill import substitute_arguments
                rendered = substitute_arguments(skill.prompt, skill_args, skill.arguments)
                run_query(f"[技能: {skill.name}]\n\n{rendered}")
            except KeyboardInterrupt:
                _track_ctrl_c()
                print(clr("\n  (已中断)", "yellow"))
            break
        if result:
            continue

        try:
            run_query(user_input)
        except KeyboardInterrupt:
            _track_ctrl_c()
            print(clr("\n  (已中断)", "yellow"))


# 程序入口
def main():
    parser = argparse.ArgumentParser(
        prog="pycc",
        description="pycc — Claude Code 的极简 Python 实现",
        add_help=False,
    )
    parser.add_argument("prompt", nargs="*", help="初始提示词(非交互模式)")
    parser.add_argument("-p", "--print", "--print-output",
                        dest="print_mode", action="store_true",
                        help="非交互模式: 执行提示词后退出")
    parser.add_argument("-m", "--model", help="覆盖模型配置")
    parser.add_argument("--accept-all", action="store_true",
                        help="无需授权确认(接受所有操作)")
    parser.add_argument("--verbose", action="store_true",
                        help="显示思考过程+令牌计数")
    parser.add_argument("--thinking", action="store_true",
                        help="启用扩展思考模式")
    parser.add_argument("--version", action="store_true", help="打印版本信息")
    parser.add_argument("-h", "--help", action="store_true", help="显示帮助")

    args = parser.parse_args()

    if args.version:
        print(f"pycc v{VERSION}")
        sys.exit(0)

    if args.help:
        print(__doc__)
        sys.exit(0)

    from config import load_config, save_config, has_api_key
    from providers import detect_provider, PROVIDERS

    config = load_config()

    # 应用命令行参数覆盖配置
    if args.model:
        m = args.model
        if "/" not in m and ":" in m:
            from providers import PROVIDERS
            left, _ = m.split(":", 1)
            if left in PROVIDERS:
                m = m.replace(":", "/", 1)
        config["model"] = m
    if args.accept_all:
        config["permission_mode"] = "accept-all"
    if args.verbose:
        config["verbose"] = True
    if args.thinking:
        config["thinking"] = True

    # 检查 API 密钥
    if not has_api_key(config):
        pname = detect_provider(config["model"])
        prov  = PROVIDERS.get(pname, {})
        env   = prov.get("api_key_env", "")
        if env:
            warn(f"未找到提供商 '{pname}' 的 API 密钥。"
                 f"设置环境变量 {env} 或执行: /config {pname}_api_key=你的密钥")

    initial = " ".join(args.prompt) if args.prompt else None
    if args.print_mode and not initial:
        err("--print 需要指定提示词参数")
        sys.exit(1)

    repl(config, initial_prompt=initial)


if __name__ == "__main__":
    main()

