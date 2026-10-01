from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from easychain.spec import FlowSpec, parse_spec
from easychain.testing.fake_openai import FakeOpenAI

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = Path(__file__).resolve().parents[1] / "src" / "easychain" / "templates"

# Keys and endpoints from the developer's shell must never leak into tests.
_ENV_TO_CLEAR = [
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_API_BASE",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_API_URL",
    "OLLAMA_HOST",
    "EASYCHAIN_SECRET_KEY",
    "EASYCHAIN_WORKSPACE",
    "EASYCHAIN_HOME",
    "LANGSMITH_TRACING",
    "LANGCHAIN_TRACING_V2",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Any:
    for name in _ENV_TO_CLEAR:
        monkeypatch.delenv(name, raising=False)
    yield
    # The secrets vault writes keys into os.environ; don't let them reach the next test.
    for name in _ENV_TO_CLEAR:
        os.environ.pop(name, None)


@pytest.fixture(scope="session")
def fake_server() -> Any:
    with FakeOpenAI() as fake:
        yield fake


@pytest.fixture
def fake_openai(fake_server: FakeOpenAI, monkeypatch: pytest.MonkeyPatch) -> FakeOpenAI:
    """Point langchain-openai at the fake server with a test key."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key-123456")
    monkeypatch.setenv("OPENAI_BASE_URL", fake_server.base_url)
    return fake_server


def make_spec(
    steps: list[dict], connections: list[tuple] | list[dict], name: str = "Test flow", **extra: Any
) -> FlowSpec:
    conns = []
    for c in connections:
        if isinstance(c, dict):
            conns.append(c)
        elif len(c) == 3:
            conns.append({"from": c[0], "exit": c[1], "to": c[2]})
        else:
            conns.append({"from": c[0], "to": c[1]})
    return parse_spec({"name": name, "steps": steps, "connections": conns, **extra})


def input_step(*fields: str | dict, mode: str = "form") -> dict:
    return {
        "id": "input",
        "type": "input",
        "settings": {
            "mode": mode,
            "fields": [f if isinstance(f, dict) else {"name": f} for f in fields],
        },
    }


def output_step(*fields: str) -> dict:
    return {"id": "output", "type": "output", "settings": {"fields": list(fields)}}
