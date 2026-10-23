"""Secrets reach the work in hand through os.environ without being exported into the process
(scoped_env.py, Phase 5.6d): each request or run sees its workspace's vault, at the same time
as others see theirs; child processes see none of them."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

from easychain import scoped_env
from easychain.server.secrets import SecretStore


@pytest.fixture(autouse=True)
def installed():
    scoped_env.install()
    yield
    scoped_env.set_default(None)


def test_the_vaults_secrets_are_read_but_never_exported(tmp_path: Path):
    vault = SecretStore(tmp_path / "home")
    vault.set("TEAM_TOKEN", "tok-default")
    scoped_env.set_default(lambda: vault.mapping)
    assert os.environ["TEAM_TOKEN"] == "tok-default"
    assert os.getenv("TEAM_TOKEN") == "tok-default"
    assert "TEAM_TOKEN" in os.environ
    assert "TEAM_TOKEN" not in scoped_env.real(), "not in the process's own environment"
    child = subprocess.run(
        [sys.executable, "-c", "import os; print(os.environ.get('TEAM_TOKEN', 'none'))"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert child.stdout.strip() == "none", "child processes don't inherit secrets"
    vault.delete("TEAM_TOKEN")
    assert "TEAM_TOKEN" not in os.environ


async def test_each_piece_of_work_sees_only_its_own_layer():
    seen: dict[str, list[str | None]] = {"a": [], "b": []}

    async def work(name: str, layer: dict[str, str]) -> None:
        with scoped_env.using(layer):
            for _ in range(5):
                seen[name].append(os.environ.get("OPENAI_API_KEY"))
                await asyncio.sleep(0)
                # Threads started for the work see it too (contexts are copied).
                seen[name].append(await asyncio.to_thread(os.getenv, "OPENAI_API_KEY"))

    await asyncio.gather(
        work("a", {"OPENAI_API_KEY": "sk-a"}), work("b", {"OPENAI_API_KEY": "sk-b"})
    )
    assert set(seen["a"]) == {"sk-a"} and set(seen["b"]) == {"sk-b"}


def test_a_layer_wins_over_the_environment_and_writes_go_to_the_process(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("SHARED_KEY", "from-the-server")
    with scoped_env.using({"SHARED_KEY": "from-the-vault", "ONLY_HERE": "v"}):
        assert os.environ["SHARED_KEY"] == "from-the-vault"
        assert dict(os.environ)["SHARED_KEY"] == "from-the-vault"
        os.environ["SET_HERE"] = "1"
        del os.environ["ONLY_HERE"]  # a secret in hand: not the process's to remove
        assert os.environ["ONLY_HERE"] == "v"
    assert os.environ["SHARED_KEY"] == "from-the-server", "the server's own keys stay"
    assert scoped_env.real()["SET_HERE"] == "1"
    del os.environ["SET_HERE"]
    with scoped_env.using({}):
        assert os.environ["SHARED_KEY"] == "from-the-server", (
            "keys the server was started with apply everywhere"
        )
