"""Which addresses Easy Chain may reach (Phase 5.6f, egress.py): the policy's modes and its
allow-list; Easy Chain's own fetches, redirects and a name that changes address between the
check and the connection (DNS rebinding); Web request steps in runs, Knowledge Base pages,
OpenAPI documents and MCP servers on a server with sign-in on; Settings → Network."""

from __future__ import annotations

import ipaddress
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from easychain import egress
from easychain.egress import EgressBlocked, EgressPolicy, parse_entry, policy_from
from easychain.fetch import FetchError, fetch

from .conftest import input_step, output_step
from .test_api_phase2 import wait
from .test_auth import _app, add_person, bearer, setup_owner, sign_in

ip = ipaddress.ip_address
INTERNAL = [
    "127.0.0.1",
    "10.0.0.1",
    "172.16.5.5",
    "192.168.1.1",
    "169.254.169.254",  # the cloud's metadata service
    "100.64.0.1",
    "0.0.0.0",
    "::1",
    "fd00:ec2::254",
    "fe80::1",
    "::ffff:10.0.0.1",  # IPv4 written as IPv6
]


class _Site(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path.startswith("/redirect"):
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/ok")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = f"ok from {self.headers.get('Host')}".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture(scope="module")
def site() -> Iterator[int]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_port
    server.shutdown()
    server.server_close()


# ── the policy ───────────────────────────────────────────────────────────────


def test_public_reaches_public_addresses_and_listed_internal_ones():
    policy = EgressPolicy("public", ("intranet.example.com", "*.corp.example", "10.1.0.0/16"))
    policy.check("example.com", [ip("93.184.216.34"), ip("2606:2800:220:1::1")])
    for address in INTERNAL:
        with pytest.raises(EgressBlocked, match="internal address"):
            policy.check("example.net", [ip(address)])
    policy.check("intranet.example.com", [ip("10.9.9.9")])  # on the list by name
    policy.check("Wiki.Corp.Example.", [ip("192.168.0.4")])  # by the end of its name
    policy.check("corp.example", [ip("192.168.0.4")])
    policy.check("10.1.2.3", [ip("10.1.2.3")])  # by network
    with pytest.raises(EgressBlocked):
        policy.check("notcorp.example", [ip("192.168.0.4")])
    with pytest.raises(EgressBlocked):
        # One internal address among public ones is enough to refuse.
        policy.check("example.org", [ip("93.184.216.34"), ip("10.0.0.1")])


def test_an_allow_list_reaches_only_what_is_on_it():
    policy = EgressPolicy("allow-list", ("api.example.com", "*.example.org", "203.0.113.7"))
    policy.check("api.example.com", [ip("93.184.216.34")])
    policy.check("docs.example.org", [ip("10.0.0.5")])
    policy.check("203.0.113.7", [ip("203.0.113.7")])
    with pytest.raises(EgressBlocked, match="only reaches the sites on its allow-list"):
        policy.check("example.com", [ip("93.184.216.34")])
    with pytest.raises(EgressBlocked) as blocked:
        policy.check("metadata.internal", [ip("169.254.169.254")])
    assert "metadata.internal (169.254.169.254)" in str(blocked.value)
    assert "Settings → Network" in str(blocked.value)
    EgressPolicy().check("anything", [ip("127.0.0.1")])  # open


def test_allow_list_entries_are_checked():
    assert parse_entry("10.0.0.0/8") == ("network", ipaddress.ip_network("10.0.0.0/8"))
    assert parse_entry(" Docs.Example.com. ") == ("name", "docs.example.com")
    assert parse_entry("*.example.com") == ("suffix", "example.com")
    for bad in ["", "http://x.com", "x.com/path", "a b", "x..com", "host:8080"]:
        with pytest.raises(ValueError):
            parse_entry(bad)
    with pytest.raises(ValueError, match="must be one of"):
        EgressPolicy("closed")


def test_where_the_policy_comes_from(monkeypatch: pytest.MonkeyPatch):
    assert policy_from(None, signed_in=False).to_dict() == {
        "mode": "open",
        "allow": [],
        "source": "default",
    }
    assert policy_from({}, signed_in=True).mode == "public"
    saved = {"mode": "allow-list", "allow": ["api.example.com"]}
    assert policy_from(saved, signed_in=True) == EgressPolicy(
        "allow-list", ("api.example.com",), "settings"
    )
    broken = {"mode": "allow-list", "allow": ["http://nope"]}
    assert policy_from(broken, signed_in=True).mode == "public", "falls back to the safe default"
    monkeypatch.setenv("EASYCHAIN_EGRESS", "allow-list")
    monkeypatch.setenv("EASYCHAIN_EGRESS_ALLOW", "a.example.com, 10.0.0.0/8")
    found = policy_from(saved, signed_in=False)
    assert (found.mode, found.allow, found.source) == (
        "allow-list",
        ("a.example.com", "10.0.0.0/8"),
        "environment",
    )


# ── Easy Chain's own fetches ─────────────────────────────────────────────────


def test_fetches_keep_to_the_policy_redirects_included(site: int):
    url = f"http://127.0.0.1:{site}/ok"
    with egress.using(EgressPolicy("public")), pytest.raises(FetchError, match="127.0.0.1"):
        fetch(url, limit=1000)
    with egress.using(EgressPolicy("public", ("127.0.0.1",))):
        assert fetch(url, limit=1000).content.startswith(b"ok from")
    assert fetch(url, limit=1000).content.startswith(b"ok"), "no policy in force: no check"
    # A listed name that redirects to an address that isn't listed: stopped at the redirect.
    allowed = EgressPolicy("allow-list", ("localhost",))
    with egress.using(allowed), pytest.raises(FetchError, match=r"127\.0\.0\.1"):
        fetch(f"http://localhost:{site}/redirect", limit=1000)


def test_a_name_that_changes_address_after_the_check_is_caught(
    site: int, monkeypatch: pytest.MonkeyPatch
):
    """DNS rebinding: public when the URL is checked, internal when connecting."""
    answers = iter([[ip("93.184.216.34")], [ip("127.0.0.1")]])
    monkeypatch.setattr(egress, "resolve", lambda host, port: next(answers))
    with (
        egress.client(EgressPolicy("public"), trust_env=False) as client,
        pytest.raises(EgressBlocked, match=r"rebind\.test \(127\.0\.0\.1\)"),
    ):
        client.get(f"http://rebind.test:{site}/ok")


def test_a_name_with_several_addresses_tries_each_like_a_plain_connection(
    site: int, monkeypatch: pytest.MonkeyPatch
):
    """``localhost`` is often ::1 first; the server here only answers on 127.0.0.1."""
    monkeypatch.setattr(egress, "resolve", lambda host, port: [ip("::1"), ip("127.0.0.1")])
    policy = EgressPolicy("allow-list", ("several.test",))
    with egress.client(policy, trust_env=False) as client:
        assert client.get(f"http://several.test:{site}/ok").status_code == 200
    # Not listed by name: every address it has is checked, and one internal one refuses.
    monkeypatch.setattr(egress, "resolve", lambda host, port: [ip("93.184.216.34"), ip("10.0.0.1")])
    with (
        egress.client(EgressPolicy("public"), trust_env=False) as client,
        pytest.raises(EgressBlocked, match=r"10\.0\.0\.1"),
    ):
        client.get(f"http://mixed.test:{site}/ok")


def test_connections_go_to_the_address_that_was_checked(site: int, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(egress, "resolve", lambda host, port: [ip("127.0.0.1")])
    policy = EgressPolicy("allow-list", ("pinned.test",))
    with egress.client(policy, trust_env=False) as client:
        response = client.get(f"http://pinned.test:{site}/ok")
    assert response.text == f"ok from pinned.test:{site}", "the name is kept for the site"


async def test_async_clients_keep_to_it_too(site: int):
    async with egress.async_client(EgressPolicy("public"), trust_env=False) as client:
        with pytest.raises(EgressBlocked):
            await client.get(f"http://127.0.0.1:{site}/ok")
    async with egress.async_client(EgressPolicy("public", ("127.0.0.0/8",))) as client:
        assert (await client.get(f"http://127.0.0.1:{site}/ok")).status_code == 200


# ── on a server with sign-in on ──────────────────────────────────────────────


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("EASYCHAIN_AUTH", "on")
    with TestClient(_app(tmp_path)) as test_client:
        setup_owner(test_client)
        yield test_client


def _fetching_flow(url: str) -> dict:
    return {
        "name": "Fetch a page",
        "steps": [
            input_step("q"),
            {
                "id": "fetch",
                "type": "http_request",
                "settings": {"url": url, "response": "text", "save_as": "page"},
            },
            output_step("page"),
        ],
        "connections": [{"from": "input", "to": "fetch"}, {"from": "fetch", "to": "output"}],
    }


def test_a_web_request_step_cant_reach_inside_until_allowed(client: TestClient, site: int):
    assert client.get("/api/settings/network").json()["mode"] == "public"
    flow_id = client.post(
        "/api/flows", json={"spec": _fetching_flow(f"http://127.0.0.1:{site}/ok")}
    ).json()["id"]
    start = {"flow_id": flow_id, "inputs": {"q": "x"}, "background": True}
    run = wait(client, client.post("/api/runs", json=start).json()["run_id"])
    assert run["status"] == "error"
    assert run["error"]["kind"] == "address_not_allowed"
    assert "Easy Chain didn't connect to 127.0.0.1" in run["error"]["message"]
    assert run["error"]["fixes"][0]["params"] == {"tab": "network"}

    saved = client.put("/api/settings/network", json={"mode": "public", "allow": ["127.0.0.1"]})
    assert saved.status_code == 200 and saved.json()["source"] == "settings"
    run = wait(client, client.post("/api/runs", json=start).json()["run_id"])
    assert run["status"] == "ok" and run["output"]["page"].startswith("ok from")
    assert client.get("/api/audit", params={"kind": "settings.network"}).json()["entries"]


def test_knowledge_pages_openapi_documents_and_mcp_servers_too(client: TestClient, site: int):
    inside = f"http://127.0.0.1:{site}/ok"
    preview = client.post("/api/knowledge-preview", data={"url": inside})
    assert preview.status_code == 422 and "didn't connect" in preview.json()["message"]
    spec = client.post("/api/openapi/inspect", json={"source": inside})
    assert spec.status_code == 422 and "didn't connect" in spec.json()["message"]
    mcp = client.post(
        "/api/mcp/tools",
        json={"server": {"id": "inside", "transport": "http", "url": f"{inside}/mcp"}},
    )
    assert mcp.status_code == 403 and "didn't connect" in mcp.json()["message"]

    kb = client.post("/api/knowledge", json={"name": "Docs", "embedding_model": "keywords"}).json()
    [doc] = client.post(f"/api/knowledge/{kb['id']}/urls", json={"urls": [inside]}).json()
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        docs = client.get(f"/api/knowledge/{kb['id']}").json()["documents"]
        found = next(d for d in docs if d["id"] == doc["id"])
        if found["status"] in ("ready", "error"):
            break
        time.sleep(0.1)
    assert found["status"] == "error" and "didn't connect" in found["error"]


def test_who_may_see_and_change_it(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    add_person(client, "ed@example.com", "editor")
    add_person(client, "vi@example.com", "viewer")
    keys = {}
    for email in ("ed@example.com", "vi@example.com"):
        assert sign_in(client, email) == 200
        keys[email] = client.post("/api/keys", json={}).json()["key"]
    assert sign_in(client, "owner@example.com") == 200
    editor, viewer = bearer(keys["ed@example.com"]), bearer(keys["vi@example.com"])
    assert client.get("/api/settings/network", headers=editor).status_code == 200
    change = {"mode": "allow-list", "allow": ["api.example.com"]}
    assert client.put("/api/settings/network", json=change, headers=editor).status_code == 403
    assert client.get("/api/settings/network", headers=viewer).status_code == 403
    bad = client.put("/api/settings/network", json={"mode": "public", "allow": ["http://x"]})
    assert bad.status_code == 422 and "isn't a host name" in bad.json()["message"]
    assert client.put("/api/settings/network", json=change).json()["allow"] == ["api.example.com"]

    monkeypatch.setenv("EASYCHAIN_EGRESS", "open")
    shown = client.get("/api/settings/network").json()
    assert (shown["mode"], shown["source"]) == ("open", "environment")
    refused = client.put("/api/settings/network", json=change)
    assert refused.status_code == 409 and "EASYCHAIN_EGRESS" in refused.json()["message"]


def test_on_this_computer_nothing_changes(tmp_path: Path, site: int):
    with TestClient(_app(tmp_path)) as client:
        assert client.get("/api/settings/network").json()["mode"] == "open"
        flow_id = client.post(
            "/api/flows", json={"spec": _fetching_flow(f"http://127.0.0.1:{site}/ok")}
        ).json()["id"]
        start = {"flow_id": flow_id, "inputs": {"q": "x"}, "background": True}
        assert wait(client, client.post("/api/runs", json=start).json()["run_id"])["status"] == "ok"
