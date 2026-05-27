"""litecc 的工具插件注册中心。

提供统一的工具定义注册、查找、schema 导出，
以及带大结果落盘能力的分发执行。
"""
from __future__ import annotations

import json
import os
import time as _time
import uuid as _uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from plan_mode import is_plan_mode


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


@dataclass
class ToolValidationResult:
    valid: bool
    code: str = ""
    errors: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)


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


def select_tool_schemas(config: Dict[str, Any], state: Any = None) -> List[Dict[str, Any]]:
    """Return tool schemas filtered for the current runtime context.

    Selection rules:
      - Plan mode exposes read-only tools plus Write/Edit/ExitPlanMode for plan flow.
      - Sub-agents can restrict tools via config["_allowed_tools"].
      - WebFetch/WebSearch are hidden when networking is disabled.
      - NotebookEdit is hidden when the workspace has no notebooks.
      - MCP tools are hidden until their backing server is connected/ready.
    """
    del state  # reserved for future context-aware filtering

    allowed_tools = _allowed_tool_names(config)
    network_enabled = _network_enabled(config)
    has_notebook = _workspace_has_notebook(config)
    ready_mcp_tools = _ready_mcp_tool_names()

    schemas: List[Dict[str, Any]] = []
    for tool in get_all_tools():
        name = tool.name

        if is_plan_mode(config) and not (
            tool.read_only or name in {"Write", "Edit", "ExitPlanMode"}
        ):
            continue

        if allowed_tools is not None and name not in allowed_tools:
            continue

        if not network_enabled and name in {"WebFetch", "WebSearch"}:
            continue

        if not has_notebook and name == "NotebookEdit":
            continue

        if name.startswith("mcp__") and name not in ready_mcp_tools:
            continue

        schemas.append(tool.schema)

    return schemas


def validate_tool_call(
    name: str,
    params: Any,
    available_tool_names: Optional[set[str]] = None,
) -> ToolValidationResult:
    tool = get_tool(name)
    if tool is None:
        return ToolValidationResult(
            valid=False,
            code="unknown_tool",
            errors=[f"Unknown tool '{name}'."],
        )

    if available_tool_names is not None and name not in available_tool_names:
        return ToolValidationResult(
            valid=False,
            code="unavailable_tool",
            errors=[f"Tool '{name}' is not available in the current context."],
            details={"available_tools": sorted(available_tool_names)},
        )

    schema = tool.schema.get("input_schema", {})
    errors: List[str] = []
    _validate_against_schema(params, schema, "$", errors, strict_object_keys=True)
    if errors:
        return ToolValidationResult(
            valid=False,
            code="schema_validation_failed",
            errors=errors,
            details={"schema": schema},
        )

    return ToolValidationResult(valid=True)


def format_tool_validation_error(
    name: str,
    params: Any,
    validation: ToolValidationResult,
    attempt: int = 1,
) -> str:
    lines = [
        "[Tool validation failed]",
        f"Tool: {name}",
    ]

    if validation.code == "unknown_tool":
        lines.append("This tool does not exist, so it was not executed.")
    elif validation.code == "unavailable_tool":
        lines.append("This tool is not available in the current context, so it was not executed.")
    else:
        lines.append(
            "The tool was not executed. Call the same tool again with corrected JSON arguments."
        )

    if validation.errors:
        lines.append("")
        lines.append("Issues:")
        lines.extend(f"- {error}" for error in validation.errors)

    available_tools = validation.details.get("available_tools", [])
    if available_tools:
        preview = ", ".join(available_tools[:12])
        suffix = " ..." if len(available_tools) > 12 else ""
        lines.append("")
        lines.append(f"Available tools in this context: {preview}{suffix}")

    schema = validation.details.get("schema")
    if isinstance(schema, dict):
        lines.append("")
        lines.append("Expected input schema:")
        lines.append(_summarize_schema(schema))

    lines.append("")
    lines.append("Received arguments:")
    lines.append(_safe_json(params))

    if attempt > 1:
        lines.append("")
        lines.append(
            f"Repair attempt: {attempt}. Use the exact schema above, or choose a different available tool."
        )

    return "\n".join(lines)


def note_tool_validation_result(name: str, valid: bool, config: Dict[str, Any]) -> int:
    failures = config.setdefault("_tool_validation_failures", {})
    if not isinstance(failures, dict):
        failures = {}
        config["_tool_validation_failures"] = failures

    if valid:
        failures.pop(name, None)
        return 0

    count = int(failures.get(name, 0)) + 1
    failures[name] = count
    return count


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

    validation = validate_tool_call(name, params)
    if not validation.valid:
        return format_tool_validation_error(name, params, validation)

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


def _validate_against_schema(
    value: Any,
    schema: Dict[str, Any],
    path: str,
    errors: List[str],
    strict_object_keys: bool = False,
) -> None:
    if not isinstance(schema, dict):
        return

    expected_type = schema.get("type")
    if expected_type and not _matches_schema_type(value, expected_type):
        errors.append(
            f"{path}: expected {_format_expected_type(expected_type)}, got {_describe_value_type(value)}"
        )
        return

    enum_values = schema.get("enum")
    if isinstance(enum_values, list) and value not in enum_values:
        rendered = ", ".join(repr(item) for item in enum_values)
        errors.append(f"{path}: expected one of [{rendered}], got {value!r}")
        return

    schema_type = _primary_schema_type(expected_type)
    if schema_type == "object":
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        additional = schema.get("additionalProperties", None)

        if isinstance(required, list):
            for field_name in required:
                if field_name not in value:
                    errors.append(f"{path}.{field_name}: missing required property")

        if not isinstance(properties, dict):
            properties = {}

        for key, child_value in value.items():
            child_path = f"{path}.{key}"
            if key in properties:
                _validate_against_schema(
                    child_value,
                    properties[key],
                    child_path,
                    errors,
                    strict_object_keys=False,
                )
                continue

            if additional is False or (strict_object_keys and properties):
                errors.append(f"{child_path}: unexpected property")
                continue

            if isinstance(additional, dict):
                _validate_against_schema(
                    child_value,
                    additional,
                    child_path,
                    errors,
                    strict_object_keys=False,
                )
        return

    if schema_type == "array":
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for idx, item in enumerate(value):
                _validate_against_schema(
                    item,
                    item_schema,
                    f"{path}[{idx}]",
                    errors,
                    strict_object_keys=False,
                )


def _matches_schema_type(value: Any, expected_type: Any) -> bool:
    if isinstance(expected_type, list):
        return any(_matches_schema_type(value, item) for item in expected_type)
    if expected_type == "object":
        return isinstance(value, dict)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "null":
        return value is None
    return True


def _primary_schema_type(expected_type: Any) -> str:
    if isinstance(expected_type, list):
        return str(expected_type[0]) if expected_type else ""
    return str(expected_type or "")


def _format_expected_type(expected_type: Any) -> str:
    if isinstance(expected_type, list):
        return " or ".join(str(item) for item in expected_type)
    return str(expected_type)


def _describe_value_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return type(value).__name__


def _summarize_schema(schema: Dict[str, Any]) -> str:
    lines: List[str] = []
    if schema.get("type"):
        lines.append(f"- type: {schema['type']}")

    required = schema.get("required", [])
    if isinstance(required, list) and required:
        lines.append("- required: " + ", ".join(str(item) for item in required))

    properties = schema.get("properties", {})
    if isinstance(properties, dict) and properties:
        lines.append("- properties:")
        for key, prop in properties.items():
            if not isinstance(prop, dict):
                lines.append(f"  - {key}")
                continue
            desc = prop.get("type", "any")
            if isinstance(prop.get("enum"), list):
                enum_preview = ", ".join(repr(item) for item in prop["enum"])
                desc = f"{desc}; enum=[{enum_preview}]"
            lines.append(f"  - {key}: {desc}")

    return "\n".join(lines) if lines else "- any JSON value"


def _safe_json(value: Any, max_chars: int = 2000) -> str:
    try:
        rendered = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    except TypeError:
        rendered = repr(value)
    if len(rendered) <= max_chars:
        return rendered
    return rendered[:max_chars] + f"\n... [{len(rendered) - max_chars} more chars]"


def _allowed_tool_names(config: Dict[str, Any]) -> Optional[set[str]]:
    raw = config.get("_allowed_tools")
    if not raw:
        return None
    if isinstance(raw, str):
        return {raw}
    if isinstance(raw, (list, tuple, set)):
        return {str(item) for item in raw if str(item).strip()}
    return None


def _network_enabled(config: Dict[str, Any]) -> bool:
    if "network_enabled" in config:
        return bool(config.get("network_enabled"))
    if "allow_network" in config:
        return bool(config.get("allow_network"))
    if "disable_network" in config:
        return not bool(config.get("disable_network"))
    return True


def _workspace_has_notebook(config: Dict[str, Any]) -> bool:
    file_access_log = config.get("_file_access_log", {})
    if isinstance(file_access_log, dict):
        for raw_path in file_access_log:
            try:
                path = Path(str(raw_path))
            except Exception:
                continue
            if path.suffix == ".ipynb" and path.exists():
                return True

    cwd = str(Path.cwd())
    cache = config.get("_tool_schema_notebook_cache")
    now = _time.time()
    if isinstance(cache, dict):
        if cache.get("cwd") == cwd and now - float(cache.get("checked_at", 0)) < 10:
            return bool(cache.get("has_notebook"))

    has_notebook = _scan_for_notebook(Path.cwd())
    config["_tool_schema_notebook_cache"] = {
        "cwd": cwd,
        "checked_at": now,
        "has_notebook": has_notebook,
    }
    return has_notebook


def _scan_for_notebook(root: Path) -> bool:
    skip_dirs = {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "__pycache__",
        "node_modules",
        ".mypy_cache",
        ".pytest_cache",
    }
    for current_root, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        for filename in filenames:
            if filename.endswith(".ipynb"):
                return True
    return False


def _ready_mcp_tool_names() -> set[str]:
    try:
        from mcp.client import get_mcp_manager
    except Exception:
        return set()

    try:
        manager = get_mcp_manager()
        return {tool.qualified_name for tool in manager.all_tools()}
    except Exception:
        return set()
