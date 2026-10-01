"""Load compiled flow source as a Python module.

The runtime executes exactly the code the compiler produced (the same code an
export contains). The only change is that the module's ``init_chat_model``
name is pointed at the model gateway.
"""

from __future__ import annotations

import hashlib
import sys
import threading
import types
from collections import OrderedDict
from typing import Any

from .gateway import gateway_init_chat_model
from .resources import Resources, memory_resources

_CACHE_SIZE = 64
_cache: OrderedDict[tuple[str, int], tuple[types.ModuleType, Any]] = OrderedDict()
_lock = threading.Lock()


def load_module(source: str, name: str = "easychain_flow") -> types.ModuleType:
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
    return module


def load_graph(
    source: str, name: str = "easychain_flow", resources: Resources | None = None
) -> tuple[types.ModuleType, Any]:
    """Return (module, compiled graph wired to the resources), cached by source."""
    res = resources or memory_resources()
    key = (hashlib.sha256(source.encode()).hexdigest(), id(res))
    with _lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    module = load_module(source, name)
    graph = module.build_graph(checkpointer=res.checkpointer, store=res.store, cache=res.cache)
    with _lock:
        _cache[key] = (module, graph)
        while len(_cache) > _CACHE_SIZE:
            _, (old, _graph) = _cache.popitem(last=False)
            if not any(m is old for m, _ in _cache.values()):
                sys.modules.pop(old.__name__, None)
    return module, graph
