#!/usr/bin/env python3
"""
litecc — Claude Code 的极简 Python 实现。

使用方法:
  python litecc.py [选项] [提示词]

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
import os
import argparse
import threading
import time
import uuid as _uuid
from pathlib import Path

import litecc_ui as ui
from litecc_ui import (
    RICH_AVAILABLE,
    console,
    clr,
    info,
    ok,
    warn,
    err,
    stream_text,
    stream_thinking,
    flush_response,
    start_tool_spinner as _start_tool_spinner,
    change_spinner_phrase as _change_spinner_phrase,
    stop_tool_spinner as _stop_tool_spinner,
    print_tool_start,
    print_tool_end,
    print_welcome_banner,
)

from config import load_config, has_api_key, HISTORY_FILE
from providers import detect_provider, PROVIDERS, ensure_provider_catalog_loaded
from litecc_helpers import ask_permission_interactive
from plan_mode import is_plan_mode, get_plan_file
from context import build_system_prompt
from memory.retriever import retrieve_for_query
from agent import AgentState, run, TextChunk, ThinkingChunk, ToolStart, ToolEnd, TurnDone, PermissionRequest
from litecc_command_handlers import (
    configure_command_layer,
    cmd_copy,
    cmd_export,
    cmd_init,
    cmd_status,
    handle_slash,
    print_background_notifications as _print_background_notifications,
    save_latest,
    setup_readline,
    trigger_session_end_memory as _trigger_session_end_memory,
)

# Windows 系统下启用 ANSI 转义码支持
if sys.platform == "win32":
    os.system("")

# 版本号
VERSION = "3.05.5"
_RICH = RICH_AVAILABLE
configure_command_layer(VERSION, __doc__ or "")

# 对话渲染开关
_RICH_LIVE = True

# 主交互循环
def repl(config: dict, initial_prompt: str = None):

    setup_readline(HISTORY_FILE) # 读取历史命令
    state = AgentState() # 初始化agent状态对象
    verbose = config.get("verbose", False)

    # 注入会话标识
    config.setdefault("_session_id", str(_uuid.uuid4()))
    config.setdefault("_cwd", str(Path.cwd()))
    _session_start_time = time.monotonic()
    config["_session_start_time"] = _session_start_time
    # 欢迎横幅
    if not initial_prompt:
        model    = config["model"]
        pname    = detect_provider(model)
        print_welcome_banner(
            version=VERSION,
            model=model,
            provider_name=pname,
            permission_mode=config.get("permission_mode", "auto"),
            plan_active=is_plan_mode(config),
            verbose_enabled=bool(config.get("verbose")),
            thinking_enabled=bool(config.get("thinking")),
        )

    query_lock = threading.RLock()

    # 应用实时渲染配置：自动检测 SSH 和哑终端

    _in_ssh = bool(os.environ.get("SSH_CLIENT") or os.environ.get("SSH_TTY"))
    _is_dumb = (console is not None and getattr(console, "is_dumb_terminal", False))
    _rich_live_default = not _in_ssh and not _is_dumb
    global _RICH_LIVE
    _RICH_LIVE = _RICH and config.get("rich_live", _rich_live_default)
    ui.set_rich_live(_RICH_LIVE)

    # 执行用户查询
    def run_query(user_input: str, is_background: bool = False):
        nonlocal verbose

        with query_lock:
            verbose = config.get("verbose", False)

            # 1.后台记忆检索（与 API 调用并行执行）
            # 检索结果不是给当前查询，而是给后续查询
            _mem_result: dict = {"content": ""}

            def _memory_retrieval_worker() -> None:
                try:
                    # 从记忆中检索
                    _mem_result["content"] = retrieve_for_query(user_input, config)
                except Exception:
                    pass

            _mem_thread = threading.Thread(
                target=_memory_retrieval_worker, daemon=True, name="mem-retrieval"
            )
            _mem_thread.start()

            # 2.重建系统提示词
            system_prompt = build_system_prompt(config)

            print(clr("\n╭─ litecc ", "dim") + clr("●", "green") + clr(" ─────────────────────────", "dim"))

            thinking_started = False # 当前是否正在显示 thinking 流
            spinner_shown = True # 当前是否正在显示加载动画
            _start_tool_spinner() # 初始加载动画
            _pre_tool_text = [] # 工具调用前已经输出的正文
            _post_tool = False # 是否已经发生过工具调用
            _post_tool_buf = [] # 工具调用后新的文本缓存（用于去重）
            _duplicate_suppressed = False # 是否已经进入“去重模式”

            try:
                # 3.运行主循环
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
                        # 处理普通文本输出
                        if thinking_started:
                            # 如果之前在显示 thinking，先结束 thinking 显示
                            print("\033[0m\n")
                            thinking_started = False

                        if _post_tool and not _duplicate_suppressed:
                            # 如果之前已经发生过工具调用，且当前不是去重模式
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
                        # 处理思考流
                        if verbose:
                            if not thinking_started:
                                flush_response()
                                print(clr("  [思考中]", "dim"))
                                thinking_started = True
                            stream_thinking(event.text, verbose)

                    elif isinstance(event, ToolStart):
                        # 处理工具调用
                        flush_response()
                        print_tool_start(event.name, event.inputs, verbose)

                    elif isinstance(event, PermissionRequest):
                        # 处理权限请求
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
                        # 处理工具执行结果
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
                        # 处理回合结束
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

    # 处理初始提示，直接运行查询
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

        # 判断当前程序是不是【在终端里手动输入】，还是【从文件 / 管道读取输入】
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
            # 执行模型请求
            run_query(user_input)
        except KeyboardInterrupt:
            _track_ctrl_c()
            print(clr("\n  (已中断)", "yellow"))


# 程序入口
def main():
    parser = argparse.ArgumentParser(prog="litecc",add_help=False,)
    parser.add_argument("prompt", nargs="*", help="初始提示词(非交互模式)")
    parser.add_argument("-p", "--print", "--print-output",dest="print_mode", action="store_true",help="非交互模式: 执行提示词后退出")
    parser.add_argument("-m", "--model", help="覆盖模型配置")
    parser.add_argument("--accept-all", action="store_true",help="无需授权确认(接受所有操作)")
    parser.add_argument("--verbose", action="store_true",help="显示思考过程+令牌计数")
    parser.add_argument("--thinking", action="store_true",help="启用扩展思考模式")
    parser.add_argument("--version", action="store_true",help="打印版本信息")
    parser.add_argument("-h", "--help", action="store_true",help="显示帮助")
    args = parser.parse_args()

    if args.version:
        print(f"litecc v{VERSION}")
        sys.exit(0)

    if args.help:
        print(__doc__)
        sys.exit(0)

    # 确保提供商目录加载
    ensure_provider_catalog_loaded()

    # 加载配置
    config = load_config()

    # 应用命令行参数覆盖配置
    if args.model:
        m = args.model
        if "/" not in m and ":" in m:
            ensure_provider_catalog_loaded()
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
        secrets_path = ".litecc/secrets.json"
        if env:
            warn(f"未找到提供商 '{pname}' 的 API 密钥。"
                 f"设置环境变量 {env} 或在项目中创建 {secrets_path}")

    initial = " ".join(args.prompt) if args.prompt else None
    if args.print_mode and not initial:
        err("--print 需要指定提示词参数")
        sys.exit(1)

    # 交互模式
    repl(config, initial_prompt=initial)

if __name__ == "__main__":
    main()
