# easychain (the Python package)

This package is the product: the flow spec, the compiler that turns a flow into LangGraph
Python, the runtime that runs it, the API server and workers, the Knowledge Base and
integrations, and the CLI. The web app in [`apps/web`](../apps/web) is a client of the API
served from here. For the project as a whole, see the [project README](../README.md) and
[ARCHITECTURE.md](../ARCHITECTURE.md).

Python 3.12, managed with [uv](https://docs.astral.sh/uv/). The LangChain stack is pinned
exactly (`langgraph==1.2.12`, `langchain==1.4.3`, `langchain-core==1.6.6`; see
`pyproject.toml`).

## Install and run

```bash
uv sync --extra providers          # everything, including the optional model providers
uv run easychain dev               # API + built web app on http://127.0.0.1:8000 (SQLite, in-process worker)
uv run easychain worker --database-url postgresql://…   # extra workers on a shared Postgres
```

`easychain dev` serves the built web app from `EASYCHAIN_WEB_DIST`, else from
`src/easychain/server/static` (where `pnpm build` copies it, and where the Docker image has it),
else, in a git checkout, straight from `apps/web/dist`. So run `pnpm build` in the repo root
first. During web development, run `pnpm dev` from the repo root instead: API on :8000, Vite
on :5173.

## The CLI

| Command | What it does |
|---|---|
| `easychain new NAME [--template ID]` | Create a flow file, blank or from a template. |
| `easychain validate FLOW` | Check a flow for problems (the same checks as the canvas). |
| `easychain compile FLOW` | Print the LangGraph code for a flow. |
| `easychain export FLOW -o DIR` | Write a standalone LangGraph project (code, `requirements.txt`, `langgraph.json`, the flow). |
| `easychain run FLOW -i key=value [--stand-in]` | Run a flow and print the result. Ask a Human and tool approvals are answered in the terminal. |
| `easychain test TESTS.yaml [--stand-in] [--var k=v]` | Run a Test Set against its flow. |
| `easychain schema [-o FILE]` | Print the flow spec JSON Schema. |
| `easychain templates` | List the starter templates. |
| `easychain dev [--port N]` | Start the app: API, web app and a worker in one process. |
| `easychain worker` | Run queued runs. Start more of them to scale out. |

`--stand-in` uses the clearly labelled stand-in AI, so no API key is needed.

## How the code is organised

```
src/easychain/
  spec/          the flow spec: Pydantic models (models.py), YAML in/out (io.py), JSON Schema (schema.py)
  steps/         one handler per step type: form, reads/writes, checks, code emitter
  compiler/      flow spec → LangGraph Python source
  runtime/       load and run compiled code; model gateway; stand-in AI; events
  server/        FastAPI app, run database, job queue and workers, Inbox, triggers, notifications
  knowledge/     Knowledge Bases: reading files, splitting, embeddings, storage, hybrid search; memory
  integrations/  read-only SQL, MCP client, OpenAPI import
  templates/     starter flows, their Test Sets, and the sample data they use
  testing/       a fake OpenAI-compatible server and a small MCP server, for tests and demos
  cli.py         the `easychain` command
  export.py      the export zip / folder
  testsets.py    Test Sets: run cases, apply checks
  providers.py   the 14 model providers, their models, prices and keys; embedding models
```

### `spec/`: the source of truth

`models.py` defines every step's settings, Flow Data fields, connections and canvas positions.
Defaults are left out when a flow is saved, so a default can't change under a saved flow
without bumping the spec `version`. After changing it, run `pnpm schema` to regenerate
[`spec/flow.schema.json`](../spec/flow.schema.json).

### `steps/`: one handler per step type

| File | Step types |
|---|---|
| `io_steps.py` | Input, Output |
| `ai.py` | Instructions, AI Model (including structured replies) |
| `actions.py` | Web request, Code |
| `logic.py` | Decision |
| `flow_control.py` | For Each, Sub-flow, Jump, Ask a Human |
| `agent.py` | Agent (tools, add-ons, MCP tools) |
| `knowledge.py` | Knowledge Base search |
| `memory.py` | Memory |
| `sql.py` | Database query |
| `mcp.py` | MCP tool |
| `base.py` | `StepHandler`, `FormField`, `StepCode`: the interface every step implements |

A handler gives the catalog entry and inspector form (the web app has no hard-coded forms),
what the step reads and writes, its checks (problems in plain words, each pinned to a setting,
with a one-click fix where possible) and the code it compiles to. Adding a step type follows
the checklist in [CONTRIBUTING.md](../CONTRIBUTING.md).

### `compiler/`: flow spec in, LangGraph Python out

| File | Job |
|---|---|
| `analysis.py` | The graph's shape, Flow Data fields and types, what each step reads and writes, which steps are an agent's tools. |
| `validate.py` | The checks before a run: structure, connections, data flow, step settings, keys. |
| `codegen.py` | Writes the module: a `TypedDict` state with reducers, one node per step, conditional edges, `build_graph()`, a `main` for the terminal. |
| `helpers.py` | Small functions copied into generated code when a flow needs them (templating, run-once side effects, search, MCP, SQL, memory). |
| `schema_code.py` | Structured replies → Pydantic classes. |
| `reducers.py`, `expressions.py`, `templates.py` | Update rules, safe Decision expressions, `{placeholder}` filling. |
| `pycode.py`, `issues.py` | Readable source (imports, names, wrapping); problems and fixes. |

Generated code imports only LangGraph, LangChain, provider packages and a few libraries it
lists in its header and `requirements.txt`. Golden files in `tests/golden/expected/` pin it:
run `pnpm golden` after an intended change and read the diff.

### `runtime/`: running a flow

`runner.py` compiles (cached), loads the module with `loader.py`, builds the graph with a
checkpointer, store and cache from `resources.py`, and streams LangGraph's output as Easy
Chain events: step started and finished, tokens, routes, tool calls, Save Points, progress and
pauses. It starts, resumes, continues and forks runs, with breakpoints and cancel.

`gateway.py` replaces `init_chat_model`, `init_embeddings` and the MCP tool loader in the loaded
module. It checks keys and installed packages, and hands back the stand-in AI
(`standin.py`) when asked. `errors.py` turns exceptions into messages people can act on, and
`inputs.py` checks run inputs.

### `server/`: the API, the queue and the workers

| File | Job |
|---|---|
| `app.py` | The FastAPI app: flows, catalog, checks, compile, export, runs (SSE and WebSocket), Inbox, secrets, settings, templates, triggers. Serves the web app. |
| `db.py` | The run database (SQLite or Postgres): runs, events, jobs, Inbox, triggers, flow versions, settings. |
| `hub.py` | Starting, resuming and watching runs; shared by the API, the worker and triggers. |
| `worker.py` | Leases jobs, runs them, heartbeats, takes over jobs from dead workers. |
| `store.py` | Flow files in the workspace folder. |
| `secrets.py` | The encrypted local secrets vault. |
| `knowledge_api.py`, `integrations_api.py` | Knowledge Bases; MCP servers and OpenAPI import. |
| `cron.py`, `mail.py`, `notify.py` | Schedules, the email trigger, and notifications (webhook, Slack, email). |

The API at a glance (all under `/api`):

| Area | Endpoints |
|---|---|
| Flows | `GET/POST /flows`, `GET/PUT/DELETE /flows/{id}`, `/flows/{id}/yaml`, `/flows/{id}/versions`, `/flows/{id}/export`, `/versions/{id}` |
| Building | `GET /catalog`, `GET /providers`, `GET /schema`, `GET /templates`, `POST /check`, `POST /compile`, `POST /export` |
| Runs | `GET/POST /runs`, `GET /runs/{id}`, `/runs/{id}/events` (SSE, `Last-Event-ID`), `/runs/{id}/ws`, `POST /runs/{id}/resume`, `/continue`, `/fork`, `/cancel`, `GET /runs/{id}/savepoints`, `GET /threads` |
| People | `GET /inbox`, `GET /inbox/{id}`, `POST /inbox/{id}/answer` |
| Triggers | `GET/POST /triggers`, `PATCH/DELETE /triggers/{id}`, `POST /hooks/{id}`, `POST /hooks/{id}/upload` |
| Knowledge | `GET/POST /knowledge`, `GET/PATCH/DELETE /knowledge/{id}`, `POST /knowledge/{id}/files`, `/urls`, `/text`, `/search`, `GET/DELETE /knowledge/{id}/documents/{doc}…`, `POST /knowledge-preview` |
| Settings | `GET /secrets`, `PUT/DELETE /secrets/{name}`, `GET/PUT /settings/mcp`, `POST /mcp/tools`, `GET/PUT /settings/notifications`, `POST /settings/notifications/test`, `POST /openapi/inspect`, `POST /openapi/steps` |

How runs, events, the Inbox and triggers behave is in [docs/runs.md](../docs/runs.md).
[`@easychain/client`](../packages/client) is a TypeScript client for the runs API.

### `knowledge/` and `integrations/`

- `knowledge/loaders.py` reads PDF, Word, HTML, Markdown, CSV and text, and web pages.
- `ingest.py` splits and embeds them. `store.py` keeps them in `kb_*` tables: pgvector on
  Postgres when it's there, numpy otherwise, plus full-text search.
- `search.py` runs hybrid search and citations. This code is also copied into exports.
- `memory.py` holds the long-term memory helpers (the LangGraph store).
- `integrations/sql.py` runs SQL read-only by default, `mcp.py` connects to MCP servers, and
  `openapi.py` turns OpenAPI operations into Web request steps.

See [docs/knowledge.md](../docs/knowledge.md) and [docs/agents.md](../docs/agents.md).

## Environment variables

| Variable | What it does |
|---|---|
| `EASYCHAIN_HOME` | Data folder (default `~/.easychain`): SQLite database, secrets vault, uploads, samples. |
| `EASYCHAIN_DATABASE_URL` | Run database: `sqlite:///…` (default) or `postgresql://…`. |
| `EASYCHAIN_KNOWLEDGE_URL` | Where Knowledge Bases live, if not the run database. |
| `EASYCHAIN_WORKER` | `inline` (default for `dev`) or `off` (API only; run `easychain worker` separately). |
| `EASYCHAIN_SECRET_KEY` | Key for the secrets vault (otherwise `secret.key` in the data folder). |
| `EASYCHAIN_PUBLIC_URL` | Base URL for links in notifications. |
| `EASYCHAIN_WEB_DIST` | Folder with the built web app to serve. |
| `EASYCHAIN_MCP_SERVERS` | MCP servers for exported code: JSON `{"<server id>": {"transport": …, "url" or "command": …}}` (see docs/steps/mcp_tool.md). |
| `EASYCHAIN_ALLOWED_HOSTS` | More host names the API answers to (comma-separated; `*.example.com`; `*` for any), e.g. behind a reverse proxy. |
| `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, … | Provider keys (or set them in the app; they go in the vault). |

## Tests

```bash
uv run pytest -q                                   # everything (about 320 tests)
uv run pytest tests/test_compiler_golden.py -q     # generated code
UPDATE_GOLDEN=1 uv run pytest tests/test_compiler_golden.py   # after an intended compiler change
uv run pytest tests/test_durability.py -q          # worker crashes and day-later approvals
uv run ruff check src tests && uv run ruff format --check src tests
```

Tests never call a paid API. They use the stand-in AI, `easychain.testing.fake_openai` (an
OpenAI-compatible server with tool calls, JSON replies, embeddings and scripts), and
`easychain.testing.mcp_server`. Postgres tests run when Postgres is installed or
`EASYCHAIN_TEST_POSTGRES_URL` is set; otherwise they are skipped. The table in
[ARCHITECTURE.md §8](../ARCHITECTURE.md#8-testing) says what each test file proves.
