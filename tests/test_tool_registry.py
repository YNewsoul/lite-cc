from __future__ import annotations

import pytest
from unittest.mock import patch

from tool_registry import (
    ToolDef,
    clear_registry,
    execute_tool,
    get_all_tools,
    get_tool,
    get_tool_schemas,
    register_tool,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    """Reset registry before each test."""
    clear_registry()
    yield
    clear_registry()


def _make_echo_tool(name: str = "echo", read_only: bool = False) -> ToolDef:
    """Helper to build a simple echo tool."""
    schema = {
        "name": name,
        "description": f"Echo tool ({name})",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "text to echo"},
            },
            "required": ["text"],
        },
    }

    def func(params: dict, config: dict) -> str:
        return params["text"]

    return ToolDef(
        name=name,
        schema=schema,
        func=func,
        read_only=read_only,
        concurrent_safe=True,
    )


# ------------------------------------------------------------------
# register and get
# ------------------------------------------------------------------

def test_register_and_get():
    tool = _make_echo_tool()
    register_tool(tool)
    result = get_tool("echo")
    assert result is not None
    assert result.name == "echo"


def test_get_unknown_returns_none():
    assert get_tool("no_such_tool") is None


# ------------------------------------------------------------------
# get_all_tools
# ------------------------------------------------------------------

def test_get_all_tools_empty():
    assert get_all_tools() == []


def test_get_all_tools():
    register_tool(_make_echo_tool("a"))
    register_tool(_make_echo_tool("b"))
    names = [t.name for t in get_all_tools()]
    assert sorted(names) == ["a", "b"]


# ------------------------------------------------------------------
# get_tool_schemas
# ------------------------------------------------------------------

def test_get_tool_schemas():
    register_tool(_make_echo_tool("echo"))
    schemas = get_tool_schemas()
    assert len(schemas) == 1
    assert schemas[0]["name"] == "echo"


# ------------------------------------------------------------------
# execute_tool
# ------------------------------------------------------------------

def test_execute_tool():
    register_tool(_make_echo_tool())
    result = execute_tool("echo", {"text": "hello"}, config={})
    assert result == "hello"


def test_execute_unknown_tool():
    result = execute_tool("missing", {}, config={})
    assert "unknown" in result.lower() or "not found" in result.lower()


# ------------------------------------------------------------------
# output truncation
# ------------------------------------------------------------------

def test_small_result_not_truncated():
    """Results under DISK_OFFLOAD_THRESHOLD (50_000) are returned unchanged."""
    import tool_registry

    def small_func(params: dict, config: dict) -> str:
        return "x" * 100

    tool = ToolDef(
        name="big",
        schema={"name": "big", "description": "big", "input_schema": {"type": "object", "properties": {}}},
        func=small_func,
        read_only=True,
        concurrent_safe=True,
    )
    register_tool(tool)

    # 100-char result is well under the 50,000-char disk-offload threshold
    result = execute_tool("big", {}, config={}, max_output=40)
    assert result == "x" * 100
    assert "truncated" not in result


def test_fallback_truncation_when_disk_offload_fails():
    """When disk offload fails, large results are hard-truncated using max_output."""
    import tool_registry

    big_str = "x" * (tool_registry.DISK_OFFLOAD_THRESHOLD + 1)

    def huge_func(params: dict, config: dict) -> str:
        return big_str

    tool = ToolDef(
        name="huge",
        schema={"name": "huge", "description": "huge", "input_schema": {"type": "object", "properties": {}}},
        func=huge_func,
        read_only=True,
        concurrent_safe=True,
    )
    register_tool(tool)

    max_out = 200
    with patch.object(tool_registry, "_offload_result_to_disk", return_value=None):
        result = execute_tool("huge", {}, config={}, max_output=max_out)

    assert len(result) < len(big_str)
    assert "truncated" in result
    first_half = max_out // 2
    last_quarter = max_out // 4
    assert result.startswith("x" * first_half)
    assert result.endswith("x" * last_quarter)


def test_no_truncation_when_within_limit():
    register_tool(_make_echo_tool())
    result = execute_tool("echo", {"text": "short"}, config={})
    assert result == "short"


# ------------------------------------------------------------------
# duplicate register overwrites
# ------------------------------------------------------------------

def test_duplicate_register_overwrites():
    register_tool(_make_echo_tool("dup"))

    def new_func(params: dict, config: dict) -> str:
        return "new"

    replacement = ToolDef(
        name="dup",
        schema={"name": "dup", "description": "new", "input_schema": {"type": "object", "properties": {}}},
        func=new_func,
        read_only=False,
        concurrent_safe=False,
    )
    register_tool(replacement)

    assert len(get_all_tools()) == 1
    result = execute_tool("dup", {}, config={})
    assert result == "new"
