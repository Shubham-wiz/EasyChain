"""Turn a flow spec into a LangGraph Python module.

This is the one compiler behind both Run and Export: the runtime executes
exactly the source produced here. The output only imports LangChain, LangGraph
and the standard library (plus httpx, which LangChain already depends on).

A module holds the root flow and, above it, every flow it uses as a Sub-flow
(their names prefixed so nothing clashes).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .. import __version__
from ..providers import PROVIDERS, split_model
from ..spec.models import SPEC_VERSION, FlowSpec
from .analysis import FlowAnalysis, Resolver
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
DEFAULT_MAX_STEPS = 25


def section(title: str) -> str:
    line = f"# ── {title} "
    return line + "─" * max(4, 79 - len(line))


def module_name(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    if not slug or slug[0].isdigit():
        slug = f"flow_{slug}".rstrip("_")
    return slug


def camel(name: str) -> str:
    return "".join(part.capitalize() for part in module_name(name).split("_")) or "Flow"


class PrefixedNames:
    """Claims module-level names with a flow prefix (so sub-flows don't clash)."""

    def __init__(self, names: Names, prefix: str):
        self._names = names
        self._prefix = prefix

    def claim(self, base: str) -> str:
        prefix = self._prefix.upper() if base.isupper() else self._prefix
        return self._names.claim(prefix + base)

    def reserve(self, name: str) -> None:
        self._names.reserve(name)


class ModuleContext:
    """State shared by every flow in one generated module."""

    def __init__(self, resolve: Resolver | None):
        self.imports = Imports()
        self.names = Names()
        self.helpers: dict[str, None] = {}
        self.providers: set[str] = set()
        self.extra_requirements: list[str] = []
        self.uses_httpx = False
        self.has_async = False
        self.has_interrupts = False
        self.uses_cache = False
        self.resolve = resolve
        # Sub-flows already emitted: flow id -> its parts.
        self.children: dict[str, FlowParts] = {}
        self.child_order: list[str] = []

    def helper(self, name: str) -> str:
        helper = HELPERS[name]
        for dep in helper.requires:
            self.helper(dep)
        self.helpers[name] = None
        for module in helper.imports:
            self.imports.add(module)
        for module, imported in helper.from_imports:
            self.imports.add_from(module, imported)
        return name


class EmitContext:
    """Per-flow state while emitting one flow's code."""

    def __init__(self, analysis: FlowAnalysis, module: ModuleContext, prefix: str = ""):
        self.an = analysis
        self.module = module
        self.prefix = prefix
        self.imports = module.imports
        self.names = PrefixedNames(module.names, prefix)
        self.fn_names: dict[str, str] = {}
        # For Each rewires the step it runs per item.
        self.node_override: dict[str, str] = {}
        self.edge_override: dict[str, list[str]] = {}
        self.extra_nodes: list[str] = []
        self.data_class = "FlowData"
        self.defaults_var: str | None = None
        for sid in analysis.order:
            if analysis.handlers[sid].has_node:
                self.fn_names[sid] = self.names.claim(sid)

    # Shortcuts used by step handlers.
    @property
    def uses_httpx(self) -> bool:
        return self.module.uses_httpx

    @uses_httpx.setter
    def uses_httpx(self, value: bool) -> None:
        self.module.uses_httpx = value

    @property
    def extra_requirements(self) -> list[str]:
        return self.module.extra_requirements

    def fn(self, step_id: str) -> str:
        return self.fn_names[step_id]

    def helper(self, name: str) -> str:
        return self.module.helper(name)

    def field_type(self, name: str | None) -> str:
        return self.an.field_type(name)

    def model_call(self, model: str, kwargs: dict[str, Any]) -> str:
        """``init_chat_model(...)`` for a provider:model string plus settings."""
        self.imports.add_from("langchain.chat_models", "init_chat_model")
        provider, _ = split_model(model)
        if provider:
            self.module.providers.add(provider)
        parts = [py_str(model)] + [f"{k}={py_literal(v)}" for k, v in kwargs.items()]
        return f"init_chat_model({', '.join(parts)})"

    def subflow(self, flow_id: str) -> FlowParts | None:
        """Emit (once) a flow used as a Sub-flow and return its parts."""
        if flow_id in self.module.children:
            return self.module.children[flow_id]
        child = self.an.child(flow_id)
        if child is None:
            return None
        parts = emit_flow(
            child.spec, child, self.module, prefix=f"{module_name(child.spec.name)}__"
        )
        self.module.children[flow_id] = parts
        self.module.child_order.append(flow_id)
        return parts


@dataclass
class FlowParts:
    """The code for one flow (the root or a Sub-flow)."""

    spec: FlowSpec
    analysis: FlowAnalysis
    prefix: str
    data_class: str
    input_class: str | None
    output_class: str | None
    build_fn: str
    graph_var: str | None
    state: str
    constants: list[str]
    steps: list[str]
    build: str
    snippets: dict[str, str]
    routers: dict[str, str]
    node_steps: dict[str, str]
    input_defaults: dict[str, Any]
    round_counters: dict[str, Any]
    # The name of the constant with this sub-flow's start values (None for the root).
    defaults_var: str | None = None


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
    round_counters: dict[str, Any] = field(default_factory=dict)
    run_config: dict[str, Any] = field(default_factory=dict)
    node_steps: dict[str, str] = field(default_factory=dict)
    has_interrupts: bool = False
    is_async: bool = False
    subflows: list[str] = field(default_factory=list)
    # Sub-flows compiled into this module, by flow id.
    children: dict[str, FlowParts] = field(default_factory=dict, repr=False)


def compile_flow(
    spec: FlowSpec,
    *,
    allow_errors: bool = False,
    resolve: Resolver | None = None,
    flow_id: str | None = None,
) -> CompiledFlow:
    """Compile a flow spec. Raises CompileError if the flow has blocking errors.

    ``resolve`` looks up other flows by id (for Sub-flow steps).
    """
    from .validate import validate

    an = FlowAnalysis(spec, resolve=resolve, flow_id=flow_id)
    issues = validate(spec, analysis=an)
    errors = [i for i in issues if i.level == "error"]
    if errors and not allow_errors:
        raise CompileError(errors)
    return _emit_module(spec, an, issues)


# ── one flow ─────────────────────────────────────────────────────────────────


def emit_flow(
    spec: FlowSpec, an: FlowAnalysis, module: ModuleContext, prefix: str = ""
) -> FlowParts:
    ctx = EmitContext(an, module, prefix)
    root = prefix == ""
    data_class = "FlowData" if root else module.names.claim(f"{camel(spec.name)}Data")
    ctx.data_class = data_class
    if root:
        for reserved in ("FlowData", "FlowInput", "FlowOutput", "build_graph", "graph"):
            module.names.reserve(reserved)

    step_blocks: dict[str, str] = {}
    routers: dict[str, str] = {}
    codes: dict[str, Any] = {}
    for sid in an.order:
        handler = an.handlers[sid]
        if not handler.has_node or sid not in an.reachable:
            continue
        code = handler.emit(an.steps[sid], ctx)
        codes[sid] = code
        step_blocks[sid] = code.text()
        if code.router:
            routers[sid] = code.router
    snippets: dict[str, str] = {}
    for sid in an.order:
        handler = an.handlers[sid]
        if not handler.has_node:
            continue
        if sid in codes:
            text = codes[sid].text()
            wiring = _node_lines(sid, an, ctx, codes[sid]) + _edge_lines(
                sid, an, ctx, codes, routers
            )
        else:
            # Not connected: show its code with a throwaway context so nothing leaks into the module.
            scratch = EmitContext(an, ModuleContext(module.resolve), prefix)
            scratch.data_class = data_class
            text = handler.emit(an.steps[sid], scratch).text()
            wiring = ["# Not connected to Input yet."]
        snippets[sid] = text + "\n\n\n# Wiring (inside build_graph)\n" + "\n".join(wiring)

    state, input_class, output_class, constants = _emit_state(an, ctx, data_class, root)
    build_fn = (
        "build_graph" if root else module.names.claim(f"build_{module_name(spec.name)}_graph")
    )
    build = _emit_build_graph(
        an, ctx, codes, routers, data_class, input_class, output_class, build_fn, root
    )
    graph_var = None if root else module.names.claim(f"{module_name(spec.name)}_graph")

    node_steps = {sid: sid for sid in codes}
    for sid, code in codes.items():
        for line in code.extra_nodes:
            match = re.match(r'builder\.add_node\("([^"]+)"', line)
            if match:
                node_steps[match.group(1)] = sid
    return FlowParts(
        spec=spec,
        analysis=an,
        prefix=prefix,
        data_class=data_class,
        input_class=input_class,
        output_class=output_class,
        build_fn=build_fn,
        graph_var=graph_var,
        state=state,
        constants=constants,
        steps=list(step_blocks.values()),
        build=build,
        snippets=snippets,
        routers=routers,
        node_steps=node_steps,
        input_defaults=_input_defaults(an),
        round_counters=an.round_counters(),
        defaults_var=ctx.defaults_var,
    )


def _emit_module(spec: FlowSpec, an: FlowAnalysis, issues: list[Issue]) -> CompiledFlow:
    module = ModuleContext(an.resolve)
    module.imports.add_from("typing", "Any")
    module.imports.add_from("typing_extensions", "TypedDict")
    module.imports.add_from("langgraph.graph", "END", "START", "StateGraph")

    root = emit_flow(spec, an, module)
    if any(s.type == "ask_human" for p in [root, *module.children.values()] for s in p.spec.steps):
        module.has_interrupts = True
    example_input = _example_input(an)
    run_config = _run_config(spec)
    main_code = _emit_main(an, module, root, example_input, run_config)

    # Update-rule helpers are used in the Flow Data annotations, so they go first.
    reducers = [name for name in module.helpers if HELPERS[name].reducer]
    reducers_code = "\n\n\n".join(HELPERS[name].code.strip("\n") for name in reducers)
    helpers_code = "\n\n\n".join(
        HELPERS[name].code.strip("\n") for name in module.helpers if name not in reducers
    )
    mod = module_name(spec.name)
    header = _emit_header(spec, mod, an, module)

    flow_data = section("Flow Data") + "\n\n\n"
    if reducers_code:
        flow_data += reducers_code + "\n\n\n"
    parts = [header + "\n\n" + module.imports.render() + "\n\n" + flow_data + root.state]
    parts += root.constants
    if run_config:
        parts.append("RUN_CONFIG: dict[str, Any] = " + py_literal(run_config))
    if helpers_code:
        parts.append(section("Helpers") + "\n\n\n" + helpers_code)
    # Sub-flows come before the flows that use them (child_order is depth-first).
    for flow_id in module.child_order:
        child = module.children[flow_id]
        body = [child.state, *child.constants, *child.steps, child.build]
        body.append(f"{child.graph_var} = {child.build_fn}()")
        parts.append(section(f"Sub-flow: {child.spec.name}") + "\n\n\n" + "\n\n\n".join(body))
    if root.steps:
        parts.append(section("Steps") + "\n\n\n" + "\n\n\n".join(root.steps))
    unreachable = [
        an.steps[s] for s in an.order if s not in an.reachable and an.handlers[s].has_node
    ]
    if unreachable:
        names = ", ".join(f"{s.name or s.id} ({s.id})" for s in unreachable)
        parts.append(f"# Not connected to Input, so not included: {names}")
    parts.append(section("Flow") + "\n\n\n" + root.build)
    parts.append("graph = build_graph()")
    parts.append(main_code)
    source = "\n\n\n".join(p.strip("\n") for p in parts) + "\n"
    source = re.sub(r"\n{4,}", "\n\n\n", source)

    node_steps = dict(root.node_steps)
    return CompiledFlow(
        source=source,
        module_name=mod,
        snippets=root.snippets,
        requirements=_requirements(module),
        issues=issues,
        chat=an.chat,
        input_defaults=root.input_defaults,
        example_input=example_input,
        output_fields=an.output_fields(),
        routers=root.routers,
        analysis=an,
        round_counters=root.round_counters,
        run_config=run_config,
        node_steps=node_steps,
        has_interrupts=module.has_interrupts,
        is_async=module.has_async,
        subflows=list(module.child_order),
        children=dict(module.children),
    )


def _emit_header(spec: FlowSpec, mod: str, an: FlowAnalysis, module: ModuleContext) -> str:
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
    if module.has_interrupts:
        lines.append(
            "Ask a Human steps pause the run (LangGraph interrupt); resume with Command(resume=...)."
        )
    if module.has_async:
        lines.append("Some steps have a time limit, so call it with `await graph.ainvoke(...)`.")
    return docstring("\n".join(lines), spaces=0)


# ── Flow Data ────────────────────────────────────────────────────────────────


def field_annotation(info: Any, ctx: EmitContext) -> str:
    base = _BASE_TYPES.get(info.type, "Any")
    if info.type == "messages":
        ctx.imports.add_from("langchain_core.messages", "AnyMessage")
    reducer = None
    if info.reducer:
        reducer = ctx.helper(info.reducer)
    elif info.update in ("append", "add"):
        if info.type == "messages":
            ctx.imports.add_from("langgraph.graph.message", "add_messages")
            reducer = "add_messages"
        else:
            ctx.imports.add("operator")
            reducer = "operator.add"
    elif info.update == "merge":
        reducer = ctx.helper("merge_dicts")
    elif info.update == "custom":
        reducer = _custom_reducer_name(info.name, ctx)
    if reducer:
        ctx.imports.add_from("typing", "Annotated")
        return f"Annotated[{base}, {reducer}]"
    return base


def _custom_reducer_name(field_name: str, ctx: EmitContext) -> str:
    return f"{ctx.prefix}combine_{field_name}"


def _emit_custom_reducers(an: FlowAnalysis, ctx: EmitContext) -> list[str]:
    from .reducers import rename_combine

    blocks = []
    for decl in an.spec.data:
        if decl.update == "custom" and decl.combine.strip():
            name = _custom_reducer_name(decl.name, ctx)
            ctx.names.reserve(name)
            code = rename_combine(decl.combine, name, ctx.imports)
            blocks.append(f"# Update rule for `{decl.name}`\n{code}")
    return blocks


def _emit_state(
    an: FlowAnalysis, ctx: EmitContext, data_class: str, root: bool
) -> tuple[str, str | None, str | None, list[str]]:
    def typed_dict(name: str, doc: str, fields: list[str]) -> str:
        lines = [f"class {name}(TypedDict, total=False):", docstring(doc), ""]
        if not fields:
            lines[-1:] = ["    pass"]
        for fname in fields:
            info = an.fields[fname]
            comment = info.description.strip().splitlines()[0] if info.description.strip() else ""
            if info.update != "replace" and not info.private:
                comment = (
                    comment.rstrip(".") + "; " if comment else ""
                ) + f"update rule: {info.update}"
            line = f"    {fname}: {field_annotation(info, ctx)}"
            lines.append(f"{line}  # {comment}" if comment else line)
        return "\n".join(lines)

    flow_label = "" if root else f" of the sub-flow “{an.spec.name}”"
    all_fields = list(an.fields)
    blocks = _emit_custom_reducers(an, ctx)
    blocks.append(
        typed_dict(
            data_class, f"The named fields that steps{flow_label} read and write.", all_fields
        )
    )
    input_class = output_class = None
    inputs = [f for f in all_fields if an.fields[f].is_input or an.fields[f].reset is not None]
    if inputs:
        input_class = "FlowInput" if root else ctx.module.names.claim(f"{camel(an.spec.name)}Input")
        blocks.append(typed_dict(input_class, "What a run takes.", inputs))
    outputs = [f for f in an.output_fields() if f in an.fields]
    if outputs:
        output_class = (
            "FlowOutput" if root else ctx.module.names.claim(f"{camel(an.spec.name)}Output")
        )
        blocks.append(typed_dict(output_class, "What a run returns.", outputs))

    constants: list[str] = []
    defaults = _input_defaults(an)
    counters = an.round_counters()
    if root:
        if defaults:
            constants.append("INPUT_DEFAULTS: dict[str, Any] = " + py_literal(defaults))
        if counters:
            constants.append(
                "# Loop round counters; every new run starts them again.\n"
                "ROUND_COUNTERS: dict[str, Any] = " + py_literal(counters)
            )
    elif defaults or counters:
        name = ctx.names.claim("INPUT_DEFAULTS")
        ctx.defaults_var = name
        constants.append(f"{name}: dict[str, Any] = " + py_literal({**defaults, **counters}))
    return "\n\n\n".join(blocks), input_class, output_class, constants


# ── wiring ───────────────────────────────────────────────────────────────────


def _targets(an: FlowAnalysis, conns: list[Any]) -> list[str]:
    out: list[str] = []
    for conn in conns:
        target = an.steps[conn.target]
        name = "END" if target.type == "output" else py_str(conn.target)
        if name not in out:
            out.append(name)
    return out


def exit_targets(an: FlowAnalysis, sid: str) -> dict[str, str]:
    """Exit label -> target expression ("END" or a quoted node name)."""
    step = an.steps[sid]
    by_exit: dict[str, str] = {}
    for conn in an.outgoing[sid]:
        if conn.exit is not None and conn.exit not in by_exit:
            target = an.steps[conn.target]
            by_exit[conn.exit] = "END" if target.type == "output" else py_str(conn.target)
    return {label: by_exit.get(label, "END") for label in an.handlers[sid].exits(step)}


def _node_lines(sid: str, an: FlowAnalysis, ctx: EmitContext, code: Any) -> list[str]:
    step = an.steps[sid]
    fn = ctx.node_override.get(sid, ctx.fn(sid))
    kwargs: dict[str, str] = {}
    policy = step.run
    if code.is_async:
        ctx.module.has_async = True
    if policy.timeout:
        ctx.module.has_async = True
        if not code.is_async:
            ctx.helper("in_thread")
            fn = f"in_thread({fn})"
        kwargs["timeout"] = py_literal(policy.timeout)
    if policy.retries:
        ctx.imports.add_from("langgraph.types", "RetryPolicy")
        retry = [f"max_attempts={policy.retries + 1}"]
        if policy.retry_wait != 1.0:
            retry.append(f"initial_interval={py_literal(policy.retry_wait)}")
        kwargs["retry_policy"] = f"RetryPolicy({', '.join(retry)})"
    if policy.cache:
        ctx.imports.add_from("langgraph.types", "CachePolicy")
        ctx.module.uses_cache = True
        kwargs["cache_policy"] = (
            f"CachePolicy(ttl={policy.cache_ttl})" if policy.cache_ttl else "CachePolicy()"
        )
    if policy.wait_for_all:
        kwargs["defer"] = "True"
    kwargs.update(code.node_kwargs)
    args = ", ".join([py_str(sid), fn, *(f"{k}={v}" for k, v in kwargs.items())])
    line = f"builder.add_node({args})"
    if len(line) > 92:
        inner = ",\n".join(
            f"    {a}" for a in [py_str(sid), fn, *(f"{k}={v}" for k, v in kwargs.items())]
        )
        line = f"builder.add_node(\n{inner},\n)"
    return [line, *code.extra_nodes]


def _edge_lines(
    sid: str, an: FlowAnalysis, ctx: EmitContext, codes: dict[str, Any], routers: dict[str, str]
) -> list[str]:
    if sid in ctx.edge_override:
        return ctx.edge_override[sid]
    code = codes[sid]
    if code.wiring is not None:
        return code.wiring
    if code.node_kwargs.get("destinations"):
        return []  # a Jump picks its next step itself
    if sid in routers:
        path_map = exit_targets(an, sid)
        items = ", ".join(f"{py_str(k)}: {v}" for k, v in path_map.items())
        call = f"builder.add_conditional_edges({py_str(sid)}, {routers[sid]}, {{{items}}})"
        if len(call) > 92:
            inner = "\n".join(f"        {py_str(k)}: {v}," for k, v in path_map.items())
            call = (
                f"builder.add_conditional_edges(\n    {py_str(sid)},\n    {routers[sid]},\n"
                f"    {{\n{inner}\n    }},\n)"
            )
        return [call]
    targets = _targets(an, an.outgoing[sid]) or ["END"]
    return [f"builder.add_edge({py_str(sid)}, {target})" for target in targets]


def _emit_build_graph(
    an: FlowAnalysis,
    ctx: EmitContext,
    codes: dict[str, Any],
    routers: dict[str, str],
    data_class: str,
    input_class: str | None,
    output_class: str | None,
    build_fn: str,
    root: bool,
) -> str:
    schemas = []
    if input_class:
        schemas.append(f"input_schema={input_class}")
    if output_class:
        schemas.append(f"output_schema={output_class}")
    node_lines: list[str] = []
    edge_lines: list[str] = []
    for sid in codes:
        node_lines += _node_lines(sid, an, ctx, codes[sid])
    node_lines += ctx.extra_nodes
    if an.input_step is not None:
        for target in _targets(an, an.outgoing[an.input_step.id]):
            edge_lines.append(f"builder.add_edge(START, {target})")
    for sid in codes:
        edge_lines += _edge_lines(sid, an, ctx, codes, routers)

    if root:
        signature = f"def {build_fn}(checkpointer=None, *, store=None, cache=None):"
        doc = docstring(
            "Wire the steps into a LangGraph graph.\n\n"
            "Pass a checkpointer (for example InMemorySaver()) to keep a Save Point after every step,\n"
            "a store to remember side effects across retries, and a cache for cached steps."
        )
        compile_call = "builder.compile(checkpointer=checkpointer, store=store, cache=cache)"
    else:
        signature = f"def {build_fn}():"
        doc = docstring(
            f"The sub-flow “{an.spec.name}” as a graph; it shares the parent's Save Points."
        )
        compile_call = "builder.compile()"
    graph_args = [data_class, *schemas]
    builder_line = f"    builder = StateGraph({', '.join(graph_args)})"
    if len(builder_line) > 92:
        builder_line = (
            "    builder = StateGraph(\n" + "".join(f"        {a},\n" for a in graph_args) + "    )"
        )
    lines = [signature, doc, builder_line, ""]
    for line in node_lines:
        lines.append("    " + line.replace("\n", "\n    "))
    lines.append("")
    for line in edge_lines:
        lines.append("    " + line.replace("\n", "\n    "))
    lines.append(f"    return {compile_call}")
    return "\n".join(lines)


# ── inputs, run config and __main__ ───────────────────────────────────────────


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


def _run_config(spec: FlowSpec) -> dict[str, Any]:
    config: dict[str, Any] = {}
    if spec.settings.max_steps != DEFAULT_MAX_STEPS:
        config["recursion_limit"] = spec.settings.max_steps
    limits = [
        s.settings.concurrency
        for s in spec.steps
        if s.type == "for_each" and s.settings.concurrency
    ]
    if spec.settings.max_parallel:
        limits.append(spec.settings.max_parallel)
    if limits:
        config["max_concurrency"] = min(limits)
    return config


def _emit_main(
    an: FlowAnalysis,
    module: ModuleContext,
    root: FlowParts,
    example: dict[str, Any],
    run_config: dict[str, Any],
) -> str:
    imports = module.imports
    is_async = module.has_async
    stateful = an.chat or module.has_interrupts
    call = "await app.ainvoke" if is_async else "app.invoke"
    starts = [
        name
        for name, present in (
            ("INPUT_DEFAULTS", root.input_defaults),
            ("ROUND_COUNTERS", root.round_counters),
        )
        if present
    ]
    config_extra = ", **RUN_CONFIG" if run_config else ""

    if not stateful and not is_async:
        imports.add("json")
        imports.add("sys")
        merged = (
            "{" + ", ".join([*(f"**{s}" for s in starts), "**inputs"]) + "}" if starts else "inputs"
        )
        config_arg = ", RUN_CONFIG" if run_config else ""
        return (
            'if __name__ == "__main__":\n'
            f"    example = {py_literal(example, indent=4)}\n"
            "    inputs = json.loads(sys.argv[1]) if len(sys.argv) > 1 else example\n"
            f"    result = graph.invoke({merged}{config_arg})\n"
            "    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))"
        )

    body: list[str] = []
    if stateful:
        imports.add_from("langgraph.checkpoint.memory", "InMemorySaver")
        body.append("app = build_graph(checkpointer=InMemorySaver())")
        body.append(f'config = {{"configurable": {{"thread_id": "terminal"}}{config_extra}}}')
    else:
        body.append("app = graph")
        body.append(f"config = {{{config_extra.lstrip(', ')}}}" if run_config else "config = {}")

    def finish(result_var: str) -> list[str]:
        if not module.has_interrupts:
            return []
        imports.add_from("langgraph.types", "Command")
        module.helper("ask_in_terminal")
        return [
            f'while "__interrupt__" in {result_var}:',
            f'    answer = ask_in_terminal({result_var}["__interrupt__"][0].value)',
            f"    {result_var} = {call}(Command(resume=answer), config)",
        ]

    if an.chat:
        message = '"messages": [{"role": "user", "content": text}]'
        start = "".join(f"**{s}, " for s in starts)
        if example:
            imports.add("json")
            imports.add("sys")
            body.append(
                f"extra = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {py_literal(example, indent=4)}"
            )
            payload = f"{{{start}**extra, {message}}}"
        else:
            payload = f"{{{start}{message}}}"
        body += [
            "# Chat in the terminal. Save Points keep the conversation between turns.",
            "while True:",
            "    try:",
            '        text = input("you> ").strip()',
            "    except (EOFError, KeyboardInterrupt):",
            "        break",
            "    if text:",
            f"        result = {call}({payload}, config)",
            *("        " + line for line in finish("result")),
            '        print("ai>", result["messages"][-1].text)',
        ]
    else:
        imports.add("json")
        imports.add("sys")
        merged = (
            "{" + ", ".join([*(f"**{s}" for s in starts), "**inputs"]) + "}" if starts else "inputs"
        )
        body += [
            f"example = {py_literal(example, indent=4)}",
            "inputs = json.loads(sys.argv[1]) if len(sys.argv) > 1 else example",
            f"result = {call}({merged}, config)",
            *finish("result"),
            "print(json.dumps(result, indent=2, ensure_ascii=False, default=str))",
        ]

    if is_async:
        imports.add("asyncio")
        main = "async def main() -> None:\n" + "\n".join("    " + line for line in body)
        return main + '\n\n\nif __name__ == "__main__":\n    asyncio.run(main())'
    return 'if __name__ == "__main__":\n' + "\n".join("    " + line for line in body)


def _requirements(module: ModuleContext) -> list[str]:
    reqs = [LANGGRAPH_REQ, *LANGCHAIN_REQS]
    for provider in sorted(module.providers):
        if provider in PROVIDERS:
            reqs.append(PROVIDERS[provider].package)
    if module.uses_httpx:
        reqs.append("httpx>=0.27")
    for req in module.extra_requirements:
        if req not in reqs:
            reqs.append(req)
    return reqs
