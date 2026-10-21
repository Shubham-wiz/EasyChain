"""The Deep Agent step and its Helpers.

A Deep Agent compiles to ``deepagents.create_deep_agent``: an agent for longer tasks that
plans with a to-do list (``write_todos``), keeps notes and drafts in files (``ls``,
``read_file``, ``write_file``, ``edit_file``…), and hands sub-tasks to Helpers through its
``task`` tool. Its files live in the agent's state, so they are saved with each Save Point,
survive a crash, and are kept in Flow Data afterwards.

A Helper is a sub-agent (a deepagents ``SubAgent``): its own role, model and tools. It isn't
a step that runs in the flow; its Deep Agent starts it when it hands it a task, and the
Helper's work runs (and pauses for approvals) inside the Deep Agent's step.
"""

from __future__ import annotations

from typing import Any

from ..compiler.helpers import DEEPAGENTS_REQ
from ..compiler.issues import Fix, Issue, error, warning
from ..compiler.pycode import docstring, py_str
from ..compiler.templates import variables
from .agent import AgentHandler, check_approvals, check_tools, has_run_policy
from .ai import check_model, missing_field_issue, secrets_in_prompt
from .base import FormField, StepCode

# deepagents' file tools (all but `execute`) and its own tools: approvals may name them.
FILE_TOOL_NAMES = {"ls", "read_file", "write_file", "edit_file", "delete", "glob", "grep"}
DEEP_AGENT_TOOLS = FILE_TOOL_NAMES | {"write_todos", "task"}


def _shift(text: str, by: int) -> str:
    """Move every line but the first ``by`` columns, left when negative (a multi-line
    expression put at another depth in the code)."""
    lines = text.split("\n")
    if by >= 0:
        return "\n".join([lines[0], *(" " * by + line for line in lines[1:])])
    return "\n".join([lines[0], *(line.removeprefix(" " * -by) for line in lines[1:])])


def _names(ids: list[str]) -> str:
    return f"{', '.join(ids[:-1])} and {ids[-1]}" if len(ids) > 1 else ids[0]


def _use_deepagents(ctx: Any) -> None:
    if DEEPAGENTS_REQ not in ctx.extra_requirements:
        ctx.extra_requirements.append(DEEPAGENTS_REQ)


class DeepAgentHandler(AgentHandler):
    type = "deep_agent"
    label = "Deep Agent"
    technical = "Deep Agent · create_deep_agent"
    icon = "brain-circuit"
    summary = (
        "An agent for longer tasks: it plans with a to-do list, keeps notes and drafts in files, "
        "and hands sub-tasks to Helpers."
    )
    beginner = False
    warn_no_tools = False

    @property
    def form(self) -> list[FormField]:  # type: ignore[override]
        fields: list[FormField] = []
        for f in AgentHandler.form:
            if f.key == "instructions":
                f = FormField(
                    **{
                        **f.__dict__,
                        "example": "You research a topic and write a report. Plan the work, "
                        "ask the researcher for facts, and write the report to /report.md.",
                    }
                )
            fields.append(f)
            if f.key == "tools":
                fields.append(
                    FormField(
                        key="helpers",
                        label="Helpers",
                        kind="helpers",
                        help="Specialists it hands sub-tasks to. Connect a Helper to its "
                        "Helpers handle, and describe each one so it knows what to hand it. "
                        "It can also hand tasks to a general helper with its own tools.",
                        technical="subagents · task",
                    )
                )
            if f.key == "save_as":
                fields.append(
                    FormField(
                        key="files",
                        label="Keep its files in",
                        kind="field_name",
                        help="The Flow Data field for the files it writes ({path: text}). "
                        "They are there again the next time it runs in this conversation. "
                        "Empty: the files are gone when it finishes.",
                        example="files",
                        technical="files · StateBackend",
                    )
                )
        fields.append(
            FormField(
                key="save_todos",
                label="Keep its to-do list in",
                kind="field_name",
                help="Also save its to-do list (a list of {content, status}) for later steps.",
                technical="todos",
                advanced=True,
                pro=True,
            )
        )
        return fields

    # ── data flow ────────────────────────────────────────────────────────────

    def writes(self, step: Any, an: Any) -> dict[str, str]:
        out = super().writes(step, an)
        s = step.settings
        if s.files:
            out[s.files] = "object"
        if s.save_todos:
            out[s.save_todos] = "list"
        return out

    def reads(self, step: Any, an: Any) -> set[str]:
        names = super().reads(step, an)
        if step.settings.files:
            names.add(step.settings.files)
        for helper in self.helper_steps(step, an):
            names |= an.handlers[helper.id].reads(helper, an)
        return names

    def helper_steps(self, step: Any, an: Any) -> list[Any]:
        """Its Helpers (each Helper works for one Deep Agent: the first that lists it)."""
        return [
            an.steps[h]
            for h in step.settings.helpers
            if h in an.steps and an.steps[h].type == "helper" and an.helper_of.get(h) == step.id
        ]

    def built_in_tools(self, step: Any, an: Any) -> set[str]:
        return super().built_in_tools(step, an) | DEEP_AGENT_TOOLS

    def is_async(self, step: Any, an: Any) -> bool:
        return super().is_async(step, an) or any(
            an.handlers[h.id].is_async(h, an) for h in self.helper_steps(step, an)
        )

    # ── checks ───────────────────────────────────────────────────────────────

    def check(self, step: Any, an: Any) -> list[Issue]:
        s = step.settings
        issues = super().check(step, an)
        for helper_id in s.helpers:
            helper = an.steps.get(helper_id)
            remove = Fix(
                "remove_from_list",
                "Remove it from the Helpers",
                {"setting": "helpers", "value": helper_id},
            )
            if helper is None or helper.type != "helper":
                what = (
                    f"“{helper_id}” isn't a step in this flow."
                    if helper is None
                    else f"“{helper.name or helper_id}” is a {an.handlers[helper_id].label} step, "
                    "not a Helper."
                )
                issues.append(
                    error("helper_missing", what, step=step.id, setting="helpers", fix=remove)
                )
                continue
            owner = an.helper_of.get(helper_id)
            if owner != step.id:
                issues.append(
                    error(
                        "helper_shared",
                        f"The Helper “{helper.name or helper_id}” already works for "
                        f"“{an.steps[owner].name or owner}”. A Helper belongs to one Deep Agent.",
                        step=step.id,
                        setting="helpers",
                        hint="Copy the Helper (Duplicate) and give the copy to this Deep Agent.",
                        fix=remove,
                    )
                )
        names = [("save_as", s.save_as), ("files", s.files), ("save_todos", s.save_todos)]
        seen: dict[str, str] = {}
        for setting, name in names:
            if not name:
                continue
            if name in seen:
                issues.append(
                    error(
                        "deep_agent_same_field",
                        f"The answer, the files and the to-do list need their own fields; "
                        f"`{name}` is used twice.",
                        step=step.id,
                        setting=setting,
                    )
                )
            seen[name] = setting
        return issues

    # ── code ─────────────────────────────────────────────────────────────────

    def emit(self, step: Any, ctx: Any) -> StepCode:
        s = step.settings
        an = ctx.an
        fn = ctx.fn(step.id)
        is_async = self.is_async(step, an)
        _use_deepagents(ctx)
        ctx.imports.add_from("deepagents", "create_deep_agent")
        ctx.imports.add_from("deepagents", "FilesystemMiddleware")
        ctx.imports.add_from("langchain.agents.middleware", "TodoListMiddleware")
        file_tools = ctx.helper("FILE_TOOLS")
        definitions: list[str] = []
        instructions_src = self._instructions(step, s.instructions, ctx, definitions)
        output_class = self._output_class(step, ctx, definitions)
        tool_calls, mcp_line = self._tools(step, s, ctx, definitions)

        helpers: list[str] = []
        helper_ids: list[str] = []
        for helper in self.helper_steps(step, an):
            handler = an.handlers[helper.id]
            factory, defs, helper_async = handler.emit_spec(helper, ctx, step)
            definitions += defs
            helpers.append(f"await {factory}(data)" if helper_async else f"{factory}(data)")
            helper_ids.append(helper.id)

        body: list[str] = []
        if mcp_line:
            body.append(mcp_line)
        model = ctx.model_call(
            s.model, {"temperature": s.temperature} if s.temperature is not None else {}
        )
        body.append(f"    model = {model}")
        tools_line = f"    tools = [{', '.join(tool_calls)}]"
        if not tool_calls:
            ctx.imports.add_from("langchain_core.tools", "BaseTool")
            tools_line = "    tools: list[BaseTool] = []"
        elif len(tools_line) > 92:
            tools_line = (
                "    tools = [\n" + "".join(f"        {t},\n" for t in tool_calls) + "    ]"
            )
        body.append(tools_line)
        addons = self._middleware(step, ctx, todo_list=False)
        if addons:
            body.append(
                "    addons = [\n"
                + "".join(f"        {_shift(m, -4)},\n" for m in addons)
                + "    ]"
            )
        addons_src = "addons" if addons else "[]"
        middleware = f"[TodoListMiddleware(), FilesystemMiddleware(tools={file_tools})" + (
            ", *addons]" if addons else "]"
        )
        subagents = [f"{ctx.helper('general_helper')}(model, tools, {addons_src})", *helpers]
        args = ["model=model", "tools=tools"]
        if instructions_src != "None":
            args.append(f"system_prompt={instructions_src}")
        args.append(f"middleware={middleware}")
        args.append(
            "subagents=[\n" + "".join(f"            {h},\n" for h in subagents) + "        ]"
        )
        if output_class:
            args.append(f"response_format=ToolStrategy({output_class})")
        args.append(f"name={py_str(step.id)}")
        body.append(
            "    agent = create_deep_agent(\n" + "".join(f"        {a},\n" for a in args) + "    )"
        )

        field, messages = self._messages(step, ctx)
        entries = [f'"messages": {messages}']
        if s.files:
            entries.append(f'"files": {ctx.helper("agent_files")}(data.get({py_str(s.files)}))')
        payload = "{" + ", ".join(entries) + "}"
        if len(payload) > 76:
            payload = "{\n" + "".join(f"            {e},\n" for e in entries) + "        }"
        body.append(self._invoke(is_async, payload, s.max_steps))
        tail, returns = self._answer(step, ctx, output_class)
        if s.files:
            returns.append(f'{py_str(s.files)}: {ctx.helper("flow_files")}(result.get("files"))')
        if s.save_todos:
            returns.append(f'{py_str(s.save_todos)}: result.get("todos") or []')
        returned = "{" + ", ".join(returns) + "}"
        if len(returned) > 80:
            returned = "{\n" + "".join(f"        {r},\n" for r in returns) + "    }"
        body += [*tail, f"    return {returned}"]

        tool_ids = [t.id for t in self.tool_steps(step, an)]
        uses = (
            f" and the tool{'s' if len(tool_ids) > 1 else ''} {_names(tool_ids)}"
            if tool_ids
            else ""
        )
        team = (
            f", hands tasks to the Helper{'s' if len(helper_ids) > 1 else ''} {_names(helper_ids)}"
            if helper_ids
            else ""
        )
        keeps = f", keeps its files in `{s.files}`" if s.files else ""
        doc = docstring(
            f"{self.title(step)}\n\nWorks on `{field}` using {s.model}{uses}{team}{keeps}, and "
            f"saves its answer as `{s.save_as}`."
        )
        head = "async def" if is_async else "def"
        code = f"{head} {fn}(data: {ctx.data_class}) -> dict[str, Any]:\n{doc}\n" + "\n".join(body)
        return StepCode([*definitions, code], node=fn, is_async=is_async)


class HelperHandler(AgentHandler):
    type = "helper"
    label = "Helper"
    technical = "Sub-agent · SubAgent"
    icon = "user-cog"
    summary = (
        "A specialist a Deep Agent hands sub-tasks to: its own role, tools and model. Describe "
        "it so the Deep Agent knows what to hand it."
    )
    beginner = False
    has_node = False
    warn_no_tools = False
    form = [
        FormField(
            key="model",
            label="Model",
            kind="model",
            help="The model this Helper uses. Empty: its Deep Agent's model. A smaller, cheaper "
            "model is often enough for a focused task.",
            placeholder="Its Deep Agent's model",
        ),
        FormField(
            key="instructions",
            label="Role and rules",
            kind="template",
            help="What this Helper is good at and how it should work and reply. Put Flow Data "
            "in with {field}.",
            example="You find facts on the web. Search, read the best pages, and reply with "
            "short notes and their sources.",
            technical="system_prompt",
        ),
        FormField(
            key="tools",
            label="Tools",
            kind="tools",
            help="Steps this Helper can use. Connect a step to the Helper's Tools handle.",
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
            key="addons",
            label="Add-ons",
            kind="agent_addons",
            help="Approvals, limits, retries and more, for this Helper's own work.",
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
    ]

    def primary_output(self, step: Any) -> str | None:
        return None

    def input_field(self, step: Any, an: Any) -> str | None:
        return None

    def writes(self, step: Any, an: Any) -> dict[str, str]:
        return {}

    def reads(self, step: Any, an: Any) -> set[str]:
        return set(variables(step.settings.instructions))

    def built_in_tools(self, step: Any, an: Any) -> set[str]:
        return super().built_in_tools(step, an) | FILE_TOOL_NAMES

    def model_of(self, step: Any, an: Any) -> str:
        owner = an.helper_of.get(step.id)
        return step.settings.model or (an.steps[owner].settings.model if owner else "")

    # ── checks ───────────────────────────────────────────────────────────────

    def check(self, step: Any, an: Any) -> list[Issue]:
        s = step.settings
        issues: list[Issue] = []
        if step.id not in an.helper_of:
            issues.append(
                warning(
                    "helper_unused",
                    "This Helper isn't on a Deep Agent's team, so it won't run.",
                    step=step.id,
                    hint="Connect it to a Deep Agent's Helpers handle, or delete it.",
                )
            )
        if an.incoming[step.id] or an.outgoing[step.id]:
            issues.append(
                error(
                    "helper_connected",
                    "A Helper only works when its Deep Agent hands it a task, so it can't also "
                    "be connected to other steps.",
                    step=step.id,
                    hint="Remove its connections, and connect it to a Deep Agent's Helpers handle.",
                )
            )
        if not (step.description or "").strip():
            issues.append(
                warning(
                    "helper_no_description",
                    "Describe what this Helper does: its Deep Agent reads it to decide which "
                    "tasks to hand it.",
                    step=step.id,
                    setting="description",
                    hint="For example: Finds facts on the web and replies with sources.",
                )
            )
        if s.model:
            issues += check_model(step.id, s.model)
        for n, model in enumerate(s.addons.fallback_models):
            issues += check_model(step.id, model, setting=f"addons.fallback_models.{n}")
        issues += secrets_in_prompt(step, [("instructions", s.instructions)], "the role and rules")
        if step.id in an.helper_of:
            available = an.available_fields(step.id)
            for name in variables(s.instructions):
                if name not in available:
                    issues.append(
                        missing_field_issue(
                            step, name, an, "instructions", "The role and rules use"
                        )
                    )
        tool_issues, names = check_tools(step.id, s.tools, an)
        issues += tool_issues
        issues += check_approvals(step.id, s, names | self.built_in_tools(step, an))
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

    def is_async(self, step: Any, an: Any) -> bool:
        if step.settings.mcp:
            return True
        return any(
            an.handlers[t.id].is_async(t, an) or has_run_policy(t)
            for t in self.tool_steps(step, an)
        )

    def emit(self, step: Any, ctx: Any) -> StepCode:
        # A Helper isn't a node: its Deep Agent emits it (``emit_spec``).
        return StepCode()

    def emit_spec(self, step: Any, ctx: Any, owner: Any) -> tuple[str, list[str], bool]:
        """``<helper>_helper(data)``: the Helper as a deepagents SubAgent spec."""
        s = step.settings
        an = ctx.an
        _use_deepagents(ctx)
        ctx.imports.add_from("deepagents", "FilesystemMiddleware")
        ctx.imports.add_from("deepagents", "SubAgent")
        file_tools = ctx.helper("FILE_TOOLS")
        definitions: list[str] = []
        instructions_src = self._instructions(step, s.instructions, ctx, definitions)
        tool_calls, mcp_line = self._tools(step, s, ctx, definitions)
        model_name = self.model_of(step, an)
        temperature = s.temperature
        if temperature is None and not s.model:
            temperature = owner.settings.temperature
        model = ctx.model_call(
            model_name, {"temperature": temperature} if temperature is not None else {}
        )
        description = " ".join((step.description or step.name or f"The helper {step.id}").split())
        entries = [f'"name": {py_str(step.id)}', f'"description": {py_str(description)}']
        if instructions_src != "None":
            entries.append(f'"system_prompt": {instructions_src}')
        entries.append(f'"model": {_shift(model, 4)}')
        tools = f'"tools": [{", ".join(tool_calls)}]'
        if len(tools) > 80:
            tools = (
                '"tools": [\n' + "".join(f"            {t},\n" for t in tool_calls) + "        ]"
            )
        entries.append(tools)
        middleware = [f"FilesystemMiddleware(tools={file_tools})"]
        middleware += self._middleware(step, ctx, model_name)
        entries.append(
            '"middleware": [\n' + "".join(f"            {m},\n" for m in middleware) + "        ]"
        )
        is_async = self.is_async(step, an)
        factory = ctx.names.claim(f"{step.id}_helper")
        tool_ids = [t.id for t in self.tool_steps(step, an)]
        uses = (
            f", works on them with the tool{'s' if len(tool_ids) > 1 else ''} {_names(tool_ids)}"
            if tool_ids
            else ""
        )
        doc = docstring(
            f"{self.title(step)}\n\nA sub-agent of `{owner.id}`: it gets tasks from it{uses}, "
            f"and replies using {model_name}."
        )
        lines = [mcp_line] if mcp_line else []
        lines.append("    return {\n" + "".join(f"        {e},\n" for e in entries) + "    }")
        head = "async def" if is_async else "def"
        code = f"{head} {factory}(data: {ctx.data_class}) -> SubAgent:\n{doc}\n" + "\n".join(lines)
        return factory, [*definitions, code], is_async
