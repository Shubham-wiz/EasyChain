"""Email trigger: new messages in an IMAP mailbox start runs.

Messages are read without marking them, the runs are started, and only then are the messages
marked as seen, so a message is never lost between the two.
"""

from __future__ import annotations

import contextlib
import email
import email.policy
import imaplib
import os
import re
from typing import Any

MAX_PER_POLL = 20


def _secret(text: str) -> str:
    return re.sub(
        r"\{secret:([A-Za-z_][A-Za-z0-9_]*)\}",
        lambda m: os.environ.get(m.group(1), ""),
        str(text or ""),
    )


def _connect(cfg: dict[str, Any]) -> imaplib.IMAP4:
    host = cfg.get("host", "")
    port = int(cfg.get("port") or (993 if cfg.get("ssl", True) else 143))
    imap = (
        imaplib.IMAP4_SSL(host, port, timeout=30)
        if cfg.get("ssl", True)
        else imaplib.IMAP4(host, port, timeout=30)
    )
    imap.login(_secret(cfg.get("username", "")), _secret(cfg.get("password", "")))
    imap.select(cfg.get("folder") or "INBOX")
    return imap


def fetch_unseen(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """New messages as {uid, subject, sender, to, date, message_id, body}, oldest first."""
    imap = _connect(cfg)
    try:
        _, found = imap.search(None, "UNSEEN")
        numbers = (found[0] or b"").split()[:MAX_PER_POLL]
        out = []
        for number in numbers:
            _, parts = imap.fetch(number, "(BODY.PEEK[])")
            raw = next((p[1] for p in parts if isinstance(p, tuple)), b"")
            msg = email.message_from_bytes(raw, policy=email.policy.default)
            body_part = msg.get_body(preferencelist=("plain", "html"))
            body = body_part.get_content() if body_part is not None else ""
            out.append(
                {
                    "uid": number.decode(),
                    "subject": str(msg["subject"] or ""),
                    "sender": str(msg["from"] or ""),
                    "to": str(msg["to"] or ""),
                    "date": str(msg["date"] or ""),
                    "message_id": str(msg["message-id"] or ""),
                    "body": body.strip(),
                }
            )
        return out
    finally:
        with contextlib.suppress(Exception):
            imap.logout()


def mark_seen(cfg: dict[str, Any], uids: list[str]) -> None:
    if not uids:
        return
    imap = _connect(cfg)
    try:
        for uid in uids:
            imap.store(uid, "+FLAGS", "\\Seen")
    finally:
        with contextlib.suppress(Exception):
            imap.logout()
