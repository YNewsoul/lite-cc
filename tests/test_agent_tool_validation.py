from __future__ import annotations

import pytest

import agent as agent_mod
from tool_registry import ToolDef, clear_registry, get_tool_schemas, register_tool


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_registry()
    yield
    clear_registry()


def test_agent_retries_after_tool_validation_failure(monkeypatch):
    called = {"count": 0}

    register_tool(ToolDef(
        name="echo",
        schema={
            "name": "echo",
            "description": "Echo text.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                },
                "required": ["text"],
            },
        },
        func=lambda params, config: called.__setitem__("count", called["count"] + 1) or params["text"],
        read_only=True,
        concurrent_safe=True,
    ))

    responses = [
        agent_mod.Response("", [{"id": "call_1", "name": "echo", "input": {}}], 1, 1),
        agent_mod.Response("", [{"id": "call_2", "name": "echo", "input": {"text": "fixed"}}], 1, 1),
        agent_mod.Response("done", [], 1, 1),
    ]

    def fake_stream(**kwargs):
        yield responses.pop(0)

    monkeypatch.setattr(agent_mod, "stream", fake_stream)
    monkeypatch.setattr(agent_mod, "select_tool_schemas", lambda config, state: get_tool_schemas())

    state = agent_mod.AgentState()
    events = list(agent_mod.run(
        "hello",
        state,
        {"model": "dummy", "permission_mode": "accept-all"},
        "system",
    ))

    tool_end_results = [event.result for event in events if isinstance(event, agent_mod.ToolEnd)]
    assert any("Tool validation failed" in result for result in tool_end_results)
    assert "fixed" in tool_end_results
    assert called["count"] == 1
    assert state.messages[-1]["role"] == "assistant"
    assert state.messages[-1]["content"] == "done"
    assert any(
        msg["role"] == "tool" and "Tool validation failed" in msg["content"]
        for msg in state.messages
    )
