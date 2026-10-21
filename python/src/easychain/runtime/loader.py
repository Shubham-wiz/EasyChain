"""Load compiled flow source as a Python module.

The runtime executes exactly the code the compiler produced (the same code an
export contains). The only changes: the module's ``init_chat_model``, ``init_embeddings``
and ``mcp_tools`` names point at the gateway, and Code steps' functions run their code in
the sandbox (``sandbox.py``) when Settings say so.
"""

from __future__ import annotations

import hashlib
import sys
import threading
import types
from collections import OrderedDict
from typing import Any

from .gateway import (
    current_sandbox,
    gateway_init_chat_model,
    gateway_init_embeddings,
    gateway_mcp_tools,
)
from .resources import Resources, memory_resources
from .sandbox import sandboxed

_CACHE_SIZE = 64
_cache: OrderedDict[tuple[str, int], tuple[types.ModuleType, Any]] = OrderedDict()
_lock = threading.Lock()


def load_module(
    source: str,
    name: str = "easychain_flow",
    code_functions: dict[str, dict[str, Any]] | None = None,
) -> types.ModuleType:
    # A unique, registered module name lets typing and LangGraph resolve the module's names.
    unique = f"easychain_flows.{name}_{hashlib.sha256(source.encode()).hexdigest()[:12]}"
    module = types.ModuleType(unique)
    module.__file__ = f"<easychain:{name}>"
    sys.modules[unique] = module
    code = compile(source, module.__file__, "exec")
    try:
        exec(code, module.__dict__)  # noqa: S102 - this is the compiled flow
    except BaseException:
        sys.modules.pop(unique, None)
        raise
    if "init_chat_model" in module.__dict__:
        module.__dict__["init_chat_model"] = gateway_init_chat_model
    if "init_embeddings" in module.__dict__:
        module.__dict__["init_embeddings"] = gateway_init_embeddings
    if "mcp_tools" in module.__dict__:
        module.__dict__["mcp_tools"] = gateway_mcp_tools
    # Before build_graph() takes the functions: each call then asks the run's settings
    # whether to use the sandbox.
    for fn_name, info in (code_functions or {}).items():
        original = module.__dict__.get(fn_name)
        if callable(original):
            module.__dict__[fn_name] = sandboxed(
                original, info, current_sandbox, module.__dict__.get("idempotency_key")
            )
    return module


def load_graph(
    source: str,
    name: str = "easychain_flow",
    resources: Resources | None = None,
    code_functions: dict[str, dict[str, Any]] | None = None,
) -> tuple[types.ModuleType, Any]:
    """Return (module, compiled graph wired to the resources), cached by source. Pass the
    compile result's ``code_functions`` every time: a cached module keeps what it was loaded
    with, and Code steps run in the sandbox only if it was loaded with them."""
    res = resources or memory_resources()
    key = (hashlib.sha256(source.encode()).hexdigest(), id(res))
    with _lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    module = load_module(source, name, code_functions)
    graph = module.build_graph(checkpointer=res.checkpointer, store=res.store, cache=res.cache)
    with _lock:
        _cache[key] = (module, graph)
        while len(_cache) > _CACHE_SIZE:
            _, (old, _graph) = _cache.popitem(last=False)
            if not any(m is old for m, _ in _cache.values()):
                sys.modules.pop(old.__name__, None)
    return module, graph
