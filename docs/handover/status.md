# Status

_Last updated: 2026-10-21 (Phase 4 in progress: the Code sandbox, the Deep Agent step, Helpers, Skills and the agent team patterns are done; the describe-it copilot is next)._

## In one paragraph

Phases 0 to 3 are built, tested and reported, and each met its "Done when". After Phase 3, a
full code review ([review.md](review.md)) was worked through, and Easy Chain now runs on
**Windows as well as Linux** (decisions R.4, R.5). **Phase 4 (Autopilot and teams) is in
progress**, with its questions answered (decisions 4.1–4.3). Done so far: the **Code step
sandbox** (Code steps run in a locked-down Docker container per call; escape tests prove they
can't get out), the **Deep Agent** step (`create_deep_agent`: a to-do list, files kept in Flow
Data and with every Save Point, approvals in the Inbox) and **Helpers** (sub-agents with their
own role, model and tools, nested in the trace with their own tokens and cost) and **Skills**
(a Skills library in the Agent Skills format, built into the generated code), and the three
**agent team patterns** (supervisor, handoffs, plan-and-execute) as templates made of ordinary
steps that the step library can insert. The describe-it copilot is next.

## Pick up here

_Updated 2026-10-09 so work can carry on from another machine or session._

1. **Set up:** clone, `pnpm run setup`, then `pnpm lint` and `pnpm test` (Windows, macOS or
   Linux). Read [AGENTS.md](../../AGENTS.md), this page, [review.md](review.md) and
   [decisions.md](decisions.md).
2. **CI** is green on Linux and Windows (all five jobs, from commit `ba6f12c`). If a run fails,
   its annotations name the failing tests; they are readable without admin rights:
   `curl -s https://api.github.com/repos/Shubham-wiz/EasyChain/check-runs/<job id>/annotations`
   (job ids: `…/actions/runs/<run id>/jobs`).
3. **The review is done** ([review.md](review.md)). Two items are left on purpose. The address
   filter for server-side fetches comes with the egress allow-list in Phase 5. Before a
   release, run the agent templates' Test Sets with a real model (`--real-model`).
4. **Phase 4** ([ROADMAP.md](../ROADMAP.md#phase-4-autopilot-and-teams)): 4.1 (the Code
   sandbox), 4.2 (the Deep Agent step, [docs](../steps/deep_agent.md)), 4.3 (Helpers,
   [docs](../steps/helper.md)), 4.4 (Skills, [docs](../skills.md)) and 4.5 (agent team
   patterns, [docs](../patterns.md)) are done. Carry on with 4.6, the describe-it copilot.

## Phases

| Phase | Scope | State | Report |
|---|---|---|---|
| 0. Foundations | Monorepo, flow spec + JSON Schema, compiler, runtime, CLI, API server | Done | [phase-0-1.md](../phases/phase-0-1.md) |
| 1. Visual MVP | Canvas, step library, inspector, run panel, chat, export, templates | Done | [phase-0-1.md](../phases/phase-0-1.md) |
| 2. Real runtime | Flow Data, loops, parallel joins, For Each, Sub-flows, Jump, Ask a Human, Inbox, Save Points, time travel, workers, triggers, notifications | Done | [phase-2.md](../phases/phase-2.md) |
| 3. Agents and knowledge | Agent step + add-ons, MCP, OpenAPI import, structured output, Knowledge Base, memory, all providers | Done | [phase-3.md](../phases/phase-3.md) |
| Review fixes | Windows support; the findings of the 2026-10-04 review | Done (two items scheduled elsewhere, see review.md) | [review.md](review.md) |
| 4. Autopilot and teams | Deep Agents, Helpers, Skills, sandboxes, multi-agent patterns, describe-it copilot | **In progress**: 4.1 Code sandbox ([docs/sandbox.md](../sandbox.md)), 4.2 Deep Agent, 4.3 Helpers, 4.4 Skills and 4.5 patterns done; 4.6 copilot next | plan in [ROADMAP.md](../ROADMAP.md#phase-4-autopilot-and-teams) |
| 5. Platform | Test Sets and Checks, Test Runs, CI gate, dashboards, model gateway, Publish, environments, roles, SSO | Not started | |
| 6. Ecosystem | LangGraph.js export, custom module registry, import, collaboration, prompt optimisation, Helm | Not started | |

`git log --oneline` lists every commit.

## Tests

| Suite | Result (2026-10-21) |
|---|---|
| Python on Linux (`pnpm test`, and CI) | 474 pass, with SQLite, Postgres (+ pgvector in CI) and Docker (the sandbox escape tests; skipped without Docker); compiler and steps coverage 93% |
| Python on Windows 11 (CI) | all pass |
| Web unit (vitest) | 84 passed |
| Client package (vitest) | 11 passed |
| Playwright e2e | 33 passed (Linux); the screenshot spec runs only with `SCREENSHOTS=1` |
| Template Test Sets | all 11 pass (`tests/test_templates.py`), with the stand-in AI and scripted turns |
| Lint | ruff check + format clean; tsc clean for web and client |

CI jobs: Python (Linux, Postgres + pgvector), Web, End-to-end (Playwright), Docker image and
Compose, and **Windows** (Python suite, CLI and worker, web checks, the app serving its page).

## Next

1. **Phase 4**, in this order: ~~sandboxes for Code steps~~ (done: `runtime/sandbox.py`,
   [docs/sandbox.md](../sandbox.md)), ~~the Deep Agent step~~ and ~~Helpers~~ (done:
   `steps/deep_agent.py`), ~~Skills~~ (done: `skills.py`, [docs/skills.md](../skills.md)),
   ~~multi-agent patterns~~ (done: three templates, [docs/patterns.md](../patterns.md)), the
   describe-it copilot, then the Research assistant and Inbox triage templates.

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
  otherwise). In Docker Compose, that takes the `docker-compose.sandbox.yml` overlay.
- There is no login (Phase 5). The API refuses other sites' pages (Host and Origin checks), but
  anything on this machine can still call it.
- Knowledge Base documents are ingested in the API server, not on the job queue; no OCR.
- Schema migrations: there is one schema version, created with `create_all`. Add Alembic with
  the first schema change.
- Event streaming polls the database (50–250 ms between processes).
- Real models are never called in CI. The template Test Sets run with the stand-in AI and
  scripted turns, so agent behaviour with real models must be checked by hand before a release.
