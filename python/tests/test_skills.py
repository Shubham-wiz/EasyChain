"""The Skills library (Agent Skills format) and Deep Agents and Helpers that use it: saving and
checking skills, built-in ones, the API, the checks, the generated code (skills ship inside
exports), and the stand-in AI picking the skill that matches the task."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from easychain.compiler import compile_flow
from easychain.compiler.validate import validate
from easychain.runtime import RunOptions, run_flow
from easychain.runtime.runner import compile_cached
from easychain.runtime.standin import Script
from easychain.server.app import create_app
from easychain.skills import SkillError, SkillLibrary, check_skill, use_library

from .conftest import input_step, make_spec, output_step


def skill_md(name: str, description: str, body: str = "Do it well.") -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n\n{body}\n"


@pytest.fixture
def lib(tmp_path: Path) -> SkillLibrary:
    library = SkillLibrary(tmp_path / "skills")
    use_library(library)
    return library


def of(events: list[dict], kind: str) -> list[dict]:
    return [e for e in events if e["type"] == kind]


def team(lead: dict | None = None, helper: dict | None = None) -> Any:
    return make_spec(
        [
            input_step("topic"),
            {
                "id": "lead",
                "type": "deep_agent",
                "settings": {"input": "topic", "helpers": ["researcher"], **(lead or {})},
            },
            {
                "id": "researcher",
                "type": "helper",
                "description": "Finds facts about a topic.",
                "settings": helper or {},
            },
            output_step("answer", "files"),
        ],
        [("input", "lead"), ("lead", "output")],
    )


def test_a_skill_is_checked_before_it_is_saved(lib: SkillLibrary):
    good = {"SKILL.md": skill_md("tone-of-voice", "How to write in our tone of voice.")}
    assert check_skill("tone-of-voice", good).description == "How to write in our tone of voice."
    problems = {
        "Bad Name": (good, "lowercase letters"),
        "no-skill-md": ({"notes.md": "x"}, "needs a SKILL.md"),
        "no-front": ({"SKILL.md": "# Just text"}, "front matter"),
        "other-name": (good, "must be the skill's name"),
        "no-description": ({"SKILL.md": "---\nname: no-description\n---\nx"}, "description"),
        "escape": ({**good, "../outside.md": "x"}, "isn't a file name inside the skill"),
        "absolute": ({**good, "/etc/x": "x"}, "isn't a file name inside the skill"),
    }
    for name, (files, message) in problems.items():
        files = {
            k: v.replace("tone-of-voice", name) if name not in ("other-name",) else v
            for k, v in files.items()
        }
        with pytest.raises(SkillError, match=message):
            check_skill(name, files)
    with pytest.raises(SkillError, match="at most"):
        check_skill(
            "many", {"SKILL.md": skill_md("many", "x"), **{f"{i}.md": "" for i in range(40)}}
        )


def test_the_library_saves_lists_and_deletes_skills_over_the_built_in_ones(lib: SkillLibrary):
    names = [s.name for s in lib.list()]
    assert {"research-report", "source-notes"} <= set(names), "the built-in skills"
    assert lib.get("research-report").built_in

    files = {
        "SKILL.md": skill_md("tone-of-voice", "How to write in our tone of voice."),
        "examples/good.md": "Short. Friendly.",
    }
    saved = lib.save("tone-of-voice", files)
    assert saved.summary()["files"] == ["SKILL.md", "examples/good.md"]
    assert lib.get("tone-of-voice").files == files
    assert (lib.folder / "tone-of-voice" / "examples" / "good.md").read_text() == "Short. Friendly."

    # A workspace skill with a built-in's name replaces it; deleting it brings the built-in back.
    mine = {"SKILL.md": skill_md("research-report", "Our own way to write reports.")}
    lib.save("research-report", mine)
    replaced = lib.get("research-report")
    assert not replaced.built_in and replaced.replaces_built_in
    assert next(s for s in lib.list() if s.name == "research-report").replaces_built_in
    assert lib.delete("research-report")
    assert lib.get("research-report").built_in

    # Saving again replaces the folder: a file left out is gone.
    lib.save("tone-of-voice", {"SKILL.md": files["SKILL.md"]})
    assert list(lib.get("tone-of-voice").files) == ["SKILL.md"]
    assert lib.delete("tone-of-voice") and lib.get("tone-of-voice") is None
    assert not lib.delete("source-notes"), "built-in skills aren't deleted"


def test_the_skills_api(tmp_path: Path):
    app = create_app(
        home=tmp_path / "home", workspace=tmp_path / "flows", static_dir=tmp_path / "x"
    )
    with TestClient(app) as client:
        listed = client.get("/api/skills").json()["skills"]
        assert any(s["name"] == "research-report" and s["built_in"] for s in listed)
        files = {"SKILL.md": skill_md("triage", "How to sort incoming email by urgency.")}
        saved = client.put("/api/skills/triage", json={"files": files})
        assert saved.status_code == 200 and saved.json()["name"] == "triage"
        assert (tmp_path / "flows" / "skills" / "triage" / "SKILL.md").exists()
        assert client.get("/api/skills/triage").json()["files"] == files
        bad = client.put("/api/skills/triage", json={"files": {"SKILL.md": "no front matter"}})
        assert bad.status_code == 422 and "front matter" in bad.json()["message"]
        assert client.put("/api/skills/triage", json={}).status_code == 422
        assert client.get("/api/skills/nothing").status_code == 404
        built_in = client.delete("/api/skills/source-notes")
        assert built_in.status_code == 404 and "Built-in" in built_in.json()["message"]
        assert client.delete("/api/skills/triage").json() == {"ok": True, "built_in": None}
        copy = {"SKILL.md": skill_md("source-notes", "Our notes.")}
        client.put("/api/skills/source-notes", json={"files": copy})
        back = client.delete("/api/skills/source-notes").json()
        assert back["built_in"]["built_in"] is True


def test_a_missing_skill_is_a_problem_with_a_fix(lib: SkillLibrary):
    issues = validate(
        team(lead={"skills": ["research-report", "gone"]}, helper={"skills": ["nope"]})
    )
    missing = [i for i in issues if i.code == "skill_missing"]
    assert [(i.step, i.message) for i in missing] == [
        ("lead", "There's no skill “gone” in the Skills library."),
        ("researcher", "There's no skill “nope” in the Skills library."),
    ]
    assert missing[0].fix.params == {"setting": "skills", "value": "gone"}


def test_skills_ship_inside_the_generated_code(lib: SkillLibrary):
    lib.save(
        "tone", {"SKILL.md": skill_md("tone", "How we write."), "words.md": "Use plain words."}
    )
    spec = team(lead={"skills": ["research-report"]}, helper={"skills": ["tone", "source-notes"]})
    source = compile_flow(spec).source
    assert "LEAD_SKILLS = {" in source
    for path in (
        "/skills/lead/research-report/SKILL.md",
        "/skills/lead/research-report/outline.md",
        "/skills/researcher/tone/SKILL.md",
        "/skills/researcher/tone/words.md",
        "/skills/researcher/source-notes/SKILL.md",
    ):
        assert f'"{path}"' in source
    assert "Use plain words." in source
    assert 'skills=["/skills/lead/"]' in source
    assert 'general_helper(model, tools, addons, ["/skills/lead/"])' in source
    assert '"skills": ["/skills/researcher/"]' in source
    assert '"files": agent_files(data.get("files"), LEAD_SKILLS)' in source
    # A skill changed in the library compiles the flow again.
    first = compile_cached(spec).source
    lib.save("tone", {"SKILL.md": skill_md("tone", "How we write."), "words.md": "Be brief."})
    again = compile_cached(spec).source
    assert first != again and "Be brief." in again


async def test_the_stand_in_reads_the_skill_that_matches_the_task(lib: SkillLibrary):
    spec = team(lead={"skills": ["research-report", "source-notes"], "tools": []})
    final, events = await run_flow(
        spec,
        {"topic": "Research sea otters and write a report"},
        RunOptions(stand_in=True),
    )
    assert final["status"] == "ok", final
    reads = [e for e in of(events, "tool_started") if e["tool"] == "read_file"]
    assert reads[0]["args"]["file_path"] == "/skills/lead/research-report/SKILL.md"
    read = next(e for e in of(events, "tool_finished") if e["call_id"] == reads[0]["call_id"])
    assert "Write it to `/report.md`" in read["result"]
    # It follows the skill: the report is in /report.md, and the skills stay out of Flow Data.
    assert "/report.md" in final["output"]["files"]
    assert not any(path.startswith("/skills/") for path in final["output"]["files"])


async def test_a_helper_reads_its_own_skills(lib: SkillLibrary):
    spec = team(helper={"skills": ["source-notes"]})
    script = Script(
        [
            {"call": "task", "args": {"subagent_type": "researcher", "description": "Find facts"}},
            {
                "call": "read_file",
                "args": {"file_path": "/skills/researcher/source-notes/SKILL.md", "limit": 1000},
            },
            "Notes, with sources.",
            "Done.",
        ]
    )
    final, events = await run_flow(
        spec, {"topic": "Otters"}, RunOptions(stand_in=True, script=script)
    )
    assert final["status"] == "ok", final
    read = next(e for e in of(events, "tool_finished") if e["tool"] == "read_file")
    assert read["helper"] == "researcher"
    assert "Notes with sources" in read["result"]
