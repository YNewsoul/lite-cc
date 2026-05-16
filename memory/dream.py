"""Layer 3: AutoDream — periodic memory consolidation.

Runs in a background thread when:
  - 5+ sessions have ended since the last dream
  - 24+ hours have passed since the last dream

Also serves as the backend for /memory consolidate (manual trigger).

Operations performed:
  - Deduplicate near-identical memories (same name or near-identical description)
  - Apply time-decay: flag memories older than 90 days as candidates for removal
  - Reinforce frequently-referenced memories (update mtime)
  - Produce a brief consolidation report written as a reference memory
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Optional

_DREAM_COOLDOWN_S    = 86400   # 24 h
_DREAM_SESSION_EVERY = 5       # trigger after every 5 sessions
_STALE_DAYS          = 90      # memories older than this are flagged

_CONSOLIDATE_SYSTEM = """\
You are a memory consolidation agent for a coding assistant called pycc.
You are given a list of existing memory entries. Your job:

1. DEDUPLICATE: Identify groups of entries that contain the same information.
   For each group, keep the most complete/recent one and mark the rest for deletion.
2. MERGE: If two entries are about the same topic but each has unique detail,
   produce a merged entry that combines both (mark originals for deletion).
3. PRUNE STALE: Entries flagged as stale (age > 90 days) that contain
   time-sensitive claims (deadlines, in-progress work) should be deleted.
   General preferences / stable facts should be KEPT even if old.
4. DO NOT touch entries that are clearly distinct and non-redundant.
5. DO NOT invent new information — only reorganise what exists.

=== CODE-FACT EXEMPTION ===
Never save code patterns, file paths, function names, architecture details,
git history, or anything derivable from the codebase.

=== OUTPUT FORMAT ===
Return a JSON object:
{
  "keep":   [<list of entry names to keep unchanged>],
  "delete": [<list of entry names to delete>],
  "merge":  [
    {
      "replaces": [<names of entries being merged>],
      "name": "new_slug",
      "description": "one-line description",
      "type": "user|feedback|project|reference",
      "content": "merged body text",
      "scope": "user|project"
    }
  ]
}

Output ONLY the JSON object, no markdown fences."""

_CONSOLIDATE_TEMPLATE = """\
Memory entries to consolidate ({count} total):

{manifest}
"""


def _load_all_entries_with_content() -> list[dict]:
    """Load all memory entries from both scopes with full content."""
    from .store import load_index, get_memory_dir, INDEX_FILENAME
    import datetime as _dt

    now = time.time()
    entries = []
    for entry in load_index("all"):
        fp = Path(entry.file_path)
        try:
            mtime = fp.stat().st_mtime
        except Exception:
            mtime = 0
        age_days = (now - mtime) / 86400
        entries.append({
            "name": entry.name,
            "description": entry.description,
            "type": entry.type,
            "scope": entry.scope,
            "content": entry.content,
            "file_path": entry.file_path,
            "age_days": round(age_days, 1),
            "stale": age_days > _STALE_DAYS,
        })
    return entries


def _build_manifest(entries: list[dict]) -> str:
    lines = []
    for e in entries:
        stale_tag = " [STALE]" if e["stale"] else ""
        lines.append(
            f"name: {e['name']}\n"
            f"  type: {e['type']}  scope: {e['scope']}  age: {e['age_days']}d{stale_tag}\n"
            f"  description: {e['description']}\n"
            f"  content: {e['content'][:300]}"
        )
    return "\n\n".join(lines)


def _apply_consolidation(plan: dict, entries: list[dict]) -> tuple[int, int, int]:
    """Apply keep/delete/merge plan. Returns (deleted, merged, errors)."""
    from .store import (
        MemoryEntry, save_memory, delete_memory, INDEX_FILENAME,
        get_memory_dir,
    )
    import datetime as _dt

    name_to_entry = {e["name"]: e for e in entries}
    today = _dt.date.today().isoformat()

    deleted = 0
    merged  = 0
    errors  = 0

    # Delete
    for name in plan.get("delete", []):
        entry = name_to_entry.get(name)
        if not entry:
            continue
        try:
            delete_memory(name, scope=entry["scope"])
            deleted += 1
        except Exception:
            errors += 1

    # Merge: delete originals, write new combined entry
    for m in plan.get("merge", []):
        replaces = m.get("replaces", [])
        for name in replaces:
            entry = name_to_entry.get(name)
            if entry:
                try:
                    delete_memory(name, scope=entry["scope"])
                    deleted += 1
                except Exception:
                    errors += 1

        new_entry = MemoryEntry(
            name=str(m.get("name", "merged")),
            description=str(m.get("description", "")),
            type=str(m.get("type", "user")),
            content=str(m.get("content", "")),
            created=today,
            scope=str(m.get("scope", "user")),
        )
        try:
            save_memory(new_entry, scope=new_entry.scope)
            merged += 1
        except Exception:
            errors += 1

    return deleted, merged, errors


def _get_dream_meta() -> dict:
    marker = Path.home() / ".pycc" / "memory" / ".dream_meta"
    try:
        return json.loads(marker.read_text())
    except Exception:
        return {"last_time": 0, "sessions_since": 0}


def _set_dream_meta(meta: dict) -> None:
    marker = Path.home() / ".pycc" / "memory" / ".dream_meta"
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps(meta, indent=2))
    except Exception:
        pass


def increment_session_count() -> None:
    """Call this after each session end to count sessions for AutoDream."""
    meta = _get_dream_meta()
    meta["sessions_since"] = meta.get("sessions_since", 0) + 1
    _set_dream_meta(meta)


def _do_consolidation(config: dict, verbose: bool = False) -> str:
    """Run the consolidation LLM call and apply results. Returns a status string."""
    entries = _load_all_entries_with_content()
    if not entries:
        return "无可整合的记忆条目。"

    manifest = _build_manifest(entries)
    user_msg = _CONSOLIDATE_TEMPLATE.format(count=len(entries), manifest=manifest)

    flash = config.get("subagent_model", "deepseek/deepseek-v4-flash")
    result_text = ""
    try:
        from providers import stream, TextChunk
        for event in stream(
            model=flash,
            system=_CONSOLIDATE_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
            tool_schemas=[],
            config={**config, "max_tokens": 4096, "no_tools": True},
        ):
            if isinstance(event, TextChunk):
                result_text += event.text
    except Exception as e:
        return f"整合失败: {e}"

    text = result_text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]

    try:
        plan = json.loads(text)
    except Exception:
        return "整合失败：模型返回格式无效。"

    deleted, merged, errors = _apply_consolidation(plan, entries)
    return (
        f"整合完成：{len(entries)} 条记忆 → "
        f"删除 {deleted}，合并 {merged}，"
        f"保留 {len(entries) - deleted} 条。"
        + (f" ({errors} 错误)" if errors else "")
    )


def consolidate(config: dict, verbose: bool = False) -> str:
    """Synchronous consolidation — used by /memory consolidate."""
    result = _do_consolidation(config, verbose)
    # Reset session counter after manual consolidation
    meta = _get_dream_meta()
    meta["sessions_since"] = 0
    meta["last_time"] = time.time()
    _set_dream_meta(meta)
    return result


def maybe_run_dream(config: dict) -> None:
    """Check trigger conditions and run AutoDream in a background thread.

    Safe to call from session-end path — returns immediately.
    """
    meta = _get_dream_meta()
    sessions_since = meta.get("sessions_since", 0)
    last_time      = meta.get("last_time", 0)

    cooldown_ok = (time.time() - last_time) >= _DREAM_COOLDOWN_S
    sessions_ok = sessions_since >= _DREAM_SESSION_EVERY

    if not (cooldown_ok and sessions_ok):
        return

    def _worker() -> None:
        try:
            _do_consolidation(config)
            meta2 = _get_dream_meta()
            meta2["last_time"] = time.time()
            meta2["sessions_since"] = 0
            _set_dream_meta(meta2)
        except Exception:
            pass

    t = threading.Thread(target=_worker, daemon=True, name="mem-dream")
    t.start()
