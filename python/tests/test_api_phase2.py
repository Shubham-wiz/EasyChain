"""The Phase 2 API: Inbox and notifications, cancel, continue, time travel, conversations
that get a second message while busy, triggers, run limits, versions and live event streams."""

from __future__ import annotations

import json
import socketserver
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from easychain.server.app import create_app

from .conftest import input_step, output_step


def sse(response: Any) -> list[dict]:
    return [json.loads(line[6:]) for line in response.iter_lines() if line.startswith("data: ")]


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    app = create_app(
        home=tmp_path / "home",
        workspace=tmp_path / "flows",
        static_dir=tmp_path / "no-web",
        worker_options={"poll": 0.05, "lease_seconds": 5},
    )
    with TestClient(app) as test_client:
        yield test_client


def create(client: TestClient, spec: dict) -> str:
    return client.post("/api/flows", json={"spec": spec}).json()["id"]


def wait(client: TestClient, run_id: str, *statuses: str, timeout: float = 20) -> dict:
    statuses = statuses or ("ok", "error", "cancelled")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = client.get(f"/api/runs/{run_id}").json()
        if run["status"] in statuses:
            return run
        time.sleep(0.05)
    raise AssertionError(f"run stayed {run['status']}")


def start(client: TestClient, flow_id: str, inputs: dict | None = None, **extra: Any) -> str:
    response = client.post(
        "/api/runs", json={"flow_id": flow_id, "inputs": inputs or {}, "background": True, **extra}
    )
    assert response.status_code == 202, response.text
    return response.json()["run_id"]


def code(id_: str, body: str, **settings: Any) -> dict:
    return {
        "id": id_,
        "type": "code",
        "settings": {"code": f"def run(data):\n{body}\n", **settings},
    }


APPROVAL = {
    "name": "Approval",
    "steps": [
        input_step("draft"),
        {
            "id": "check",
            "type": "ask_human",
            "settings": {"question": "Send {draft}?", "show": ["draft"]},
        },
        code("send", "    return {'sent': data['draft'].upper()}"),
        output_step("sent", "human_answer", "human_answer_comment"),
    ],
    "connections": [
        {"from": "input", "to": "check"},
        {"from": "check", "to": "send", "exit": "Approved"},
        {"from": "check", "to": "output", "exit": "Rejected"},
        {"from": "send", "to": "output"},
    ],
}

SLOW = {
    "name": "Slow",
    "steps": [
        input_step("x"),
        code(
            "slow",
            "    import time\n    time.sleep(float(data.get('x') or 2))\n    return {'y': 1}",
        ),
        output_step("y"),
    ],
    "connections": [{"from": "input", "to": "slow"}, {"from": "slow", "to": "output"}],
}


class _SMTP(socketserver.StreamRequestHandler):
    messages: list[str] = []

    def handle(self) -> None:
        self.wfile.write(b"220 test ESMTP\r\n")
        data: list[str] = []
        in_data = False
        while line := self.rfile.readline():
            text = line.decode("utf-8", "replace")
            if in_data:
                if text.rstrip("\r\n") == ".":
                    in_data = False
                    _SMTP.messages.append("".join(data))
                    self.wfile.write(b"250 queued\r\n")
                else:
                    data.append(text)
                continue
            verb = text[:4].upper()
            if verb in ("EHLO", "HELO"):
                self.wfile.write(b"250 test\r\n")
            elif verb == "DATA":
                in_data = True
                self.wfile.write(b"354 go ahead\r\n")
            elif verb == "QUIT":
                self.wfile.write(b"221 bye\r\n")
                return
            else:
                self.wfile.write(b"250 ok\r\n")


@pytest.fixture
def smtp() -> Iterator[int]:
    _SMTP.messages.clear()
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _SMTP)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


def test_inbox_answer_resumes_the_run_and_notifies_people(client, fake_server, smtp):
    httpx.post(f"{fake_server.url}/effects/reset")
    settings = client.put(
        "/api/settings/notifications",
        json={
            "webhook": {"enabled": True, "url": f"{fake_server.url}/effects"},
            "slack": {"enabled": True, "webhook_url": f"{fake_server.url}/effects"},
            "email": {
                "enabled": True,
                "smtp_host": "127.0.0.1",
                "smtp_port": smtp,
                "starttls": False,
                "sender": "bot@example.com",
                "to": ["me@example.com"],
            },
            "public_url": "http://easychain.test",
        },
    ).json()
    assert settings["email"]["to"] == ["me@example.com"]
    flow_id = create(client, APPROVAL)
    run_id = start(client, flow_id, {"draft": "hello"})
    run = wait(client, run_id, "paused")
    assert run["pending"]["reason"] == "ask_human"
    [item] = client.get("/api/inbox").json()
    assert item["run_id"] == run_id and item["step"] == "check"
    assert item["request"]["question"] == "Send hello?"
    assert item["request"]["show"] == {"draft": "hello"}

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        events = client.get(f"/api/runs/{run_id}").json()["events"]
        if any(e["type"] == "notified" for e in events):
            break
        time.sleep(0.05)
    calls = httpx.get(f"{fake_server.url}/effects").json()["calls"]
    hook = next(c["body"] for c in calls if c["body"].get("event") == "ask_human")
    assert hook["question"] == "Send hello?"
    assert hook["url"] == f"http://easychain.test/inbox/{item['id']}"
    assert any("“Approval” is waiting for you" in c["body"].get("text", "") for c in calls)
    assert "Send hello?" in _SMTP.messages[0]
    detail = client.get(f"/api/runs/{run_id}").json()
    notified = [e for e in detail["events"] if e["type"] == "notified"]
    assert notified and all(r["ok"] for r in notified[0]["results"])

    answer = {"action": "approve", "comment": "fine", "by": "sam"}
    assert client.post(f"/api/inbox/{item['id']}/answer", json=answer).json()["run_id"] == run_id
    run = wait(client, run_id)
    assert run["status"] == "ok"
    assert run["output"] == {
        "sent": "HELLO",
        "human_answer": "Approved",
        "human_answer_comment": "fine",
    }
    answered = client.get("/api/inbox?status=answered").json()
    assert answered[0]["answered_by"] == "sam"
    assert client.post("/api/settings/notifications/test").json()["results"]


def test_resume_endpoint_streams_the_rest_of_the_run(client):
    flow_id = create(client, APPROVAL)
    with client.stream(
        "POST", "/api/runs", json={"flow_id": flow_id, "inputs": {"draft": "hi"}}
    ) as r:
        events = sse(r)
    assert events[-1]["status"] == "paused"
    run_id = events[-1]["run_id"]
    assert client.post(f"/api/runs/{run_id}/continue").status_code == 409
    with client.stream(
        "POST", f"/api/runs/{run_id}/resume", json={"answer": {"action": "reject"}}
    ) as r:
        events = sse(r)
    assert events[0]["type"] == "run_queued"
    assert events[-1]["status"] == "ok"
    assert ("check", "Rejected") in [(e["step"], e["exit"]) for e in events if e["type"] == "route"]
    assert client.post(f"/api/runs/{run_id}/resume", json={"answer": "x"}).status_code == 409


def test_cancel_a_running_run(client):
    flow_id = create(client, SLOW)
    run_id = start(client, flow_id, {"x": "5"})
    wait(client, run_id, "running")
    time.sleep(0.2)
    assert client.post(f"/api/runs/{run_id}/cancel").json()["status"] in ("cancelling", "cancelled")
    run = wait(client, run_id, "cancelled", timeout=5)
    assert run["status"] == "cancelled"


def test_cancel_a_queued_run(client, tmp_path):
    app = create_app(
        home=tmp_path / "h2", workspace=tmp_path / "f2", static_dir=tmp_path / "nw", worker=False
    )
    with TestClient(app) as no_worker:
        flow_id = create(no_worker, SLOW)
        run_id = start(no_worker, flow_id)
        assert no_worker.get(f"/api/runs/{run_id}").json()["status"] == "queued"
        assert no_worker.post(f"/api/runs/{run_id}/cancel").json()["status"] == "cancelled"
        with no_worker.stream("GET", f"/api/runs/{run_id}/events") as r:
            events = sse(r)
        assert events[-1]["type"] == "run_finished" and events[-1]["status"] == "cancelled"


def test_breakpoint_continue_save_points_and_fork(client):
    spec = {
        "name": "Steps",
        "steps": [
            input_step("text"),
            code("shout", "    return {'loud': data['text'].upper()}"),
            code("exclaim", "    return {'final': data['loud'] + '!'}"),
            output_step("final"),
        ],
        "connections": [
            {"from": "input", "to": "shout"},
            {"from": "shout", "to": "exclaim"},
            {"from": "exclaim", "to": "output"},
        ],
    }
    flow_id = create(client, spec)
    with client.stream(
        "POST",
        "/api/runs",
        json={"flow_id": flow_id, "inputs": {"text": "hey"}, "pause_after": ["shout"]},
    ) as r:
        events = sse(r)
    final = events[-1]
    assert (
        final["status"] == "paused"
        and final["reason"] == "breakpoint"
        and final["next"] == ["exclaim"]
    )
    run_id = final["run_id"]
    with client.stream("POST", f"/api/runs/{run_id}/continue") as r:
        events = sse(r)
    assert events[-1]["status"] == "ok" and events[-1]["output"] == {"final": "HEY!"}

    points = client.get(f"/api/runs/{run_id}/savepoints").json()
    assert all(p["run_id"] == run_id for p in points)
    before = next(p for p in points if p["next"] == ["exclaim"])
    assert before["values"]["loud"] == "HEY"
    with client.stream(
        "POST",
        f"/api/runs/{run_id}/fork",
        json={"checkpoint_id": before["checkpoint_id"], "update": {"loud": "CHANGED"}},
    ) as r:
        events = sse(r)
    assert events[-1]["status"] == "ok" and events[-1]["output"] == {"final": "CHANGED!"}
    forked = client.get(f"/api/runs/{events[-1]['run_id']}").json()
    assert forked["parent_run_id"] == run_id and forked["trigger"] == "fork"


CHAT = {
    "name": "Echo chat",
    "settings": {},
    "steps": [
        input_step(mode="chat"),
        code(
            "reply",
            "    import time\n    time.sleep(0.8)\n    text = data['messages'][-1].content\n"
            "    return {'messages': [{'role': 'assistant', 'content': 'echo ' + text}]}",
        ),
        output_step(),
    ],
    "connections": [{"from": "input", "to": "reply"}, {"from": "reply", "to": "output"}],
}


def _chat(policy: str) -> dict:
    return {**CHAT, "name": f"Chat {policy}", "settings": {"double_texting": policy}}


def test_double_texting_queue_runs_messages_in_order(client):
    flow_id = create(client, _chat("queue"))
    a = start(client, flow_id, {"message": "one"}, thread_id="q")
    b = start(client, flow_id, {"message": "two"}, thread_id="q")
    run_a, run_b = wait(client, a), wait(client, b)
    assert run_a["status"] == run_b["status"] == "ok"
    assert run_b["started"] >= run_a["finished"] - 0.01
    texts = [m["content"] for m in run_b["output"]["messages"]]
    assert texts == ["one", "echo one", "two", "echo two"]


def test_double_texting_reject(client):
    flow_id = create(client, _chat("reject"))
    a = start(client, flow_id, {"message": "one"}, thread_id="r")
    response = client.post(
        "/api/runs",
        json={
            "flow_id": flow_id,
            "inputs": {"message": "two"},
            "thread_id": "r",
            "background": True,
        },
    )
    assert response.status_code == 409 and response.json()["kind"] == "busy"
    assert wait(client, a)["status"] == "ok"


def test_double_texting_interrupt_and_rollback(client):
    for policy, expected in (
        ("interrupt", ["one", "two", "echo two"]),
        ("rollback", ["two", "echo two"]),
    ):
        flow_id = create(client, _chat(policy))
        a = start(client, flow_id, {"message": "one"}, thread_id=policy)
        wait(client, a, "running")
        # "interrupt" keeps what the first run saved, so wait until it has saved "one" (its
        # Save Point before the slow reply step); a fixed sleep was flaky on a busy machine.
        deadline = time.time() + 20
        while not any(
            p["run_id"] == a and p["next"] == ["reply"]
            for p in client.get(f"/api/runs/{a}/savepoints").json()
        ):
            assert time.time() < deadline, "the first run never saved its input"
            time.sleep(0.05)
        b = start(client, flow_id, {"message": "two"}, thread_id=policy)
        assert wait(client, a)["status"] == "cancelled"
        run_b = wait(client, b)
        assert run_b["status"] == "ok", run_b
        assert [m["content"] for m in run_b["output"]["messages"]] == expected, policy


def test_flow_run_limit(client):
    spec = {**SLOW, "name": "Limited", "settings": {"max_concurrent_runs": 1}}
    flow_id = create(client, spec)
    a = start(client, flow_id, {"x": "0.6"})
    b = start(client, flow_id, {"x": "0.6"})
    run_a, run_b = wait(client, a), wait(client, b)
    first, second = sorted([run_a, run_b], key=lambda r: r["started"])
    assert second["started"] >= first["finished"] - 0.01


def test_webhook_trigger(client):
    flow_id = create(client, APPROVAL)
    trig = client.post(
        "/api/triggers",
        json={
            "flow_id": flow_id,
            "kind": "webhook",
            "config": {"inputs": {"draft": "{ticket.text}"}},
        },
    ).json()
    url = trig["url"]
    assert client.post(url, json={"ticket": {"text": "x"}}).status_code == 404  # no token
    response = client.post(
        url, json={"ticket": {"text": "from hook"}}, headers={"X-Easychain-Token": trig["token"]}
    )
    assert response.status_code == 202
    run = wait(client, response.json()["run_id"], "paused")
    assert run["trigger"] == "webhook" and run["inputs"] == {"draft": "from hook"}
    client.patch(f"/api/triggers/{trig['id']}", json={"enabled": False})
    assert client.post(f"{url}?token={trig['token']}", json={}).status_code == 409
    assert client.delete(f"/api/triggers/{trig['id']}").json()["deleted"]


def test_upload_trigger(client):
    spec = {
        "name": "Count lines",
        "steps": [
            input_step({"name": "doc", "type": "file"}),
            code("count", "    return {'lines': len(open(data['doc']).read().splitlines())}"),
            output_step("lines"),
        ],
        "connections": [{"from": "input", "to": "count"}, {"from": "count", "to": "output"}],
    }
    flow_id = create(client, spec)
    assert (
        client.post("/api/triggers", json={"flow_id": flow_id, "kind": "upload"}).status_code == 422
    )
    trig = client.post(
        "/api/triggers", json={"flow_id": flow_id, "kind": "upload", "config": {"field": "doc"}}
    ).json()
    response = client.post(
        f"{trig['url']}?filename=../notes.txt&token={trig['token']}", content=b"a\nb\nc\n"
    )
    assert response.status_code == 202
    assert response.json()["file"].endswith("notes.txt") and ".." not in response.json()["file"]
    assert wait(client, response.json()["run_id"])["output"] == {"lines": 3}


def test_schedule_trigger_fires_once_per_slot(client):
    flow_id = create(client, SLOW)
    bad = client.post(
        "/api/triggers", json={"flow_id": flow_id, "kind": "schedule", "config": {"cron": "nope"}}
    )
    assert bad.status_code == 422
    trig = client.post(
        "/api/triggers",
        json={
            "flow_id": flow_id,
            "kind": "schedule",
            "config": {
                "cron": "*/5 * * * *",
                "timezone": "Europe/London",
                "inputs_values": {"x": "0"},
            },
        },
    ).json()
    assert trig["describe"] == "Every 5 minutes"
    assert trig["next_fire_at"] > time.time()
    worker = client.app.state.worker
    portal = client.portal
    started = portal.call(worker.check_schedules, trig["next_fire_at"] + 1)
    again = portal.call(worker.check_schedules, trig["next_fire_at"] + 1)
    assert len(started) == 1 and again == []
    run = wait(client, started[0])
    assert run["trigger"] == "schedule" and run["status"] == "ok"
    moved = client.get("/api/triggers").json()[0]
    assert moved["next_fire_at"] - trig["next_fire_at"] == pytest.approx(300, abs=1)


def test_after_flow_trigger(client):
    first = create(
        client,
        {
            "name": "First",
            "steps": [
                input_step("text"),
                code("up", "    return {'upper': data['text'].upper()}"),
                output_step("upper"),
            ],
            "connections": [{"from": "input", "to": "up"}, {"from": "up", "to": "output"}],
        },
    )
    second = create(
        client,
        {
            "name": "Second",
            "steps": [
                input_step("value"),
                code("wrap", "    return {'wrapped': '[' + data['value'] + ']'}"),
                output_step("wrapped"),
            ],
            "connections": [{"from": "input", "to": "wrap"}, {"from": "wrap", "to": "output"}],
        },
    )
    loop = client.post(
        "/api/triggers",
        json={"flow_id": first, "kind": "after_flow", "config": {"source_flow_id": first}},
    )
    assert loop.status_code == 422
    client.post(
        "/api/triggers",
        json={
            "flow_id": second,
            "kind": "after_flow",
            "config": {"source_flow_id": first, "inputs": {"value": "{upper}"}},
        },
    )
    run_id = start(client, first, {"text": "abc"})
    assert wait(client, run_id)["status"] == "ok"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        follow = client.get(f"/api/runs?flow_id={second}").json()
        if follow and follow[0]["status"] == "ok":
            break
        time.sleep(0.05)
    assert follow[0]["output"] == {"wrapped": "[ABC]"}
    assert follow[0]["parent_run_id"] == run_id and follow[0]["trigger"] == "after_flow"


def test_runs_use_the_version_they_started_with(client, tmp_path):
    flow_id = create(client, SLOW)
    spec = client.get(f"/api/flows/{flow_id}").json()["spec"]
    first = client.put(f"/api/flows/{flow_id}", json=spec).json()["version_id"]
    spec["steps"][1]["settings"]["code"] = "def run(data):\n    return {'y': 2}\n"
    second = client.put(f"/api/flows/{flow_id}", json=spec).json()["version_id"]
    assert first != second
    assert len(client.get(f"/api/flows/{flow_id}/versions").json()) == 2
    run = wait(client, start(client, flow_id, {"x": "0"}))
    assert run["output"] == {"y": 2} and run["version_id"] == second
    old = client.get(f"/api/versions/{first}").json()
    assert "time.sleep" in old["bundle"]["spec"]["steps"][1]["settings"]["code"]


def test_event_stream_picks_up_after_last_event_id(client):
    flow_id = create(client, APPROVAL)
    run_id = start(client, flow_id, {"draft": "x"})
    wait(client, run_id, "paused")
    with client.stream("GET", f"/api/runs/{run_id}/events") as r:
        everything = sse(r)
    middle = everything[len(everything) // 2]["event_id"]
    with client.stream(
        "GET", f"/api/runs/{run_id}/events", headers={"Last-Event-ID": str(middle)}
    ) as r:
        rest = sse(r)
    assert [e["event_id"] for e in rest] == [
        e["event_id"] for e in everything if e["event_id"] > middle
    ]


def test_websocket_follows_a_run_and_can_cancel_it(client):
    flow_id = create(client, SLOW)
    run_id = start(client, flow_id, {"x": "5"})
    with client.websocket_connect(f"/api/runs/{run_id}/ws") as ws:
        seen = []
        while True:
            event = json.loads(ws.receive_text())
            seen.append(event["type"])
            if event["type"] == "step_started" and event.get("step") == "slow":
                ws.send_json({"type": "cancel"})
            if event["type"] == "run_finished":
                assert event["status"] == "cancelled"
                break
    assert "run_queued" in seen and "run_started" in seen


def test_sub_flows_resolve_from_the_workspace(client):
    child = create(
        client,
        {
            "name": "Tidy",
            "steps": [
                input_step("text"),
                code("tidy", "    return {'tidied': data['text'].strip()}"),
                output_step("tidied"),
            ],
            "connections": [{"from": "input", "to": "tidy"}, {"from": "tidy", "to": "output"}],
        },
    )
    parent = {
        "name": "Uses tidy",
        "steps": [
            input_step("text"),
            {"id": "sub", "type": "subflow", "settings": {"flow": child}},
            output_step("tidied"),
        ],
        "connections": [{"from": "input", "to": "sub"}, {"from": "sub", "to": "output"}],
    }
    check = client.post("/api/check", json=parent).json()
    assert not [i for i in check["issues"] if i["level"] == "error"]
    assert "tidy__tidy" in client.post("/api/compile", json=parent).json()["source"].replace(
        "def tidy__tidy", "tidy__tidy"
    )
    parent_id = create(client, parent)
    run = wait(client, start(client, parent_id, {"text": "  padded  "}))
    assert run["output"] == {"tidied": "padded"}
    assert client.get(f"/api/flows/{parent_id}/export").status_code == 200


class _IMAP(socketserver.StreamRequestHandler):
    """Just enough IMAP4rev1 for imaplib: login, select, search, fetch, store, logout."""

    mailbox: list[dict[str, Any]] = []

    def reply(self, line: str) -> None:
        self.wfile.write(line.encode() + b"\r\n")

    def handle(self) -> None:
        self.reply("* OK test IMAP ready")
        while line := self.rfile.readline():
            parts = line.decode().strip().split(" ")
            tag, command, args = parts[0], parts[1].upper(), parts[2:]
            if command == "CAPABILITY":
                self.reply("* CAPABILITY IMAP4rev1")
            elif command == "SELECT":
                self.reply(f"* {len(self.mailbox)} EXISTS")
            elif command == "SEARCH":
                unseen = [str(i + 1) for i, m in enumerate(self.mailbox) if not m["seen"]]
                self.reply("* SEARCH " + " ".join(unseen))
            elif command == "FETCH":
                raw = self.mailbox[int(args[0]) - 1]["raw"]
                self.wfile.write(
                    f"* {args[0]} FETCH (BODY[] {{{len(raw)}}}\r\n".encode() + raw + b")\r\n"
                )
            elif command == "STORE":
                self.mailbox[int(args[0]) - 1]["seen"] = True
                self.reply(f"* {args[0]} FETCH (FLAGS (\\Seen))")
            elif command == "LOGOUT":
                self.reply("* BYE")
                self.reply(f"{tag} OK LOGOUT completed")
                return
            self.reply(f"{tag} OK {command} completed")


@pytest.fixture
def imap() -> Iterator[int]:
    def message(subject: str, body: str) -> bytes:
        return (
            f"From: Ann <ann@example.com>\r\nTo: help@example.com\r\nSubject: {subject}\r\n"
            f"Message-ID: <{subject.replace(' ', '')}@example.com>\r\n\r\n{body}\r\n"
        ).encode()

    _IMAP.mailbox = [
        {"raw": message("Order late", "Where is order 12?"), "seen": False},
        {"raw": message("Old", "Already read"), "seen": True},
        {"raw": message("Refund", "Please refund me."), "seen": False},
    ]
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _IMAP)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


def test_email_trigger_starts_a_run_per_new_message(client, imap):
    spec = {
        "name": "Mail desk",
        "steps": [
            input_step("subject", "text"),
            code(
                "make_label", "    return {'label': data['subject'].upper() + ': ' + data['text']}"
            ),
            output_step("label"),
        ],
        "connections": [
            {"from": "input", "to": "make_label"},
            {"from": "make_label", "to": "output"},
        ],
    }
    flow_id = create(client, spec)
    bad = client.post("/api/triggers", json={"flow_id": flow_id, "kind": "email", "config": {}})
    assert bad.status_code == 422
    trig = client.post(
        "/api/triggers",
        json={
            "flow_id": flow_id,
            "kind": "email",
            "config": {
                "host": "127.0.0.1",
                "port": imap,
                "ssl": False,
                "username": "help@example.com",
                "password": "secret-pass",
                "inputs": {"subject": "{subject}", "text": "{body}"},
            },
        },
    ).json()
    assert trig["config"]["password"] == "••••••"
    worker = client.app.state.worker
    started = client.portal.call(worker.check_schedules, time.time() + 1)
    assert len(started) == 2
    labels = sorted(wait(client, run_id)["output"]["label"] for run_id in started)
    assert labels == ["ORDER LATE: Where is order 12?", "REFUND: Please refund me."]
    assert all(m["seen"] for m in _IMAP.mailbox)
    # The next poll is a minute later, and finds nothing new.
    assert client.portal.call(worker.check_schedules, time.time() + 120) == []


def test_step_over_runs_one_step_at_a_time(client):
    spec = {
        "name": "Three steps",
        "steps": [
            input_step("x"),
            code("one", "    return {'a': 1}"),
            code("two", "    return {'b': 2}"),
            code("three", "    return {'c': 3}"),
            output_step("a", "b", "c"),
        ],
        "connections": [
            {"from": "input", "to": "one"},
            {"from": "one", "to": "two"},
            {"from": "two", "to": "three"},
            {"from": "three", "to": "output"},
        ],
    }
    flow_id = create(client, spec)
    run_id = start(client, flow_id, {"x": "go"}, pause_before=["one"])
    assert wait(client, run_id, "paused")["pending"]["next"] == ["one"]
    for expected_next in (["two"], ["three"]):
        assert (
            client.post(f"/api/runs/{run_id}/continue?step=true&background=true").status_code == 202
        )
        run = wait(client, run_id, "paused", "ok")
        assert run["status"] == "paused" and run["pending"]["next"] == expected_next
    client.post(f"/api/runs/{run_id}/continue?background=true")
    assert wait(client, run_id)["output"] == {"a": 1, "b": 2, "c": 3}
