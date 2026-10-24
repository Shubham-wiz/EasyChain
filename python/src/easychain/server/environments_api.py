"""Environments of a workspace (Phase 5.4, docs/publish.md): dev, staging and production, each
with its own secrets (over the workspace's), model swaps and Knowledge Base swaps.

Editors see them; Owners change them. A run of a published flow, and a Test Run started in an
environment, use them (spaces.py, environments.py).
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from ..environments import ENVIRONMENTS, LABELS
from ..knowledge.store import KnowledgeStore
from .audit import note
from .secrets import VaultLocked
from .spaces import space_of

MODEL = re.compile(r"^[a-z][a-z0-9_-]*:[^\s]+$")


class EnvironmentSettings(BaseModel):
    # {model the flow names: model to use instead}, both as provider:model
    models: dict[str, str] = Field(default_factory=dict)
    # {Knowledge Base the flow names: Knowledge Base to search instead}
    knowledge: dict[str, str] = Field(default_factory=dict)


class SecretValue(BaseModel):
    value: str


def known(environment: str) -> str:
    if environment not in ENVIRONMENTS:
        raise HTTPException(
            404,
            detail={
                "message": f"There's no environment “{environment}”: use {', '.join(ENVIRONMENTS)}."
            },
        )
    return environment


def add_environment_routes(app: FastAPI) -> None:
    def db() -> Any:
        return app.state.hub.db

    async def _settings(request: Request, environment: str) -> dict[str, Any]:
        key = space_of(request).setting(f"environment:{environment}")
        return EnvironmentSettings.model_validate(
            await db().get_setting(key, {}) or {}
        ).model_dump()

    @app.get("/api/environments")
    async def list_environments(request: Request) -> dict[str, Any]:
        """Each environment's model and Knowledge Base swaps, and its secrets' names."""
        space = space_of(request)
        shown = []
        for environment in ENVIRONMENTS:
            vault = space.env_vault(environment)
            if vault is not None:
                vault.reload()
            shown.append(
                {
                    "id": environment,
                    "label": LABELS[environment],
                    **await _settings(request, environment),
                    "secrets": sorted(s["name"] for s in vault.names()) if vault else [],
                }
            )
        return {"environments": shown}

    @app.put("/api/environments/{environment}")
    async def save_environment(
        environment: str, body: EnvironmentSettings, request: Request
    ) -> dict[str, Any]:
        known(environment)
        problems = [
            f"“{m}” isn't a model as provider:model (like openai:gpt-4.1)."
            for pair in body.models.items()
            for m in pair
            if not MODEL.match(m)
        ]
        if body.knowledge:
            store = KnowledgeStore().setup()
            workspace = space_of(request).id
            for kb in body.knowledge.values():
                if store.get_base(kb, workspace) is None:
                    problems.append(f"There's no Knowledge Base “{kb}” in this workspace.")
        if problems:
            raise HTTPException(
                422, detail={"message": "These swaps can't be saved yet.", "problems": problems}
            )
        key = space_of(request).setting(f"environment:{environment}")
        await db().set_setting(key, body.model_dump())
        note(request, environment, models=len(body.models), knowledge=len(body.knowledge))
        return {"id": environment, "label": LABELS[environment], **body.model_dump()}

    @app.put("/api/environments/{environment}/secrets/{name}")
    def set_environment_secret(
        environment: str, name: str, body: SecretValue, request: Request
    ) -> dict[str, Any]:
        vault = space_of(request).env_vault(known(environment))
        try:
            vault.set(name, body.value.strip())
        except VaultLocked as exc:
            raise HTTPException(409, detail={"message": str(exc)}) from exc
        except ValueError as exc:
            raise HTTPException(422, detail={"message": str(exc)}) from exc
        note(request, f"{environment}/{name}")
        return {"environment": environment, "name": name, "saved": True}

    @app.delete("/api/environments/{environment}/secrets/{name}")
    def delete_environment_secret(environment: str, name: str, request: Request) -> dict[str, Any]:
        vault = space_of(request).env_vault(known(environment))
        try:
            deleted = vault.delete(name)
        except VaultLocked as exc:
            raise HTTPException(409, detail={"message": str(exc)}) from exc
        if not deleted:
            raise HTTPException(404, detail={"message": "No secret with that name is saved there."})
        note(request, f"{environment}/{name}")
        return {"environment": environment, "name": name, "deleted": True}
