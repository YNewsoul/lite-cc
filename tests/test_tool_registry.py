from __future__ import annotations

import pytest
from unittest.mock import patch

from tool_registry import (
    ToolDef,
    clear_registry,
    execute_tool,
    format_tool_validation_error,
    get_all_tools,
    get_tool,
    get_tool_schemas,
    note_tool_validation_result,
    register_tool,
    select_tool_schemas,
    validate_tool_call,
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
# register/get 相关测试
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
# get_all_tools 相关测试
# ------------------------------------------------------------------

def test_get_all_tools_empty():
    assert get_all_tools() == []


def test_get_all_tools():
    register_tool(_make_echo_tool("a"))
    register_tool(_make_echo_tool("b"))
    names = [t.name for t in get_all_tools()]
    assert sorted(names) == ["a", "b"]


# ------------------------------------------------------------------
# get_tool_schemas 相关测试
# ------------------------------------------------------------------

def test_get_tool_schemas():
    register_tool(_make_echo_tool("echo"))
    schemas = get_tool_schemas()
    assert len(schemas) == 1
    assert schemas[0]["name"] == "echo"


def test_validate_tool_call_missing_required_property():
    register_tool(_make_echo_tool("echo"))
    validation = validate_tool_call("echo", {}, available_tool_names={"echo"})
    assert validation.valid is False
    assert validation.code == "schema_validation_failed"
    assert any("missing required property" in error for error in validation.errors)


def test_validate_tool_call_rejects_unexpected_top_level_property():
    register_tool(_make_echo_tool("echo"))
    validation = validate_tool_call(
        "echo",
        {"text": "hello", "extra": 1},
        available_tool_names={"echo"},
    )
    assert validation.valid is False
    assert any("unexpected property" in error for error in validation.errors)


def test_validate_tool_call_rejects_unavailable_tool():
    register_tool(_make_echo_tool("echo"))
    validation = validate_tool_call("echo", {"text": "hello"}, available_tool_names={"other"})
    assert validation.valid is False
    assert validation.code == "unavailable_tool"
    rendered = format_tool_validation_error("echo", {"text": "hello"}, validation)
    assert "not available in the current context" in rendered
    assert "other" in rendered


def test_note_tool_validation_result_counts_and_resets():
    config = {}
    assert note_tool_validation_result("echo", False, config) == 1
    assert note_tool_validation_result("echo", False, config) == 2
    assert note_tool_validation_result("echo", True, config) == 0
    assert config["_tool_validation_failures"] == {}


def test_select_tool_schemas_plan_mode_keeps_plan_flow_tools(monkeypatch):
    import tool_registry

    monkeypatch.setattr(tool_registry, "_workspace_has_notebook", lambda config: True)
    monkeypatch.setattr(tool_registry, "_ready_mcp_tool_names", lambda: set())

    register_tool(_make_echo_tool("read_only", read_only=True))
    register_tool(_make_echo_tool("Write", read_only=False))
    register_tool(_make_echo_tool("Edit", read_only=False))
    register_tool(_make_echo_tool("write_like", read_only=False))
    register_tool(ToolDef(
        name="ExitPlanMode",
        schema={"name": "ExitPlanMode", "description": "exit", "input_schema": {"type": "object", "properties": {}}},
        func=lambda params, config: "ok",
        read_only=False,
        concurrent_safe=False,
    ))

    schemas = select_tool_schemas({"_plan_mode_active": True})
    names = [schema["name"] for schema in schemas]
    assert names == ["read_only", "Write", "Edit", "ExitPlanMode"]


def test_select_tool_schemas_respects_allowed_tools(monkeypatch):
    import tool_registry

    monkeypatch.setattr(tool_registry, "_workspace_has_notebook", lambda config: True)
    monkeypatch.setattr(tool_registry, "_ready_mcp_tool_names", lambda: set())

    register_tool(_make_echo_tool("a"))
    register_tool(_make_echo_tool("b"))

    schemas = select_tool_schemas({"_allowed_tools": ["b"]})
    assert [schema["name"] for schema in schemas] == ["b"]


def test_select_tool_schemas_hides_web_tools_when_network_disabled(monkeypatch):
    import tool_registry

    monkeypatch.setattr(tool_registry, "_workspace_has_notebook", lambda config: True)
    monkeypatch.setattr(tool_registry, "_ready_mcp_tool_names", lambda: set())

    register_tool(_make_echo_tool("Read", read_only=True))
    register_tool(_make_echo_tool("WebFetch", read_only=True))
    register_tool(_make_echo_tool("WebSearch", read_only=True))

    schemas = select_tool_schemas({"network_enabled": False})
    assert [schema["name"] for schema in schemas] == ["Read"]


def test_select_tool_schemas_hides_notebook_edit_without_notebook(monkeypatch):
    import tool_registry

    monkeypatch.setattr(tool_registry, "_workspace_has_notebook", lambda config: False)
    monkeypatch.setattr(tool_registry, "_ready_mcp_tool_names", lambda: set())

    register_tool(_make_echo_tool("Read", read_only=True))
    register_tool(_make_echo_tool("NotebookEdit", read_only=False))

    schemas = select_tool_schemas({})
    assert [schema["name"] for schema in schemas] == ["Read"]


def test_select_tool_schemas_keeps_notebook_edit_after_notebook_seen(tmp_path, monkeypatch):
    import tool_registry

    notebook_path = tmp_path / "demo.ipynb"
    notebook_path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(tool_registry, "_ready_mcp_tool_names", lambda: set())
    monkeypatch.setattr(tool_registry, "_scan_for_notebook", lambda root: False)

    register_tool(_make_echo_tool("Read", read_only=True))
    register_tool(_make_echo_tool("NotebookEdit", read_only=False))

    schemas = select_tool_schemas({"_file_access_log": {str(notebook_path): 1.0}})
    assert [schema["name"] for schema in schemas] == ["Read", "NotebookEdit"]


def test_select_tool_schemas_hides_unready_mcp_tools(monkeypatch):
    import tool_registry

    monkeypatch.setattr(tool_registry, "_workspace_has_notebook", lambda config: True)
    monkeypatch.setattr(tool_registry, "_ready_mcp_tool_names", lambda: {"mcp__ready__tool"})

    register_tool(_make_echo_tool("Read", read_only=True))
    register_tool(_make_echo_tool("mcp__ready__tool", read_only=True))
    register_tool(_make_echo_tool("mcp__waiting__tool", read_only=True))

    schemas = select_tool_schemas({})
    assert [schema["name"] for schema in schemas] == ["Read", "mcp__ready__tool"]


# ------------------------------------------------------------------
# execute_tool 相关测试
# ------------------------------------------------------------------

def test_execute_tool():
    register_tool(_make_echo_tool())
    result = execute_tool("echo", {"text": "hello"}, config={})
    assert result == "hello"


def test_execute_tool_invalid_input_returns_validation_error():
    called = {"count": 0}

    def func(params: dict, config: dict) -> str:
        called["count"] += 1
        return params["text"]

    register_tool(ToolDef(
        name="echo",
        schema={
            "name": "echo",
            "description": "Echo tool",
            "input_schema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                },
                "required": ["text"],
            },
        },
        func=func,
        read_only=True,
        concurrent_safe=True,
    ))

    result = execute_tool("echo", {}, config={})
    assert "Tool validation failed" in result
    assert "missing required property" in result
    assert called["count"] == 0


def test_execute_unknown_tool():
    result = execute_tool("missing", {}, config={})
    assert "unknown" in result.lower() or "not found" in result.lower()


# ------------------------------------------------------------------
# 输出截断相关测试
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
# 重复注册应覆盖旧定义
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
