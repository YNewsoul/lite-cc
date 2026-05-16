"""Tests for compaction.py — token estimation, context limits, snipping, split point."""
from __future__ import annotations

import sys
import os

# Ensure project root is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from compaction import estimate_tokens, get_context_limit, snip_old_messages, find_split_point


# ── estimate_tokens ───────────────────────────────────────────────────────

class TestEstimateTokens:
    def test_simple_messages(self):
        msgs = [
            {"role": "user", "content": "Hello world"},          # 11 chars
            {"role": "assistant", "content": "Hi there!"},       # 9 chars
        ]
        result = estimate_tokens(msgs)
        # (11 + 9) / 3.5 = 5.71 -> 5
        assert result == int(20 / 3.5)

    def test_empty_messages(self):
        assert estimate_tokens([]) == 0

    def test_empty_content(self):
        msgs = [{"role": "user", "content": ""}]
        assert estimate_tokens(msgs) == 0

    def test_tool_result_messages(self):
        msgs = [
            {"role": "tool", "tool_call_id": "abc", "name": "Read", "content": "x" * 350},
        ]
        result = estimate_tokens(msgs)
        assert result == int(350 / 3.5)

    def test_structured_content(self):
        """Content that is a list of dicts (e.g. Anthropic tool_result blocks)."""
        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "id1", "content": "A" * 70},
                ],
            },
        ]
        result = estimate_tokens(msgs)
        # "tool_result" (11) + "id1" (3) + "A"*70 (70) = 84  -> 84/3.5 = 24
        assert result == int(84 / 3.5)

    def test_with_tool_calls(self):
        msgs = [
            {
                "role": "assistant",
                "content": "ok",
                "tool_calls": [
                    {"id": "c1", "name": "Bash", "input": {"command": "ls"}},
                ],
            },
        ]
        result = estimate_tokens(msgs)
        # content "ok" (2) + tool_calls string values: "c1" (2) + "Bash" (4) = 8
        assert result == int(8 / 3.5)


# ── get_context_limit ─────────────────────────────────────────────────────

class TestGetContextLimit:
    def test_anthropic(self):
        assert get_context_limit("claude-opus-4-6") == 200000

    def test_gemini(self):
        assert get_context_limit("gemini-2.0-flash") == 1000000

    def test_deepseek(self):
        assert get_context_limit("deepseek-chat") >= 64000
        assert get_context_limit("deepseek-v4-pro") >= 1000000

    def test_openai(self):
        assert get_context_limit("gpt-4o") == 128000

    def test_qwen(self):
        assert get_context_limit("qwen-max") == 1000000

    def test_unknown_model_fallback(self):
        # Unknown models fall back to openai provider which has 128000
        assert get_context_limit("some-random-model-xyz") == 128000


# ── snip_old_messages ─────────────────────────────────────────────────────

def _make_turns(n_turns: int, extra_messages: list | None = None) -> list:
    """Build a message list with n_turns assistant+tool turns preceded by user messages."""
    msgs: list = []
    for i in range(n_turns):
        msgs.append({"role": "user", "content": f"user {i}"})
        msgs.append({"role": "assistant", "content": f"assistant {i}", "tool_calls": [{"id": f"t{i}", "name": "Read", "input": {}}]})
        msgs.append({"role": "tool", "tool_call_id": f"t{i}", "name": "Read", "content": f"result {i}"})
    if extra_messages:
        msgs.extend(extra_messages)
    return msgs


class TestSnipOldMessages:
    def test_excess_turns_removed(self):
        """When there are more turns than preserve_last_n_turns, old turns are deleted."""
        msgs = _make_turns(8)
        original_len = len(msgs)
        freed = snip_old_messages(msgs, preserve_last_n_turns=4)
        # 4 turns removed; replaced by 2 boundary messages (boundary + ack)
        # original: 8 turns * 3 msgs each = 24; keeping 4 = 12; boundary adds 2
        assert len(msgs) < original_len
        assert freed > 0
        # Boundary marker should appear at the removed position
        assert any("Earlier conversation history has been removed" in m.get("content", "") for m in msgs)

    def test_within_limit_nothing_removed(self):
        """When turns <= preserve_last_n_turns, nothing is removed."""
        msgs = _make_turns(3)
        original_len = len(msgs)
        freed = snip_old_messages(msgs, preserve_last_n_turns=6)
        assert freed == 0
        assert len(msgs) == original_len

    def test_exact_limit_nothing_removed(self):
        """When turns == preserve_last_n_turns, nothing is removed."""
        msgs = _make_turns(6)
        original_len = len(msgs)
        freed = snip_old_messages(msgs, preserve_last_n_turns=6)
        assert freed == 0
        assert len(msgs) == original_len

    def test_mutates_in_place(self):
        """snip_old_messages mutates the original list."""
        msgs = _make_turns(8)
        original_ref = msgs
        snip_old_messages(msgs, preserve_last_n_turns=4)
        assert msgs is original_ref

    def test_recent_turns_content_preserved(self):
        """The last preserve_last_n_turns turns should still exist after snipping."""
        msgs = _make_turns(8)
        snip_old_messages(msgs, preserve_last_n_turns=4)
        # The last 4 turn results should still be in the history
        contents = [m.get("content", "") for m in msgs]
        for i in range(4, 8):
            assert f"result {i}" in contents


# ── find_split_point ──────────────────────────────────────────────────────

class TestFindSplitPoint:
    def test_returns_reasonable_index(self):
        msgs = [
            {"role": "user", "content": "A" * 1000},
            {"role": "assistant", "content": "B" * 1000},
            {"role": "user", "content": "C" * 1000},
            {"role": "assistant", "content": "D" * 1000},
            {"role": "user", "content": "E" * 1000},
        ]
        idx = find_split_point(msgs, keep_ratio=0.3)
        # With equal-size messages and keep_ratio=0.3, split should be around index 3-4
        assert 2 <= idx <= 4

    def test_single_message(self):
        msgs = [{"role": "user", "content": "hello"}]
        idx = find_split_point(msgs, keep_ratio=0.3)
        assert idx == 0

    def test_empty_messages(self):
        idx = find_split_point([], keep_ratio=0.3)
        assert idx == 0

    def test_split_preserves_recent(self):
        # Recent portion should contain ~30% of tokens
        msgs = [{"role": "user", "content": "X" * 100} for _ in range(10)]
        idx = find_split_point(msgs, keep_ratio=0.3)
        total = estimate_tokens(msgs)
        recent = estimate_tokens(msgs[idx:])
        # Recent should be roughly 30% of total (allow some tolerance)
        assert recent >= total * 0.2
        assert recent <= total * 0.5
