"""Action steps: Web request (HTTP) and Code (a Python function)."""

from __future__ import annotations

import ast
import functools
import re
from typing import Any

from ..compiler.issues import Issue, error, warning
from ..compiler.pycode import Names, docstring, py_literal, py_str
from ..compiler.templates import secrets, variables
from .ai import missing_field_issue
from .base import FormField, StepCode, StepHandler

USER_AGENT = "Mozilla/5.0 (compatible; EasyChain/0.1)"


class HttpRequestHandler(StepHandler):
    type = "http_request"
    label = "Web request"
    technical = "Action · HTTP request tool"
    category = "actions"
    icon = "globe"
    summary = "Fetches a web page or calls an API, and saves what comes back."
    default_name = "Fetch the page"
    form = [
        FormField(
            key="url",
            label="URL",
            kind="template",
            help="The address to call. Use {field} to put in Flow Data, for example {url}.",
            example="https://api.example.com/search?q={question}",
            placeholder="https://… or {url}",
        ),
        FormField(
            key="method",
            label="Method",
            kind="select",
            options=[{"value": m, "label": m} for m in ("GET", "POST", "PUT", "PATCH", "DELETE")],
            help="GET reads a page. POST sends data.",
        ),
        FormField(
            key="response",
            label="Keep",
            kind="select",
            options=[
                {"value": "readable_text", "label": "The readable text (good for web pages)"},
                {"value": "text", "label": "The raw response"},
                {"value": "json", "label": "JSON data (for APIs)"},
            ],
            help="Readable text drops menus, scripts and HTML tags so an AI Model can read the page.",
        ),
        FormField(
            key="save_as",
            label="Save the result as",
            kind="field_name",
            example="page",
            help="The Flow Data field that holds what came back.",
        ),
        FormField(
            key="headers",
            label="Headers",
            kind="key_value",
            help="Extra HTTP headers. Use {secret:NAME} for API keys so they never appear in the flow.",
            example="Authorization: Bearer {secret:MY_API_KEY}",
            advanced=True,
        ),
        FormField(
            key="body",
            label="Body",
            kind="template",
            help="Data to send with POST, PUT or PATCH. JSON is fine; {field} values are filled in.",
            example='{"query": "{question}"}',
            advanced=True,
            show_if={"method": ["POST", "PUT", "PATCH", "DELETE"]},
        ),
        FormField(
            key="max_chars",
            label="Keep at most (characters)",
            kind="number",
            min=1,
            help="Cuts long pages so they fit in the AI Model's context.",
            advanced=True,
        ),
        FormField(key="timeout", label="Time limit (seconds)", kind="number", min=1, advanced=True),
    ]

    def _texts(self, step: Any) -> list[tuple[str, str]]:
        s = step.settings
        return [("url", s.url), ("body", s.body), *(("headers", v) for v in s.headers.values())]

    def writes(self, step: Any, an: Any) -> dict[str, str]:
        return {step.settings.save_as: "any" if step.settings.response == "json" else "text"}

    def reads(self, step: Any, an: Any) -> set[str]:
        return {name for _, text in self._texts(step) for name in variables(text)}

    def check(self, step: Any, an: Any) -> list[Issue]:
        s = step.settings
        issues: list[Issue] = []
        url = s.url.strip()
        if not url:
            issues.append(
                error(
                    "no_url",
                    "Add the URL to call.",
                    step=step.id,
                    setting="url",
                    hint="Type an address like https://example.com, or {url} to use the input.",
                )
            )
        elif not (url.startswith(("http://", "https://")) or url.startswith("{")):
            issues.append(
                warning(
                    "url_scheme",
                    "This URL doesn't start with https:// or http://.",
                    step=step.id,
                    setting="url",
                )
            )
        if s.body.strip() and s.method == "GET":
            issues.append(
                warning(
                    "get_with_body",
                    "GET requests don't send a body; it will be ignored.",
                    step=step.id,
                    setting="body",
                )
            )
        available = an.available_fields(step.id)
        for setting, text in self._texts(step):
            for name in variables(text):
                if name not in available:
                    issues.append(missing_field_issue(step, name, an, setting, "This request uses"))
        return issues

    def emit(self, step: Any, ctx: Any) -> StepCode:
        s = step.settings
        fn = ctx.fn(step.id)
        ctx.imports.add("httpx")
        ctx.uses_httpx = True

        def templated(text: str, kind: str = "fill") -> str:
            whole = re.fullmatch(r"\{([a-z][a-z0-9_]*)\}", text.strip())
            if whole and kind == "fill_url":
                return f"data[{py_str(whole.group(1))}]"  # the whole URL comes from a field
            if variables(text) or secrets(text):
                ctx.helper("fill")
                return f"{ctx.helper(kind)}({py_str(text)}, data)"
            return py_str(text)

        headers: dict[str, str] = {}
        if not any(k.lower() == "user-agent" for k in s.headers):
            headers["User-Agent"] = USER_AGENT
        body = s.body if s.method != "GET" else ""
        content_type = next((v for k, v in s.headers.items() if k.lower() == "content-type"), "")
        is_json = "json" in content_type.lower() or (
            not content_type and body.strip()[:1] in ("{", "[")
        )
        if is_json and not content_type:
            headers["Content-Type"] = "application/json"
        headers.update(s.headers)
        header_items = [f"{py_str(k)}: {templated(v)}" for k, v in headers.items()]
        header_src = "{" + ", ".join(header_items) + "}"
        if len(header_src) > 70:
            header_src = (
                "{\n" + "".join(f"            {item},\n" for item in header_items) + "        }"
            )

        args = [
            f"        {py_str(s.method)},",
            f"        {templated(s.url, 'fill_url')},",
            f"        headers={header_src},",
        ]
        if body.strip():
            args.append(f"        content={templated(body, 'fill_json' if is_json else 'fill')},")
        args.append(f"        timeout={py_literal(s.timeout)},")
        args.append("        follow_redirects=True,")

        if s.response == "json":
            value = "response.json()"
            what = "the JSON data"
        elif s.response == "text":
            value = "response.text"
            what = "the response"
        else:
            value = f"{ctx.helper('readable_text')}(response.text)"
            what = "the page text"
        if s.max_chars and s.response != "json":
            value += f"[:{s.max_chars}]"
        doc = docstring(
            f"{self.title(step)}\n\n{s.method} {s.url} and save {what} as `{s.save_as}`."
        )
        code = (
            f"def {fn}(data: FlowData) -> dict[str, Any]:\n"
            f"{doc}\n"
            "    response = httpx.request(\n" + "\n".join(args) + "\n    )\n"
            "    response.raise_for_status()\n"
            f"    return {{{py_str(s.save_as)}: {value}}}"
        )
        return StepCode([code], node=fn)


# ── Code ─────────────────────────────────────────────────────────────────────


@functools.lru_cache(maxsize=4096)
def analyse_code(code: str) -> CodeAnalysis:
    """Parse once per distinct code text (checks call this many times per flow)."""
    return CodeAnalysis(code)


class CodeAnalysis:
    """What a Code step's Python does, worked out from its syntax tree."""

    def __init__(self, code: str):
        self.error: str | None = None
        self.error_line: int | None = None
        self.func: ast.FunctionDef | None = None
        self.param = "data"
        self.writes: list[str] | None = None
        self.reads: set[str] = set()
        self.top_level_names: set[str] = set()
        # Plain top-level imports, hoisted into the generated module's import block.
        self.imports: list[tuple[int, int, list[tuple[str, str | None, str | None]]]] = []
        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            self.error = f"Python can't read this code: {exc.msg} (line {exc.lineno})."
            self.error_line = exc.lineno
            return
        for node in tree.body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                self.top_level_names.add(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        self.top_level_names.add(target.id)
            elif isinstance(node, ast.Import):
                specs = [(a.name, None, a.asname) for a in node.names]
                self.imports.append((node.lineno, node.end_lineno or node.lineno, specs))
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                if any(a.name == "*" for a in node.names):
                    self.top_level_names.add("*")
                    continue
                specs = [(node.module, a.name, a.asname) for a in node.names]
                self.imports.append((node.lineno, node.end_lineno or node.lineno, specs))
        runs = [
            n
            for n in tree.body
            if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name == "run"
        ]
        if not runs:
            self.error = (
                "The code needs a function called run(data) that returns the fields to update."
            )
            return
        func = runs[0]
        if isinstance(func, ast.AsyncFunctionDef):
            self.error = "run(data) can't be async in this version; use a normal def."
            self.error_line = func.lineno
            return
        if len(func.args.args) != 1 or func.args.vararg or func.args.kwonlyargs:
            self.error = "run should take exactly one argument: run(data)."
            self.error_line = func.lineno
            return
        self.func = func
        self.param = func.args.args[0].arg
        self.writes = self._infer_writes(func)
        self.reads = self._infer_reads(func)

    @staticmethod
    def _own_nodes(func: ast.FunctionDef) -> list[ast.AST]:
        nodes: list[ast.AST] = []
        stack: list[ast.AST] = list(func.body)
        while stack:
            node = stack.pop()
            nodes.append(node)
            for child in ast.iter_child_nodes(node):
                if not isinstance(
                    child, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.ClassDef
                ):
                    stack.append(child)
        return nodes

    def _infer_writes(self, func: ast.FunctionDef) -> list[str] | None:
        keys: dict[str, None] = {}
        returns = [n for n in self._own_nodes(func) if isinstance(n, ast.Return)]
        if not returns:
            return []
        for ret in returns:
            if ret.value is None or (
                isinstance(ret.value, ast.Constant) and ret.value.value is None
            ):
                continue
            if not isinstance(ret.value, ast.Dict):
                return None
            for key in ret.value.keys:
                if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
                    return None
                keys.setdefault(key.value, None)
        return list(keys)

    def _infer_reads(self, func: ast.FunctionDef) -> set[str]:
        reads: set[str] = set()
        for node in self._own_nodes(func):
            if (
                isinstance(node, ast.Subscript)
                and isinstance(node.value, ast.Name)
                and node.value.id == self.param
                and isinstance(node.slice, ast.Constant)
                and isinstance(node.slice.value, str)
            ):
                reads.add(node.slice.value)
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == self.param
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                reads.add(node.args[0].value)
        return reads


_IDENT = re.compile(r"^[a-z][a-z0-9_]{0,62}$")

# Imports a Code step may repeat because they bind the same thing the generated code does.
_SAFE_IMPORTS: dict[str, tuple[str, str | None]] = {
    "json": ("json", None),
    "sys": ("sys", None),
    "os": ("os", None),
    "re": ("re", None),
    "operator": ("operator", None),
    "httpx": ("httpx", None),
    "Any": ("typing", "Any"),
    "Annotated": ("typing", "Annotated"),
    "AnyMessage": ("langchain_core.messages", "AnyMessage"),
}


class CodeHandler(StepHandler):
    type = "code"
    label = "Code"
    technical = "Action · Python function"
    category = "actions"
    icon = "code"
    summary = "Runs a small Python function over Flow Data, for anything the other steps can't do."
    beginner = True
    form = [
        FormField(
            key="code",
            label="Python",
            kind="code",
            help="Write run(data). Read fields with data['name'] or data.get('name'); return a dict "
            "of the fields to set, e.g. {'word_count': 42}.",
        ),
        FormField(
            key="writes",
            label="Sets these fields",
            kind="string_list",
            help="Only needed when Easy Chain can't tell from the code which fields it returns.",
            advanced=True,
        ),
        FormField(
            key="requirements",
            label="Packages",
            kind="string_list",
            help="Python packages the code imports, e.g. pandas>=2.",
            advanced=True,
            pro=True,
        ),
    ]

    def analyse(self, step: Any) -> CodeAnalysis:
        return analyse_code(step.settings.code)

    def primary_output(self, step: Any) -> str | None:
        writes = step.settings.writes or (self.analyse(step).writes or [])
        return writes[0] if writes else None

    def writes(self, step: Any, an: Any) -> dict[str, str]:
        names = step.settings.writes or self.analyse(step).writes or []
        return {name: "any" for name in names if _IDENT.match(name)}

    def reads(self, step: Any, an: Any) -> set[str]:
        return self.analyse(step).reads

    def check(self, step: Any, an: Any) -> list[Issue]:
        info = self.analyse(step)
        issues: list[Issue] = []
        if info.error:
            hint = f"Line {info.error_line}." if info.error_line else None
            issues.append(
                error("code_invalid", info.error, step=step.id, setting="code", hint=hint)
            )
            return issues
        if info.writes is None and not step.settings.writes:
            issues.append(
                warning(
                    "code_writes_unknown",
                    "Easy Chain can't tell which fields this code sets.",
                    step=step.id,
                    setting="writes",
                    hint="Return a dict literal like {'total': total}, or list the fields under "
                    "“Sets these fields”. Fields that aren't listed are dropped.",
                )
            )
        for name in step.settings.writes or info.writes or []:
            if not _IDENT.match(name):
                issues.append(
                    error(
                        "code_bad_field",
                        f"`{name}` can't be a field name. Use lowercase letters, digits and underscores.",
                        step=step.id,
                        setting="code",
                    )
                )
        clash = info.top_level_names & (Names.RESERVED - {"data"})
        for _, _, specs in info.imports:
            for module, name, alias in specs:
                bound = alias or (name if name else module.split(".")[0])
                if bound in Names.RESERVED and _SAFE_IMPORTS.get(bound) != (module, name):
                    clash.add(bound)
        if "*" in info.top_level_names:
            issues.append(
                error(
                    "code_star_import",
                    "Use named imports instead of `import *`.",
                    step=step.id,
                    setting="code",
                )
            )
        if clash:
            issues.append(
                error(
                    "code_name_clash",
                    f"The code defines {', '.join(sorted(clash))}, which the generated flow already uses.",
                    step=step.id,
                    setting="code",
                    hint="Rename it.",
                )
            )
        mine = info.top_level_names - {"run"}
        if mine:
            for other in an.spec.steps:
                if other.type != "code" or an.index[other.id] >= an.index[step.id]:
                    continue
                shared = analyse_code(other.settings.code).top_level_names & mine
                shared = {
                    n for n in shared if n not in _SAFE_IMPORTS and n not in ("math", "datetime")
                }
                if shared:
                    issues.append(
                        warning(
                            "code_shared_names",
                            f"This code and “{other.name or other.id}” both define {', '.join(sorted(shared))}.",
                            step=step.id,
                            setting="code",
                            hint="Both end up in one Python file; rename one to avoid surprises.",
                        )
                    )
        available = an.available_fields(step.id)
        for name in sorted(info.reads):
            if name not in available and name in an.fields:
                issues.append(missing_field_issue(step, name, an, "code", "This code reads"))
        return issues

    def emit(self, step: Any, ctx: Any) -> StepCode:
        fn = ctx.fn(step.id)
        info = self.analyse(step)
        code = step.settings.code
        if info.func is not None:
            lines = code.split("\n")
            idx = info.func.lineno - 1
            lines[idx] = re.sub(r"\bdef\s+run\s*\(", f"def {fn}(", lines[idx], count=1)
            # Move plain imports up to the module's import block.
            for start, end, specs in info.imports:
                for module, name, alias in specs:
                    if name is None:
                        ctx.imports.add(module, alias)
                    else:
                        ctx.imports.add_from(module, f"{name} as {alias}" if alias else name)
                for i in range(start - 1, end):
                    lines[i] = None  # type: ignore[call-overload]
            code = "\n".join(line for line in lines if line is not None)
        code = code.strip("\n")
        sets = ", ".join(self.writes(step, ctx.an)) or "no fields"
        header = f"# {self.title(step)} (sets {sets})"
        for req in step.settings.requirements:
            if req not in ctx.extra_requirements:
                ctx.extra_requirements.append(req)
        return StepCode([header + "\n" + code], node=fn)
