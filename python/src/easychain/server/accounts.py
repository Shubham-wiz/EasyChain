"""People, their roles, signed-in sessions and API keys, in the run database (server/auth.py
says how they are used)."""

from __future__ import annotations

import hmac
import secrets
import time
from typing import Any

import sqlalchemy as sa

from .auth import (
    DEFAULT_WORKSPACE,
    ROLES,
    SESSION_DAYS,
    Principal,
    hash_password,
    new_api_key,
    new_session_token,
    rank,
    split_api_key,
    token_hash,
    verify_password,
)
from .db import (
    Database,
    api_keys,
    memberships,
    new_id,
    sessions,
    settings,
    user_identities,
    users,
)

SETUP_KEY = "auth_setup_code"
# The first account's code: 12 letters and digits that can't be mistaken for each other
# (60 bits, so it can't be guessed), shown as ABCD-EFGH-JKLM.
CODE_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class AccountError(ValueError):
    """Something a person asked for that can't be done; the message says why."""


def _norm(email: str) -> str:
    return email.strip().lower()


class Accounts:
    def __init__(self, db: Database):
        self.db = db

    # ── people ───────────────────────────────────────────────────────────────

    async def count(self) -> int:
        async with self.db.reader.connect() as conn:
            return int((await conn.execute(sa.select(sa.func.count()).select_from(users))).scalar())

    async def create(
        self,
        email: str,
        name: str,
        password: str | None,
        role: str,
        workspace: str = DEFAULT_WORKSPACE,
        *,
        first: bool = False,
    ) -> dict[str, Any]:
        """Add a person with a role. ``first``: only if nobody exists yet (the first Owner)."""
        email = _norm(email)
        if "@" not in email or len(email) > 320:
            raise AccountError("That doesn't look like an email address.")
        if role not in ROLES:
            raise AccountError(f"The role must be one of: {', '.join(ROLES)}.")
        now = time.time()
        user_id = new_id()
        async with self.db._tx() as conn:
            if first and (await conn.execute(sa.select(users.c.id).limit(1))).first():
                raise AccountError("Easy Chain already has its first account; sign in instead.")
            if (await conn.execute(sa.select(users.c.id).where(users.c.email == email))).first():
                raise AccountError(f"{email} already has an account.")
            await conn.execute(
                users.insert().values(
                    id=user_id,
                    email=email,
                    name=name.strip()[:200],
                    password_hash=hash_password(password) if password else None,
                    disabled=False,
                    created_at=now,
                )
            )
            await conn.execute(
                memberships.insert().values(
                    workspace_id=workspace, user_id=user_id, role=role, created_at=now
                )
            )
        return await self.member(user_id, workspace) or {}

    async def member(
        self, user_id: str, workspace: str = DEFAULT_WORKSPACE
    ) -> dict[str, Any] | None:
        query = (
            sa.select(
                users.c.id,
                users.c.email,
                users.c.name,
                users.c.disabled,
                users.c.created_at,
                users.c.last_login_at,
                (users.c.password_hash.is_not(None)).label("has_password"),
                memberships.c.role,
            )
            .select_from(users.join(memberships, memberships.c.user_id == users.c.id))
            .where(users.c.id == user_id, memberships.c.workspace_id == workspace)
        )
        async with self.db.reader.connect() as conn:
            row = (await conn.execute(query)).first()
        return dict(row._mapping) if row else None

    async def members(self, workspace: str = DEFAULT_WORKSPACE) -> list[dict[str, Any]]:
        query = (
            sa.select(
                users.c.id,
                users.c.email,
                users.c.name,
                users.c.disabled,
                users.c.created_at,
                users.c.last_login_at,
                memberships.c.role,
            )
            .select_from(users.join(memberships, memberships.c.user_id == users.c.id))
            .where(memberships.c.workspace_id == workspace)
            .order_by(users.c.created_at)
        )
        async with self.db.reader.connect() as conn:
            return [dict(r._mapping) for r in await conn.execute(query)]

    async def by_email(self, email: str) -> dict[str, Any] | None:
        async with self.db.reader.connect() as conn:
            row = (
                await conn.execute(sa.select(users).where(users.c.email == _norm(email)))
            ).first()
        return dict(row._mapping) if row else None

    async def _owners(self, conn: Any, workspace: str) -> list[str]:
        rows = await conn.execute(
            sa.select(memberships.c.user_id)
            .select_from(memberships.join(users, users.c.id == memberships.c.user_id))
            .where(
                memberships.c.workspace_id == workspace,
                memberships.c.role == "owner",
                users.c.disabled.is_(False),
            )
        )
        return [r[0] for r in rows]

    async def update(
        self,
        user_id: str,
        *,
        role: str | None = None,
        disabled: bool | None = None,
        name: str | None = None,
        workspace: str = DEFAULT_WORKSPACE,
    ) -> dict[str, Any]:
        if role is not None and role not in ROLES:
            raise AccountError(f"The role must be one of: {', '.join(ROLES)}.")
        async with self.db._tx() as conn:
            found = (
                await conn.execute(
                    sa.select(memberships.c.role).where(
                        memberships.c.user_id == user_id, memberships.c.workspace_id == workspace
                    )
                )
            ).first()
            if found is None:
                raise AccountError("That person isn't in this workspace.")
            losing_owner = found[0] == "owner" and (
                (role is not None and role != "owner") or disabled is True
            )
            if losing_owner and (await self._owners(conn, workspace)) == [user_id]:
                raise AccountError(
                    "This is the only Owner. Make someone else an Owner first, so the workspace "
                    "always has one."
                )
            if role is not None:
                await conn.execute(
                    memberships.update()
                    .where(
                        memberships.c.user_id == user_id, memberships.c.workspace_id == workspace
                    )
                    .values(role=role)
                )
            values: dict[str, Any] = {}
            if disabled is not None:
                values["disabled"] = disabled
            if name is not None:
                values["name"] = name.strip()[:200]
            if values:
                await conn.execute(users.update().where(users.c.id == user_id).values(**values))
            if disabled:
                await conn.execute(sessions.delete().where(sessions.c.user_id == user_id))
        return await self.member(user_id, workspace) or {}

    async def remove(self, user_id: str, workspace: str = DEFAULT_WORKSPACE) -> None:
        async with self.db._tx() as conn:
            owners = await self._owners(conn, workspace)
            if owners == [user_id]:
                raise AccountError(
                    "This is the only Owner. Make someone else an Owner first, so the workspace "
                    "always has one."
                )
            await conn.execute(
                memberships.delete().where(
                    memberships.c.user_id == user_id, memberships.c.workspace_id == workspace
                )
            )
            left = (
                await conn.execute(
                    sa.select(memberships.c.user_id).where(memberships.c.user_id == user_id)
                )
            ).first()
            if left is None:
                await conn.execute(sessions.delete().where(sessions.c.user_id == user_id))
                await conn.execute(
                    api_keys.update()
                    .where(api_keys.c.user_id == user_id, api_keys.c.revoked_at.is_(None))
                    .values(revoked_at=time.time())
                )
                await conn.execute(
                    user_identities.delete().where(user_identities.c.user_id == user_id)
                )
                await conn.execute(users.delete().where(users.c.id == user_id))

    async def set_password(
        self, user_id: str, password: str, keep_session: str | None = None
    ) -> None:
        """Change a password; every other signed-in browser of that person is signed out."""
        async with self.db._tx() as conn:
            await conn.execute(
                users.update()
                .where(users.c.id == user_id)
                .values(password_hash=hash_password(password))
            )
            query = sessions.delete().where(sessions.c.user_id == user_id)
            if keep_session:
                query = query.where(sessions.c.id != token_hash(keep_session))
            await conn.execute(query)

    async def check_password(self, email: str, password: str) -> dict[str, Any] | None:
        """The person, if the email and password match and the account is on."""
        person = await self.by_email(email)
        ok = verify_password(password, person["password_hash"] if person else None)
        if not ok or person is None or person["disabled"]:
            return None
        return person

    # ── single sign-on ───────────────────────────────────────────────────────

    async def by_identity(self, provider: str, subject: str) -> dict[str, Any] | None:
        """The account a provider's person was linked to, if any."""
        async with self.db.reader.connect() as conn:
            row = (
                await conn.execute(
                    sa.select(user_identities.c.user_id).where(
                        user_identities.c.provider == provider,
                        user_identities.c.subject == subject,
                    )
                )
            ).first()
        if row is None:
            return None
        async with self.db.reader.connect() as conn:
            person = (await conn.execute(sa.select(users).where(users.c.id == row[0]))).first()
        return dict(person._mapping) if person else None

    async def link_identity(self, user_id: str, provider: str, subject: str, email: str) -> None:
        now = time.time()
        async with self.db._tx() as conn:
            found = await conn.execute(
                user_identities.update()
                .where(user_identities.c.provider == provider, user_identities.c.subject == subject)
                .values(user_id=user_id, email=email, last_used_at=now)
            )
            if not found.rowcount:
                await conn.execute(
                    user_identities.insert().values(
                        provider=provider,
                        subject=subject,
                        user_id=user_id,
                        email=email,
                        created_at=now,
                        last_used_at=now,
                    )
                )

    # ── sessions ─────────────────────────────────────────────────────────────

    async def start_session(self, user_id: str) -> str:
        token = new_session_token()
        now = time.time()
        async with self.db._tx() as conn:
            await conn.execute(sessions.delete().where(sessions.c.expires_at < now))
            await conn.execute(
                sessions.insert().values(
                    id=token_hash(token),
                    user_id=user_id,
                    created_at=now,
                    expires_at=now + SESSION_DAYS * 86400,
                    last_seen_at=now,
                )
            )
            await conn.execute(
                users.update().where(users.c.id == user_id).values(last_login_at=now)
            )
        return token

    async def end_session(self, token: str) -> None:
        async with self.db._tx() as conn:
            await conn.execute(sessions.delete().where(sessions.c.id == token_hash(token)))

    async def _principal(
        self, user_id: str, via: str, role_cap: str | None = None, key_id: str | None = None
    ) -> Principal | None:
        member = await self.member(user_id)
        if member is None or member["disabled"]:
            return None
        role = member["role"]
        if role_cap is not None and rank(role_cap) < rank(role):
            role = role_cap
        return Principal(
            role=role,
            user_id=user_id,
            email=member["email"],
            name=member["name"],
            via=via,
            key_id=key_id,
        )

    async def principal_for_session(self, token: str) -> Principal | None:
        now = time.time()
        async with self.db.reader.connect() as conn:
            row = (
                await conn.execute(sa.select(sessions).where(sessions.c.id == token_hash(token)))
            ).first()
        if row is None or row.expires_at < now:
            return None
        if now - row.last_seen_at > 300:
            async with self.db._tx() as conn:
                await conn.execute(
                    sessions.update().where(sessions.c.id == row.id).values(last_seen_at=now)
                )
        return await self._principal(row.user_id, "session")

    # ── API keys ─────────────────────────────────────────────────────────────

    async def create_key(
        self, user_id: str, name: str, role: str, workspace: str = DEFAULT_WORKSPACE
    ) -> dict[str, Any]:
        member = await self.member(user_id, workspace)
        if member is None:
            raise AccountError("That person isn't in this workspace.")
        if role not in ROLES:
            raise AccountError(f"The role must be one of: {', '.join(ROLES)}.")
        if rank(role) > rank(member["role"]):
            raise AccountError("A key can't have a higher role than the person who makes it.")
        key_id, secret, key = new_api_key()
        now = time.time()
        async with self.db._tx() as conn:
            await conn.execute(
                api_keys.insert().values(
                    id=key_id,
                    user_id=user_id,
                    workspace_id=workspace,
                    name=name.strip()[:200] or "API key",
                    role=role,
                    secret_hash=token_hash(secret),
                    created_at=now,
                )
            )
        return {"id": key_id, "name": name, "role": role, "key": key, "created_at": now}

    async def keys(self, user_id: str | None = None) -> list[dict[str, Any]]:
        query = sa.select(
            api_keys.c.id,
            api_keys.c.user_id,
            api_keys.c.name,
            api_keys.c.role,
            api_keys.c.created_at,
            api_keys.c.last_used_at,
        ).where(api_keys.c.revoked_at.is_(None))
        if user_id is not None:
            query = query.where(api_keys.c.user_id == user_id)
        async with self.db.reader.connect() as conn:
            return [
                dict(r._mapping) for r in await conn.execute(query.order_by(api_keys.c.created_at))
            ]

    async def revoke_key(self, key_id: str, user_id: str | None = None) -> bool:
        query = api_keys.update().where(api_keys.c.id == key_id, api_keys.c.revoked_at.is_(None))
        if user_id is not None:
            query = query.where(api_keys.c.user_id == user_id)
        async with self.db._tx() as conn:
            done = await conn.execute(query.values(revoked_at=time.time()))
        return bool(done.rowcount)

    async def principal_for_key(self, key: str) -> Principal | None:
        parts = split_api_key(key)
        if parts is None:
            return None
        key_id, secret = parts
        async with self.db.reader.connect() as conn:
            row = (await conn.execute(sa.select(api_keys).where(api_keys.c.id == key_id))).first()
        if row is None or row.revoked_at is not None:
            return None
        if not hmac.compare_digest(row.secret_hash, token_hash(secret)):
            return None
        now = time.time()
        if row.last_used_at is None or now - row.last_used_at > 300:
            async with self.db._tx() as conn:
                await conn.execute(
                    api_keys.update().where(api_keys.c.id == key_id).values(last_used_at=now)
                )
        return await self._principal(row.user_id, "key", role_cap=row.role, key_id=key_id)

    # ── the first account ────────────────────────────────────────────────────

    async def setup_code(self) -> str | None:
        """While nobody has an account: a one-time code to make the first Owner (it is shown in
        the server's log, so only someone who can see the server can claim it)."""
        if await self.count():
            return None
        raw = "".join(secrets.choice(CODE_LETTERS) for _ in range(12))
        await self.db.set_setting(SETUP_KEY, {"hash": token_hash(raw), "at": time.time()})
        return "-".join(raw[i : i + 4] for i in range(0, 12, 4))

    async def check_setup_code(self, code: str) -> bool:
        saved = await self.db.get_setting(SETUP_KEY, None)
        if not saved or not code:
            return False
        raw = "".join(code.split()).replace("-", "").upper()
        return hmac.compare_digest(saved.get("hash", ""), token_hash(raw))

    async def forget_setup_code(self) -> None:
        async with self.db._tx() as conn:
            await conn.execute(settings.delete().where(settings.c.key == SETUP_KEY))
