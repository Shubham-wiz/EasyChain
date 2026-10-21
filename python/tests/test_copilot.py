"""The describe-it copilot (Phase 4.6): proposals for ten written requests validate with no errors
(the stand-in AI plays the model's replies), problems go back to the model until it fixes them,
a change to a flow becomes separate changes that keep the canvas, the stand-in's example without
a key, the model it picks, and the API."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from easychain.compiler.validate import validate
from easychain.copilot import copilot_model, diff, propose
from easychain.runtime.standin import Script
from easychain.server.app import create_app
from easychain.spec import parse_spec

from .conftest import input_step, make_spec, output_step


def reply(summary: str, flow: str, tests: str = "") -> str:
    body = f"summary: {summary}\nflow:\n" + "\n".join(
        "  " + line for line in flow.strip().splitlines()
    )
    if tests:
        body += "\ntests:\n" + "\n".join("  " + line for line in tests.strip().splitlines())
    return f"Here it is.\n\n```yaml\n{body}\n```"


REQUESTS: list[tuple[str, str]] = [
    (
        "Summarise a web page in three bullet points",
        """
name: Summarise a page
steps:
- {id: input, type: input, settings: {fields: [{name: url, example: "https://example.com"}]}}
- {id: fetch, type: http_request, name: Fetch the page, settings: {url: "{url}", save_as: page}}
- id: write_prompt
  type: instructions
  settings: {system: You summarise pages., user: "Three bullet points about:\\n\\n{page}"}
- {id: summarise, type: ai_model, settings: {save_as: summary}}
- {id: output, type: output, settings: {fields: [summary]}}
connections:
- {from: input, to: fetch}
- {from: fetch, to: write_prompt}
- {from: write_prompt, to: summarise}
- {from: summarise, to: output}
""",
    ),
    (
        "Translate a text into French",
        """
name: Translate to French
steps:
- {id: input, type: input, settings: {fields: [{name: text}]}}
- id: ask
  type: instructions
  settings: {system: You translate into French., user: "{text}"}
- {id: translate, type: ai_model, settings: {save_as: french}}
- {id: output, type: output, settings: {fields: [french]}}
connections:
- {from: input, to: ask}
- {from: ask, to: translate}
- {from: translate, to: output}
""",
    ),
    (
        "Sort customer feedback into complaints and praise, and draft a reply to each",
        """
name: Feedback replies
steps:
- {id: input, type: input, settings: {fields: [{name: feedback}]}}
- id: kind
  type: decision
  settings:
    mode: ai
    input: feedback
    exits: [{label: Complaint, description: the customer is unhappy}]
    otherwise: Praise
- {id: sorry, type: instructions, settings: {user: "Apologise for: {feedback}"}}
- {id: thanks, type: instructions, settings: {user: "Thank them for: {feedback}"}}
- {id: write, type: ai_model, settings: {save_as: reply}}
- {id: output, type: output, settings: {fields: [reply]}}
connections:
- {from: input, to: kind}
- {from: kind, to: sorry, exit: Complaint}
- {from: kind, to: thanks, exit: Praise}
- {from: sorry, to: write}
- {from: thanks, to: write}
- {from: write, to: output}
""",
    ),
    (
        "A friendly chat assistant",
        """
name: Chat assistant
steps:
- {id: input, type: input, settings: {mode: chat}}
- {id: reply, type: ai_model, settings: {prompt: messages, save_as: messages}}
- {id: output, type: output, settings: {fields: []}}
connections:
- {from: input, to: reply}
- {from: reply, to: output}
""",
    ),
    (
        "An agent that looks up an order by its number",
        """
name: Order agent
data:
- {name: order_id, description: The order number}
steps:
- {id: input, type: input, settings: {fields: [{name: question}]}}
- id: helper
  type: agent
  settings: {input: question, tools: [lookup]}
- id: lookup
  type: http_request
  description: Looks up an order by its number.
  settings: {url: "https://shop.example.com/orders/{order_id}", response: json, save_as: order}
- {id: output, type: output, settings: {fields: [answer]}}
connections:
- {from: input, to: helper}
- {from: helper, to: output}
""",
    ),
    (
        "Summarise every page in a list of links",
        """
name: Many pages
steps:
- {id: input, type: input, settings: {fields: [{name: urls, type: list}]}}
- {id: each, type: for_each, settings: {items: urls, item_name: url, save_as: pages}}
- {id: fetch, type: http_request, settings: {url: "{url}", save_as: page}}
- {id: ask, type: instructions, settings: {user: "One line per page:\\n\\n{pages}"}}
- {id: summarise, type: ai_model, settings: {save_as: summary}}
- {id: output, type: output, settings: {fields: [summary]}}
connections:
- {from: input, to: each}
- {from: each, to: fetch, exit: Each item}
- {from: each, to: ask, exit: When done}
- {from: ask, to: summarise}
- {from: summarise, to: output}
""",
    ),
    (
        "Draft an email, ask me to approve it, then send it",
        """
name: Approve and send
steps:
- {id: input, type: input, settings: {fields: [{name: topic}]}}
- {id: ask, type: instructions, settings: {user: "Write a short email about {topic}"}}
- {id: draft, type: ai_model, settings: {save_as: email}}
- id: approve
  type: ask_human
  settings: {kind: approve, question: "Send this email?", show: [email]}
- id: send
  type: http_request
  settings: {method: POST, url: "https://mail.example.com/send", body: '{"text": "{email}"}', response: json, save_as: sent}
- {id: output, type: output, settings: {fields: [email, sent]}}
connections:
- {from: input, to: ask}
- {from: ask, to: draft}
- {from: draft, to: approve}
- {from: approve, to: send, exit: Approved}
- {from: approve, to: output, exit: Rejected}
- {from: send, to: output}
""",
    ),
    (
        "Count the words in a text with Python",
        """
name: Word count
steps:
- {id: input, type: input, settings: {fields: [{name: text}]}}
- id: count
  type: code
  settings:
    code: |
      def run(data):
          \"\"\"Count the words.\"\"\"
          return {"words": len(data["text"].split())}
- {id: output, type: output, settings: {fields: [words]}}
connections:
- {from: input, to: count}
- {from: count, to: output}
""",
    ),
    (
        "Research a topic with a Deep Agent and a researcher helper, and write a report",
        """
name: Research
steps:
- {id: input, type: input, settings: {fields: [{name: topic}]}}
- id: lead
  type: deep_agent
  settings: {input: topic, helpers: [researcher], instructions: "Research {topic} and write /report.md."}
- id: researcher
  type: helper
  description: Finds facts about a topic.
- {id: output, type: output, settings: {fields: [answer, files]}}
connections:
- {from: input, to: lead}
- {from: lead, to: output}
""",
    ),
    (
        "Keep asking an AI to improve a draft three times",
        """
name: Improve a draft
data:
- {name: rounds, type: number}
steps:
- {id: input, type: input, settings: {fields: [{name: draft}]}}
- {id: ask, type: instructions, settings: {user: "Improve this:\\n\\n{draft}"}}
- {id: improve, type: ai_model, settings: {save_as: draft}}
- id: again
  type: jump
  settings:
    updates: [{field: rounds, expression: "(rounds or 0) + 1"}]
    exits: [{label: Again, when: {expression: "rounds < 3"}}]
    otherwise: Done
- {id: output, type: output, settings: {fields: [draft, rounds]}}
connections:
- {from: input, to: ask}
- {from: ask, to: improve}
- {from: improve, to: again}
- {from: again, to: ask, exit: Again}
- {from: again, to: output, exit: Done}
""",
    ),
]


@pytest.mark.parametrize("request_text,flow", REQUESTS, ids=[r[0][:40] for r in REQUESTS])
def test_proposals_for_ten_requests_validate_with_no_errors(request_text: str, flow: str):
    tests = "- {name: It answers, inputs: {}, expect: {status: ok}}"
    script = Script([reply(f"I made a flow: {request_text}.", flow, tests)])
    proposal = propose(request_text, None, model="openai:gpt-4.1", stand_in=True, script=script)
    assert proposal.ok, proposal.problems
    assert proposal.attempts == 1
    assert [i for i in validate(proposal.spec) if i.level == "error"] == []
    ops = {c["op"] for c in proposal.changes}
    assert {"add_step", "add_connection"} <= ops
    assert proposal.test_cases == [{"name": "It answers", "inputs": {}, "expect": {"status": "ok"}}]
    assert all(sid in proposal.spec.canvas.steps for sid in proposal.spec.step_map())


def test_problems_go_back_to_the_model_until_it_fixes_them():
    broken = """
name: Broken
steps:
- {id: input, type: input, settings: {fields: [{name: text}]}}
- {id: ask, type: decision, settings: {exits: [{label: Yes, when: {field: nothing, op: is_true}}]}}
- {id: output, type: output, settings: {fields: [text]}}
connections:
- {from: input, to: ask}
- {from: ask, to: output}
"""
    fixed = REQUESTS[1][1]
    script = Script(["Not YAML at all: [", reply("broken", broken), reply("Fixed it.", fixed)])
    proposal = propose("Translate", None, stand_in=True, script=script)
    assert proposal.ok and proposal.attempts == 3
    assert proposal.summary == "Fixed it."

    script = Script([reply("broken", broken)] * 3)
    proposal = propose("Translate", None, stand_in=True, script=script)
    assert not proposal.ok and proposal.attempts == 3
    assert any("exit" in p.lower() for p in proposal.problems), proposal.problems


def current_flow() -> Any:
    spec = make_spec(
        [
            input_step("text"),
            {"id": "ask", "type": "instructions", "settings": {"user": "{text}"}},
            {"id": "reply", "type": "ai_model", "settings": {"save_as": "answer"}},
            output_step("answer"),
        ],
        [("input", "ask"), ("ask", "reply"), ("reply", "output")],
        name="Answer",
    )
    data = spec.model_dump(by_alias=True)
    data["canvas"] = {"steps": {s: {"x": i * 280, "y": 100} for i, s in enumerate(spec.step_map())}}
    return parse_spec(data)


def test_a_change_to_a_flow_is_separate_changes_that_keep_the_canvas():
    current = current_flow()
    changed = yaml.safe_load(
        """
name: Answer
data:
- {name: short, description: The answer in one line}
steps:
- {id: input, type: input, settings: {fields: [{name: text}]}}
- {id: ask, type: instructions, settings: {user: "{text}", system: Be brief.}}
- {id: reply, type: ai_model, settings: {save_as: answer}}
- {id: shorten, type: ai_model, name: Shorten, settings: {prompt: answer, save_as: short}}
- {id: output, type: output, settings: {fields: [answer, short]}}
connections:
- {from: input, to: ask}
- {from: ask, to: reply}
- {from: reply, to: shorten}
- {from: shorten, to: output}
"""
    )
    import json

    flow_yaml = yaml.safe_dump(changed)
    script = Script(
        [f"```yaml\nsummary: Added a shorter answer.\nflow:\n{_indent(flow_yaml)}\n```"]
    )
    proposal = propose("Also give a one-line answer", current, stand_in=True, script=script)
    assert proposal.ok, proposal.problems
    labels = [c["label"] for c in proposal.changes]
    assert labels == [
        "Add the Flow Data field `short`",
        "Change “ask” (settings)",
        "Add the AI Model step “Shorten”",
        "Change “output” (settings)",
        "Move “output” right, to make room",
        "Connect “reply” to “Shorten”",
        "Connect “Shorten” to “output”",
        "Remove the connection from “reply” to “output”",
    ]
    canvas = proposal.spec.canvas.steps
    assert canvas["reply"].x == 560 and canvas["shorten"].x == 860, "new steps go right of theirs"
    assert canvas["output"].x == 1160 and canvas["output"].y == 100, "later steps make room"
    move = next(c for c in proposal.changes if c["op"] == "move_steps")
    assert move["positions"] == {"output": {"x": 1160, "y": 100}}
    assert json.dumps(proposal.to_dict(), default=str)


def test_new_steps_never_land_on_old_ones():
    current = current_flow()
    data = current.model_dump(by_alias=True)
    data["steps"].insert(2, {"id": "draft", "type": "ai_model", "settings": {"save_as": "draft"}})
    data["steps"].append(
        {
            "id": "lookup",
            "type": "http_request",
            "description": "Looks it up.",
            "settings": {"url": "https://example.com"},
        }
    )
    data["steps"].append(
        {"id": "helper", "type": "agent", "settings": {"input": "text", "tools": ["lookup"]}}
    )
    data["connections"] = [
        {"from": "input", "to": "ask"},
        {"from": "ask", "to": "draft"},
        {"from": "draft", "to": "helper"},
        {"from": "helper", "to": "reply"},
        {"from": "reply", "to": "output"},
    ]
    from easychain.copilot import place_new_steps

    placed = place_new_steps(current, parse_spec(data))
    boxes = list(placed.values())
    for i, a in enumerate(boxes):
        for b in boxes[i + 1 :]:
            assert abs(a["x"] - b["x"]) >= 260 or abs(a["y"] - b["y"]) >= 140, placed
    assert placed["lookup"]["y"] > placed["helper"]["y"], "a new tool goes under its agent"
    assert [placed[s]["x"] for s in ("input", "ask")] == [0, 280], "steps before stay put"
    xs = [placed[s]["x"] for s in ("ask", "draft", "helper", "reply", "output")]
    assert xs == sorted(xs), placed


def test_a_removed_step_names_the_step_it_removes():
    current = current_flow()
    data = current.model_dump(by_alias=True)
    data["steps"] = [s for s in data["steps"] if s["id"] != "ask"]
    data["connections"] = [
        {"from": "input", "to": "reply"},
        {"from": "reply", "to": "output"},
    ]
    changes = diff(current, parse_spec(data))
    removal = next(c for c in changes if c["op"] == "remove_step")
    assert removal["step"] == "ask" and removal["id"].startswith("c")
    assert removal["label"] == "Remove the step “ask”"
    assert len({c["id"] for c in changes}) == len(changes)


def _indent(text: str) -> str:
    return "\n".join("  " + line for line in text.splitlines())


@pytest.mark.parametrize(
    "request_text,template",
    [
        ("Summarise the page at a URL", "summarise-url"),
        ("A chat assistant that remembers the conversation", "chat-assistant"),
        ("Reply to customer feedback, complaints and praise", "reply-to-feedback"),
        ("Summarise several pages from a list of links", "summarise-many-pages"),
        ("Ask a human to approve a reply before it is sent", "approve-reply"),
        ("Answer questions from our help centre documents with citations", "support-bot"),
        ("Answer questions about sales from the database with SQL", "sql-analyst"),
        ("A supervisor that hands requests to specialist agents", "supervisor-team"),
        ("Hand the traveller between flight and hotel desks in one chat", "handoff-chat"),
        ("Plan the steps, then carry out each step with an agent", "plan-and-execute"),
    ],
)
def test_without_a_key_the_stand_in_shows_the_closest_template(request_text: str, template: str):
    from easychain.templates import load_template

    proposal = propose(request_text, None, stand_in=True)
    assert proposal.ok and proposal.model == "stand-in"
    assert proposal.spec.name == load_template(template).name
    assert [i for i in validate(proposal.spec) if i.level == "error"] == []
    assert proposal.summary.startswith("[Stand-in copilot]")


def test_the_stand_in_can_not_fix_problems():
    proposal = propose("", current_flow(), stand_in=True, issue={"message": "x", "step": "ask"})
    assert not proposal.ok and "fix button" in proposal.summary


def test_the_copilot_uses_a_model_there_is_a_key_for(monkeypatch: pytest.MonkeyPatch):
    assert copilot_model("ollama:llama3.2") == "ollama:llama3.2"
    assert copilot_model() is None
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert copilot_model() == "openai:gpt-4.1"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert copilot_model() == "anthropic:claude-sonnet-5-5"


def test_the_copilot_api(tmp_path: Path):
    app = create_app(
        home=tmp_path / "home", workspace=tmp_path / "flows", static_dir=tmp_path / "x"
    )
    with TestClient(app) as client:
        assert client.get("/api/settings/copilot").json() == {"model": None, "using": None}
        saved = client.put("/api/settings/copilot", json={"model": "openai:gpt-4.1"}).json()
        assert saved == {"model": "openai:gpt-4.1", "using": "openai:gpt-4.1"}
        client.put("/api/settings/copilot", json={"model": None})
        body = {"request": "Summarise the page at a URL", "spec": None}
        proposal = client.post("/api/copilot", json=body).json()
        assert proposal["ok"] and proposal["model"] == "stand-in"
        assert proposal["changes"] and proposal["spec"]["name"] == "Summarise a URL"
        bad = client.post("/api/copilot", json={"request": "", "spec": None})
        assert bad.status_code == 422

        flow_id = client.post("/api/flows", json={"name": "Mine"}).json()["id"]
        cases = [{"name": "Works", "inputs": {"url": "https://example.com"}}]
        added = client.post(f"/api/flows/{flow_id}/tests", json={"cases": cases}).json()
        assert added == {"added": 1, "total": 1}
        client.post(f"/api/flows/{flow_id}/tests", json={"cases": cases})
        saved_set = yaml.safe_load((tmp_path / "flows" / f"{flow_id}.tests.yaml").read_text())
        assert saved_set["flow"] == f"{flow_id}.flow.yaml"
        assert [c["name"] for c in saved_set["cases"]] == ["Works", "Works (2)"]
