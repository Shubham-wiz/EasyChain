"""Export a flow as a standalone LangGraph project (no Easy Chain needed to run it)."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from .compiler import compile_flow
from .compiler.analysis import Resolver
from .providers import PROVIDERS, split_model
from .spec import FlowSpec, dumps_spec


def export_files(
    spec: FlowSpec, resolve: Resolver | None = None, flow_id: str | None = None
) -> dict[str, str]:
    compiled = compile_flow(spec, resolve=resolve, flow_id=flow_id)
    mod = compiled.module_name
    env_lines = []
    providers = {
        split_model(s.settings.model)[0]
        for s in spec.steps
        if s.type in ("ai_model", "decision")
        and getattr(s.settings, "model", None)
        and (s.type != "decision" or s.settings.mode == "ai")
    }
    for pid in sorted(p for p in providers if p):
        provider = PROVIDERS.get(pid)
        if provider and provider.key_env:
            env_lines.append(f"{provider.key_env}=")
        if provider and provider.host_env:
            env_lines.append(f"# {provider.host_env}=http://localhost:11434")
    from .compiler.templates import secrets

    secret_names: set[str] = set()
    for step in spec.steps:
        if step.type == "http_request":
            for text in [step.settings.url, step.settings.body, *step.settings.headers.values()]:
                secret_names.update(secrets(text))
    env_lines += [f"{name}=" for name in sorted(secret_names)]

    run_hint = (
        f"python {mod}.py"
        if compiled.chat
        else f"python {mod}.py '{json.dumps(compiled.example_input)}'"
    )
    readme = f"""# {spec.name}

{spec.description or "A flow built with Easy Chain."}

This folder was exported from Easy Chain. It is plain LangChain + LangGraph code:
nothing from Easy Chain is needed to run it.

## Run it

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in your keys, and export them (or use direnv)
{run_hint}
```

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
        ".env.example": "\n".join(env_lines) + ("\n" if env_lines else ""),
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
        path.write_text(content, encoding="utf-8")
        written.append(path)
    return written
