# Status

_Last updated: 2026-10-02 (end of Phase 3, waiting for review)._

## In one paragraph

Phases 0 to 3 are built, tested and reported, and each met its "Done when". Phase 3 (Agents and
knowledge) ended with its [report](../phases/phase-3.md) and three questions for Phase 4; work
is **stopped for review**, as the build rules say. All work is committed on the branch
`claude/tender-fermat-4kj4k6`. **Pushing to GitHub is blocked:** the Claude GitHub App has no
access to `Shubham-wiz/EasyChain` (HTTP 403), so the commits exist only in the working copy that
made them (and in the git bundle handed to the owner) until access is granted and the branch is
pushed.

## Phases

| Phase | Scope | State | Report |
|---|---|---|---|
| 0. Foundations | Monorepo, flow spec + JSON Schema, compiler, runtime, CLI, API server | Done | [phase-0-1.md](../phases/phase-0-1.md) |
| 1. Visual MVP | Canvas, step library, inspector, run panel, chat, export, templates | Done | [phase-0-1.md](../phases/phase-0-1.md) |
| 2. Real runtime | Flow Data, loops, parallel joins, For Each, Sub-flows, Jump, Ask a Human, Inbox, Save Points, time travel, workers, triggers, notifications | Done | [phase-2.md](../phases/phase-2.md) |
| 3. Agents and knowledge | Agent step + add-ons, MCP, OpenAPI import, structured output, Knowledge Base, memory, all providers | Done | [phase-3.md](../phases/phase-3.md) |
| 4. Autopilot and teams | Deep Agents, Helpers, Skills, sandboxes, multi-agent patterns, describe-it copilot | **Next, after review** | plan in [phase-3.md](../phases/phase-3.md#phase-4-plan-autopilot-and-teams) |
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
| `8082c15` | Handover: Phase 3 progress, decisions and lessons |
| `fd9f701` | Web: Agent tools on the canvas, Knowledge page, MCP and API import |
| `7ba21d7` | Web tests for Phase 3, and fixes they found |
| `6697af7` | Phase 3 docs, report and handover; version 0.3.0 |

`git log --oneline` is the authoritative list. This table is updated as phases land.

## Test results at the end of Phase 3

| Suite | Result |
|---|---|
| Python (`make test-python`) | 320 passed, none skipped (Postgres and pgvector available locally) |
| Web unit (vitest) | 32 passed |
| Client package (vitest) | 4 passed |
| Playwright e2e | 31 passed; the screenshot spec runs only with `SCREENSHOTS=1` |
| Template Test Sets | Support bot over docs 12/12, SQL analyst 10/10, and all six earlier templates pass (`tests/test_templates.py`) |
| Lint | ruff check + format clean; tsc clean for web and client |

## Waiting on the owner

The Phase 3 report asks three questions (also in `state.yaml` under `open_questions`):

1. Phase 4's "Done when". The original brief isn't in the repo; a proposal is in the report
   (decision 3.13).
2. The sandbox backend for Code steps: Docker/gVisor locally, or a hosted sandbox such as E2B.
3. Which model the describe-it copilot uses.

## Next (after review)

The full plan is in [docs/ROADMAP.md](../ROADMAP.md).

1. **Step 0:** the owner pushes the code, and the first CI run on GitHub is made green.
2. **Phase 4**, in this order: sandboxes for Code steps, the Deep Agent step, Helpers, Skills,
   multi-agent patterns, the describe-it copilot, then the Research assistant and Inbox triage
   templates.

## GitHub

- **Pushed.** The branch `claude/tender-fermat-4kj4k6` is on GitHub with its full history. It
  was the first branch pushed to the empty repo, so it is currently the default branch.
  - Earlier pushes failed with HTTP 403 because the Claude GitHub App hadn't been given access
    to this repo.
  - The owner fixed that in the app's repository access settings. If it ever comes back, see
    lessons.md.
- **CI has not run yet.** `.github/workflows/ci.yml` runs on pushes to `main` and on pull
  requests only. To start it, either create `main` from this branch or open a pull request.
  That is the owner's call.

## Known gaps carried forward

These are listed in full in the phase reports. The ones that matter for the next phases:

- Code steps run unsandboxed, in the worker (sandboxing is the first Phase 4 item). Agents can
  call Code tools, so keep the server on localhost and use approvals. There is no login (Phase 5).
- Knowledge Base documents are ingested in the API server, not on the job queue; no OCR.
- Schema migrations: there is one schema version, created with `create_all`. Add Alembic with
  the first schema change.
- Event streaming polls the database (50–250 ms between processes).
- `docker compose up` was not run end to end in the build sandbox (Docker Hub rate limits). CI
  runs the full Compose stack, now on `pgvector/pgvector:pg16`.
- Real models are never called in CI; agent behaviour with real models should be checked by
  hand before a release.
