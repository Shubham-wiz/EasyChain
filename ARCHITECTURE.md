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
   and hands MCP tool loading the server connections from Settings. On a server, a run's
   `GatewayPolicy` (`runtime/model_policy.py`, Phase 5.5) adds LangChain's own pieces to each
   chat model it builds: a `BaseCache` (answers in the run database), an `InMemoryRateLimiter`
   (smoothing), the provider's `max_retries`, a `RunnableWithFallbacks` inside a chat model
   that keeps each model's callbacks (fallbacks), and a callback that checks budgets before a
   call and records it in `model_calls` after (budgets, alerts, spending). Exported code keeps
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
  WebSocket clients. In one process the EventBus wakes the follower at once; between processes,
  on Postgres, writing events and queueing or finishing jobs also sends a `NOTIFY`
  (`Database._wake`), and each process keeps one `LISTEN` connection (`server/listen.py`) that
  turns it into the same wake-up: a follower in another process sees new events in about 2 ms
  (median, measured in `tests/test_listen.py`), and a worker picks up a job queued by the API at
  once. Polling (0.25 s for events, 0.5 s for jobs) stays as the fallback, and is all SQLite
  has. The final event is written only after the run's status, Inbox items and notifications
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
  split with `RecursiveCharacterTextSplitter`, embedded with `init_embeddings` and written by a
  **worker**, from the job queue (since Phase 5.8): adding a document queues an `ingest` job
  whose `run_id` is the document's id and whose thread is `kb:<id>`, so one Knowledge Base's
  documents are read one at a time. An uploaded file waits in the `uploads` table until it has
  been read, so a worker needs no disk shared with the API server. A job taken over after a
  crash replaces the chunks the first try wrote. The search functions are copied into exported code with
  `inspect.getsource`, so the export searches exactly as Easy Chain does.
- **Memory** uses the LangGraph store (namespace per user); trim and summarise operate on a
  messages field.
- **MCP** uses `langchain-mcp-adapters` (`MultiServerMCPClient`): streamable HTTP and SSE, and
  stdio for commands on an allow-list.
- **The stand-in AI** (`runtime/standin.py`) picks tools by keyword overlap with their
  descriptions, fills arguments from the question, answers from tool results with `[n]`
  citations, and fills JSON schemas. Test Sets can **script** its turns instead.
- **Deep Agent step** (Phase 4, `steps/deep_agent.py`). The node builds `deepagents.
  create_deep_agent(model, tools, system_prompt, middleware=[TodoListMiddleware(),
  FilesystemMiddleware(tools=FILE_TOOLS), *addons], subagents=[general_helper(...),
  <helper>_helper(data), ...])`. `FILE_TOOLS` is every file tool except `execute` (there is no
  shell). The agent's files live in its own graph state (deepagents' `StateBackend`), so they are
  checkpointed with the flow: a worker crash resumes inside the agent with its files, and time
  travel rewinds them. The node passes the Flow Data `files` field (`{path: text}`) in and saves
  it back. Approvals may name the built-in tools (`write_file`, `task`, …).
- **Helpers** are steps of type `helper` with no node. Each compiles to a function returning a
  deepagents `SubAgent` spec (own model, prompt, tools, add-on middleware, the same file tools).
  deepagents always adds a general-purpose helper; Easy Chain passes its own, with the Deep
  Agent's tools and add-ons, so approvals and limits can't be bypassed through it.
- **Skills** (`skills.py`): Agent Skills folders (`SKILL.md` with name and description, plus
  files) in `<workspace>/skills/`, over built-in ones in `templates/skills/`. The process's
  library is set by the server, the worker and the CLI (next to the flow file). The compiler puts
  the skill files of a Deep Agent and its Helpers into a constant in the generated code
  (`/skills/<step>/<skill>/…`), passes them in with the agent's files, and points
  `create_deep_agent(skills=[...])` and each Helper's `skills` at them; deepagents'
  `SkillsMiddleware` shows the descriptions and the agent reads a skill with `read_file`. The
  skills' content is part of the compile cache key; `flow_files` keeps `/skills/` out of Flow
  Data.
- **Agent team patterns** (Phase 4.5, [docs/patterns.md](docs/patterns.md)): supervisor,
  handoffs and plan-and-execute are templates made of ordinary steps (an AI Decision in a loop
  with a round limit; Agents sharing the chat with a Jump that records the active desk; a
  structured plan, a For Each and a write-up). The step library inserts a pattern's steps into
  the open flow (`insertPattern`, from `GET /api/templates/{id}`). Two small changes made them
  possible: an AI Decision's guidance takes `{field}` placeholders, and in a chat a field a
  later step sets counts as available (it carries over from the previous turn).
- **The describe-it copilot** (Phase 4.6, `copilot.py`, [docs/copilot.md](docs/copilot.md)).
  The model gets the step catalog (each type's settings, from the same models the editor
  uses), the spec's rules, one example flow and the open flow as YAML, and writes the **whole**
  flow back as YAML with a summary and Test Set cases. Easy Chain reads it with `parse_spec`,
  runs `validate`, and sends errors back (twice at most), so a proposal is shown only when it
  has none. Writing the whole flow, rather than a list of edits, keeps the model's job to the
  format it is told and lets the checks see the result; the diff (`diff()`) then turns it into
  separate changes with plain labels, which the editor previews and applies with one `apply()`
  (one undo step). Steps that were there keep their positions; new ones go right of the step
  that leads into them and later steps move right (`place_new_steps`, a `move_steps` change).
  The model is any one the person has a key for (`copilot_model()`); with none, the stand-in
  shows the closest template as a proposal.
- **Helper events.** A Helper's graph runs inside the Deep Agent's `tools` task
  (`lead:<id>|tools:<id>`). The runner records which `task` call started each such task and
  tags the Helper's tool events with `helper` and `within` (the task's call id). It adds the
  Helper's model usage to that task's `tool_finished` and to the step's total, and emits `todos`
  and `files` events from the tool results. A Helper's own tokens don't stream into the answer.

## 6. The web app

- **React 19 + TypeScript + Vite**, **React Flow (xyflow) 12** for the canvas, **Tailwind 4**
  with design tokens in `index.css` (light and dark themes, WCAG AA contrast), **Radix**
  primitives in the shadcn/ui style, **Zustand** stores, **zundo** for undo and redo, **Monaco**
  bundled locally (no CDN) and loaded lazily, and **dagre** for auto-layout.
- **State.** `flow` holds the spec plus undo history (typing in one field merges into one undo
  step). `check` holds issues, analysis and compiled snippets, refreshed in the background
  (debounced) after every edit. `copilot` holds an open proposal, the changes kept, and the preview:
  the canvas and its step cards read the preview while it is open (`useShown`), with editing
  paused. `run` holds per-step run state built from events (including
  waiting steps, For Each progress, Sub-flow detail and Save Points), plus chat. `ui` holds the
  selection, Beginner/Pro mode, theme, dialogs and breakpoints (kept per flow in the browser).
  Flows autosave; every save records a flow version.
- **Sign-in and roles** (`state/auth.ts`). The app asks `GET /api/auth/status` first, and shows
  the first-account or sign-in page when it needs to. A 401 later (the session ran out) shows
  sign-in over the page, so nothing open is lost. `useCan(role)` and `useRoleNote` drive what
  each role sees: a role that can't change flows gets a view-only editor (`flow.readOnly` makes
  every edit a no-op; the canvas can't move or connect; the Inspector is a disabled fieldset; no
  step library or copilot). A Runner's runs send only the flow's id, never its steps. Settings
  show Editors the server's settings without letting them change them. This is only for
  clarity: the server checks every request (section 7).
- **Runs outlive the page.** A run is a server-side job; the run panel follows its events and
  re-attaches after a reload (`GET /api/runs/{id}/events?after=…`).
- **Canvas performance.** Nodes subscribe to just their own step, run state and issues, so a
  streaming token re-renders one node. Node objects are reused across syncs, positions are
  committed once per drag, and React Flow only renders visible elements beyond 150 steps.
- **Pure editing helpers** (`lib/spec.ts`) implement every edit as `spec → spec` with no
  mutation: add, connect (with a reason when refused), rename, insert before, renaming variables
  for fixes, copy and paste, auto-layout. They are unit tested.

## 7. Security

- **Sign-in and roles** (Phase 5.6, `server/auth.py`, `accounts.py`, `auth_api.py`,
  [docs/people.md](docs/people.md)). Sign-in is on or off for the whole server: off when it
  answers only this computer (`easychain dev` on 127.0.0.1), on when it answers the network (the
  Docker image), or as `EASYCHAIN_AUTH` says.
  - Requests are signed in by a session cookie (HttpOnly, SameSite=Lax, 14 days; only its
    SHA-256 is stored) or by an API key (`Authorization: Bearer eck_<id>_<secret>`; only the
    secret's SHA-256 is stored; its role is capped at the one it was made with).
  - Every route is in `PERMISSIONS`, which gives the least role that may use it (Viewer <
    Runner < Editor < Owner). One app-level dependency, `authorize`, checks it for every HTTP
    and WebSocket route, using the matched route's path template. A route missing from the table
    is closed to everyone, and `tests/test_auth.py` fails until it is added.
  - A check that depends on the request lives in the route: running a flow given in full needs an
    Editor, because it can run any code.
  - Passwords use scrypt. Wrong passwords lock that email for 15 minutes after 5 tries (30 per
    address); the count lives in each API process's memory.
  - The first Owner needs a 12-character code printed in the server's log (or made by
    `easychain users setup-code`).
  - The tables (`users`, `workspaces`, `memberships`, `sessions`, `api_keys`) come in migration
    0003.
  - With sign-in off, every request is the Owner (`via: "local"`), as in Phases 0–4.
- **Workspaces** (Phase 5.6d, `server/spaces.py`, `current_workspace.py`,
  [docs/workspaces.md](docs/workspaces.md)).
  - A request names its workspace (`X-EasyChain-Workspace`, or `?workspace=` for WebSockets).
    `authorize` takes the person's role from their membership there; an API key is bound to
    its own. Owners of `default` are the server's Owners (`server_owner`), the only ones for
    routes marked `server_owner` in `PERMISSIONS` (sandbox, network, single sign-on, making
    workspaces).
  - A second app-level dependency, `in_workspace`, then enters the workspace for the rest of
    the request: a context variable (`current_workspace`), the workspace's vault as the
    `scoped_env` layer, and its skills library. The worker enters each job's workspace (the
    run's `workspace_id`, or the ingest job's payload), and each due schedule's.
  - A `Space` is a workspace's files: its `FlowStore`, `SecretStore` and `SkillLibrary`
    (`default` keeps the old folders; others live in `~/.easychain/workspaces/<id>/`).
  - The run database stamps `workspace_id` on flow versions, runs, Inbox items and triggers
    (migration 0006) and lists only the current workspace's. Getting a row by id is checked in
    the route, so another workspace's row is a 404.
  - Settings are per workspace (`mcp@<id>`, `notifications@<id>`, `copilot@<id>`), except
    those every workspace shares. MCP's allowed commands are always the default workspace's.
  - Knowledge Bases carry a `workspace_id` (added by `KnowledgeStore.setup`); the sample one
    is shared (`*`). The loader wraps a flow module's `search_knowledge` so a run searches only
    its workspace's.
  - Long-term memory: the loader gives the graph a `WorkspaceStore`, which puts every namespace
    of a non-default workspace under `("easychain-workspace", <id>)` and takes it off again in
    results.
- **Single sign-on** (Phase 5.6e, `server/sso.py`, [docs/sso.md](docs/sso.md)).
  - OpenID Connect, using the authorization code flow with PKCE (S256).
  - The `state` is bound to the browser by a short-lived cookie. The state's nonce and
    verifier wait in the database, so any API server can finish the sign-in.
  - The ID token is checked against the provider's JWKS (PyJWT, public-key algorithms only),
    with its issuer, audience, expiry and nonce, and the email must be verified.
  - People are matched by `user_identities` (provider, subject), then by email. New people
    get the provider's role, if that's allowed and their domain is.
  - Providers come from Settings or `EASYCHAIN_SSO`. Client secrets are kept in the vault.
  - `easychain.testing.oidc_provider` is the test identity provider used by pytest and
    Playwright.
- **Publishing** (Phase 5.4, `server/publish_api.py`, [docs/publish.md](docs/publish.md)).
  - Published flows are reached without a sign-in, like webhooks. An **access key**
    (`epk_<id>_<secret>`; only the secret's SHA-256 is kept) is tied to one flow and
    environment. A **chat link** has no secret; its page sends `frame-ancestors` with the
    sites listed for it, and the widget only frames that page.
  - Limits per minute are counted in `key_usage` (an upsert per key and minute), shared by
    every API server. A chat link counts per visitor's address too.
  - Runs started this way carry `trigger` `api` or `chat` and the key as `trigger_id`. The
    calls aren't in the audit log (each is a run); publishing, unpublishing and keys are.
- **The model gateway** (Phase 5.5, `server/gateway.py`, [docs/gateway.md](docs/gateway.md)).
  - Budgets are checked from `model_calls`, which every process writes, so they hold across
    workers. Model calls run in worker threads; the meter hands its database work to the
    event loop that made it, and never waits for itself on that loop's own thread.
  - Cached answers are keyed by a hash of the workspace, the model with its settings and the
    messages, so one workspace never gets another's. They are stored as LangChain message
    dicts, never as pickled objects.
  - Alerts are sent once per budget, period and level (`budget_alerts`, an insert that only
    one process wins).
- **Tracing** (Phase 5.7, `server/tracing.py`, [docs/tracing.md](docs/tracing.md)).
  - After each part of a run, the worker turns the part's events and its `model_calls` into
    OpenTelemetry spans in a background task, so a slow or missing collector never holds a run
    up. A `TracerProvider` per collector (with its batch exporter) is shared by the process;
    an id generator takes the trace id from the run id.
  - LangSmith is LangChain's `LangChainTracer`, added to the run's callbacks with a client
    made from the workspace's `LANGSMITH_API_KEY`; the worker waits for it to flush after the
    part, in a thread.
  - Header values (collector keys) are shown hidden and can be `{secret:NAME}`.
- **The audit log** (Phase 5.6c, `server/audit.py`, [docs/audit.md](docs/audit.md)).
  - `AuditMiddleware` writes one row to `audit_log` (migration 0004) when the response to a
    change starts with a success status. The row holds who (the principal `authorize` found),
    the action (from `AUDITED`, keyed by the matched route), the target (the first path
    parameter, or what the route `note`s) and the address.
  - Request bodies are never recorded, so secret values can't leak into the log.
  - Failed sign-ins are recorded by the sign-in routes themselves.
  - Flow saves are recorded at most every 5 minutes per person and flow.
  - Database triggers make the table append-only on SQLite and Postgres.
  - `tests/test_audit.py` fails if a route that changes something is in neither `AUDITED` nor
    `NOT_AUDITED`.
  - The command line's account changes are recorded too.

- **Secrets** are referenced by name (`{secret:NAME}`, provider `*_API_KEY`). Values are stored
  encrypted with Fernet in `~/.easychain/secrets.enc` (the key is in `secret.key` with mode 0600,
  or in `EASYCHAIN_SECRET_KEY`). They are never returned by the API, never written to flow files
  or exports, and are redacted from run events.
  - Since 5.6d they are **not exported into the process's environment**. `scoped_env.py`
    replaces `os.environ` with a view that lays the vault of the work in hand (a context
    variable) over the real environment. So generated code and the providers still read
    `os.environ`, but child processes inherit none of it, and one process can serve several
    workspaces.
  - The vault wins over the server's environment for the same name.
- **Network exposure.** `easychain dev` binds to 127.0.0.1 by default. The Docker image binds to
  0.0.0.0 inside the container (so sign-in is on), and the compose file publishes it on
  localhost's port 8000.
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
- **Code steps run in a sandbox** ([docs/sandbox.md](docs/sandbox.md), `runtime/sandbox.py`):
  each call is a fresh container started with `docker run`. The container has no network
  (unless the step asks for it), a read-only file system, no host files, no Easy Chain
  environment or secrets, and runs as `nobody` with no capabilities. It has memory, CPU,
  process and time limits, and uses gVisor (`runsc`) when Docker has it. The loader swaps each
  Code step's module-level function for a proxy (`sandboxed()`), which asks the run's settings
  at call time, so cached modules serve sandboxed and in-process runs alike. Without Docker,
  `auto` mode runs Code steps in-process and the check before a run warns; `docker` mode fails
  closed. Exported code runs Code steps in-process and says so in its header.
- **Agents** can only call the tools you gave them. Steps that change things keep "send at most
  once", and **Ask a person before …** makes a person approve each call. The check before a run
  warns when an agent's Database query can write.
- **Database queries** are read-only by default: a read-only transaction (`SET TRANSACTION READ
  ONLY` on Postgres, `PRAGMA query_only` on SQLite) plus a statement check. Flow Data goes in as
  bound parameters, never into the SQL text. Passwords in connection URLs are flagged; use
  `{secret:NAME}`.
- **MCP servers** started as local commands (stdio) run only if the command is on an
  allow-list that can be edited in Pro mode. Tokens go in headers as `{secret:NAME}`.
- **Which addresses Easy Chain may reach** (Phase 5.6f, `egress.py`, `server/network.py`,
  [docs/network.md](docs/network.md)). The egress policy has three modes:
  - `open`: the default with sign-in off;
  - `public`: public addresses, plus internal ones on the allow-list; the default with sign-in on;
  - `allow-list`: only the listed sites.

  It is a context variable, set from Settings → Network (or `EASYCHAIN_EGRESS`):
  - for runs, through `RunOptions.egress`;
  - for Knowledge Base reading, in the worker;
  - for previews and imports, in the API.

  What it covers:
  - Easy Chain's own fetches (`fetch.py`).
  - Web request steps: the loader swaps the module's `httpx` for `GuardedHttpx`, while
    exported code keeps plain httpx.
  - MCP HTTP servers, via an httpx client factory.

  How it checks:
  - every request and redirect, by URL (an event hook);
  - the address connected to, for direct connections: `GuardedBackend` wraps httpcore's
    network backend, and connects to the address it checked, which stops DNS rebinding.

  Model providers and notifications aren't checked.

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
| `test_migrations.py` | The migrations build exactly the tables `db.py` describes (SQLite and Postgres); a database made by 0.4.0 is upgraded in place with its data; a newer one is refused; every migration can be undone and redone; `easychain db` |
| `test_auth.py` | Every route declares who may use it (a new one fails the test); sign-in off by default only on this computer; the first Owner needs the code from the log, and guessing it is stopped; each role gets what it should and no more (through keys); the last Owner can't be demoted, switched off or removed; switching someone off signs them out and stops their keys; a new password signs out other browsers; wrong passwords lock sign-in; keys are capped, shown once and revocable; live run streams need sign-in, webhooks need only their token; the Inbox records who answered; `easychain users` / `keys` |
| `test_audit.py` | Every route that changes something is recorded or says why not; who did what (sessions, keys, webhooks, the command line, sign-in off); refused and invalid changes and secret values are never recorded; a burst of saves is one entry; failed sign-ins are; filters and pages; only Owners read it; the table refuses UPDATE, DELETE (and TRUNCATE on Postgres); a failure writing the log doesn't undo the change; `easychain audit` |
| `test_checks.py` | Every check passes and fails as it should and says why (JSON schema, numbers, choices, the order of tool calls in three ways, call counts); budgets add up every part of a case, and a cost with no price is skipped; the AI judge grades with a real model (the fake server), and is skipped without a key or with the stand-in; a flow's Test Set is read, checked, saved and run from the API (answers, variables, one case, the canvas's flow), and recorded in the audit log; the command line shows skipped checks |
| `test_testruns.py` | Repeats give a pass rate and mark flaky cases; an unknown cost stays unknown; each case is compared with the baseline (better, worse, same, new, not run) with time and cost changes; through the API on the inline worker: a kept run against the saved version, a baseline, a regression after the flow changes, the old run keeping its cases, one baseline per flow, nothing seen from another workspace |
| `test_evaluate.py` | The CI gate: with no baseline every case must pass; with one, only a regression fails it (a known failure doesn't), and so does a run that got too slow; the budget stops starting cases; the baseline file and the Markdown report; `easychain eval` exit codes, `--save-baseline`, `--summary` (appended) and `--json` |
| `test_publish.py` | An environment swaps models and Knowledge Bases, and its secrets win over the workspace's (in Test Runs in it); promotion: dev as saved, staging only after a passing Test Run in staging, production only the staging version, and only Owners; access keys run what is published (their environment's secrets), are refused for another environment, after revoking and when nothing is published, and are rate limited with `Retry-After`; chat links serve the chat page (with `frame-ancestors`), chat through the fake model and keep the conversation; `easychain publish` runs the Test Run first; `easychain deploy` writes a Compose bundle and builds an image |
| `test_gateway.py` | Each model call is recorded with its tokens and cost; own prices win and price unknown models (an unknown price stays unknown); a budget used up stops the call before the provider is asked and doesn't fall back; cached answers are free, per workspace and keyed by the settings; a failing model falls back (plain, with tools, structured output and streaming), and a fallback with no key is left out; retries and one smoothing limiter per workspace and provider; on the server, budgets alert (webhook) and then stop runs with a fix, drafts' cache and forgetting it, a fallback in a run, and spending per flow, person, environment, model and run for runs, Test Runs, the Tests tab and published runs; the ledger's queries on SQLite and Postgres |
| `test_observe.py` | The dashboard's numbers (every day of the window, counts, failure rate, nearest-rank p50/p95, tokens and cost from model calls, flows, the commonest errors) and its API with filters; tracing settings hide header values and keep them when sent back hidden; a run becomes a trace (run, steps, model calls under their step, GenAI attributes); a paused and resumed run is one trace; spans reach a real OTLP/HTTP collector (protobuf, headers, service name) and the test button says whether the collector took a span; LangSmith gets the run (and its steps) only with the workspace's key |
| `test_egress.py` | The policy's modes and allow-list (names, endings, addresses, networks; IPv4 written as IPv6); Easy Chain's fetches and their redirects; a name that changes address between the check and the connection is refused, and connections go to the checked address; async clients; on a server with sign-in on, a Web request step, a Knowledge Base page, an OpenAPI document and an MCP server can't reach inside until allowed; who may change it; the environment wins; nothing changes on this computer |
| `test_sso.py` | Single sign-on against the test identity provider: an existing account signs in and stays linked by the provider's id; new people get the provider's role only for allowed domains, or are told to ask an Owner; a wrong nonce, audience, issuer, signing key, an expired token, an unverified email, a refusal at the provider, the wrong client secret, a sign-in from another browser and a reused state are all refused with a message; a switched-off account can't; Owners set it up, the secret goes to the vault; the environment wins; it's in the audit log |
| `test_scoped_env.py` | Secrets reach the work in hand through `os.environ` but aren't exported into the process (child processes don't see them); work running at the same time sees only its own layer, threads included; a layer wins over the server's environment, and the server's keys apply everywhere |
| `test_workspaces.py` | Each workspace keeps its own flows, runs, versions, conversations, Inbox, triggers (webhooks run in theirs), settings, skills, Knowledge Bases (runs can't search another's; the sample is shared, read-only), secrets (a run sees only its workspace's vault) and long-term memory; roles are per workspace, keys are bound to theirs, only server Owners change shared settings; the command line's `workspaces` and `--workspace` |
| `test_listen.py` | On Postgres, a run's new events wake a follower in another process at once (median about 2 ms), a queued job wakes a waiting worker, and a listener whose connection is killed reconnects |
| `test_knowledge_queue.py` | Knowledge Base documents wait for a worker and survive a restart; a worker with its own disk reads uploads from the database; a worker that dies mid-document is replaced without doubling chunks; a removed document leaves nothing; an unexpected problem shows on the document and isn't retried |
| `apps/web/src/**/*.test.ts` | Editing helpers, undo grouping, SSE parsing, formatting, the copilot's changes and panel |
| `apps/web/e2e/*.spec.ts` | Playwright: build → run → debug → export (and run the export), templates and chat, fixes, undo, copy/paste, Pro mode, settings, Ask a Human in the run panel and the Inbox, For Each, breakpoints and Save Points, stop and carry on, reload during a run, triggers, the Flow Data panel, Sub-flows, agent tools and live tool calls, tool approval, the reply format builder, Knowledge Bases, cited answers, MCP servers, API import, the copilot (propose, preview, apply as one undo step, discard), the Research assistant (Helpers on the canvas, the trace with nested Helper calls, the to-do list and files), the Skills library, Inbox triage (a supervisor and an Inbox pause), the sandbox settings, sign-in on a second server with it on (the first Owner with the code, adding a Runner, keys for scripts, signing out and in, signing in again over the page; a Viewer's view-only editor and Inbox, a Runner running a saved flow and answering it, an Editor's read-only server settings, the Audit log, a blocked Web request allowed by an Owner, single sign-on through a test identity provider), axe WCAG 2.2 AA scans, 300-step canvas |
| `test_standin_tools.py` | The stand-in AI calls tools, fills arguments and JSON schemas, cites sources, reads each web address a request names, and plays scripts |
| `test_agent.py` | Agent tool calls and their events, approvals (approve, edit, reject) through the Inbox shape, structured answers, add-ons, limits, MCP tools |
| `test_knowledge.py` | Every file format, chunk provenance, hybrid search and citations on SQLite and on Postgres with pgvector, the API, an agent searching with its own queries, documents cut off by a restart, exported code searching on its own |
| `test_integrations.py` | Memory (remember, recall, trim, summarise), read-only SQL, MCP over stdio and HTTP and its allow-list, OpenAPI import |
| `test_deep_agent.py` | The Deep Agent plans, hands a task to a Helper (nested trace, usage per Helper), keeps its files across turns; approvals inside the Deep Agent, inside a Helper and in the general helper pause the run and go through the Inbox (with a worker); the checks; Test Set `helpers:`/`files:`; **exported code runs on its own**. `test_durability.py` also kills a worker inside a Deep Agent: it resumes with the files it had |
| `test_skills.py` | Skills are checked before they are saved; the library over the built-in skills (a copy replaces one, deleting it brings it back); the API; a missing skill is a problem with a fix; the skills ship inside the generated code and a changed skill recompiles; the stand-in reads the skill that matches the task and writes the file it asks for; a Helper reads its own skills |
| `test_patterns.py` | The three agent team patterns use only ordinary steps and compile cleanly; an AI Decision fills Flow Data into its guidance; each exported pattern runs on its own against the fake model server (their Test Sets run in `test_templates.py`) |
| `test_copilot.py` | **Proposals for ten written requests validate with no errors** (scripted replies); problems go back to the model until they are fixed, or it gives up and says why; a change becomes separate changes with plain labels; new steps never land on old ones; the stand-in picks the closest template; the copilot uses a model there is a key for; the API |
| `test_research_assistant.py` | **Phase 4's Research assistant "Done when"**: the template is a Deep Agent with two Helpers, skills, a to-do list and files (its 10-case Test Set runs in `test_templates.py`), and the exported project runs on its own, skills included, against the fake model server |
| `test_sandbox.py` | **Phase 4's sandbox "Done when"**, with real containers: the host's files, the network, Easy Chain's environment and secrets, root and capabilities, the Docker socket, writes outside `/tmp`, a fork bomb, memory and time are all out of reach; errors come back as they were; a flow's Code step runs in the sandbox; the settings API and the check before a run |
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
| SQLAlchemy + Alembic | SQLAlchemy Core, with Alembic migrations since Phase 5 (`server/migrations/`) | Every process upgrades the database when it starts, under an advisory lock (Postgres) or SQLite's write lock, so several can start together and nothing has to be run by hand; `easychain db status` and `upgrade` are there for operators. Databases made before migrations (0.4.0 and earlier, by `create_all`) are stamped at the baseline. A test checks that the migrations build exactly the tables `db.py` describes. |
| Code modules run in a sandbox | Code steps run in a Docker container per call (gVisor when installed); without Docker they run in-process with a warning, unless the sandbox is set to "Always" | Docker works on Linux, Windows and macOS (decision 4.2) and needs no extra service. A warm pool of containers, microVMs and hosted sandboxes (E2B, Modal) can be added behind the same proxy. In Docker Compose the worker needs the host's Docker socket for this (`docker-compose.sandbox.yml`), which is powerful; it is an opt-in overlay. |
| Deep Agents keep a virtual file area in the LangGraph store | Their files live in the agent's checkpointed state and are saved to a Flow Data field (`files`) | The state is saved with every Save Point, so the files survive a crash (tested) and rewind with time travel; the store would keep them outside the run's history. Flow Data also lets later steps read a file. A store-backed `/memories/` area can come with long-term memory for Deep Agents. |
| The copilot has "Fix this" on any problem | **Ask the copilot** is on problems that have no fix button of their own | A problem with a fix already has a one-click, exact fix; sending it to a model would be slower and could change more than it needs. |
| (not in prompt) | Deep Agents have no `execute` tool | Running commands belongs in Code steps, which run in the sandbox. A deepagents sandbox backend on the same Docker sandbox can add it later. |
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
| Templates with 10-case Test Sets (section 14) | Thirteen templates ship with Test Sets of 10 to 12 cases (Phase 3 adds *Support bot over docs* and *SQL analyst*; Phase 4 the three agent team patterns, *Research assistant* and *Inbox triage*); section 14's other templates arrive with the phases that provide their features | They need publishing and so on. |
| For Each runs "a step or sub-flow" per item | One step per item; several steps per item go in a Sub-flow | Keeps the canvas honest about what runs per item and maps to one `Send` target. |
| Breakpoints | Set per flow in the browser, not saved in the flow file | Breakpoints are a debugging aid for test runs, not part of the flow's behaviour, so they don't belong in reviewed YAML. |
| Event streaming | The API tails the run's events from the database; on Postgres, LISTEN/NOTIFY wakes it (since Phase 5.8), with polling as the fallback | The same code on SQLite and Postgres, and it survives API restarts; between processes events arrive in about 2 ms on Postgres, and within 0.25 s on SQLite. |
| Knowledge Base on pgvector (via LangChain's vector stores) | Easy Chain's own three tables, using pgvector directly when present, numpy otherwise, and Postgres/SQLite full-text search | One schema for SQLite and Postgres, hybrid search (LangChain's PGVector store has no full-text side), per-document status and provenance, and search code small enough to copy into exports. The tables are plain SQL, so moving to another store later is a migration, not a rewrite. |
| Documents ingested by workers | Done in Phase 5.8: documents are read by workers from the job queue (until then, in the API server's background tasks) | The file is kept in the database until a worker reads it, so documents survive restarts and the API server stays light. |
| OCR for scanned PDFs | Not supported; such PDFs fail with a message saying so | OCR needs a heavy system dependency (Tesseract) or a paid API. |
| Agent = graph of its own | The agent is built and invoked inside the step's node (a subgraph at run time) | One node per canvas box keeps the trace, glow, errors and token counts 1:1 with the canvas, and the agent still checkpoints and pauses with the flow. |
| Test Sets judge agents | Test Sets check tools called, not called and call counts, and can script the model's turns | Deterministic, key-free tests of a flow's logic. LLM-as-judge and trajectory checks are Phase 5. |
