# Status

_Last updated: 2026-10-21 (Phase 4 is done and reported; waiting for the owner's review before Phase 5)._

## In one paragraph

Phases 0 to 4 are built, tested and reported, and each met its "Done when". After Phase 3, a
full code review ([review.md](review.md)) was worked through, and Easy Chain runs on **Windows
as well as Linux** (decisions R.4, R.5). **Phase 4 (Autopilot and teams)** added:

- the **Code step sandbox**: a locked-down Docker container per call, with escape tests;
- the **Deep Agent** step and its **Helpers**: a to-do list, files kept with every Save Point,
  and sub-agents nested in the trace with their own tokens and cost;
- **Skills**: a library in the Agent Skills format, built into the generated code;
- three **agent team patterns** made of ordinary steps;
- the **describe-it copilot**;
- the **Research assistant** and **Inbox triage** templates, each passing a 10-case Test Set.

The [Phase 4 report](../phases/phase-4.md) ends with five questions about Phase 5. **Work stops
here until the owner answers them.**

## Pick up here

1. **Set up:** clone, `pnpm run setup`, then `pnpm lint` and `pnpm test` (Windows, macOS or
   Linux). Read [AGENTS.md](../../AGENTS.md), this page, [decisions.md](decisions.md) and the
   [Phase 4 report](../phases/phase-4.md).
2. **CI** runs on every push to `main`. If a run fails, its annotations name the failing tests;
   they are readable without admin rights:
   `curl -s https://api.github.com/repos/Shubham-wiz/EasyChain/check-runs/<job id>/annotations`
   (job ids: `…/actions/runs/<run id>/jobs`).
3. **Waiting for the owner:** the Phase 4 report's
   [questions](../phases/phase-4.md#questions-for-you) on Phase 5's "Done when", single
   sign-on, publishing, LLM-as-judge checks and real-model checks. Record the answers in
   [decisions.md](decisions.md) as 5.x, then start Phase 5 with migrations (5.8), as
   [ROADMAP.md](../ROADMAP.md#phase-5-platform) orders it.
4. **Two review items are left on purpose** ([review.md](review.md)). The address filter for
   server-side fetches comes with the egress allow-list in Phase 5. Before a release, run the
   agent templates' Test Sets with a real model (`--real-model`).

## Phases

| Phase | Scope | State | Report |
|---|---|---|---|
| 0. Foundations | Monorepo, flow spec + JSON Schema, compiler, runtime, CLI, API server | Done | [phase-0-1.md](../phases/phase-0-1.md) |
| 1. Visual MVP | Canvas, step library, inspector, run panel, chat, export, templates | Done | [phase-0-1.md](../phases/phase-0-1.md) |
| 2. Real runtime | Flow Data, loops, parallel joins, For Each, Sub-flows, Jump, Ask a Human, Inbox, Save Points, time travel, workers, triggers, notifications | Done | [phase-2.md](../phases/phase-2.md) |
| 3. Agents and knowledge | Agent step + add-ons, MCP, OpenAPI import, structured output, Knowledge Base, memory, all providers | Done | [phase-3.md](../phases/phase-3.md) |
| Review fixes | Windows support; the findings of the 2026-10-04 review | Done (two items scheduled elsewhere, see review.md) | [review.md](review.md) |
| 4. Autopilot and teams | Deep Agents, Helpers, Skills, sandboxes, multi-agent patterns, describe-it copilot | Done | [phase-4.md](../phases/phase-4.md) |
| 5. Platform | Test Sets and Checks, Test Runs, CI gate, dashboards, model gateway, Publish, environments, roles, SSO | Next, after the owner's review | plan in [ROADMAP.md](../ROADMAP.md#phase-5-platform) |
| 6. Ecosystem | LangGraph.js export, custom module registry, import, collaboration, prompt optimisation, Helm | Not started | |

`git log --oneline` lists every commit.

## Tests

| Suite | Result (2026-10-21, end of Phase 4) |
|---|---|
| Python on Linux (`pnpm test`, and CI) | 513 pass, with SQLite, Postgres (+ pgvector in CI) and Docker (the sandbox escape tests; skipped without Docker); compiler and steps coverage 94% |
| Python on Windows 11 (CI) | all pass |
| Web unit (vitest) | 94 passed |
| Client package (vitest) | 11 passed |
| Playwright e2e | 39 passed (Linux); the screenshot spec runs only with `SCREENSHOTS=1` |
| Template Test Sets | all 13 pass (`tests/test_templates.py`), with the stand-in AI and scripted turns; research-assistant 10/10, inbox-triage 10/10 |
| Lint | ruff check + format clean; tsc clean for web and client |

CI jobs: Python (Linux, Postgres + pgvector), Web, End-to-end (Playwright), Docker image and
Compose, and **Windows** (Python suite, CLI and worker, web checks, the app serving its page).

## Next

1. The owner answers the [Phase 4 report's questions](../phases/phase-4.md#questions-for-you).
2. **Phase 5 (Platform)**, in the order in [ROADMAP.md](../ROADMAP.md#phase-5-platform):
   migrations, then users, roles, SSO and the audit log (with the egress allow-list), then Test
   Sets, Test Runs and the CI gate, then environments, Publish and the model gateway, then
   dashboards and tracing.

## GitHub

- `main` is on GitHub with the full history; CI runs on every push to `main` and on pull
  requests.
- The first branch, `claude/tender-fermat-4kj4k6`, which had the AI-attribution trailers, is
  deleted (decision R.3). Only `main` is left.
- Earlier pushes failed with HTTP 403 until the Claude GitHub App was given access to this repo;
  see lessons.md if it comes back.

## Known gaps carried forward

These are listed in full in the phase reports and review.md. The ones that matter next:

- Code steps run in the sandbox only where Docker is available (the check before a run warns
  otherwise). In Docker Compose, that takes the `docker-compose.sandbox.yml` overlay. Exported
  code runs Code steps in its own process.
- The copilot and the agent templates are tested with scripted model turns, not real models.
- There is no login (Phase 5). The API refuses other sites' pages (Host and Origin checks), but
  anything on this machine can still call it.
- Knowledge Base documents are ingested in the API server, not on the job queue; no OCR.
- Schema migrations: there is one schema version, created with `create_all`. Add Alembic with
  the first schema change.
- Event streaming polls the database (50–250 ms between processes).
- Real models are never called in CI. The template Test Sets run with the stand-in AI and
  scripted turns, so agent behaviour with real models must be checked by hand before a release.
