"""A small encrypted secrets vault for a single-user local install.

Secrets are referenced by name (e.g. OPENAI_API_KEY). Values are encrypted at
rest with Fernet, never returned by the API, never written to flow files,
exports or traces. Generated code and LangChain providers read them through
``os.environ``: scoped_env.py lays the vault of the work in hand over the
process's environment (``mapping``), without exporting them into it.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from types import MappingProxyType
from urllib.parse import unquote, urlsplit, urlunsplit

from cryptography.fernet import Fernet, InvalidToken

from .. import scoped_env

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


_TOKEN_LIKE = re.compile(r"^(?=.*\d)[A-Za-z0-9_\-.~%]{16,}$|^[A-Za-z0-9_\-.~%]{32,}$")


def masked_url(value: str | None) -> str:
    """A saved webhook or server address as the API shows it. The site stays visible, but a
    password, query values, a fragment and path parts that look like tokens are hidden.
    ``{secret:NAME}`` references are safe to show and are left as they are."""
    if not value:
        return ""
    if _REFERENCE.fullmatch(value):
        return value
    try:
        parts = urlsplit(value)
    except ValueError:
        return MASK
    if not parts.scheme or not parts.netloc:
        return MASK

    def keep(text: str) -> str:
        return text if _REFERENCE.fullmatch(unquote(text)) else MASK

    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    if parts.username:
        host = f"{parts.username}:{MASK}@{host}" if parts.password else f"{parts.username}@{host}"
    path = "/".join(
        MASK
        if _TOKEN_LIKE.match(segment) and not _REFERENCE.fullmatch(unquote(segment))
        else segment
        for segment in parts.path.split("/")
    )
    query = "&".join(
        f"{name}={keep(val)}" if val else name
        for name, val in (
            (pair.split("=", 1) + [""])[:2] for pair in parts.query.split("&") if pair
        )
    )
    return urlunsplit((parts.scheme, host, path, query, MASK if parts.fragment else ""))


def unmasked_url(value: str | None, saved: str | None) -> str:
    """What to save when a client sends an address back: the hidden form of the saved address,
    unchanged, keeps it. A hidden part in any other address can't be saved."""
    if saved and value == masked_url(saved):
        return saved
    if value and MASK in value:
        raise ValueError(
            "Part of this address is hidden (••••••). Type the whole address again to change it."
        )
    return value or ""


def unmasked(value: str | None, saved: str | None) -> str:
    """What to save when a client sends a setting back: the mask, unchanged, keeps the saved
    value."""
    return (saved or "") if value == MASK else (value or "")


class VaultLocked(ValueError):
    """The saved secrets can't be read with this key, so they must not be overwritten."""


class SecretStore:
    def __init__(self, home: Path, key_dir: Path | None = None):
        """The vault in ``home``. ``key_dir`` holds ``secret.key`` when it isn't ``home``: every
        workspace's vault is encrypted with the server's one key (or EASYCHAIN_SECRET_KEY)."""
        self.home = home
        self.key_dir = key_dir or home
        self.path = home / "secrets.enc"
        self._lock = threading.Lock()
        self._values: dict[str, str] = {}
        # What scoped_env lays over os.environ: always the current values, read-only.
        self.mapping = MappingProxyType(self._values)
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
                if scoped_env.real().get("EASYCHAIN_SECRET_KEY")
                else str(self.key_dir / "secret.key")
            )
            raise ValueError(
                f"The secrets key in {where} isn't a valid key: it must be 32 random bytes in "
                f"URL-safe base64 (44 characters), not a password. {NEW_KEY_HINT}"
            ) from None

    def _key(self) -> bytes:
        env_key = scoped_env.real().get("EASYCHAIN_SECRET_KEY")
        if env_key:
            return env_key.strip().encode()
        key_path = self.key_dir / "secret.key"
        if key_path.exists():
            return key_path.read_bytes().strip()
        self.key_dir.mkdir(parents=True, exist_ok=True)
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
                f"with (or unset it to use {self.key_dir / 'secret.key'}). To start again without "
                "them, move secrets.enc somewhere else and restart."
            )
            return
        self.problem = None
        # Secrets deleted by another process go away here too.
        for name in set(self._values) - set(data):
            del self._values[name]
        self._values.update(data)

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

    def names(self) -> list[dict[str, str]]:
        with self._lock:
            return [{"name": n, "source": "vault"} for n in sorted(self._values)]

    def has(self, name: str) -> bool:
        return name in self._values or bool(scoped_env.real().get(name))

    def source(self, name: str) -> str | None:
        if name in self._values:
            return "vault"
        if scoped_env.real().get(name):
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
            self._save()

    def delete(self, name: str) -> bool:
        with self._lock:
            if self.problem:
                raise VaultLocked(self.problem)
            if name not in self._values:
                return False
            del self._values[name]
            self._save()
            return True
