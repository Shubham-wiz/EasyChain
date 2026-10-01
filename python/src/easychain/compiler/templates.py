"""``{variable}`` placeholders in Instructions and Web request settings.

Easy Chain treats ``{name}`` (a Flow Data field) and ``{secret:NAME}`` (a secret
read from the environment) as placeholders. Every other brace is literal text,
so people can paste JSON into a prompt without escaping anything; the compiler
doubles those braces when it writes a LangChain f-string template.
"""

from __future__ import annotations

import re

VAR_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
SECRET_RE = re.compile(r"\{secret:([A-Za-z_][A-Za-z0-9_]*)\}")
TOKEN_RE = re.compile(r"\{(?:secret:)?[A-Za-z_][A-Za-z0-9_]*\}")


def variables(text: str) -> list[str]:
    """Field names used as ``{name}``, in order of first use."""
    seen: dict[str, None] = {}
    for match in VAR_RE.finditer(text or ""):
        seen.setdefault(match.group(1), None)
    return list(seen)


def secrets(text: str) -> list[str]:
    seen: dict[str, None] = {}
    for match in SECRET_RE.finditer(text or ""):
        seen.setdefault(match.group(1), None)
    return list(seen)


def to_fstring_template(text: str) -> str:
    """Escape literal braces so LangChain's f-string templates keep them as text."""
    out: list[str] = []
    pos = 0
    for match in VAR_RE.finditer(text):
        out.append(text[pos : match.start()].replace("{", "{{").replace("}", "}}"))
        out.append(match.group(0))
        pos = match.end()
    out.append(text[pos:].replace("{", "{{").replace("}", "}}"))
    return "".join(out)
