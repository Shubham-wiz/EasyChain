"""The CLI, and the Phase 1 promise: exported Python runs unchanged."""

from __future__ import annotations

import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from easychain.cli import main
from easychain.compiler import compile_flow
from easychain.export import export_files, export_zip
from easychain.spec import load_spec

from .conftest import ROOT, TEMPLATES, input_step, make_spec, output_step


def test_new_validate_compile_and_run(tmp_path, capsys):
    assert main(["new", "hello", "--dir", str(tmp_path)]) == 0
    flow = tmp_path / "hello.flow.yaml"
    assert main(["new", "hello", "--dir", str(tmp_path)]) == 1  # exists
    assert main(["validate", str(flow)]) == 0
    assert "0 error(s)" in capsys.readouterr().out

    out = tmp_path / "hello.py"
    assert main(["compile", str(flow), "-o", str(out)]) == 0
    assert "StateGraph" in out.read_text(encoding="utf-8")
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
    bad.write_text("name: x\nsteps:\n- id: Bad\n  type: input\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        main(["validate", str(bad)])
    broken = tmp_path / "broken.flow.yaml"
    broken.write_text("name: x\nsteps: []\n", encoding="utf-8")
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
    assert (
        json.loads((tmp_path / "s.json").read_text(encoding="utf-8"))["title"] == "Easy Chain flow"
    )
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
        "    output.reply: {max_length: 3, min_length: 1, matches: 'zzz', equals: 'q', bogus: 1}\n",
        encoding="utf-8",
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


def test_env_example_lists_everything_the_flow_reads():
    child = make_spec(
        [
            input_step("question"),
            {
                "id": "answer_it",
                "type": "ai_model",
                "settings": {"model": "deepseek:deepseek-chat", "prompt": "question"},
            },
            output_step("answer"),
        ],
        [("input", "answer_it"), ("answer_it", "output")],
        name="Answer",
    )
    spec = make_spec(
        [
            input_step("question", "user_id"),
            {
                "id": "helper",
                "type": "agent",
                "settings": {
                    "model": "anthropic:claude-haiku-4-5",
                    "input": "question",
                    "tools": ["orders"],
                    "mcp": [{"server": "docs"}],
                    "addons": {"fallback_models": ["mistralai:mistral-small-latest"]},
                },
            },
            {
                "id": "orders",
                "type": "sql_query",
                "description": "Runs one read-only query.",
                "settings": {
                    "connection": "postgresql://app:{secret:DB_PASSWORD}@db/shop",
                    "query": "{sql}",
                },
            },
            {
                "id": "search",
                "type": "knowledge_search",
                "settings": {
                    "knowledge_base": "docs",
                    "embedding_model": "google_genai:models/gemini-embedding-001",
                    "query": "question",
                },
            },
            {"id": "facts", "type": "memory", "settings": {"action": "recall"}},
            {"id": "sub", "type": "subflow", "settings": {"flow": "answer"}},
            output_step("answer"),
        ],
        [
            ("input", "search"),
            ("search", "facts"),
            ("facts", "helper"),
            ("helper", "sub"),
            ("sub", "output"),
        ],
        data=[{"name": "sql", "description": "One SELECT statement."}],
    )
    env = export_files(spec, resolve={"answer": child}.get)[".env.example"]
    names = [line.split("=")[0] for line in env.splitlines() if not line.startswith("#")]
    assert names == [
        "ANTHROPIC_API_KEY",  # the agent
        "DEEPSEEK_API_KEY",  # the sub-flow's AI Model
        "GOOGLE_API_KEY",  # the Knowledge Base's embedding model
        "MISTRAL_API_KEY",  # the agent's fallback model
        "DB_PASSWORD",  # {secret:…} in the database URL
        "EASYCHAIN_MCP_SERVERS",
        "EASYCHAIN_KNOWLEDGE_URL",
    ]  # and no OpenAI key: the Memory step only recalls, so its model isn't used
    assert '# {"docs": {"transport": "streamable_http"' in env


def test_readme_run_hints_keep_quotes_in_the_example(tmp_path):
    # The example input of the approval template says "hasn't".
    spec = load_spec(TEMPLATES / "approve-reply.flow.yaml")
    example = compile_flow(spec).example_input
    assert "hasn't" in json.dumps(example)
    files = export_files(spec)
    module = next(name for name in files if name.endswith(".py"))[:-3]
    blocks = files["README.md"].split("```")
    bash = next(b for b in blocks if b.startswith("bash\n")).strip().splitlines()[-1]
    powershell = next(b for b in blocks if b.startswith("powershell\n")).strip().splitlines()[-1]
    run = f"python {module}.py "
    assert bash.startswith(run) and powershell.startswith(run)
    # bash quoting is POSIX shell quoting.
    assert [json.loads(arg) for arg in shlex.split(bash.removeprefix(run))] == [example]

    # Run each line in its shell where there is one, with a script that echoes the input.
    echo = tmp_path / "echo_input.py"
    echo.write_text(
        "import json, sys\nprint(json.dumps(json.loads(sys.argv[1])))\n", encoding="utf-8"
    )
    commands = []
    if os.name != "nt" and shutil.which("bash"):
        line = f"{shlex.quote(sys.executable)} {shlex.quote(str(echo))} {bash.removeprefix(run)}"
        commands.append((["bash", "-c", line], None))
    if os.name == "nt":
        shell = Path(os.environ.get("SYSTEMROOT", "C:/Windows"), "System32/WindowsPowerShell/v1.0")
        line = f"& '{sys.executable}' '{echo}' {powershell.removeprefix(run)}\n"
        commands.append(([str(shell / "powershell.exe"), "-NoProfile", "-Command", "-"], line))
    for command, stdin in commands:
        done = subprocess.run(command, input=stdin, capture_output=True, text=True, timeout=60)
        assert done.returncode == 0, done.stderr
        assert json.loads(done.stdout) == example


def _run_exported(
    tmp_path: Path, template: str, args: list[str], env: dict[str, str], stdin: str | None = None
) -> str:
    spec = load_spec(TEMPLATES / f"{template}.flow.yaml")
    out = tmp_path / template
    out.mkdir()
    files = export_files(spec)
    for name, content in files.items():
        (out / name).write_text(content, encoding="utf-8")
    script = next(name for name in files if name.endswith(".py"))
    # Run with a clean environment: only LangChain packages are importable as usual,
    # and the easychain package is explicitly hidden to prove it isn't needed.
    clean = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("OPENAI", "ANTHROPIC", "EASYCHAIN"))
    }
    clean.update(env, PYTHONIOENCODING="utf-8")
    hide = "import sys; sys.modules['easychain'] = None; import runpy; sys.argv = sys.argv[1:]; runpy.run_path(sys.argv[0], run_name='__main__')"
    result = subprocess.run(
        [sys.executable, "-c", hide, script, *args],
        cwd=out,
        env=clean,
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
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
