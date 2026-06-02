"""上下文窗口管理：针对长对话的五层压缩机制。

各层触发顺序如下：
  第 1 层：大工具结果落盘                 — tool_registry.py
  第 2 层：移除较早的完整轮次             — snip_old_messages()
  第 3 层：微压缩可清理的工具结果         — micro_compact()
  第 4 层：读取时的上下文折叠             — apply_context_collapse()  [由 agent.py 调用]
  第 5 层：完整的 LLM 摘要压缩            — compact_messages()
"""
from __future__ import annotations

import time as _time
from pathlib import Path

import providers
from plan_mode import is_plan_mode


# ── Token 估算 ─────────────────────────────────────────────────────────────

def estimate_tokens(messages: list) -> int:
    """通过累计内容长度再除以 3.5 来粗略估算 token 数。"""
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
    """查询指定模型的上下文窗口大小。"""
    provider_name = providers.detect_provider(model)
    prov = providers.PROVIDERS.get(provider_name, {})
    return prov.get("context_limit", 128_000)


# ── 第 2 层：移除旧轮次 ─────────────────────────────────────────────────────

def snip_old_messages(
    messages: list,
    preserve_last_n_turns: int = 6,
) -> int:
    """从历史前部移除较早的完整对话轮次。

    这里的“轮次”是指一条 assistant 消息，以及紧随其后的所有 tool 结果消息。
    被移除的轮次会被替换成一条边界提示 user 消息和一条 assistant 确认消息。

    参数：
        messages:              消息字典列表，会被原地修改
        preserve_last_n_turns: 要保留的 assistant+tool 轮次数量

    返回：
        估算释放掉的 token 数。
    """
    # 识别轮次边界：(start_idx, end_idx_exclusive)
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


# ── 第 3 层：微压缩 ─────────────────────────────────────────────────────────

# 这些工具的结果可以安全清空，因为可从磁盘或网络重新获取。
_CLEARABLE_TOOLS = {"Read", "Bash", "Glob", "Grep", "WebFetch", "WebSearch", "Edit", "Write"}
# 这些工具的结果必须保留。
_PRESERVE_TOOLS  = {"Agent", "TaskCreate", "TaskUpdate", "TaskGet", "TaskList"}

_MICRO_COMPACT_IDLE_MINUTES = 60   # 触发阈值


def micro_compact(messages: list, config: dict) -> int:
    """在 prompt cache 很可能已失效时，清除较旧且可重新获取的工具结果。

    会保留最近 5 条可清理工具结果的原始内容，更早的则替换为占位文本。
    不做摘要，也不删除整轮对话，而是专门去处理那些较老、可重新获取、继续留在上下文里价值不高的工具结果。
    只有当 agent 空闲时间超过 _MICRO_COMPACT_IDLE_MINUTES 时才会触发。

    返回：
        被清空的工具结果消息数量。
    """
    last_call = config.get("_last_api_call_time")
    if last_call is None:
        return 0
    idle_min = (_time.time() - last_call) / 60
    if idle_min < _MICRO_COMPACT_IDLE_MINUTES:
        return 0

    # 收集可清理的工具结果索引（按从旧到新顺序）
    clearable: list[int] = []
    for i, m in enumerate(messages):
        if m.get("role") != "tool":
            continue
        tool_name = m.get("name", "")
        # # _CLEARABLE_TOOLS 中的工具结果可以安全清空，_PRESERVE_TOOLS 中的工具结果必须保留
        if tool_name in _CLEARABLE_TOOLS and tool_name not in _PRESERVE_TOOLS:
            clearable.append(i)

    # 保留最近 5 条，其余清空
    to_clear = clearable[:-5] if len(clearable) > 5 else []
    for i in to_clear:
        messages[i]["content"] = "[Old tool result content cleared]"
    return len(to_clear)


# ── 第 4 层：上下文折叠（读取时投影） ───────────────────────────────────────

def apply_context_collapse(messages: list, config: dict) -> list:
    """仅为本次 API 调用返回一个压缩后的消息视图。

    不会修改 `messages` 本身，而是返回一个新列表。

    阈值规则：
      90 %  → 最近约 40 % token 保留原文，更早部分做摘要
      95 %  → 最近约 25 % token 保留原文，更早部分做摘要

    参数：
        messages: 当前 state.messages，不会被修改
        config:   agent 配置字典

    返回：
        本次 API 调用要使用的、可能经过压缩的消息列表。
    """
    # 保护措施：摘要调用内部不能再次递归进入折叠逻辑
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
        # 摘要失败时，退回到轻量截断策略
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
    """用紧凑提示词调用 LLM，为 old_messages 生成摘要。"""
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


# ── 第 5 层：完整 LLM 摘要 ─────────────────────────────────────────────────

# 结构化的 9 维摘要系统提示词
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
    """把旧消息压缩成结构化的 LLM 摘要（第 4 层）。

    特性：
    - 使用结构化的 9 维提示词，不做 content[:500] 这种简单截断
    - 带熔断器：连续失败 3 次后停止尝试
    - 压缩后恢复：重新注入最近访问的文件和计划文件

    参数：
        messages: 完整消息列表
        config:   agent 配置字典（必须包含 "model"）
        focus:    可选的额外关注点说明

    返回：
        成功时返回压缩后的新消息列表；失败时返回原始列表。
    """
    # 熔断器
    failures = config.get("_compact_failures", 0)
    if failures >= 3:
        # 如果最近连续 3 次压缩失败，直接放弃继续尝试
        return messages

    # 决定“旧历史”和“最近历史”的分界点
    # 尽量保留最近约 30% token 的原始消息，较早 70% 进入摘要
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
        # 调用一次模型来生成摘要
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
        return messages  # 兜底：保留原始消息

    # 成功后重置失败计数器
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

    # 压缩后的恢复注入
    compacted.extend(_restore_recent_files(config))
    compacted.extend(_restore_active_skills(config))

    return compacted


# ── 压缩后恢复 ─────────────────────────────────────────────────────────────

def _restore_recent_files(config: dict, max_files: int = 5, token_budget: int = 50_000) -> list:
    """在压缩后重新注入最近访问的文件内容。"""
    log: dict = config.get("_file_access_log", {})
    if not log:
        return []

    # 按最近访问时间倒序排序
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
            # 为了不超预算，对内容做截断
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
    """在压缩后重新注入活跃的 skill 内容（尽力而为）。"""
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
    """若当前处于计划模式，则返回恢复计划文件上下文所需的消息。"""
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


# ── 辅助函数 ───────────────────────────────────────────────────────────────

def find_split_point(messages: list, keep_ratio: float = 0.3) -> int:
    """找到一个切分点，使较新的部分大约占总 token 的 keep_ratio。"""
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
    """把消息渲染成便于摘要器读取的文本，长度最多到 max_chars。"""
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


# ── 主入口 ─────────────────────────────────────────────────────────────────

def maybe_compact(state, config: dict) -> bool:
    """检查上下文窗口是否接近上限，并在需要时执行压缩。

    各层顺序：
      2. snip_old_messages  — 移除完整的旧轮次，并返回释放量
      3. micro_compact      — 长时间空闲后清空可重新获取的工具结果
      (4. apply_context_collapse — 在 agent.py 中于 API 调用前单独执行)
      5. compact_messages   — 若仍超阈值，则执行完整 LLM 摘要

    参数：
        state:  带有 .messages 列表的 AgentState
        config: agent 配置字典（必须包含 "model"）

    返回：
        是否执行过任意压缩操作。
    """
    model     = config.get("model", "")
    limit     = get_context_limit(model)
    threshold = limit * 0.7

    if estimate_tokens(state.messages) <= threshold:
        return False

    # 第 2 层：移除较早的完整轮次
    snip_old_messages(state.messages)

    # 第 3 层：长时间空闲时，微压缩可清理工具结果
    micro_compact(state.messages, config)

    if estimate_tokens(state.messages) <= threshold:
        return True

    # 压缩前 hook
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

    # 第 4 层：完整 LLM 摘要
    state.messages = compact_messages(state.messages, config)
    state.messages.extend(_restore_plan_context(config))
    return True


# ── 手动压缩 ───────────────────────────────────────────────────────────────

def manual_compact(state, config: dict, focus: str = "") -> tuple[bool, str]:
    """通过 /compact 触发的手动压缩，不受自动阈值限制。

    返回 (success, info_message)。
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
