from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Union

from tools import ask_input_interactive
from plan_mode import is_plan_mode, enter_plan_mode, exit_plan_mode, get_plan_file
import litecc_background as bg_jobs
import litecc_sessions as session_store
import litecc_ui as ui
from litecc_commands import CommandRegistry


APP_VERSION = "unknown"
HELP_TEXT = ""


def configure_command_layer(version: str, help_text: str) -> None:
    global APP_VERSION, HELP_TEXT, _COMMAND_REGISTRY
    APP_VERSION = version
    HELP_TEXT = help_text
    _COMMAND_REGISTRY = None


def clr(text: str, *keys: str) -> str:
    return ui.clr(text, *keys)


def info(msg: str):
    ui.info(msg)


def ok(msg: str):
    ui.ok(msg)


def warn(msg: str):
    ui.warn(msg)


def err(msg: str):
    ui.err(msg)


def save_latest(_args: str, state, config=None) -> bool:
    result = session_store.save_latest_session(state, config)
    if result is None:
        return True

    ok(f"会话已保存 -> {result['latest_path']}")
    ok(f"             -> {result['daily_path']}  (ID: {result['session_id']})")
    ok(
        "             -> "
        f"{result['history_path']}  ({result['history_sessions']} 个会话 / "
        f"{result['history_turns']} 总轮次)"
    )
    return True


def trigger_session_end_memory(state, config: dict, start_time: float | None = None) -> None:
    bg_jobs.trigger_session_end_memory(state, config, start_time)


def print_background_notifications() -> None:
    bg_jobs.print_background_notifications(clr)


def cmd_help(_args: str, _state, _config) -> bool:
    print(HELP_TEXT)
    return True


def cmd_model(args: str, _state, config) -> bool:
    from providers import PROVIDERS, detect_provider, ensure_provider_catalog_loaded

    ensure_provider_catalog_loaded()
    if not args:
        model = config["model"]
        pname = detect_provider(model)
        info(f"当前模型:    {model}  (服务提供商: {pname})")
        info("\n各提供商可用模型:")
        for pn, pdata in PROVIDERS.items():
            models = pdata.get("models", [])
            if models:
                info(f"  {pn:12s}  " + ", ".join(models[:4]) + ("..." if len(models) > 4 else ""))
        info("\n格式: '提供商/模型' 或直接输入模型名(自动检测)")
        info("  例如: /model gpt-4o")
        info("  例如: /model deepseek/deepseek-v4-pro")
        info("  例如: /model kimi:moonshot-v1-32k")
        return True

    model_name = args.strip()
    if "/" not in model_name and ":" in model_name:
        left, right = model_name.split(":", 1)
        if left in PROVIDERS:
            model_name = f"{left}/{right}"

    config["model"] = model_name
    pname = detect_provider(model_name)
    ok(f"模型已设置为 {model_name}  (服务提供商: {pname})")

    from config import save_config

    save_config(config)
    return True


def cmd_clear(_args: str, state, _config) -> bool:
    state.messages.clear()
    state.turn_count = 0
    ok("对话已清空。")
    return True


def cmd_config(args: str, _state, config) -> bool:
    from config import save_config

    def _is_secret_key(key: str) -> bool:
        return key == "api_key" or key.endswith("_api_key")

    def _display_value(key: str, value):
        if _is_secret_key(key):
            return "<hidden>" if value else ""
        return value

    if not args:
        display = {k: _display_value(k, v) for k, v in config.items() if not k.startswith("_")}
        print(json.dumps(display, indent=2))
        return True

    if "=" in args:
        key, _, val = args.partition("=")
        key, val = key.strip(), val.strip()
        if val.lower() in ("true", "false"):
            val = val.lower() == "true"
        elif val.isdigit():
            val = int(val)
        config[key] = val
        save_config(config)
        ok(f"已设置 {key} = {_display_value(key, val)}")
        return True

    key = args.strip()
    value = _display_value(key, config.get(key, "(未设置)"))
    info(f"{key} = {value}")
    return True


def cmd_save(args: str, state, _config) -> bool:
    from config import SESSIONS_DIR

    session_id = uuid.uuid4().hex[:8]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = args.strip() or f"session_{ts}_{session_id}.json"
    path = Path(fname) if "/" in fname or "\\" in fname else SESSIONS_DIR / fname
    saved = session_store.save_named_session(state, path, session_id=session_id)
    ok(f"会话已保存 -> {saved['path']}  (ID: {saved['data']['session_id']})")
    return True


def cmd_load(args: str, state, config) -> bool:
    from config import SESSION_HIST_FILE

    path: Path | None = None
    if not args.strip():
        sessions = session_store.collect_saved_sessions()
        if not sessions:
            info("未找到保存的会话。")
            return True

        print(clr("  选择要加载的会话:", "cyan", "bold"))
        menu_buf = clr("  选择要加载的会话:", "cyan", "bold")
        prev_date = None
        for i, session_path in enumerate(sessions):
            date_label = session_path.parent.name if session_path.parent.name != "mr_sessions" else ""
            if date_label and date_label != prev_date:
                section = clr(f"\n  ── {date_label} ──", "dim")
                print(section)
                menu_buf += "\n" + section
                prev_date = date_label

            label = session_path.name
            try:
                meta = session_store.load_session_file(session_path)
                saved_at = meta.get("saved_at", "")[-8:]
                sid = meta.get("session_id", "")
                turns = meta.get("turn_count", "?")
                label = f"{saved_at}  ID:{sid}  轮次:{turns}  {session_path.name}"
            except Exception:
                pass

            entry = clr(f"  [{i+1:2d}] ", "yellow") + label
            print(entry)
            menu_buf += "\n" + entry

        has_history = SESSION_HIST_FILE.exists()
        if has_history:
            try:
                hist_meta = json.loads(SESSION_HIST_FILE.read_text(encoding="utf-8"))
                n_sess = len(hist_meta.get("sessions", []))
                n_turns = hist_meta.get("total_turns", 0)
                history_title = clr("\n  ── 完整历史记录 ──", "dim")
                history_entry = clr("  [ H] ", "yellow") + (
                    f"加载全部历史  ({n_sess} 个会话 / {n_turns} 总轮次)  {SESSION_HIST_FILE}"
                )
                print(history_title)
                print(history_entry)
                menu_buf += "\n" + history_title + "\n" + history_entry
            except Exception:
                has_history = False

        print()
        ans = ask_input_interactive(
            clr("  输入序号(例如 1 或 1,2,3)，H 加载全部历史，回车取消> ", "cyan"),
            config,
            menu_buf,
        ).strip().lower()

        if not ans:
            info("  已取消。")
            return True

        if ans == "h":
            if not has_history:
                err("未找到历史记录文件。")
                return True

            hist_data = json.loads(SESSION_HIST_FILE.read_text(encoding="utf-8"))
            all_sessions = hist_data.get("sessions", [])
            if not all_sessions:
                info("历史记录为空。")
                return True

            all_messages: list[dict] = []
            for saved_session in all_sessions:
                all_messages.extend(saved_session.get("messages", []))

            total_turns = sum(saved_session.get("turn_count", 0) for saved_session in all_sessions)
            est_tokens = sum(len(str(msg.get("content", ""))) for msg in all_messages) // 4
            print()
            print(clr(f"  {len(all_messages)} 条消息 / 预估约 {est_tokens:,} 令牌", "dim"))
            confirm = ask_input_interactive(
                clr("  将全部历史加载到当前会话？[y/N] > ", "yellow"),
                config,
            ).strip().lower()
            if confirm != "y":
                info("  已取消。")
                return True

            session_store.restore_state_from_data(
                state,
                {
                    "messages": all_messages,
                    "turn_count": total_turns,
                    "total_input_tokens": 0,
                    "total_output_tokens": 0,
                },
            )
            ok(f"已从 {SESSION_HIST_FILE} 加载全部历史 ({len(all_messages)} 条消息，{len(all_sessions)} 个会话)")
            return True

        raw_parts = [p.strip() for p in ans.split(",")]
        indices: list[int] = []
        for part in raw_parts:
            if not part.isdigit():
                err(f"无效输入 '{part}'，请输入数字或 H。")
                return True
            idx = int(part) - 1
            if idx < 0 or idx >= len(sessions):
                err(f"无效选择: {part} (有效范围: 1-{len(sessions)})")
                return True
            if idx not in indices:
                indices.append(idx)

        if len(indices) == 1:
            path = sessions[indices[0]]
        else:
            merged_messages: list[dict] = []
            total_turns = 0
            loaded_names: list[str] = []
            for idx in indices:
                session_path = sessions[idx]
                session_data = session_store.load_session_file(session_path)
                merged_messages.extend(session_data.get("messages", []))
                total_turns += session_data.get("turn_count", 0)
                loaded_names.append(session_path.name)

            est_tokens = sum(len(str(msg.get("content", ""))) for msg in merged_messages) // 4
            print()
            print(clr(f"  {len(loaded_names)} 个会话 / {len(merged_messages)} 条消息 / 预估约 {est_tokens:,} 令牌", "dim"))
            confirm = ask_input_interactive(clr("  合并并加载？[y/N] > ", "yellow"), config).strip().lower()
            if confirm != "y":
                info("  已取消。")
                return True

            session_store.restore_state_from_data(
                state,
                {
                    "messages": merged_messages,
                    "turn_count": total_turns,
                    "total_input_tokens": 0,
                    "total_output_tokens": 0,
                },
            )
            ok(f"已加载 {len(loaded_names)} 个会话 ({len(merged_messages)} 条消息): {', '.join(loaded_names)}")
            return True

    if path is None:
        path = session_store.resolve_session_path(args.strip())
        if not path.exists():
            err(f"文件不存在: {path}")
            return True

    data = session_store.load_session_file(path)
    session_store.restore_state_from_data(state, data)
    ok(f"已从 {path} 加载会话 ({len(state.messages)} 条消息)")
    return True


def cmd_resume(args: str, state, _config) -> bool:
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

    data = session_store.load_session_file(path)
    session_store.restore_state_from_data(state, data)
    ok(f"已从 {path} 加载会话 ({len(state.messages)} 条消息)")
    return True


def cmd_history(_args: str, state, _config) -> bool:
    if not state.messages:
        info("(对话为空)")
        return True

    for i, message in enumerate(state.messages):
        role = clr(message["role"].upper(), "bold", "cyan" if message["role"] == "user" else "green")
        content = message["content"]
        if isinstance(content, str):
            print(f"[{i}] {role}: {content[:200]}")
            continue

        for block in content:
            if isinstance(block, dict):
                block_type = block.get("type", "")
            else:
                block_type = getattr(block, "type", "")

            if block_type == "text":
                text = block.get("text", "") if isinstance(block, dict) else block.text
                print(f"[{i}] {role}: {text[:200]}")
            elif block_type == "tool_use":
                name = block.get("name", "") if isinstance(block, dict) else block.name
                print(f"[{i}] {role}: [工具调用: {name}]")
            elif block_type == "tool_result":
                cval = block.get("content", "") if isinstance(block, dict) else block.content
                print(f"[{i}] {role}: [工具结果: {str(cval)[:100]}]")

    return True


def cmd_context(_args: str, state, config) -> bool:
    msg_chars = sum(len(str(message.get("content", ""))) for message in state.messages)
    est_tokens = msg_chars // 4
    info(f"消息数:         {len(state.messages)}")
    info(f"预估令牌数: ~{est_tokens:,}")
    info(f"模型:            {config['model']}")
    info(f"最大令牌数:       {config['max_tokens']:,}")
    return True


def cmd_cost(_args: str, state, config) -> bool:
    from config import calc_cost

    cost = calc_cost(config["model"], state.total_input_tokens, state.total_output_tokens)
    info(f"输入令牌:  {state.total_input_tokens:,}")
    info(f"输出令牌: {state.total_output_tokens:,}")
    info(f"预估费用:     ${cost:.4f} 美元")
    return True


def cmd_verbose(_args: str, _state, config) -> bool:
    from config import save_config

    config["verbose"] = not config.get("verbose", False)
    state_str = "开启" if config["verbose"] else "关闭"
    ok(f"详细模式: {state_str}")
    save_config(config)
    return True


def cmd_thinking(_args: str, _state, config) -> bool:
    from config import save_config

    config["thinking"] = not config.get("thinking", False)
    state_str = "开启" if config["thinking"] else "关闭"
    ok(f"扩展思考模式: {state_str}")
    save_config(config)
    return True


def cmd_permissions(args: str, _state, config) -> bool:
    from config import save_config

    modes = ["auto", "accept-all", "manual"]
    mode_desc = {
        "auto": "每次工具调用都询问(默认)",
        "accept-all": "静默允许所有工具调用",
        "manual": "每次工具调用都询问(严格模式)",
    }
    if not args.strip():
        current = config.get("permission_mode", "auto")
        menu_buf = clr("\n  ── 权限模式 ──", "dim")
        for i, mode in enumerate(modes):
            marker = clr("●", "green") if mode == current else clr("●", "dim")
            menu_buf += f"\n  {marker} {clr(f'[{i+1}]', 'yellow')} {clr(mode, 'cyan')}  {clr(mode_desc[mode], 'dim')}"
        print(menu_buf)
        print()
        try:
            ans = ask_input_interactive(clr("  选择模式序号或回车取消> ", "cyan"), config, menu_buf).strip()
        except (KeyboardInterrupt, EOFError):
            print()
            return True
        if not ans:
            return True
        if ans.isdigit() and 1 <= int(ans) <= len(modes):
            mode = modes[int(ans) - 1]
            config["permission_mode"] = mode
            save_config(config)
            ok(f"权限模式已设置为: {mode}")
        else:
            err("无效选择。")
        return True

    mode = args.strip()
    if mode not in modes:
        err(f"未知模式: {mode}，可选: {', '.join(modes)}")
    else:
        config["permission_mode"] = mode
        save_config(config)
        ok(f"权限模式已设置为: {mode}")
    return True


def cmd_cwd(args: str, _state, _config) -> bool:
    if not args.strip():
        info(f"当前工作目录: {os.getcwd()}")
        return True

    path = args.strip()
    try:
        os.chdir(path)
        ok(f"已切换目录至: {os.getcwd()}")
    except Exception as exc:
        err(str(exc))
    return True


def cmd_exit(_args: str, state, config) -> bool:
    if sys.stdin.isatty() and sys.platform != "win32":
        sys.stdout.write("\x1b[?2004l")
        sys.stdout.flush()
    ok("再见！")
    save_latest("", state, config)
    trigger_session_end_memory(state, config)
    sys.exit(0)


def cmd_memory(args: str, _state, config) -> bool:
    from memory import search_memory
    from memory.scan import scan_all_memories, memory_freshness_text

    stripped = args.strip()
    if stripped == "consolidate":
        info("正在整合记忆(使用轻量模型)…")
        try:
            from memory.dream import consolidate

            result = consolidate(config)
            ok(result)
        except Exception as exc:
            err(f"整合失败: {exc}")
        return True

    if stripped:
        results = search_memory(stripped)
        if not results:
            info(f"未找到匹配 '{stripped}' 的记忆")
            return True
        info(f"  找到 {len(results)} 条结果匹配 '{stripped}':")
        for memory in results:
            info(f"  [{memory.type:9s}|{memory.scope:7s}] {memory.name}: {memory.description}")
            info(f"    {memory.content[:120]}{'...' if len(memory.content) > 120 else ''}")
        return True

    headers = scan_all_memories()
    if not headers:
        info("未存储任何记忆，模型将通过 MemorySave 保存记忆。")
        return True
    info(f"  共 {len(headers)} 条记忆，最新优先:")
    for header in headers:
        fresh_warn = "  ⚠ 已过期" if memory_freshness_text(header.mtime_s) else ""
        tag = f"[{header.type or '?':9s}|{header.scope:7s}]"
        info(f"  {tag} {header.filename}{fresh_warn}")
        if header.description:
            info(f"    {header.description}")
    return True


def cmd_agents(_args: str, _state, _config) -> bool:
    try:
        from multi_agent.tools import get_agent_manager

        mgr = get_agent_manager()
        tasks = mgr.list_tasks()
        if not tasks:
            info("无子代理任务。")
            return True
        info(f"  {len(tasks)} 个子代理任务:")
        for task in tasks:
            preview = task.prompt[:50] + ("..." if len(task.prompt) > 50 else "")
            wt_info = f"  分支:{task.worktree_branch}" if task.worktree_branch else ""
            info(f"  {task.id} [{task.status:9s}] 名称={task.name}{wt_info}  {preview}")
    except Exception:
        info("子代理系统未初始化。")
    return True


def cmd_skills(_args: str, _state, _config) -> bool:
    from skill import load_skills

    skills = load_skills()
    if not skills:
        info("未找到任何技能。")
        return True
    info(f"可用技能({len(skills)}):")
    for skill in skills:
        triggers = ", ".join(skill.triggers)
        source_label = f"[{skill.source}]" if skill.source != "builtin" else ""
        hint = f"  参数: {skill.argument_hint}" if skill.argument_hint else ""
        print(
            f"  {clr(skill.name, 'cyan'):24s} {skill.description}  "
            f"{clr(triggers, 'dim')}{hint} {clr(source_label, 'yellow')}"
        )
        if skill.when_to_use:
            print(f"    {clr(skill.when_to_use[:80], 'dim')}")
    return True


def cmd_mcp(args: str, _state, _config) -> bool:
    from mcp.client import get_mcp_manager
    from mcp.config import (
        add_server_to_user_config,
        list_config_files,
        load_mcp_configs,
        remove_server_from_user_config,
    )
    from mcp.tools import refresh_server, reload_mcp

    parts = args.split() if args.strip() else []
    subcmd = parts[0].lower() if parts else ""

    if subcmd == "reload":
        target = parts[1] if len(parts) > 1 else ""
        if target:
            reload_error = refresh_server(target)
            if reload_error:
                err(f"重新加载 '{target}' 失败: {reload_error}")
            else:
                ok(f"已重新加载 MCP 服务器: {target}")
        else:
            errors = reload_mcp()
            for name, reload_error in errors.items():
                if reload_error:
                    print(f"  {clr('✗', 'red')} {name}: {reload_error}")
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

    mgr = get_mcp_manager()
    servers = mgr.list_servers()

    config_files = list_config_files()
    if config_files:
        info(f"配置文件: {', '.join(str(path) for path in config_files)}")

    if not servers:
        configs = load_mcp_configs()
        if not configs:
            info("未配置任何 MCP 服务器。")
            info("可在 ~/.litecc/mcp.json 或 .mcp.json 中添加服务器")
            info("示例: /mcp add my-git uvx mcp-server-git")
        else:
            info("已配置 MCP 服务器但未连接，执行 /mcp reload")
        return True

    info(f"MCP 服务器({len(servers)}):")
    total_tools = 0
    for client in servers:
        status_color = {
            "connected": "green",
            "connecting": "yellow",
            "disconnected": "dim",
            "error": "red",
        }.get(client.state.value, "dim")
        print(f"  {clr(client.status_line(), status_color)}")
        for tool in client._tools:
            print(f"      {clr(tool.qualified_name, 'cyan')}  {tool.description[:60]}")
            total_tools += 1

    if total_tools:
        info(f"总计: {total_tools} 个 MCP 工具可供 Claude 使用")
    return True


def cmd_tasks(args: str, _state, _config) -> bool:
    from task import clear_all_tasks, create_task, delete_task, get_task, list_tasks, update_task
    from task.types import TaskStatus

    parts = args.split(None, 1)
    subcmd = parts[0].lower() if parts else ""
    rest = parts[1].strip() if len(parts) > 1 else ""

    status_map = {
        "done": "completed",
        "start": "in_progress",
        "cancel": "cancelled",
    }

    if not subcmd:
        tasks = list_tasks()
        if not tasks:
            info("暂无任务，可使用 TaskCreate 工具或 /tasks create <主题> 创建。")
            return True
        total = len(tasks)
        done = sum(1 for task in tasks if task.status == TaskStatus.COMPLETED)
        info(f"任务({done}/{total} 已完成):")
        for task in tasks:
            owner_str = f" {clr(f'({task.owner})', 'dim')}" if task.owner else ""
            status_color = {
                TaskStatus.PENDING: "dim",
                TaskStatus.IN_PROGRESS: "cyan",
                TaskStatus.COMPLETED: "green",
                TaskStatus.CANCELLED: "red",
            }.get(task.status, "dim")
            icon = task.status_icon()
            print(f"  #{task.id} {clr(icon + ' ' + task.status.value, status_color)} {task.subject}{owner_str}")
        return True

    if subcmd == "create":
        if not rest:
            err("用法: /tasks create <主题>")
            return True
        task = create_task(rest, description="(通过交互模式创建)")
        ok(f"任务 #{task.id} 已创建: {task.subject}")
        return True

    if subcmd in status_map:
        new_status = status_map[subcmd]
        if not rest:
            err(f"用法: /tasks {subcmd} <任务ID>")
            return True
        task, _fields = update_task(rest, status=new_status)
        if task is None:
            err(f"未找到任务 #{rest}。")
        else:
            ok(f"任务 #{task.id} -> {new_status}: {task.subject}")
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
        task = get_task(rest)
        if task is None:
            err(f"未找到任务 #{rest}。")
            return True
        print(f"  #{task.id} [{task.status.value}] {task.subject}")
        print(f"  描述: {task.description}")
        if task.owner:
            print(f"  负责人:       {task.owner}")
        if task.active_form:
            print(f"  激活表单: {task.active_form}")
        if task.metadata:
            print(f"  元数据:    {task.metadata}")
        print(f"  创建时间: {task.created_at[:19]}  更新时间: {task.updated_at[:19]}")
        return True

    if subcmd == "clear":
        clear_all_tasks()
        ok("所有任务已删除。")
        return True

    err(f"未知任务子命令: {subcmd}  (尝试 /tasks 或 /help)")
    return True


def cmd_image(args: str, _state, config) -> Union[bool, tuple]:
    try:
        import base64
        import io
        from PIL import ImageGrab
    except ImportError:
        err("需要安装 Pillow 才能使用 /image，命令: pip install litecc[vision]")
        if sys.platform == "linux":
            err("Linux 系统还需要安装 xclip: sudo apt install xclip")
        return True

    img = ImageGrab.grabclipboard()
    if img is None:
        if sys.platform == "linux":
            err(
                "剪贴板中未找到图片，Linux 系统需要 xclip (sudo apt install xclip)。"
                "使用 Flameshot、GNOME 截图工具复制图片，或执行: "
                "xclip -selection clipboard -t image/png -i 文件名.png"
            )
        elif sys.platform == "darwin":
            err("剪贴板中未找到图片，请先复制图片(Cmd+Ctrl+Shift+4 可将截图区域复制到剪贴板)。")
        else:
            err("剪贴板中未找到图片，请先复制图片(Win+Shift+S 可将截图区域复制到剪贴板)。")
        return True

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    size_kb = len(buf.getvalue()) / 1024

    info(f"📲 已捕获剪贴板图片 ({size_kb:.0f} KB，{img.size[0]}x{img.size[1]})")
    config["_pending_image"] = b64

    prompt = args.strip() if args.strip() else "你在这张图片中看到了什么？详细描述一下。"
    return ("__image__", prompt)


def cmd_plan(args: str, _state, config) -> Union[bool, tuple]:
    arg = args.strip()

    plan_file = get_plan_file(config)
    in_plan_mode = is_plan_mode(config)

    if arg == "done":
        if not in_plan_mode:
            err("未处于计划模式。")
            return True
        exit_plan_mode(config, require_nonempty=False)
        info("计划限制层已停用。")
        if plan_file:
            info(f"计划已保存至: {plan_file}")
            info("现在可以让 Claude 执行该计划。")
        return True

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

    if not arg:
        if not plan_file:
            info("未处于计划模式，使用 /plan <描述> 开始制定计划。")
            return True
        path = Path(plan_file)
        if path.exists() and path.stat().st_size > 0:
            info(f"计划文件: {plan_file}")
            print(path.read_text(encoding="utf-8"))
        else:
            info(f"计划文件为空: {plan_file}")
        return True

    if in_plan_mode:
        err("已处于计划模式，先使用 /plan done 退出。")
        return True

    _message, plan_path = enter_plan_mode(config, arg)
    info("计划限制层已激活(仅计划文件可写入)。")
    info(f"计划文件: {plan_path}")
    info("使用 /plan done 退出并开始执行。")
    print()
    return ("__plan__", arg)


def cmd_compact(args: str, state, config) -> bool:
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


def cmd_init(_args: str, _state, _config) -> bool:
    target = Path.cwd() / "CLAUDE.md"
    if target.exists():
        err(f"{target} 已存在")
        info("直接编辑或先删除该文件。")
        return True

    project_name = Path.cwd().name
    template = (
        f"# {project_name}\n\n"
        "## 项目概览\n"
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


def cmd_export(args: str, state, _config) -> bool:
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
        out_path.write_text(json.dumps(state.messages, indent=2, ensure_ascii=False), encoding="utf-8")
    else:
        lines = []
        for message in state.messages:
            role = message.get("role", "unknown")
            content = message.get("content", "")
            if isinstance(content, list):
                content = "(结构化内容)"
            if role == "user":
                lines.append(f"## 用户\n\n{content}\n")
            elif role == "assistant":
                lines.append(f"## 助手\n\n{content}\n")
            elif role == "tool":
                name = message.get("name", "tool")
                lines.append(f"### 工具: {name}\n\n```\n{content[:2000]}\n```\n")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text("\n".join(lines), encoding="utf-8")

    info(f"已导出 {len(state.messages)} 条消息到 {out_path}")
    return True


def cmd_copy(_args: str, state, _config) -> bool:
    last_reply = None
    for message in reversed(state.messages):
        if message.get("role") == "assistant":
            content = message.get("content", "")
            if isinstance(content, str) and content.strip():
                last_reply = content
                break

    if not last_reply:
        err("无助手回复可复制。")
        return True

    try:
        import subprocess as _sp

        if sys.platform == "win32":
            proc = _sp.Popen(["clip"], stdin=_sp.PIPE)
            proc.communicate(last_reply.encode("utf-16le"))
        elif sys.platform == "darwin":
            proc = _sp.Popen(["pbcopy"], stdin=_sp.PIPE)
            proc.communicate(last_reply.encode("utf-8"))
        else:
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
    except Exception as exc:
        err(f"复制失败: {exc}")
    return True


def cmd_status(_args: str, state, config) -> bool:
    from compaction import estimate_tokens, get_context_limit
    from providers import detect_provider

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

    print(f"  版本:     {APP_VERSION}")
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


def cmd_doctor(_args: str, _state, config) -> bool:
    import subprocess as _sp
    from providers import PROVIDERS, detect_provider, get_api_key

    ok_n = warn_n = fail_n = 0

    def _print_safe(text: str):
        try:
            print(text)
        except UnicodeEncodeError:
            print(text.encode("ascii", errors="replace").decode())

    def doctor_ok(msg: str):
        nonlocal ok_n
        ok_n += 1
        _print_safe(clr("  [通过] ", "green") + msg)

    def doctor_warn(msg: str):
        nonlocal warn_n
        warn_n += 1
        _print_safe(clr("  [警告] ", "yellow") + msg)

    def doctor_fail(msg: str):
        nonlocal fail_n
        fail_n += 1
        _print_safe(clr("  [失败] ", "red") + msg)

    info("正在运行诊断...")
    print()

    version_info = sys.version_info
    if version_info >= (3, 10):
        doctor_ok(f"Python {version_info.major}.{version_info.minor}.{version_info.micro}")
    else:
        doctor_fail(f"Python {version_info.major}.{version_info.minor}.{version_info.micro} (需要 >= 3.10)")

    try:
        result = _sp.run(["git", "--version"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            doctor_ok(f"Git: {result.stdout.strip()}")
        else:
            doctor_fail("Git: 工作异常")
    except Exception:
        doctor_fail("Git: 未找到")

    try:
        result = _sp.run(["git", "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            doctor_ok("位于 Git 仓库内")
        else:
            doctor_warn("未位于 Git 仓库内")
    except Exception:
        doctor_warn("无法检查 Git 仓库状态")

    model = config.get("model", "")
    provider = detect_provider(model)
    key = get_api_key(provider, config)

    if key:
        doctor_ok(f"{provider} 的 API 密钥: 已设置 ({key[:4]}...{key[-4:]})")
    else:
        doctor_fail(f"{provider} 的 API 密钥: 未设置")

    if key:
        print(f"  ... 正在测试 {provider} API 连接...")
        try:
            import urllib.error
            import urllib.request

            prov = PROVIDERS.get(provider, {})
            ptype = prov.get("type", "openai")

            if ptype == "anthropic":
                req = urllib.request.Request(
                    "https://api.anthropic.com/v1/messages",
                    data=json.dumps(
                        {
                            "model": model,
                            "max_tokens": 1,
                            "messages": [{"role": "user", "content": "hi"}],
                        }
                    ).encode(),
                    headers={
                        "x-api-key": key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                )
                try:
                    urllib.request.urlopen(req, timeout=10)
                    doctor_ok(f"Anthropic API: 可访问，模型 {model} 正常")
                except urllib.error.HTTPError as exc:
                    if exc.code == 401:
                        doctor_fail("Anthropic API: 无效的 API 密钥 (401)")
                    elif exc.code == 404:
                        doctor_fail(f"Anthropic API: 未找到模型 {model} (404)")
                    elif exc.code == 429:
                        doctor_warn("Anthropic API: 请求频率受限 (429) — 密钥有效")
                    else:
                        doctor_warn(f"Anthropic API: HTTP {exc.code}")
                except Exception as exc:
                    doctor_fail(f"Anthropic API: 连接错误 — {exc}")
            else:
                base = prov.get("base_url", "")
                if provider == "custom":
                    base = config.get("custom_base_url", base or "")
                if base:
                    models_url = base.rstrip("/") + "/models"
                    req = urllib.request.Request(models_url, headers={"Authorization": f"Bearer {key}"})
                    try:
                        urllib.request.urlopen(req, timeout=10)
                        doctor_ok(f"{provider} API: 可访问")
                    except urllib.error.HTTPError as exc:
                        if exc.code == 401:
                            doctor_fail(f"{provider} API: 无效的 API 密钥 (401)")
                        elif exc.code == 429:
                            doctor_warn(f"{provider} API: 请求频率受限 (429) — 密钥有效")
                        else:
                            doctor_warn(f"{provider} API: HTTP {exc.code}")
                    except Exception as exc:
                        doctor_fail(f"{provider} API: 连接错误 — {exc}")
                else:
                    doctor_warn(f"{provider}: 未配置基础地址")
        except Exception as exc:
            doctor_warn(f"API 测试跳过: {exc}")

    print()
    for pname, pdata in PROVIDERS.items():
        if pname == provider:
            continue
        env_var = pdata.get("api_key_env")
        if env_var and os.environ.get(env_var, ""):
            doctor_ok(f"{pname} 密钥 ({env_var}): 已设置")

    print()
    for mod, desc in [
        ("rich", "Rich (实时 Markdown 渲染)"),
        ("PIL", "Pillow (剪贴板图片/image)"),
        ("sounddevice", "sounddevice (语音录制)"),
        ("faster_whisper", "faster-whisper (本地语音识别)"),
    ]:
        try:
            __import__(mod)
            doctor_ok(desc)
        except ImportError:
            doctor_warn(f"{desc}: 未安装")

    print()
    claude_md = Path.cwd() / "CLAUDE.md"
    global_md = Path.home() / ".claude" / "CLAUDE.md"
    if claude_md.exists():
        doctor_ok(f"项目 CLAUDE.md: {claude_md}")
    else:
        doctor_warn("无项目 CLAUDE.md (执行 /init 创建)")
    if global_md.exists():
        doctor_ok(f"全局 CLAUDE.md: {global_md}")

    perm = config.get("permission_mode", "auto")
    if perm == "accept-all":
        doctor_warn(f"权限模式: {perm} (所有操作自动允许)")
    else:
        doctor_ok(f"权限模式: {perm}")

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


COMMANDS = {
    "help": cmd_help,
    "clear": cmd_clear,
    "model": cmd_model,
    "config": cmd_config,
    "save": cmd_save,
    "load": cmd_load,
    "history": cmd_history,
    "context": cmd_context,
    "cost": cmd_cost,
    "verbose": cmd_verbose,
    "thinking": cmd_thinking,
    "permissions": cmd_permissions,
    "cwd": cmd_cwd,
    "skills": cmd_skills,
    "memory": cmd_memory,
    "agents": cmd_agents,
    "mcp": cmd_mcp,
    "tasks": cmd_tasks,
    "task": cmd_tasks,
    "image": cmd_image,
    "img": cmd_image,
    "plan": cmd_plan,
    "compact": cmd_compact,
    "init": cmd_init,
    "export": cmd_export,
    "copy": cmd_copy,
    "status": cmd_status,
    "doctor": cmd_doctor,
    "exit": cmd_exit,
    "quit": cmd_exit,
    "resume": cmd_resume,
}


COMMAND_META: dict[str, tuple[str, list[str]]] = {
    "help": ("显示帮助", []),
    "clear": ("清空对话历史", []),
    "model": ("查看/设置模型", []),
    "config": ("查看/设置配置 key=value", []),
    "save": ("保存会话到文件", []),
    "load": ("加载保存的会话", []),
    "history": ("显示对话历史", []),
    "context": ("显示令牌上下文使用", []),
    "cost": ("显示费用估算", []),
    "verbose": ("切换详细输出", []),
    "thinking": ("切换扩展思考", []),
    "permissions": ("设置权限模式", ["auto", "accept-all", "manual"]),
    "cwd": ("查看/切换工作目录", []),
    "skills": ("列出可用技能", []),
    "memory": ("搜索/列出记忆", []),
    "agents": ("显示后台代理", []),
    "mcp": ("管理 MCP 服务器", ["reload", "add", "remove"]),
    "tasks": ("管理任务", ["create", "delete", "get", "clear", "todo", "in-progress", "done", "blocked"]),
    "task": ("管理任务(别名)", ["create", "delete", "get", "clear", "todo", "in-progress", "done", "blocked"]),
    "image": ("发送剪贴板图片给模型", []),
    "img": ("发送剪贴板图片(别名)", []),
    "plan": ("进入/退出计划模式", ["done", "status"]),
    "compact": ("压缩对话历史", []),
    "init": ("初始化 CLAUDE.md 模板", []),
    "export": ("导出对话到文件", []),
    "copy": ("复制最后回复到剪贴板", []),
    "status": ("显示会话状态和模型信息", []),
    "doctor": ("诊断安装环境", []),
    "exit": ("退出 litecc", []),
    "quit": ("退出(别名 /exit)", []),
    "resume": ("恢复最近会话", []),
}


_COMMAND_REGISTRY: CommandRegistry | None = None


def _get_command_registry() -> CommandRegistry:
    global _COMMAND_REGISTRY
    if _COMMAND_REGISTRY is None:
        registry = CommandRegistry()
        for name, handler in COMMANDS.items():
            desc, subs = COMMAND_META.get(name, ("", []))
            registry.register(name, handler, description=desc, subcommands=subs)
        _COMMAND_REGISTRY = registry
    return _COMMAND_REGISTRY


# 处理 / 命令
def handle_slash(line: str, state, config) -> Union[bool, tuple]:
    result = _get_command_registry().handle_slash(line, state, config)
    if result is False:
        return False
    if result is None:
        cmd = line[1:].split(None, 1)[0].lower() if line.startswith("/") and line[1:].strip() else ""
        err(f"未知命令: /{cmd}  (输入 /help 查看命令列表)")
        return True
    if isinstance(result, tuple):
        return result
    return True


def setup_readline(history_file: Path):
    _get_command_registry().setup_readline(history_file)
