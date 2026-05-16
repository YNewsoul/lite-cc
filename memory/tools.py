"""Memory tool registrations: MemorySave, MemoryDelete, MemorySearch, MemoryList.

Importing this module registers the four tools into the central registry.
"""
from __future__ import annotations

from datetime import datetime

from tool_registry import ToolDef, register_tool
from .store import MemoryEntry, save_memory, delete_memory, load_index, load_entries
from .context import find_relevant_memories
from .scan import scan_all_memories, format_memory_manifest


# ── Tool implementations ───────────────────────────────────────────────────

def _memory_save(params: dict, config: dict) -> str:
    """Save or update a persistent memory entry."""
    scope = params.get("scope", "user")
    entry = MemoryEntry(
        name=params["name"],
        description=params["description"],
        type=params["type"],
        content=params["content"],
        created=datetime.now().strftime("%Y-%m-%d"),
    )

    save_memory(entry, scope=scope)

    scope_label = "project" if scope == "project" else "user"
    return f"Memory saved: '{entry.name}' [{entry.type}/{scope_label}]"


def _memory_delete(params: dict, config: dict) -> str:
    """Delete a persistent memory entry by name."""
    name = params["name"]
    scope = params.get("scope", "user")
    delete_memory(name, scope=scope)
    return f"Memory deleted: '{name}' (scope: {scope})"


def _memory_search(params: dict, config: dict) -> str:
    """Search memories by keyword query with optional AI relevance filtering."""
    query = params["query"]
    use_ai = params.get("use_ai", False)
    max_results = params.get("max_results", 5)

    results = find_relevant_memories(
        query, max_results=max_results, use_ai=use_ai, config=config
    )

    if not results:
        return f"No memories found matching '{query}'."

    lines = [f"Found {len(results)} relevant memory/memories for '{query}':", ""]
    for r in results:
        freshness = f"  ⚠ {r['freshness_text']}" if r["freshness_text"] else ""
        lines.append(
            f"[{r['type']}/{r['scope']}] {r['name']}\n"
            f"  {r['description']}\n"
            f"  {r['content'][:200]}{'...' if len(r['content']) > 200 else ''}"
            f"{freshness}"
        )
    return "\n\n".join(lines)


def _memory_list(params: dict, config: dict) -> str:
    """List all memory entries with type, scope, and description."""
    scope_filter = params.get("scope", "all")
    scopes = ["user", "project"] if scope_filter == "all" else [scope_filter]

    all_entries = []
    for s in scopes:
        all_entries.extend(load_entries(s))

    if not all_entries:
        return "No memories stored." if scope_filter == "all" else f"No {scope_filter} memories stored."

    lines = [f"{len(all_entries)} memory/memories:"]
    for e in all_entries:
        tag = f"[{e.type:9s}|{e.scope:7s}]"
        lines.append(f"  {tag} {e.name}")
        if e.description:
            lines.append(f"    {e.description}")
    return "\n".join(lines)


# ── Tool registrations ─────────────────────────────────────────────────────

register_tool(ToolDef(
    name="MemorySave",
    schema={
        "name": "MemorySave",
        "description": (
            "Save a persistent memory entry as a markdown file.\n\n"
            "## When to save\n"
            "Save information that must survive across conversations and cannot be "
            "re-derived cheaply: user preferences, explicit corrections, project "
            "decisions, or pointers to external systems.\n\n"
            "## NEVER save\n"
            "- Code structure, file locations, or project architecture "
            "(use Glob/Grep to find these in real-time)\n"
            "- Git history or recent changes (use `git log`/`git blame`)\n"
            "- Content already in CLAUDE.md\n"
            "- Temporary task state or current-conversation context\n\n"
            "## Types & required format\n"
            "- **user**: user's role, preferences, expertise level\n"
            "- **feedback**: how you should behave — MUST include "
            "'**Why:**' and '**How to apply:**' sections in content\n"
            "- **project**: ongoing decisions/context — convert ALL relative dates "
            "(e.g. 'next Thursday') to absolute ISO dates (e.g. '2026-04-17')\n"
            "- **reference**: pointer to an external system or resource"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Human-readable name (becomes the filename slug)",
                },
                "type": {
                    "type": "string",
                    "enum": ["user", "feedback", "project", "reference"],
                    "description": (
                        "user=preferences/role  |  feedback=how to behave (requires Why + How to apply)  |  "
                        "project=ongoing decisions (use absolute dates)  |  reference=external pointers"
                    ),
                },
                "description": {
                    "type": "string",
                    "description": "One-line summary used for retrieval decisions — be specific",
                },
                "content": {
                    "type": "string",
                    "description": (
                        "Body text. "
                        "feedback type: state the rule/correction, then '**Why:**' and '**How to apply:**'. "
                        "project type: use absolute dates, not 'yesterday'/'next week'."
                    ),
                },
                "scope": {
                    "type": "string",
                    "enum": ["user", "project"],
                    "description": (
                        "'user' (default) = ~/.pycc/memory/ shared across projects; "
                        "'project' = .pycc/memory/ local to this project"
                    ),
                },
            },
            "required": ["name", "type", "description", "content"],
        },
    },
    func=_memory_save,
    read_only=False,
    concurrent_safe=False,
))

register_tool(ToolDef(
    name="MemoryDelete",
    schema={
        "name": "MemoryDelete",
        "description": "Delete a persistent memory entry by name.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name of the memory to delete"},
                "scope": {
                    "type": "string",
                    "enum": ["user", "project"],
                    "description": "Scope to delete from (default: 'user')",
                },
            },
            "required": ["name"],
        },
    },
    func=_memory_delete,
    read_only=False,
    concurrent_safe=False,
))

register_tool(ToolDef(
    name="MemorySearch",
    schema={
        "name": "MemorySearch",
        "description": (
            "Search persistent memories by keyword. Returns matching entries with "
            "content preview and staleness warning for old memories. "
            "Set use_ai=true to use AI-powered relevance ranking (costs a small API call)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "max_results": {
                    "type": "integer",
                    "description": "Maximum results to return (default: 5)",
                },
                "use_ai": {
                    "type": "boolean",
                    "description": "Use AI relevance ranking (default: false = keyword only)",
                },
                "scope": {
                    "type": "string",
                    "enum": ["user", "project", "all"],
                    "description": "Which scope to search (default: 'all')",
                },
            },
            "required": ["query"],
        },
    },
    func=_memory_search,
    read_only=True,
    concurrent_safe=True,
))

register_tool(ToolDef(
    name="MemoryList",
    schema={
        "name": "MemoryList",
        "description": (
            "List all memory entries with type, scope, age, and description. "
            "Useful for reviewing what's been remembered before deciding to save or delete."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "enum": ["user", "project", "all"],
                    "description": "Which scope to list (default: 'all')",
                },
            },
        },
    },
    func=_memory_list,
    read_only=True,
    concurrent_safe=True,
))
