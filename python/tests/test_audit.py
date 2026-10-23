"""The audit log (Phase 5.6c, server/audit.py): every change is recorded with who did it, or
says why it isn't; failures and secret values never are; only Owners read it; nothing in it
can be changed or removed, on SQLite and on Postgres; the command line's changes are in it too."""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from easychain.server import audit
from easychain.server.db import Database

from . import pg
from .test_api_phase2 import wait
from .test_auth import FLOW, PASSWORD, _app, add_person, bearer, setup_owner, sign_in


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("EASYCHAIN_AUTH", "on")
    with TestClient(_app(tmp_path)) as test_client:
        yield test_client


def entries(client: TestClient, headers: dict | None = None, **filters) -> list[dict]:
    response = client.get("/api/audit", params=filters, headers=headers or {})
    assert response.status_code == 200, response.text
    return response.json()["entries"]


def key_for(client: TestClient, email: str, role: str) -> str:
    add_person(client, email, role)
    assert sign_in(client, email) == 200
    key = client.post("/api/keys", json={"name": f"{role} key"}).json()["key"]
    assert sign_in(client, "owner@example.com") == 200
    return key


# ── nothing is missed ────────────────────────────────────────────────────────


def test_every_change_is_recorded_or_says_why_not(tmp_path: Path):
    """A new route that changes something must be added to AUDITED (or NOT_AUDITED, with the
    reason) in server/audit.py; this test fails until it is."""
    changing = set()
    for app in (_app(tmp_path / "a"), _app(tmp_path / "b", web=True)):
        for route in app.routes:
            if isinstance(route, APIRoute):
                changing |= {(m, route.path) for m in route.methods if m not in ("GET", "HEAD")}
    known = set(audit.AUDITED) | set(audit.NOT_AUDITED)
    assert not sorted(changing - known), "add these to AUDITED or NOT_AUDITED in server/audit.py"
    assert not sorted(known - changing), "these entries have no route any more"
    assert not set(audit.AUDITED) & set(audit.NOT_AUDITED)
    assert set(audit.AUDITED.values()) <= set(audit.LABELS)
    assert all(any(a.startswith(k) for k in audit.KINDS) for a in audit.LABELS)


# ── who did what ─────────────────────────────────────────────────────────────


def test_who_did_what_and_nothing_that_failed(client: TestClient):
    owner = setup_owner(client)
    editor_key = key_for(client, "ed@example.com", "editor")
    viewer_key = key_for(client, "vi@example.com", "viewer")

    made = client.post("/api/flows", json={"spec": FLOW}, headers=bearer(editor_key)).json()
    flow_id = made["id"]
    # The editor saves as you type: one entry for a burst of saves.
    for name in ("Echo 2", "Echo 3", "Echo 4"):
        spec = {**FLOW, "name": name}
        saved = client.put(f"/api/flows/{flow_id}", json=spec, headers=bearer(editor_key))
        assert saved.status_code == 200, saved.text
    secret = "sk-very-secret-value-123"
    assert client.put("/api/secrets/SOME_KEY", json={"value": secret}).status_code == 200
    run = client.post(
        "/api/runs",
        json={"flow_id": flow_id, "inputs": {"text": "hi"}, "background": True},
        headers=bearer(editor_key),
    ).json()
    wait(client, run["run_id"])
    # Refused or invalid: not changes.
    assert (
        client.post("/api/flows", json={"spec": FLOW}, headers=bearer(viewer_key)).status_code
        == 403
    )
    broken = {**FLOW, "steps": "not a list"}
    assert (
        client.put(f"/api/flows/{flow_id}", json=broken, headers=bearer(editor_key)).status_code
        == 422
    )
    assert client.post("/api/flows", json={"yaml": "not: [a flow"}).status_code == 422

    log = entries(client)
    mine = [(e["actor"], e["action"], e["target"]) for e in reversed(log)]
    assert mine == [
        ("Olive", "auth.setup", "owner@example.com"),
        ("Olive", "person.add", "ed@example.com"),
        ("Editor", "auth.sign_in", "ed@example.com"),
        ("Editor", "key.create", "editor key"),
        ("Olive", "auth.sign_in", "owner@example.com"),
        ("Olive", "person.add", "vi@example.com"),
        ("Viewer", "auth.sign_in", "vi@example.com"),
        ("Viewer", "key.create", "viewer key"),
        ("Olive", "auth.sign_in", "owner@example.com"),
        ("Editor", "flow.create", flow_id),
        ("Editor", "flow.save", flow_id),
        ("Olive", "secret.set", "SOME_KEY"),
        ("Editor", "run.start", flow_id),
    ]
    by_action = {e["action"]: e for e in reversed(log)}  # the newest of each
    assert by_action["run.start"]["detail"]["run"] == run["run_id"]
    assert by_action["flow.create"]["via"] == "key" and by_action["secret.set"]["via"] == "session"
    assert by_action["person.add"]["detail"] == {"role": "viewer"}
    assert by_action["key.create"]["detail"]["role"] == "viewer"
    assert by_action["flow.create"]["actor_id"] != owner["id"]
    assert all(e["address"] == "testclient" for e in log)
    assert secret not in json.dumps(log), "secret values are never recorded"
    assert by_action["run.start"]["text"] == f"Editor started a run of {flow_id}"

    # Filters: by kind, person and target; pages with `next`.
    assert {e["action"] for e in entries(client, kind="flow.")} == {"flow.create", "flow.save"}
    assert [e["action"] for e in entries(client, kind="secret.set")] == ["secret.set"]
    editor_id = by_action["flow.create"]["actor_id"]
    assert {e["actor"] for e in entries(client, person=editor_id)} == {"Editor"}
    assert {e["action"] for e in entries(client, target=flow_id)} == {
        "flow.create",
        "flow.save",
        "run.start",
    }
    page = client.get("/api/audit", params={"limit": 5}).json()
    rest = client.get("/api/audit", params={"limit": 100, "before": page["next"]}).json()
    assert [e["id"] for e in page["entries"] + rest["entries"]] == [e["id"] for e in log]
    assert rest["next"] is None
    assert {"id": "flow.", "label": "Flows"} in page["kinds"]


def test_signing_in_and_out_and_failing_to(client: TestClient):
    setup_owner(client)
    add_person(client, "ed@example.com", "editor")
    client.cookies.clear()
    assert sign_in(client, "ed@example.com", "not the password") == 401
    assert sign_in(client, "nobody@example.com", "whatever pass") == 401
    wrong = client.post(
        "/api/auth/setup",
        json={"code": "AAAA-AAAA-AAAA", "email": "x@example.com", "password": PASSWORD},
    )
    assert wrong.status_code == 403
    assert sign_in(client, "owner@example.com") == 200
    assert client.post("/api/auth/logout").status_code == 200
    assert client.post("/api/auth/logout").status_code == 200, "not signed in: nothing to record"
    assert sign_in(client, "owner@example.com") == 200
    log = [(e["actor"], e["action"], e["target"]) for e in reversed(entries(client))]
    assert log[-6:] == [
        ("someone not signed in", "auth.sign_in_failed", "ed@example.com"),
        ("someone not signed in", "auth.sign_in_failed", "nobody@example.com"),
        ("someone not signed in", "auth.setup_failed", "x@example.com"),
        ("Olive", "auth.sign_in", "owner@example.com"),
        ("Olive", "auth.sign_out", None),
        ("Olive", "auth.sign_in", "owner@example.com"),
    ]
    # A browser already signed in that fails to sign in as someone else is named.
    assert sign_in(client, "ed@example.com", "not the password") == 401
    assert entries(client, kind="auth.sign_in_failed")[0]["actor"] == "Olive"


def test_people_changes_name_the_person(client: TestClient):
    setup_owner(client)
    ed = add_person(client, "ed@example.com", "editor")
    client.patch(f"/api/people/{ed['id']}", json={"role": "runner", "password": "a new password"})
    client.delete(f"/api/people/{ed['id']}")
    changes = [e for e in reversed(entries(client, kind="person.")) if e["target"] == ed["email"]]
    assert [(e["action"], e["detail"]) for e in changes] == [
        ("person.add", {"role": "editor"}),
        ("person.change", {"user_id": ed["id"], "role": "runner", "password": "set"}),
        ("person.remove", {"user_id": ed["id"]}),
    ]
    assert "a new password" not in json.dumps(changes)
    assert changes[1]["text"] == "Olive changed ed@example.com"


def test_only_owners_read_the_log(client: TestClient):
    setup_owner(client)
    key = key_for(client, "ed@example.com", "editor")
    refused = client.get("/api/audit", headers=bearer(key))
    assert refused.status_code == 403 and "needs Owner" in refused.json()["message"]
    client.cookies.clear()
    assert client.get("/api/audit").status_code == 401


def test_a_webhook_run_is_recorded_as_the_webhook(client: TestClient):
    setup_owner(client)
    flow_id = client.post("/api/flows", json={"spec": FLOW}).json()["id"]
    trig = client.post(
        "/api/triggers",
        json={"flow_id": flow_id, "kind": "webhook", "config": {"inputs": {"text": "{text}"}}},
    ).json()
    fired = client.post(
        trig["url"], json={"text": "hi"}, headers={"X-Easychain-Token": trig["token"]}
    )
    assert fired.status_code == 202
    wait(client, fired.json()["run_id"])
    log = {e["action"]: e for e in entries(client)}
    assert log["trigger.create"]["target"] == flow_id
    assert log["trigger.create"]["detail"]["kind"] == "webhook"
    hook = log["trigger.fired"]
    assert (hook["actor"], hook["via"], hook["target"]) == ("a webhook", "trigger", trig["id"])
    assert hook["detail"]["run"] == fired.json()["run_id"]
    assert trig["token"] not in json.dumps(list(log.values())), "the hook's token isn't kept"


def test_with_sign_in_off_changes_are_the_local_users(tmp_path: Path):
    with TestClient(_app(tmp_path)) as client:
        flow_id = client.post("/api/flows", json={"spec": FLOW}).json()["id"]
        [entry] = entries(client)
        assert (entry["actor"], entry["via"], entry["action"], entry["target"]) == (
            "the local user (sign-in off)",
            "local",
            "flow.create",
            flow_id,
        )


def test_a_problem_writing_the_log_doesnt_undo_the_change(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    setup_owner(client)

    async def broken(**_: object) -> int:
        raise RuntimeError("disk full")

    monkeypatch.setattr(client.app.state.hub.db, "add_audit", broken)
    assert client.post("/api/flows", json={"spec": FLOW}).status_code == 201
    assert "Couldn't write the audit log entry" in caplog.text


# ── append-only ──────────────────────────────────────────────────────────────


async def _refuses_changes(url: str) -> None:
    db = await Database.connect(url)
    try:
        await db.add_audit(workspace_id="default", actor="x", via="cli", action="flow.save")
        for statement in (
            "UPDATE audit_log SET actor = 'someone else'",
            "DELETE FROM audit_log",
        ):
            with pytest.raises(sa.exc.DBAPIError, match="append-only"):
                async with db.engine.begin() as conn:
                    await conn.execute(sa.text(statement))
        if url.startswith("postgresql"):
            with pytest.raises(sa.exc.DBAPIError, match="append-only"):
                async with db.engine.begin() as conn:
                    await conn.execute(sa.text("TRUNCATE audit_log"))
        assert [e["actor"] for e in await db.list_audit()] == ["x"]
    finally:
        await db.close()


async def test_nothing_in_the_log_can_be_changed_or_removed(tmp_path: Path):
    await _refuses_changes(f"sqlite:///{tmp_path / 'audit.db'}")


async def test_nothing_in_the_log_can_be_changed_or_removed_on_postgres():
    if not pg.available():
        pytest.skip("Postgres isn't available here")
    with pg.server() as base:
        await _refuses_changes(pg.new_database(base))


# ── the command line ─────────────────────────────────────────────────────────


def test_the_command_line_records_its_changes_and_shows_the_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    from easychain.cli import main

    home = ["--home", str(tmp_path / "home")]
    monkeypatch.setattr("sys.stdin", io.StringIO(f"{PASSWORD}\n"))
    assert (
        main(["users", "add", "ann@example.com", "--role", "owner", "--password-stdin", *home]) == 0
    )
    assert main(["keys", "create", "ann@example.com", "--name", "ci", *home]) == 0
    capsys.readouterr()

    assert main(["audit", *home]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert [line.split("  ", 1)[1] for line in lines] == [
        "the command line added ann@example.com",
        "the command line made a key for scripts ci",
    ]
    assert main(["audit", "--kind", "key.", "--json", *home]) == 0
    [entry] = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert entry["via"] == "cli" and entry["detail"]["person"] == "ann@example.com"
    assert main(["audit", "--since", "2999-01-01", *home]) == 0
    assert "Nothing in the audit log matches" in capsys.readouterr().out
    assert main(["audit", "--person", "nobody@example.com", *home]) == 1
