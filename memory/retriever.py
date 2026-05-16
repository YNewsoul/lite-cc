"""Memory retriever: fast header scanning + Sonnet-powered relevance selection.

Usage in run_query():
  1. Start _retrieval_thread() concurrently with the main model API call.
  2. After the turn, store result in config["_retrieved_memories"].
  3. build_system_prompt() injects it into the next system prompt.
"""
from __future__ import annotations

import json
import time as _time
from pathlib import Path
from typing import Optional

from .store import parse_frontmatter, USER_MEMORY_DIR, get_memory_dir


# ── Freshness warning ──────────────────────────────────────────────────────

def memory_freshness_warning(mtime: float) -> str:
    """Return a staleness notice for memories older than 1 day.

    Args:
        mtime: file modification time as a Unix timestamp

    Returns:
        Warning string, or "" if the memory is fresh.
    """
    days = (_time.time() - mtime) / 86400
    if days <= 1:
        return ""
    return (
        f"⚠ This memory is {int(days)} day(s) old. "
        "Assertions about code behaviour or file locations may be stale — "
        "verify against current code before relying on it."
    )


# ── Header scanning ────────────────────────────────────────────────────────

def scan_memory_headers(
    memory_dir: Path,
    max_entries: int = 200,
) -> list[dict]:
    """Scan memory .md files, reading only the first 30 lines for frontmatter.

    Returns a list of header dicts sorted by modification time (newest first):
        {name, description, type, file_path, mtime_s}

    Args:
        memory_dir:  directory to scan (user or project memory dir)
        max_entries: cap on returned entries
    """
    if not memory_dir.exists():
        return []

    entries: list[dict] = []
    for fp in memory_dir.glob("*.md"):
        if fp.name == "MEMORY.md":
            continue
        try:
            # Read only first 30 lines — fast, avoids loading large files
            lines: list[str] = []
            with fp.open(encoding="utf-8", errors="replace") as fh:
                for i, line in enumerate(fh):
                    if i >= 30:
                        break
                    lines.append(line)
            meta, _ = parse_frontmatter("".join(lines))
            mtime = fp.stat().st_mtime
            entries.append({
                "name":        meta.get("name", fp.stem),
                "description": meta.get("description", ""),
                "type":        meta.get("type", "user"),
                "file_path":   str(fp),
                "mtime_s":     mtime,
            })
        except Exception:
            continue

    entries.sort(key=lambda e: e["mtime_s"], reverse=True)
    return entries[:max_entries]


def scan_all_memory_headers() -> list[dict]:
    """Scan both user and project memory dirs and return merged header list."""
    user_headers = scan_memory_headers(USER_MEMORY_DIR)
    proj_headers = scan_memory_headers(get_memory_dir("project"))
    # Deduplicate by file_path (project entries take priority)
    seen: dict[str, dict] = {}
    for h in user_headers + proj_headers:
        seen[h["file_path"]] = h
    merged = list(seen.values())
    merged.sort(key=lambda e: e["mtime_s"], reverse=True)
    return merged


# ── AI relevance selection ─────────────────────────────────────────────────

def select_relevant_memories(
    query: str,
    headers: list[dict],
    tool_in_use: Optional[str] = None,
    config: Optional[dict] = None,
    max_results: int = 5,
) -> list[str]:
    """Use a fast LLM call to pick the most relevant memory file paths.

    Falls back to returning the newest `max_results` entries on any error.

    Args:
        query:       the user's current input (or tool name/description)
        headers:     list of header dicts from scan_memory_headers()
        tool_in_use: name of tool currently in use (reserved for future filtering)
        config:      agent config dict (must contain "model")
        max_results: maximum number of memories to select

    Returns:
        List of file_path strings for the selected memories.
    """
    if not headers:
        return []
    if not config:
        # No config → just return the most recent entries
        return [h["file_path"] for h in headers[:max_results]]

    # Build a numbered manifest for the model
    manifest_lines = [
        f"{i}: [{h['type']}] {h['name']} — {h['description']}"
        for i, h in enumerate(headers)
    ]
    manifest = "\n".join(manifest_lines)

    system = (
        "You select memories relevant to a coding assistant's current task. "
        "Return ONLY a JSON object with key 'indices' containing a list of "
        f"integers (0-based). Select at most {max_results} entries. "
        "Return {\"indices\": []} if none are clearly relevant."
    )
    user_msg = f"Current task / query:\n{query}\n\nAvailable memories:\n{manifest}"

    try:
        from providers import stream, TextChunk

        result_text = ""
        flash = config.get("subagent_model", config.get("model", "deepseek/deepseek-v4-flash"))
        for event in stream(
            model=flash,
            system=system,
            messages=[{"role": "user", "content": user_msg}],
            tool_schemas=[],
            config={**config, "max_tokens": 256, "no_tools": True},
        ):
            if isinstance(event, TextChunk):
                result_text += event.text

        parsed = json.loads(result_text.strip())
        indices = [
            int(i) for i in parsed.get("indices", [])
            if isinstance(i, (int, float)) and 0 <= int(i) < len(headers)
        ]
        return [headers[i]["file_path"] for i in indices[:max_results]]

    except Exception:
        # Fallback: return newest entries
        return [h["file_path"] for h in headers[:max_results]]


# ── Full content loader ────────────────────────────────────────────────────

def load_selected_memories(
    file_paths: list[str],
    max_total_chars: int = 50_000,
) -> str:
    """Load full content of selected memory files with staleness warnings.

    Args:
        file_paths:      list of absolute file paths to load
        max_total_chars: budget for total returned characters

    Returns:
        Formatted string suitable for injection as a <system-reminder>, or "".
    """
    if not file_paths:
        return ""

    parts: list[str] = []
    total = 0

    for fp_str in file_paths:
        fp = Path(fp_str)
        if not fp.exists():
            continue
        try:
            content = fp.read_text(encoding="utf-8", errors="replace").strip()
            mtime   = fp.stat().st_mtime
        except Exception:
            continue

        warning = memory_freshness_warning(mtime)
        entry = content + (f"\n{warning}" if warning else "")

        remaining = max_total_chars - total
        if remaining < 200:
            break
        if len(entry) > remaining:
            entry = entry[:remaining] + "\n[... truncated to fit context budget ...]"

        parts.append(entry)
        total += len(entry)

    if not parts:
        return ""

    return "\n\n---\n\n".join(parts)


# ── Convenience: run retrieval and return formatted content ────────────────

def retrieve_for_query(query: str, config: Optional[dict] = None) -> str:
    """One-shot: scan → select → load. Returns formatted memory content or "".

    This is the function called from the background retrieval thread.
    """
    try:
        headers = scan_all_memory_headers()
        if not headers:
            return ""
        paths = select_relevant_memories(query, headers, config=config)
        return load_selected_memories(paths)
    except Exception:
        return ""
