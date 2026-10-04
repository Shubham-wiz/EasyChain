# Easy Chain architecture

Easy Chain is a visual layer on top of LangChain and LangGraph. It does not reimplement an
agent or graph engine. It turns a diagram into a **flow spec**, compiles that spec into
**standard LangGraph Python**, and runs it. Easy Chain's own code is the canvas, the step
catalog, the compiler, and the platform around them.

This document describes what exists now (Phases 0 to 3), how it is meant to grow, and where it
deliberately differs from the build prompt, with the reason for each difference. How runs,
workers, the Inbox and triggers behave is described for users in [docs/runs.md](docs/runs.md);
agents and tools in [docs/agents.md](docs/agents.md); Knowledge Bases in
[docs/knowledge.md](docs/knowledge.md).

## 1. The big picture

```
┌──────────────────────── apps/web (React + TypeScript) ─────────────────────────┐
│ Step library │ Canvas │ Inspector │ Run panel, Save Points │ Inbox │ Knowledge  │
│        Zustand stores: flow (+ undo/redo), checks, run, ui, catalog            │
└───────────────────────────────┬─────────────────────────────────────────────────┘
                                │ REST · Server-Sent Events · WebSocket (/api/…)
┌───────────────────────────────▼──────────── python/src/easychain ──────────────┐
│ server/   FastAPI app, run database (db.py), Hub (start/resume/watch runs),     │
│           worker (queue, leases, crash recovery, Inbox, triggers, notify),      │
│           cron, secrets vault, flow files, Knowledge/MCP/OpenAPI endpoints      │
│ knowledge/ loaders, splitting, embeddings, storage, hybrid search, memory       │
│ integrations/ SQL (read-only), MCP client, OpenAPI → Web request steps         │
│ spec/     Pydantic models of the flow spec, YAML I/O, JSON Schema               │
│ steps/    one handler per step type: form, reads/writes, checks, code emitter   │
│ compiler/ analysis → validate → codegen  ⇒  LangGraph Python source             │
│ runtime/  load the module, resources (checkpointer/store/cache), gateway,       │
│           stream_run (start, resume, continue, fork, breakpoints, cancel)       │
│ cli.py    new · validate · compile · export · run · test · schema · dev · worker│
└──────────────┬────────────────────────────────────────┬────────────────────────┘
               │ the generated module imports only:     │ SQLite or Postgres
   langgraph · langchain · langchain-core ·              │ runs, events, jobs, Inbox, triggers,
   provider packages · httpx (+ sqlalchemy, numpy,       │ versions, settings + LangGraph Save Points
   langchain-mcp-adapters when a flow uses them)         │ + Knowledge Bases (pgvector when present)

packages/client: @easychain/client, a TypeScript client and React hook for the runs API.
```

`easychain dev` runs the API, serves the built web app and runs a worker in the same process
(SQLite by default). `docker compose up` runs Postgres, the API (which only queues runs) and a
worker that can be scaled out.

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
| Decision | A node plus `add_conditional_edges(node, route_fn, {exit: target})`. Rules compile to readable `if` statements; Pro expressions are checked against a safe subset of Python. A round limit adds a private counter field (reset every run) that the node increments and the router checks first. |
| Ask a Human | A node that calls `interrupt({step, kind, question, show, …})` and turns the answer into the saved field, plus a router for its exits |
| For Each | A dispatch node that resets a private results list (`collect_items` reducer), a router returning one `Send(body, {**data, item, index})` per item, a wrapper around the body that returns `(index, result)`, and a `defer=True` node that sorts the results into the saved list |
| Sub-flow | The child flow is compiled into the same module with a name prefix. Shared data: `add_node(id, child_graph)` (a subgraph node). Separate data: a node that calls `child_graph.invoke({...defaults, mapped inputs})` and maps its results back. |
| Jump | A node returning `Command(update=…, goto=…)`, registered with `destinations=` so the graph still draws |
| Run policy | `RetryPolicy(max_attempts, initial_interval)`, `CachePolicy(ttl)`, `defer=True`, and `timeout=` (the step is wrapped with `in_thread` so LangGraph can stop it) |
| Side effects | `run_once(idempotency_key(), action)`: the key is a hash of the thread id and the task's `checkpoint_ns` (stable across retries and resumes); the result is kept in the LangGraph store; Web requests also send it as `Idempotency-Key` |
| Custom update rule | The user's `combine(old, new)` renamed to `combine_<field>` and used as the field's reducer |

Quality gates: golden tests pin the exact output for every template and edge-case flow
(`tests/golden/`); every generated module must compile and pass `ruff check --select F,E9,B,I`
(no undefined names, no unused imports, sorted imports); compiler coverage is at least 90% (it is
95% today).

## 5. Running a flow

The runtime (`runtime/`) executes **the exact source the compiler produced**, so what runs is
what exports:

1. `compile_cached(spec, resolve)` compiles once per distinct flow (including the flows it uses
   as Sub-flows), then `load_graph(source, resources)` executes the module under a unique name and
   builds the graph with the run's **resources**: a checkpointer (Save Points), a store (side
   effects) and a cache. Resources are in memory for `easychain run` and tests, or LangGraph's
   SQLite/Postgres savers and stores for the server.
2. The only change to the loaded module: its `init_chat_model`, `init_embeddings` and
   `mcp_tools` names point at the **gateway**. The gateway checks for the provider's API key
   (raising `MissingAPIKey`, which becomes "Add your API key" on the step) and its package
   (`MissingPackage`: "… isn't installed, run `pip install 'easychain[providers]'`"), returns the
   **stand-in AI** (which can call tools and fill structured replies) when the run asks for it,
   and hands MCP tool loading the server connections from Settings. Exported code keeps
   LangChain's own functions and reads MCP servers from `EASYCHAIN_MCP_SERVERS`.
3. `stream_run()` starts, **resumes** (`Command(resume=…)`), **continues** (`None` input) or
   **forks** (`update_state` at a checkpoint, then continue) a run, with breakpoints
   (`interrupt_before` / `interrupt_after`), `durability="sync"` on durable resources, and
   `astream(stream_mode=["tasks", "messages", "values", "checkpoints"], subgraphs=True)`. The
   stream is consumed in its own task so a **cancel** can stop it at once. It turns that into the
   event protocol in [docs/runs.md](docs/runs.md#events): steps inside Sub-flows carry a `path`,
   For Each reports per-item events and progress, routes are worked out from routers and Jump
   targets, and every checkpoint becomes a `save_point` event. After the stream, the state tells
   whether the run is waiting (interrupts) or stopped at a breakpoint.
4. Exceptions become plain-language explanations with fixes (`runtime/errors.py`): missing or bad
   key, rate limit, unknown model, provider outage, Ollama not running, HTTP 4xx/5xx with a hint
   per status, timeouts, blocked by proxy, invalid URL, not JSON, missing field, too many rounds.
5. Secret values (vault and provider keys) are redacted from every event.

## 5a. Durable runs: the queue and the workers

- **The run database** (`server/db.py`, SQLAlchemy Core with async drivers) has `runs`,
  `run_events`, `jobs`, `inbox`, `triggers`, `flow_versions` and `settings`. Every run records the
  flow version it ran (the flow and its Sub-flows, deduplicated by hash).
- **The queue** is the `jobs` table. `lease()` picks the oldest job whose conversation has no other
  job running or waiting ahead of it and whose flow is under its run limit, in one
  `UPDATE … WHERE id = (SELECT … LIMIT 1) RETURNING`. On Postgres the inner select uses
  `FOR UPDATE SKIP LOCKED` and a transaction-scoped advisory lock per conversation, so many workers
  can lease at once; on SQLite every write transaction is `BEGIN IMMEDIATE`.
- **Leases** are renewed every few seconds by a heartbeat that also delivers cancel requests. A job
  whose lease ran out is leased again; the worker then compares the conversation's last Save Point
  with the run (each checkpoint carries the run id in its metadata) and resumes, continues, or
  starts over. A worker that is shut down hands its jobs back to the queue at once.
- **Events** are written in batches (tokens every 50 ms) and tailed by the API for SSE and
  WebSocket clients, by polling plus an in-process wake-up when the worker runs in the same
  process. The final event is written only after the run's status, Inbox items and notifications
  are saved, so a client that sees it can rely on them.
- **The Inbox**: a paused run's interrupts become Inbox items; answering one queues a `resume` job
  with `{interrupt_id: answer}`. Notifications (webhook, Slack, SMTP) go out per item.
- **Triggers**: webhooks and uploads are endpoints with a per-trigger token; schedules use a small
  cron parser (`server/cron.py`) and a compare-and-set on `next_fire_at`, so a slot fires once with
  any number of workers; *after another flow* triggers fire when a run of the source flow ends.
- **Double texting** on busy conversations: queue (thread FIFO), reject (HTTP 409), interrupt
  (cancel the running job, then queue) or rollback (cancel, then start the new run from the last
  checkpoint before the cancelled runs).

Performance (from `tests/test_performance.py` and `e2e/quality.spec.ts`): Easy Chain adds about
**1 ms per step** on top of LangGraph (budget: 10 ms). Checking and compiling a 300-step flow takes
about **40 ms**. A 300-step flow opens on the canvas in under 1 s and pans at about 60 fps.

## 5b. Agents, tools and Knowledge Bases (Phase 3)

- **Agent step.** The node builds `create_agent(model, tools, system_prompt, middleware,
  response_format=ToolStrategy(Answer))` and invokes it inside the step. LangGraph runs it as a
  subgraph of the step's node, so it shares the flow's checkpointer and store: an approval
  (`HumanInTheLoopMiddleware`) is an ordinary interrupt, becomes an Inbox item of kind
  `approve_tool`, and an answer resumes it with `{"decisions": [...]}`. Add-ons map one-to-one
  onto LangChain middleware.
- **Steps as tools.** A step listed in an agent's `tools` is not wired into the graph. The
  compiler emits a factory that wraps the step's own node function in `@tool(step_id,
  description=…)`; its arguments are the fields the step reads that the agent can't see, typed
  from Flow Data. Each call is its own `Send` task, so per-call idempotency keys stay unique and
  the step's run policy still applies.
- **Tool events.** The runner turns the agent subgraph's stream (`agent:<task>` namespaces,
  `model` and `tools` nodes) into `tool_started` / `tool_finished` events with arguments,
  results and timing, and attributes all model usage to the Agent step.
- **Structured output.** Fixed fields compile to Pydantic models (`schema_code.py`), used with
  `with_structured_output` plus a retry on validation errors (AI Model) or `ToolStrategy`
  (Agent). "Also save each field on its own" spreads the fields into Flow Data.
- **Knowledge Bases.** Own tables (`kb_bases`, `kb_documents`, `kb_chunks`) in the run database
  or `EASYCHAIN_KNOWLEDGE_URL`: pgvector with a per-Knowledge-Base partial HNSW index on Postgres
  when the extension is available, float32 blobs plus numpy otherwise; full-text search with
  `tsvector` (Postgres) or FTS5 (SQLite); hybrid results merged by reciprocal rank fusion.
  Documents are loaded (pypdf, python-docx, an HTML text extractor, Markdown by headings, CSV),
  split with `RecursiveCharacterTextSplitter`, embedded with `init_embeddings` and written in the
  API server's background tasks. The search functions are copied into exported code with
  `inspect.getsource`, so the export searches exactly as Easy Chain does.
- **Memory** uses the LangGraph store (namespace per user); trim and summarise operate on a
  messages field.
- **MCP** uses `langchain-mcp-adapters` (`MultiServerMCPClient`): streamable HTTP and SSE, and
  stdio for commands on an allow-list.
- **The stand-in AI** (`runtime/standin.py`) picks tools by keyword overlap with their
  descriptions, fills arguments from the question, answers from tool results with `[n]`
  citations, and fills JSON schemas. Test Sets can **script** its turns instead.

## 6. The web app

- **React 19 + TypeScript + Vite**, **React Flow (xyflow) 12** for the canvas, **Tailwind 4**
  with design tokens in `index.css` (light and dark themes, WCAG AA contrast), **Radix**
  primitives in the shadcn/ui style, **Zustand** stores, **zundo** for undo and redo, **Monaco**
  bundled locally (no CDN) and loaded lazily, and **dagre** for auto-layout.
- **State.** `flow` holds the spec plus undo history (typing in one field merges into one undo
  step). `check` holds issues, analysis and compiled snippets, refreshed in the background
  (debounced) after every edit. `run` holds per-step run state built from events (including
  waiting steps, For Each progress, Sub-flow detail and Save Points), plus chat. `ui` holds the
  selection, Beginner/Pro mode, theme, dialogs and breakpoints (kept per flow in the browser).
  Flows autosave; every save records a flow version.
- **Runs outlive the page.** A run is a server-side job; the run panel follows its events and
  re-attaches after a reload (`GET /api/runs/{id}/events?after=…`).
- **Canvas performance.** Nodes subscribe to just their own step, run state and issues, so a
  streaming token re-renders one node. Node objects are reused across syncs, positions are
  committed once per drag, and React Flow only renders visible elements beyond 150 steps.
- **Pure editing helpers** (`lib/spec.ts`) implement every edit as `spec → spec` with no
  mutation: add, connect (with a reason when refused), rename, insert before, renaming variables
  for fixes, copy and paste, auto-layout. They are unit tested.

## 7. Security in Phases 0 to 3

- **Secrets** are referenced by name (`{secret:NAME}`, provider `*_API_KEY`). Values are stored
  encrypted with Fernet in `~/.easychain/secrets.enc` (the key is in `secret.key` with mode 0600,
  or in `EASYCHAIN_SECRET_KEY`). They are never returned by the API, never written to flow files
  or exports, and are redacted from run events.
- **Network exposure.** `easychain dev` binds to 127.0.0.1 by default. The Docker image binds to
  0.0.0.0 inside the container, and the compose file publishes it on localhost's port 8000.
- **Other sites' pages** (`server/hostguard.py`). The API answers only to known host names
  (`localhost`, `127.0.0.1`, `::1`, `*.localhost`, plus `EASYCHAIN_ALLOWED_HOSTS` and the host of
  `EASYCHAIN_PUBLIC_URL`), which stops DNS rebinding. Requests that change things (and
  WebSockets) carrying another site's `Origin` are refused, which stops cross-site form posts.
  Trigger addresses (`/api/hooks/…`) are exempt: they check their own token.
- **Secrets vault.** If `secrets.enc` can't be decrypted with the current key, the vault refuses
  to write rather than replace it, and says how to recover.
- **Expressions** in Decisions are parsed and limited to comparisons, boolean logic, arithmetic and
  a list of safe functions and methods. Dunder attributes, imports, calls by keyword and
  comprehensions are rejected.
- **Triggers** are reached only with their own random token (header or query); a wrong or
  missing token gets a 404. Uploaded file names are reduced to a safe base name and stored in a
  fresh folder per upload.
- **Ask a Human questions** can't contain `{secret:…}` placeholders, so reviewers never see secrets.
- **Code steps run in the worker process with no sandbox** in this phase. See the deviations below.
- **Agents** can only call the tools you gave them. Steps that change things keep "send at most
  once", and **Ask a person before …** makes a person approve each call. The check before a run
  warns when an agent's Database query can write.
- **Database queries** are read-only by default: a read-only transaction (`SET TRANSACTION READ
  ONLY` on Postgres, `PRAGMA query_only` on SQLite) plus a statement check. Flow Data goes in as
  bound parameters, never into the SQL text. Passwords in connection URLs are flagged; use
  `{secret:NAME}`.
- **MCP servers** started as local commands (stdio) run only if the command is on an
  allow-list that can be edited in Pro mode. Tokens go in headers as `{secret:NAME}`.
- **Fetching on the server.** Adding a web page to a Knowledge Base and reading an OpenAPI
  address make the server fetch that URL. That is fine for a single-user local install; a
  shared deployment needs an egress allow-list (Phase 5).

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
| `test_phase2_runtime.py` | For Each order and concurrency, Ask a Human (edit, choose, answer), Jump, round limits, Sub-flows (shared, separate, per item, nested pauses), retries with one Idempotency-Key, timeouts, cache, breakpoints, fork, cancel |
| `test_validate_phase2.py` | The checks for every new step and setting |
| `test_api_phase2.py` | Inbox answers and notifications (webhook, Slack, SMTP), cancel, continue, Save Points and fork, double texting (queue, reject, interrupt, rollback), triggers (webhook, upload, schedule, after-flow), run limits, versions, `Last-Event-ID`, WebSocket |
| `test_durability.py` | **The Phase 2 Done-when**, on SQLite and Postgres: a worker killed with SIGKILL mid-run resumes from the last step without repeating finished steps or side effects; a paused approval is answered a day later through a fresh server and worker |
| `test_db_startup.py` | Several processes starting at once set up the Postgres tables without racing |
| `apps/web/src/**/*.test.ts` | Editing helpers, undo grouping, SSE parsing, formatting |
| `apps/web/e2e/*.spec.ts` | Playwright: build → run → debug → export (and run the export), templates and chat, fixes, undo, copy/paste, Pro mode, settings, Ask a Human in the run panel and the Inbox, For Each, breakpoints and Save Points, stop and carry on, reload during a run, triggers, the Flow Data panel, Sub-flows, agent tools and live tool calls, tool approval, the reply format builder, Knowledge Bases, cited answers, MCP servers, API import, axe WCAG 2.2 AA scans, 300-step canvas |
| `test_standin_tools.py` | The stand-in AI calls tools, fills arguments and JSON schemas, cites sources, and plays scripts |
| `test_agent.py` | Agent tool calls and their events, approvals (approve, edit, reject) through the Inbox shape, structured answers, add-ons, limits, MCP tools |
| `test_knowledge.py` | Every file format, chunk provenance, hybrid search and citations on SQLite and on Postgres with pgvector, the API, an agent searching with its own queries, documents cut off by a restart, exported code searching on its own |
| `test_integrations.py` | Memory (remember, recall, trim, summarise), read-only SQL, MCP over stdio and HTTP and its allow-list, OpenAPI import |
| `packages/client/src/*.test.ts(x)` | The client's SSE handling, errors, reconnecting `wait()`, and the React hook through a pause and an answer |

Tests never call a paid API. `easychain.testing.fake_openai` is a small OpenAI-compatible server
that the real `langchain-openai` package talks to unchanged: chat (with tool calls and JSON
schema replies, scriptable through `POST /__script`) and embeddings. It also serves sample pages
and records side effects. `easychain.testing.mcp_server` is a small MCP server (stdio or HTTP).

## 9. Version policy

LangChain, LangGraph and provider packages are pinned exactly in `python/pyproject.toml`
(`langgraph==1.2.12`, `langchain==1.4.3`, `langchain-core==1.6.6`, `langchain-openai==1.6.7`,
`langchain-anthropic==1.7.5`, `langchain-ollama==1.1.0`, `langchain-text-splitters==1.1.2`,
`langchain-mcp-adapters==0.3.2`, and the provider packages in the `providers` and `vertex`
extras) and locked in `uv.lock`. Exported
`requirements.txt` files use the same pins. To upgrade: bump the pins, run `pnpm test`
(golden + template Test Sets), review golden diffs, then ship.

## 10. Deviations from the build prompt, and why

| Prompt says | Easy Chain does | Why |
|---|---|---|
| Postgres for flows, versions, checkpoints; Redis queue; workers | Postgres (or SQLite locally) for runs, events, jobs, versions and Save Points; flows stay YAML files in a workspace folder; the queue is a database table; workers are `easychain worker` processes | Files keep flows git-friendly, and every run still records the exact version it ran. A table queue (`SKIP LOCKED` + advisory locks) needs no extra service; Redis Streams can come later if load needs it. |
| SQLAlchemy + Alembic | SQLAlchemy Core with `create_all` and a schema version check | There is one schema version so far; Alembic migrations arrive with the first schema change. Startup takes an advisory lock so several processes can start together. |
| Code modules run in a sandbox | Code steps run in the worker process | Sandboxing (gVisor or microVMs) is Phase 4. Mitigations: localhost-only by default, a single-user local vault, and workers can run in their own containers (Docker Compose does this), which separates them from the API but not from each other's runs. Do not expose the server to untrusted users. |
| Cancel stops a run | Cancel stops the run at once and keeps its Save Points, but a Code step already running in a thread finishes in the background (its result is dropped) | Python can't kill a thread. Time limits on steps bound this. |
| Show ARCHITECTURE.md, schema and plan before writing feature code; one phase at a time | Phases 0 and 1 were built in one go and reported together ([report](docs/phases/phase-0-1.md)); Phases 2 and 3 followed, each after its report ([Phase 2](docs/phases/phase-2.md), [Phase 3](docs/phases/phase-3.md)) | The request was "take this and build, do testing and all", and each phase's questions were answered with "whatever you think is right" or "continue". |
| Decision = conditional edge | Decision = a node (no-op for rules; the classifier for AI mode) plus a conditional edge | Keeps one node per step, so the trace, glow and errors map 1:1 to canvas boxes, and AI classification tokens are attributed to the Decision. The routing itself is still a standard conditional edge. |
| Models defined once | `init_chat_model(...)` is called inside each AI step function | A missing key then surfaces as that step's error rather than an import failure, and the runtime can swap in the gateway by replacing one module-level name. The overhead is negligible next to a model call. |
| (not in prompt) | A clearly labelled **stand-in AI** | Lets people, templates ("Try it") and tests run any flow with no key. It answers from the prompt itself and never pretends to be a real model. |
| Secrets in a KMS/Vault-backed vault | A Fernet-encrypted local file, with values placed in the server's environment | Single-user local install for now. Per-workspace vaults, roles and an audit log are Phase 5. |
| LangSmith or OpenTelemetry tracing | Easy Chain's own event stream, stored with each run | Phase 5 adds OTel export and LangSmith. The event protocol is designed to map onto spans. |
| shadcn/ui | shadcn-style components written directly on Radix (`components/ui.tsx`) | Same look and accessibility, without the shadcn CLI or copying dozens of unused components. |
| CLI: new, dev, run, test, eval, deploy, export | new, dev, run (answers Ask a Human in the terminal), test (Test Sets, with scripted human answers), export, plus validate, compile, schema, templates and worker | `eval` and `deploy` belong to Phase 5. `test` exists now so templates can ship with their Test Sets. |
| Python 3.12+ | Python 3.12 (CI and Docker) | As specified. |
| Latest framework majors | TypeScript 5.9, Vite 7, Vitest 3 (not TS 7 or Vite 8) | "Prefer well-known, boring libraries." Those newer majors were brand new at build time. |
| Templates with 10-case Test Sets (section 14) | Eight templates ship with Test Sets of 10 to 12 cases (Phase 3 adds *Support bot over docs* and *SQL analyst*); section 14's other templates arrive with the phases that provide their features | They need Autopilot, Helpers, publishing and so on. |
| For Each runs "a step or sub-flow" per item | One step per item; several steps per item go in a Sub-flow | Keeps the canvas honest about what runs per item and maps to one `Send` target. |
| Breakpoints | Set per flow in the browser, not saved in the flow file | Breakpoints are a debugging aid for test runs, not part of the flow's behaviour, so they don't belong in reviewed YAML. |
| Event streaming | The API tails the run's events from the database (poll plus an in-process wake-up), not Postgres LISTEN/NOTIFY | Works the same on SQLite and Postgres and survives API restarts; latency is about 50–250 ms. |
| Knowledge Base on pgvector (via LangChain's vector stores) | Easy Chain's own three tables, using pgvector directly when present, numpy otherwise, and Postgres/SQLite full-text search | One schema for SQLite and Postgres, hybrid search (LangChain's PGVector store has no full-text side), per-document status and provenance, and search code small enough to copy into exports. The tables are plain SQL, so moving to another store later is a migration, not a rewrite. |
| Documents ingested by workers | Documents are read and embedded in the API server's background tasks | Ingestion needs the uploaded file, which the API process has; it is not part of a run. A restart marks unfinished documents as failed with a clear message. Moving ingestion onto the job queue is listed for Phase 5. |
| OCR for scanned PDFs | Not supported; such PDFs fail with a message saying so | OCR needs a heavy system dependency (Tesseract) or a paid API. |
| Agent = graph of its own | The agent is built and invoked inside the step's node (a subgraph at run time) | One node per canvas box keeps the trace, glow, errors and token counts 1:1 with the canvas, and the agent still checkpoints and pauses with the flow. |
| Test Sets judge agents | Test Sets check tools called, not called and call counts, and can script the model's turns | Deterministic, key-free tests of a flow's logic. LLM-as-judge and trajectory checks are Phase 5. |
