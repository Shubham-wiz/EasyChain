"""Environments and Publish (Phase 5.4, environments.py, server/environments_api.py,
server/publish_api.py, docs/publish.md)."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from easychain.environments import Environment, using
from easychain.knowledge.store import KnowledgeStore, in_this_workspace
from easychain.runtime.gateway import gateway_init_chat_model
from easychain.server.app import create_app

from .test_api_phase2 import code
from .test_checks import ECHO
from .test_workspaces import TOKEN_LENGTH


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    app = create_app(
        home=tmp_path / "home",
        workspace=tmp_path / "flows",
        static_dir=tmp_path / "no-web",
        worker_options={"poll": 0.05, "schedules": False},
    )
    with TestClient(app) as test_client:
        yield test_client


def _test_run(client: TestClient, flow_id: str, **body) -> dict:
    run_id = client.post(f"/api/flows/{flow_id}/test-runs", json=body).json()["id"]
    for _ in range(400):
        found = client.get(f"/api/test-runs/{run_id}").json()
        if found["status"] in ("done", "error"):
            return found
        time.sleep(0.05)
    raise AssertionError("the Test Run didn't finish")


def test_an_environment_swaps_models_and_knowledge_bases(fake_openai):
    swapped = Environment("dev", models={"openai:gpt-4.1": "openai:gpt-4.1-mini"})
    with using(swapped):
        assert gateway_init_chat_model("openai:gpt-4.1").model_name == "gpt-4.1-mini"
        assert gateway_init_chat_model("openai:gpt-4o").model_name == "gpt-4o", "only swaps"
    assert gateway_init_chat_model("openai:gpt-4.1").model_name == "gpt-4.1", "a draft as drawn"

    store = KnowledgeStore().setup()
    drafts = store.create_base("Docs being written", "keywords", 0)["id"]
    live = store.create_base("Docs people read", "keywords", 0)["id"]
    searched = []
    search = in_this_workspace(lambda base, *a, **k: searched.append(base) or [])
    with using(Environment("production", knowledge={drafts: live})):
        search(drafts, "q", None)
    search(drafts, "q", None)
    assert searched == [live, drafts]


def test_environments_have_their_own_secrets_and_swaps(client: TestClient):
    shown = client.get("/api/environments").json()["environments"]
    assert [e["id"] for e in shown] == ["dev", "staging", "production"]
    assert shown[1] == {
        "id": "staging",
        "label": "Staging",
        "models": {},
        "knowledge": {},
        "secrets": [],
    }
    bad = client.put(
        "/api/environments/staging",
        json={"models": {"gpt-4.1": "openai:gpt-4.1-mini"}, "knowledge": {"a": "nowhere"}},
    )
    assert bad.status_code == 422
    assert bad.json()["problems"] == [
        "“gpt-4.1” isn't a model as provider:model (like openai:gpt-4.1).",
        "There's no Knowledge Base “nowhere” in this workspace.",
    ]
    saved = client.put(
        "/api/environments/staging", json={"models": {"openai:gpt-4.1": "openai:gpt-4.1-mini"}}
    )
    assert saved.json()["models"] == {"openai:gpt-4.1": "openai:gpt-4.1-mini"}
    assert client.put("/api/environments/qa", json={}).status_code == 404

    # A Test Run in staging sees staging's secret over the workspace's; one without, the
    # workspace's.
    flow_id = client.post("/api/flows", json={"spec": TOKEN_LENGTH}).json()["id"]
    client.put(
        f"/api/flows/{flow_id}/tests",
        json={"cases": [{"name": "Length", "inputs": {"text": "x"}}]},
    )
    client.put("/api/secrets/TEAM_TOKEN", json={"value": "aaaa"})
    client.put("/api/environments/staging/secrets/TEAM_TOKEN", json={"value": "bbbbbbbb"})
    assert "TEAM_TOKEN" in client.get("/api/environments").json()["environments"][1]["secrets"]
    plain = _test_run(client, flow_id)
    staging = _test_run(client, flow_id, environment="staging")
    assert staging["environment"] == "staging" and plain["environment"] is None
    assert [c["passed"] for c in staging["cases"]] == [1]
    assert _outputs(client, staging) == {"length": 8}
    assert _outputs(client, plain) == {"length": 4}
    assert client.delete("/api/environments/staging/secrets/TEAM_TOKEN").status_code == 200
    assert _outputs(client, _test_run(client, flow_id, environment="staging")) == {"length": 4}
    audit = {e["action"] for e in client.get("/api/audit").json()["entries"]}
    assert {"environment.change", "secret.set", "secret.delete"} <= audit


def _outputs(client: TestClient, test_run: dict) -> dict:
    """The output of a Test Run's only case, from its run (kept on the case's summary)."""
    return test_run["cases"][0]["output"]


# ── publishing and promotion ─────────────────────────────────────────────────


def _flow_with_tests(client: TestClient, spec: dict, cases: list[dict]) -> str:
    flow_id = client.post("/api/flows", json={"spec": spec}).json()["id"]
    client.put(f"/api/flows/{flow_id}/tests", json={"cases": cases})
    return flow_id


def test_a_version_moves_to_staging_and_production_only_when_its_test_run_passes(
    client: TestClient,
):
    flow_id = _flow_with_tests(
        client,
        ECHO,
        [{"name": "Shouts", "inputs": {"text": "hi"}, "expect": {"output.loud": "HI"}}],
    )
    dev = client.post(f"/api/flows/{flow_id}/publish", json={"environment": "dev"})
    assert dev.status_code == 200, dev.text
    version = dev.json()["version_id"]

    refused = client.post(f"/api/flows/{flow_id}/publish", json={"environment": "staging"})
    assert refused.status_code == 409 and refused.json()["action"] == "test_run"
    assert "kept Test Run of this version, run in Staging" in refused.json()["message"]
    not_yet = client.post(f"/api/flows/{flow_id}/publish", json={"environment": "production"})
    assert not_yet.status_code == 409 and not_yet.json()["action"] == "staging"

    elsewhere = _test_run(client, flow_id)  # passes, but not in staging
    assert elsewhere["summary"]["failed"] == 0
    assert (
        client.post(f"/api/flows/{flow_id}/publish", json={"environment": "staging"}).status_code
        == 409
    )
    passed = _test_run(client, flow_id, environment="staging")
    assert passed["version_id"] == version
    staging = client.post(f"/api/flows/{flow_id}/publish", json={"environment": "staging"})
    assert staging.status_code == 200, staging.text
    assert staging.json()["test_run_id"] == passed["id"]
    production = client.post(f"/api/flows/{flow_id}/publish", json={"environment": "production"})
    assert production.json()["version_id"] == version

    # A changed flow is a new version: it can't skip staging, and a failing Test Run keeps it out.
    broken = {**ECHO, "steps": [ECHO["steps"][0], code("shout", "    return {'loud': data['text'], 'size': 0}"), ECHO["steps"][2]]}  # fmt: skip
    client.put(f"/api/flows/{flow_id}", json=broken)
    assert (
        client.post(f"/api/flows/{flow_id}/publish", json={"environment": "production"}).status_code
        == 409
    )
    failing = _test_run(client, flow_id, environment="staging")
    assert failing["summary"]["failed"] == 1
    assert (
        client.post(f"/api/flows/{flow_id}/publish", json={"environment": "staging"}).status_code
        == 409
    )

    state = client.get(f"/api/flows/{flow_id}/publish").json()
    published = {e["id"]: (e["published"] or {}).get("version_id") for e in state["environments"]}
    assert published == {"dev": version, "staging": version, "production": version}
    assert [h["environment"] for h in state["history"]] == ["production", "staging", "dev"]
    assert client.delete(f"/api/flows/{flow_id}/publish/dev").json() == {"unpublished": "dev"}
    assert client.delete(f"/api/flows/{flow_id}/publish/dev").status_code == 404
    audit = {e["action"] for e in client.get("/api/audit").json()["entries"]}
    assert {"flow.publish", "flow.unpublish"} <= audit


def test_only_an_owner_publishes_to_production(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from .test_auth import PASSWORD, Browser, setup_owner, sign_in

    monkeypatch.setenv("EASYCHAIN_AUTH", "on")
    app = create_app(
        home=tmp_path / "home",
        workspace=tmp_path / "flows",
        static_dir=tmp_path / "no-web",
        worker_options={"poll": 0.05, "schedules": False},
    )
    with TestClient(app) as owner:
        setup_owner(owner)
        flow_id = _flow_with_tests(owner, ECHO, [{"name": "Shouts", "inputs": {"text": "hi"}}])
        _test_run(owner, flow_id, environment="staging")
        owner.post(
            "/api/people",
            json={"email": "ed@example.com", "name": "Ed", "role": "editor", "password": PASSWORD},
        )
        editor = Browser(owner)
        assert sign_in(editor, "ed@example.com") == 200
        assert (
            editor.post(
                f"/api/flows/{flow_id}/publish", json={"environment": "staging"}
            ).status_code
            == 200
        )
        refused = editor.post(f"/api/flows/{flow_id}/publish", json={"environment": "production"})
        assert refused.status_code == 403 and refused.json()["action"] == "role"
        assert (
            editor.post(
                f"/api/flows/{flow_id}/keys", json={"environment": "production"}
            ).status_code
            == 403
        )
        assert (
            owner.post(
                f"/api/flows/{flow_id}/publish", json={"environment": "production"}
            ).status_code
            == 200
        )


# ── running what is published ────────────────────────────────────────────────


def _published(client: TestClient, spec: dict, environment: str = "dev") -> str:
    flow_id = client.post("/api/flows", json={"spec": spec}).json()["id"]
    assert (
        client.post(f"/api/flows/{flow_id}/publish", json={"environment": environment}).status_code
        == 200
    )
    return flow_id


def test_other_apps_run_a_published_flow_with_its_access_key(client: TestClient):
    flow_id = _published(client, TOKEN_LENGTH)
    client.put("/api/secrets/TEAM_TOKEN", json={"value": "aaaa"})
    client.put("/api/environments/dev/secrets/TEAM_TOKEN", json={"value": "bbbbbbbb"})
    made = client.post(
        f"/api/flows/{flow_id}/keys", json={"environment": "dev", "name": "Shop", "per_minute": 2}
    )
    assert made.status_code == 201, made.text
    key = made.json()["key"]
    assert (
        key.startswith("epk_")
        and "key" not in client.get(f"/api/flows/{flow_id}/publish").json()["keys"][0]
    )
    url = f"/api/published/{flow_id}/dev"

    ran = client.post(
        url, json={"inputs": {"text": "x"}}, headers={"Authorization": f"Bearer {key}"}
    )
    assert ran.status_code == 200, ran.text
    assert ran.json()["status"] == "ok" and ran.json()["output"] == {"length": 8}, "dev's secret"
    run = client.get(f"/api/runs/{ran.json()['run_id']}").json()
    assert (run["trigger"], run["flow_id"]) == ("api", flow_id)
    again = client.get(
        f"{url}/runs/{ran.json()['run_id']}", headers={"Authorization": f"Bearer {key}"}
    )
    assert again.json()["output"] == {"length": 8}

    for bad in ("", "Bearer epk_nope_x", f"Bearer {key}x"):
        assert client.post(url, json={}, headers={"Authorization": bad}).status_code == 401
    assert (
        client.post(
            f"/api/published/{flow_id}/staging", json={}, headers={"Authorization": f"Bearer {key}"}
        ).status_code
        == 401
    ), "a key works in its own environment only"

    client.post(
        url, json={"inputs": {"text": "x"}, "wait": 0}, headers={"Authorization": f"Bearer {key}"}
    )
    limited = client.post(
        url, json={"inputs": {"text": "x"}}, headers={"Authorization": f"Bearer {key}"}
    )
    assert limited.status_code == 429 and int(limited.headers["Retry-After"]) > 0
    assert "allows 2 a minute" in limited.json()["message"]

    key_id = made.json()["id"]
    assert client.delete(f"/api/flows/{flow_id}/keys/{key_id}").json() == {"revoked": key_id}
    assert client.post(url, json={}, headers={"Authorization": f"Bearer {key}"}).status_code == 401
    client.delete(f"/api/flows/{flow_id}/publish/dev")
    other = client.post(f"/api/flows/{flow_id}/keys", json={"environment": "dev"}).json()["key"]
    gone = client.post(url, json={}, headers={"Authorization": f"Bearer {other}"})
    assert (
        gone.status_code == 404 and "Nothing is published to Development" in gone.json()["message"]
    )


def test_people_chat_with_a_published_flow(client: TestClient, fake_openai):
    flow_id = client.post("/api/flows", json={"template": "chat-assistant"}).json()["id"]
    client.post(f"/api/flows/{flow_id}/publish", json={"environment": "dev"})
    link = client.post(
        f"/api/flows/{flow_id}/keys",
        json={"environment": "dev", "kind": "chat", "origins": ["https://shop.example.com/"]},
    ).json()
    assert "key" not in link and link["chat_path"] == f"/chat/{link['id']}"
    page = client.get(link["chat_path"])
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    assert (
        page.headers["content-security-policy"] == "frame-ancestors 'self' https://shop.example.com"
    )
    assert f'"{link["id"]}"' in page.text
    assert client.get(f"/api/chat/{link['id']}").json()["available"] is True

    first = client.post(f"/api/chat/{link['id']}", json={"message": "Hello there"}).json()
    assert first["status"] == "ok" and first["reply"], first
    second = client.post(
        f"/api/chat/{link['id']}", json={"message": "And again", "thread_id": first["thread_id"]}
    ).json()
    assert second["thread_id"] == first["thread_id"], "the conversation goes on"
    widget = client.get("/widget.js")
    assert widget.headers["content-type"].startswith("text/javascript")
    assert "data-chat" in widget.text
    assert client.get("/chat/nope").status_code == 404


# ── easychain publish (from CI) and easychain deploy ─────────────────────────


def test_publish_from_ci_runs_the_test_run_first(client: TestClient):
    from easychain.deploy import DeployError, publish_flow

    said: list[str] = []
    good = _flow_with_tests(
        client,
        ECHO,
        [{"name": "Shouts", "inputs": {"text": "hi"}, "expect": {"output.loud": "HI"}}],
    )
    made = publish_flow(client, good, "staging", poll=0.05, say=said.append)
    assert made["environment"] == "staging" and made["test_run_id"]
    assert said[1:] == [
        "1/1 cases passed.",
        f"Published version {made['version_id'][:7]} of {good} to staging.",
    ]
    assert (
        publish_flow(client, good, "production", say=said.append)["version_id"]
        == made["version_id"]
    )

    bad = _flow_with_tests(
        client,
        ECHO,
        [{"name": "Whispers", "inputs": {"text": "hi"}, "expect": {"output.loud": "hi"}}],
    )
    with pytest.raises(DeployError, match="Whispers: output.loud: expected 'hi', got 'HI'"):
        publish_flow(client, bad, "staging", poll=0.05, say=said.append)
    with pytest.raises(DeployError, match="Production only takes the version that is in Staging"):
        publish_flow(client, bad, "production", say=said.append)


def test_deploy_packs_the_flows_for_a_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import yaml

    from easychain.cli import main
    from easychain.deploy import DeployError, build_image, compose_bundle
    from easychain.spec import dumps_spec, parse_spec

    flows = tmp_path / "flows"
    (flows / "skills" / "tone").mkdir(parents=True)
    (flows / "echo.flow.yaml").write_text(dumps_spec(parse_spec(ECHO)), encoding="utf-8")
    (flows / "echo.tests.yaml").write_text("flow: echo.flow.yaml\ncases: []\n", encoding="utf-8")
    (flows / "skills" / "tone" / "SKILL.md").write_text("---\nname: tone\n---\n", encoding="utf-8")
    (flows / "notes.txt").write_text("not a flow", encoding="utf-8")

    out = tmp_path / "bundle"
    assert (
        main(["deploy", "compose", str(out), "--flows", str(flows), "--base", "easychain:test"])
        == 0
    )
    assert sorted(p.name for p in (out / "flows").iterdir()) == [
        "echo.flow.yaml",
        "echo.tests.yaml",
        "skills",
    ]
    assert (out / "Dockerfile").read_text(encoding="utf-8").splitlines()[1] == "FROM easychain:test"
    compose = yaml.safe_load((out / "docker-compose.yml").read_text(encoding="utf-8"))
    assert set(compose["services"]) == {"db", "easychain", "worker"}
    assert compose["services"]["worker"]["image"] == "easychain-flows:latest"
    assert (out / ".env.example").exists() and "docker compose up -d" in (
        out / "README.md"
    ).read_text(encoding="utf-8")
    with pytest.raises(DeployError, match="isn't empty"):
        compose_bundle(flows, out)
    with pytest.raises(DeployError, match="no flows"):
        compose_bundle(tmp_path / "skills-only", tmp_path / "other")

    built: list[list[str]] = []

    def docker(args, check=False):
        context = Path(args[-1])
        assert (context / "flows" / "echo.flow.yaml").exists() and (context / "Dockerfile").exists()
        built.append(args)
        return type("Done", (), {"returncode": 0})()

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/docker")
    assert build_image(flows, "shop-flows:1", run=docker) == 1
    assert built[0][:4] == ["/usr/bin/docker", "build", "-t", "shop-flows:1"]
    monkeypatch.setattr("shutil.which", lambda name: None)
    with pytest.raises(DeployError, match="Docker isn't installed"):
        build_image(flows, "shop-flows:1", run=docker)
