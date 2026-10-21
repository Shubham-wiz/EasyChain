"""The Skills library: reusable instructions (and the files they refer to) for Deep Agents and
Helpers, in the Agent Skills format.

A skill is a folder with a ``SKILL.md`` whose YAML front matter names and describes it::

    skills/report-writing/
        SKILL.md          ---\\nname: report-writing\\ndescription: How to write …\\n---\\n…
        outline.md        (optional files the instructions refer to)

The workspace's skills live in ``<workspace>/skills/``; Easy Chain ships some built-in ones
(``templates/skills/``). A workspace skill with the same name as a built-in one replaces it.
An agent sees each of its skills' names and descriptions, and reads a skill's instructions
when its description matches the task (deepagents' ``SkillsMiddleware``). The compiler puts the
skills a flow uses into its generated code, so exported code carries them.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_NAME = 64
MAX_DESCRIPTION = 1024
MAX_FILES = 30
MAX_FILE_CHARS = 100_000
BUILT_IN = Path(__file__).parent / "templates" / "skills"
_FRONT = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.S)


class SkillError(ValueError):
    """A skill that can't be saved or used, with a message that says what to change."""


@dataclass
class Skill:
    name: str
    description: str
    files: dict[str, str] = field(default_factory=dict)
    built_in: bool = False
    # A workspace skill named like a built-in one, which it replaces.
    replaces_built_in: bool = False

    def summary(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "files": sorted(self.files),
            "built_in": self.built_in,
            "replaces_built_in": self.replaces_built_in,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.summary(), "files": dict(sorted(self.files.items()))}


def front_matter(text: str) -> dict[str, Any]:
    """The YAML front matter of a SKILL.md (empty when it has none)."""
    match = _FRONT.match(text.lstrip("﻿"))
    if not match:
        return {}
    try:
        data = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def check_skill(name: str, files: dict[str, str]) -> Skill:
    """A valid skill from its files, or SkillError saying what is wrong."""
    if not NAME_RE.match(name) or len(name) > MAX_NAME:
        raise SkillError(
            "A skill's name is lowercase letters, digits and dashes, like report-writing "
            f"(at most {MAX_NAME} characters)."
        )
    if "SKILL.md" not in files:
        raise SkillError("A skill needs a SKILL.md file with its instructions.")
    if len(files) > MAX_FILES:
        raise SkillError(f"A skill can have at most {MAX_FILES} files.")
    for path, text in files.items():
        parts = PurePosixPath(path).parts
        if not parts or path.startswith("/") or any(p in ("", ".", "..") for p in parts):
            raise SkillError(f"“{path}” isn't a file name inside the skill, like notes/outline.md.")
        if not isinstance(text, str):
            raise SkillError(f"“{path}” must be text.")
        if len(text) > MAX_FILE_CHARS:
            raise SkillError(f"“{path}” is too long (at most {MAX_FILE_CHARS:,} characters).")
    meta = front_matter(files["SKILL.md"])
    if not meta:
        raise SkillError(
            "SKILL.md must start with front matter: a line ---, then name: and description:, "
            "then another line ---."
        )
    if meta.get("name") != name:
        raise SkillError(f"The name in SKILL.md must be the skill's name, “{name}”.")
    description = str(meta.get("description") or "").strip()
    if not description:
        raise SkillError(
            "SKILL.md needs a description: what the skill is for and when to use it. "
            "The agent reads it to decide when to open the skill."
        )
    if len(description) > MAX_DESCRIPTION:
        raise SkillError(f"The description is too long (at most {MAX_DESCRIPTION} characters).")
    return Skill(name, description, dict(files))


def _read(folder: Path, built_in: bool) -> Skill | None:
    files: dict[str, str] = {}
    for path in sorted(folder.rglob("*")):
        if path.is_file() and not path.name.startswith("."):
            try:
                files[path.relative_to(folder).as_posix()] = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
    try:
        skill = check_skill(folder.name, files)
    except SkillError:
        return None
    skill.built_in = built_in
    return skill


class SkillLibrary:
    """A workspace's skills folder, over Easy Chain's built-in skills."""

    def __init__(self, folder: Path | None, built_in: Path | None = BUILT_IN):
        self.folder = folder
        self.built_in = built_in
        self._lock = threading.Lock()

    def _own(self, name: str) -> Path | None:
        if self.folder is None or not NAME_RE.match(name):
            return None
        return self.folder / name

    def get(self, name: str) -> Skill | None:
        if not NAME_RE.match(name or ""):
            return None
        own = self._own(name)
        shipped = self.built_in is not None and (self.built_in / name / "SKILL.md").is_file()
        if own is not None and (own / "SKILL.md").is_file():
            skill = _read(own, built_in=False)
            if skill is not None:
                skill.replaces_built_in = shipped
                return skill
        if shipped:
            return _read(self.built_in / name, built_in=True)  # type: ignore[operator]
        return None

    def list(self) -> list[Skill]:
        found: dict[str, Skill] = {}
        for root, built_in in ((self.built_in, True), (self.folder, False)):
            if root is None or not root.is_dir():
                continue
            for folder in sorted(root.iterdir()):
                if (folder / "SKILL.md").is_file():
                    skill = _read(folder, built_in)
                    if skill is not None:
                        skill.replaces_built_in = not built_in and skill.name in found
                        found[skill.name] = skill
        return [found[name] for name in sorted(found)]

    def save(self, name: str, files: dict[str, str]) -> Skill:
        skill = check_skill(name, files)
        target = self._own(name)
        if target is None:
            raise SkillError("This Easy Chain has no workspace folder to save skills in.")
        with self._lock:
            tmp = target.with_name(f".{name}.saving")
            shutil.rmtree(tmp, ignore_errors=True)
            for path, text in files.items():
                out = tmp / path
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(text, encoding="utf-8", newline="\n")
            old = target.with_name(f".{name}.old")
            shutil.rmtree(old, ignore_errors=True)
            if target.exists():
                target.replace(old)
            tmp.replace(target)
            shutil.rmtree(old, ignore_errors=True)
        return skill

    def delete(self, name: str) -> bool:
        target = self._own(name)
        if target is None or not target.is_dir():
            return False
        with self._lock:
            shutil.rmtree(target)
        return True


def used_skills(spec: Any) -> list[str]:
    """The skills a flow's Deep Agents and Helpers use, by name."""
    return sorted(
        {
            name
            for step in spec.steps
            if step.type in ("deep_agent", "helper")
            for name in step.settings.skills
        }
    )


def skills_key(spec: Any) -> str:
    """What a flow's skills say now: part of the compile cache's key, so that editing a skill
    compiles the flow again."""
    lib = library()
    parts = []
    for name in used_skills(spec):
        skill = lib.get(name)
        text = "-" if skill is None else json.dumps(skill.files, sort_keys=True)
        parts.append(f"{name}:{hashlib.sha256(text.encode()).hexdigest()}")
    return "|".join(parts)


_library: SkillLibrary | None = None


def default_workspace() -> Path:
    home = Path(os.environ.get("EASYCHAIN_HOME") or Path.home() / ".easychain")
    return Path(os.environ.get("EASYCHAIN_WORKSPACE") or home / "flows")


def library() -> SkillLibrary:
    """The process's Skills library: the one set with ``use_library``, or the default
    workspace's (``EASYCHAIN_WORKSPACE``, else ``<EASYCHAIN_HOME>/flows``)."""
    return _library or SkillLibrary(default_workspace() / "skills")


def use_library(lib: SkillLibrary | None) -> None:
    """Make ``lib`` the library flows are checked and compiled with (None: the default)."""
    global _library
    _library = lib
