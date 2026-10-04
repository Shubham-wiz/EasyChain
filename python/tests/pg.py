"""A throwaway Postgres for tests.

Uses EASYCHAIN_TEST_POSTGRES_URL when set (CI runs a postgres service). Otherwise, if the
Postgres server binaries are installed, starts a temporary cluster on a free port.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import socket
import subprocess
import tempfile
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

BIN_DIRS = [
    Path(p)
    for p in (
        "/usr/lib/postgresql/17/bin",
        "/usr/lib/postgresql/16/bin",
        "/usr/lib/postgresql/15/bin",
    )
]


def _bin(name: str) -> str | None:
    for folder in BIN_DIRS:
        if (folder / name).exists():
            return str(folder / name)
    return shutil.which(name)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _is_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0  # there is no root on Windows


def _as_postgres(cmd: list[str]) -> list[str]:
    """initdb and postgres refuse to run as root; run them as the postgres user."""
    if _is_root() and shutil.which("runuser"):
        return ["runuser", "-u", "postgres", "--", *cmd]
    return cmd


def available() -> bool:
    if os.environ.get("EASYCHAIN_TEST_POSTGRES_URL"):
        return True
    if not (_bin("initdb") and _bin("pg_ctl")):
        return False
    if _is_root():
        try:
            import pwd

            pwd.getpwnam("postgres")
        except KeyError:
            return False
    return True


@contextlib.contextmanager
def server() -> Iterator[str]:
    """Yield a postgresql:// URL for an empty database."""
    url = os.environ.get("EASYCHAIN_TEST_POSTGRES_URL")
    if url:
        yield _fresh_database(url)
        return
    initdb, pg_ctl = _bin("initdb"), _bin("pg_ctl")
    assert initdb and pg_ctl
    folder = Path(tempfile.mkdtemp(prefix="easychain-pg-"))
    data = folder / "data"
    port = _free_port()
    if _is_root():
        shutil.chown(folder, "postgres", "postgres")
    subprocess.run(
        _as_postgres([initdb, "-D", str(data), "-U", "postgres", "--auth=trust", "-E", "UTF8"]),
        check=True,
        capture_output=True,
    )
    log = folder / "log.txt"
    subprocess.run(
        _as_postgres(
            [
                pg_ctl,
                "-D",
                str(data),
                "-l",
                str(log),
                "-o",
                # Windows has no Unix socket folder by default; elsewhere it goes in the temp folder.
                f"-p {port}{'' if os.name == 'nt' else f' -k {folder}'} "
                "-c listen_addresses=127.0.0.1 -c fsync=off",
                "-w",
                "start",
            ]
        ),
        check=True,
        # Not captured: the server keeps the output handles open, and on Windows reading them
        # would wait for the server to exit. Its messages go to the log file instead.
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        base = f"postgresql://postgres@127.0.0.1:{port}/postgres"
        _wait(base)
        yield base
    finally:
        subprocess.run(
            _as_postgres([pg_ctl, "-D", str(data), "-m", "immediate", "stop"]), capture_output=True
        )
        shutil.rmtree(folder, ignore_errors=True)


def _wait(url: str) -> None:
    import psycopg

    for _ in range(100):
        try:
            with psycopg.connect(url, connect_timeout=2):
                return
        except psycopg.OperationalError:
            time.sleep(0.1)
    raise RuntimeError("Postgres didn't start")


def _fresh_database(url: str) -> str:
    import psycopg

    name = f"easychain_test_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    return url.rsplit("/", 1)[0] + f"/{name}"


def new_database(base_url: str) -> str:
    """A new empty database on the same server."""
    return _fresh_database(base_url)
