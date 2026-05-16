"""Layer 2: Auto Memory Extraction — triggered at session end.

Runs asynchronously in a background thread after save_latest().
Uses the lightweight subagent_model (default: deepseek-v4-flash) to
analyse the session transcript and extract durable memories.

Trigger conditions (all must hold):
  - session duration ≥ 5 min
  - turn_count ≥ 10
  - >30 min elapsed since last extraction for this project

Code-fact exemption (strict): code patterns, architecture, file paths,
git history, and debugging fixes are NOT saved — only user intent,
preferences, and project-level decisions not derivable from the code.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

# Cooldown between extractions, in seconds (30 min)
_EXTRACT_COOLDOWN_S = 1800
# Minimum session wall-clock time before extraction makes sense (5 min)
_MIN_DURATION_S = 300
# Minimum turns
_MIN_TURNS = 10

_EXTRACTION_SYSTEM = """\
You are a memory extraction agent for a coding assistant called pycc.
Your job: read the session transcript below and extract ONLY facts that
should persist to future sessions. Apply the CODE-FACT EXEMPTION strictly.

=== MEMORY TYPES (extract only these) ===
user       — user's role, skills, working style, explicit preferences
feedback   — corrections or confirmations about HOW to approach work
             (not what code to write). Lead: rule. Then Why: / How to apply:
project    — ongoing goals, decisions, deadlines NOT derivable from git history.
             Lead: fact. Then Why: / How to apply: Always use absolute dates.
reference  — pointers to external systems (Linear, Grafana, Slack, dashboards)

=== CODE-FACT EXEMPTION — DO NOT EXTRACT ===
- File paths, function names, class names, variable names
- Code architecture, patterns, algorithms, data structures
- Bug descriptions, fix recipes, error messages
- Git history, branch names, PR numbers
- Anything visible by running grep/git on the codebase
- In-progress work or TODO lists from THIS session
- Generic best practices that apply to any project

=== OUTPUT FORMAT ===
Return a JSON array. Each element:
{
  "name": "short_slug_name",
  "description": "one-line description (used for future relevance decisions)",
  "type": "user|feedback|project|reference",
  "content": "memory body — for feedback/project: lead with rule/fact, then **Why:** and **How to apply:** lines",
  "scope": "user|project"
}

Return [] if nothing worth saving was found.
scope="user" for preferences/feedback that apply across all projects.
scope="project" for facts specific to the current repository.
Output ONLY the JSON array, no markdown fences."""

_EXTRACTION_TEMPLATE = """\
Project directory: {cwd}
Session duration: {duration_min:.0f} min  |  Turns: {turns}

=== SESSION TRANSCRIPT (assistant/user turns only) ===
{transcript}
"""


def _format_transcript(messages: list[dict], max_chars: int = 40_000) -> str:
    """Flatten messages into a readable transcript, budget-capped."""
    parts: list[str] = []
    total = 0
    for msg in messages:
        role = msg.get("role", "?")
        content = msg.get("content", "")
        if isinstance(content, list):
            text_parts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                    elif block.get("type") == "tool_use":
                        text_parts.append(f"[tool: {block.get('name', '?')}]")
                    elif block.get("type") == "tool_result":
                        inner = block.get("content", "")
                        if isinstance(inner, list):
                            inner = " ".join(
                                b.get("text", "") for b in inner
                                if isinstance(b, dict) and b.get("type") == "text"
                            )
                        text_parts.append(f"[result: {str(inner)[:200]}]")
            content = " ".join(text_parts)
        snippet = str(content)[:1000]
        line = f"[{role}]: {snippet}"
        remaining = max_chars - total
        if remaining < 50:
            break
        if len(line) > remaining:
            line = line[:remaining]
        parts.append(line)
        total += len(line)
    return "\n".join(parts)


def _get_last_extraction_time(cwd: str) -> float:
    """Read the timestamp of the last successful extraction for this project."""
    marker = Path.home() / ".pycc" / "memory" / ".last_extraction"
    try:
        data = json.loads(marker.read_text())
        return float(data.get(cwd, 0))
    except Exception:
        return 0.0


def _set_last_extraction_time(cwd: str) -> None:
    marker = Path.home() / ".pycc" / "memory" / ".last_extraction"
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        data: dict = {}
        if marker.exists():
            data = json.loads(marker.read_text())
        data[cwd] = time.time()
        marker.write_text(json.dumps(data, indent=2))
    except Exception:
        pass


def _save_extracted(memories: list[dict], cwd: str) -> int:
    """Persist extracted memory dicts to disk. Returns count saved."""
    from .store import MemoryEntry, save_memory
    import datetime as _dt

    today = _dt.date.today().isoformat()
    saved = 0
    for m in memories:
        name = str(m.get("name", "")).strip()
        desc = str(m.get("description", "")).strip()
        mtype = str(m.get("type", "user")).strip()
        content = str(m.get("content", "")).strip()
        scope = str(m.get("scope", "user")).strip()

        if not name or not content:
            continue
        if mtype not in ("user", "feedback", "project", "reference"):
            mtype = "user"
        if scope not in ("user", "project"):
            scope = "user"

        entry = MemoryEntry(
            name=name,
            description=desc,
            type=mtype,
            content=content,
            created=today,
            scope=scope,
        )
        try:
            save_memory(entry, scope=scope)
            saved += 1
        except Exception:
            pass
    return saved


def _do_extraction(messages: list[dict], config: dict,
                   cwd: str, duration_s: float, turns: int) -> int:
    """Core extraction: call flash model, parse JSON, save memories. Returns count."""
    transcript = _format_transcript(messages)
    user_msg = _EXTRACTION_TEMPLATE.format(
        cwd=cwd,
        duration_min=duration_s / 60,
        turns=turns,
        transcript=transcript,
    )

    flash = config.get("subagent_model", "deepseek/deepseek-v4-flash")
    result_text = ""
    try:
        from providers import stream, TextChunk
        for event in stream(
            model=flash,
            system=_EXTRACTION_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
            tool_schemas=[],
            config={**config, "max_tokens": 2048, "no_tools": True},
        ):
            if isinstance(event, TextChunk):
                result_text += event.text
    except Exception:
        return 0

    # Strip markdown fences if model wrapped output
    text = result_text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]

    try:
        memories = json.loads(text)
        if not isinstance(memories, list):
            return 0
    except Exception:
        return 0

    return _save_extracted(memories, cwd)


def maybe_extract_memories(
    messages: list[dict],
    config: dict,
    session_start_time: float,
    turn_count: int,
) -> None:
    """Check trigger conditions and run extraction in a background thread.

    Safe to call from the main thread at session end — returns immediately.
    The extraction runs asynchronously and does not block exit.
    """
    duration_s = time.time() - session_start_time
    if duration_s < _MIN_DURATION_S:
        return
    if turn_count < _MIN_TURNS:
        return

    cwd = str(config.get("_cwd", ""))
    last = _get_last_extraction_time(cwd)
    if time.time() - last < _EXTRACT_COOLDOWN_S:
        return

    # Take a snapshot of messages (the list may be mutated after this)
    msgs_snapshot = list(messages)

    def _worker() -> None:
        try:
            count = _do_extraction(msgs_snapshot, config, cwd, duration_s, turn_count)
            if count:
                _set_last_extraction_time(cwd)
        except Exception:
            pass

    t = threading.Thread(target=_worker, daemon=True, name="mem-extract")
    t.start()
