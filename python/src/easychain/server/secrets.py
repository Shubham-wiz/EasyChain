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


class SecretStore:
    def __init__(self, home: Path):
        self.home = home
        self.path = home / "secrets.enc"
        self._lock = threading.Lock()
        self._values: dict[str, str] = {}
        self._env_before: dict[str, str | None] = {}
        self._fernet = Fernet(self._key())
        self._load()

    def _key(self) -> bytes:
        env_key = os.environ.get("EASYCHAIN_SECRET_KEY")
        if env_key:
            return env_key.encode()
        key_path = self.home / "secret.key"
        if key_path.exists():
            return key_path.read_bytes().strip()
        self.home.mkdir(parents=True, exist_ok=True)
        key = Fernet.generate_key()
        key_path.write_bytes(key)
        key_path.chmod(0o600)
        return key

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self._fernet.decrypt(self.path.read_bytes()))
        except (InvalidToken, ValueError):
            # Wrong key or damaged file: start empty rather than crash, keep the file.
            return
        for name, value in data.items():
            self._values[name] = value
            self._export(name, value)

    def _save(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        token = self._fernet.encrypt(json.dumps(self._values).encode())
        tmp = self.path.with_suffix(".tmp")
        tmp.write_bytes(token)
        tmp.chmod(0o600)
        tmp.replace(self.path)

    def _export(self, name: str, value: str) -> None:
        if name not in self._env_before:
            self._env_before[name] = os.environ.get(name)
        os.environ[name] = value

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
            self._values[name] = value
            self._export(name, value)
            self._save()

    def delete(self, name: str) -> bool:
        with self._lock:
            if name not in self._values:
                return False
            del self._values[name]
            previous = self._env_before.pop(name, None)
            if previous is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = previous
            self._save()
            return True
