"""The CLI, and the Phase 1 promise: exported Python runs unchanged."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from easychain.cli import main
from easychain.export import export_files, export_zip
from easychain.spec import load_spec

from .conftest import ROOT, TEMPLATES


def test_new_validate_compile_and_run(tmp_path, capsys):
    assert main(["new", "hello", "--dir", str(tmp_path)]) == 0
    flow = tmp_path / "hello.flow.yaml"
    assert main(["new", "hello", "--dir", str(tmp_path)]) == 1  # exists
    assert main(["validate", str(flow)]) == 0
    assert "0 error(s)" in capsys.readouterr().out

    out = tmp_path / "hello.py"
    assert main(["compile", str(flow), "-o", str(out)]) == 0
    assert "StateGraph" in out.read_text()
    assert main(["compile", str(flow)]) == 0
    assert "def ask_ai" in capsys.readouterr().out

    assert main(["run", str(flow), "-i", "question=What is LangGraph?", "--stand-in"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["answer"].startswith("[Stand-in AI")
    assert "✓ Ask the AI" in captured.err

    assert main(["run", str(flow), "--json", '{"question": "hi"}', "--stand-in", "--events"]) == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert lines[-1]["type"] == "run_finished"

    assert main(["run", str(flow), "-i", "question=hi"]) == 1
    assert "needs your OpenAI API key" in capsys.readouterr().err
    assert main(["run", str(flow)]) == 1
    assert "Fill in `question`" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(["run", str(flow), "-i", "no-equals-sign"])


def test_new_from_template_and_errors(tmp_path, capsys):
    assert main(["new", "mine", "--template", "summarise-url", "--dir", str(tmp_path)]) == 0
    assert load_spec(tmp_path / "mine.flow.yaml").name == "Summarise a URL"
    assert main(["new", "x", "--template", "nope", "--dir", str(tmp_path)]) == 2
    with pytest.raises(SystemExit):
        main(["validate", str(tmp_path / "missing.flow.yaml")])
    bad = tmp_path / "bad.flow.yaml"
    bad.write_text("name: x\nsteps:\n- id: Bad\n  type: input\n")
    with pytest.raises(SystemExit):
        main(["validate", str(bad)])
    broken = tmp_path / "broken.flow.yaml"
    broken.write_text("name: x\nsteps: []\n")
    assert main(["validate", str(broken)]) == 1
    assert main(["compile", str(broken)]) == 1
    assert main(["export", str(broken), "-o", str(tmp_path / "out")]) == 1
    capsys.readouterr()
    assert main(["run", str(broken)]) == 1
    assert "needs an Input step" in capsys.readouterr().err


def test_templates_schema_and_test_commands(tmp_path, capsys, fake_openai):
    assert main(["templates"]) == 0
    assert "summarise-url" in capsys.readouterr().out
    assert main(["schema", "-o", str(tmp_path / "s.json")]) == 0
    assert json.loads((tmp_path / "s.json").read_text())["title"] == "Easy Chain flow"
    assert main(["schema"]) == 0
    capsys.readouterr()
    assert main(["test", str(TEMPLATES / "reply-to-feedback.tests.yaml")]) == 0
    assert "10/10 passed" in capsys.readouterr().out
    assert (
        main(
            [
                "test",
                str(TEMPLATES / "summarise-url.tests.yaml"),
                "--var",
                f"base_url={fake_openai.url}",
            ]
        )
        == 0
    )
    failing = tmp_path / "failing.tests.yaml"
    failing.write_text(
        f"flow: {TEMPLATES / 'reply-to-feedback.flow.yaml'}\nstand_in: true\ncases:\n"
        "- name: wrong\n  inputs: {feedback: 'I love it'}\n  expect:\n    output.kind: Complaint\n"
        "    output.reply: {max_length: 3, min_length: 1, matches: 'zzz', equals: 'q', bogus: 1}\n"
    )
    assert main(["test", str(failing)]) == 1
    out = capsys.readouterr().out
    assert "0/1 passed" in out and "unknown check 'bogus'" in out


def test_export_bundle_contents():
    spec = load_spec(TEMPLATES / "summarise-url.flow.yaml")
    files = export_files(spec)
    assert set(files) == {
        "summarise_a_url.py",
        "requirements.txt",
        "langgraph.json",
        ".env.example",
        "README.md",
        "summarise_a_url.flow.yaml",
    }
    assert files[".env.example"] == "OPENAI_API_KEY=\n"
    assert json.loads(files["langgraph.json"])["graphs"] == {
        "summarise_a_url": "./summarise_a_url.py:graph"
    }
    assert "easychain" not in files["requirements.txt"]
    with zipfile.ZipFile(io.BytesIO(export_zip(spec))) as zf:
        assert "summarise_a_url/README.md" in zf.namelist()


def test_export_lists_secrets_and_cli_writes_files(tmp_path, capsys):
    flow = Path(__file__).parent / "golden" / "cases" / "http_post_json.flow.yaml"
    assert "SEARCH_API_KEY=" in export_files(load_spec(flow))[".env.example"]
    assert main(["export", str(flow), "-o", str(tmp_path / "out")]) == 0
    assert (tmp_path / "out" / "search_an_api.py").exists()
    assert main(["export", str(flow), "--zip", "-o", str(tmp_path / "x.zip")]) == 0
    assert zipfile.is_zipfile(tmp_path / "x.zip")


def _run_exported(
    tmp_path: Path, template: str, args: list[str], env: dict[str, str], stdin: str | None = None
) -> str:
    spec = load_spec(TEMPLATES / f"{template}.flow.yaml")
    out = tmp_path / template
    out.mkdir()
    files = export_files(spec)
    for name, content in files.items():
        (out / name).write_text(content)
    script = next(name for name in files if name.endswith(".py"))
    # Run with a clean environment: only LangChain packages are importable as usual,
    # and the easychain package is explicitly hidden to prove it isn't needed.
    clean = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("OPENAI", "ANTHROPIC", "EASYCHAIN"))
    }
    clean.update(env)
    hide = "import sys; sys.modules['easychain'] = None; import runpy; sys.argv = sys.argv[1:]; runpy.run_path(sys.argv[0], run_name='__main__')"
    result = subprocess.run(
        [sys.executable, "-c", hide, script, *args],
        cwd=out,
        env=clean,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_exported_python_runs_unchanged(tmp_path, fake_openai):
    env = {"OPENAI_API_KEY": "sk-test-export", "OPENAI_BASE_URL": fake_openai.base_url}
    stdout = _run_exported(
        tmp_path, "summarise-url", [json.dumps({"url": fake_openai.url + "/pages/langchain"})], env
    )
    result = json.loads(stdout)
    assert result["summary"].startswith("[fake gpt-4o-mini]")
    assert "LangChain" in result["summary"]


def test_exported_chat_runs_in_the_terminal(tmp_path, fake_openai):
    env = {"OPENAI_API_KEY": "sk-test-export", "OPENAI_BASE_URL": fake_openai.base_url}
    stdout = _run_exported(tmp_path, "chat-assistant", [], env, stdin="Hello\nHow are you?\n")
    assert stdout.count("ai> [fake gpt-4o-mini]") == 2


def test_exported_decision_flow_runs(tmp_path, fake_openai):
    env = {"OPENAI_API_KEY": "sk-test-export", "OPENAI_BASE_URL": fake_openai.base_url}
    stdout = _run_exported(
        tmp_path, "smart-summary", [json.dumps({"url": fake_openai.url + "/pages/long"})], env
    )
    result = json.loads(stdout)
    assert result["word_count"] > 800
    assert "Paragraph" in result["summary"]


def test_phase0_example_flow_runs_from_cli(capsys):
    example = ROOT / "examples" / "hello.flow.yaml"
    assert main(["run", str(example), "-i", "question=Hi there", "--stand-in", "-q"]) == 0
    assert "Stand-in" in capsys.readouterr().out
