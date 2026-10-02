"""Step types available on the canvas.

The registry maps a step ``type`` (as written in the flow spec) to the handler
that checks it and compiles it to LangGraph code.
"""

from __future__ import annotations

from .actions import CodeHandler, HttpRequestHandler
from .agent import AgentHandler
from .ai import AIModelHandler, InstructionsHandler
from .base import FormField, StepCode, StepHandler
from .flow_control import AskHumanHandler, ForEachHandler, SubflowHandler
from .io_steps import InputHandler, OutputHandler
from .knowledge import KnowledgeSearchHandler
from .logic import DecisionHandler, JumpHandler
from .mcp import McpToolHandler
from .memory import MemoryHandler
from .sql import SqlQueryHandler

HANDLERS: dict[str, StepHandler] = {
    h.type: h
    for h in [
        InputHandler(),
        OutputHandler(),
        InstructionsHandler(),
        AIModelHandler(),
        AgentHandler(),
        KnowledgeSearchHandler(),
        MemoryHandler(),
        HttpRequestHandler(),
        CodeHandler(),
        SqlQueryHandler(),
        McpToolHandler(),
        DecisionHandler(),
        ForEachHandler(),
        JumpHandler(),
        SubflowHandler(),
        AskHumanHandler(),
    ]
}

CATEGORIES = [
    {"id": "start_end", "label": "Start and finish"},
    {"id": "ai", "label": "AI"},
    {"id": "knowledge", "label": "Knowledge and memory"},
    {"id": "actions", "label": "Actions"},
    {"id": "logic", "label": "Logic"},
    {"id": "people", "label": "People"},
]


def handler_for(step_type: str) -> StepHandler:
    try:
        return HANDLERS[step_type]
    except KeyError:
        raise KeyError(f"Unknown step type '{step_type}'") from None


def catalog() -> dict:
    return {"categories": CATEGORIES, "steps": [h.catalog() for h in HANDLERS.values()]}


__all__ = ["HANDLERS", "FormField", "StepCode", "StepHandler", "catalog", "handler_for"]
