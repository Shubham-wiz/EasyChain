"""Helper functions that generated code may include.

They are copied into the generated module only when a step needs them, so
exported code stays self-contained (no Easy Chain runtime required).
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Helper:
    name: str
    code: str
    imports: tuple[str, ...] = ()
    from_imports: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    # Reducers appear in Flow Data annotations and must be defined before them.
    reducer: bool = False
    # Other helpers this one calls.
    requires: tuple[str, ...] = ()
    # Packages exported code needs for it (requirements.txt).
    requirements: tuple[str, ...] = ()


def _source(*objects: object, constants: tuple[str, ...] = ()) -> str:
    """Helper code taken from Easy Chain's own source, so there is one implementation.

    ``constants`` are module-level caches (empty dicts) the functions share."""
    import inspect

    parts = [f"{name}: dict[Any, Any] = {{}}" for name in constants]
    parts += [inspect.getsource(o).strip("\n") for o in objects]  # type: ignore[arg-type]
    return "\n" + "\n\n\n".join(parts) + "\n"


FILL = Helper(
    "fill",
    '''
def fill(template: str, data: dict[str, Any]) -> str:
    """Put Flow Data values into {field} and environment variables into {secret:NAME}."""

    def value(match: re.Match[str]) -> str:
        name = match.group(1)
        if name.startswith("secret:"):
            return os.environ.get(name.removeprefix("secret:"), "")
        found = data.get(name)
        return "" if found is None else str(found)

    return re.sub(r"\\{((?:secret:)?[A-Za-z_][A-Za-z0-9_]*)\\}", value, template)
''',
    imports=("os", "re"),
    from_imports=(("typing", "Any"),),
)

FILL_URL = Helper(
    "fill_url",
    '''
def fill_url(template: str, data: dict[str, Any]) -> str:
    """Like fill(), but URL-encodes every value, "/" included, so no value can change which
    address is called. A placeholder that starts the URL (a base address such as {base_url})
    is used as it is. A value that would make a part of the path empty, "." or ".." is refused.
    """
    placeholder = r"\\{(?:secret:)?[A-Za-z_][A-Za-z0-9_]*\\}"

    def encoded(text: str) -> str:
        return re.sub(placeholder, lambda m: quote(fill(m.group(0), data), safe=""), text)

    start = ""
    first = re.match(placeholder, template)
    if first:
        start, template = fill(first.group(0), data), template[first.end() :]
    path, mark, query = template.partition("?")
    parts = []
    for part in path.split("/"):
        filled = encoded(part)
        if filled != part and filled in ("", ".", ".."):
            shown = f"“{filled}”" if filled else "empty"
            raise ValueError(
                f"{part} in the URL is {shown}, which would call a different address. "
                "Give it a real value."
            )
        parts.append(filled)
    return start + "/".join(parts) + mark + encoded(query)
''',
    from_imports=(("typing", "Any"), ("urllib.parse", "quote")),
    imports=("re",),
    requires=("fill",),
)

FILL_JSON = Helper(
    "fill_json",
    '''
def fill_json(template: str, data: dict[str, Any]) -> str:
    """Like fill(), but writes values as JSON so quotes and newlines can't break the body.

    "{field}" becomes a JSON string, a bare {field} becomes its JSON value, and a
    placeholder inside a longer string is escaped.
    """
    out: list[str] = []
    in_string = False
    pos = 0
    for match in re.finditer(r"\\{(?:secret:)?[A-Za-z_][A-Za-z0-9_]*\\}", template):
        literal = template[pos : match.start()]
        for i, char in enumerate(literal):
            if char == \'"\' and (i == 0 or literal[i - 1] != "\\\\"):
                in_string = not in_string
        text = fill(match.group(0), data)
        name = match.group(0)[1:-1]
        found = text if name.startswith("secret:") else data.get(name)
        after = template[match.end() : match.end() + 1]
        if in_string and literal.endswith(\'"\') and after == \'"\':
            out.append(literal[:-1] + json.dumps(text, ensure_ascii=False))
            pos = match.end() + 1
            in_string = False
            continue
        out.append(literal)
        if in_string:
            out.append(json.dumps(text, ensure_ascii=False)[1:-1])
        else:
            out.append(json.dumps(found, ensure_ascii=False, default=str))
        pos = match.end()
    out.append(template[pos:])
    return "".join(out)
''',
    from_imports=(("typing", "Any"),),
    imports=("json", "re"),
    requires=("fill",),
)

READABLE_TEXT = Helper(
    "readable_text",
    '''
class _TextExtractor(HTMLParser):
    """Collects the visible text of an HTML page."""

    SKIP = {"script", "style", "noscript", "head", "svg", "template"}
    BLOCK = {
        "p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
        "section", "article", "header", "footer", "pre", "blockquote",
    }

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.skipping = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in self.SKIP:
            self.skipping += 1
        elif tag in self.BLOCK:
            self.parts.append("\\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self.skipping:
            self.skipping -= 1
        elif tag in self.BLOCK:
            self.parts.append("\\n")

    def handle_data(self, text: str) -> None:
        if not self.skipping:
            self.parts.append(text)


def readable_text(page: str) -> str:
    """Turn an HTML page into the plain text a person would read."""
    if "<" not in page:
        return page.strip()
    parser = _TextExtractor()
    parser.feed(page)
    lines = (re.sub(r"\\s+", " ", line).strip() for line in "".join(parser.parts).splitlines())
    return re.sub(r"\\n{3,}", "\\n\\n", "\\n".join(lines)).strip()
''',
    imports=("re",),
    from_imports=(("html.parser", "HTMLParser"),),
)

PICK_EXIT = Helper(
    "pick_exit",
    '''
def pick_exit(reply: str, exits: list[str], otherwise: str) -> str:
    """Match an AI reply to one of a Decision's exit names.

    The reply must be an exit name, or start with one ("Refund, because ..."), ignoring case,
    spaces and punctuation. Longer names are tried first, so "No refund" isn't taken for "No".
    Anything else ("None of the above", "Not a complaint") takes the otherwise exit.
    """
    said = re.findall(r"\\w+", reply.lower())
    for name in sorted(exits, key=lambda name: -len(re.findall(r"\\w+", name))):
        words = re.findall(r"\\w+", name.lower())
        if reply.strip().lower() == name.strip().lower() or (
            words and said[: len(words)] == words
        ):
            return name
    return otherwise
''',
    imports=("re",),
)

PICK_OPTION = Helper(
    "pick_option",
    '''
def pick_option(answer: str, options: list[str]) -> str | None:
    """The option a person picked: the one their answer names exactly (ignoring case and
    spaces), or None."""
    said = " ".join(answer.split()).lower()
    return next((o for o in options if " ".join(o.split()).lower() == said), None)
''',
)

MERGE_DICTS = Helper(
    "merge_dicts",
    '''
def merge_dicts(old: dict[str, Any] | None, new: dict[str, Any] | None) -> dict[str, Any]:
    """Update rule "merge": add the new keys to the existing object."""
    return {**(old or {}), **(new or {})}
''',
    from_imports=(("typing", "Any"),),
    reducer=True,
)

COLLECT_ITEMS = Helper(
    "collect_items",
    '''
def collect_items(old: list[Any] | None, new: list[Any] | None) -> list[Any]:
    """Update rule for For Each results: None starts a new list, anything else is added."""
    if new is None:
        return []
    return (old or []) + new
''',
    from_imports=(("typing", "Any"),),
    reducer=True,
)

IN_THREAD = Helper(
    "in_thread",
    '''
def in_thread(step: Callable[[Any], Any]) -> Callable[[Any], Any]:
    """Run a step in a worker thread, so LangGraph can enforce its time limit."""

    async def run(data: Any) -> Any:
        return await asyncio.to_thread(step, data)

    run.__name__ = step.__name__
    return run
''',
    imports=("asyncio",),
    from_imports=(("typing", "Any"), ("collections.abc", "Callable")),
)

WITH_RUN_POLICY = Helper(
    "with_run_policy",
    '''
async def with_run_policy(
    step: Callable[[Any], Any],
    data: Any,
    *,
    timeout: float | None = None,
    retry: RetryPolicy | None = None,
) -> Any:
    """Run a step that an agent uses as a tool with the step's own time limit and retries,
    the way LangGraph runs a step in the flow."""
    attempt = 1
    while True:
        work = step(data) if inspect.iscoroutinefunction(step) else asyncio.to_thread(step, data)
        try:
            return await asyncio.wait_for(work, timeout)
        except GraphBubbleUp:
            raise  # a pause (Ask a Human, an approval) isn't a failure
        except Exception as error:
            if retry is None or attempt >= retry.max_attempts or not retry.retry_on(error):
                if isinstance(error, TimeoutError) and timeout and not str(error):
                    message = f"It took longer than its time limit ({timeout:g} s)."
                    raise TimeoutError(message) from None
                raise
            backoff = retry.initial_interval * retry.backoff_factor ** (attempt - 1)
            jitter = random.uniform(0, 1) if retry.jitter else 0
            await asyncio.sleep(min(retry.max_interval, backoff) + jitter)
        attempt += 1
''',
    imports=("asyncio", "inspect", "random"),
    from_imports=(
        ("typing", "Any"),
        ("collections.abc", "Callable"),
        ("langgraph.errors", "GraphBubbleUp"),
        ("langgraph.types", "RetryPolicy"),
    ),
)

IDEMPOTENCY_KEY = Helper(
    "idempotency_key",
    '''
def idempotency_key() -> str:
    """A key that stays the same when this step is retried or resumed after a crash."""
    configurable = get_config()["configurable"]
    task = f"{configurable.get('thread_id', '')}|{configurable.get('checkpoint_ns', '')}"
    return hashlib.sha256(task.encode()).hexdigest()[:32]
''',
    imports=("hashlib",),
    from_imports=(("langgraph.config", "get_config"),),
)

RUN_ONCE = Helper(
    "run_once",
    '''
def run_once(key: str, action: Callable[[], Any]) -> Any:
    """Do a side effect at most once per key, remembering its result in the LangGraph store."""
    store = get_store()
    if store is None:
        return action()
    saved = store.get(("side_effects",), key)
    if saved is not None:
        return saved.value["result"]
    result = action()
    try:
        store.put(("side_effects",), key, {"result": result})
    except (TypeError, ValueError):
        pass  # results that can't be stored as JSON aren't remembered
    return result
''',
    from_imports=(
        ("typing", "Any"),
        ("collections.abc", "Callable"),
        ("langgraph.config", "get_store"),
    ),
)

ASK_IN_TERMINAL = Helper(
    "ask_in_terminal",
    '''
def ask_in_terminal(request: dict[str, Any]) -> dict[str, Any]:
    """Answer an Ask a Human step in the terminal (Easy Chain's Inbox does this in the app)."""
    if "action_requests" in request:
        # An agent wants to use a tool that needs approval (HumanInTheLoopMiddleware).
        decisions = []
        for action in request["action_requests"]:
            print(f"\\nThe agent wants to use {action['name']} with {action['args']}")
            if input("approve? [y/n]> ").strip().lower().startswith("y"):
                decisions.append({"type": "approve"})
            else:
                decisions.append({"type": "reject", "message": input("why not?> ")})
        return {"decisions": decisions}
    print(f"\\n{request['question']}")
    for name, value in request.get("show", {}).items():
        print(f"  {name}: {value}")
    kind = request.get("kind", "approve")
    if kind == "answer":
        return {"action": "approve", "value": input("answer> ")}
    if kind == "choose":
        options = request.get("options", [])
        for number, option in enumerate(options, start=1):
            print(f"  {number}. {option}")
        while True:
            picked = input("choose a number> ").strip()
            if picked.isdigit() and 1 <= int(picked) <= len(options):
                return {"action": "approve", "value": options[int(picked) - 1]}
            print(f"Type a number from 1 to {len(options)}.")
    approved = input("approve? [y/n]> ").strip().lower().startswith("y")
    answer: dict[str, Any] = {"action": "approve" if approved else "reject", "comment": input("comment> ")}
    if kind == "edit" and approved:
        edited = input(f"new {request.get('field')} (leave empty to keep it)> ")
        if edited:
            answer["value"] = edited
    return answer
''',
    from_imports=(("typing", "Any"),),
)

AS_TEXT = Helper(
    "as_text",
    '''
def as_text(value: Any) -> str:
    """Put a list or object into a prompt as readable text (one list item per paragraph)."""
    if value is None:
        return ""
    if isinstance(value, list):
        return "\\n\\n".join(as_text(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, indent=2, ensure_ascii=False, default=str)
    return str(value)
''',
    imports=("json",),
    from_imports=(("typing", "Any"),),
)

TELL_AGENT = Helper(
    "tell_agent",
    '''
def tell_agent(error: Exception, request: Any) -> str:
    """When a tool fails, tell the agent (so it can try another way) instead of stopping."""
    return f"The tool failed ({type(error).__name__}): {error}"
''',
    from_imports=(("typing", "Any"),),
)


def _knowledge_helpers() -> tuple[Helper, ...]:
    from ..integrations import mcp, sql
    from ..knowledge import memory, search
    from ..knowledge.embeddings import KeywordEmbeddings

    graph_imports = (
        ("typing", "Any"),
        ("langgraph.config", "get_config"),
        ("langgraph.config", "get_store"),
    )
    return (
        Helper(
            "mcp_tools",
            _source(*mcp.HELPER_FUNCTIONS),
            imports=("json", "os"),
            from_imports=(
                ("langchain_core.tools", "BaseTool"),
                ("langchain_mcp_adapters.client", "MultiServerMCPClient"),
            ),
            requirements=("langchain-mcp-adapters==0.3.2",),
        ),
        Helper("mcp_text", _source(mcp.mcp_text), from_imports=(("typing", "Any"),)),
        Helper(
            "sql",
            _source(*sql.HELPER_FUNCTIONS, constants=sql.HELPER_GLOBALS),
            imports=("datetime", "decimal", "os", "re", "sqlalchemy as sa"),
            from_imports=(("typing", "Any"),),
            requirements=("sqlalchemy>=2.0.36", "psycopg[binary]>=3.2"),
        ),
        Helper(
            "memory",
            _source(*memory.HELPER_FUNCTIONS),
            imports=("re", "uuid"),
            from_imports=graph_imports,
        ),
        Helper(
            "memory_tools",
            _source(memory.memory_tools),
            from_imports=(
                ("typing", "Any"),
                ("langchain_core.tools", "BaseTool"),
                ("langchain_core.tools", "tool"),
            ),
            requires=("memory",),
        ),
        Helper(
            "search_knowledge",
            _source(*search.HELPER_FUNCTIONS, constants=search.HELPER_GLOBALS),
            imports=("os", "re", "numpy as np", "sqlalchemy as sa"),
            from_imports=(("typing", "Any"),),
            requirements=("sqlalchemy>=2.0.36", "numpy>=2", "psycopg[binary]>=3.2"),
        ),
        Helper("cite_passages", _source(search.cite_passages), from_imports=(("typing", "Any"),)),
        Helper(
            "rerank_passages",
            _source(search.rerank_passages),
            imports=("re",),
            from_imports=(("typing", "Any"),),
        ),
        Helper(
            "KeywordEmbeddings",
            _source(KeywordEmbeddings),
            imports=("hashlib", "math", "re"),
            from_imports=(("langchain_core.embeddings", "Embeddings"),),
        ),
    )


HELPERS = {
    h.name: h
    for h in (
        MERGE_DICTS,
        AS_TEXT,
        COLLECT_ITEMS,
        FILL,
        FILL_URL,
        FILL_JSON,
        READABLE_TEXT,
        PICK_EXIT,
        PICK_OPTION,
        IN_THREAD,
        WITH_RUN_POLICY,
        IDEMPOTENCY_KEY,
        RUN_ONCE,
        ASK_IN_TERMINAL,
        TELL_AGENT,
        *_knowledge_helpers(),
    )
}


@functools.cache
def helper_names() -> frozenset[str]:
    """Every module-level name helper code defines or imports, so steps can't take them."""
    import ast

    names: set[str] = set()
    for helper in HELPERS.values():
        for node in ast.parse(helper.code).body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                names.add(node.name)
            elif isinstance(node, ast.AnnAssign | ast.Assign):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                names.update(t.id for t in targets if isinstance(t, ast.Name))
        for module in helper.imports:
            module, _, alias = module.partition(" as ")
            names.add(alias or module.split(".")[0])
        for _module, imported in helper.from_imports:
            name, _, alias = imported.partition(" as ")
            names.add(alias or name)
    return frozenset(names)
