"""The Code step sandbox (Phase 4's "Done when": Code steps can't escape their sandbox).

The escape tests run real containers, so they need Docker (Linux containers). They are skipped
when Docker isn't there; CI's Linux job has it.
"""

from __future__ import annotations

import os
import subprocess
import time
from datetime import datetime

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from easychain.runtime import RunOptions, run_flow
from easychain.runtime import sandbox as sandbox_module
from easychain.runtime.sandbox import (
    DockerSandbox,
    SandboxLimit,
    SandboxSettings,
    SandboxUnavailable,
    docker_status,
    plain,
    sandboxed,
)

from .conftest import input_step, make_spec, output_step

NO_DOCKER = {
    "available": False,
    "reason": "Docker isn't installed.",
    "version": None,
    "gvisor": False,
}

DOCKER = docker_status(refresh=True)["available"]
needs_docker = pytest.mark.skipif(not DOCKER, reason="Docker (Linux containers) isn't available")


def code(body: str) -> str:
    return "def run(data):\n" + "".join(f"    {line}\n" for line in body.strip("\n").splitlines())


@pytest.fixture(scope="module")
def box() -> DockerSandbox:
    if not DOCKER:
        pytest.skip("Docker (Linux containers) isn't available")
    sandbox = DockerSandbox(SandboxSettings(mode="docker", memory_mb=128, pids=32, timeout=20))
    sandbox.ensure(sandbox.settings.image)
    return sandbox


# ── without Docker ────────────────────────────────────────────────────────────


def test_flow_data_goes_in_as_plain_values():
    data = {
        "messages": [HumanMessage("hi"), AIMessage("hello")],
        "n": 3,
        "nested": {"set": {1}, "when": datetime(2026, 1, 1, 9, 30)},
    }
    out = plain(data)
    assert out["messages"] == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    assert out["n"] == 3 and out["nested"]["set"] == [1]
    assert out["nested"]["when"] == "2026-01-01 09:30:00"  # anything else becomes text


def test_settings_are_checked():
    assert SandboxSettings.from_dict({"mode": "off"}).mode == "off"
    with pytest.raises(ValueError, match="mode"):
        SandboxSettings.from_dict({"mode": "sometimes"})
    with pytest.raises(ValueError, match="memory_mb"):
        SandboxSettings.from_dict({"memory_mb": 1})
    with pytest.raises(ValueError, match="time limit"):
        SandboxSettings.from_dict({"timeout": 0})


def test_mode_off_runs_the_step_itself_and_required_mode_refuses_without_docker(monkeypatch):
    def original(data):
        return {"ran": "in-process"}

    info = {"code": code("return {'ran': 'sandbox'}")}
    off = sandboxed(original, info, lambda: SandboxSettings(mode="off"))
    assert off({}) == {"ran": "in-process"}

    no_docker = {
        "available": False,
        "reason": "Docker isn't installed.",
        "version": None,
        "gvisor": False,
    }
    monkeypatch.setattr(sandbox_module, "docker_status", lambda refresh=False: no_docker)
    auto = sandboxed(original, info, lambda: SandboxSettings(mode="auto"))
    assert auto({}) == {"ran": "in-process"}  # the check before a run says so
    required = sandboxed(original, info, lambda: SandboxSettings(mode="docker"))
    with pytest.raises(SandboxUnavailable, match="Docker isn't installed"):
        required({})


def test_gvisor_is_used_when_docker_has_it(monkeypatch):
    with_runsc = {"available": True, "reason": None, "version": "x", "gvisor": True}
    monkeypatch.setattr(sandbox_module, "docker_status", lambda refresh=False: with_runsc)
    cmd = DockerSandbox(SandboxSettings(mode="docker")).command("n", "img", network=False)
    assert cmd[cmd.index("--runtime") + 1] == "runsc"
    plain_runc = DockerSandbox(SandboxSettings(mode="docker", runtime="runc"))
    assert "--runtime" not in plain_runc.command("n", "img", network=False)
    # Every container is locked down, whatever the runtime.
    for flag in ("--read-only", "--cap-drop", "--pids-limit", "--memory", "--user"):
        assert flag in cmd
    assert cmd[cmd.index("--network") + 1] == "none"


# ── it works ──────────────────────────────────────────────────────────────────


@needs_docker
def test_code_runs_in_the_sandbox_and_gets_its_data(box):
    result = box.run(
        code(
            """
import os
print("anything printed goes to the log, not the result")
return {
    "double": data["n"] * 2,
    "last": data["messages"][-1].content,
    "kind": data["messages"][0].type,
    "user": os.getuid(),
}
"""
        ),
        {"n": 21, "messages": [HumanMessage("hi"), AIMessage("hello")]},
    )
    assert result == {"double": 42, "last": "hello", "kind": "human", "user": 65534}


@needs_docker
def test_the_codes_own_errors_come_back_as_they_were(box):
    with pytest.raises(ValueError, match="no such order"):
        box.run(code("raise ValueError('no such order')"), {})
    with pytest.raises(ZeroDivisionError):
        box.run(code("return {'x': 1 / 0}"), {})
    with pytest.raises(ModuleNotFoundError, match="add numpy to this step's Packages"):
        box.run("import numpy\n\n" + code("return {}"), {})
    with pytest.raises(RuntimeError, match="CustomError: odd"):
        box.run(
            "class CustomError(Exception):\n    pass\n\n" + code("raise CustomError('odd')"), {}
        )


# ── escape tests ──────────────────────────────────────────────────────────────


@needs_docker
def test_the_hosts_files_are_out_of_reach(box, tmp_path):
    secret = tmp_path / "host-secret.txt"
    secret.write_text("top secret")
    result = box.run(
        code(
            f"""
import os
seen = {{}}
for path in [{str(secret)!r}, "/home", "/root", "/var/run/docker.sock", "/etc/shadow"]:
    try:
        with open(path) as f:
            seen[path] = f.read()[:20]
    except OSError as error:
        seen[path] = type(error).__name__
return seen
"""
        ),
        {},
    )
    assert result[str(secret)] == "FileNotFoundError"  # the host's files aren't there at all
    assert result["/var/run/docker.sock"] == "FileNotFoundError"  # no way to Docker itself
    assert "top secret" not in str(result)
    assert result["/etc/shadow"] in ("PermissionError", "FileNotFoundError")


@needs_docker
def test_nothing_outside_tmp_can_be_written(box):
    result = box.run(
        code(
            """
out = {}
for path in ["/usr/local/lib/x.py", "/etc/passwd", "/x", "/tmp/scratch.txt"]:
    try:
        with open(path, "w") as f:
            f.write("x")
        out[path] = "written"
    except OSError as error:
        out[path] = type(error).__name__
return out
"""
        ),
        {},
    )
    assert result.pop("/tmp/scratch.txt") == "written"  # scratch space, thrown away afterwards
    assert set(result.values()) <= {"OSError", "PermissionError"}, result


@needs_docker
def test_there_is_no_network(box):
    result = box.run(
        code(
            """
import socket
out = {}
for host, port in [("1.1.1.1", 53), ("8.8.8.8", 443), ("172.17.0.1", 80), ("host.docker.internal", 80)]:
    try:
        socket.create_connection((host, port), timeout=3).close()
        out[host] = "connected"
    except OSError as error:
        out[host] = "blocked"
return out
"""
        ),
        {},
    )
    assert set(result.values()) == {"blocked"}, result


@needs_docker
def test_easy_chains_secrets_and_environment_stay_outside(box, monkeypatch):
    monkeypatch.setenv("EASYCHAIN_TEST_SECRET", "s3cret-value")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-for-the-sandbox")
    result = box.run(code("import os\nreturn dict(os.environ)"), {})
    assert "EASYCHAIN_TEST_SECRET" not in result and "OPENAI_API_KEY" not in result
    assert "s3cret-value" not in str(result)


@needs_docker
def test_no_root_and_no_privileges(box):
    result = box.run(
        code(
            """
import os
status = open("/proc/self/status").read()
caps = {l.split(":")[0]: l.split()[1] for l in status.splitlines() if l.startswith("Cap")}
try:
    os.setuid(0)
    became_root = True
except PermissionError:
    became_root = False
return {"uid": os.getuid(), "caps": caps, "became_root": became_root}
"""
        ),
        {},
    )
    assert result["uid"] == 65534 and not result["became_root"]
    assert int(result["caps"]["CapEff"], 16) == 0 and int(result["caps"]["CapBnd"], 16) == 0


@needs_docker
def test_a_fork_bomb_is_contained(box):
    result = box.run(
        code(
            """
import os
made = 0
try:
    for _ in range(500):
        if os.fork() == 0:
            import time
            time.sleep(5)
            os._exit(0)
        made += 1
except OSError:
    pass
return {"made": made}
"""
        ),
        {},
    )
    assert result["made"] < box.settings.pids  # stopped by the process limit
    # The host is fine: a fresh container still runs.
    assert box.run(code("return {'ok': True}"), {}) == {"ok": True}


@needs_docker
def test_memory_is_limited(box):
    with pytest.raises(SandboxLimit, match="memory limit"):
        box.run(code("blob = bytearray(1024 * 1024 * 1024)\nreturn {'size': len(blob)}"), {})


@needs_docker
def test_time_is_limited_and_the_container_is_stopped(box):
    began = time.monotonic()
    with pytest.raises(SandboxLimit, match="time limit"):
        box.run(code("while True:\n    pass"), {}, timeout=3)
    assert time.monotonic() - began < 3 + sandbox_module._START_ALLOWANCE + 10
    left = subprocess.run(
        ["docker", "ps", "--filter", "label=easychain.sandbox=1", "-q"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert left.stdout.strip() == ""  # no sandbox container keeps running


# ── in a flow ─────────────────────────────────────────────────────────────────


def _code_flow() -> object:
    return make_spec(
        [
            input_step({"name": "n", "type": "number"}),
            {
                "id": "work",
                "type": "code",
                "settings": {
                    "code": code("import os\nreturn {'double': data['n'] * 2, 'uid': os.getuid()}"),
                },
            },
            output_step("double", "uid"),
        ],
        [("input", "work"), ("work", "output")],
    )


@needs_docker
async def test_a_flows_code_step_runs_in_the_sandbox():
    spec = _code_flow()
    final, events = await run_flow(
        spec, {"n": 4}, RunOptions(sandbox=SandboxSettings(mode="docker"))
    )
    assert final["status"] == "ok", final
    assert final["output"] == {"double": 8, "uid": 65534}
    # The same flow with the sandbox off runs in Easy Chain's process.
    final, _ = await run_flow(spec, {"n": 4}, RunOptions(sandbox=SandboxSettings(mode="off")))
    assert final["output"] == {"double": 8, "uid": os.getuid()}


@needs_docker
async def test_a_failing_sandboxed_step_is_explained_on_the_step():
    spec = make_spec(
        [
            input_step("q"),
            {
                "id": "work",
                "type": "code",
                "settings": {"code": code("raise ValueError('bad input')")},
            },
            output_step("q"),
        ],
        [("input", "work"), ("work", "output")],
    )
    final, events = await run_flow(
        spec, {"q": "x"}, RunOptions(sandbox=SandboxSettings(mode="docker"))
    )
    assert final["status"] == "error"
    assert "bad input" in final["error"]["message"]
    assert any(e["type"] == "step_failed" and e["step"] == "work" for e in events)


# ── Settings and the check before a run ───────────────────────────────────────


def test_sandbox_settings_and_the_check_before_a_run(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from easychain.server import app as app_module
    from easychain.server.app import create_app

    monkeypatch.setattr(sandbox_module, "docker_status", lambda refresh=False: NO_DOCKER)
    monkeypatch.setattr(app_module, "docker_status", lambda refresh=False: NO_DOCKER)
    app = create_app(
        workspace=tmp_path / "flows",
        home=tmp_path / "home",
        static_dir=tmp_path / "web",
        database_url=f"sqlite:///{tmp_path / 'app.db'}",
        worker=False,
    )
    spec = {
        "name": "Code",
        "steps": [
            {"id": "input", "type": "input", "settings": {"fields": [{"name": "q"}]}},
            {
                "id": "work",
                "type": "code",
                "settings": {"code": "def run(data):\n    return {'a': 1}\n"},
            },
            {"id": "output", "type": "output", "settings": {"fields": ["a"]}},
        ],
        "connections": [{"from": "input", "to": "work"}, {"from": "work", "to": "output"}],
    }
    with TestClient(app) as client:
        assert client.get("/api/settings/sandbox").json()["mode"] == "off"  # EASYCHAIN_SANDBOX
        codes = [i["code"] for i in client.post("/api/check", json=spec).json()["issues"]]
        assert "sandbox_off" not in codes  # switched off on purpose: nothing to say

        saved = client.put("/api/settings/sandbox", json={"mode": "auto", "memory_mb": 512}).json()
        assert saved["mode"] == "auto" and saved["memory_mb"] == 512
        status = client.get("/api/sandbox/status").json()
        assert status["available"] is False and status["effective"] == "off"
        [issue] = [
            i
            for i in client.post("/api/check", json=spec).json()["issues"]
            if "sandbox" in i["code"]
        ]
        assert issue["code"] == "sandbox_off" and issue["level"] == "warning"
        assert issue["step"] == "work" and "Docker isn't installed" in issue["message"]
        assert issue["fix"]["kind"] == "open_settings" and issue["fix"]["params"] == {
            "tab": "sandbox"
        }

        client.put("/api/settings/sandbox", json={"mode": "docker"})
        [issue] = [
            i
            for i in client.post("/api/check", json=spec).json()["issues"]
            if "sandbox" in i["code"]
        ]
        assert issue["code"] == "sandbox_unavailable" and issue["level"] == "error"

        bad = client.put("/api/settings/sandbox", json={"mode": "sometimes"})
        assert bad.status_code == 422 and "mode" in bad.json()["message"]
        assert client.post("/api/sandbox/prepare").json() == {
            "ok": False,
            "message": "Docker isn't installed.",
        }


@needs_docker
async def test_a_run_at_most_once_step_gets_its_idempotency_key_in_the_sandbox():
    spec = make_spec(
        [
            input_step("q"),
            {
                "id": "send",
                "type": "code",
                "settings": {
                    "code": code(
                        "import os\nreturn {'key': idempotency_key(), 'uid': os.getuid()}"
                    ),
                    "side_effect": True,
                },
            },
            output_step("key", "uid"),
        ],
        [("input", "send"), ("send", "output")],
    )
    final, _ = await run_flow(
        spec, {"q": "x"}, RunOptions(sandbox=SandboxSettings(mode="docker"), thread_id="once-1")
    )
    assert final["status"] == "ok", final
    assert final["output"]["uid"] == 65534  # ran in the sandbox
    assert isinstance(final["output"]["key"], str) and len(final["output"]["key"]) >= 16
