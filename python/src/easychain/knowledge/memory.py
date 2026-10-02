"""Long-term memory in the LangGraph store: remember facts about a user, recall them later.

Used by the Memory step and the Agent's memory add-on. Like ``knowledge.search``,
these functions are copied into generated code, so they may only use the standard
library, LangChain and LangGraph.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from langchain_core.tools import BaseTool, tool
from langgraph.config import get_config, get_store


def memory_namespace(user: Any = None) -> tuple[str, str]:
    """Where a user's memories live: ("memories", user id), or the conversation without one."""
    if not user:
        user = get_config()["configurable"].get("thread_id", "anyone")
    return ("memories", str(user))


def memory_store() -> Any:
    store = get_store()
    if store is None:
        raise RuntimeError(
            "Long-term memory needs a LangGraph store: build_graph(store=InMemoryStore()), "
            "or a database store."
        )
    return store


def remember_fact(namespace: tuple[str, str], fact: str) -> None:
    """Save one fact."""
    if fact.strip():
        memory_store().put(namespace, uuid.uuid4().hex, {"fact": fact.strip()})


def recall_facts(namespace: tuple[str, str], query: str = "", limit: int = 10) -> list[str]:
    """Remembered facts, the ones sharing most words with the query first (newest first otherwise)."""
    items = memory_store().search(namespace, limit=1000)
    items.sort(key=lambda item: item.created_at, reverse=True)
    facts = [item.value.get("fact", "") for item in items]
    if query:

        def stems(text: str) -> set[str]:
            return {w[:4] for w in re.findall(r"[a-z0-9]{3,}", text.lower())}

        wanted = stems(query)
        facts.sort(key=lambda fact: -len(wanted & stems(fact)))
    return facts[:limit]


def memory_tools(data: dict[str, Any]) -> list[BaseTool]:
    """Long-term memory for an agent: remember facts about the user and recall them later.

    Memories belong to the `user_id` field when the flow has one, otherwise to the
    conversation.
    """
    namespace = memory_namespace(data.get("user_id"))

    @tool("remember", description="Save a fact about the user, to use in later conversations.")
    def remember(fact: str) -> str:
        remember_fact(namespace, fact)
        return "Remembered."

    @tool("recall", description="Look up what was remembered about the user before.")
    def recall(query: str = "") -> str:
        facts = recall_facts(namespace, query)
        return "\n".join(f"- {fact}" for fact in facts) or "Nothing remembered yet."

    return [remember, recall]


HELPER_FUNCTIONS = (memory_namespace, memory_store, remember_fact, recall_facts)
