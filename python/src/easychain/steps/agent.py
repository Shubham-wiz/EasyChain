"""The Agent step: an AI that picks tools in a loop until it has an answer.

It compiles to LangChain's ``create_agent``. Other steps on the canvas (Web
request, Code, Sub-flow, Knowledge Base search, Database query, MCP tool) can be
its tools: each becomes a ``@tool`` that runs the step's own function. Agent
Add-ons are LangChain agent middleware, switched on with a toggle.
"""

from __future__ import annotations

from typing import Any

from ..compiler.issues import Fix, Issue, error, warning
from ..compiler.pycode import docstring, py_literal, py_str
from ..compiler.schema_code import check_schema, emit_schema, spread_types
from ..compiler.templates import variables
from .ai import check_model, missing_field_issue, secrets_in_prompt
from .base import FormField, StepCode, StepHandler

# Step types an agent can use as tools.
TOOL_TYPES = {
    "http_request",
    "code",
    "subflow",
    "knowledge_search",
    "sql_query",
    "mcp_tool",
    "memory",
}

_ARG_TYPES = {
    "text": "str",
    "number": "float",
    "yes_no": "bool",
    "list": "list",
    "object": "dict",
    "file": "str",
}


def _camel(name: str) -> str:
    return "".join(part.capitalize() for part in name.split("_"))


def tool_description(step: Any, handler: Any) -> str:
    text = (step.description or step.name or handler.summary).strip()
    return " ".join(text.split())


class AgentHandler(StepHandler):
    type = "agent"
    label = "Agent"
    technical = "Agent · create_agent"
    category = "ai"
    icon = "bot"
    summary = "An AI that decides which tools to use, step by step, until it has an answer."
    form = [
        FormField(
            key="model",
            label="Model",
            kind="model",
            help="The model that thinks and picks tools. Pick one that is good at using tools.",
            example="openai:gpt-4o-mini",
        ),
        FormField(
            key="instructions",
            label="Role and rules",
            kind="template",
            help="Who the agent is, what it should do and how. Put Flow Data in with {field}.",
            example="You are a support agent for Acme. Search the docs before answering and cite "
            "sources like [1].",
            technical="system_prompt",
        ),
        FormField(
            key="input",
            label="Work on",
            kind="field",
            help="What the agent works on: a question (text) or a chat (messages). Empty means: "
            "whatever the previous step saved.",
            placeholder="From the previous step",
        ),
        FormField(
            key="tools",
            label="Tools",
            kind="tools",
            help="Steps the agent can use. Connect a Web request, Code, Knowledge Base search or "
            "another step to the agent's Tools handle, and describe each one so the agent knows "
            "when to use it.",
            technical="tools",
        ),
        FormField(
            key="mcp",
            label="MCP tools",
            kind="mcp_tools",
            help="Tools from MCP servers you connected in Settings → MCP servers.",
            technical="langchain-mcp-adapters",
            advanced=True,
        ),
        FormField(
            key="save_as",
            label="Save the answer as",
            kind="field_name",
            help="The Flow Data field that holds the agent's final answer.",
            example="answer",
        ),
        FormField(
            key="output",
            label="Answer format",
            kind="schema",
            help="Free text, or a fixed set of fields that later steps can use directly.",
            technical="response_format · ToolStrategy",
        ),
        FormField(
            key="addons",
            label="Add-ons",
            kind="agent_addons",
            help="Approvals, limits, retries, summaries, personal data checks and more.",
            technical="middleware",
        ),
        FormField(
            key="temperature",
            label="Creativity",
            kind="slider",
            min=0,
            max=2,
            step=0.1,
            technical="temperature",
            advanced=True,
        ),
        FormField(
            key="max_steps",
            label="Most rounds",
            kind="number",
            min=10,
            help="Stops an agent that goes round in circles (each model call, tool call and "
            "add-on counts as a round).",
            technical="recursion_limit",
            advanced=True,
        ),
        FormField(
            key="save_messages",
            label="Keep the whole conversation in",
            kind="field_name",
            help="Also save every message, tool calls included, for later steps.",
            technical="messages",
            advanced=True,
            pro=True,
        ),
    ]

    # ── data flow ────────────────────────────────────────────────────────────

    def input_field(self, step: Any, an: Any) -> str | None:
        return step.settings.input or an.upstream_output(step.id)

    def writes(self, step: Any, an: Any) -> dict[str, str]:
        s = step.settings
        out: dict[str, str] = {}
        if s.output is not None:
            out[s.save_as] = "object"
            out.update(spread_types(s.output))
        elif an.fields.get(s.save_as) is not None and an.fields[s.save_as].type == "messages":
            out[s.save_as] = "messages"
        else:
            out[s.save_as] = "text"
        if s.save_messages:
            out[s.save_messages] = "messages"
        return out

    def reads(self, step: Any, an: Any) -> set[str]:
        names = set(variables(step.settings.instructions))
        field = self.input_field(step, an)
        if field:
            names.add(field)
        return names

    def tool_steps(self, step: Any, an: Any) -> list[Any]:
        return [an.steps[t] for t in step.settings.tools if t in an.steps and t != step.id]

    def is_async(self, step: Any, an: Any) -> bool:
        if step.settings.mcp:
            return True
        return any(an.handlers[t.id].is_async(t, an) for t in self.tool_steps(step, an))

    # ── checks ───────────────────────────────────────────────────────────────

    def check(self, step: Any, an: Any) -> list[Issue]:
        s = step.settings
        issues = check_model(step.id, s.model)
        for n, model in enumerate(s.addons.fallback_models):
            issues += check_model(step.id, model, setting=f"addons.fallback_models.{n}")
        field = self.input_field(step, an)
        if not field:
            issues.append(
                error(
                    "agent_no_input",
                    "This Agent has nothing to work on.",
                    step=step.id,
                    setting="input",
                    hint="Connect it after Input (or another step), or pick the field it works on.",
                )
            )
        elif field not in an.available_fields(step.id):
            issues.append(missing_field_issue(step, field, an, "input", "This agent works on"))
        available = an.available_fields(step.id)
        issues += secrets_in_prompt(step, [("instructions", s.instructions)], "the role and rules")
        for name in variables(s.instructions):
            if name not in available:
                issues.append(
                    missing_field_issue(step, name, an, "instructions", "The role and rules use")
                )
        if s.output is not None:
            issues += check_schema(step.id, s.output, save_as=s.save_as)
        names = set()
        for tool_id in s.tools:
            tool = an.steps.get(tool_id)
            if tool is None or tool_id == step.id:
                issues.append(
                    error(
                        "agent_tool_missing",
                        f"The tool “{tool_id}” isn't a step in this flow.",
                        step=step.id,
                        setting="tools",
                        fix=Fix(
                            "remove_from_list",
                            "Remove it from the tools",
                            {"setting": "tools", "value": tool_id},
                        ),
                    )
                )
                continue
            names.add(tool_id)
            if tool.type not in TOOL_TYPES:
                issues.append(
                    error(
                        "agent_tool_type",
                        f"An agent can't use a {an.handlers[tool_id].label} step as a tool.",
                        step=tool_id,
                        hint="Tools can be Web request, Code, Sub-flow, Knowledge Base search, "
                        "Database query, MCP tool and Memory steps.",
                    )
                )
            if an.incoming[tool_id] or an.outgoing[tool_id]:
                issues.append(
                    error(
                        "agent_tool_connected",
                        "A step used as a tool only runs when the agent calls it, so it can't "
                        "also be connected to other steps.",
                        step=tool_id,
                        hint="Remove its connections, or take it off the agent's tools.",
                    )
                )
            if not (tool.description or "").strip():
                issues.append(
                    warning(
                        "tool_no_description",
                        "Describe what this tool does: the agent reads it to decide when to use it.",
                        step=tool_id,
                        setting="description",
                        hint="For example: Looks up an order by its number and returns its status.",
                    )
                )
        if s.mcp:
            names.update(t for m in s.mcp for t in m.tools)
        for tool_name in s.addons.approve_tools:
            if tool_name not in names and not any(not m.tools for m in s.mcp):
                issues.append(
                    error(
                        "approve_unknown_tool",
                        f"Approval is set for “{tool_name}”, which isn't one of this agent's tools.",
                        step=step.id,
                        setting="addons.approve_tools",
                    )
                )
        if not s.tools and not s.mcp and not s.addons.memory:
            issues.append(
                warning(
                    "agent_no_tools",
                    "This agent has no tools yet, so it can only answer from what it knows.",
                    step=step.id,
                    setting="tools",
                    hint="Connect a step to its Tools handle, or use an AI Model step instead.",
                )
            )
        if s.addons.emulate_tools:
            issues.append(
                warning(
                    "agent_emulated_tools",
                    "Pretend tools is on: an AI model makes up tool results, nothing really runs.",
                    step=step.id,
                    setting="addons.emulate_tools",
                    fix=Fix(
                        "set_setting",
                        "Turn it off",
                        {"key": "addons.emulate_tools", "value": False},
                    ),
                )
            )
        return issues

    # ── code ─────────────────────────────────────────────────────────────────

    def emit(self, step: Any, ctx: Any) -> StepCode:
        s = step.settings
        an = ctx.an
        fn = ctx.fn(step.id)
        is_async = self.is_async(step, an)
        ctx.imports.add_from("langchain.agents", "create_agent")
        definitions: list[str] = []

        # Role and rules
        instructions_src = "None"
        if s.instructions.strip():
            const = ctx.names.claim(f"{step.id}_instructions".upper())
            definitions.append(f"{const} = {py_str(s.instructions.strip())}")
            instructions_src = (
                f"{ctx.helper('fill')}({const}, data)" if variables(s.instructions) else const
            )

        # Answer format
        output_class = None
        if s.output is not None:
            output_class = ctx.names.claim(f"{_camel(step.id)}Answer")
            what = s.output.description.strip() or f"The answer of “{step.name or step.id}”."
            definitions += emit_schema(s.output, output_class, what, ctx)
            ctx.imports.add_from("langchain.agents.structured_output", "ToolStrategy")

        # Tools
        tool_calls: list[str] = []
        for tool in self.tool_steps(step, an):
            factory, defs = self._tool_factory(tool, ctx)
            definitions += defs
            tool_calls.append(f"{factory}(data)")
        if s.addons.memory:
            tool_calls.append(f"*{ctx.helper('memory_tools')}(data)")
            ctx.module.uses_store = True
        mcp_line = None
        if s.mcp:
            mcp_line = self._mcp_tools(step, ctx)
            tool_calls.append("*server_tools")

        middleware = self._middleware(step, ctx)
        model = ctx.model_call(
            s.model, {"temperature": s.temperature} if s.temperature is not None else {}
        )
        args = [f"model={model}"]
        tools_arg = f"tools=[{', '.join(tool_calls)}]"
        if len(tools_arg) > 80:
            tools_arg = (
                "tools=[\n" + "".join(f"            {t},\n" for t in tool_calls) + "        ]"
            )
        args.append(tools_arg)
        if instructions_src != "None":
            args.append(f"system_prompt={instructions_src}")
        if middleware:
            args.append(
                "middleware=[\n" + "".join(f"            {m},\n" for m in middleware) + "        ]"
            )
        if output_class:
            args.append(f"response_format=ToolStrategy({output_class})")
        args.append(f"name={py_str(step.id)}")
        create = "    agent = create_agent(\n" + "".join(f"        {a},\n" for a in args) + "    )"
        create = create.replace(
            "model=init_chat_model(\n        ", "model=init_chat_model(\n            "
        )

        field = self.input_field(step, an) or "question"
        ftype = ctx.field_type(field)
        if ftype == "messages":
            messages = f"data.get({py_str(field)}, [])"
        else:
            ctx.imports.add_from("langchain_core.messages", "HumanMessage")
            value = (
                f"{ctx.helper('as_text')}(data.get({py_str(field)}))"
                if ftype in ("list", "object")
                else f'str(data.get({py_str(field)}, ""))'
            )
            messages = f"[HumanMessage({value})]"
        call = "await agent.ainvoke" if is_async else "agent.invoke"
        invoke = (
            f"    result = {call}(\n"
            f'        {{"messages": {messages}}}, {{"recursion_limit": {s.max_steps}}}\n'
            "    )"
        )
        if len(f'        {{"messages": {messages}}}, {{"recursion_limit": {s.max_steps}}}') > 92:
            invoke = (
                f"    result = {call}(\n"
                f'        {{"messages": {messages}}},\n'
                f'        {{"recursion_limit": {s.max_steps}}},\n'
                "    )"
            )

        out_type = self.writes(step, an)[s.save_as]
        returns: list[str] = []
        tail: list[str] = []
        if output_class:
            tail += [
                '    if result.get("structured_response") is None:',
                "        # A limit (or an error the agent was told about) ended it before it answered.",
                "        raise ValueError(f\"The agent stopped before it answered: {result['messages'][-1].text}\")",
                '    answer = result["structured_response"].model_dump()',
            ]
            returns.append(f"{py_str(s.save_as)}: answer")
            if s.output.spread:
                returns += [f"{py_str(f.name)}: answer[{py_str(f.name)}]" for f in s.output.fields]
        elif out_type == "messages":
            ctx.imports.add_from("langchain_core.messages", "AIMessage")
            returns.append(f'{py_str(s.save_as)}: [AIMessage(result["messages"][-1].text)]')
        else:
            returns.append(f'{py_str(s.save_as)}: result["messages"][-1].text')
        if s.save_messages:
            returns.append(f'{py_str(s.save_messages)}: result["messages"]')
        returned = "{" + ", ".join(returns) + "}"
        if len(returned) > 80:
            returned = "{\n" + "".join(f"        {r},\n" for r in returns) + "    }"

        tool_names = [t.id for t in self.tool_steps(step, an)]
        if len(tool_names) > 1:
            uses = f" and the tools {', '.join(tool_names[:-1])} and {tool_names[-1]}"
        elif tool_names:
            uses = f" and the tool {tool_names[0]}"
        else:
            uses = ""
        doc = docstring(
            f"{self.title(step)}\n\nWorks on `{field}` using {s.model}{uses}, and saves its "
            f"answer as `{s.save_as}`."
        )
        body = [create, invoke, *tail, f"    return {returned}"]
        if mcp_line:
            body.insert(0, mcp_line)
        head = "async def" if is_async else "def"
        code = f"{head} {fn}(data: {ctx.data_class}) -> dict[str, Any]:\n{doc}\n" + "\n".join(body)
        return StepCode([*definitions, code], node=fn, is_async=is_async)

    def _tool_factory(self, tool: Any, ctx: Any) -> tuple[str, list[str]]:
        """``<tool>_tool(data)``: the step as a LangChain tool (emitted once per module)."""
        emitted = ctx.module.agent_tools
        key = (ctx.prefix, tool.id)
        if key in emitted:
            return emitted[key], []
        an = ctx.an
        handler = an.handlers[tool.id]
        code = handler.emit(tool, ctx)
        defs = list(code.definitions)
        step_fn = code.node or ctx.fn(tool.id)
        factory = ctx.names.claim(f"{tool.id}_tool")
        emitted[key] = factory
        ctx.imports.add_from("langchain_core.tools", "BaseTool", "tool")
        params: list[str] = []
        fills: list[str] = []
        for name in an.tool_args(tool.id):
            py_type = _ARG_TYPES.get(an.field_type(name), "str")
            info = an.fields.get(name)
            desc = (
                info.description.strip().splitlines()[0]
                if info and info.description.strip()
                else ""
            )
            if desc:
                ctx.imports.add_from("typing", "Annotated")
                params.append(f"{name}: Annotated[{py_type}, {py_str(desc)}]")
            else:
                params.append(f"{name}: {py_type}")
            fills.append(f"{py_str(name)}: {name}")
        primary = handler.primary_output(tool)
        writes = handler.writes(tool, an)
        result_src = f"result.get({py_str(primary)})" if primary and primary in writes else "result"
        call_data = "{**data, " + ", ".join(fills) + "}" if fills else "data"
        is_async = code.is_async
        call = f"await {step_fn}({call_data})" if is_async else f"{step_fn}({call_data})"
        head = "async def" if is_async else "def"
        description = tool_description(tool, handler)
        decorator = f"    @tool({py_str(tool.id)}, description={py_str(description)})"
        if len(decorator) > 92:
            decorator = f"    @tool(\n        {py_str(tool.id)},\n        description={py_str(description)},\n    )"
        signature = f"    {head} run_tool({', '.join(params)}) -> str:"
        if len(signature) > 92:
            signature = (
                f"    {head} run_tool(\n"
                + "".join(f"        {p},\n" for p in params)
                + "    ) -> str:"
            )
        args_text = ", ".join(an.tool_args(tool.id))
        factory_code = (
            f"def {factory}(data: {ctx.data_class}) -> BaseTool:\n"
            + docstring(
                f"{handler.label} “{tool.name or tool.id}” as a tool for agents: "
                f"{tool.id}({args_text})."
            )
            + "\n\n"
            + decorator
            + "\n"
            + signature
            + "\n"
            + f"        result = {call}\n"
            + f"        return {ctx.helper('as_text')}({result_src})\n\n"
            + "    return run_tool"
        )
        return factory, [*defs, factory_code]

    def _mcp_tools(self, step: Any, ctx: Any) -> str:
        loader = ctx.helper("mcp_tools")
        servers = {m.server: list(m.tools) for m in step.settings.mcp}
        return f"    server_tools = await {loader}({py_literal(servers)})"

    def _middleware(self, step: Any, ctx: Any) -> list[str]:
        s = step.settings
        a = s.addons
        out: list[str] = []

        def use(name: str) -> str:
            ctx.imports.add_from("langchain.agents.middleware", name)
            return name

        model = ctx.model_call(s.model, {})
        if a.pii:
            for kind in a.pii:
                out.append(
                    f"{use('PIIMiddleware')}({py_str(kind)}, strategy={py_str(a.pii_strategy)})"
                )
        if a.summarise:
            out.append(
                f"{use('SummarizationMiddleware')}(\n"
                f"                {model},\n"
                f'                trigger=("tokens", {a.summarise_after}),\n'
                f'                keep=("messages", {a.summarise_keep}),\n'
                "            )"
            )
        if a.clear_tool_results:
            use("ClearToolUsesEdit")
            out.append(
                f"{use('ContextEditingMiddleware')}(edits=[ClearToolUsesEdit(trigger={a.clear_tool_results})])"
            )
        if a.select_tools:
            out.append(
                f"{use('LLMToolSelectorMiddleware')}(model={model}, max_tools={a.select_tools})"
            )
        if a.todo_list:
            out.append(f"{use('TodoListMiddleware')}()")
        if a.approve_tools:
            rules = {
                name: {"allowed_decisions": ["approve", "edit", "reject"]}
                for name in a.approve_tools
            }
            out.append(
                f"{use('HumanInTheLoopMiddleware')}(\n"
                f"                interrupt_on={py_literal(rules, indent=16)},\n"
                f'                description_prefix="The agent wants to use a tool that needs your approval",\n'
                "            )"
            )
        if a.max_model_calls:
            out.append(
                f'{use("ModelCallLimitMiddleware")}(run_limit={a.max_model_calls}, exit_behavior="end")'
            )
        if a.max_tool_calls:
            out.append(f"{use('ToolCallLimitMiddleware')}(run_limit={a.max_tool_calls})")
        if a.fallback_models:
            fallbacks = ", ".join(ctx.model_call(m, {}) for m in a.fallback_models)
            out.append(f"{use('ModelFallbackMiddleware')}({fallbacks})")
        if a.model_retries:
            out.append(
                f'{use("ModelRetryMiddleware")}(max_retries={a.model_retries}, on_failure="error")'
            )
        if a.tool_errors == "tell_agent":
            out.append(f"{use('ToolErrorMiddleware')}({ctx.helper('tell_agent')})")
        if a.tool_retries:
            out.append(
                f'{use("ToolRetryMiddleware")}(max_retries={a.tool_retries}, on_failure="error")'
            )
        if a.emulate_tools:
            out.append(f"{use('LLMToolEmulator')}(model={model})")
        return out
