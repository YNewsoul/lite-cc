"""用于注入系统提示词的记忆上下文构建模块。

提供功能：
  get_memory_context()      - 生成用于系统提示词的完整上下文字符串
  find_relevant_memories()  - 关键词（+ 可选 AI）相关性过滤
  truncate_index_content()  - 按行 + 字节截断内容并添加警告
"""
from __future__ import annotations

from pathlib import Path

from .store import (
    USER_MEMORY_DIR,
    INDEX_FILENAME,
    MAX_INDEX_LINES,
    MAX_INDEX_BYTES,
    get_memory_dir,
    get_index_content,
    load_entries,
    search_memory,
)
from .scan import scan_all_memories, format_memory_manifest, memory_freshness_text
from .types import MEMORY_SYSTEM_PROMPT


# ── 索引内容截断 ───────────────────────────────────────────────────────

def truncate_index_content(raw: str) -> str:
    """将 MEMORY.md 内容按行数和字节数限制截断，并附加警告。

    与 Claude Code 的截断逻辑保持一致：
      - 先按行截断（自然边界）
      - 再按字节数在限制前最后一个换行处截断
      - 记录触发的限制类型
    """
    trimmed = raw.strip()
    content_lines = trimmed.split("\n")
    line_count = len(content_lines)
    byte_count = len(trimmed.encode())

    was_line_truncated = line_count > MAX_INDEX_LINES
    was_byte_truncated = byte_count > MAX_INDEX_BYTES

    if not was_line_truncated and not was_byte_truncated:
        return trimmed

    # 先按行数截断
    truncated = "\n".join(content_lines[:MAX_INDEX_LINES]) if was_line_truncated else trimmed

    # 再按字节数截断
    if len(truncated.encode()) > MAX_INDEX_BYTES:
        raw_bytes = truncated.encode()
        # 在字节限制前找到最后一个换行符
        cut = raw_bytes[:MAX_INDEX_BYTES].rfind(b"\n")
        truncated = raw_bytes[: cut if cut > 0 else MAX_INDEX_BYTES].decode(errors="replace")

    # 生成警告原因
    if was_byte_truncated and not was_line_truncated:
        reason = f"{byte_count:,} 字节 (限制: {MAX_INDEX_BYTES:,}) - 索引条目过长"
    elif was_line_truncated and not was_byte_truncated:
        reason = f"{line_count} 行 (限制: {MAX_INDEX_LINES})"
    else:
        reason = f"{line_count} 行 和 {byte_count:,} 字节"

    warning = (
        f"\n\n> 警告: {INDEX_FILENAME} 超出 {reason}。"
        "仅加载部分内容。请将索引条目控制在 1 行、约 150 字符以内。"
    )
    return truncated + warning


# ── 系统提示词上下文 ──────────────────────────────────────────────────

def get_memory_context(include_guidance: bool = False) -> str:
    """返回用于注入系统提示词的记忆上下文。

    合并用户级和项目级 MEMORY.md 内容（存在时）。
    无记忆时返回空字符串。

    参数:
        include_guidance: 若为 True，在开头添加完整记忆使用指南
                          (MEMORY_SYSTEM_PROMPT)。通常为 False，
                          因为系统提示词已包含简要指南。
    """
    parts: list[str] = []

    # 用户级记忆索引
    user_content = get_index_content("user")
    if user_content:
        truncated = truncate_index_content(user_content)
        parts.append(truncated)

    # 项目级记忆索引（单独标记）
    proj_content = get_index_content("project")
    if proj_content:
        truncated = truncate_index_content(proj_content)
        parts.append(f"[项目记忆]\n{truncated}")

    if not parts:
        return ""

    body = "\n\n".join(parts)
    if include_guidance:
        return f"{MEMORY_SYSTEM_PROMPT}\n\n## MEMORY.md\n{body}"
    return body


# ── 相关记忆查找 ─────────────────────────────────────────────────

def find_relevant_memories(
    query: str,
    max_results: int = 5,
    use_ai: bool = False,
    config: dict | None = None,
) -> list[dict]:
    """查找与查询相关的记忆。

    策略:
      1. 基础：对名称 + 描述 + 内容进行关键词匹配
      2. 若 use_ai=True 且配置了模型：使用轻量 AI 调用进行排序

    返回:
        字典列表，包含键：name, description, type, scope, content,
        file_path, mtime_s, freshness_text
    """
    # 步骤 1：关键词过滤
    keyword_results = search_memory(query)
    if not keyword_results:
        return []

    if not use_ai or not config:
        # 按时间最新排序，返回前 max_results 条
        from .scan import scan_all_memories
        headers = scan_all_memories()
        path_to_mtime = {h.file_path: h.mtime_s for h in headers}

        results = []
        for entry in keyword_results[:max_results * 3]:
            mtime_s = path_to_mtime.get(entry.file_path, 0)
            results.append({
                "name": entry.name,
                "description": entry.description,
                "type": entry.type,
                "scope": entry.scope,
                "content": entry.content,
                "file_path": entry.file_path,
                "mtime_s": mtime_s,
                "freshness_text": memory_freshness_text(mtime_s),
            })
        # 按修改时间倒序
        results.sort(key=lambda r: r["mtime_s"], reverse=True)
        return results[:max_results]

    # 步骤 2：AI 增强相关性筛选（可选、轻量级）
    return _ai_select_memories(query, keyword_results, max_results, config)


def _ai_select_memories(
    query: str,
    candidates: list,
    max_results: int,
    config: dict,
) -> list[dict]:
    """使用快速 AI 调用从候选列表中选择最相关的记忆。

    出现任何错误时，回退到关键词结果。
    """
    try:
        from providers import stream, Response
        from .scan import scan_all_memories

        headers = scan_all_memories()
        path_to_mtime = {h.file_path: h.mtime_s for h in headers}

        # 仅构建候选记忆清单
        manifest_lines = []
        for i, e in enumerate(candidates):
            manifest_lines.append(f"{i}: [{e.type}] {e.name} — {e.description}")
        manifest = "\n".join(manifest_lines)

        system = (
            "你需要选择与查询相关的记忆。"
            "返回一个 JSON 对象，key 为 'indices'，值为整数索引列表"
            f"(从 0 开始)。最多选择 {max_results} 条。"
            "只包含与查询明显相关的索引。无相关时返回 {\"indices\": []}。"
        )
        messages = [{"role": "user", "content": f"查询: {query}\n\n记忆列表:\n{manifest}"}]

        result_text = ""
        for event in stream(
            model=config.get("model", "claude-haiku-4-5-20251001"),
            system=system,
            messages=messages,
            tool_schemas=[],
            config={**config, "max_tokens": 256, "no_tools": True},
        ):
            if isinstance(event, Response):
                result_text = event.text
                break

        import json as _json
        parsed = _json.loads(result_text)
        selected_indices = [int(i) for i in parsed.get("indices", []) if isinstance(i, int)]

    except Exception:
        # 出错则回退到关键词结果
        selected_indices = list(range(min(max_results, len(candidates))))

    results = []
    for i in selected_indices[:max_results]:
        if i < 0 or i >= len(candidates):
            continue
        entry = candidates[i]
        mtime_s = path_to_mtime.get(entry.file_path, 0) if "path_to_mtime" in dir() else 0
        results.append({
            "name": entry.name,
            "description": entry.description,
            "type": entry.type,
            "scope": entry.scope,
            "content": entry.content,
            "file_path": entry.file_path,
            "mtime_s": mtime_s,
            "freshness_text": memory_freshness_text(mtime_s),
        })
    return results