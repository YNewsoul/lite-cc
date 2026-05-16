"""Tool plugin registry for pycc.

Provides a central registry for tool definitions, lookup, schema export,
and dispatch with large-result disk offloading.
"""
from __future__ import annotations

import time as _time
import uuid as _uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


@dataclass
class ToolDef:
    """Definition of a single tool plugin.

    Attributes:
        name: unique tool identifier
        schema: JSON-schema dict sent to the API (name, description, input_schema)
        func: callable(params: dict, config: dict) -> str
        read_only: True if the tool never mutates state
        concurrent_safe: True if safe to run in parallel with other tools
    """
    name: str
    schema: Dict[str, Any]
    func: Callable[[Dict[str, Any], Dict[str, Any]], str]
    read_only: bool = False
    concurrent_safe: bool = False


# ── Constants ─────────────────────────────────────────────────────────────

# Results larger than this are offloaded to disk instead of truncated
DISK_OFFLOAD_THRESHOLD = 50_000   # ~50 KB in characters

# How many chars of a large result to keep in-context as a preview
PREVIEW_SIZE = 2_048

# Tools whose file_path input is logged in _file_access_log
_FILE_LOG_TOOLS = {"Read", "Write", "Edit"}


# ── Internal state ─────────────────────────────────────────────────────────

_registry: Dict[str, ToolDef] = {}


# ── Public API ─────────────────────────────────────────────────────────────

def register_tool(tool_def: ToolDef) -> None:
    """Register a tool, overwriting any existing tool with the same name."""
    _registry[tool_def.name] = tool_def


def get_tool(name: str) -> Optional[ToolDef]:
    """Look up a tool by name. Returns None if not found."""
    return _registry.get(name)


def get_all_tools() -> List[ToolDef]:
    """Return all registered tools (insertion order)."""
    return list(_registry.values())


def get_tool_schemas() -> List[Dict[str, Any]]:
    """Return the schemas of all registered tools (for API tool parameter)."""
    return [t.schema for t in _registry.values()]


def execute_tool(
    name: str,
    params: Dict[str, Any],
    config: Dict[str, Any],
    max_output: int = 32000,
    tool_use_id: Optional[str] = None,
) -> str:
    """Dispatch a tool call by name.

    Large results (> DISK_OFFLOAD_THRESHOLD chars) are written to disk and
    replaced with a short in-context preview. This avoids hard truncation
    while keeping the context window manageable.

    Args:
        name:        tool name
        params:      tool input parameters dict
        config:      runtime configuration dict
        max_output:  fallback hard-cap (chars) used only when disk offload fails
        tool_use_id: optional unique ID for naming the offloaded file

    Returns:
        Tool result string (possibly replaced with a disk-offload preview).
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

    # ── Update file access log ──────────────────────────────────────────
    _update_file_access_log(name, params, config)

    # ── Large-result disk offload ───────────────────────────────────────
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
            # Disk offload failed — fall back to soft truncation
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
    """Remove all registered tools. Intended for testing."""
    _registry.clear()


# ── Disk offload helpers ───────────────────────────────────────────────────

def _offload_result_to_disk(
    result: str,
    config: Dict[str, Any],
    tool_use_id: Optional[str] = None,
) -> Optional[str]:
    """Write result to disk and return the path, or None on error."""
    try:
        session_id = config.get("_session_id", "default")
        tid = tool_use_id or _uuid.uuid4().hex[:12]
        out_dir = Path.home() / ".pycc" / "tool_results" / session_id
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
    """Record file access time in config['_file_access_log'] for file tools."""
    if name not in _FILE_LOG_TOOLS:
        return
    file_path = params.get("file_path") or params.get("notebook_path", "")
    if not file_path:
        return
    log: Dict[str, float] = config.setdefault("_file_access_log", {})
    log[str(file_path)] = _time.time()
