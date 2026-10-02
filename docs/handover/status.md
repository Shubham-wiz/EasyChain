# Status

_Last updated: 2026-10-02._

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
| (this) | Handover pack: AGENTS.md, CLAUDE.md, docs/handover |

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

## Next

Phase 3, following the plan in [phase-2.md](../phases/phase-2.md#phase-3-plan-agents-and-knowledge):

1. Agent step on LangChain `create_agent`, with tools from other steps and Agent Add-ons
   (middleware toggles).
2. Structured output builder.
3. Knowledge Base: upload and chunk, embeddings, pgvector or a local index, hybrid search,
   citations.
4. MCP client and OpenAPI import.
5. Memory (short- and long-term).
6. More providers.
7. Templates: **Support bot over docs** and **SQL analyst**, with 10-case Test Sets. Passing
   them is the "Done when".

The Phase 3 sections below are updated as work lands.

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
