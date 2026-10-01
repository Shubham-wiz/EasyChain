"""The API server: flows, catalog, checks, code, export, runs over SSE and secrets."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from easychain.server.app import create_app


@pytest.fixture
def client(tmp_path: Path):
    app = create_app(
        home=tmp_path / "home", static_dir=tmp_path / "no-web", worker_options={"poll": 0.05}
    )
    with TestClient(app) as test_client:
        yield test_client


def sse_events(response) -> list[dict]:
    events = []
    for line in response.iter_lines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


def test_health_and_catalog(client):
    assert client.get("/api/health").json()["ok"] is True
    catalog = client.get("/api/catalog").json()
    types = [s["type"] for s in catalog["steps"]]
    assert types == [
        "input",
        "output",
        "instructions",
        "ai_model",
        "http_request",
        "code",
        "decision",
        "for_each",
        "jump",
        "subflow",
        "ask_human",
    ]
    ai = next(s for s in catalog["steps"] if s["type"] == "ai_model")
    assert ai["label"] == "AI Model" and ai["technical"].startswith("Chat model")
    assert ai["defaults"]["model"] == "openai:gpt-4o-mini"
    assert any(f["key"] == "temperature" and f["advanced"] for f in ai["form"])
    assert {p["id"] for p in catalog["providers"]} == {"openai", "anthropic", "ollama"}
    assert client.get("/api/schema").json()["title"] == "Easy Chain flow"


def test_templates_report_key_status(client, monkeypatch):
    items = client.get("/api/templates").json()
    assert items[0]["keys"][0]["set"] is False
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    assert client.get("/api/templates").json()[0]["keys"][0]["set"] is True


def test_flow_crud(client):
    created = client.post("/api/flows", json={"template": "summarise-url"}).json()
    assert created["id"] == "summarise-a-url"
    again = client.post("/api/flows", json={"template": "summarise-url"}).json()
    assert again["id"] == "summarise-a-url-2"
    blank = client.post("/api/flows", json={"name": "Blank one"}).json()
    assert [s["type"] for s in blank["spec"]["steps"]] == ["input", "output"]
    listed = client.get("/api/flows").json()
    assert {f["id"] for f in listed} == {"summarise-a-url", "summarise-a-url-2", "blank-one"}

    spec = client.get("/api/flows/summarise-a-url").json()["spec"]
    spec["name"] = "Renamed"
    assert client.put("/api/flows/summarise-a-url", json=spec).json()["saved"] is True
    assert client.get("/api/flows/summarise-a-url").json()["spec"]["name"] == "Renamed"
    yaml_text = client.get("/api/flows/summarise-a-url/yaml").text
    assert yaml_text.startswith("version: 1\nname: Renamed")

    bad = client.put(
        "/api/flows/summarise-a-url", json={"name": "x", "steps": [{"id": "Bad", "type": "input"}]}
    )
    assert bad.status_code == 422
    assert "lowercase" in bad.json()["problems"][0]

    imported = client.post("/api/flows", json={"yaml": yaml_text}).json()
    assert imported["spec"]["name"] == "Renamed"
    assert client.post("/api/flows", json={"yaml": "name: ["}).status_code == 422
    assert client.post("/api/flows", json={"template": "nope"}).status_code == 404
    assert client.post("/api/flows", json={"spec": spec}).status_code == 201

    assert client.delete("/api/flows/blank-one").json()["deleted"] is True
    assert client.get("/api/flows/blank-one").status_code == 404
    assert client.delete("/api/flows/blank-one").status_code == 404
    assert client.get("/api/flows/..%2Fetc").status_code == 404
    assert client.get("/api/flows/nope/yaml").status_code == 404


def test_check_and_compile(client):
    spec = client.post("/api/flows", json={"template": "summarise-url"}).json()["spec"]
    checked = client.post("/api/check", json=spec).json()
    assert [i["code"] for i in checked["issues"]] == ["missing_key"]
    assert checked["issues"][0]["fix"]["kind"] == "add_key"
    assert "summary" in {f["name"] for f in checked["analysis"]["fields"]}
    compiled = client.post("/api/compile", json=spec).json()
    assert "builder = StateGraph(" in compiled["source"]
    assert "def summarise(data: FlowData)" in compiled["snippets"]["summarise"]
    assert "langchain-openai==1.6.7" in compiled["requirements"]

    broken = dict(spec, steps=[s for s in spec["steps"] if s["type"] != "input"])
    result = client.post("/api/compile", json=broken).json()
    assert any(i["code"] == "no_input" for i in result["issues"])


def test_missing_secret_is_flagged(client):
    spec = {
        "name": "Secret flow",
        "steps": [
            {"id": "input", "type": "input", "settings": {"fields": [{"name": "q"}]}},
            {
                "id": "call",
                "type": "http_request",
                "settings": {
                    "url": "https://x.example",
                    "headers": {"Authorization": "Bearer {secret:MY_TOKEN}"},
                },
            },
            {"id": "output", "type": "output", "settings": {"fields": ["response"]}},
        ],
        "connections": [{"from": "input", "to": "call"}, {"from": "call", "to": "output"}],
    }
    issues = client.post("/api/check", json=spec).json()["issues"]
    assert [i["code"] for i in issues] == ["missing_secret"]
    client.put("/api/secrets/MY_TOKEN", json={"value": "tok-123456"})
    assert client.post("/api/check", json=spec).json()["issues"] == []


def test_export_zip(client):
    flow_id = client.post("/api/flows", json={"template": "summarise-url"}).json()["id"]
    response = client.get(f"/api/flows/{flow_id}/export")
    assert response.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(response.content)).namelist()
    assert "summarise_a_url/summarise_a_url.py" in names
    assert "summarise_a_url/requirements.txt" in names
    bad = client.post("/api/export", json={"name": "x", "steps": []})
    assert bad.status_code == 422
    assert bad.json()["issues"]


def test_run_streams_events_and_is_recorded(client):
    flow_id = client.post("/api/flows", json={"template": "reply-to-feedback"}).json()["id"]
    with client.stream(
        "POST",
        "/api/runs",
        json={"flow_id": flow_id, "inputs": {"feedback": "Thanks, I love it"}, "stand_in": True},
    ) as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        events = sse_events(response)
    kinds = [e["type"] for e in events]
    assert kinds[:2] == ["run_queued", "run_started"] and kinds[-1] == "run_finished"
    assert "token" in kinds and "route" in kinds
    assert events[-1]["status"] == "ok"
    runs = client.get(f"/api/runs?flow_id={flow_id}").json()
    assert runs[0]["status"] == "ok"
    detail = client.get(f"/api/runs/{runs[0]['run_id']}").json()
    assert all(e["type"] != "token" for e in detail["events"])
    assert client.get("/api/runs/nope").status_code == 404


def test_run_unsaved_spec_and_errors(client):
    spec = client.post("/api/flows", json={"template": "summarise-url"}).json()["spec"]
    with client.stream("POST", "/api/runs", json={"spec": spec, "inputs": {}}) as response:
        events = sse_events(response)
    assert events[-1]["error"]["kind"] == "bad_input"
    assert client.post("/api/runs", json={"flow_id": "missing"}).status_code == 404


def test_chat_run_keeps_thread(client):
    flow_id = client.post("/api/flows", json={"template": "chat-assistant"}).json()["id"]
    for text in ("Hi there", "Second message"):
        with client.stream(
            "POST",
            "/api/runs",
            json={
                "flow_id": flow_id,
                "inputs": {"message": text},
                "thread_id": "chat-1",
                "stand_in": True,
            },
        ) as response:
            final = sse_events(response)[-1]
    assert len(final["output"]["messages"]) == 4
    assert final["reply"].startswith("[Stand-in AI")


def test_secrets_are_write_only_and_encrypted(client, tmp_path, monkeypatch):
    assert client.put("/api/secrets/bad name", json={"value": "x"}).status_code == 422
    assert client.put("/api/secrets/OPENAI_API_KEY", json={"value": ""}).status_code == 422
    assert client.put("/api/secrets/OPENAI_API_KEY", json={"value": "sk-secret-value"}).json()[
        "saved"
    ]
    listed = client.get("/api/secrets").json()
    assert listed == [{"name": "OPENAI_API_KEY", "source": "vault"}]
    assert "sk-secret-value" not in json.dumps(listed)
    stored = (tmp_path / "home" / "secrets.enc").read_bytes()
    assert b"sk-secret-value" not in stored
    providers = client.get("/api/providers").json()
    assert next(p for p in providers if p["id"] == "openai")["key_set"] is True

    # A new server process with the same home reads the vault back.
    monkeypatch.delenv("OPENAI_API_KEY")
    TestClient(create_app(home=tmp_path / "home", static_dir=tmp_path / "no-web"))
    import os

    assert os.environ["OPENAI_API_KEY"] == "sk-secret-value"
    assert client.delete("/api/secrets/OPENAI_API_KEY").json()["deleted"]
    assert client.delete("/api/secrets/OPENAI_API_KEY").status_code == 404


def test_environment_keys_are_listed(client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    assert client.get("/api/secrets").json() == [
        {"name": "ANTHROPIC_API_KEY", "source": "environment"}
    ]


def test_serves_built_web_app(tmp_path):
    web = tmp_path / "web"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text("<html>app</html>")
    (web / "assets" / "app.js").write_text("console.log(1)")
    (web / "favicon.svg").write_text("<svg/>")
    client = TestClient(create_app(home=tmp_path / "home", static_dir=web))
    assert client.get("/").text == "<html>app</html>"
    assert client.get("/flows/abc").text == "<html>app</html>"
    assert client.get("/assets/app.js").text == "console.log(1)"
    assert client.get("/favicon.svg").text == "<svg/>"
    assert client.get("/api/unknown").status_code == 404


def test_without_web_build_explains(client):
    assert "web app isn't built" in client.get("/").text
