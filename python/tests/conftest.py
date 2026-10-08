from __future__ import annotations

import asyncio
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from easychain import _platform
from easychain.spec import FlowSpec, parse_spec
from easychain.testing.fake_openai import FakeOpenAI

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = Path(__file__).resolve().parents[1] / "src" / "easychain" / "templates"

# Keys, endpoints and databases from the developer's shell must never leak into tests.
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
    "EASYCHAIN_DATABASE_URL",
    "EASYCHAIN_KNOWLEDGE_URL",
    "EASYCHAIN_MCP_SERVERS",
    "EASYCHAIN_WORKER",
    "EASYCHAIN_SAVEPOINTS",
    "EASYCHAIN_WEB_DIST",
    "EASYCHAIN_CORS_ORIGINS",
    "EASYCHAIN_PUBLIC_URL",
    "EASYCHAIN_ALLOWED_HOSTS",
    "EASYCHAIN_HOST",
    "LANGSMITH_TRACING",
    "LANGCHAIN_TRACING_V2",
]


@pytest.fixture(scope="session")
def _test_home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("easychain-home")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch, _test_home: Path) -> Any:
    for name in _ENV_TO_CLEAR:
        monkeypatch.delenv(name, raising=False)
    # Nothing a test does may land in the developer's own ~/.easychain.
    monkeypatch.setenv("EASYCHAIN_HOME", str(_test_home))
    # TestClient calls the app as http://testserver.
    monkeypatch.setenv("EASYCHAIN_ALLOWED_HOSTS", "testserver")
    yield
    # The secrets vault writes keys into os.environ; don't let them reach the next test.
    for name in _ENV_TO_CLEAR:
        os.environ.pop(name, None)


if _platform.IS_WINDOWS:
    # Async tests, and the app inside TestClient, run on the same kind of event loop as
    # Easy Chain does on Windows (psycopg's async mode can't use the default one).
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def pytest_terminal_summary(terminalreporter: Any) -> None:
    """On GitHub Actions, show each failure as an annotation on the run's page."""
    if not os.environ.get("GITHUB_ACTIONS"):
        return
    for report in terminalreporter.stats.get("failed", []) + terminalreporter.stats.get(
        "error", []
    ):
        path, line, _ = report.location
        text = str(report.longrepr)[-3000:]
        text = text.replace("%", "%25").replace("\r", "").replace("\n", "%0A")
        title = report.nodeid.replace(",", ";").replace("::", " ")
        terminalreporter.write_line(
            f"::error file=python/{path},line={(line or 0) + 1},title={title}::{text}"
        )


@pytest.fixture(scope="session")
def fake_server() -> Any:
    with FakeOpenAI() as fake:
        yield fake


class _Pages(BaseHTTPRequestHandler):
    """Serves WebServer.pages; /endless sends data with no length until the client stops."""

    def do_GET(self) -> None:
        if self.path == "/endless":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            try:
                for _ in range(10_000):
                    self.wfile.write(b"x" * 65536)
            except OSError:  # the client stopped reading
                pass
            return
        page = self.server.pages.get(self.path)  # type: ignore[attr-defined]
        if page is None:
            self.send_error(404)
            return
        content_type, body = page
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:  # keep test output quiet
        pass


class WebServer:
    """A local web server for fetching tests: put (content type, bytes) in ``pages``."""

    def __init__(self) -> None:
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Pages)
        self.httpd.daemon_threads = True
        self.pages: dict[str, tuple[str, bytes]] = {}
        self.httpd.pages = self.pages  # type: ignore[attr-defined]
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"


@pytest.fixture
def web_server() -> Any:
    server = WebServer()
    thread = threading.Thread(target=server.httpd.serve_forever, daemon=True)
    thread.start()
    yield server
    server.httpd.shutdown()
    server.httpd.server_close()


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
