"""Context window management: five-layer compression for long conversations.

Layers (in order of invocation):
  Layer 1: Disk offload of large tool results    — tool_registry.py
  Layer 2: Remove old complete turns             — snip_old_messages()
  Layer 3: Micro-compact clearable tool results  — micro_compact()
  Layer 4: Read-time context collapse            — apply_context_collapse()  [called from agent.py]
  Layer 5: Full LLM summary                      — compact_messages()
"""
from __future__ import annotations

import time as _time
from pathlib import Path

import providers
from plan_mode import is_plan_mode


# ── Token estimation ──────────────────────────────────────────────────────

def estimate_tokens(messages: list) -> int:
    """Estimate token count by summing content lengths / 3.5."""
    total_chars = 0
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, str):
            total_chars += len(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    for v in block.values():
                        if isinstance(v, str):
                            total_chars += len(v)
        for tc in m.get("tool_calls", []):
            if isinstance(tc, dict):
                for v in tc.values():
                    if isinstance(v, str):
                        total_chars += len(v)
    return int(total_chars / 3.5)


def get_context_limit(model: str) -> int:
    """Look up context window size for a model."""
    provider_name = providers.detect_provider(model)
    prov = providers.PROVIDERS.get(provider_name, {})
    return prov.get("context_limit", 128_000)


# ── Layer 2: Remove old turns ──────────────────────────────────────────────

def snip_old_messages(
    messages: list,
    preserve_last_n_turns: int = 6,
) -> int:
    """Remove old complete conversation turns from the front of history.

    A "turn" is an assistant message plus all its following tool-result messages.
    Removed turns are replaced by a single boundary marker user message.

    Args:
        messages:             list of message dicts (mutated in place)
        preserve_last_n_turns: number of assistant+tool turns to keep

    Returns:
        Approximate tokens freed.
    """
    # Identify turn boundaries: (start_idx, end_idx_exclusive)
    turns: list[tuple[int, int]] = []
    i = 0
    while i < len(messages):
        if messages[i].get("role") == "assistant":
            start = i
            i += 1
            while i < len(messages) and messages[i].get("role") == "tool":
                i += 1
            turns.append((start, i))
        else:
            i += 1

    if len(turns) <= preserve_last_n_turns:
        return 0

    turns_to_remove = turns[:-preserve_last_n_turns]
    remove_start = turns_to_remove[0][0]
    remove_end   = turns_to_remove[-1][1]

    freed = estimate_tokens(messages[remove_start:remove_end])

    boundary = {
        "role": "user",
        "content": (
            f"[Earlier conversation history has been removed. "
            f"~{freed} tokens freed.]"
        ),
    }
    ack = {
        "role": "assistant",
        "content": "Understood. I'll continue from the current context.",
    }
    messages[remove_start:remove_end] = [boundary, ack]
    return freed


# ── Layer 3: Micro-compact ─────────────────────────────────────────────────

# Tools whose results can safely be cleared (re-fetchable from disk/web)
_CLEARABLE_TOOLS = {"Read", "Bash", "Glob", "Grep", "WebFetch", "WebSearch", "Edit", "Write"}
# Tools whose results must be preserved
_PRESERVE_TOOLS  = {"Agent", "TaskCreate", "TaskUpdate", "TaskGet", "TaskList"}

_MICRO_COMPACT_IDLE_MINUTES = 60   # trigger threshold


def micro_compact(messages: list, config: dict) -> int:
    """Clear old re-fetchable tool results when prompt cache is likely expired.

    Keeps the most recent 5 clearable tool results verbatim; replaces
    older ones with a placeholder. Only fires when the agent has been idle
    for more than _MICRO_COMPACT_IDLE_MINUTES.

    Returns:
        Number of tool result messages cleared.
    """
    last_call = config.get("_last_api_call_time")
    if last_call is None:
        return 0
    idle_min = (_time.time() - last_call) / 60
    if idle_min < _MICRO_COMPACT_IDLE_MINUTES:
        return 0

    # Collect indices of clearable tool results (oldest first)
    clearable: list[int] = []
    for i, m in enumerate(messages):
        if m.get("role") != "tool":
            continue
        tool_name = m.get("name", "")
        if tool_name in _CLEARABLE_TOOLS and tool_name not in _PRESERVE_TOOLS:
            clearable.append(i)

    # Keep last 5, clear the rest
    to_clear = clearable[:-5] if len(clearable) > 5 else []
    for i in to_clear:
        messages[i]["content"] = "[Old tool result content cleared]"
    return len(to_clear)


# ── Layer 4: Context collapse (read-time projection) ─────────────────────

def apply_context_collapse(messages: list, config: dict) -> list:
    """Return a compressed view of messages for this API call only.

    Does NOT modify `messages`. Returns a new list.

    Thresholds:
      90 %  → keep most-recent 40 % of tokens verbatim, summarise the rest
      95 %  → keep most-recent 25 % of tokens verbatim, summarise the rest

    Args:
        messages: current state.messages (not mutated)
        config:   agent config dict

    Returns:
        Possibly-compressed message list for the API call.
    """
    # Guard: never recurse from inside a summarisation call
    if config.get("_in_collapse"):
        return messages

    model = config.get("model", "")
    limit = get_context_limit(model)
    if limit == 0:
        return messages

    total = estimate_tokens(messages)
    ratio = total / limit

    if ratio < 0.90:
        return messages

    keep_ratio = 0.25 if ratio >= 0.95 else 0.40
    split = find_split_point(messages, keep_ratio=keep_ratio)
    if split <= 1:
        return messages

    old    = messages[:split]
    recent = messages[split:]

    collapse_config = {**config, "_in_collapse": True}
    summary = _collapse_summarize(old, collapse_config)

    if not summary:
        # Summarisation failed — light truncation fallback
        truncated_old: list[dict] = []
        for m in old:
            body = m.get("content", "")
            if isinstance(body, str) and len(body) > 300:
                body = body[:300] + "…"
            truncated_old.append({**m, "content": body})
        return [*truncated_old, *recent]

    return [
        {"role": "user",      "content": f"[Context collapse: earlier conversation summary]\n{summary}"},
        {"role": "assistant", "content": "Understood. Continuing from summarised context."},
        *recent,
    ]


def _collapse_summarize(old_messages: list, config: dict) -> str:
    """Call the LLM with a compact prompt to summarise old_messages."""
    old_text = _format_for_summary(old_messages, max_chars=40_000)
    prompt = (
        "Summarise the following conversation history in 3-5 concise paragraphs. "
        "Focus on: decisions made, files touched, key outcomes, and any unresolved issues. "
        "Do NOT include filler. Be dense.\n\n"
        + old_text
    )
    try:
        summary = ""
        for event in providers.stream(
            model=config["model"],
            system="You are a concise summariser.",
            messages=[{"role": "user", "content": prompt}],
            tool_schemas=[],
            config={**config, "max_tokens": 512, "no_tools": True},
        ):
            if isinstance(event, providers.TextChunk):
                summary += event.text
        return summary.strip()
    except Exception:
        return ""


# ── Layer 5: Full LLM summary ─────────────────────────────────────────────

# Structured 9-dimension summary prompt
_COMPACT_SYSTEM = "You are an expert at distilling technical conversation histories."

_COMPACT_PROMPT_TEMPLATE = """\
Summarise the following conversation. Produce a structured summary with ALL nine sections:

**1. User Intent** — What the user is trying to accomplish overall.
**2. Key Decisions** — Important choices made (file approaches, architecture, configs, etc.).
**3. Files Involved** — Files read/written with their key content or purpose.
**4. Tool Results** — Significant tool outputs, findings, and data retrieved.
**5. Errors & Fixes** — Problems encountered and how they were resolved.
**6. User Messages** — ALL user messages verbatim, in order. Do NOT omit any.
**7. Pending Tasks** — Work started but not yet completed.
**8. Current State** — Where the conversation stands right now.
**9. Next Steps** — Recommended immediate next actions.

---

{old_text}
"""


def compact_messages(messages: list, config: dict, focus: str = "") -> list:
    """Compress old messages into a structured LLM summary (Layer 5).

    Features:
    - Structured 9-dimension prompt (no content[:500] truncation)
    - Circuit breaker: stops after 3 consecutive failures
    - Post-compact restoration: re-injects recently accessed files + plan file

    Args:
        messages: full message list
        config:   agent config dict (must contain "model")
        focus:    optional extra focus instruction

    Returns:
        New compacted message list, or original on failure.
    """
    # Circuit breaker
    failures = config.get("_compact_failures", 0)
    if failures >= 3:
        # Give up on LLM compaction; just return as-is
        return messages

    split = find_split_point(messages)
    if split <= 0:
        return messages

    old    = messages[:split]
    recent = messages[split:]

    old_text = _format_for_summary(old)
    if focus:
        extra = f"\n\nFocus especially on: {focus}"
    else:
        extra = ""

    prompt = _COMPACT_PROMPT_TEMPLATE.format(old_text=old_text) + extra

    try:
        summary_text = ""
        for event in providers.stream(
            model=config["model"],
            system=_COMPACT_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            tool_schemas=[],
            config={**config, "max_tokens": 2048, "no_tools": True},
        ):
            if isinstance(event, providers.TextChunk):
                summary_text += event.text

        if not summary_text.strip():
            raise ValueError("Empty summary returned")

    except Exception:
        config["_compact_failures"] = failures + 1
        return messages  # fallback: keep original

    # Reset failure counter on success
    config["_compact_failures"] = 0

    summary_msg = {
        "role": "user",
        "content": f"[Previous conversation summary]\n{summary_text.strip()}",
    }
    ack_msg = {
        "role": "assistant",
        "content": "Understood. I have the context from the previous conversation. Let's continue.",
    }
    compacted = [summary_msg, ack_msg, *recent]

    # Post-compact restoration
    compacted.extend(_restore_recent_files(config))
    compacted.extend(_restore_active_skills(config))

    return compacted


# ── Post-compact restoration ───────────────────────────────────────────────

def _restore_recent_files(config: dict, max_files: int = 5, token_budget: int = 50_000) -> list:
    """Re-inject the most recently accessed files after compaction."""
    log: dict = config.get("_file_access_log", {})
    if not log:
        return []

    # Sort by most recently accessed
    sorted_paths = sorted(log.items(), key=lambda kv: kv[1], reverse=True)

    injections: list[dict] = []
    tokens_used = 0

    for file_path, _ in sorted_paths[:max_files]:
        p = Path(file_path)
        if not p.exists() or not p.is_file():
            continue
        try:
            content = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        snippet_tokens = int(len(content) / 3.5)
        if tokens_used + snippet_tokens > token_budget:
            # Truncate to fit budget
            allowed_chars = int((token_budget - tokens_used) * 3.5)
            if allowed_chars < 200:
                break
            content = content[:allowed_chars] + "\n[... truncated to fit token budget ...]"
            snippet_tokens = int(len(content) / 3.5)

        injections.append({
            "role": "user",
            "content": f"[File context restored after compaction: {file_path}]\n\n{content}",
        })
        injections.append({
            "role": "assistant",
            "content": f"I have the content of {p.name}.",
        })
        tokens_used += snippet_tokens
        if tokens_used >= token_budget:
            break

    return injections


def _restore_active_skills(config: dict, token_budget: int = 25_000) -> list:
    """Re-inject active skill content after compaction (best-effort)."""
    active_skill = config.get("_active_skill_content", "")
    if not active_skill:
        return []
    chars = int(token_budget * 3.5)
    if len(active_skill) > chars:
        active_skill = active_skill[:chars] + "\n[... truncated ...]"
    return [
        {"role": "user",      "content": f"[Active skill context restored]\n{active_skill}"},
        {"role": "assistant", "content": "Skill context noted."},
    ]


def _restore_plan_context(config: dict) -> list:
    """If in plan mode, return messages that restore plan file context."""
    plan_file = config.get("_plan_file", "")
    if not plan_file or not is_plan_mode(config):
        return []
    p = Path(plan_file)
    if not p.exists():
        return []
    content = p.read_text(encoding="utf-8").strip()
    if not content:
        return []
    return [
        {"role": "user",      "content": f"[Plan file restored after compaction: {plan_file}]\n\n{content}"},
        {"role": "assistant", "content": "I have the plan context. Let's continue."},
    ]


# ── Helpers ────────────────────────────────────────────────────────────────

def find_split_point(messages: list, keep_ratio: float = 0.3) -> int:
    """Find index where recent portion holds ~keep_ratio of total tokens."""
    total = estimate_tokens(messages)
    if total == 0:
        return 0
    target = int(total * keep_ratio)
    running = 0
    for i in range(len(messages) - 1, -1, -1):
        running += estimate_tokens([messages[i]])
        if running >= target:
            return i
    return 0


def _format_for_summary(messages: list, max_chars: int = 80_000) -> str:
    """Render messages as readable text for the summariser, up to max_chars."""
    lines: list[str] = []
    total = 0
    for m in messages:
        role    = m.get("role", "?")
        content = m.get("content", "")
        if isinstance(content, list):
            content = " ".join(
                b.get("text", "") for b in content if isinstance(b, dict)
            )
        entry = f"[{role}]: {content}"
        if total + len(entry) > max_chars:
            remaining = max_chars - total
            if remaining > 0:
                lines.append(entry[:remaining] + "…")
            break
        lines.append(entry)
        total += len(entry)
    return "\n".join(lines)


# ── Main entry ─────────────────────────────────────────────────────────────

def maybe_compact(state, config: dict) -> bool:
    """Check if context window is getting full and compress if needed.

    Layer order:
      2. snip_old_messages  — removes whole old turns, returns freed count
      3. micro_compact      — clears re-fetchable tool results on long idle
      (4. apply_context_collapse — called separately in agent.py before API)
      5. compact_messages   — full LLM summary if still over threshold

    Args:
        state:  AgentState with .messages list
        config: agent config dict (must contain "model")

    Returns:
        True if any compaction was performed.
    """
    model     = config.get("model", "")
    limit     = get_context_limit(model)
    threshold = limit * 0.7

    if estimate_tokens(state.messages) <= threshold:
        return False

    # Layer 2: remove old complete turns
    snip_old_messages(state.messages)

    # Layer 3: micro-compact clearable tools on long idle
    micro_compact(state.messages, config)

    if estimate_tokens(state.messages) <= threshold:
        return True

    # Pre-compact hook
    try:
        from hooks.dispatcher import fire_pre_compact as _fire_pre_compact
        _fire_pre_compact(
            len(state.messages),
            estimate_tokens(state.messages),
            config.get("_session_id", ""),
            config.get("_cwd", "."),
        )
    except Exception:
        pass

    # Layer 5: full LLM summary
    state.messages = compact_messages(state.messages, config)
    state.messages.extend(_restore_plan_context(config))
    return True


# ── Manual compact ────────────────────────────────────────────────────────

def manual_compact(state, config: dict, focus: str = "") -> tuple[bool, str]:
    """User-triggered compaction via /compact. Not gated by threshold.

    Returns (success, info_message).
    """
    if len(state.messages) < 4:
        return False, "Not enough messages to compact."

    before = estimate_tokens(state.messages)
    snip_old_messages(state.messages)
    state.messages = compact_messages(state.messages, config, focus=focus)
    state.messages.extend(_restore_plan_context(config))
    after = estimate_tokens(state.messages)
    saved = before - after
    return True, f"Compacted: ~{before} → ~{after} tokens (~{saved} saved)"
