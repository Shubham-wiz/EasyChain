"""Helper functions that generated code may include.

They are copied into the generated module only when a step needs them, so
exported code stays self-contained (no Easy Chain runtime required).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Helper:
    name: str
    code: str
    imports: tuple[str, ...] = ()
    from_imports: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    # Reducers appear in Flow Data annotations and must be defined before them.
    reducer: bool = False


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
    """Like fill(), but URL-encodes values (except a placeholder that starts the URL)."""
    query_at = template.find("?")

    def value(match: re.Match[str]) -> str:
        text = fill(match.group(0), data)
        if match.start() == 0:
            return text
        in_query = query_at != -1 and match.start() > query_at
        return quote(text, safe="" if in_query else "/")

    return re.sub(r"\\{(?:secret:)?[A-Za-z_][A-Za-z0-9_]*\\}", value, template)
''',
    from_imports=(("typing", "Any"), ("urllib.parse", "quote")),
    imports=("re",),
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
    """Match an AI reply to one of a Decision's exit names (ignoring case)."""
    text = reply.strip().strip(".!\\"'`*").lower()
    for name in exits:
        if text == name.lower():
            return name
    for name in exits:
        if name.lower() in text:
            return name
    return otherwise
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

HELPERS = {h.name: h for h in (MERGE_DICTS, FILL, FILL_URL, FILL_JSON, READABLE_TEXT, PICK_EXIT)}
