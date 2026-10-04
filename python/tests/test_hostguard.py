"""Web pages on other sites can't use the local API (DNS rebinding, cross-site requests)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from easychain.server.app import create_app
from easychain.server.hostguard import _host_name, allowed_hosts


@pytest.fixture
def app(tmp_path):
    return create_app(home=tmp_path / "home", static_dir=tmp_path / "no-web", worker=False)


def test_only_known_host_names_are_answered(app):
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/health", headers={"host": "localhost:8000"}).status_code == 200
        # DNS rebinding: the attacker's own name now points at 127.0.0.1.
        refused = client.get("/api/flows", headers={"host": "evil.example:8000"})
        assert refused.status_code == 400
        assert "EASYCHAIN_ALLOWED_HOSTS" in refused.json()["message"]


def test_other_sites_cant_change_things(app):
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        evil = {"origin": "http://evil.example"}
        refused = client.post("/api/knowledge", json={"name": "x"}, headers=evil)
        assert refused.status_code == 403
        assert "evil.example" in refused.json()["message"]
        assert (
            client.put("/api/secrets/OPENAI_API_KEY", json={"value": "x"}, headers=evil).status_code
            == 403
        )
        # The web app itself (served here, or by the dev server on :5173) is fine.
        ok = client.put(
            "/api/secrets/OPENAI_API_KEY",
            json={"value": "sk-x"},
            headers={"origin": "http://localhost:5173"},
        )
        assert ok.status_code == 200
        # Trigger addresses have their own secret token and may be called from anywhere.
        hook = client.post("/api/hooks/nope", json={}, headers=evil)
        assert hook.status_code != 403


def test_more_host_names_can_be_allowed(tmp_path, monkeypatch):
    monkeypatch.setenv("EASYCHAIN_ALLOWED_HOSTS", "easychain.internal, *.example.org")
    monkeypatch.setenv("EASYCHAIN_PUBLIC_URL", "https://flows.example.net")
    hosts = allowed_hosts()
    assert {"easychain.internal", "*.example.org", "flows.example.net"} <= set(hosts)
    app = create_app(home=tmp_path / "home", static_dir=tmp_path / "no-web", worker=False)
    with TestClient(app, base_url="http://easychain.internal") as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/health", headers={"host": "a.example.org"}).status_code == 200
        assert client.get("/api/health", headers={"host": "example.com"}).status_code == 400


def test_host_names_are_read_like_browsers_send_them():
    assert _host_name("localhost:8000") == "localhost"
    assert _host_name("[::1]:8000") == "::1"
    assert _host_name("http://[::1]:5173") == "::1"
    assert _host_name("HTTP://Example.COM") == "example.com"
