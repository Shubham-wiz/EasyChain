"""Workspaces (Phase 5.6d, docs/workspaces.md): each keeps its own flows, runs, Inbox, triggers,
Knowledge Bases, skills, settings, secrets and long-term memory, and its own people with their
roles; what every workspace shares (the sandbox, the network, single sign-on) is the server
Owners' to change."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from langgraph.store.base import InvalidNamespaceError
from langgraph.store.memory import InMemoryStore

from easychain import current_workspace
from easychain.knowledge.store import KnowledgeNotHere, KnowledgeStore, in_this_workspace
from easychain.runtime.workspace_store import WorkspaceStore
from easychain.server.app import create_app

from .conftest import input_step, output_step
from .test_api_phase2 import APPROVAL, code
from .test_auth import PASSWORD, Browser, bearer, setup_owner, sign_in

B = {"X-EasyChain-Workspace": "team-b"}

TOKEN_LENGTH = {
    "name": "Token length",
    "steps": [
        input_step("text"),
        code(
            "measure",
            "    import os\n    return {'length': len(os.environ.get('TEAM_TOKEN', ''))}",
        ),
        output_step("length"),
    ],
    "connections": [{"from": "input", "to": "measure"}, {"from": "measure", "to": "output"}],
}


def _app(tmp_path: Path):
    return create_app(
        home=tmp_path / "home",
        workspace=tmp_path / "flows",
        static_dir=tmp_path / "no-web",
        worker_options={"poll": 0.05, "schedules": False},
    )


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    """Sign-in off: the one person is the Owner of every workspace."""
    with TestClient(_app(tmp_path)) as test_client:
        made = test_client.post("/api/workspaces", json={"name": "Team B", "id": "team-b"})
        assert made.status_code == 201, made.text
        yield test_client


@pytest.fixture
def signed_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("EASYCHAIN_AUTH", "on")
    with TestClient(_app(tmp_path)) as test_client:
        setup_owner(test_client)
        made = test_client.post("/api/workspaces", json={"name": "Team B", "id": "team-b"})
        assert made.status_code == 201, made.text
        yield test_client


def _run(client: TestClient, flow_id: str, headers: dict | None = None, **extra) -> dict:
    response = client.post(
        "/api/runs",
        json={"flow_id": flow_id, "inputs": {"text": "x"}, "background": True, **extra},
        headers=headers or {},
    )
    assert response.status_code == 202, response.text
    run_id = response.json()["run_id"]
    deadline = 200
    while deadline:
        run = client.get(f"/api/runs/{run_id}", headers=headers or {}).json()
        if run["status"] in ("ok", "error", "cancelled", "paused"):
            return run
        deadline -= 1
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} didn't finish")


def test_flows_runs_and_secrets_stay_in_their_workspace(client: TestClient):
    here = client.post("/api/flows", json={"spec": TOKEN_LENGTH}).json()["id"]
    there = client.post("/api/flows", json={"spec": TOKEN_LENGTH}, headers=B).json()["id"]
    assert [f["id"] for f in client.get("/api/flows").json()] == [here]
    assert [f["id"] for f in client.get("/api/flows", headers=B).json()] == [there]
    if here != there:
        assert client.get(f"/api/flows/{here}", headers=B).status_code == 404

    # Each workspace's vault reaches only its own runs.
    assert client.put("/api/secrets/TEAM_TOKEN", json={"value": "aaaa"}).status_code == 200
    assert "TEAM_TOKEN" not in {s["name"] for s in client.get("/api/secrets", headers=B).json()}
    assert _run(client, there, B)["output"] == {"length": 0}, "not the default workspace's"
    client.put("/api/secrets/TEAM_TOKEN", json={"value": "bbbbbbbb"}, headers=B)
    assert _run(client, here)["output"] == {"length": 4}
    run_b = _run(client, there, B)
    assert run_b["output"] == {"length": 8}

    # Runs, versions and conversations of one workspace don't show in another.
    assert {r["run_id"] for r in client.get("/api/runs").json()}.isdisjoint(
        {r["run_id"] for r in client.get("/api/runs", headers=B).json()}
    )
    assert client.get(f"/api/runs/{run_b['run_id']}").status_code == 404
    assert client.post(f"/api/runs/{run_b['run_id']}/cancel").status_code == 404
    assert client.get(f"/api/versions/{run_b['version_id']}").status_code == 404
    assert client.get(f"/api/versions/{run_b['version_id']}", headers=B).status_code == 200
    taken = client.post(
        "/api/runs",
        json={"flow_id": here, "inputs": {"text": "x"}, "thread_id": run_b["thread_id"]},
    )
    assert taken.status_code == 404
    assert "isn't in this workspace" in taken.json()["message"]


def test_the_inbox_and_triggers_stay_in_their_workspace(client: TestClient):
    approval = client.post("/api/flows", json={"spec": APPROVAL}, headers=B).json()["id"]
    response = client.post(
        "/api/runs",
        json={"flow_id": approval, "inputs": {"draft": "hi"}, "background": True},
        headers=B,
    )
    run_id = response.json()["run_id"]
    item = None
    for _ in range(200):
        items = client.get("/api/inbox", headers=B).json()
        if items:
            item = items[0]
            break
        time.sleep(0.05)
    assert item is not None and item["run_id"] == run_id
    assert client.get("/api/inbox").json() == []
    assert client.get(f"/api/inbox/{item['id']}").status_code == 404
    refused = client.post(f"/api/inbox/{item['id']}/answer", json={"action": "approve"})
    assert refused.status_code == 404
    answered = client.post(f"/api/inbox/{item['id']}/answer", json={"action": "approve"}, headers=B)
    assert answered.status_code == 200, answered.text

    hook = client.post(
        "/api/triggers", json={"flow_id": approval, "kind": "webhook"}, headers=B
    ).json()
    assert client.get("/api/triggers").json() == []
    assert client.patch(f"/api/triggers/{hook['id']}", json={"enabled": False}).status_code == 404
    assert client.delete(f"/api/triggers/{hook['id']}").status_code == 404
    # A webhook needs no workspace: its run is the trigger's workspace's.
    fired = client.post(f"{hook['url']}?token={hook['token']}", json={"draft": "from a hook"})
    assert fired.status_code == 202, fired.text
    hooked = fired.json()["run_id"]
    assert client.get(f"/api/runs/{hooked}").status_code == 404
    assert client.get(f"/api/runs/{hooked}", headers=B).json()["trigger"] == "webhook"


def test_settings_skills_and_names(client: TestClient):
    # Settings kept per workspace, and those every workspace shares.
    saved = client.put(
        "/api/settings/notifications",
        json={"webhook": {"url": "https://hooks.example.com/b"}},
        headers=B,
    )
    assert saved.status_code == 200, saved.text
    assert client.get("/api/settings/notifications").json() != saved.json()
    mcp = client.get("/api/settings/mcp", headers=B).json()
    widened = client.put("/api/settings/mcp", json={**mcp, "allowed_commands": ["bash"]}, headers=B)
    assert widened.status_code == 403, "commands that run on the server are the server's"
    assert "default workspace" in widened.json()["message"]
    client.put("/api/settings/mcp", json={"servers": [], "allowed_commands": ["npx"]})
    assert client.get("/api/settings/mcp", headers=B).json()["allowed_commands"] == ["npx"]
    client.put("/api/settings/copilot", json={"model": "openai:gpt-4.1"}, headers=B)
    assert client.get("/api/settings/copilot").json()["model"] is None

    # Skills.
    skill = {"files": {"SKILL.md": "---\nname: tone\ndescription: How we write\n---\nBe kind.\n"}}
    assert client.put("/api/skills/tone", json=skill, headers=B).status_code == 200
    assert client.get("/api/skills/tone", headers=B).status_code == 200
    assert client.get("/api/skills/tone").status_code == 404

    # Ids come from names; a workspace nobody made is refused, in words.
    assert client.post("/api/workspaces", json={"name": "Sales team"}).json()["id"] == "sales-team"
    assert client.post("/api/workspaces", json={"name": "Sales team"}).json()["id"] == (
        "sales-team-2"
    )
    assert client.post("/api/workspaces", json={"name": "x", "id": "Bad Id"}).status_code == 422
    renamed = client.patch("/api/workspaces/sales-team", json={"name": "Sales"})
    assert renamed.json()["name"] == "Sales"
    listed = client.get("/api/workspaces").json()["workspaces"]
    assert [w["id"] for w in listed][:2] == ["default", "team-b"]
    unknown = client.get("/api/flows", headers={"X-EasyChain-Workspace": "nowhere"})
    assert unknown.status_code == 403 and unknown.json()["action"] == "workspace"
    # Public routes don't mind a stale workspace (the web app still loads).
    assert client.get("/api/auth/status", headers={"X-EasyChain-Workspace": "nowhere"}).is_success


def test_knowledge_bases_stay_in_their_workspace(client: TestClient):
    made = client.post("/api/knowledge", json={"name": "Team docs"}, headers=B).json()
    assert made["workspace_id"] == "team-b"
    assert made["id"] not in {k["id"] for k in client.get("/api/knowledge").json()}
    assert client.get(f"/api/knowledge/{made['id']}").status_code == 404
    assert client.delete(f"/api/knowledge/{made['id']}").status_code == 404
    text = client.post(
        f"/api/knowledge/{made['id']}/text", json={"title": "Note", "text": "Hello there"}
    )
    assert text.status_code == 404

    # The templates' sample is every workspace's to search, and only the default one's to change.
    sample = "help_centre"
    assert sample in {k["id"] for k in client.get("/api/knowledge", headers=B).json()}
    assert (
        client.patch(f"/api/knowledge/{sample}", json={"name": "Mine"}, headers=B).status_code
        == 403
    )

    # In a run, a flow searches only its own workspace's Knowledge Bases.
    calls = []
    search = in_this_workspace(lambda base, *a, **k: calls.append(base) or [])
    token = current_workspace.workspace.set("team-b")
    try:
        assert search(made["id"], "hello", None) == []
        assert search(sample, "hello", None) == []
        default_base = KnowledgeStore().setup().create_base("Default only", "keywords", 0)
        with pytest.raises(KnowledgeNotHere, match="in this workspace"):
            search(default_base["id"], "hello", None)
    finally:
        current_workspace.workspace.reset(token)
    assert calls == [made["id"], sample]
    checked = client.post(
        "/api/check",
        json={
            "name": "Search",
            "steps": [
                input_step("question"),
                {
                    "id": "find",
                    "type": "knowledge_search",
                    "settings": {"knowledge_base": default_base["id"]},
                },
                output_step("context"),
            ],
            "connections": [{"from": "input", "to": "find"}, {"from": "find", "to": "output"}],
        },
        headers=B,
    ).json()
    assert "knowledge_base_missing" in {i["code"] for i in checked["issues"]}


def test_long_term_memory_stays_in_its_workspace():
    store = WorkspaceStore(InMemoryStore())
    store.put(("memories", "ann"), "likes", {"tea": True})
    token = current_workspace.workspace.set("team-b")
    try:
        assert store.get(("memories", "ann"), "likes") is None
        store.put(("memories", "ann"), "likes", {"coffee": True})
        found = store.get(("memories", "ann"), "likes")
        assert found.value == {"coffee": True} and found.namespace == ("memories", "ann")
        assert [i.value for i in store.search(("memories",))] == [{"coffee": True}]
        assert store.list_namespaces() == [("memories", "ann")]
    finally:
        current_workspace.workspace.reset(token)
    assert store.get(("memories", "ann"), "likes").value == {"tea": True}
    assert [i.value for i in store.search(())] == [{"tea": True}]
    assert store.list_namespaces() == [("memories", "ann")]
    with pytest.raises(InvalidNamespaceError):
        store.get(("easychain-workspace", "team-b", "memories", "ann"), "likes")


def test_people_have_a_role_in_each_workspace(signed_in: TestClient):
    client = signed_in
    bea = Browser(client)
    added = client.post(
        "/api/people",
        json={"email": "bea@example.com", "name": "Bea", "role": "owner", "password": PASSWORD},
        headers=B,
    )
    assert added.status_code == 201, added.text
    assert added.json()["existing"] is False
    assert sign_in(bea, "bea@example.com") == 200
    me = bea.get("/api/auth/me").json()
    assert me["workspace"] == "team-b", "the first workspace she's in"
    assert [w["id"] for w in me["workspaces"]] == ["team-b"]
    assert me["server_owner"] is False
    refused = bea.get("/api/flows", headers={"X-EasyChain-Workspace": "default"})
    assert refused.status_code == 403 and refused.json()["action"] == "workspace"

    # Someone with an account is added to another workspace without a new password.
    again = client.post("/api/people", json={"email": "bea@example.com", "role": "viewer"})
    assert again.status_code == 201 and again.json()["existing"] is True
    assert bea.get("/api/auth/me").json()["role"] == "viewer", "in the default workspace now"
    assert bea.post("/api/flows", json={"name": "No"}).status_code == 403
    assert bea.post("/api/flows", json={"name": "Yes"}, headers=B).status_code == 201

    # An Owner of one workspace isn't an Owner of the server.
    for method, url, body in (
        ("PUT", "/api/settings/sandbox", {"mode": "off"}),
        ("PUT", "/api/settings/network", {"mode": "open", "allow": []}),
        ("GET", "/api/settings/sso", None),
        ("POST", "/api/workspaces", {"name": "Mine"}),
    ):
        response = bea.request(method, url, json=body, headers=B)
        assert response.status_code == 403, (url, response.text)
        assert "server's Owners" in response.json()["message"]
    # Nor can she change an account that is in other workspaces too.
    olive = next(p for p in client.get("/api/people").json()["people"] if p["name"] == "Olive")
    client.post("/api/people", json={"email": "owner@example.com", "role": "editor"}, headers=B)
    switched_off = bea.request(
        "PATCH", f"/api/people/{olive['id']}", json={"disabled": True}, headers=B
    )
    assert switched_off.status_code == 403
    assert bea.request(
        "PATCH", f"/api/people/{olive['id']}", json={"role": "viewer"}, headers=B
    ).is_success

    # A key belongs to the workspace it was made in.
    key = bea.post("/api/keys", json={"name": "script", "role": "editor"}, headers=B).json()
    assert client.get("/api/flows", headers=bearer(key["key"])).status_code == 200
    assert [f["name"] for f in client.get("/api/flows", headers=bearer(key["key"])).json()] == [
        "Yes"
    ]
    other = client.get(
        "/api/flows", headers={**bearer(key["key"]), "X-EasyChain-Workspace": "default"}
    )
    assert other.status_code == 403

    # Each workspace's audit log has its own entries.
    team_b = {e["action"] for e in bea.get("/api/audit", headers=B).json()["entries"]}
    default = {e["action"] for e in client.get("/api/audit").json()["entries"]}
    assert "flow.create" in team_b and "key.create" in team_b
    assert "workspace.create" in default and "key.create" not in default


def test_workspaces_from_the_command_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    import io

    from easychain.cli import main

    home = ["--home", str(tmp_path / "home")]
    monkeypatch.setattr("sys.stdin", io.StringIO(f"{PASSWORD}\n"))
    assert (
        main(["users", "add", "ann@example.com", "--role", "owner", "--password-stdin", *home]) == 0
    )
    assert (
        main(["workspaces", "create", "--name", "Sales team", "--owner", "ann@example.com", *home])
        == 0
    )
    assert "Made workspace sales-team" in capsys.readouterr().out
    assert (
        main(
            ["users", "add", "ann@example.com", "--role", "viewer", "--workspace", "nowhere", *home]
        )
        == 1
    )
    assert "no workspace nowhere" in capsys.readouterr().err
    monkeypatch.setattr("sys.stdin", io.StringIO(f"{PASSWORD}\n"))
    assert (
        main(
            [
                "users",
                "add",
                "cy@example.com",
                "--role",
                "runner",
                "--workspace",
                "sales-team",
                "--password-stdin",
                *home,
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert main(["users", "list", "--workspace", "sales-team", *home]) == 0
    assert [line.split()[0] for line in capsys.readouterr().out.splitlines()] == [
        "ann@example.com",
        "cy@example.com",
    ]
    assert main(["workspaces", "rename", "sales-team", "--name", "Sales", *home]) == 0
    assert "now called Sales" in capsys.readouterr().out
    assert main(["workspaces", "list", *home]) == 0
    listed = capsys.readouterr().out.splitlines()
    assert listed[0].split()[0] == "default" and listed[1].split()[:2] == ["sales-team", "Sales"]
    assert main(["audit", "--workspace", "sales-team", *home]) == 0
    assert "added cy@example.com" in capsys.readouterr().out
