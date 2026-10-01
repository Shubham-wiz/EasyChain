"""Quality bar from the spec: Easy Chain adds under 10 ms per step on top of LangGraph,
and large flows (300+ steps) stay quick to check and compile."""

from __future__ import annotations

import statistics
import time

from easychain.compiler import compile_flow, validate
from easychain.runtime import RunOptions, run_flow
from easychain.runtime.loader import CHECKPOINTER, load_graph

from .conftest import input_step, make_spec, output_step


def chain(n: int):
    steps = [input_step({"name": "n", "type": "number"})]
    conns = []
    prev = "input"
    for i in range(n):
        sid = f"step_{i}"
        steps.append(
            {
                "id": sid,
                "type": "code",
                "settings": {"code": "def run(data):\n    return {'n': data['n'] + 1}\n"},
            }
        )
        conns.append((prev, sid))
        prev = sid
    steps.append(output_step("n"))
    conns.append((prev, "output"))
    return make_spec(steps, conns, name=f"Chain of {n}")


async def test_runtime_overhead_per_step_is_under_10ms():
    n = 40
    spec = chain(n)
    compiled = compile_flow(spec)
    _, graph = load_graph(compiled.source, compiled.module_name)

    def direct() -> float:
        start = time.perf_counter()
        graph.invoke(
            {"n": 0},
            {"configurable": {"thread_id": f"direct-{time.time()}"}, "recursion_limit": 1000},
        )
        return time.perf_counter() - start

    async def easychain() -> float:
        start = time.perf_counter()
        final, _ = await run_flow(spec, {"n": 0}, RunOptions(recursion_limit=1000))
        assert final["output"] == {"n": n}
        return time.perf_counter() - start

    direct()
    await easychain()  # warm caches
    base = statistics.median(direct() for _ in range(5))
    ours = statistics.median([await easychain() for _ in range(5)])
    per_step_ms = (ours - base) / n * 1000
    print(
        f"LangGraph {base * 1000:.1f} ms, Easy Chain {ours * 1000:.1f} ms, overhead {per_step_ms:.2f} ms/step"
    )
    assert per_step_ms < 10
    assert CHECKPOINTER is not None


def test_300_step_flow_checks_and_compiles_quickly():
    spec = chain(300)
    start = time.perf_counter()
    issues = validate(spec)
    checked = time.perf_counter() - start
    start = time.perf_counter()
    compiled = compile_flow(spec)
    compiled_in = time.perf_counter() - start
    print(f"check {checked * 1000:.0f} ms, compile {compiled_in * 1000:.0f} ms")
    assert issues == []
    assert compiled.source.count("def step_") == 300
    assert checked < 2.0 and compiled_in < 3.0
