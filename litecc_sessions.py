"""litecc 的会话持久化辅助模块。

这个模块负责把运行时 `AgentState` 转换成可落盘的 JSON 结构，
并提供最近会话、每日备份、历史汇总等读写能力。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path


def build_session_data(state, session_id: str | None = None) -> dict:
    """把当前会话状态序列化成可写入 JSON 的字典。"""
    return {
        "session_id": session_id or uuid.uuid4().hex[:8],
        "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "messages": [
            m
            if not isinstance(m.get("content"), list)
            else {
                **m,
                "content": [
                    b if isinstance(b, dict) else b.model_dump()
                    for b in m["content"]
                ],
            }
            for m in state.messages
        ],
        "turn_count": state.turn_count,
        "total_input_tokens": state.total_input_tokens,
        "total_output_tokens": state.total_output_tokens,
    }


def restore_state_from_data(state, data: dict) -> None:
    """把已保存的会话数据恢复回运行时状态对象。"""
    state.messages = data.get("messages", [])
    state.turn_count = data.get("turn_count", 0)
    state.total_input_tokens = data.get("total_input_tokens", 0)
    state.total_output_tokens = data.get("total_output_tokens", 0)


def save_named_session(state, path: Path, session_id: str | None = None) -> dict:
    """按指定路径保存一个会话文件。"""
    data = build_session_data(state, session_id=session_id)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    return {"path": path, "data": data}


def save_latest_session(state, config: dict | None = None) -> dict | None:
    """保存最近会话、每日备份和历史汇总。

    返回值里带有新写入的路径和统计信息，便于上层统一打印提示。
    如果当前没有任何消息，则返回 `None`。
    """
    from config import DAILY_DIR, MR_SESSION_DIR, SESSION_HIST_FILE

    if not state.messages:
        return None

    cfg = config or {}
    daily_limit = cfg.get("session_daily_limit", 5)
    history_limit = cfg.get("session_history_limit", 100)

    now = datetime.now()
    session_id = uuid.uuid4().hex[:8]
    ts = now.strftime("%H%M%S")
    date_str = now.strftime("%Y-%m-%d")
    data = build_session_data(state, session_id=session_id)
    payload = json.dumps(data, indent=2, default=str)

    # 1. 保存“最近一次会话”，用于快速恢复。
    MR_SESSION_DIR.mkdir(parents=True, exist_ok=True)
    latest_path = MR_SESSION_DIR / "session_latest.json"
    latest_path.write_text(payload, encoding="utf-8")

    # 2. 保存当天的时间戳快照。
    day_dir = DAILY_DIR / date_str
    day_dir.mkdir(parents=True, exist_ok=True)
    daily_path = day_dir / f"session_{ts}_{session_id}.json"
    daily_path.write_text(payload, encoding="utf-8")

    # 3. 限制每日备份数量，避免目录无限增长。
    daily_files = sorted(day_dir.glob("session_*.json"))
    for old in daily_files[:-daily_limit]:
        old.unlink(missing_ok=True)

    # 4. 维护总历史汇总文件。
    if SESSION_HIST_FILE.exists():
        try:
            hist = json.loads(SESSION_HIST_FILE.read_text(encoding="utf-8"))
        except Exception:
            hist = {"total_turns": 0, "sessions": []}
    else:
        hist = {"total_turns": 0, "sessions": []}

    hist["sessions"].append(data)
    if len(hist["sessions"]) > history_limit:
        hist["sessions"] = hist["sessions"][-history_limit:]
    hist["total_turns"] = sum(s.get("turn_count", 0) for s in hist["sessions"])
    SESSION_HIST_FILE.write_text(json.dumps(hist, indent=2, default=str), encoding="utf-8")

    return {
        "session_id": session_id,
        "latest_path": latest_path,
        "daily_path": daily_path,
        "history_path": SESSION_HIST_FILE,
        "history_sessions": len(hist["sessions"]),
        "history_turns": hist["total_turns"],
    }


def collect_saved_sessions() -> list[Path]:
    """按“最近优先”的顺序收集所有可加载的会话文件。"""
    from config import DAILY_DIR, MR_SESSION_DIR, SESSIONS_DIR

    sessions: list[Path] = []
    if DAILY_DIR.exists():
        for day_dir in sorted(DAILY_DIR.iterdir(), reverse=True):
            if day_dir.is_dir():
                sessions.extend(sorted(day_dir.glob("session_*.json"), reverse=True))

    # 兼容旧目录：如果没有每日目录数据，再回退到旧的最近会话目录。
    if not sessions and MR_SESSION_DIR.exists():
        sessions = [
            s for s in sorted(MR_SESSION_DIR.glob("*.json"), reverse=True)
            if s.name != "session_latest.json"
        ]

    # 手动 /save 保存的会话单独放在普通会话目录里。
    sessions.extend(sorted(SESSIONS_DIR.glob("session_*.json"), reverse=True))
    return sessions


def resolve_session_path(name: str) -> Path:
    """把用户输入的名称解析成具体会话路径。"""
    from config import DAILY_DIR, MR_SESSION_DIR, SESSIONS_DIR

    path = Path(name) if "/" in name or "\\" in name else SESSIONS_DIR / name
    if not path.exists() and ("/" not in name and "\\" not in name):
        alt_paths = [MR_SESSION_DIR / name]
        if DAILY_DIR.exists():
            alt_paths.extend(d / name for d in DAILY_DIR.iterdir() if d.is_dir())
        for alt in alt_paths:
            if alt.exists():
                return alt
    return path


def load_session_file(path: Path) -> dict:
    """读取会话 JSON 文件。"""
    return json.loads(path.read_text(encoding="utf-8"))
