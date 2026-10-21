"""The Code step sandbox: Code steps run in a throwaway container, not in Easy Chain's process.

A Code step's Python runs in a fresh container for each call:

- no network (unless the step says it needs it), no access to the host's files (the container's
  own file system is read-only; ``/tmp`` is a small scratch space), none of Easy Chain's
  environment variables or secrets;
- as an unprivileged user, with every Linux capability dropped and no way to gain more;
- with limits on memory, CPU, processes and time;
- on gVisor (``runsc``) when Docker has it, which also keeps the host's kernel out of reach.

The step's Flow Data goes in as JSON on stdin and its result comes back as JSON, so the code
sees plain values (chat messages arrive as dicts that also allow ``message.content``).

Docker is used through its command line, which works the same with Docker Engine on Linux and
Docker Desktop on Windows and macOS (with Linux containers). The mode is chosen in Settings or
with ``EASYCHAIN_SANDBOX``:

- ``auto`` (the default): use Docker when it is there; otherwise run Code steps in-process, and
  the check before a run says so;
- ``docker``: always use Docker; a Code step fails with a clear message when it isn't there;
- ``off``: run Code steps in Easy Chain's own process (as exported code does).
"""

from __future__ import annotations

import builtins
import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from typing import Any

MODES = ("auto", "docker", "off")
RUNTIMES = ("auto", "runc", "runsc")
DEFAULT_IMAGE = "python:3.12-slim"
# Docker gets this long to answer `docker info` before it counts as not running.
_STATUS_TIMEOUT = 8.0
_STATUS_TTL = 30.0
# Seconds a container may take to start, on top of the code's own time limit.
_START_ALLOWANCE = 15.0


@dataclass(frozen=True)
class SandboxSettings:
    """How Code steps are sandboxed (Settings → Sandbox, or ``EASYCHAIN_SANDBOX*``)."""

    mode: str = "auto"
    image: str = DEFAULT_IMAGE
    runtime: str = "auto"
    memory_mb: int = 256
    cpus: float = 1.0
    pids: int = 64
    timeout: float = 120.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(
        cls, value: dict[str, Any] | None, base: SandboxSettings | None = None
    ) -> SandboxSettings:
        out = base or settings_from_env()
        known = {k: v for k, v in (value or {}).items() if k in cls.__dataclass_fields__}
        if known.get("mode") not in (None, *MODES):
            raise ValueError(f"The sandbox mode must be one of {', '.join(MODES)}.")
        if known.get("runtime") not in (None, *RUNTIMES):
            raise ValueError(f"The sandbox runtime must be one of {', '.join(RUNTIMES)}.")
        for key, low, high in (("memory_mb", 32, 65536), ("cpus", 0.1, 64), ("pids", 8, 4096)):
            if key in known and not low <= float(known[key]) <= high:
                raise ValueError(f"The sandbox's {key} must be between {low} and {high}.")
        if "timeout" in known and not 1 <= float(known["timeout"]) <= 3600:
            raise ValueError("The sandbox's time limit must be between 1 and 3600 seconds.")
        return replace(out, **known)


def settings_from_env() -> SandboxSettings:
    mode = os.environ.get("EASYCHAIN_SANDBOX", "auto").strip().lower() or "auto"
    runtime = os.environ.get("EASYCHAIN_SANDBOX_RUNTIME", "auto").strip().lower() or "auto"
    return SandboxSettings(
        mode=mode if mode in MODES else "auto",
        image=os.environ.get("EASYCHAIN_SANDBOX_IMAGE", "").strip() or DEFAULT_IMAGE,
        runtime=runtime if runtime in RUNTIMES else "auto",
    )


class SandboxUnavailable(RuntimeError):
    """Settings say Code steps must run in the sandbox, and Docker can't be used."""


class SandboxLimit(RuntimeError):
    """The code went over one of the sandbox's limits (time, memory, processes)."""


# ── Docker status ─────────────────────────────────────────────────────────────

_status: tuple[float, dict[str, Any]] | None = None
_status_lock = threading.Lock()


def docker_status(refresh: bool = False) -> dict[str, Any]:
    """Whether Docker can run the sandbox here: ``{"available", "reason", "version",
    "gvisor"}``. Cached for a little while; ``refresh`` asks again."""
    global _status
    with _status_lock:
        if not refresh and _status and time.monotonic() - _status[0] < _STATUS_TTL:
            return _status[1]
    status = _ask_docker()
    with _status_lock:
        _status = (time.monotonic(), status)
    return status


def _ask_docker() -> dict[str, Any]:
    docker = shutil.which("docker")
    if not docker:
        return {
            "available": False,
            "reason": "Docker isn't installed (or isn't on the PATH).",
            "version": None,
            "gvisor": False,
        }
    try:
        done = subprocess.run(
            [docker, "info", "--format", "{{json .}}"],
            capture_output=True,
            text=True,
            timeout=_STATUS_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "available": False,
            "reason": f"Docker didn't answer ({exc}).",
            "version": None,
            "gvisor": False,
        }
    if done.returncode != 0:
        detail = (done.stderr or done.stdout).strip().splitlines()
        reason = detail[-1] if detail else "docker info failed"
        return {
            "available": False,
            "reason": f"Docker isn't running: {reason}",
            "version": None,
            "gvisor": False,
        }
    try:
        info = json.loads(done.stdout)
    except ValueError:
        info = {}
    if str(info.get("OSType", "linux")).lower() != "linux":
        return {
            "available": False,
            "reason": "Docker is running Windows containers. Switch Docker Desktop to Linux "
            "containers to use the sandbox.",
            "version": info.get("ServerVersion"),
            "gvisor": False,
        }
    return {
        "available": True,
        "reason": None,
        "version": info.get("ServerVersion"),
        "gvisor": "runsc" in (info.get("Runtimes") or {}),
    }


def effective_mode(settings: SandboxSettings) -> str:
    """``docker`` when Code steps will run in the sandbox, ``off`` when they won't, and
    ``unavailable`` when Settings require it but Docker can't be used."""
    if settings.mode == "off":
        return "off"
    if docker_status()["available"]:
        return "docker"
    return "unavailable" if settings.mode == "docker" else "off"


# ── Running code ──────────────────────────────────────────────────────────────

# The program the container runs (``python -I -c``). It reads {"code", "data"} as JSON from
# stdin, runs the step's ``run(data)``, and writes {"ok": …} as JSON to stdout. Anything the
# code prints goes to stderr, so it can't be mistaken for the result.
RUNNER = r'''
import json, sys, traceback

class Message(dict):
    """A chat message: a dict that also allows message.content, message.type, message.role."""
    def __getattr__(self, name):
        if name == "type":
            return {"user": "human", "assistant": "ai"}.get(self.get("role"), self.get("role"))
        if name == "text":
            return self.get("content")
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None

def wrap(value):
    if isinstance(value, dict):
        out = {k: wrap(v) for k, v in value.items()}
        return Message(out) if "role" in out and "content" in out else out
    if isinstance(value, list):
        return [wrap(v) for v in value]
    return value

out = sys.stdout
sys.stdout = sys.stderr
try:
    payload = json.loads(sys.stdin.read())
    key = payload.get("idempotency_key")
    space = {"__name__": "easychain_code_step", "idempotency_key": lambda: key}
    exec(compile(payload["code"], "<code step>", "exec"), space)
    result = space["run"](wrap(payload["data"]))
    text = json.dumps({"ok": True, "result": result}, default=str)
except MemoryError:
    text = json.dumps({"ok": False, "type": "MemoryError", "message": "", "traceback": ""})
except BaseException as error:
    text = json.dumps({
        "ok": False,
        "type": type(error).__name__,
        "message": str(error),
        "traceback": traceback.format_exc(limit=8),
    })
out.write("\n" + text + "\n")
out.flush()
'''

_build_locks: dict[str, threading.Lock] = {}
_build_guard = threading.Lock()


def plain(value: Any, depth: int = 0) -> Any:
    """Flow Data as plain JSON values (chat messages as {"role", "content"} dicts)."""
    if depth > 50:
        return None
    content = getattr(value, "content", None)
    kind = getattr(value, "type", None)
    if content is not None and isinstance(kind, str) and hasattr(value, "additional_kwargs"):
        role = {"human": "user", "ai": "assistant"}.get(kind, kind)
        out: dict[str, Any] = {"role": role, "content": plain(content, depth + 1)}
        if getattr(value, "tool_calls", None):
            out["tool_calls"] = plain(value.tool_calls, depth + 1)
        return out
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, dict):
        return {str(k): plain(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list | tuple | set):
        return [plain(v, depth + 1) for v in value]
    return str(value)


class DockerSandbox:
    """Runs a Code step's ``run(data)`` in a fresh, locked-down container."""

    def __init__(self, settings: SandboxSettings):
        self.settings = settings
        self.docker = shutil.which("docker") or "docker"

    def runtime(self) -> str | None:
        if self.settings.runtime == "runsc":
            return "runsc"
        if self.settings.runtime == "auto" and docker_status()["gvisor"]:
            return "runsc"
        return None

    def image_for(self, requirements: list[str]) -> str:
        """The base image, or one built on it with the step's packages (cached by them)."""
        wanted = sorted({r.strip() for r in requirements if r.strip()})
        if not wanted:
            return self.settings.image
        digest = hashlib.sha256(json.dumps([self.settings.image, wanted]).encode()).hexdigest()
        tag = f"easychain-sandbox:{digest[:16]}"
        with _build_guard:
            lock = _build_locks.setdefault(tag, threading.Lock())
        with lock:
            have = subprocess.run(
                [self.docker, "image", "inspect", tag], capture_output=True, timeout=30
            )
            if have.returncode == 0:
                return tag
            dockerfile = (
                f"FROM {self.settings.image}\n"
                f"RUN pip install --no-cache-dir --disable-pip-version-check {' '.join(json.dumps(r) for r in wanted)}\n"
            )
            built = subprocess.run(
                [self.docker, "build", "--quiet", "-t", tag, "-"],
                input=dockerfile,
                capture_output=True,
                text=True,
                timeout=900,
            )
            if built.returncode != 0:
                detail = (built.stderr or built.stdout).strip()[-600:]
                raise RuntimeError(
                    f"Couldn't install this step's packages ({', '.join(wanted)}) in the sandbox: "
                    f"{detail}"
                )
        return tag

    def command(self, name: str, image: str, network: bool) -> list[str]:
        s = self.settings
        cmd = [
            self.docker, "run", "--rm", "-i", "--name", name,
            "--network", "bridge" if network else "none",
            "--read-only", "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m,mode=1777",
            "--memory", f"{s.memory_mb}m", "--memory-swap", f"{s.memory_mb}m",
            "--cpus", f"{s.cpus:g}", "--pids-limit", str(s.pids),
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--user", "65534:65534", "--workdir", "/tmp",
            "--env", "HOME=/tmp", "--env", "PYTHONDONTWRITEBYTECODE=1",
            "--label", "easychain.sandbox=1",
        ]  # fmt: skip
        runtime = self.runtime()
        if runtime:
            cmd += ["--runtime", runtime]
        return [*cmd, image, "python", "-I", "-c", RUNNER]

    def run(
        self,
        code: str,
        data: Any,
        *,
        requirements: list[str] | None = None,
        network: bool = False,
        timeout: float | None = None,
        idempotency_key: str | None = None,
    ) -> Any:
        """Run ``code``'s ``run(data)`` in the sandbox and return what it returned.
        ``idempotency_key`` is what the code's ``idempotency_key()`` returns (steps that run at
        most once)."""
        image = self.image_for(list(requirements or []))
        self.ensure(image)
        limit = min(timeout or self.settings.timeout, self.settings.timeout)
        name = f"easychain-code-{uuid.uuid4().hex[:12]}"
        payload = json.dumps(
            {"code": code, "data": plain(data), "idempotency_key": idempotency_key}
        )
        began = time.monotonic()
        try:
            done = subprocess.run(
                self.command(name, image, network),
                input=payload,
                capture_output=True,
                text=True,
                encoding="utf-8",
                # Starting the container (more so on gVisor) isn't the code's time.
                timeout=limit + _START_ALLOWANCE,
            )
        except subprocess.TimeoutExpired:
            self._kill(name)
            raise SandboxLimit(
                f"The code took longer than its time limit ({limit:g} s) and was stopped."
            ) from None
        took = time.monotonic() - began
        answer = _last_json(done.stdout)
        if answer is None:
            if done.returncode in (137, -9) or "OOMKilled" in done.stderr:
                if took >= limit:
                    raise SandboxLimit(
                        f"The code took longer than its time limit ({limit:g} s) and was stopped."
                    )
                raise SandboxLimit(
                    f"The code used more than its memory limit ({self.settings.memory_mb} MB) "
                    "and was stopped."
                )
            detail = (done.stderr or "").strip().splitlines()
            reason = detail[-1] if detail else f"exit code {done.returncode}"
            if "Unable to find image" in done.stderr or "pull access denied" in done.stderr:
                reason = f"the sandbox image {image} isn't available: {reason}"
            raise RuntimeError(f"The sandbox couldn't run the code: {reason}")
        if answer.get("ok"):
            return answer.get("result")
        raise _error_from(answer, self.settings)

    def ensure(self, image: str) -> None:
        """Pull the image if Docker doesn't have it yet (outside the code's time limit)."""
        have = subprocess.run(
            [self.docker, "image", "inspect", image], capture_output=True, timeout=30
        )
        if have.returncode == 0:
            return
        pulled = subprocess.run(
            [self.docker, "pull", image], capture_output=True, text=True, timeout=900
        )
        if pulled.returncode != 0:
            detail = (pulled.stderr or pulled.stdout).strip().splitlines()
            reason = detail[-1] if detail else "docker pull failed"
            raise RuntimeError(
                f"The sandbox image {image} isn't available and couldn't be downloaded: {reason}"
            )

    def _kill(self, name: str) -> None:
        with contextlib.suppress(OSError, subprocess.TimeoutExpired):
            subprocess.run([self.docker, "kill", name], capture_output=True, timeout=30)


def _last_json(text: str) -> dict[str, Any] | None:
    for line in reversed((text or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                value = json.loads(line)
            except ValueError:
                return None
            return value if isinstance(value, dict) and "ok" in value else None
    return None


def _error_from(answer: dict[str, Any], settings: SandboxSettings) -> BaseException:
    """The code's own error, raised again here: the same type for Python's built-in errors
    (so retries and messages work as they do in-process), with the sandbox's traceback."""
    kind = str(answer.get("type") or "Exception")
    message = str(answer.get("message") or "")
    if kind == "MemoryError":
        return SandboxLimit(
            f"The code used more than its memory limit ({settings.memory_mb} MB) and was stopped."
        )
    if kind == "ModuleNotFoundError":
        missing = message.split("'")[1] if message.count("'") >= 2 else message
        return ModuleNotFoundError(
            f"{message}. The sandbox has only Python's standard library: add {missing} to this "
            "step's Packages (More options)."
        )
    cls = getattr(builtins, kind, None)
    if not (isinstance(cls, type) and issubclass(cls, Exception)) or kind in (
        "SystemExit",
        "KeyboardInterrupt",
    ):
        cls = RuntimeError
        message = f"{kind}: {message}" if message else kind
    try:
        error = cls(message)
    except Exception:
        error = RuntimeError(f"{kind}: {message}")
    error.sandbox_traceback = answer.get("traceback") or ""  # type: ignore[attr-defined]
    return error


# ── Code steps in a loaded flow ───────────────────────────────────────────────


def sandboxed(
    original: Callable[[Any], Any],
    info: dict[str, Any],
    settings: Callable[[], SandboxSettings],
    idempotency_key: Callable[[], str] | None = None,
) -> Callable[[Any], Any]:
    """A Code step's function that runs the step's code in the sandbox (as Settings say at
    the time of the call), or the original function when the sandbox is off.
    ``idempotency_key`` is the module's own helper, for steps that run at most once."""

    def run_step(data: Any) -> Any:
        current = settings()
        mode = effective_mode(current)
        if mode == "off":
            return original(data)
        if mode == "unavailable":
            raise SandboxUnavailable(
                "Code steps must run in the sandbox (Settings → Sandbox), but Docker can't be "
                f"used here: {docker_status()['reason']}"
            )
        return DockerSandbox(current).run(
            info["code"],
            data,
            requirements=info.get("requirements") or [],
            network=bool(info.get("network")),
            timeout=info.get("timeout"),
            idempotency_key=idempotency_key()
            if info.get("side_effect") and idempotency_key
            else None,
        )

    run_step.__name__ = getattr(original, "__name__", "run_step")
    run_step.__qualname__ = getattr(original, "__qualname__", run_step.__name__)
    run_step.__doc__ = original.__doc__
    run_step.__wrapped__ = original  # type: ignore[attr-defined]
    return run_step


def prepare(settings: SandboxSettings) -> dict[str, Any]:
    """Pull the sandbox image now, so the first Code step doesn't wait for it."""
    status = docker_status(refresh=True)
    if not status["available"]:
        return {"ok": False, "message": status["reason"]}
    docker = shutil.which("docker") or "docker"
    done = subprocess.run(
        [docker, "pull", settings.image], capture_output=True, text=True, timeout=900
    )
    if done.returncode != 0:
        detail = (done.stderr or done.stdout).strip().splitlines()
        return {"ok": False, "message": detail[-1] if detail else "docker pull failed"}
    return {"ok": True, "message": f"The sandbox image {settings.image} is ready."}
