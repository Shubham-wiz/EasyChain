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


HELP_CENTRE = "help_centre"


def ensure_sample_knowledge(store: object | None = None) -> str:
    """The sample Knowledge Base the Support bot template searches (built once, offline)."""
    from ...knowledge.ingest import ingest
    from ...knowledge.loaders import load_bytes
    from ...knowledge.store import KnowledgeStore

    st = store if isinstance(store, KnowledgeStore) else KnowledgeStore().setup()
    files = sorted((HERE / "help").glob("*.md"))
    base = st.get_base(HELP_CENTRE)
    if base is not None:
        docs = st.list_documents(HELP_CENTRE)
        if len(docs) == len(files) and all(d["status"] == "ready" for d in docs):
            return HELP_CENTRE
        st.delete_base(HELP_CENTRE)
    st.create_base(
        "Help centre (sample)",
        "keywords",
        0,
        description="The help centre of Bean & Leaf, a sample coffee and tea shop.",
        chunk_size=600,
        chunk_overlap=60,
        kb_id=HELP_CENTRE,
    )
    for path in files:
        loaded = load_bytes(path.name, path.read_bytes())
        doc = st.add_document(HELP_CENTRE, loaded.title, path.name, loaded.kind)
        ingest(st, HELP_CENTRE, doc["id"], loaded, path.name)
    return HELP_CENTRE
