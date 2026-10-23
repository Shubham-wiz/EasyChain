"""Each workspace's long-term memory kept apart (Phase 5.6d).

Flows keep memories and side-effect records in LangGraph's store, under namespaces the flow
chooses (``("memories", user_id)``). One server's workspaces share one store, so the graph gets
a ``WorkspaceStore`` instead: in any workspace but the default one, every namespace is put under
``("easychain-workspace", <id>)``. Flows still see their own namespaces as they wrote them.

The default workspace keeps its namespaces as they always were. It can't name the prefix
itself, and anything under it is left out of its searches and listings.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from langgraph.store.base import (
    BaseStore,
    GetOp,
    InvalidNamespaceError,
    Item,
    ListNamespacesOp,
    MatchCondition,
    PutOp,
    SearchItem,
    SearchOp,
)

from .. import current_workspace

MARK = "easychain-workspace"


def _prefix() -> tuple[str, ...]:
    workspace = current_workspace.current()
    return () if workspace == current_workspace.DEFAULT else (MARK, workspace)


def _refuse_mark(path: tuple[str, ...]) -> None:
    if path and path[0] == MARK:
        raise InvalidNamespaceError(
            f"Namespaces starting with “{MARK}” are kept for Easy Chain's workspaces."
        )


class WorkspaceStore(BaseStore):
    """A store that keeps each workspace's namespaces apart (see the module's docstring)."""

    def __init__(self, inner: BaseStore):
        self.inner = inner
        self.supports_ttl = getattr(inner, "supports_ttl", False)
        self.ttl_config = getattr(inner, "ttl_config", None)

    def batch(self, ops: Iterable[Any]) -> list[Any]:
        prefix = _prefix()
        ops = list(ops)
        found = self.inner.batch([_inside(op, prefix) for op in ops])
        return [_outside(op, result, prefix) for op, result in zip(ops, found, strict=True)]

    async def abatch(self, ops: Iterable[Any]) -> list[Any]:
        prefix = _prefix()
        ops = list(ops)
        found = await self.inner.abatch([_inside(op, prefix) for op in ops])
        return [_outside(op, result, prefix) for op, result in zip(ops, found, strict=True)]


def _inside(op: Any, prefix: tuple[str, ...]) -> Any:
    """The operation, on the workspace's own namespaces."""
    if isinstance(op, (GetOp, PutOp)):
        if not prefix:
            _refuse_mark(tuple(op.namespace))
        return op._replace(namespace=prefix + tuple(op.namespace))
    if isinstance(op, SearchOp):
        if not prefix:
            _refuse_mark(tuple(op.namespace_prefix))
        return op._replace(namespace_prefix=prefix + tuple(op.namespace_prefix))
    if isinstance(op, ListNamespacesOp):
        conditions = list(op.match_conditions or ())
        if not prefix:
            for condition in conditions:
                if condition.match_type == "prefix":
                    _refuse_mark(tuple(condition.path))
            return op
        moved = [
            MatchCondition("prefix", prefix + tuple(c.path)) if c.match_type == "prefix" else c
            for c in conditions
        ]
        if not any(c.match_type == "prefix" for c in conditions):
            moved.insert(0, MatchCondition("prefix", prefix))
        depth = op.max_depth + len(prefix) if op.max_depth is not None else None
        return op._replace(match_conditions=tuple(moved), max_depth=depth)
    return op


def _outside(op: Any, result: Any, prefix: tuple[str, ...]) -> Any:
    """The result, with namespaces as the flow wrote them."""
    if isinstance(op, GetOp):
        return _item(result, prefix) if result is not None else None
    if isinstance(op, SearchOp):
        return [_item(found, prefix) for found in result if _ours(found.namespace, prefix)]
    if isinstance(op, ListNamespacesOp):
        return [tuple(ns)[len(prefix) :] for ns in result if _ours(tuple(ns), prefix)]
    return result


def _ours(namespace: tuple[str, ...], prefix: tuple[str, ...]) -> bool:
    if prefix:
        return tuple(namespace[: len(prefix)]) == prefix
    return not namespace or namespace[0] != MARK


def _item(found: Item, prefix: tuple[str, ...]) -> Item:
    """A copy with the workspace's prefix taken off (the store's own item stays as it is)."""
    if not prefix:
        return found
    namespace = tuple(found.namespace)[len(prefix) :]
    if isinstance(found, SearchItem):
        return SearchItem(
            namespace,
            found.key,
            found.value,
            found.created_at,
            found.updated_at,
            found.score,
        )
    return Item(
        value=found.value,
        key=found.key,
        namespace=namespace,
        created_at=found.created_at,
        updated_at=found.updated_at,
    )
