"""Settings → Network: which addresses Easy Chain may reach (the egress policy, egress.py)."""

from __future__ import annotations

import time
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .. import egress
from .auth import auth_mode

# How long a process trusts the policy it read (another API server may change it).
FRESH_FOR = 10.0


class NetworkSettings(BaseModel):
    mode: str = "public"
    allow: list[str] = Field(default_factory=list, max_length=500)


async def policy(app: FastAPI, refresh: bool = False) -> egress.EgressPolicy:
    """The policy in force for this server (cached for a few seconds)."""
    cached = getattr(app.state, "egress_policy", None)
    if not refresh and cached is not None and time.monotonic() - cached[0] < FRESH_FOR:
        return cached[1]
    saved = await app.state.hub.db.get_setting("network", {})
    found = egress.policy_from(saved, signed_in=auth_mode() == "on")
    app.state.egress_policy = (time.monotonic(), found)
    return found


def add_network_routes(app: FastAPI) -> None:
    @app.get("/api/settings/network")
    async def get_network() -> dict[str, Any]:
        return {**(await policy(app, refresh=True)).to_dict(), "modes": list(egress.MODES)}

    @app.put("/api/settings/network")
    async def set_network(body: NetworkSettings) -> dict[str, Any]:
        current = await policy(app, refresh=True)
        if current.source == "environment":
            raise HTTPException(
                409,
                detail={
                    "message": "The server's environment sets this (EASYCHAIN_EGRESS), so it "
                    "can't be changed here. Change it where Easy Chain is started."
                },
            )
        allow = [a.strip() for a in body.allow if a.strip()]
        try:
            checked = egress.EgressPolicy(body.mode, tuple(dict.fromkeys(allow)), "settings")
        except ValueError as exc:
            raise HTTPException(422, detail={"message": str(exc)}) from exc
        await app.state.hub.db.set_setting(
            "network", {"mode": checked.mode, "allow": list(checked.allow)}
        )
        return {**(await policy(app, refresh=True)).to_dict(), "modes": list(egress.MODES)}
