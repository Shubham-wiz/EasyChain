"""Turn a flow spec into a LangGraph Python module.

This is the one compiler behind both Run and Export: the runtime executes
exactly the source produced here. The output only imports LangChain, LangGraph
and the standard library (plus httpx, which LangChain already depends on).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .. import __version__
from ..providers import PROVIDERS, split_model
from ..spec.models import SPEC_VERSION, FlowSpec
from .analysis import FlowAnalysis
from .helpers import HELPERS
from .issues import CompileError, Issue
from .pycode import Imports, Names, docstring, py_literal, py_str

_BASE_TYPES = {
    "text": "str",
    "number": "float",
    "yes_no": "bool",
    "list": "list[Any]",
    "object": "dict[str, Any]",
    "file": "str",
    "messages": "list[AnyMessage]",
    "any": "Any",
}


LANGGRAPH_REQ = "langgraph==1.2.12"
LANGCHAIN_REQS = ["langchain==1.4.3", "langchain-core==1.6.6"]


def section(title: str) -> str:
    line = f"# ── {title} "
    return line + "─" * max(4, 79 - len(line))


class EmitContext:
    """Shared state while emitting one module."""

    def __init__(self, analysis: FlowAnalysis):
        self.an = analysis
        self.imports = Imports()
        self.names = Names()
        self.helpers: dict[str, None] = {}
        self.fn_names: dict[str, str] = {}
        self.providers: set[str] = set()
        self.extra_requirements: list[str] = []
        self.uses_httpx = False
        for sid in analysis.order:
            if analysis.handlers[sid].has_node:
                self.fn_names[sid] = self.names.claim(sid)

    def fn(self, step_id: str) -> str:
        return self.fn_names[step_id]

    def helper(self, name: str) -> str:
        helper = HELPERS[name]
        self.helpers[name] = None
        for module in helper.imports:
            self.imports.add(module)
        for module, imported in helper.from_imports:
            self.imports.add_from(module, imported)
        return name

    def field_type(self, name: str | None) -> str:
        return self.an.field_type(name)

    def model_call(self, model: str, kwargs: dict[str, Any]) -> str:
        """``init_chat_model(...)`` for a provider:model string plus settings."""
        self.imports.add_from("langchain.chat_models", "init_chat_model")
        provider, _ = split_model(model)
        if provider:
            self.providers.add(provider)
        parts = [py_str(model)] + [f"{k}={py_literal(v)}" for k, v in kwargs.items()]
        return f"init_chat_model({', '.join(parts)})"


@dataclass
class CompiledFlow:
    source: str
    module_name: str
    snippets: dict[str, str]
    requirements: list[str]
    issues: list[Issue]
    chat: bool
    input_defaults: dict[str, Any]
    example_input: dict[str, Any]
    output_fields: list[str]
    routers: dict[str, str]
    analysis: FlowAnalysis = field(repr=False)


def module_name(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    if not slug or slug[0].isdigit():
        slug = f"flow_{slug}".rstrip("_")
    return slug


def compile_flow(spec: FlowSpec, *, allow_errors: bool = False) -> CompiledFlow:
    """Compile a flow spec. Raises CompileError if the flow has blocking errors."""
    from .validate import validate

    an = FlowAnalysis(spec)
    issues = validate(spec, analysis=an)
    errors = [i for i in issues if i.level == "error"]
    if errors and not allow_errors:
        raise CompileError(errors)
    return _emit_module(spec, an, issues)


def _emit_module(spec: FlowSpec, an: FlowAnalysis, issues: list[Issue]) -> CompiledFlow:
    ctx = EmitContext(an)
    ctx.imports.add_from("typing", "Any")
    ctx.imports.add_from("typing_extensions", "TypedDict")
    ctx.imports.add_from("langgraph.graph", "END", "START", "StateGraph")

    compiled_steps = [sid for sid in an.order if sid in an.reachable and an.handlers[sid].has_node]

    # Steps first: they register the imports and helpers they need.
    step_blocks: dict[str, str] = {}
    routers: dict[str, str] = {}
    snippets: dict[str, str] = {}
    for sid in an.order:
        handler = an.handlers[sid]
        step = an.steps[sid]
        if not handler.has_node:
            continue
        sub = EmitContext(an) if sid not in an.reachable else ctx
        if sub is not ctx:
            sub.fn_names = ctx.fn_names
        code = handler.emit(step, sub)
        text = code.text()
        if code.router:
            routers[sid] = code.router
        if sid in an.reachable:
            step_blocks[sid] = text
        snippets[sid] = (
            text
            + "\n\n\n# Wiring (inside build_graph)\n"
            + "\n".join(_wiring_for(sid, an, ctx, routers))
        )

    state_code = _emit_state(an, ctx)
    graph_code = _emit_build_graph(an, ctx, compiled_steps, routers)
    input_defaults = _input_defaults(an)
    example_input = _example_input(an)
    main_code = _emit_main(an, ctx, input_defaults, example_input)

    # Update-rule helpers are used in the Flow Data annotations, so they go first.
    reducers = [name for name in ctx.helpers if HELPERS[name].reducer]
    reducers_code = "\n\n\n".join(HELPERS[name].code.strip("\n") for name in reducers)
    helpers_code = "\n\n\n".join(
        HELPERS[name].code.strip("\n") for name in ctx.helpers if name not in reducers
    )
    mod = module_name(spec.name)
    header = _emit_header(spec, mod, an)

    flow_data = section("Flow Data") + "\n\n\n"
    if reducers_code:
        flow_data += reducers_code + "\n\n\n"
    parts = [header + "\n\n" + ctx.imports.render() + "\n\n" + flow_data + state_code]
    if input_defaults:
        parts.append("INPUT_DEFAULTS: dict[str, Any] = " + py_literal(input_defaults))
    if helpers_code:
        parts.append(section("Helpers") + "\n\n\n" + helpers_code)
    if step_blocks:
        parts.append(section("Steps") + "\n\n\n" + "\n\n\n".join(step_blocks.values()))
    unreachable = [
        an.steps[s] for s in an.order if s not in an.reachable and an.handlers[s].has_node
    ]
    if unreachable:
        names = ", ".join(f"{s.name or s.id} ({s.id})" for s in unreachable)
        parts.append(f"# Not connected to Input, so not included: {names}")
    parts.append(section("Flow") + "\n\n\n" + graph_code)
    parts.append("graph = build_graph()")
    parts.append(main_code)
    source = "\n\n\n".join(p.strip("\n") for p in parts) + "\n"
    source = re.sub(r"\n{4,}", "\n\n\n", source)

    return CompiledFlow(
        source=source,
        module_name=mod,
        snippets=snippets,
        requirements=_requirements(spec, ctx),
        issues=issues,
        chat=an.chat,
        input_defaults=input_defaults,
        example_input=example_input,
        output_fields=an.output_fields(),
        routers={sid: fn for sid, fn in routers.items() if sid in an.reachable},
        analysis=an,
    )


def _emit_header(spec: FlowSpec, mod: str, an: FlowAnalysis) -> str:
    lines = [spec.name, ""]
    if spec.description:
        lines += [spec.description.strip(), ""]
    lines.append(
        f"Generated by Easy Chain {__version__} from a version {SPEC_VERSION} flow spec. It needs only"
    )
    lines.append("LangChain and LangGraph: `pip install -r requirements.txt`, then")
    if an.chat:
        lines.append(f"`python {mod}.py` to chat in the terminal.")
    else:
        lines.append(f"`python {mod}.py '<inputs as JSON>'`.")
    lines += [
        "",
        "The compiled graph is `graph`; call `build_graph(checkpointer=...)` to keep Save Points.",
    ]
    return docstring("\n".join(lines), spaces=0)


def field_annotation(info: Any, ctx: EmitContext) -> str:
    base = _BASE_TYPES.get(info.type, "Any")
    if info.type == "messages":
        ctx.imports.add_from("langchain_core.messages", "AnyMessage")
    reducer = None
    if info.update in ("append", "add"):
        if info.type == "messages":
            ctx.imports.add_from("langgraph.graph.message", "add_messages")
            reducer = "add_messages"
        else:
            ctx.imports.add("operator")
            reducer = "operator.add"
    elif info.update == "merge":
        reducer = ctx.helper("merge_dicts")
    if reducer:
        ctx.imports.add_from("typing", "Annotated")
        return f"Annotated[{base}, {reducer}]"
    return base


def _emit_state(an: FlowAnalysis, ctx: EmitContext) -> str:
    def typed_dict(name: str, doc: str, fields: list[str]) -> str:
        lines = [f"class {name}(TypedDict, total=False):", docstring(doc), ""]
        if not fields:
            lines[-1:] = ["    pass"]
        for fname in fields:
            info = an.fields[fname]
            comment = info.description.strip().splitlines()[0] if info.description.strip() else ""
            if info.update != "replace":
                comment = (
                    comment.rstrip(".") + "; " if comment else ""
                ) + f"update rule: {info.update}"
            line = f"    {fname}: {field_annotation(info, ctx)}"
            lines.append(f"{line}  # {comment}" if comment else line)
        return "\n".join(lines)

    all_fields = list(an.fields)
    blocks = [typed_dict("FlowData", "The named fields that steps read and write.", all_fields)]
    inputs = [f for f in all_fields if an.fields[f].is_input]
    if inputs:
        blocks.append(typed_dict("FlowInput", "What a run takes.", inputs))
    outputs = [f for f in an.output_fields() if f in an.fields]
    if outputs:
        blocks.append(typed_dict("FlowOutput", "What a run returns.", outputs))
    return "\n\n\n".join(blocks)


def _targets(an: FlowAnalysis, conns: list[Any]) -> list[str]:
    out: list[str] = []
    for conn in conns:
        target = an.steps[conn.target]
        name = "END" if target.type == "output" else py_str(conn.target)
        if name not in out:
            out.append(name)
    return out


def _wiring_for(sid: str, an: FlowAnalysis, ctx: EmitContext, routers: dict[str, str]) -> list[str]:
    step = an.steps[sid]
    handler = an.handlers[sid]
    lines = [f"builder.add_node({py_str(sid)}, {ctx.fn(sid)})"]
    if sid in routers:
        path_map: dict[str, str] = {}
        by_exit: dict[str, str] = {}
        for conn in an.outgoing[sid]:
            if conn.exit is not None and conn.exit not in by_exit:
                target = an.steps[conn.target]
                by_exit[conn.exit] = "END" if target.type == "output" else py_str(conn.target)
        for label in handler.exits(step):
            path_map[label] = by_exit.get(label, "END")
        items = ", ".join(f"{py_str(k)}: {v}" for k, v in path_map.items())
        call = f"builder.add_conditional_edges({py_str(sid)}, {routers[sid]}, {{{items}}})"
        if len(call) > 92:
            inner = "\n".join(f"        {py_str(k)}: {v}," for k, v in path_map.items())
            call = (
                f"builder.add_conditional_edges(\n    {py_str(sid)},\n    {routers[sid]},\n"
                f"    {{\n{inner}\n    }},\n)"
            )
        lines.append(call)
    else:
        targets = _targets(an, an.outgoing[sid]) or ["END"]
        for target in targets:
            lines.append(f"builder.add_edge({py_str(sid)}, {target})")
    return lines


def _emit_build_graph(
    an: FlowAnalysis, ctx: EmitContext, steps: list[str], routers: dict[str, str]
) -> str:
    schemas = []
    if any(f.is_input for f in an.fields.values()):
        schemas.append("input_schema=FlowInput")
    if [f for f in an.output_fields() if f in an.fields]:
        schemas.append("output_schema=FlowOutput")
    lines = [
        "def build_graph(checkpointer=None):",
        docstring(
            "Wire the steps into a LangGraph graph.\n\n"
            "Pass a checkpointer (for example InMemorySaver()) to keep a Save Point after every step."
        ),
        f"    builder = StateGraph({', '.join(['FlowData', *schemas])})",
        "",
    ]
    for sid in steps:
        lines.append(f"    builder.add_node({py_str(sid)}, {ctx.fn(sid)})")
    lines.append("")
    if an.input_step is not None:
        for target in _targets(an, an.outgoing[an.input_step.id]):
            lines.append(f"    builder.add_edge(START, {target})")
    for sid in steps:
        for line in _wiring_for(sid, an, ctx, routers)[1:]:
            lines.append("    " + line.replace("\n", "\n    "))
    lines.append("    return builder.compile(checkpointer=checkpointer)")
    return "\n".join(lines)


def _input_defaults(an: FlowAnalysis) -> dict[str, Any]:
    if an.input_step is None:
        return {}
    return {f.name: f.default for f in an.input_step.settings.fields if f.default is not None}


def _example_input(an: FlowAnalysis) -> dict[str, Any]:
    if an.input_step is None:
        return {}
    example: dict[str, Any] = {}
    for f in an.input_step.settings.fields:
        if f.example is not None:
            example[f.name] = f.example
        elif f.default is not None:
            example[f.name] = f.default
        elif f.required:
            example[f.name] = {"number": 1, "yes_no": True, "list": [], "object": {}}.get(
                f.type, ""
            )
    return example


def _emit_main(
    an: FlowAnalysis, ctx: EmitContext, defaults: dict[str, Any], example: dict[str, Any]
) -> str:
    defaults_expr = "**INPUT_DEFAULTS, " if defaults else ""
    if an.chat:
        ctx.imports.add_from("langgraph.checkpoint.memory", "InMemorySaver")
        message = '"messages": [{"role": "user", "content": text}]'
        if example:
            ctx.imports.add("json")
            ctx.imports.add("sys")
            extra_inputs = (
                "    extra = json.loads(sys.argv[1]) if len(sys.argv) > 1 else "
                f"{py_literal(example, indent=4)}\n"
            )
            payload = f"{{{defaults_expr}**extra, {message}}}"
        else:
            extra_inputs = ""
            payload = f"{{{defaults_expr}{message}}}"
        return (
            'if __name__ == "__main__":\n'
            "    # Chat in the terminal. Save Points keep the conversation between turns.\n"
            "    chat = build_graph(checkpointer=InMemorySaver())\n"
            '    config = {"configurable": {"thread_id": "terminal"}}\n'
            f"{extra_inputs}"
            "    while True:\n"
            "        try:\n"
            '            text = input("you> ").strip()\n'
            "        except (EOFError, KeyboardInterrupt):\n"
            "            break\n"
            "        if text:\n"
            f"            result = chat.invoke({payload}, config)\n"
            '            print("ai>", result["messages"][-1].text)'
        )
    ctx.imports.add("json")
    ctx.imports.add("sys")
    ex = py_literal(example, indent=4)
    return (
        'if __name__ == "__main__":\n'
        f"    example = {ex}\n"
        "    inputs = json.loads(sys.argv[1]) if len(sys.argv) > 1 else example\n"
        f"    result = graph.invoke({'{**INPUT_DEFAULTS, **inputs}' if defaults else 'inputs'})\n"
        "    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))"
    )


def _requirements(spec: FlowSpec, ctx: EmitContext) -> list[str]:
    reqs = [LANGGRAPH_REQ, *LANGCHAIN_REQS]
    for provider in sorted(ctx.providers):
        if provider in PROVIDERS:
            reqs.append(PROVIDERS[provider].package)
    if ctx.uses_httpx:
        reqs.append("httpx>=0.27")
    for req in ctx.extra_requirements:
        if req not in reqs:
            reqs.append(req)
    return reqs
