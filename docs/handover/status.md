# Status

_Last updated: 2026-10-02 (Phase 3 in progress)._

## In one paragraph

Phases 0, 1 and 2 are built, tested and reported. Each met its "Done when". The owner said
"continue", which starts **Phase 3 (Agents and knowledge)**, using the recommended answers to the
three Phase 3 questions (see [decisions.md](decisions.md)). All work is committed on the branch
`claude/tender-fermat-4kj4k6`. **Pushing to GitHub is blocked:** the Claude GitHub App has no
access to `Shubham-wiz/EasyChain` (HTTP 403), so the commits exist only in the working copy that
made them, until access is granted and the branch is pushed.

## Phases

| Phase | Scope | State | Report |
|---|---|---|---|
| 0. Foundations | Monorepo, flow spec + JSON Schema, compiler, runtime, CLI, API server | Done | [phase-0-1.md](../phases/phase-0-1.md) |
| 1. Visual MVP | Canvas, step library, inspector, run panel, chat, export, templates | Done | [phase-0-1.md](../phases/phase-0-1.md) |
| 2. Real runtime | Flow Data, loops, parallel joins, For Each, Sub-flows, Jump, Ask a Human, Inbox, Save Points, time travel, workers, triggers, notifications | Done | [phase-2.md](../phases/phase-2.md) |
| 3. Agents and knowledge | Agent step + add-ons, MCP, OpenAPI import, structured output, Knowledge Base, memory, all providers | **In progress** | (phase-3.md when done) |
| 4. Autopilot and teams | Deep Agents, Helpers, Skills, sandboxes, multi-agent patterns, describe-it copilot | Not started | |
| 5. Platform | Test Sets and Checks, Test Runs, CI gate, dashboards, model gateway, Publish, environments, roles, SSO | Not started | |
| 6. Ecosystem | LangGraph.js export, custom module registry, import, collaboration, prompt optimisation, Helm | Not started | |

## Commits on `claude/tender-fermat-4kj4k6`

| Commit | What |
|---|---|
| `dbbdb46` | Phase 0: flow spec, compiler, runtime, CLI and API server |
| `0c87fc4` | Phase 1: visual editor (React Flow canvas, inspector, run panel) with e2e tests |
| `472ea32` | Packaging, CI, performance and accessibility checks, docs and phase report |
| `c27b270` | Phase 2 compiler and runtime: For Each, Ask a Human, Jump, Sub-flows, run policies |
| `ea99268` | Phase 2 durability: run database, job queue, workers, Inbox, triggers and notifications |
| `2904329` | Phase 2 web app: Ask a Human, Inbox, Save Points, breakpoints, triggers, Flow Data panel |
| `a176712` | Phase 2 packaging, docs and report: client package, Compose with Postgres and workers |
| `48afafb` | Handover pack: AGENTS.md, CLAUDE.md, docs/handover |
| `09c1d47` | Phase 3 groundwork: stand-in tool calling, offline embeddings, more providers |
| `16f9dda` | Agent step and structured replies |
| `06608d2` | Knowledge Bases: ingestion, hybrid search, citations, search step |
| `513e665` | Memory, Database query, MCP tools and OpenAPI import |
| `09faba5` | Support bot over docs and SQL analyst templates, with passing Test Sets |

`git log --oneline` is the authoritative list. This table is updated as phases land.

## Test results at the end of Phase 2

| Suite | Result |
|---|---|
| Python (`make test-python`) | 259 passed. Compiler and steps coverage 94% (CI floor 90%). |
| Web unit (vitest) | 19 passed |
| Client package (vitest) | 4 passed |
| Playwright e2e | 23 passed, 1 skipped (the screenshot spec runs only with `SCREENSHOTS=1`) |
| Lint | ruff check + format clean; tsc clean for web and client; JSON Schema up to date |
| Durability | Worker SIGKILL takeover and day-later approval: passed 18 repeated runs on SQLite and Postgres |

## Phase 3 progress

The "Done when" is met offline: the **Support bot over docs** template passes its Test Set
(12/12) and the **SQL analyst** passes its own (10/10), with the stand-in AI and no API key. In
the SQL analyst set, only the SQL a model would write is scripted. Every template's Test Set runs
in `tests/test_templates.py`. 317 Python tests pass.

| Piece | State | Where |
|---|---|---|
| Stand-in AI calls tools, fills structured answers, answers from cited passages; scripted turns for Test Sets | Done | `runtime/standin.py` |
| Fake OpenAI server: tool calls, JSON replies, embeddings, scripts | Done | `testing/fake_openai.py` |
| Providers: Gemini, Vertex AI, Bedrock, Azure OpenAI, Mistral, Groq, Together, Fireworks, OpenRouter, DeepSeek, xAI (pip extras) | Done | `providers.py`, `runtime/gateway.py` |
| Structured replies (AI Model and Agent): typed fields, choices, lists, nested objects, retries, spread into Flow Data | Done | `compiler/schema_code.py` |
| Agent step (`create_agent`), steps as tools, 13 add-ons as middleware, tool approval in the Inbox, tool-call trace | Done | `steps/agent.py`, `runtime/runner.py` |
| Knowledge Bases: PDF, Word, HTML, Markdown, CSV and text; chunk preview; pgvector or SQLite; hybrid search; re-ranking; citations; search step; API | Done | `knowledge/`, `steps/knowledge.py`, `server/knowledge_api.py` |
| Memory step (remember, recall, trim, summarise) and the agent memory add-on | Done | `knowledge/memory.py`, `steps/memory.py` |
| Database query step (read-only by transaction; schema mode for agents) | Done | `integrations/sql.py`, `steps/sql.py` |
| MCP: servers in Settings (stdio only from an approved list), agent MCP tools, MCP tool step | Done | `integrations/mcp.py`, `steps/mcp.py`, `server/integrations_api.py` |
| OpenAPI import into typed Web request steps | Done | `integrations/openapi.py` |
| Templates: Support bot over docs, SQL analyst, with sample data | Done | `templates/` |
| **Web UI** for all of the above | **Next** | `apps/web` |
| Docs, Phase 3 report, e2e tests | After the UI | `docs/` |

## Next

1. The web app for Phase 3:
   - Agent inspector, with tool connections drawn on the canvas, Add-ons and tool calls in the trace.
   - Structured output builder.
   - Knowledge page: bases, uploads, chunk preview, search.
   - Memory, Database query and MCP tool forms.
   - MCP servers in Settings, the OpenAPI import dialog, the new providers in the model picker.
   - Citations in the run panel.
2. Playwright journeys for the new screens, with axe scans.
3. Docs: step pages, Knowledge Bases, agents, MCP and OpenAPI. Then the Phase 3 report.

## Blocked

- **Push to GitHub** (HTTP 403). Fix: connect GitHub at https://claude.ai/connect-github and
  install the Claude GitHub App on `Shubham-wiz/EasyChain`. Then run
  `git push -u origin claude/tender-fermat-4kj4k6`.

## Known gaps carried forward

These are listed in full in the phase reports. The ones that matter for the next phases:

- Code steps run unsandboxed, in the worker (sandboxing is Phase 4). There is no login (Phase 5).
- Schema migrations: there is one schema version, created with `create_all`. Add Alembic with
  the first schema change.
- Event streaming polls the database (50–250 ms between processes).
- `docker compose up` was not run end to end in the build sandbox, because Docker Hub rate
  limits blocked the image pulls. The same containers were tested against a host Postgres, and
  CI runs the full Compose stack.
