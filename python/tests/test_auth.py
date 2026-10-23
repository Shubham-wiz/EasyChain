"""Sign-in, people, roles and API keys (Phase 5.6, server/auth.py): every route says who may use
it, the first Owner needs the code from the server's log, each role gets what it should and no
more, and the guards hold (the last Owner, lock-out, revoked keys, signed-out sessions)."""

from __future__ import annotations

import io
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.routing import APIRoute, APIWebSocketRoute
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from easychain.server import auth
from easychain.server.app import create_app

from .conftest import input_step, output_step
from .test_api_phase2 import APPROVAL, code, wait

PASSWORD = "correct horse battery"
FLOW = {
    "name": "Echo",
    "steps": [
        input_step("text"),
        code("shout", "    return {'loud': data['text'].upper()}"),
        output_step("loud"),
    ],
    "connections": [{"from": "input", "to": "shout"}, {"from": "shout", "to": "output"}],
}


def _app(tmp_path: Path, web: bool = False):
    static = tmp_path / ("web" if web else "no-web")
    if web:
        static.mkdir(parents=True, exist_ok=True)
        (static / "index.html").write_text("<title>Easy Chain</title>", encoding="utf-8")
    return create_app(
        home=tmp_path / "home",
        workspace=tmp_path / "flows",
        static_dir=static,
        worker_options={"poll": 0.05, "schedules": False},
    )


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("EASYCHAIN_AUTH", "on")
    with TestClient(_app(tmp_path)) as test_client:
        yield test_client


class Browser:
    """Another browser on the same server: its own cookies, through the same TestClient (two
    TestClients would start the app twice)."""

    def __init__(self, client: TestClient):
        self.client, self.jar = client, type(client.cookies)()

    def request(self, method: str, url: str, **kwargs) -> Any:
        mine, self.client.cookies = self.client.cookies, self.jar
        try:
            return self.client.request(method, url, **kwargs)
        finally:
            self.jar, self.client.cookies = self.client.cookies, mine

    def get(self, url: str, **kwargs) -> Any:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> Any:
        return self.request("POST", url, **kwargs)


def setup_owner(client: TestClient, email: str = "owner@example.com") -> dict:
    code = client.portal.call(client.app.state.hub.accounts.setup_code)
    response = client.post(
        "/api/auth/setup",
        json={"code": code, "email": email, "name": "Olive", "password": PASSWORD},
    )
    assert response.status_code == 200, response.text
    return response.json()["me"]


def add_person(client: TestClient, email: str, role: str) -> dict:
    response = client.post(
        "/api/people",
        json={"email": email, "name": role.title(), "role": role, "password": PASSWORD},
    )
    assert response.status_code == 201, response.text
    return response.json()


def sign_in(client: TestClient | Browser, email: str, password: str = PASSWORD) -> int:
    return client.post("/api/auth/login", json={"email": email, "password": password}).status_code


def bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


# ── every route is declared ──────────────────────────────────────────────────


def _declared(app) -> set[tuple[str, str]]:
    found = set()
    for route in app.routes:
        if isinstance(route, APIWebSocketRoute):
            found.add(("WS", route.path))
        elif isinstance(route, APIRoute):
            found |= {(method, route.path) for method in route.methods}
    return found


def test_every_route_says_who_may_use_it(tmp_path: Path):
    """A new endpoint must be added to PERMISSIONS (server/auth.py); until it is, it is closed
    to everyone, and this test fails."""
    routes = _declared(_app(tmp_path / "a")) | _declared(_app(tmp_path / "b", web=True))
    missing = sorted(routes - set(auth.PERMISSIONS))
    assert not missing, f"add these to PERMISSIONS in server/auth.py: {missing}"
    stale = sorted(set(auth.PERMISSIONS) - routes)
    assert not stale, f"these PERMISSIONS entries have no route any more: {stale}"
    assert set(auth.PERMISSIONS.values()) <= {auth.PUBLIC, auth.SIGNED_IN, *auth.ROLES}


def test_sign_in_is_off_on_this_computer_and_on_for_the_network(monkeypatch):
    assert auth.auth_mode() == "off"  # EASYCHAIN_HOST unset: easychain dev, 127.0.0.1
    for host, mode in [("127.0.0.1", "off"), ("localhost", "off"), ("0.0.0.0", "on")]:
        monkeypatch.setenv("EASYCHAIN_HOST", host)
        assert auth.auth_mode() == mode
    monkeypatch.setenv("EASYCHAIN_AUTH", "off")
    assert auth.auth_mode() == "off", "said out loud, it wins"
    monkeypatch.setenv("EASYCHAIN_HOST", "127.0.0.1")
    monkeypatch.setenv("EASYCHAIN_AUTH", "ON")
    assert auth.auth_mode() == "on"


def test_passwords_keys_and_codes_are_kept_only_as_hashes():
    stored = auth.hash_password(PASSWORD)
    assert PASSWORD not in stored and stored.startswith("scrypt$")
    assert auth.verify_password(PASSWORD, stored)
    assert not auth.verify_password("wrong password!", stored)
    assert not auth.verify_password(PASSWORD, None)
    assert auth.password_problem("short") and auth.password_problem("aaaaaaaaaaaa")
    assert auth.password_problem(PASSWORD) is None
    key_id, secret, key = auth.new_api_key()
    assert key == f"eck_{key_id}_{secret}" and auth.split_api_key(key) == (key_id, secret)
    assert auth.split_api_key("eck_nothing") is None and auth.split_api_key("sk-123") is None


# ── sign-in off ──────────────────────────────────────────────────────────────


def test_with_sign_in_off_everyone_is_the_owner_as_before(tmp_path: Path):
    with TestClient(_app(tmp_path)) as client:
        status = client.get("/api/auth/status").json()
        assert status["mode"] == "off" and status["signed_in"] is True
        assert status["me"]["role"] == "owner" and status["me"]["via"] == "local"
        assert client.post("/api/flows", json={"spec": FLOW}).status_code == 201
        assert client.put("/api/secrets/SOME_KEY", json={"value": "x"}).status_code == 200
        assert sign_in(client, "a@example.com") == 409
        assert client.get("/api/keys").json()["keys"] == []


# ── the first account ────────────────────────────────────────────────────────


def test_the_first_owner_needs_the_code_from_the_servers_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    monkeypatch.setenv("EASYCHAIN_AUTH", "on")
    with TestClient(_app(tmp_path)) as client:
        log = capsys.readouterr().out
        code = re.search(r"enter the code ([A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4})", log)
        assert code, log
        status = client.get("/api/auth/status").json()
        assert status == {**status, "mode": "on", "signed_in": False, "setup_needed": True}

        # Nothing but sign-in is open until then.
        response = client.get("/api/flows")
        assert response.status_code == 401
        assert response.json()["action"] == "sign_in"
        assert client.get("/api/health").status_code == 200

        person = {"email": "Owner@Example.com", "name": "Olive", "password": PASSWORD}
        wrong = client.post("/api/auth/setup", json={**person, "code": "AAAA-AAAA-AAAA"})
        assert wrong.status_code == 403 and "log" in wrong.json()["message"]
        weak = client.post("/api/auth/setup", json={**person, "code": code[1], "password": "abc"})
        assert weak.status_code == 422

        made = client.post("/api/auth/setup", json={**person, "code": code[1].lower()})
        assert made.status_code == 200, made.text
        assert made.json()["me"]["role"] == "owner"
        assert made.json()["me"]["email"] == "owner@example.com"
        cookie = made.headers["set-cookie"]
        assert "easychain_session=" in cookie and "HttpOnly" in cookie
        assert "samesite=lax" in cookie.lower()

        # Signed in by the cookie straight away; the code is used up.
        me = client.get("/api/auth/me").json()
        assert me["role"] == "owner" and me["via"] == "session"
        assert client.get("/api/flows").status_code == 200
        again = client.post("/api/auth/setup", json={**person, "code": code[1]})
        assert again.status_code == 403
        assert client.get("/api/auth/status").json()["setup_needed"] is False
        assert client.portal.call(client.app.state.hub.accounts.setup_code) is None


def test_guessing_the_code_is_stopped(client: TestClient):
    person = {"email": "x@example.com", "password": PASSWORD}
    for _ in range(30):
        client.post("/api/auth/setup", json={**person, "code": "AAAA-AAAA-AAAA"})
    code = client.portal.call(client.app.state.hub.accounts.setup_code)
    assert client.post("/api/auth/setup", json={**person, "code": code}).status_code == 429


# ── roles ────────────────────────────────────────────────────────────────────


def test_each_role_gets_what_it_should_and_no_more(client: TestClient):
    setup_owner(client)
    flow_id = client.post("/api/flows", json={"spec": FLOW}).json()["id"]
    keys = {}
    for role in auth.ROLES[:-1]:
        add_person(client, f"{role}@example.com", role)
    for role in auth.ROLES:
        assert sign_in(client, f"{role}@example.com") == 200
        made = client.post("/api/keys", json={"name": f"{role} key"})
        assert made.status_code == 201, made.text
        assert made.json()["key"].startswith("eck_") and made.json()["role"] == role
        keys[role] = made.json()["key"]
    client.cookies.clear()

    run_saved = {"flow_id": flow_id, "inputs": {"text": "hi"}, "background": True}
    run_given = {"spec": FLOW, "inputs": {"text": "hi"}, "background": True}
    asks = [
        # (method, path, body, the least role that may)
        ("GET", "/api/flows", None, "viewer"),
        ("GET", f"/api/flows/{flow_id}", None, "viewer"),
        ("GET", "/api/runs", None, "viewer"),
        ("GET", "/api/inbox", None, "viewer"),
        ("GET", "/api/knowledge", None, "viewer"),
        ("POST", "/api/runs", run_saved, "runner"),
        ("POST", "/api/runs", run_given, "editor"),
        ("POST", "/api/flows", {"spec": FLOW}, "editor"),
        ("POST", "/api/knowledge", {"name": "Notes"}, "editor"),
        ("GET", "/api/secrets", None, "editor"),
        ("PUT", "/api/secrets/SOME_KEY", {"value": "x"}, "owner"),
        ("GET", "/api/people", None, "owner"),
    ]
    for method, path, body, least in asks:
        for role in auth.ROLES:
            response = client.request(method, path, json=body, headers=bearer(keys[role]))
            if auth.rank(role) >= auth.rank(least):
                assert response.status_code < 400, (role, method, path, response.text)
            else:
                assert response.status_code == 403, (role, method, path, response.text)
                detail = response.json()
                assert detail["action"] == "role" and "Owner can change" in detail["message"]

    nobody = client.get("/api/flows", headers=bearer("eck_nope_nope"))
    assert nobody.status_code == 401


def test_the_inbox_records_who_answered(client: TestClient):
    setup_owner(client)
    flow_id = client.post("/api/flows", json={"spec": APPROVAL}).json()["id"]
    run_id = client.post(
        "/api/runs", json={"flow_id": flow_id, "inputs": {"draft": "x"}, "background": True}
    ).json()["run_id"]
    wait(client, run_id, "paused")
    item = client.get("/api/inbox").json()[0]
    answered = client.post(
        f"/api/inbox/{item['id']}/answer", json={"action": "approve", "by": "someone else"}
    )
    assert answered.status_code == 200, answered.text
    assert client.get(f"/api/inbox/{item['id']}").json()["answered_by"] == "Olive"


# ── people ───────────────────────────────────────────────────────────────────


def test_the_last_owner_cant_be_demoted_removed_or_switched_off(client: TestClient):
    owner = setup_owner(client)
    for change in ({"role": "editor"}, {"disabled": True}):
        response = client.patch(f"/api/people/{owner['id']}", json=change)
        assert response.status_code == 409 and "only Owner" in response.json()["message"]
    assert client.delete(f"/api/people/{owner['id']}").status_code == 409

    second = add_person(client, "second@example.com", "owner")
    assert client.patch(f"/api/people/{owner['id']}", json={"role": "editor"}).status_code == 200
    # Now an Editor, the first person can't manage people any more.
    assert client.get("/api/people").status_code == 403
    assert sign_in(client, "second@example.com") == 200
    people = client.get("/api/people").json()["people"]
    assert [(p["email"], p["role"]) for p in people] == [
        ("owner@example.com", "editor"),
        ("second@example.com", "owner"),
    ]
    assert people[0]["role_label"] == "Editor" and "password_hash" not in people[0]
    assert (
        client.post(
            "/api/people",
            json={"email": "Second@example.com", "role": "viewer", "password": PASSWORD},
        ).status_code
        == 409
    ), "one account per email"
    bad_role = {"email": "new@example.com", "role": "admin", "password": PASSWORD}
    assert client.post("/api/people", json=bad_role).status_code == 409
    assert client.delete(f"/api/people/{second['id']}").status_code == 409


def test_switching_someone_off_signs_them_out_at_once(client: TestClient):
    owner, other = client, Browser(client)
    setup_owner(owner)
    person = add_person(owner, "ed@example.com", "editor")
    assert sign_in(other, "ed@example.com") == 200
    key = other.post("/api/keys", json={"name": "script"}).json()["key"]
    assert other.get("/api/flows").status_code == 200

    assert owner.patch(f"/api/people/{person['id']}", json={"disabled": True}).status_code == 200
    assert other.get("/api/flows").status_code == 401
    assert other.get("/api/flows", headers=bearer(key)).status_code == 401
    assert sign_in(other, "ed@example.com") == 401

    owner.patch(f"/api/people/{person['id']}", json={"disabled": False})
    assert other.get("/api/flows", headers=bearer(key)).status_code == 200, "keys come back"
    assert owner.delete(f"/api/people/{person['id']}").status_code == 200
    assert other.get("/api/flows", headers=bearer(key)).status_code == 401, "keys go with them"


def test_a_new_password_signs_out_the_other_browsers(client: TestClient):
    here, there = client, Browser(client)
    setup_owner(here)
    assert sign_in(there, "owner@example.com") == 200
    wrong = here.post("/api/auth/password", json={"current": "nope", "new": "a new password"})
    assert wrong.status_code == 403
    weak = here.post("/api/auth/password", json={"current": PASSWORD, "new": "short"})
    assert weak.status_code == 422
    changed = here.post("/api/auth/password", json={"current": PASSWORD, "new": "a new password"})
    assert changed.status_code == 200
    assert here.get("/api/flows").status_code == 200, "this browser stays signed in"
    assert there.get("/api/flows").status_code == 401
    assert sign_in(there, "owner@example.com") == 401
    assert sign_in(there, "owner@example.com", "a new password") == 200

    assert here.post("/api/auth/logout").status_code == 200
    assert here.get("/api/flows").status_code == 401


def test_wrong_passwords_lock_sign_in_for_a_while(client: TestClient):
    setup_owner(client)
    add_person(client, "ed@example.com", "editor")
    client.cookies.clear()
    for _ in range(5):
        assert sign_in(client, "ed@example.com", "not the password") == 401
    assert sign_in(client, "ed@example.com") == 429, "even the right password waits"
    assert sign_in(client, "owner@example.com") == 200, "other people can still sign in"
    assert sign_in(client, "nobody@example.com", "whatever pw") == 401


# ── API keys ─────────────────────────────────────────────────────────────────


def test_keys_are_capped_shown_once_and_revocable(client: TestClient):
    setup_owner(client)
    add_person(client, "ed@example.com", "editor")
    made = client.post("/api/keys", json={"name": "read only", "role": "viewer"}).json()
    key = made["key"]
    listed = client.get("/api/keys").json()["keys"]
    assert [k["name"] for k in listed] == ["read only"] and "key" not in listed[0]
    assert listed[0]["mine"] is True and listed[0]["role"] == "viewer"

    # A viewer key of an Owner is a viewer.
    assert client.get("/api/flows", headers=bearer(key)).status_code == 200
    assert client.post("/api/flows", json={"spec": FLOW}, headers=bearer(key)).status_code == 403
    me = client.get("/api/auth/me", headers=bearer(key)).json()
    assert me["role"] == "viewer" and me["via"] == "key"
    # A key can't make keys, or have more than its person.
    assert client.post("/api/keys", json={}, headers=bearer(key)).status_code == 403
    assert sign_in(client, "ed@example.com") == 200
    assert client.post("/api/keys", json={"role": "owner"}).status_code == 403
    assert client.post("/api/keys", json={"role": "boss"}).status_code == 422
    eds = client.post("/api/keys", json={"name": "ed's"}).json()
    assert [k["name"] for k in client.get("/api/keys").json()["keys"]] == ["ed's"]
    assert client.delete(f"/api/keys/{made['id']}").status_code == 404, "not Ed's key"

    # A wrong secret for a real key id is refused; revoking stops the key.
    forged = f"eck_{made['id']}_wrong"
    assert client.get("/api/flows", headers=bearer(forged)).status_code == 401
    assert sign_in(client, "owner@example.com") == 200
    assert {k["name"] for k in client.get("/api/keys").json()["keys"]} == {"read only", "ed's"}
    assert client.delete(f"/api/keys/{eds['id']}").status_code == 200, "an Owner can revoke any"
    assert client.delete(f"/api/keys/{made['id']}").status_code == 200
    assert client.get("/api/flows", headers=bearer(key)).status_code == 401


# ── what stays open ──────────────────────────────────────────────────────────


def test_live_run_streams_need_sign_in(client: TestClient):
    setup_owner(client)
    flow_id = client.post("/api/flows", json={"spec": FLOW}).json()["id"]
    run_id = client.post(
        "/api/runs", json={"flow_id": flow_id, "inputs": {"text": "hi"}, "background": True}
    ).json()["run_id"]
    with client.websocket_connect(f"/api/runs/{run_id}/ws") as ws:
        while (event := ws.receive_json())["type"] != "run_finished":
            pass
        assert event["status"] == "ok"
    client.cookies.clear()
    with (
        pytest.raises(WebSocketDisconnect) as refused,
        client.websocket_connect(f"/api/runs/{run_id}/ws") as ws,
    ):
        ws.receive_json()
    assert refused.value.code == 1008


def test_webhooks_need_their_token_not_a_sign_in(client: TestClient):
    setup_owner(client)
    flow_id = client.post("/api/flows", json={"spec": FLOW}).json()["id"]
    trig = client.post(
        "/api/triggers",
        json={"flow_id": flow_id, "kind": "webhook", "config": {"inputs": {"text": "{text}"}}},
    ).json()
    client.cookies.clear()
    assert client.get("/api/flows").status_code == 401
    hook = client.post(
        trig["url"], json={"text": "hi"}, headers={"X-Easychain-Token": trig["token"]}
    )
    assert hook.status_code == 202, hook.text
    assert client.post(trig["url"], json={"text": "hi"}).status_code == 404
    assert sign_in(client, "owner@example.com") == 200
    assert wait(client, hook.json()["run_id"])["output"] == {"loud": "HI"}


# ── the command line ─────────────────────────────────────────────────────────


def test_people_and_keys_from_the_command_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    """For servers set up by a script (and the Compose smoke test): the first Owner and a key
    without opening the app."""
    from easychain.cli import main

    home = ["--home", str(tmp_path / "home")]
    assert main(["users", "setup-code", *home]) == 0
    assert re.fullmatch(r"[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}\n", capsys.readouterr().out)
    monkeypatch.setattr("sys.stdin", io.StringIO(f"{PASSWORD}\n"))
    assert (
        main(["users", "add", "Ann@example.com", "--role", "owner", "--password-stdin", *home]) == 0
    )
    assert (
        main(["keys", "create", "ann@example.com", "--role", "runner", "--name", "ci", *home]) == 0
    )
    key = capsys.readouterr().out.strip().splitlines()[-1]
    assert key.startswith("eck_")
    assert main(["users", "role", "ann@example.com", "--role", "viewer", *home]) == 1
    assert "only Owner" in capsys.readouterr().err
    monkeypatch.setattr("sys.stdin", io.StringIO("short\n"))
    assert (
        main(["users", "add", "b@example.com", "--role", "viewer", "--password-stdin", *home]) == 1
    )
    assert main(["users", "list", *home]) == 0
    assert capsys.readouterr().out.split() == ["ann@example.com", "Owner"]
    assert main(["users", "setup-code", *home]) == 1, "only before the first account"

    monkeypatch.setenv("EASYCHAIN_AUTH", "on")
    with TestClient(_app(tmp_path)) as client:
        assert client.get("/api/auth/status").json()["setup_needed"] is False
        assert client.get("/api/flows", headers=bearer(key)).status_code == 200
        made = client.post("/api/flows", json={"spec": FLOW}, headers=bearer(key))
        assert made.status_code == 403
        assert sign_in(client, "ann@example.com") == 200
    key_id = auth.split_api_key(key)[0]
    assert main(["keys", "revoke", key_id, *home]) == 0
    assert main(["keys", "revoke", key_id, *home]) == 1
    assert main(["users", "remove", "ann@example.com", *home]) == 1, "the only Owner"
