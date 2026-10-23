# Decisions

Newest first. Each entry says who decided (**owner** = the project owner; **build** = chosen
during the build and open to review) and why. Questions already answered here should not be
asked again.

## Working rules

| # | Question | Decision | Who |
|---|---|---|---|
| R.5 | Fix the code review first | The review of 2026-10-04 ([review.md](review.md)) is worked through before Phase 4: data-loss and security findings first, each fix with a test. | owner: "OK go on" to the proposal |
| R.4 | Windows and Linux | Easy Chain runs from source on **both Windows and Linux** (and macOS). CI tests Linux and Windows. Developer commands are `pnpm <task>` (`scripts/tasks.mjs`), so `make` isn't needed; what differs between systems lives in `easychain/_platform.py`. | owner: "we need it to be platform independent, Linux and Windows both can run it" |
| R.3 | The retired branch | `claude/tender-fermat-4kj4k6` still carried the AI-attribution trailers on GitHub, so it was deleted (the code is all on `main`). Only `main` is left. | owner: "OK go on"; the owner deleted it |
| R.2 | Branch | Work happens on `main`. The first branch, `claude/tender-fermat-4kj4k6`, was replaced by `main` when the history was rewritten for R.1 (it was not actually deleted then; see R.3). | owner: "yes" to renaming it to `main` |
| R.1 | AI attribution in git | No `Co-Authored-By: Claude…`, session links or "generated with" lines in commit messages or pull request descriptions. Commits are authored as `Easy Chain Dev <19shubhamdwivedi@gmail.com>`. The existing history was rewritten to remove the trailers. | owner: "remove from git that it's made by claude" |

## Phase 5 (answers to the Phase 4 report's questions)

| # | Question | Decision | Who |
|---|---|---|---|
| 5.1 | Phase 5 "Done when" | The four proposed criteria: a flow moves **draft → staging → production** only when its Test Run passes in CI; a published flow serves an **API endpoint and a chat widget** with per-key rate limits; **roles** stop a Viewer from editing and an Editor from publishing to production; every change and publish is in the **audit log** | owner: "report looks fine so lets get on" to the recommended answers |
| 5.2 | Single sign-on provider for the final check | **None for now.** OIDC sign-in is built and tested against a local test identity provider; setup for Okta, Google and Microsoft Entra is documented | owner: same |
| 5.3 | Publishing | **Self-hosted only** in Phase 5: your Easy Chain server serves the API endpoint, the chat page and the widget; `easychain deploy` builds an image or a Compose bundle | owner: same |
| 5.4 | The AI judge in Checks | **Any model with a key** (the copilot's rule); with no key, judge checks are skipped with a note, never faked | owner: same |
| 5.5 | Real-model checks before a release | A **manual CI workflow** with a budgeted key stored as a repository secret, so the result is recorded | owner: same |
| 5.6 | Tracking the build | The README has a **build tracker**: every step of every phase marked done, in progress or not started, and a **Fix later** list of known issues with where each is handled. It is updated in the same commit as the work. | owner: "please make sure u mark every step in readme, so we can go fix things later" |
| 5.7 | Database migrations | **Alembic**, run by every process when it starts (under the schema lock), so upgrading Easy Chain upgrades the database; `easychain db status` and `upgrade` for operators. Migrations are numbered (`0001`, `0002`, …). A database made by 0.4.0 or earlier is stamped at the baseline (`0001`, the same tables). A database a newer Easy Chain upgraded is refused. | build |
| 5.8 | Where Knowledge Base documents are read | By **workers, from the job queue**: an `ingest` job per document (its thread is `kb:<id>`, so one Knowledge Base's documents are read one at a time); an uploaded file waits in the `uploads` table (migration 0002) until it has been read, then is deleted; a document whose reading hit an unexpected problem says so and isn't retried. At startup only documents with no job are marked as cut off. | build |
| 5.10 | Sign-in | **On when the server answers the network, off on this computer**: `easychain dev` (127.0.0.1) stays as it was; the Docker image (0.0.0.0) needs sign-in; `EASYCHAIN_AUTH=on/off` decides otherwise. The **first Owner** needs a code printed in the server's log (or `easychain users setup-code`). Roles **Viewer < Runner < Editor < Owner** (docs/people.md); running a flow given in full needs an Editor. **Keys for scripts** act as their person, capped at the role they were made with. Every route declares its least role; an undeclared one is closed. | build: the safe default for anything on a network, without changing one-person use |
| 5.9 | Waking other processes | On Postgres, **LISTEN/NOTIFY**: writing run events, queueing and finishing jobs send a NOTIFY on commit; each API server and worker keeps one listening connection that wakes its EventBus, and reconnects when it drops. Polling stays as the fallback and for SQLite. | build: the step was optional; it cuts the wait between processes from up to 250 ms to about 2 ms |

## Phase 4 (answers to the Phase 3 report's questions)

| # | Question | Decision | Who |
|---|---|---|---|
| 4.1 | Phase 4 "Done when" | The proposal in decision 3.13: a **Research assistant** template (Deep Agent + Helpers + to-do list + files) passes a 10-case Test Set, and Code steps can't escape their sandbox (escape tests) | owner: "OK go on" to the recommended answers |
| 4.2 | Sandbox backend for Code steps | **Docker** by default, with **gVisor** (`runsc`) where it is installed; hosted sandboxes (E2B, Modal) later as plugins. Must work with Docker Desktop on Windows too (R.4). | owner: same |
| 4.3 | The describe-it copilot's model | **Any model the user has a key for**; with no key, the stand-in AI shows a canned example | owner: same |
| 4.4 | Where a Deep Agent's files live | In the agent's **checkpointed state**, saved to a Flow Data field (`files`), not the LangGraph store | build: they are kept with every Save Point, survive a crash (tested) and rewind with time travel; later steps can read them |
| 4.5 | A shell for Deep Agents | **No `execute` tool**; running code belongs in Code steps, which run in the sandbox | build: one sandboxed way to run code |
| 4.6 | Skills in exports | The skills a flow uses are written into its generated code, so an export has them; the workspace `skills/` folder is over the built-in skills (a copy replaces one) | build: exported code must run on its own |
| 4.7 | Agent team patterns | Templates made of **ordinary steps** (AI Decision loop, Jump, For Each), which the step library can insert; no "team" step type | build: they stay editable and export to plain LangGraph |
| 4.8 | How the copilot proposes | The model writes the **whole flow** as YAML; it is checked (errors go back to the model twice at most) and turned into separate changes; existing steps keep their place and later steps move right to make room | build: the checks see the result, and the person reviews plain changes |
| 4.9 | "Fix this" on problems | **Ask the copilot** is on problems without a fix of their own | build: an exact one-click fix beats a model's guess |
| 4.10 | Version | 0.4.0 at the end of Phase 4 (Python package, web app, client, Compose image tags) | build |

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
| 3.10 | Where documents are ingested | In the API server's background tasks, not on the job queue; a restart marks unfinished documents failed with a clear message | build: the upload is in the API process and ingestion isn't a run; replaced by 5.8 |
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
| 2.7 | Event streaming | The API tails run events from the database (polling plus an in-process wake-up), not LISTEN/NOTIFY | build: the same on SQLite and Postgres, and survives API restarts; Postgres wake-ups added in 5.9 |
| 2.8 | Side effects | POST/PUT/PATCH/DELETE requests and Code steps marked "at most once" record their result in the LangGraph store under a stable key, and send it as `Idempotency-Key` | build: needed for the "no duplicate side effects" Done-when |
| 2.9 | Schema migrations | `create_all` plus a schema version check; Alembic arrives with the first schema change | build; replaced by 5.7 |
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
