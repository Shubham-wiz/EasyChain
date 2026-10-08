"""Tell people when a run waits for them: a webhook, a Slack message or an email.

Settings live in the database (``notifications``); passwords and webhook URLs that are
secrets can be given as ``{secret:NAME}`` so they stay in the encrypted vault.
"""

from __future__ import annotations

import asyncio
import os
import re
import smtplib
from email.message import EmailMessage
from typing import Any

import httpx

from .secrets import masked, unmasked

DEFAULTS: dict[str, Any] = {
    "webhook": {"enabled": False, "url": ""},
    "slack": {"enabled": False, "webhook_url": ""},
    "email": {
        "enabled": False,
        "smtp_host": "",
        "smtp_port": 587,
        "starttls": True,
        "username": "",
        "password": "",
        "sender": "",
        "to": [],
    },
    "public_url": "",
}

# Settings the API never shows: (section, key).
SECRET_SETTINGS = (("slack", "webhook_url"), ("email", "password"))


def merged(saved: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    saved = saved or {}
    for key, default in DEFAULTS.items():
        if isinstance(default, dict):
            out[key] = {**default, **(saved.get(key) or {})}
        else:
            out[key] = saved.get(key, default)
    return out


def shown(saved: dict[str, Any] | None) -> dict[str, Any]:
    """The settings as the API returns them, with the password and Slack webhook hidden."""
    out = merged(saved)
    for section, key in SECRET_SETTINGS:
        out[section][key] = masked(out[section][key])
    return out


def to_save(sent: dict[str, Any] | None, saved: dict[str, Any] | None) -> dict[str, Any]:
    """Settings sent by a client; a hidden value sent back unchanged keeps the saved one."""
    out, before = merged(sent), merged(saved)
    for section, key in SECRET_SETTINGS:
        out[section][key] = unmasked(out[section][key], before[section][key])
    return out


def _secret(text: str) -> str:
    return re.sub(
        r"\{secret:([A-Za-z_][A-Za-z0-9_]*)\}", lambda m: os.environ.get(m.group(1), ""), text or ""
    )


def message_for(item: dict[str, Any], base_url: str) -> dict[str, Any]:
    request = item.get("request") or {}
    question = str(request.get("question") or "A step is waiting for an answer.")
    link = f"{base_url.rstrip('/')}/inbox/{item['id']}" if base_url else ""
    text = f"“{item.get('flow_name') or 'A flow'}” is waiting for you: {question}"
    return {"text": text, "question": question, "link": link}


async def send_all(config: dict[str, Any], item: dict[str, Any]) -> list[dict[str, Any]]:
    """Send every enabled notification for one Inbox item; returns what happened per channel."""
    cfg = merged(config)
    base_url = cfg.get("public_url") or os.environ.get("EASYCHAIN_PUBLIC_URL", "")
    msg = message_for(item, base_url)
    results: list[dict[str, Any]] = []
    if cfg["webhook"]["enabled"] and cfg["webhook"]["url"]:
        payload = {
            "event": "ask_human",
            "inbox_id": item["id"],
            "run_id": item["run_id"],
            "flow_id": item.get("flow_id"),
            "flow": item.get("flow_name"),
            "step": item.get("step"),
            "step_name": item.get("step_name") or item.get("step"),
            "question": msg["question"],
            "request": item.get("request"),
            "url": msg["link"],
        }
        results.append(await _post("webhook", _secret(cfg["webhook"]["url"]), payload))
    if cfg["slack"]["enabled"] and cfg["slack"]["webhook_url"]:
        text = msg["text"] + (f"\n<{msg['link']}|Open the Inbox>" if msg["link"] else "")
        results.append(await _post("slack", _secret(cfg["slack"]["webhook_url"]), {"text": text}))
    email = cfg["email"]
    if email["enabled"] and email["smtp_host"] and email["to"]:
        results.append(await asyncio.to_thread(_send_email, email, msg))
    return results


async def _post(channel: str, url: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
        return {"channel": channel, "ok": True}
    except Exception as exc:  # a failed notification must never break the run
        return {"channel": channel, "ok": False, "error": str(exc)[:300]}


def subject_line(text: str, limit: int = 150) -> str:
    """An email subject: one line (a header can't hold line breaks), at most ``limit`` long.
    The whole question is in the body."""
    line = " ".join(text.split())
    return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"


def _send_email(email: dict[str, Any], msg: dict[str, Any]) -> dict[str, Any]:
    try:
        message = EmailMessage()
        message["Subject"] = subject_line(msg["text"])
        message["From"] = email["sender"] or email["username"] or "easychain@localhost"
        recipients = email["to"] if isinstance(email["to"], list) else [email["to"]]
        message["To"] = ", ".join(recipients)
        body = msg["text"] + (f"\n\nAnswer it here: {msg['link']}\n" if msg["link"] else "\n")
        message.set_content(body)
        with smtplib.SMTP(email["smtp_host"], int(email["smtp_port"]), timeout=15) as smtp:
            if email.get("starttls"):
                smtp.starttls()
            if email.get("username"):
                smtp.login(email["username"], _secret(email.get("password", "")))
            smtp.send_message(message)
        return {"channel": "email", "ok": True}
    except Exception as exc:
        return {"channel": "email", "ok": False, "error": str(exc)[:300]}
