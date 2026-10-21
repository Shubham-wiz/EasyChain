"""The Deep Agent step and its Helpers: planning, files, Helpers in the trace with their own
usage, approvals (the Deep Agent's, a Helper's, the general helper's) in the Inbox, checks,
and exported code that runs on its own."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path
from typing import Any

import httpx
import pytest

from easychain.compiler import compile_flow
from easychain.compiler.helpers import DEEPAGENTS_REQ
from easychain.compiler.validate import validate
from easychain.export import export_files
from easychain.runtime import RunOptions, run_flow
from easychain.runtime.resources import open_resources
from easychain.runtime.standin import Script
from easychain.server.db import Database, checkpoint_url
from easychain.server.hub import Hub
from easychain.server.store import FlowStore
from easychain.server.worker import Worker
from easychain.spec import dumps_spec
from easychain.testing.fake_openai import FakeOpenAI

from .conftest import input_step, make_spec, output_step


def of(events: list[dict], kind: str) -> list[dict]:
    return [e for e in events if e["type"] == kind]


@pytest.fixture
def shop():
    with FakeOpenAI() as fake:
        httpx.post(f"{fake.url}/effects/reset")
        yield fake


def team_flow(
    base: str,
    *,
    lead: dict | None = None,
    researcher: dict | None = None,
    mode: str = "form",
) -> Any:
    """A Deep Agent with a researcher Helper (who reads pages) and a note it can send itself."""
    inputs = input_step(mode=mode) if mode == "chat" else input_step("topic")
    return make_spec(
        [
            inputs,
            {
                "id": "lead",
                "type": "deep_agent",
                "name": "Lead",
                "settings": {
                    "input": "messages" if mode == "chat" else "topic",
                    "tools": ["send_note"],
                    "helpers": ["researcher"],
                    "save_todos": "plan",
                    **(lead or {}),
                },
            },
            {
                "id": "researcher",
                "type": "helper",
                "name": "Researcher",
                "description": "Reads pages about a topic and replies with facts.",
                "settings": {"tools": ["read_page"], **(researcher or {})},
            },
            {
                "id": "read_page",
                "type": "http_request",
                "description": "Reads a page about a topic.",
                "settings": {"url": base + "/pages/{page_name}", "save_as": "page"},
            },
            {
                "id": "send_note",
                "type": "http_request",
                "description": "Sends a note to the team.",
                "settings": {
                    "method": "POST",
                    "url": base + "/effects",
                    "body": '{"note": "{note}"}',
                    "response": "json",
                    "save_as": "receipt",
                },
            },
            output_step("answer", "files", "plan"),
        ],
        [("input", "lead"), ("lead", "output")],
        data=[{"name": "page_name", "description": "Short name of the page, like bees."}],
    )


PLAN = [
    {"content": "Find facts about bees", "status": "in_progress"},
    {"content": "Write the report", "status": "pending"},
]
RESEARCH = [
    {"call": "write_todos", "args": {"todos": PLAN}},
    {"call": "task", "args": {"subagent_type": "researcher", "description": "Facts about bees"}},
    # The researcher's turns
    {"call": "read_page", "args": {"page_name": "bees"}},
    "Honey bees live in colonies of thousands.",
    # The lead again
    {"call": "write_file", "args": {"file_path": "/report.md", "content": "# Bees\nColonies."}},
    "The report is in /report.md.",
]


async def run(spec: Any, inputs: dict | None = None, **opts: Any) -> tuple[dict, list[dict]]:
    opts.setdefault("stand_in", True)
    return await run_flow(spec, inputs, RunOptions(**opts))


async def test_deep_agent_plans_hands_a_task_to_a_helper_and_keeps_its_files(shop):
    final, events = await run(team_flow(shop.url), {"topic": "Bees"}, script=Script(list(RESEARCH)))
    assert final["status"] == "ok", final
    out = final["output"]
    assert out["answer"] == "The report is in /report.md."
    assert out["files"] == {"/report.md": "# Bees\nColonies."}
    assert out["plan"] == PLAN

    assert of(events, "todos") == [{**of(events, "todos")[0], "step": "lead", "todos": PLAN}]
    files = of(events, "files")
    assert files and files[-1]["files"] == {"/report.md": "# Bees\nColonies."}

    started = of(events, "tool_started")
    assert [(e["tool"], e.get("helper"), e.get("to_helper")) for e in started] == [
        ("write_todos", None, None),
        ("task", None, "researcher"),
        ("read_page", "researcher", None),
        ("write_file", None, None),
    ]
    task = started[1]
    assert started[2]["within"] == task["call_id"], "the Helper's tool call nests under its task"
    assert all(e["step"] == "lead" for e in started)

    finished = {e["call_id"]: e for e in of(events, "tool_finished")}
    reply = finished[task["call_id"]]
    assert reply["result"] == "Honey bees live in colonies of thousands."
    assert reply["to_helper"] == "researcher"
    assert reply["usage"]["input_tokens"] > 0 and reply["model"] == "stand-in"
    assert "Honey bees" in finished[started[2]["call_id"]]["result"]

    step = next(e for e in of(events, "step_finished") if e["step"] == "lead")
    assert step["usage"]["input_tokens"] > reply["usage"]["input_tokens"], (
        "the step counts its own model calls and its Helper's"
    )
    assert final["usage"]["input_tokens"] == step["usage"]["input_tokens"]
    # Only the Deep Agent's own answer streams; the Helper's reply comes back as its task.
    tokens = "".join(e["text"] for e in of(events, "token") if e["step"] == "lead")
    assert "colonies" not in tokens and "report.md" in tokens


async def test_files_are_there_again_in_the_next_turn(shop):
    spec = team_flow(shop.url, mode="chat")
    first = Script(
        [
            {"call": "write_file", "args": {"file_path": "/notes.md", "content": "likes bees"}},
            "Noted.",
        ]
    )
    final, _ = await run(spec, {"message": "Remember I like bees"}, thread_id="t", script=first)
    assert final["status"] == "ok", final
    second = Script([{"call": "read_file", "args": {"file_path": "/notes.md"}}, "You like bees."])
    final, events = await run(spec, {"message": "What do I like?"}, thread_id="t", script=second)
    assert final["status"] == "ok", final
    read = next(e for e in of(events, "tool_finished") if e["tool"] == "read_file")
    assert "likes bees" in read["result"]
    assert final["output"]["files"] == {"/notes.md": "likes bees"}


async def test_approval_inside_a_deep_agent_waits_in_the_inbox(shop):
    spec = team_flow(shop.url, lead={"addons": {"approve_tools": ["write_file"]}})
    write = {"call": "write_file", "args": {"file_path": "/a.md", "content": "draft"}}
    for answer, expected in (("approve", {"/a.md": "draft"}), ("reject", {})):
        opts = RunOptions(stand_in=True, thread_id=f"t-{answer}", script=Script([write, "Done."]))
        final, _ = await run_flow(spec, {"topic": "x"}, opts)
        assert final["status"] == "paused", final
        waiting = final["interrupts"][0]
        assert waiting["step"] == "lead"
        assert waiting["request"]["kind"] == "approve_tool"
        assert waiting["request"]["actions"] == [
            {"tool": "write_file", "args": {"file_path": "/a.md", "content": "draft"}}
        ]
        opts.action, opts.resume = "resume", {waiting["id"]: {"action": answer}}
        final, _ = await run_flow(spec, None, opts)
        assert final["status"] == "ok", final
        assert final["output"]["files"] == expected


async def test_a_helpers_approval_pauses_the_run_and_its_tool_runs_once(shop):
    spec = team_flow(
        shop.url,
        researcher={"tools": ["send_note"], "addons": {"approve_tools": ["send_note"]}},
        lead={"tools": []},
    )
    script = Script(
        [
            {"call": "task", "args": {"subagent_type": "researcher", "description": "Tell them"}},
            {"call": "send_note", "args": {"note": "hello"}},
            "Sent.",
            "The researcher sent it.",
        ]
    )
    opts = RunOptions(stand_in=True, thread_id="helper-approval", script=script)
    final, events = await run_flow(spec, {"topic": "x"}, opts)
    assert final["status"] == "paused", final
    waiting = final["interrupts"][0]
    assert waiting["step"] == "lead" and waiting["request"]["actions"][0]["tool"] == "send_note"
    assert of(events, "step_paused")[0]["step"] == "lead"
    assert httpx.get(f"{shop.url}/effects").json()["applied"] == []

    opts.action, opts.resume = "resume", {waiting["id"]: {"action": "approve"}}
    final, events = await run_flow(spec, None, opts)
    assert final["status"] == "ok", final
    assert final["output"]["answer"] == "The researcher sent it."
    applied = httpx.get(f"{shop.url}/effects").json()["applied"]
    assert [a["body"] for a in applied] == [{"note": "hello"}]
    sent = next(e for e in of(events, "tool_finished") if e["tool"] == "send_note")
    assert sent["helper"] == "researcher"


async def test_the_general_helper_has_the_deep_agents_approvals(shop):
    spec = team_flow(shop.url, lead={"addons": {"approve_tools": ["send_note"]}})
    script = Script(
        [
            {
                "call": "task",
                "args": {"subagent_type": "general-purpose", "description": "Send a note"},
            },
            {"call": "send_note", "args": {"note": "sneaky"}},
            "Sent.",
        ]
    )
    final, events = await run(spec, {"topic": "x"}, script=script, thread_id="general")
    assert final["status"] == "paused", final
    assert final["interrupts"][0]["request"]["actions"][0]["tool"] == "send_note"
    assert httpx.get(f"{shop.url}/effects").json()["applied"] == []
    task = next(e for e in of(events, "tool_started") if e["tool"] == "task")
    assert task["to_helper"] == "general-purpose"


async def test_a_helpers_approval_reaches_the_inbox_and_the_answer_resumes_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shop: FakeOpenAI
):
    # A worker runs it with a "real" model: the fake OpenAI server, with scripted turns.
    monkeypatch.setenv("OPENAI_BASE_URL", shop.base_url)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-inbox")
    spec = team_flow(
        shop.url,
        researcher={"tools": ["send_note"], "addons": {"approve_tools": ["send_note"]}},
        lead={"tools": []},
    )
    folder = tmp_path / "flows"
    folder.mkdir()
    (folder / "team.flow.yaml").write_text(dumps_spec(spec), encoding="utf-8")
    url = f"sqlite:///{tmp_path / 'runs.db'}"
    db = await Database.connect(url)
    resources = await open_resources(checkpoint_url(url))
    hub = Hub(db, resources, FlowStore(folder))
    worker = Worker(hub, name="test-worker")

    async def work() -> None:
        while job := await db.lease(worker.name, 30):
            await worker._process(job)

    try:
        httpx.post(
            f"{shop.url}/__script",
            json=[
                {"call": "task", "args": {"subagent_type": "researcher", "description": "Tell"}},
                {"call": "send_note", "args": {"note": "hello"}},
                "Sent.",
                "The researcher told them.",
            ],
        )
        run_id = await hub.start_run(hub.flows.get("team"), flow_id="team", inputs={"topic": "x"})
        await work()
        assert (await db.get_run(run_id))["status"] == "paused"
        [item] = [i for i in await db.list_inbox(status="open") if i["run_id"] == run_id]
        assert item["step"] == "lead"
        assert item["request"]["kind"] == "approve_tool"
        assert item["request"]["actions"] == [{"tool": "send_note", "args": {"note": "hello"}}]
        assert httpx.get(f"{shop.url}/effects").json()["applied"] == []

        await hub.answer(item["id"], {"action": "approve"})
        await work()
        run = await db.get_run(run_id)
        assert run["status"] == "ok", run
        assert run["output"]["answer"].endswith("The researcher told them.")
        applied = httpx.get(f"{shop.url}/effects").json()["applied"]
        assert [a["body"] for a in applied] == [{"note": "hello"}]
    finally:
        await resources.aclose()
        await db.close()


def test_generated_code_creates_a_deep_agent_with_its_helpers(shop):
    compiled = compile_flow(team_flow(shop.url))
    src = compiled.source
    assert "agent = create_deep_agent(" in src
    middleware = (
        "middleware=[TodoListMiddleware(), FilesystemMiddleware(tools=FILE_TOOLS), *addons]"
    )
    assert middleware in src
    assert "general_helper(model, tools, addons)" in src
    assert "ToolErrorMiddleware(tell_agent)" in src.split("def lead(")[1]
    assert "researcher_helper(data)" in src
    assert '"files": agent_files(data.get("files"))' in src
    assert '"plan": result.get("todos") or []' in src
    assert DEEPAGENTS_REQ in compiled.requirements
    assert '"execute"' not in src, "no shell for the agent"


async def test_a_test_set_checks_the_helpers_and_the_files(shop):
    from easychain.testsets import run_case

    case = {
        "name": "Writes the report",
        "inputs": {"topic": "Bees"},
        "script": RESEARCH,
        "expect": {
            "helpers": ["researcher"],
            "tools": ["write_todos", "read_page", "write_file"],
            "files": {"/report.md": {"contains": "Colonies"}},
        },
    }
    result = await run_case(team_flow(shop.url), case, True)
    assert result.passed, result.failures
    case["expect"] = {"helpers": ["editor"], "files": {"/report.md": "x", "/missing.md": "y"}}
    result = await run_case(team_flow(shop.url), case, True)
    assert result.failures == [
        "helpers: expected a task for 'editor' (got ['researcher'])",
        "files /report.md: expected 'x', got '# Bees\\nColonies.'",
        "files: expected the file '/missing.md'",
    ]


def test_deepagents_is_pinned_to_the_installed_version():
    assert f"deepagents=={version('deepagents')}" == DEEPAGENTS_REQ


def codes(spec: Any) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for issue in validate(spec):
        found.setdefault(issue.code, []).append(issue.step or "")
    return found


def test_checks_for_deep_agents_and_helpers(shop):
    assert "unreachable" not in codes(team_flow(shop.url))
    assert "agent_no_tools" not in codes(team_flow(shop.url, lead={"tools": []}))

    spec = team_flow(shop.url)
    spec.steps.append(
        spec.steps[2].model_copy(update={"id": "spare", "description": ""}, deep=True)
    )
    found = codes(spec)
    assert found["helper_unused"] == ["spare"]
    assert found["helper_no_description"] == ["spare"]

    found = codes(team_flow(shop.url, lead={"helpers": ["researcher", "missing", "send_note"]}))
    assert found["helper_missing"] == ["lead", "lead"]

    data = team_flow(shop.url).model_dump(by_alias=True)
    data["steps"].append({**data["steps"][1], "id": "second", "name": "Second"})
    data["connections"].append({"from": "input", "to": "second"})
    found = codes(make_spec(data["steps"], data["connections"], data=data["data"]))
    assert found["helper_shared"] == ["second"]

    spec = team_flow(shop.url)
    spec.connections.append(spec.connections[0].model_copy(update={"target": "researcher"}))
    assert "helper_connected" in codes(spec)

    found = codes(team_flow(shop.url, lead={"save_todos": "answer"}))
    assert found["deep_agent_same_field"] == ["lead"]

    ok = team_flow(
        shop.url,
        lead={"addons": {"approve_tools": ["write_file", "task", "send_note"]}},
        researcher={"addons": {"approve_tools": ["edit_file", "read_page"]}},
    )
    assert "approve_unknown_tool" not in codes(ok)
    bad = team_flow(shop.url, researcher={"addons": {"approve_tools": ["send_note"]}})
    assert codes(bad)["approve_unknown_tool"] == ["researcher"]

    found = codes(team_flow(shop.url, researcher={"instructions": "Write for {reader}."}))
    assert "researcher" in found["missing_field"]


def test_a_helpers_tool_arguments_are_what_its_deep_agent_lacks(shop):
    compiled = compile_flow(team_flow(shop.url))
    assert "def run_tool(page_name: Annotated[str, " in compiled.source
    assert compiled.analysis.tool_of["read_page"] == "lead"
    assert compiled.analysis.helper_of == {"researcher": "lead"}


def test_exported_deep_agent_runs_on_its_own(tmp_path: Path):
    with FakeOpenAI() as fake:
        spec = team_flow(fake.url)
        out = tmp_path / "export"
        out.mkdir()
        files = export_files(spec)
        for name, content in files.items():
            (out / name).write_text(content, encoding="utf-8")
        assert DEEPAGENTS_REQ in files["requirements.txt"]
        script = next(name for name in files if name.endswith(".py"))
        httpx.post(f"{fake.url}/__script", json=RESEARCH)
        clean = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("OPENAI", "ANTHROPIC", "EASYCHAIN"))
        }
        clean.update(
            OPENAI_API_KEY="sk-test-export",
            OPENAI_BASE_URL=fake.base_url,
            PYTHONIOENCODING="utf-8",
        )
        hide = (
            "import sys; sys.modules['easychain'] = None; import runpy; "
            "sys.argv = sys.argv[1:]; runpy.run_path(sys.argv[0], run_name='__main__')"
        )
        result = subprocess.run(
            [sys.executable, "-c", hide, script, json.dumps({"topic": "Bees"})],
            cwd=out,
            env=clean,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        )
        assert result.returncode == 0, result.stderr
        output = json.loads(result.stdout)
        assert output["files"] == {"/report.md": "# Bees\nColonies."}
        assert output["plan"] == PLAN
        assert output["answer"] == "The report is in /report.md."
