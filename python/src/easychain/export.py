"""Export a flow as a standalone LangGraph project (no Easy Chain needed to run it)."""

from __future__ import annotations

import io
import json
import shlex
import subprocess
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .compiler import compile_flow
from .compiler.analysis import Resolver
from .compiler.templates import secrets
from .providers import flow_models, get_provider
from .spec import FlowSpec, dumps_spec


def _texts(value: Any) -> Iterator[str]:
    """Every piece of text in a step's settings, however deeply nested."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _texts(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _texts(item)


def env_example(specs: list[FlowSpec]) -> str:
    """The .env.example of an export: what the flow (and its Sub-flows) read from the
    environment. Model keys, {secret:NAME} values, and where MCP servers and Knowledge
    Bases are."""
    lines: list[str] = []
    providers = {
        provider.id: provider
        for spec in specs
        for _, _, model in flow_models(spec)
        if (provider := get_provider(model)) is not None
    }
    for pid in sorted(providers):
        provider = providers[pid]
        if provider.key_env:
            lines.append(f"{provider.key_env}=")
        lines += [f"{name}=" for name, _ in provider.settings_env]
        if provider.host_env:
            lines.append(f"# {provider.host_env}=http://localhost:11434")
        if provider.credentials:
            lines.append(f"# {provider.label}: {provider.credentials}")
    secret_names: set[str] = set()
    steps = [step for spec in specs for step in spec.steps]
    for step in steps:
        for text in _texts(step.settings.model_dump()):
            secret_names.update(secrets(text))
        if step.type == "ai_model" and step.settings.api_key:
            secret_names.add(step.settings.api_key)  # the key of a custom endpoint
    listed = {line.split("=")[0] for line in lines if not line.startswith("#")}
    lines += [f"{name}=" for name in sorted(secret_names - listed)]
    types = {step.type for step in steps}
    if "mcp_tool" in types or any(step.type == "agent" and step.settings.mcp for step in steps):
        lines += [
            "# How to reach each MCP server, as JSON by server id, for example",
            '# {"docs": {"transport": "streamable_http", "url": "https://example.com/mcp"}}',
            "EASYCHAIN_MCP_SERVERS=",
        ]
    if "knowledge_search" in types:
        lines += [
            "# The database that holds the Knowledge Bases, e.g. postgresql://user:password@host/db",
            "# (empty: EASYCHAIN_DATABASE_URL, or the local Easy Chain database)",
            "EASYCHAIN_KNOWLEDGE_URL=",
        ]
    return "\n".join(lines) + ("\n" if lines else "")


def run_hints(module: str, example: dict[str, Any] | None, chat: bool) -> tuple[str, str]:
    """How to run the exported flow from bash and from PowerShell, quoting the example
    inputs so that quotes in them (``hasn't``) survive."""
    if chat or not example:
        return f"python {module}.py", f"python {module}.py"
    inputs = json.dumps(example, ensure_ascii=False)
    # PowerShell passes what follows --% to the program as it is (in every version; without
    # it, Windows PowerShell drops the double quotes), and Python reads it with the
    # Windows rules that list2cmdline follows.
    powershell = f"--% {subprocess.list2cmdline([inputs])}"
    return f"python {module}.py {shlex.quote(inputs)}", f"python {module}.py {powershell}"


def export_files(
    spec: FlowSpec, resolve: Resolver | None = None, flow_id: str | None = None
) -> dict[str, str]:
    compiled = compile_flow(spec, resolve=resolve, flow_id=flow_id)
    mod = compiled.module_name
    env = env_example([spec, *(child.spec for child in compiled.children.values())])
    bash_hint, powershell_hint = run_hints(mod, compiled.example_input, compiled.chat)
    if compiled.chat:
        run_note = f"`python {mod}.py` starts a chat in the terminal."
    else:
        run_note = (
            f"`python {mod}.py` on its own runs the example inputs; give your own as JSON. In\n"
            "PowerShell, keep the `--%`: it passes the JSON to Python as it is written."
        )
    readme = f"""# {spec.name}

{spec.description or "A flow built with Easy Chain."}

This folder was exported from Easy Chain. It is plain LangChain + LangGraph code:
nothing from Easy Chain is needed to run it.

## Run it

In bash (Linux, macOS, or Git Bash on Windows):

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in your keys, and export them (or use direnv)
{bash_hint}
```

In PowerShell (Windows):

```powershell
python -m venv .venv; .venv\\Scripts\\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env   # then fill in your keys, and set them ($env:NAME = "...")
{powershell_hint}
```

{run_note}

## Use it from Python

```python
from {mod} import graph

result = graph.invoke({json.dumps(compiled.example_input or {"messages": [{"role": "user", "content": "Hello"}]})})
```

## Serve it with LangGraph

`langgraph.json` is included, so `langgraph dev` (from `langgraph-cli`) serves the
graph with the LangGraph API and Studio.

## Files

- `{mod}.py`: the flow as LangGraph code
- `{mod}.flow.yaml`: the Easy Chain flow spec, to open it on the canvas again
- `requirements.txt`: pinned dependencies
"""
    langgraph_json = {"dependencies": ["."], "graphs": {mod: f"./{mod}.py:graph"}, "env": ".env"}
    return {
        f"{mod}.py": compiled.source,
        "requirements.txt": "\n".join(compiled.requirements) + "\n",
        "langgraph.json": json.dumps(langgraph_json, indent=2) + "\n",
        ".env.example": env,
        "README.md": readme,
        f"{mod}.flow.yaml": dumps_spec(spec),
    }


def export_zip(
    spec: FlowSpec, resolve: Resolver | None = None, flow_id: str | None = None
) -> bytes:
    files = export_files(spec, resolve, flow_id)
    folder = next(name for name in files if name.endswith(".py"))[:-3]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(f"{folder}/{name}", content)
    return buf.getvalue()


def export_to_dir(
    spec: FlowSpec,
    directory: str | Path,
    resolve: Resolver | None = None,
    flow_id: str | None = None,
) -> list[Path]:
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for name, content in export_files(spec, resolve, flow_id).items():
        path = out / name
        path.write_text(content, encoding="utf-8", newline="\n")
        written.append(path)
    return written
