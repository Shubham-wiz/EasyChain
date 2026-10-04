# Decisions

Newest first. Each entry says who decided (**owner** = the project owner; **build** = chosen
during the build and open to review) and why. Questions already answered here should not be
asked again.

## Working rules

| # | Question | Decision | Who |
|---|---|---|---|
| R.5 | Fix the code review first | The review of 2026-10-04 ([review.md](review.md)) is worked through before Phase 4: data-loss and security findings first, each fix with a test. | owner: "OK go on" to the proposal |
| R.4 | Windows and Linux | Easy Chain runs from source on **both Windows and Linux** (and macOS). CI tests Linux and Windows. Developer commands are `pnpm <task>` (`scripts/tasks.mjs`), so `make` isn't needed; what differs between systems lives in `easychain/_platform.py`. | owner: "we need it to be platform independent, Linux and Windows both can run it" |
| R.3 | The retired branch | `claude/tender-fermat-4kj4k6` still carries the AI-attribution trailers on GitHub, so it is to be deleted (the code is all on `main`): `git push origin --delete claude/tender-fermat-4kj4k6`. The owner runs this. | owner: "OK go on" to deleting it |
| R.2 | Branch | Work happens on `main`. The first branch, `claude/tender-fermat-4kj4k6`, was replaced by `main` when the history was rewritten for R.1 (it was not actually deleted then; see R.3). | owner: "yes" to renaming it to `main` |
| R.1 | AI attribution in git | No `Co-Authored-By: Claude…`, session links or "generated with" lines in commit messages or pull request descriptions. Commits are authored as `Easy Chain Dev <19shubhamdwivedi@gmail.com>`. The existing history was rewritten to remove the trailers. | owner: "remove from git that it's made by claude" |

## Phase 4 (answers to the Phase 3 report's questions)

| # | Question | Decision | Who |
|---|---|---|---|
| 4.1 | Phase 4 "Done when" | The proposal in decision 3.13: a **Research assistant** template (Deep Agent + Helpers + to-do list + files) passes a 10-case Test Set, and Code steps can't escape their sandbox (escape tests) | owner: "OK go on" to the recommended answers |
| 4.2 | Sandbox backend for Code steps | **Docker** by default, with **gVisor** (`runsc`) where it is installed; hosted sandboxes (E2B, Modal) later as plugins. Must work with Docker Desktop on Windows too (R.4). | owner: same |
| 4.3 | The describe-it copilot's model | **Any model the user has a key for**; with no key, the stand-in AI shows a canned example | owner: same |

## Phase 3

| # | Question | Decision | Who |
|---|---|---|---|
| 3.1 | Default vector store | **pgvector** in the Phase 2 Postgres; a built-in local index for SQLite installs; Qdrant/Chroma adapters later | owner said "continue" to the recommendation |
| 3.2 | Default embeddings | OpenAI `text-embedding-3-small` when an OpenAI key is set, else Ollama `nomic-embed-text` when `OLLAMA_HOST` is set, else the key-free **Keywords** embedder (also used by tests and the sample help centre) | owner said "continue" to the recommendation; Keywords added so a fresh install works with no key or Ollama |
| 3.3 | MCP servers over stdio (they start local processes) | Allowed only in **Pro mode**, and only commands on an allow-list approved in Settings; streamable-HTTP MCP servers are allowed normally | owner said "continue" to the recommendation |
| 3.4 | How agents get tools | Other steps on the canvas are an Agent's tools (`settings.tools` lists their ids). Each becomes a `@tool` that runs the step's own function. Its arguments are the fields the step reads that Flow Data doesn't have when the agent runs. | build: reuses every step type as a tool, with no second implementation |
| 3.5 | Agent Add-ons | LangChain's own middleware classes, emitted into the code | build: exported code stays plain LangChain |
| 3.6 | Tool approval in the Inbox | HumanInTheLoopMiddleware interrupts are shown as kind `approve_tool`; Inbox answers become its decisions | build: one Inbox for Ask a Human and agents |
| 3.7 | Knowledge Base storage | Easy Chain's own tables (`kb_bases`, `kb_documents`, `kb_chunks`) in its database, not a LangChain vector store class. Search is one function, copied into exported code. | build: one design for SQLite and Postgres, hybrid search and citations, and exported code that searches the same data |
| 3.8 | Test Sets without a key | The stand-in AI answers from cited passages by itself; agent cases may script the model's tool calls (`script:`), while tool results, limits and approvals are real | build: CI has no keys; a real model ignores scripts |
| 3.9 | SQL safety | Read-only queries run in a read-only transaction (SQLite `query_only`, Postgres `READ ONLY`), plus a statement check | build |
| 3.10 | Where documents are ingested | In the API server's background tasks, not on the job queue; a restart marks unfinished documents failed with a clear message | build: the upload is in the API process and ingestion isn't a run; moving it to the queue is a Phase 5 item |
| 3.11 | Postgres image | Docker Compose and CI use `pgvector/pgvector:pg16` instead of `postgres:16-alpine` | build: Knowledge Bases use pgvector when present; existing Compose volumes should be reindexed after the switch (musl vs glibc collation) |
| 3.12 | Version | 0.3.0 at the end of Phase 3 (Python package, web app, client, Compose image tags) | build |
| 3.13 | Phase 4 "Done when" | **Proposed, not decided:** a Research assistant template (Deep Agent + Helpers + to-do list + files) passes a 10-case Test Set, and Code steps can't escape their sandbox (escape tests) | the original brief isn't in the repo; asked in the Phase 3 report |

## Phase 2

| # | Question | Decision | Who |
|---|---|---|---|
| 2.1 | Where flows live | Flows stay **YAML files** in a workspace folder (git-friendly). The database holds runs, events, jobs, flow versions (every run records the exact version it ran), Save Points, the Inbox, triggers and settings. | owner: "ok what ever u think is right" |
| 2.2 | Job queue | A **database-table queue** (`FOR UPDATE SKIP LOCKED` + advisory locks on Postgres; `BEGIN IMMEDIATE` on SQLite). No Redis until load needs it. | owner: same answer |
| 2.3 | Code step safety before Phase 4 | Code steps stay **unsandboxed** (local, single-user, trusted) until Phase 4. The server binds to 127.0.0.1, and workers can run in separate containers. | owner: same answer |
| 2.4 | Licence | Apache 2.0 | owner: same answer (suggested by the build prompt) |
| 2.5 | Breakpoints | Stored per flow in the browser, not in the flow file | build: they are a debugging aid, not flow behaviour |
| 2.6 | For Each body | One step per item; several steps per item go in a Sub-flow | build: maps to one `Send` target, so the canvas shows what runs per item |
| 2.7 | Event streaming | The API tails run events from the database (polling plus an in-process wake-up), not LISTEN/NOTIFY | build: the same on SQLite and Postgres, and survives API restarts |
| 2.8 | Side effects | POST/PUT/PATCH/DELETE requests and Code steps marked "at most once" record their result in the LangGraph store under a stable key, and send it as `Idempotency-Key` | build: needed for the "no duplicate side effects" Done-when |
| 2.9 | Schema migrations | `create_all` plus a schema version check; Alembic arrives with the first schema change | build |
| 2.10 | Ask a Human default `save_as` | `human_answer` (not `review`, which collided with a common step id) | build |

## Phases 0 and 1

| # | Question | Decision | Who |
|---|---|---|---|
| 1.1 | How much to build before stopping | Phases 0 and 1 were built in one go and reported together | owner: "you are the developer, take this and build, do testing and all as well" |
| 1.2 | Decision step | A node (a no-op for rules; the classifier for AI mode) plus a conditional edge | build: one node per canvas box, so the trace, glow and errors map 1:1 |
| 1.3 | Model construction | `init_chat_model(...)` inside each AI step function | build: a missing key becomes that step's error, and the gateway can swap one name |
| 1.4 | Running without keys | A clearly labelled **stand-in AI** | build: templates, demos and tests run with no key |
| 1.5 | Secrets | A Fernet-encrypted local vault (`~/.easychain`); values go into the server's environment | build: single-user local install for now; KMS, roles and audit come in Phase 5 |
| 1.6 | Tracing | Easy Chain's own event stream, stored with each run | build: OTel/LangSmith export comes in Phase 5; the events map onto spans |
| 1.7 | UI components | shadcn-style components written directly on Radix (`components/ui.tsx`) | build: same look and accessibility, without the CLI |
| 1.8 | Framework majors | TypeScript 5.9, Vite 7, Vitest 3, React 19 | build: "prefer well-known, boring libraries" |
| 1.9 | Pins | LangGraph 1.2.12, LangChain 1.4.3, langchain-core 1.6.6, langchain-openai 1.6.7, langchain-anthropic 1.7.5, langchain-ollama 1.1.0 | build: exact pins plus `uv.lock`; upgrades go through golden tests |
| 1.10 | Templates | Templates ship with Test Sets of 10–12 cases. The build prompt's section 14 templates arrive with the phases that provide their features. | build |

The full table of deliberate deviations from the build prompt is in
[ARCHITECTURE.md §10](../../ARCHITECTURE.md#10-deviations-from-the-build-prompt-and-why).
