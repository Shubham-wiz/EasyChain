"""Sample data the templates use: the shop database and the help-centre documents."""

from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path

HERE = Path(__file__).parent


def home() -> Path:
    return Path(os.environ.get("EASYCHAIN_HOME") or Path.home() / ".easychain")


def ensure_samples(easychain_home: Path | None = None) -> Path:
    """Build {home}/samples/shop.db from shop.sql (again when shop.sql changes)."""
    folder = (easychain_home or home()) / "samples"
    folder.mkdir(parents=True, exist_ok=True)
    script = (HERE / "shop.sql").read_text(encoding="utf-8")
    version = re.search(r"sample_version: (\d+)", script)
    wanted = int(version.group(1)) if version else 1
    target = folder / "shop.db"
    if target.exists():
        with sqlite3.connect(target) as conn:
            if conn.execute("PRAGMA user_version").fetchone()[0] == wanted:
                return target
        target.unlink()
    tmp = folder / f".shop-{os.getpid()}.db"
    with sqlite3.connect(tmp) as conn:
        conn.executescript(script)
        conn.execute(f"PRAGMA user_version = {wanted}")
    tmp.replace(target)
    return target
