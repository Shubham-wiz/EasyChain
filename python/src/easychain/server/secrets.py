"""A small encrypted secrets vault for a single-user local install.

Secrets are referenced by name (e.g. OPENAI_API_KEY). Values are encrypted at
rest with Fernet, never returned by the API, never written to flow files,
exports or traces. On load they are placed in the server's environment so that
generated code (which reads ``os.environ``) and LangChain providers find them.
Phase 5 replaces this with a per-workspace vault (KMS/Vault backed).
"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")

# How settings that hold a password, token or webhook URL are shown by the API.
MASK = "••••••"
# "{secret:NAME}", optionally after an auth scheme ("Bearer {secret:NAME}"): it only names a
# secret in the vault, so it is safe to show.
_REFERENCE = re.compile(r"\s*(?:[A-Za-z][A-Za-z0-9_-]*\s+)?\{secret:[A-Za-z_][A-Za-z0-9_]*\}\s*")

NEW_KEY_HINT = (
    'Make one with: python -c "from cryptography.fernet import Fernet; '
    'print(Fernet.generate_key().decode())"'
)


def masked(value: str | None) -> str:
    """A saved password, token or webhook URL as the API shows it: hidden, unless it is a
    ``{secret:NAME}`` reference."""
    if not value:
        return ""
    return value if _REFERENCE.fullmatch(value) else MASK


def unmasked(value: str | None, saved: str | None) -> str:
    """What to save when a client sends a setting back: the mask, unchanged, keeps the saved
    value."""
    return (saved or "") if value == MASK else (value or "")


class VaultLocked(ValueError):
    """The saved secrets can't be read with this key, so they must not be overwritten."""


class SecretStore:
    def __init__(self, home: Path):
        self.home = home
        self.path = home / "secrets.enc"
        self._lock = threading.Lock()
        self._values: dict[str, str] = {}
        self._env_before: dict[str, str | None] = {}
        self._mtime: float | None = None
        # Why the saved secrets can't be read (wrong key, damaged file); None when they can.
        self.problem: str | None = None
        self._fernet = self._make_fernet()
        self._load()

    def _make_fernet(self) -> Fernet:
        key = self._key()
        try:
            return Fernet(key)
        except ValueError:
            where = (
                "EASYCHAIN_SECRET_KEY"
                if os.environ.get("EASYCHAIN_SECRET_KEY")
                else str(self.home / "secret.key")
            )
            raise ValueError(
                f"The secrets key in {where} isn't a valid key: it must be 32 random bytes in "
                f"URL-safe base64 (44 characters), not a password. {NEW_KEY_HINT}"
            ) from None

    def _key(self) -> bytes:
        env_key = os.environ.get("EASYCHAIN_SECRET_KEY")
        if env_key:
            return env_key.strip().encode()
        key_path = self.home / "secret.key"
        if key_path.exists():
            return key_path.read_bytes().strip()
        self.home.mkdir(parents=True, exist_ok=True)
        key = Fernet.generate_key()
        key_path.write_bytes(key)
        key_path.chmod(0o600)
        return key

    def reload(self) -> None:
        """Pick up secrets saved by another process (an API server next to this worker)."""
        try:
            mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            return
        if mtime == self._mtime:
            return
        with self._lock:
            self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        self._mtime = self.path.stat().st_mtime
        try:
            data = json.loads(self._fernet.decrypt(self.path.read_bytes()))
        except (InvalidToken, ValueError):
            # Wrong key or damaged file: keep running, but never write over the file, or every
            # secret in it would be lost for good.
            self.problem = (
                f"The saved secrets in {self.path} can't be read with this key. If "
                "EASYCHAIN_SECRET_KEY was set or changed, set it back to the key they were saved "
                f"with (or unset it to use {self.home / 'secret.key'}). To start again without "
                "them, move secrets.enc somewhere else and restart."
            )
            return
        self.problem = None
        # Secrets deleted by another process go away here too.
        for name in set(self._values) - set(data):
            del self._values[name]
            self._unexport(name)
        for name, value in data.items():
            self._values[name] = value
            self._export(name, value)

    def _save(self) -> None:
        if self.problem:
            raise VaultLocked(self.problem)
        self.home.mkdir(parents=True, exist_ok=True)
        token = self._fernet.encrypt(json.dumps(self._values).encode())
        tmp = self.path.with_suffix(".tmp")
        tmp.write_bytes(token)
        tmp.chmod(0o600)
        tmp.replace(self.path)
        self._mtime = self.path.stat().st_mtime

    def _export(self, name: str, value: str) -> None:
        if name not in self._env_before:
            self._env_before[name] = os.environ.get(name)
        os.environ[name] = value

    def _unexport(self, name: str) -> None:
        previous = self._env_before.pop(name, None)
        if previous is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = previous

    def names(self) -> list[dict[str, str]]:
        with self._lock:
            return [{"name": n, "source": "vault"} for n in sorted(self._values)]

    def has(self, name: str) -> bool:
        return name in self._values or bool(os.environ.get(name))

    def source(self, name: str) -> str | None:
        if name in self._values:
            return "vault"
        if os.environ.get(name):
            return "environment"
        return None

    def values(self) -> list[str]:
        with self._lock:
            return list(self._values.values())

    def set(self, name: str, value: str) -> None:
        if not NAME_RE.match(name):
            raise ValueError(
                "Secret names use capital letters, digits and underscores, like MY_API_KEY."
            )
        if not value:
            raise ValueError("The secret is empty.")
        with self._lock:
            if self.problem:
                raise VaultLocked(self.problem)
            self._values[name] = value
            self._export(name, value)
            self._save()

    def delete(self, name: str) -> bool:
        with self._lock:
            if self.problem:
                raise VaultLocked(self.problem)
            if name not in self._values:
                return False
            del self._values[name]
            self._unexport(name)
            self._save()
            return True
