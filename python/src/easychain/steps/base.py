"""Base class for step types.

A step type knows how to: describe itself in the Step library and inspector
(``catalog``), say which Flow Data fields it reads and writes, check its own
settings, and emit LangGraph code for itself. Adding a step type means adding
one subclass and registering it in ``easychain.steps``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from ..compiler.issues import Issue

if TYPE_CHECKING:
    from ..compiler.analysis import FlowAnalysis
    from ..compiler.codegen import EmitContext


@dataclass
class FormField:
    """One control in the step inspector form."""

    key: str
    label: str
    kind: str
    help: str = ""
    example: str | None = None
    placeholder: str | None = None
    options: list[dict[str, str]] | None = None
    advanced: bool = False
    pro: bool = False
    show_if: dict[str, Any] | None = None
    min: float | None = None
    max: float | None = None
    step: float | None = None
    technical: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None and v is not False}


@dataclass
class StepCode:
    """Generated code for one step."""

    definitions: list[str] = field(default_factory=list)
    node: str | None = None
    router: str | None = None
    # Lines for build_graph() that replace the usual edges (e.g. For Each's Send).
    wiring: list[str] | None = None
    # Extra add_node keyword arguments (e.g. destinations for a Jump).
    node_kwargs: dict[str, str] = field(default_factory=dict)
    # More add_node lines this step needs (e.g. For Each's "collect results" node).
    extra_nodes: list[str] = field(default_factory=list)
    # The node function is ``async def``.
    is_async: bool = False

    def text(self) -> str:
        return "\n\n\n".join(d.strip("\n") for d in self.definitions)


class StepHandler:
    type: str = ""
    label: str = ""
    technical: str = ""
    category: str = ""
    icon: str = "box"
    summary: str = ""
    beginner: bool = True
    has_node: bool = True
    default_name: str = ""
    form: list[FormField] = []

    # ── catalog ──────────────────────────────────────────────────────────────

    def catalog(self) -> dict[str, Any]:
        from ..spec.models import STEP_MODELS

        model = STEP_MODELS[self.type]
        settings_model = model.model_fields["settings"].default_factory()  # type: ignore[misc]
        return {
            "type": self.type,
            "label": self.label,
            "technical": self.technical,
            "category": self.category,
            "icon": self.icon,
            "summary": self.summary,
            "beginner": self.beginner,
            "default_name": self.default_name or self.label,
            "defaults": settings_model.model_dump(mode="json"),
            "form": [f.to_dict() for f in self.form],
            "docs": f"docs/steps/{self.type}.md",
        }

    # ── data flow ────────────────────────────────────────────────────────────

    def primary_output(self, step: Any) -> str | None:
        return getattr(step.settings, "save_as", None)

    def writes(self, step: Any, an: FlowAnalysis) -> dict[str, str]:
        return {}

    def reads(self, step: Any, an: FlowAnalysis) -> set[str]:
        return set()

    def exits(self, step: Any) -> list[str]:
        return []

    def is_async(self, step: Any, an: FlowAnalysis) -> bool:
        """Whether the step's node function is ``async def``."""
        return False

    def system_fields(self, step: Any, an: FlowAnalysis) -> list[Any]:
        """Bookkeeping Flow Data fields this step needs (FieldInfo objects)."""
        return []

    # ── checks and code ──────────────────────────────────────────────────────

    def check(self, step: Any, an: FlowAnalysis) -> list[Issue]:
        return []

    def emit(self, step: Any, ctx: EmitContext) -> StepCode:
        return StepCode()

    def title(self, step: Any) -> str:
        # One line: titles also go into comments of the generated code.
        return " ".join(f"{self.label} · {step.name or step.id}".split())


def template_value(text: str, ctx: EmitContext) -> str:
    """Python source for a value typed as text with {field} placeholders.

    A lone ``{field}`` keeps the field's value as it is (a list stays a list).
    """
    import re

    from ..compiler.pycode import py_str

    whole = re.fullmatch(r"\{([a-z][a-z0-9_]*)\}", text.strip())
    if whole:
        return f"data.get({py_str(whole.group(1))})"
    if re.search(r"\{(?:secret:)?[A-Za-z_][A-Za-z0-9_]*\}", text):
        return f"{ctx.helper('fill')}({py_str(text)}, data)"
    return py_str(text)
