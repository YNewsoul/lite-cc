"""litecc 的工具插件注册中心。

提供统一的工具定义注册、查找、schema 导出，
以及带大结果落盘能力的分发执行。
"""
from __future__ import annotations

import time as _time
import uuid as _uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


@dataclass
class ToolDef:
    """单个工具插件的定义。

    字段说明：
        name: 工具的唯一标识符
        schema: 发给模型 API 的 JSON Schema（name、description、input_schema）
        func: 可调用对象，签名为 callable(params: dict, config: dict) -> str
        read_only: 若为 True，表示该工具不会修改状态
        concurrent_safe: 若为 True，表示可与其他工具并行安全执行
    """
    name: str
    schema: Dict[str, Any]
    func: Callable[[Dict[str, Any], Dict[str, Any]], str]
    read_only: bool = False
    concurrent_safe: bool = False


# ── 常量 ───────────────────────────────────────────────────────────────────

# 超过这个阈值的结果会落盘保存，而不是直接在上下文里截断。
DISK_OFFLOAD_THRESHOLD = 50_000   # 按字符数估算，约 50 KB

# 对于超大结果，在上下文中最多保留这么多字符作为预览。
PREVIEW_SIZE = 2_048

# 这些工具的 file_path 输入会记录到 _file_access_log 中。
_FILE_LOG_TOOLS = {"Read", "Write", "Edit"}


# ── 内部状态 ───────────────────────────────────────────────────────────────

_registry: Dict[str, ToolDef] = {}


# ── 对外 API ───────────────────────────────────────────────────────────────

def register_tool(tool_def: ToolDef) -> None:
    """注册一个工具；若同名工具已存在则直接覆盖。"""
    _registry[tool_def.name] = tool_def


def get_tool(name: str) -> Optional[ToolDef]:
    """按名称查找工具；若不存在则返回 None。"""
    return _registry.get(name)


def get_all_tools() -> List[ToolDef]:
    """返回当前所有已注册工具，保持注册顺序。"""
    return list(_registry.values())


def get_tool_schemas() -> List[Dict[str, Any]]:
    """返回所有已注册工具的 schema，供模型 API 的 tools 参数使用。"""
    return [t.schema for t in _registry.values()]


def execute_tool(
    name: str,
    params: Dict[str, Any],
    config: Dict[str, Any],
    max_output: int = 32000,
    tool_use_id: Optional[str] = None,
) -> str:
    """按名称分发并执行一次工具调用。

    对于超大结果（长度大于 DISK_OFFLOAD_THRESHOLD），会优先写入磁盘，
    然后只在上下文里保留一小段预览文本。这样可以避免硬截断，
    同时把上下文窗口占用控制在可接受范围内。

    参数：
        name:        工具名称
        params:      工具输入参数字典
        config:      运行时配置字典
        max_output:  仅在落盘失败时使用的兜底最大输出长度（字符数）
        tool_use_id: 可选的唯一 ID，用于命名落盘文件

    返回：
        工具执行结果字符串；若发生大结果落盘，则返回替换后的预览文本。
    """
    tool = get_tool(name)
    if tool is None:
        return f"Error: tool '{name}' not found."

    try:
        result = tool.func(params, config)
    except Exception as e:
        return f"Error executing {name}: {e}"

    if not isinstance(result, str):
        result = str(result)

    # ── 更新文件访问日志 ───────────────────────────────────────────────────
    _update_file_access_log(name, params, config)

    # ── 超大结果落盘 ───────────────────────────────────────────────────────
    if len(result) > DISK_OFFLOAD_THRESHOLD:
        offload_path = _offload_result_to_disk(result, config, tool_use_id)
        if offload_path:
            preview = result[:PREVIEW_SIZE]
            result = (
                f"{preview}\n"
                f"[... {len(result) - PREVIEW_SIZE:,} more chars. "
                f"Full result saved to: {offload_path} — "
                f"use Read tool to access it if needed ...]"
            )
        else:
            # 落盘失败时，退回到软截断策略。
            if len(result) > max_output:
                first_half = max_output // 2
                last_quarter = max_output // 4
                snipped = len(result) - first_half - last_quarter
                result = (
                    result[:first_half]
                    + f"\n[... {snipped:,} chars truncated ...]\n"
                    + result[-last_quarter:]
                )

    return result


def clear_registry() -> None:
    """清空所有已注册工具，主要用于测试场景。"""
    _registry.clear()


# ── 落盘辅助函数 ───────────────────────────────────────────────────────────

def _offload_result_to_disk(
    result: str,
    config: Dict[str, Any],
    tool_use_id: Optional[str] = None,
) -> Optional[str]:
    """把结果写入磁盘并返回文件路径；失败时返回 None。"""
    try:
        session_id = config.get("_session_id", "default")
        tid = tool_use_id or _uuid.uuid4().hex[:12]
        out_dir = Path.home() / ".litecc" / "tool_results" / session_id
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / f"{tid}.txt"
        out_file.write_text(result, encoding="utf-8", errors="replace")
        return str(out_file)
    except Exception:
        return None


def _update_file_access_log(
    name: str,
    params: Dict[str, Any],
    config: Dict[str, Any],
) -> None:
    """为文件类工具记录访问时间到 config['_file_access_log'] 中。"""
    if name not in _FILE_LOG_TOOLS:
        return
    file_path = params.get("file_path") or params.get("notebook_path", "")
    if not file_path:
        return
    log: Dict[str, float] = config.setdefault("_file_access_log", {})
    log[str(file_path)] = _time.time()
