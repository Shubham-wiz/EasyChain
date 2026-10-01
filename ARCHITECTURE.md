# Easy Chain architecture

Easy Chain is a visual layer on top of LangChain and LangGraph. It does not reimplement an
agent or graph engine. It turns a diagram into a **flow spec**, compiles that spec into
**standard LangGraph Python**, and runs it. Easy Chain's own code is the canvas, the step
catalog, the compiler, and the platform around them.

This document describes what exists now (Phases 0 and 1), how it is meant to grow, and where it
deliberately differs from the build prompt, with the reason for each difference.

## 1. The big picture

```
┌──────────────────────── apps/web (React + TypeScript) ─────────────────────────┐
│ Step library │ Canvas (React Flow) │ Inspector forms │ Run panel / chat │ Export │
│        Zustand stores: flow (+ undo/redo), checks, run, ui, catalog            │
└───────────────────────────────┬─────────────────────────────────────────────────┘
                                │ REST + Server-Sent Events (/api/…)
┌───────────────────────────────▼──────────── python/src/easychain ──────────────┐
│ server/   FastAPI app: flows, catalog, check, compile, export, runs, secrets    │
│ spec/     Pydantic models of the flow spec, YAML I/O, JSON Schema               │
│ steps/    one handler per step type: form, reads/writes, checks, code emitter   │
│ compiler/ analysis → validate → codegen  ⇒  LangGraph Python source             │
│ runtime/  load the generated module, model gateway, event stream, errors        │
│ cli.py    new · validate · compile · export · run · test · schema · dev         │
└───────────────────────────────┬─────────────────────────────────────────────────┘
                                │ the generated module imports only:
                    langgraph · langchain · langchain-core · provider packages · httpx
```

One process serves everything in Phases 0 and 1: `easychain dev` runs the API and serves the
built web app. `docker compose up` runs the same image.

## 2. The flow spec

The flow spec is the single source of truth. The canvas edits it, the compiler reads it, and it
is saved as YAML (`*.flow.yaml`). Reference: [docs/flow-spec.md](docs/flow-spec.md). Published
schema: [spec/flow.schema.json](spec/flow.schema.json), generated from the Pydantic models in
`python/src/easychain/spec/models.py`.

```yaml
version: 1
name: Summarise a URL
description: Fetches a web page and summarises it in three bullet points.
data: []            # optional declared Flow Data fields (type + update rule)
steps:
- id: fetch_page    # also the LangGraph node name
  type: http_request
  name: Fetch the page
  settings: {url: '{url}', save_as: page}
connections:
- {from: input, to: fetch_page}
- {from: is_long, exit: Long, to: detailed}   # Decision exits are labelled
canvas:             # positions, notes, viewport: visual only, kept last for clean diffs
  steps: {fetch_page: {x: 260, y: 120}}
```

Design rules:

- **Readable and diff-friendly.** Keys are written in a stable order, multi-line text uses YAML
  block scalars, and settings equal to their default are left out. All visual data lives in the
  `canvas` section at the end, so moving a box never touches the lines above it.
- **Defaults are part of the spec version.** Because defaults are left out of files, changing a
  default means bumping `version`. The spec is versioned independently of LangChain, so saved
  flows survive LangChain upgrades: only the compiler changes.
- **Typed per step.** `steps` is a discriminated union on `type`, so each step's `settings` is
  validated strictly (unknown settings are rejected with "this setting is not recognised").
- **Identifiers.** Step ids and field names match `^[a-z][a-z0-9_]*$` because they become Python
  names. A step id must differ from every Flow Data field, because LangGraph does not allow a
  node to share a name with a state key. The checks report this, and the UI avoids it when
  naming new steps.
- **Readable errors.** A bad file reports `step 'ask' › settings › temperature: Input should be
  less than or equal to 2`, not a Pydantic stack trace.

## 3. Step types

Each step type is one `StepHandler` subclass (`python/src/easychain/steps/`), and the registry in
`steps/__init__.py` lists them. A handler provides:

| Method | Used for |
|---|---|
| `catalog()` and `form` | The Step library entry and the inspector form: label, plain-language summary, LangChain term (shown in Pro mode), icon, category, and the form fields with help, examples and advanced/pro flags. The web app has no hard-coded forms. |
| `reads()` and `writes()` | Data flow: which Flow Data fields the step uses and sets, and their types. |
| `primary_output()` | The field "the previous step saved", used as the default input of AI Model and AI Decision steps. |
| `check()` | Step-specific problems, in plain words, with optional one-click `Fix`es. |
| `emit()` | The LangGraph code for the step. |

Adding a step type means one handler plus one settings model; nothing in the web app changes.
Phase 6's custom module registry builds on this interface.

## 4. The compiler

`compile_flow(spec)` runs three stages:

1. **Analysis** (`compiler/analysis.py`): graph shape (outgoing and incoming connections, BFS
   order from Input, ancestors, loops), Flow Data inference, and per-step reads and writes.
   Fields come from declared `data`, Input fields (chat adds `messages` with an append rule),
   and each step's writes. Conflicting types are reported. The validator, the compiler and the
   web app's Flow Data panel all share this one answer.
2. **Checks before a run** (`compiler/validate.py` plus each handler's `check`): structure (one
   Input, an Output, something between them), connections (into Input, out of Output,
   self-loops, Decision exits), data flow (each `{variable}` or field read must be set by an
   upstream step or Input, with difflib suggestions such as "Did you mean `page`?"),
   unreachable steps, loops (no exit means an error; no guard means a warning), and settings. The
   server adds checks that depend on the machine: missing API keys and secrets.
3. **Code generation** (`compiler/codegen.py`), which emits one module:

```python
"""Summarise a URL …"""                       # docstring: what it is, how to run it
import …                                       # stdlib, then third party (isort order)
class FlowData(TypedDict, total=False): …      # state; update rules become Annotated reducers
class FlowInput / FlowOutput(TypedDict): …     # input/output schemas from Input/Output steps
def fill(…) / readable_text(…) / pick_exit(…)  # small helpers, only when used
def fetch_page(data: FlowData) -> dict: …      # one function per step, with a docstring
def build_graph(checkpointer=None):            # StateGraph wiring
    builder.add_edge(START, "fetch_page") …
graph = build_graph()                          # for `langgraph dev` / imports
if __name__ == "__main__": …                   # run from the terminal (chat REPL for chat flows)
```

How the concepts map to LangGraph:

| Easy Chain | Generated code |
|---|---|
| Flow Data field with update rule | `TypedDict` key; `append` becomes `operator.add` (or `add_messages` for messages), `add` becomes `operator.add`, and `merge` becomes a `merge_dicts` reducer |
| Input / Output | `START` edges plus `input_schema` / `output_schema`. They are not nodes, so no empty nodes appear in exported code. |
| Instructions | `ChatPromptTemplate.from_messages(...)`. Literal braces are escaped automatically, so JSON in a prompt just works. History uses `MessagesPlaceholder`. |
| AI Model | `init_chat_model("provider:model", **settings).invoke(data[prompt])` |
| Web request | `httpx.request(...)`. URL values are percent-encoded, JSON bodies are filled with JSON-escaped values, `{secret:NAME}` becomes `os.environ`, and HTML can be reduced to readable text. |
| Code | The user's `run(data)` function, renamed to the step id, with its imports hoisted |
| Decision | A node plus `add_conditional_edges(node, route_fn, {exit: target})`. Rules compile to readable `if` statements; Pro expressions are checked against a safe subset of Python. |

Quality gates: golden tests pin the exact output for every template and edge-case flow
(`tests/golden/`); every generated module must compile and pass `ruff check --select F,E9,B,I`
(no undefined names, no unused imports, sorted imports); compiler coverage is at least 90% (it is
95% today).

## 5. Running a flow

The runtime (`runtime/`) executes **the exact source the compiler produced**, so what runs is
what exports:

1. `compile_cached(spec)` compiles once per distinct spec, then `load_graph(source)` executes the
   module under a unique name and caches it.
2. The only change to the loaded module: its `init_chat_model` name points at the **model
   gateway**. The gateway checks for the provider's API key and raises `MissingAPIKey`, which
   becomes "Add your API key" on the step, or returns the **stand-in AI** when the run asks for it.
   Exported code keeps LangChain's own `init_chat_model`.
3. `stream_run()` calls `graph.astream(stream_mode=["tasks", "messages", "values"])` with a usage
   callback, and turns that into a small event protocol: `run_started`, `step_started`
   (with the fields the step reads), `token`, `step_finished` (with output, duration, tokens,
   cost and model), `route` (the exit a Decision took), `step_failed`, and `run_finished`.
4. Exceptions become plain-language explanations with fixes (`runtime/errors.py`): missing or bad
   key, rate limit, unknown model, provider outage, Ollama not running, HTTP 4xx/5xx with a hint
   per status, timeouts, blocked by proxy, invalid URL, not JSON, missing field, too many loop
   steps.
5. Secret values (vault and provider keys) are redacted from every event.

The API streams events over SSE (`POST /api/runs`) and keeps the last 200 runs in memory, so the
UI can show history and Run Replay.

Performance (from `tests/test_performance.py` and `e2e/quality.spec.ts`): Easy Chain adds about
**1 ms per step** on top of LangGraph (budget: 10 ms). Checking and compiling a 300-step flow takes
about **40 ms**. A 300-step flow opens on the canvas in under 1 s and pans at about 60 fps.

## 6. The web app

- **React 19 + TypeScript + Vite**, **React Flow (xyflow) 12** for the canvas, **Tailwind 4**
  with design tokens in `index.css` (light and dark themes, WCAG AA contrast), **Radix**
  primitives in the shadcn/ui style, **Zustand** stores, **zundo** for undo and redo, **Monaco**
  bundled locally (no CDN) and loaded lazily, and **dagre** for auto-layout.
- **State.** `flow` holds the spec plus undo history (typing in one field merges into one undo
  step). `check` holds issues, analysis and compiled snippets, refreshed in the background
  (debounced) after every edit. `run` holds per-step run state built from SSE events, plus chat.
  `ui` holds the selection, Beginner/Pro mode, theme and dialogs. Flows autosave.
- **Canvas performance.** Nodes subscribe to just their own step, run state and issues, so a
  streaming token re-renders one node. Node objects are reused across syncs, positions are
  committed once per drag, and React Flow only renders visible elements beyond 150 steps.
- **Pure editing helpers** (`lib/spec.ts`) implement every edit as `spec → spec` with no
  mutation: add, connect (with a reason when refused), rename, insert before, renaming variables
  for fixes, copy and paste, auto-layout. They are unit tested.

## 7. Security in Phases 0 and 1

- **Secrets** are referenced by name (`{secret:NAME}`, provider `*_API_KEY`). Values are stored
  encrypted with Fernet in `~/.easychain/secrets.enc` (the key is in `secret.key` with mode 0600,
  or in `EASYCHAIN_SECRET_KEY`). They are never returned by the API, never written to flow files
  or exports, and are redacted from run events.
- **Network exposure.** `easychain dev` binds to 127.0.0.1 by default. The Docker image binds to
  0.0.0.0 inside the container, and the compose file publishes it on localhost's port 8000.
- **Expressions** in Decisions are parsed and limited to comparisons, boolean logic, arithmetic and
  a list of safe functions and methods. Dunder attributes, imports, calls by keyword and
  comprehensions are rejected.
- **Code steps run in-process with no sandbox** in this phase. See the deviations below.

## 8. Testing

| Suite | What it proves |
|---|---|
| `python/tests/test_compiler_golden.py` | Each flow compiles to exactly the expected code, which is lint-clean |
| `test_compiler_run.py` | Compiled flows behave as drawn: routes, loops, reducers, chat memory, HTTP templating |
| `test_validate.py` | Every check fires, is pinned to the right step, and offers the right fix |
| `test_runtime.py` | Event protocol, stand-in AI, gateway, error explanations, redaction |
| `test_server.py` | REST, SSE runs, write-only encrypted secrets, static web serving |
| `test_cli_and_export.py` | CLI commands, and **exported code runs unchanged** in a subprocess with `easychain` hidden |
| `test_templates.py` | Every template passes its Test Set (10+ cases each) |
| `test_performance.py` | Overhead under 10 ms per step; 300-step flows check and compile quickly |
| `apps/web/src/**/*.test.ts` | Editing helpers, undo grouping, SSE parsing, formatting |
| `apps/web/e2e/*.spec.ts` | Playwright: build → run → debug → export (and run the export), templates and chat, fixes, undo, copy/paste, Pro mode, settings, axe WCAG 2.2 AA scan, 300-step canvas |

Tests never call a paid API. `easychain.testing.fake_openai` is a small OpenAI-compatible server
that the real `langchain-openai` package talks to unchanged. It also serves sample pages.

## 9. Version policy

LangChain, LangGraph and provider packages are pinned exactly in `python/pyproject.toml`
(`langgraph==1.2.12`, `langchain==1.4.3`, `langchain-core==1.6.6`, `langchain-openai==1.6.7`,
`langchain-anthropic==1.7.5`, `langchain-ollama==1.1.0`) and locked in `uv.lock`. Exported
`requirements.txt` files use the same pins. To upgrade: bump the pins, run `make test`
(golden + template Test Sets), review golden diffs, then ship.

## 10. Deviations from the build prompt, and why

| Prompt says | Phases 0 and 1 do | Why |
|---|---|---|
| Postgres for flows, versions, checkpoints; Redis queue; workers | Flows are YAML files in a workspace folder; runs and Save Points live in memory (`InMemorySaver`); runs execute in the API process | Phase 0 asks for "in-memory Save Points", and Phase 2 is where durability, the queue and workers land. Files keep flows git-friendly. Docker Compose has only the app service until Postgres has a job to do. |
| Show ARCHITECTURE.md, schema and plan before writing feature code; one phase at a time | Phases 0 and 1 were built in one go, then reported together ([phase report](docs/phases/phase-0-1.md)) | The request was "take this and build, do testing and all". Work stopped at the end of Phase 1 for review, as the prompt asks, instead of starting Phase 2. |
| Code modules run in a sandbox | Code steps run in the server process | Sandboxing (gVisor or microVMs) is Phase 4. Mitigations: localhost-only by default, a single-user local vault, and this is documented as a known gap. Do not expose a Phase 1 server to untrusted users. |
| Decision = conditional edge | Decision = a node (no-op for rules; the classifier for AI mode) plus a conditional edge | Keeps one node per step, so the trace, glow and errors map 1:1 to canvas boxes, and AI classification tokens are attributed to the Decision. The routing itself is still a standard conditional edge. |
| Models defined once | `init_chat_model(...)` is called inside each AI step function | A missing key then surfaces as that step's error rather than an import failure, and the runtime can swap in the gateway by replacing one module-level name. The overhead is negligible next to a model call. |
| (not in prompt) | A clearly labelled **stand-in AI** | Lets people, templates ("Try it") and tests run any flow with no key. It answers from the prompt itself and never pretends to be a real model. |
| Secrets in a KMS/Vault-backed vault | A Fernet-encrypted local file, with values placed in the server's environment | Single-user local install for now. Per-workspace vaults, roles and an audit log are Phase 5. |
| LangSmith or OpenTelemetry tracing | Easy Chain's own event stream and in-memory run history | Phase 5 adds OTel export and LangSmith. The event protocol is designed to map onto spans. |
| shadcn/ui | shadcn-style components written directly on Radix (`components/ui.tsx`) | Same look and accessibility, without the shadcn CLI or copying dozens of unused components. |
| CLI: new, dev, run, test, eval, deploy, export | new, dev, run, test (minimal Test Sets), export, plus validate, compile, schema, templates | `eval` and `deploy` belong to Phase 5. `test` exists now so templates can ship with their Test Sets. |
| Python 3.12+ | Python 3.12 (CI and Docker) | As specified. |
| Latest framework majors | TypeScript 5.9, Vite 7, Vitest 3 (not TS 7 or Vite 8) | "Prefer well-known, boring libraries." Those newer majors were brand new at build time. |
| Templates with 10-case Test Sets (section 14) | The four Phase 1 templates ship with Test Sets of 10 to 12 cases; section 14's templates arrive with the phases that provide their features | The section 14 templates need Knowledge Base, Agents, Autopilot and so on. |
