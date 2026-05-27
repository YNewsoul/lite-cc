from __future__ import annotations

import json
from pathlib import Path

import config as _config
import providers


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def test_reload_provider_catalog_loads_project_provider(tmp_path, monkeypatch):
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    user_models = home / ".litecc" / "models.json"
    project_models = repo / ".litecc" / "models.json"

    _write_json(user_models, {
        "providers": {
            "ollama": {
                "type": "openai",
                "base_url": "http://127.0.0.1:11434/v1",
                "context_limit": 32768,
                "models": ["qwen2.5-coder:7b"],
                "prefixes": ["qwen2.5-coder"],
            }
        },
        "costs": {
            "qwen2.5-coder:7b": [0, 0],
        },
    })
    _write_json(project_models, {
        "providers": {
            "ollama": {
                "base_url": "http://localhost:11434/v1",
                "models": ["deepseek-r1:8b"],
            }
        }
    })

    monkeypatch.setattr(providers, "USER_MODEL_CATALOG", user_models)
    monkeypatch.chdir(repo)

    providers.reload_provider_catalog()

    assert "ollama" in providers.PROVIDERS
    assert providers.PROVIDERS["ollama"]["base_url"] == "http://localhost:11434/v1"
    assert providers.PROVIDERS["ollama"]["context_limit"] == 32768
    assert providers.PROVIDERS["ollama"]["models"] == ["qwen2.5-coder:7b", "deepseek-r1:8b"]
    assert providers.detect_provider("ollama/qwen2.5-coder:7b") == "ollama"
    assert providers.detect_provider("qwen2.5-coder:7b") == "ollama"
    assert providers.detect_provider("deepseek-r1:8b") == "ollama"
    assert providers.calc_cost("ollama/qwen2.5-coder:7b", 1000, 1000) == 0.0


def test_reload_provider_catalog_accepts_top_level_prefix_map(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    project_models = repo / ".litecc" / "models.json"
    _write_json(project_models, {
        "providers": {
            "lmstudio": {
                "type": "openai",
                "base_url": "http://127.0.0.1:1234/v1",
                "models": ["local-model"],
            }
        },
        "prefixes": {
            "my-local-": "lmstudio",
        },
    })

    monkeypatch.setattr(providers, "USER_MODEL_CATALOG", tmp_path / "missing.json")
    monkeypatch.chdir(repo)

    providers.reload_provider_catalog()

    assert providers.detect_provider("my-local-model") == "lmstudio"
    assert providers.PROVIDERS["lmstudio"]["base_url"] == "http://127.0.0.1:1234/v1"


def test_load_project_secrets_accepts_dynamic_provider_keys(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    secrets_file = repo / ".litecc" / "secrets.json"
    _write_json(secrets_file, {
        "ollama_api_key": "secret-value",
        "api_key": "fallback-anthropic",
    })

    monkeypatch.chdir(repo)

    secrets, path = _config._load_project_secrets()

    assert path == secrets_file
    assert secrets["ollama_api_key"] == "secret-value"
    assert secrets["anthropic_api_key"] == "fallback-anthropic"
