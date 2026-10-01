"""Problems found while checking a flow, phrased for people, pinned to a step."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Level = Literal["error", "warning"]


@dataclass
class Fix:
    """A one-click fix the UI can offer next to a problem."""

    kind: str  # add_key | add_step | set_setting | connect
    label: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class Issue:
    level: Level
    code: str
    message: str
    step: str | None = None
    setting: str | None = None
    hint: str | None = None
    fix: Fix | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return {k: v for k, v in data.items() if v is not None}

    def __str__(self) -> str:
        where = f"[{self.step}] " if self.step else ""
        hint = f" {self.hint}" if self.hint else ""
        return f"{self.level}: {where}{self.message}{hint}"


def error(code: str, message: str, **kw: Any) -> Issue:
    return Issue("error", code, message, **kw)


def warning(code: str, message: str, **kw: Any) -> Issue:
    return Issue("warning", code, message, **kw)


class CompileError(Exception):
    """Raised when a flow has errors that stop it from compiling."""

    def __init__(self, issues: list[Issue]):
        self.issues = issues
        lines = "\n".join(f"  - {i}" for i in issues)
        super().__init__(f"The flow can't be compiled yet:\n{lines}")
