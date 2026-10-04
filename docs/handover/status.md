# Status

_Last updated: 2026-10-04 (after Phase 3: Windows support and fixes from the code review)._

## In one paragraph

Phases 0 to 3 are built, tested and reported, and each met its "Done when". The first CI run on
GitHub passed. A full code review ([review.md](review.md)) followed, and the owner asked for two
things before Phase 4 (decisions R.4, R.5): Easy Chain must run on **Windows as well as Linux**,
and the review's serious findings get fixed first. Windows support is done (CI now tests both),
and the data-loss and security fixes are being worked through; review.md tracks which are done.
The Phase 4 questions are answered (decisions 4.1–4.3), so Phase 4 starts after the review fixes.

## Pick up here

_Written 2026-10-04 so work can carry on from another machine or session._

1. **Set up:** clone, `pnpm run setup`, then `pnpm lint` and `pnpm test` (Windows, macOS or
   Linux). Read [AGENTS.md](../../AGENTS.md), this page, [review.md](review.md) and
   [decisions.md](decisions.md).
2. **CI.** Check the latest run on GitHub (Actions). Two jobs were red and are fixed in the
   last commit; if either is still red, its annotations name the failing tests:
   - **End-to-end (Playwright)**: the accessibility checks measured colours during the
     theme-switch animation; they now wait for animations that end (spinners never do).
     All 31 journeys pass locally on Windows too.
   - **Windows**: `initdb` can't create a throwaway Postgres on GitHub's Windows runner, so the
     job starts the runner's own Postgres service and sets `EASYCHAIN_TEST_POSTGRES_URL`.
   Annotations are readable without admin rights:
   `curl -s https://api.github.com/repos/Shubham-wiz/EasyChain/check-runs/<job id>/annotations`
   (job ids: `…/actions/runs/<run id>/jobs`).
3. **Then** the rest of [review.md](review.md) (everything under "Other findings"), most serious
   first, each fix with a test.
4. **Then Phase 4** ([ROADMAP.md](../ROADMAP.md#phase-4-autopilot-and-teams)); its questions are
   answered (decisions 4.1–4.3).
5. **For the owner:** delete the old branch: `git push origin --delete claude/tender-fermat-4kj4k6`
   (decision R.3).

## Phases

| Phase | Scope | State | Report |
|---|---|---|---|
| 0. Foundations | Monorepo, flow spec + JSON Schema, compiler, runtime, CLI, API server | Done | [phase-0-1.md](../phases/phase-0-1.md) |
| 1. Visual MVP | Canvas, step library, inspector, run panel, chat, export, templates | Done | [phase-0-1.md](../phases/phase-0-1.md) |
| 2. Real runtime | Flow Data, loops, parallel joins, For Each, Sub-flows, Jump, Ask a Human, Inbox, Save Points, time travel, workers, triggers, notifications | Done | [phase-2.md](../phases/phase-2.md) |
| 3. Agents and knowledge | Agent step + add-ons, MCP, OpenAPI import, structured output, Knowledge Base, memory, all providers | Done | [phase-3.md](../phases/phase-3.md) |
| Review fixes | Windows support; data-loss and security findings of the 2026-10-04 review | **In progress** | [review.md](review.md) |
| 4. Autopilot and teams | Deep Agents, Helpers, Skills, sandboxes, multi-agent patterns, describe-it copilot | Next (questions answered: decisions 4.1–4.3) | plan in [ROADMAP.md](../ROADMAP.md#phase-4-autopilot-and-teams) |
| 5. Platform | Test Sets and Checks, Test Runs, CI gate, dashboards, model gateway, Publish, environments, roles, SSO | Not started | |
| 6. Ecosystem | LangGraph.js export, custom module registry, import, collaboration, prompt optimisation, Helm | Not started | |

`git log --oneline` lists every commit.

## Tests

| Suite | Result (2026-10-04) |
|---|---|
| Python on Windows 11 (`pnpm test`) | all pass, with SQLite and a throwaway Postgres 18 (no pgvector) |
| Python on Linux (CI) | all pass, with SQLite and Postgres 16 + pgvector |
| Web unit (vitest) | 36 passed (Linux CI and Windows) |
| Client package (vitest) | 4 passed |
| Playwright e2e | 31 passed in CI (Linux); the screenshot spec runs only with `SCREENSHOTS=1` |
| Template Test Sets | all 8 pass (`tests/test_templates.py`), with the stand-in AI and scripted turns |
| Lint | ruff check + format clean; tsc clean for web and client |

CI jobs: Python (Linux, Postgres + pgvector), Web, End-to-end (Playwright), Docker image and
Compose, and **Windows** (Python suite, CLI and worker, web checks, the app serving its page).

## Next

1. Work through the rest of [review.md](review.md), most serious first (the "fix first" list
   is done).
2. Then **Phase 4**, in this order: sandboxes for Code steps (Docker, gVisor where installed;
   Docker Desktop on Windows), the Deep Agent step, Helpers, Skills, multi-agent patterns, the
   describe-it copilot, then the Research assistant and Inbox triage templates.

## GitHub

- `main` is on GitHub with the full history; CI runs on every push to `main` and on pull
  requests.
- The first branch, `claude/tender-fermat-4kj4k6`, still has the AI-attribution trailers. Its
  deletion is approved (decision R.3); the owner runs
  `git push origin --delete claude/tender-fermat-4kj4k6`. Check with `git ls-remote origin`.
- Earlier pushes failed with HTTP 403 until the Claude GitHub App was given access to this repo;
  see lessons.md if it comes back.

## Known gaps carried forward

These are listed in full in the phase reports and review.md. The ones that matter next:

- Code steps run unsandboxed, in the worker (sandboxing is the first Phase 4 item). Agents can
  call Code tools, so keep the server on localhost and use approvals. There is no login (Phase 5);
  the API now refuses other sites' pages (Host and Origin checks), but anything on this machine
  can still call it.
- Knowledge Base documents are ingested in the API server, not on the job queue; no OCR.
- Schema migrations: there is one schema version, created with `create_all`. Add Alembic with
  the first schema change.
- Event streaming polls the database (50–250 ms between processes).
- Real models are never called in CI. The template Test Sets run with the stand-in AI and
  scripted turns, so agent behaviour with real models must be checked by hand before a release.
