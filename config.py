"""Configuration management for litecc (multi-provider)."""
import json
from pathlib import Path

CONFIG_DIR        = Path.home() / ".litecc"
CONFIG_FILE       = CONFIG_DIR  / "config.json"
HISTORY_FILE      = CONFIG_DIR  / "input_history.txt"
SESSIONS_DIR      = CONFIG_DIR  / "sessions"
DAILY_DIR         = SESSIONS_DIR / "daily"       # daily/YYYY-MM-DD/session_*.json
SESSION_HIST_FILE = SESSIONS_DIR / "history.json" # master: all sessions ever
PROJECT_SECRETS_NAME = "secrets.json"

# 为兼容旧版本而保留（/resume 仍会从这里读取）
MR_SESSION_DIR = SESSIONS_DIR / "mr_sessions"

SECRET_CONFIG_KEYS = {
    "api_key",
    "anthropic_api_key",
    "openai_api_key",
    "gemini_api_key",
    "kimi_api_key",
    "qwen_api_key",
    "zhipu_api_key",
    "deepseek_api_key",
    "minimax_api_key",
    "custom_api_key",
}

DEFAULTS = {
    "model":            "zhipu/glm-4",
    "subagent_model":   "zhipu/glm-4-flash",  # lightweight model for memory ops
    "max_tokens":       40000,
    "permission_mode":  "auto",   # auto | accept-all | manual  (plan mode is a separate runtime overlay)
    "verbose":          False,
    "thinking":         False,
    "thinking_budget":  10000,
    "custom_base_url":  "",       # for "custom" provider
    "max_tool_output":  32000,
    "max_agent_depth":  3,
    "max_concurrent_agents": 3,
    "session_daily_limit":   10,    # max sessions kept per day in daily/
    "session_history_limit": 200,  # max sessions kept in history.json
    # API keys are injected from environment variables or project-local secrets.
    "anthropic_api_key": "",
    "openai_api_key":    "",
    "gemini_api_key":    "",
    "kimi_api_key":      "",
    "qwen_api_key":      "",
    "zhipu_api_key":     "",
    "deepseek_api_key":  "",
    "minimax_api_key":   "",
    "custom_api_key":    "",
}


def _load_json_file(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _find_project_secrets_file(start_dir: Path | None = None) -> Path | None:
    current = (start_dir or Path.cwd()).resolve()
    while True:
        candidate = current / ".litecc" / PROJECT_SECRETS_NAME
        if candidate.exists():
            return candidate
        parent = current.parent
        if parent == current:
            return None
        current = parent


def _load_project_secrets() -> tuple[dict, Path | None]:
    secrets_path = _find_project_secrets_file()
    if secrets_path is None:
        return {}, None

    raw = _load_json_file(secrets_path)
    if not isinstance(raw, dict):
        return {}, secrets_path

    secrets = {k: v for k, v in raw.items() if k in SECRET_CONFIG_KEYS and isinstance(v, str)}
    if secrets.get("api_key") and not secrets.get("anthropic_api_key"):
        secrets["anthropic_api_key"] = secrets["api_key"]
    return secrets, secrets_path


def load_config() -> dict:
    CONFIG_DIR.mkdir(exist_ok=True)
    SESSIONS_DIR.mkdir(exist_ok=True)
    cfg = dict(DEFAULTS)
    if CONFIG_FILE.exists():
        cfg.update(_load_json_file(CONFIG_FILE))
    # 向后兼容：旧版单一 api_key 映射到 anthropic_api_key
    if cfg.get("api_key") and not cfg.get("anthropic_api_key"):
        cfg["anthropic_api_key"] = cfg.pop("api_key")
    # 向后兼容：旧配置里可能仍有 permission_mode == "plan"
    # 计划模式现在是独立的运行时叠加层，这里静默降级处理。
    if cfg.get("permission_mode") == "plan":
        cfg["permission_mode"] = "auto"

    project_secrets, project_secrets_path = _load_project_secrets()
    cfg["_project_secrets"] = project_secrets
    if project_secrets_path is not None:
        cfg["_project_secrets_file"] = str(project_secrets_path)
    return cfg


def save_config(cfg: dict):
    CONFIG_DIR.mkdir(exist_ok=True)
    # 保存前移除内部运行时字段（例如 _run_query_callback）
    data = {k: v for k, v in cfg.items() if not k.startswith("_")}
    CONFIG_FILE.write_text(json.dumps(data, indent=2))


def current_provider(cfg: dict) -> str:
    from providers import detect_provider
    return detect_provider(cfg.get("model", "claude-opus-4-6"))


def has_api_key(cfg: dict) -> bool:
    """Check whether the active provider has an API key configured."""
    from providers import get_api_key
    pname = current_provider(cfg)
    key = get_api_key(pname, cfg)
    return bool(key)


def calc_cost(model: str, in_tokens: int, out_tokens: int) -> float:
    from providers import calc_cost as _cc
    return _cc(model, in_tokens, out_tokens)
