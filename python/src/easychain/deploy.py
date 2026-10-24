"""``easychain publish`` and ``easychain deploy`` (Phase 5.4, docs/publish.md).

``publish`` is for CI: it asks an Easy Chain server, with a key, to run the flow's Test Set
in the target environment (a kept Test Run) and, when every case passes, to publish that
version there. The server checks the same rules as the app: staging takes a version whose Test
Run in staging passed; production takes the version in staging, from an Owner's key.

``deploy`` packs a folder of flows for a self-hosted server:

- ``easychain deploy compose DIR``: a Compose bundle (Easy Chain, a worker and Postgres) with
  the flows inside its image, ready for ``docker compose up``;
- ``easychain deploy image NAME``: the same image, built with Docker.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import __version__

FLOW_FILES = ("*.flow.yaml", "*.tests.yaml", "*.baseline.json")


class DeployError(RuntimeError):
    """Something that keeps a publish or a deploy from going ahead; the message says why."""


# ── publish, through a server's API ─────────────────────────────────────────


def publish_flow(
    client: Any,
    flow_id: str,
    environment: str,
    *,
    repeat: int = 1,
    variables: dict[str, str] | None = None,
    timeout: float = 1800,
    poll: float = 2,
    say: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Publish ``flow_id`` to ``environment`` through ``client`` (an httpx client with the
    server's address, key and workspace). Staging first runs a Test Run there."""
    test_run_id = None
    if environment == "staging":
        started = _call(
            client.post(
                f"/api/flows/{flow_id}/test-runs",
                json={"environment": "staging", "repeat": repeat, "variables": variables or {}},
            )
        )
        say(f"Running the Test Set in Staging (Test Run {started['id']})…")
        deadline = time.monotonic() + timeout
        while True:
            found = _call(client.get(f"/api/test-runs/{started['id']}"))
            if found["status"] in ("done", "error"):
                break
            if time.monotonic() > deadline:
                raise DeployError(f"The Test Run didn't finish in {timeout:g} seconds.")
            time.sleep(poll)
        if found["status"] == "error":
            raise DeployError(found.get("error") or "The Test Run didn't finish.")
        s = found["summary"]
        say(f"{s['passed']}/{s['total']} cases passed.")
        failing = [c for c in found["cases"] if c["passed"] < c["runs"]]
        if failing:
            lines = [f"  {c['name']}: {'; '.join(c['failures'])}" for c in failing]
            raise DeployError("Not published: some cases failed.\n" + "\n".join(lines))
        test_run_id = found["id"]
    body: dict[str, Any] = {"environment": environment}
    if test_run_id:
        body["test_run_id"] = test_run_id
    made = _call(client.post(f"/api/flows/{flow_id}/publish", json=body))
    say(f"Published version {made['version_id'][:7]} of {flow_id} to {environment}.")
    return made


def _call(response: Any) -> dict[str, Any]:
    if response.status_code >= 400:
        try:
            message = response.json().get("message") or response.text
        except ValueError:
            message = response.text
        raise DeployError(f"The server said: {message} ({response.status_code})")
    return response.json()


# ── deploy: a Compose bundle or an image ────────────────────────────────────

DOCKERFILE = """# Easy Chain with these flows inside (made by `easychain deploy`).
FROM {base}
COPY --chown=easychain flows /app/flows
ENV EASYCHAIN_WORKSPACE=/app/flows
"""

COMPOSE = """# Made by `easychain deploy compose`. Start it with:  docker compose up -d
# Then open http://localhost:${{EASYCHAIN_PORT:-8000}}; the first account (an Owner) needs the
# code that `docker compose logs easychain` shows. See README.md here.
x-easychain-env: &easychain-env
  EASYCHAIN_DATABASE_URL: postgresql://easychain:${{POSTGRES_PASSWORD:-easychain}}@db:5432/easychain
  EASYCHAIN_PUBLIC_URL: ${{EASYCHAIN_PUBLIC_URL:-http://localhost:8000}}
  EASYCHAIN_SECRET_KEY: ${{EASYCHAIN_SECRET_KEY:-}}
  OPENAI_API_KEY: ${{OPENAI_API_KEY:-}}
  ANTHROPIC_API_KEY: ${{ANTHROPIC_API_KEY:-}}

services:
  db:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_USER: easychain
      POSTGRES_PASSWORD: ${{POSTGRES_PASSWORD:-easychain}}
      POSTGRES_DB: easychain
    volumes:
      - postgres-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U easychain -d easychain"]
      interval: 3s
      timeout: 3s
      retries: 30
    restart: unless-stopped

  easychain:
    build: .
    image: {image}
    ports:
      - "127.0.0.1:${{EASYCHAIN_PORT:-8000}}:8000"
    environment:
      <<: *easychain-env
      EASYCHAIN_WORKER: "off"
    volumes:
      - easychain-data:/data
    depends_on:
      db:
        condition: service_healthy
    restart: unless-stopped

  worker:
    image: {image}
    command: ["easychain", "worker"]
    environment:
      <<: *easychain-env
    volumes:
      - easychain-data:/data
    depends_on:
      db:
        condition: service_healthy
      easychain:
        condition: service_started
    healthcheck:
      disable: true
    restart: unless-stopped

volumes:
  postgres-data:
  easychain-data:
"""

ENV_EXAMPLE = """# Copy to .env and fill in. Keys can also be added in the app (Settings).
EASYCHAIN_PUBLIC_URL=https://easychain.example.com
EASYCHAIN_PORT=8000
POSTGRES_PASSWORD=change-me
# A Fernet key, set before the first secret is saved, keeps the vault readable on another
# machine: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
EASYCHAIN_SECRET_KEY=
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
"""

README = """# {name}

Easy Chain {version} with {count} flow(s), made by `easychain deploy compose`.

1. If the image `{base}` isn't on this machine, build it from Easy Chain's source:
   `docker build -t {base} PATH/TO/EasyChain`.
2. Copy `.env.example` to `.env` and fill it in.
3. `docker compose up -d`, then open the address in `EASYCHAIN_PUBLIC_URL`. The first account
   (an Owner) needs the code in `docker compose logs easychain`.
4. Publish the flows (the editor's Publish button, or `easychain publish` from CI) and make
   access keys or chat links for them.

The flows live in the image (`flows/` here). Runs, secrets and people live in the volumes, so
rebuilding with new flows (`docker compose up -d --build`) keeps them.
"""


def _flow_files(folder: Path) -> list[Path]:
    files = sorted({p for pattern in FLOW_FILES for p in folder.glob(pattern)})
    if not any(p.name.endswith(".flow.yaml") for p in files):
        raise DeployError(f"There are no flows (*.flow.yaml) in {folder}.")
    return files


def _stage(flows: Path, into: Path) -> int:
    """Copy the flows, their Test Sets and baselines, and the skills into ``into/flows``."""
    target = into / "flows"
    target.mkdir(parents=True, exist_ok=True)
    files = _flow_files(flows)
    for path in files:
        shutil.copy2(path, target / path.name)
    if (flows / "skills").is_dir():
        shutil.copytree(flows / "skills", target / "skills", dirs_exist_ok=True)
    return sum(1 for p in files if p.name.endswith(".flow.yaml"))


def default_base() -> str:
    return f"easychain:{__version__}"


def compose_bundle(
    flows: Path, out: Path, *, image: str = "easychain-flows:latest", base: str | None = None
) -> int:
    """Write a Compose bundle to ``out``; returns how many flows it holds."""
    base = base or default_base()
    if out.exists() and any(out.iterdir()):
        raise DeployError(f"{out} isn't empty; give a new folder.")
    out.mkdir(parents=True, exist_ok=True)
    count = _stage(flows, out)
    (out / "Dockerfile").write_text(DOCKERFILE.format(base=base), encoding="utf-8", newline="\n")
    (out / "docker-compose.yml").write_text(
        COMPOSE.format(image=image), encoding="utf-8", newline="\n"
    )
    (out / ".env.example").write_text(ENV_EXAMPLE, encoding="utf-8", newline="\n")
    (out / "README.md").write_text(
        README.format(name=out.name, version=__version__, count=count, base=base),
        encoding="utf-8",
        newline="\n",
    )
    return count


def build_image(
    flows: Path,
    name: str,
    *,
    base: str | None = None,
    run: Callable[..., Any] = subprocess.run,
) -> int:
    """Build a Docker image ``name``: Easy Chain with the flows inside."""
    docker = shutil.which("docker")
    if docker is None:
        raise DeployError(
            "Docker isn't installed here. Make a Compose bundle instead (easychain deploy "
            "compose DIR) and build it where Docker is."
        )
    with tempfile.TemporaryDirectory(prefix="easychain-deploy-") as folder:
        context = Path(folder)
        count = _stage(flows, context)
        (context / "Dockerfile").write_text(
            DOCKERFILE.format(base=base or default_base()), encoding="utf-8", newline="\n"
        )
        done = run([docker, "build", "-t", name, str(context)], check=False)
        if getattr(done, "returncode", 1) != 0:
            raise DeployError(
                f"docker build failed. If the base image {base or default_base()} is missing, "
                "build it from Easy Chain's source first: docker build -t "
                f"{base or default_base()} PATH/TO/EasyChain"
            )
    return count
