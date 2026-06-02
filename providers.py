"""
Provider registry and streaming adapters for litecc.

Model/provider metadata ships with built-in defaults, and can be extended from:
  - ~/.litecc/models.json
  - <project>/.litecc/models.json

Project-local definitions override user-level ones.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Generator


# 内置模型注册表
_BUILTIN_PROVIDERS: dict[str, dict] = {
    "anthropic": {
        "type": "anthropic",
        "api_key_env": "ANTHROPIC_API_KEY",
        "context_limit": 200000,
        "models": [
            "claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5-20251001",
            "claude-opus-4-5", "claude-sonnet-4-5",
            "claude-3-5-sonnet-20241022", "claude-3-5-haiku-20241022",
        ],
    },
    "openai": {
        "type": "openai",
        "api_key_env": "OPENAI_API_KEY",
        "base_url": "https://api.openai.com/v1",
        "context_limit": 128000,
        "max_completion_tokens": 16384,
        "models": [
            "gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-4.1", "gpt-4.1-mini",
            "o3-mini", "o1", "o1-mini",
        ],
    },
    "gemini": {
        "type": "openai",
        "api_key_env": "GEMINI_API_KEY",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "context_limit": 1000000,
        "models": [
            "gemini-2.5-pro-preview-03-25",
            "gemini-2.0-flash", "gemini-2.0-flash-lite",
            "gemini-1.5-pro", "gemini-1.5-flash",
        ],
    },
    "kimi": {
        "type": "openai",
        "api_key_env": "MOONSHOT_API_KEY",
        "base_url": "https://api.moonshot.cn/v1",
        "context_limit": 128000,
        "models": [
            "moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k",
            "kimi-latest",
        ],
    },
    "qwen": {
        "type": "openai",
        "api_key_env": "DASHSCOPE_API_KEY",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "context_limit": 1000000,
        "models": [
            "qwen-max", "qwen-max-latest",
            "Qwen3-235B-A22B", "Qwen3-30B-A3B",
            "qwen-plus", "qwen-turbo", "qwen-long",
            "qwen2.5-72b-instruct", "qwen2.5-coder-32b-instruct",
            "qwq-32b",
        ],
    },
    "zhipu": {
        "type": "openai",
        "api_key_env": "ZHIPU_API_KEY",
        "base_url": "https://open.bigmodel.cn/api/paas/v4/",
        "context_limit": 128000,
        "models": [
            "glm-4-plus", "glm-4", "glm-4-flash", "glm-4-air",
            "glm-z1-flash",
        ],
    },
    "deepseek": {
        "type": "openai",
        "api_key_env": "DEEPSEEK_API_KEY",
        "base_url": "https://api.deepseek.com/v1",
        "context_limit": 1000000,
        "models": [
            "deepseek-v4-pro", "deepseek-v4-flash",
            "deepseek-chat", "deepseek-coder", "deepseek-reasoner",
        ],
    },
    "minimax": {
        "type": "openai",
        "api_key_env": "MINIMAX_API_KEY",
        "base_url": "https://api.minimaxi.chat/v1",
        "context_limit": 1000000,
        "models": [
            "MiniMax-Text-01", "MiniMax-VL-01",
            "abab6.5s-chat", "abab6.5-chat",
            "abab5.5s-chat", "abab5.5-chat",
        ],
    },
    "custom": {
        "type": "openai",
        "api_key_env": "CUSTOM_API_KEY",
        "base_url": None,
        "context_limit": 128000,
        "models": [],
    },
}

# 内置模型成本表
_BUILTIN_COSTS: dict[str, tuple[float, float]] = {
    "claude-opus-4-6": (15.0, 75.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5-20251001": (0.8, 4.0),
    "gpt-4o": (2.5, 10.0),
    "gpt-4o-mini": (0.15, 0.6),
    "o3-mini": (1.1, 4.4),
    "gemini-2.0-flash": (0.075, 0.3),
    "gemini-1.5-pro": (1.25, 5.0),
    "gemini-2.5-pro-preview-03-25": (1.25, 10.0),
    "moonshot-v1-8k": (1.0, 3.0),
    "moonshot-v1-32k": (2.4, 7.0),
    "moonshot-v1-128k": (8.0, 24.0),
    "qwen-max": (2.4, 9.6),
    "qwen-plus": (0.4, 1.2),
    "deepseek-chat": (0.27, 1.1),
    "deepseek-reasoner": (0.55, 2.19),
    "glm-4-plus": (0.7, 0.7),
    "MiniMax-Text-01": (0.7, 2.1),
    "abab6.5s-chat": (0.1, 0.1),
    "abab6.5-chat": (0.5, 0.5),
}

# 内置模型前缀表
_BUILTIN_PREFIXES: list[tuple[str, str]] = [
    ("claude-", "anthropic"),
    ("gpt-", "openai"),
    ("o1", "openai"),
    ("o3", "openai"),
    ("gemini-", "gemini"),
    ("moonshot-", "kimi"),
    ("kimi-", "kimi"),
    ("qwen", "qwen"),
    ("qwq-", "qwen"),
    ("glm-", "zhipu"),
    ("deepseek-", "deepseek"),
    ("minimax-", "minimax"),
    ("abab", "minimax"),
]

# 用户模型目录
USER_MODEL_CATALOG = Path.home() / ".litecc" / "models.json"
PROJECT_MODEL_CATALOG_DIR = ".litecc"
PROJECT_MODEL_CATALOG_NAME = "models.json"

# 模型注册表
PROVIDERS: dict[str, dict] = {}
COSTS: dict[str, tuple[float, float]] = {}
_PREFIXES: list[tuple[str, str]] = []
_CATALOG_SIGNATURE: tuple | None = None

# 加载 JSON 文件
def _load_json_file(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}

# 查找项目模型目录
def _find_project_model_catalog(start_dir: Path | None = None) -> Path | None:
    current = (start_dir or Path.cwd()).resolve()
    while True:
        candidate = current / PROJECT_MODEL_CATALOG_DIR / PROJECT_MODEL_CATALOG_NAME
        if candidate.exists():
            return candidate
        parent = current.parent
        if parent == current:
            return None
        current = parent

# 查找所有模型目录
def _catalog_files(start_dir: Path | None = None) -> list[Path]:
    files: list[Path] = []
    if USER_MODEL_CATALOG.exists():
        files.append(USER_MODEL_CATALOG)
    project_file = _find_project_model_catalog(start_dir)
    if project_file is not None:
        files.append(project_file)
    return files

# 计算路径签名
def _path_signature(path: Path) -> tuple[str, int, int] | tuple[str, None, None]:
    if not path.exists():
        return (str(path), None, None)
    try:
        stat = path.stat()
        return (str(path), stat.st_mtime_ns, stat.st_size)
    except Exception:
        return (str(path), None, None)

# 计算模型目录签名
def _catalog_signature(start_dir: Path | None = None) -> tuple:
    return tuple(_path_signature(path) for path in _catalog_files(start_dir))


def _normalize_model_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _register_prefix(prefix: str, provider_name: str, prefixes: list[tuple[str, str]]) -> None:
    normalized = prefix.strip().lower()
    if not normalized:
        return
    entry = (normalized, provider_name)
    if entry not in prefixes:
        prefixes.append(entry)


def _merge_model_lists(base: list[str], extra: list[str]) -> list[str]:
    merged: list[str] = []
    for model_name in [*base, *extra]:
        if model_name not in merged:
            merged.append(model_name)
    return merged


def _normalize_cost_pair(value) -> tuple[float, float] | None:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            return (float(value[0]), float(value[1]))
        except (TypeError, ValueError):
            return None
    return None


def _apply_model_catalog(data: dict, providers: dict[str, dict],
                         costs: dict[str, tuple[float, float]],
                         prefixes: list[tuple[str, str]]) -> None:
    raw_providers = data.get("providers", {})
    if isinstance(raw_providers, dict):
        for provider_name, raw_provider in raw_providers.items():
            if not isinstance(raw_provider, dict):
                continue

            base = copy.deepcopy(providers.get(provider_name, {}))
            merged = {**base, **raw_provider}

            base_models = _normalize_model_list(base.get("models", []))
            new_models = _normalize_model_list(raw_provider.get("models", []))
            merged["models"] = _merge_model_lists(base_models, new_models)

            provider_prefixes: list[str] = []
            raw_provider_prefixes = raw_provider.get("prefixes", [])
            if isinstance(raw_provider_prefixes, list):
                provider_prefixes = [str(item) for item in raw_provider_prefixes if str(item).strip()]
            merged.pop("prefixes", None)

            providers[provider_name] = merged

            for model_name in merged["models"]:
                _register_prefix(model_name, provider_name, prefixes)
            for prefix in provider_prefixes:
                _register_prefix(prefix, provider_name, prefixes)

    raw_costs = data.get("costs", {})
    if isinstance(raw_costs, dict):
        for model_name, pair in raw_costs.items():
            normalized = _normalize_cost_pair(pair)
            if normalized is not None:
                costs[str(model_name)] = normalized

    raw_prefixes = data.get("prefixes", {})
    if isinstance(raw_prefixes, dict):
        for prefix, provider_name in raw_prefixes.items():
            if isinstance(provider_name, str):
                _register_prefix(str(prefix), provider_name, prefixes)
    elif isinstance(raw_prefixes, list):
        for item in raw_prefixes:
            if not isinstance(item, dict):
                continue
            prefix = str(item.get("prefix", "")).strip()
            provider_name = str(item.get("provider", "")).strip()
            if prefix and provider_name:
                _register_prefix(prefix, provider_name, prefixes)

# 重新加载模型目录
def reload_provider_catalog(start_dir: Path | None = None) -> None:
    global _CATALOG_SIGNATURE

    providers = copy.deepcopy(_BUILTIN_PROVIDERS)
    costs = dict(_BUILTIN_COSTS)
    prefixes = list(_BUILTIN_PREFIXES)

    for provider_name, provider_data in providers.items():
        for model_name in _normalize_model_list(provider_data.get("models", [])):
            _register_prefix(model_name, provider_name, prefixes)

    for path in _catalog_files(start_dir):
        _apply_model_catalog(_load_json_file(path), providers, costs, prefixes)

    PROVIDERS.clear()
    PROVIDERS.update(providers)

    COSTS.clear()
    COSTS.update(costs)

    _PREFIXES.clear()
    _PREFIXES.extend(prefixes)

    _CATALOG_SIGNATURE = _catalog_signature(start_dir)

# 确保模型目录已加载
def ensure_provider_catalog_loaded(start_dir: Path | None = None) -> None:
    global _CATALOG_SIGNATURE # 
    signature = _catalog_signature(start_dir)
    if _CATALOG_SIGNATURE != signature or not PROVIDERS:
        reload_provider_catalog(start_dir)

# 检测模型提供程序
def detect_provider(model: str) -> str:
    """Detect provider from `provider/model` or from known model prefixes."""
    ensure_provider_catalog_loaded()
    if "/" in model:
        return model.split("/", 1)[0]
    normalized = model.lower()
    for prefix, provider_name in _PREFIXES:
        if normalized.startswith(prefix):
            return provider_name
    return "openai"

# 移除可选的提供程序前缀
def bare_model(model: str) -> str:
    """Strip the optional `provider/` prefix from a model string."""
    return model.split("/", 1)[1] if "/" in model else model


def get_api_key(provider_name: str, config: dict) -> str:
    ensure_provider_catalog_loaded()
    prov = PROVIDERS.get(provider_name, {})

    env_var = prov.get("api_key_env")
    if env_var:
        env_key = os.environ.get(env_var, "")
        if env_key:
            return env_key

    project_secrets = config.get("_project_secrets", {})
    if isinstance(project_secrets, dict):
        project_key = project_secrets.get(f"{provider_name}_api_key", "")
        if project_key:
            return project_key

    cfg_key = config.get(f"{provider_name}_api_key", "")
    if cfg_key:
        return cfg_key

    return prov.get("api_key", "")


def calc_cost(model: str, in_tok: int, out_tok: int) -> float:
    ensure_provider_catalog_loaded()
    ic, oc = COSTS.get(bare_model(model), (0.0, 0.0))
    return (in_tok * ic + out_tok * oc) / 1_000_000


def tools_to_openai(tool_schemas: list) -> list:
    """Convert Anthropic-style tool schemas to OpenAI function tools."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in tool_schemas
    ]


def messages_to_anthropic(messages: list) -> list:
    """Convert internal message format to Anthropic message blocks."""
    result = []
    i = 0
    while i < len(messages):
        message = messages[i]
        role = message["role"]

        if role == "user":
            result.append({"role": "user", "content": message["content"]})
            i += 1
        elif role == "assistant":
            blocks = []
            text = message.get("content", "")
            if text:
                blocks.append({"type": "text", "text": text})
            for tool_call in message.get("tool_calls", []):
                blocks.append({
                    "type": "tool_use",
                    "id": tool_call["id"],
                    "name": tool_call["name"],
                    "input": tool_call["input"],
                })
            result.append({"role": "assistant", "content": blocks})
            i += 1
        elif role == "tool":
            tool_blocks = []
            while i < len(messages) and messages[i]["role"] == "tool":
                tool_message = messages[i]
                tool_blocks.append({
                    "type": "tool_result",
                    "tool_use_id": tool_message["tool_call_id"],
                    "content": tool_message["content"],
                })
                i += 1
            result.append({"role": "user", "content": tool_blocks})
        else:
            i += 1

    return result


def messages_to_openai(messages: list) -> list:
    """Convert internal message format to OpenAI-compatible chat messages."""
    result = []
    for message in messages:
        role = message["role"]

        if role == "user":
            content = message["content"]
            if message.get("images"):
                parts = [{"type": "text", "text": content}]
                for image_b64 in message["images"]:
                    parts.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{image_b64}"},
                    })
                result.append({"role": "user", "content": parts})
            else:
                result.append({"role": "user", "content": content})
        elif role == "assistant":
            out: dict = {"role": "assistant", "content": message.get("content") or None}
            if message.get("reasoning_content"):
                out["reasoning_content"] = message["reasoning_content"]
            tool_calls = message.get("tool_calls", [])
            if tool_calls:
                out["tool_calls"] = []
                for tool_call in tool_calls:
                    rendered = {
                        "id": tool_call["id"],
                        "type": "function",
                        "function": {
                            "name": tool_call["name"],
                            "arguments": json.dumps(tool_call["input"], ensure_ascii=False),
                        },
                    }
                    if tool_call.get("extra_content"):
                        rendered["extra_content"] = tool_call["extra_content"]
                    out["tool_calls"].append(rendered)
            result.append(out)
        elif role == "tool":
            result.append({
                "role": "tool",
                "tool_call_id": message["tool_call_id"],
                "content": message["content"],
            })

    return result


class TextChunk:
    """Streaming text chunk."""

    def __init__(self, text):
        self.text = text


class ThinkingChunk:
    """Streaming reasoning/thinking chunk."""

    def __init__(self, text):
        self.text = text


class Response:
    """Completed assistant response."""

    def __init__(self, text, tool_calls, in_tokens, out_tokens, reasoning_content=""):
        self.text = text
        self.tool_calls = tool_calls
        self.in_tokens = in_tokens
        self.out_tokens = out_tokens
        self.reasoning_content = reasoning_content


def stream_anthropic(
    api_key: str,
    model: str,
    system: str,
    messages: list,
    tool_schemas: list,
    config: dict,
) -> Generator:
    """Stream a response from Anthropic."""
    import anthropic as _ant

    client = _ant.Anthropic(api_key=api_key)
    kwargs = {
        "model": model,
        "max_tokens": config.get("max_tokens", 8192),
        "system": system,
        "messages": messages_to_anthropic(messages),
        "tools": tool_schemas,
    }
    if config.get("thinking"):
        kwargs["thinking"] = {
            "type": "enabled",
            "budget_tokens": config.get("thinking_budget", 10000),
        }

    tool_calls = []
    text = ""

    with client.messages.stream(**kwargs) as stream:
        for event in stream:
            event_type = getattr(event, "type", None)
            if event_type != "content_block_delta":
                continue
            delta = event.delta
            delta_type = getattr(delta, "type", None)
            if delta_type == "text_delta":
                text += delta.text
                yield TextChunk(delta.text)
            elif delta_type == "thinking_delta":
                yield ThinkingChunk(delta.thinking)

        final = stream.get_final_message()
        for block in final.content:
            if block.type == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })

        yield Response(
            text,
            tool_calls,
            final.usage.input_tokens,
            final.usage.output_tokens,
        )


def stream_openai_compat(
    api_key: str,
    base_url: str,
    model: str,
    system: str,
    messages: list,
    tool_schemas: list,
    config: dict,
) -> Generator:
    """Stream a response from an OpenAI-compatible API."""
    from openai import OpenAI

    ensure_provider_catalog_loaded()
    client = OpenAI(api_key=api_key or "dummy", base_url=base_url)
    request_messages = [{"role": "system", "content": system}] + messages_to_openai(messages)

    kwargs: dict = {
        "model": model,
        "messages": request_messages,
        "stream": True,
    }

    if tool_schemas and not config.get("no_tools"):
        kwargs["tools"] = tools_to_openai(tool_schemas)
        if not config.get("disable_tool_choice"):
            kwargs["tool_choice"] = "auto"

    if config.get("max_tokens"):
        provider_cap = PROVIDERS.get(detect_provider(model), {}).get("max_completion_tokens")
        max_tokens = config["max_tokens"]
        kwargs["max_tokens"] = min(max_tokens, provider_cap) if provider_cap else max_tokens

    text = ""
    reasoning_text = ""
    tool_buffer: dict = {}
    in_tokens = 0
    out_tokens = 0

    stream = client.chat.completions.create(**kwargs)
    for chunk in stream:
        if not chunk.choices:
            if hasattr(chunk, "usage") and chunk.usage:
                in_tokens = chunk.usage.prompt_tokens
                out_tokens = chunk.usage.completion_tokens
            continue

        choice = chunk.choices[0]
        delta = choice.delta

        reasoning_chunk = getattr(delta, "reasoning_content", None)
        if reasoning_chunk:
            reasoning_text += reasoning_chunk
            yield ThinkingChunk(reasoning_chunk)

        if delta.content:
            text += delta.content
            yield TextChunk(delta.content)

        if delta.tool_calls:
            for tool_call in delta.tool_calls:
                idx = tool_call.index
                if idx not in tool_buffer:
                    tool_buffer[idx] = {"id": "", "name": "", "args": "", "extra_content": None}
                if tool_call.id:
                    tool_buffer[idx]["id"] = tool_call.id
                if tool_call.function:
                    if tool_call.function.name:
                        tool_buffer[idx]["name"] += tool_call.function.name
                    if tool_call.function.arguments:
                        tool_buffer[idx]["args"] += tool_call.function.arguments
                extra = getattr(tool_call, "extra_content", None)
                if extra:
                    tool_buffer[idx]["extra_content"] = extra

        if hasattr(chunk, "usage") and chunk.usage:
            in_tokens = chunk.usage.prompt_tokens or in_tokens
            out_tokens = chunk.usage.completion_tokens or out_tokens

    tool_calls = []
    for idx in sorted(tool_buffer):
        value = tool_buffer[idx]
        try:
            parsed_input = json.loads(value["args"]) if value["args"] else {}
        except json.JSONDecodeError:
            parsed_input = {"_raw": value["args"]}
        rendered = {
            "id": value["id"] or f"call_{idx}",
            "name": value["name"],
            "input": parsed_input,
        }
        if value.get("extra_content"):
            rendered["extra_content"] = value["extra_content"]
        tool_calls.append(rendered)

    yield Response(text, tool_calls, in_tokens, out_tokens, reasoning_content=reasoning_text)


def stream(
    model: str,
    system: str,
    messages: list,
    tool_schemas: list,
    config: dict,
) -> Generator:
    """Unified streaming entrypoint."""
    ensure_provider_catalog_loaded()
    provider_name = detect_provider(model)
    model_name = bare_model(model)
    prov = PROVIDERS.get(provider_name, PROVIDERS["openai"])
    api_key = get_api_key(provider_name, config)

    if prov["type"] == "anthropic":
        yield from stream_anthropic(api_key, model_name, system, messages, tool_schemas, config)
        return

    if provider_name == "custom":
        base_url = (
            config.get("custom_base_url")
            or os.environ.get("CUSTOM_BASE_URL", "")
            or prov.get("base_url", "")
        )
        if not base_url:
            raise ValueError(
                "custom provider requires a base_url. "
                "Set CUSTOM_BASE_URL, configure /config custom_base_url=http://..., "
                "or define base_url in models.json."
            )
    else:
        base_url = prov.get("base_url", "https://api.openai.com/v1")

    yield from stream_openai_compat(
        api_key,
        base_url,
        model_name,
        system,
        messages,
        tool_schemas,
        config,
    )


reload_provider_catalog()
