# Status

_Last updated: 2026-10-24 (Phase 5: 5.8, 5.6 and 5.1 are done: sign-in, single sign-on, people, roles, keys, a role-aware web app, the audit log, the egress allow-list, workspaces, and Test Sets edited and run in the app with every kind of check; 5.2, Test Runs and baselines, is next)._

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

All phases are summarised in [report.md](../report.md). **Phase 5 (Platform) has started**: the
owner accepted the recommended answers to the Phase 4 report's questions (decisions 5.1–5.5).
Every step is marked in the README's [build tracker](../../README.md#build-tracker), with known
issues under *Fix later* (decision 5.6).

## Pick up here

1. **Set up:** clone, `pnpm run setup`, then `pnpm lint` and `pnpm test` (Windows, macOS or
   Linux). Read [AGENTS.md](../../AGENTS.md), this page, [decisions.md](decisions.md) and the
   [Phase 4 report](../phases/phase-4.md).
2. **CI** runs on every push to `main`. If a run fails, its annotations name the failing tests;
   they are readable without admin rights:
   `curl -s https://api.github.com/repos/Shubham-wiz/EasyChain/check-runs/<job id>/annotations`
   (job ids: `…/actions/runs/<run id>/jobs`).
3. **Phase 5** follows [ROADMAP.md](../ROADMAP.md#phase-5-platform) in this order: 5.8 (migrations,
   Knowledge Base ingestion on the queue, optional LISTEN/NOTIFY), then 5.6, 5.1, 5.2, 5.3, 5.4,
   5.5 and 5.7. The README's build tracker shows which steps are done.
4. **One review item is left on purpose** ([review.md](review.md)): before a release, run the
   agent templates' Test Sets with a real model (`--real-model`). The address filter for
   server-side fetches is done (5.6f).

## Phases

| Phase | Scope | State | Report |
|---|---|---|---|
| 0. Foundations | Monorepo, flow spec + JSON Schema, compiler, runtime, CLI, API server | Done | [phase-0-1.md](../phases/phase-0-1.md) |
| 1. Visual MVP | Canvas, step library, inspector, run panel, chat, export, templates | Done | [phase-0-1.md](../phases/phase-0-1.md) |
| 2. Real runtime | Flow Data, loops, parallel joins, For Each, Sub-flows, Jump, Ask a Human, Inbox, Save Points, time travel, workers, triggers, notifications | Done | [phase-2.md](../phases/phase-2.md) |
| 3. Agents and knowledge | Agent step + add-ons, MCP, OpenAPI import, structured output, Knowledge Base, memory, all providers | Done | [phase-3.md](../phases/phase-3.md) |
| Review fixes | Windows support; the findings of the 2026-10-04 review | Done (two items scheduled elsewhere, see review.md) | [review.md](review.md) |
| 4. Autopilot and teams | Deep Agents, Helpers, Skills, sandboxes, multi-agent patterns, describe-it copilot | Done | [phase-4.md](../phases/phase-4.md) |
| 5. Platform | Test Sets and Checks, Test Runs, CI gate, dashboards, model gateway, Publish, environments, roles, SSO | **In progress** (see the README's build tracker) | plan in [ROADMAP.md](../ROADMAP.md#phase-5-platform) |
| 6. Ecosystem | LangGraph.js export, custom module registry, import, collaboration, prompt optimisation, Helm | Not started | |

`git log --oneline` lists every commit.

## Tests

| Suite | Result (2026-10-21, end of Phase 4) |
|---|---|
| Python on Linux (`pnpm test`, and CI) | 606 pass (2026-10-24, 5.1), with SQLite, Postgres (+ pgvector in CI) and Docker (the sandbox escape tests; skipped without Docker); compiler and steps coverage 94% |
| Python on Windows 11 (CI) | all pass |
| Web unit (vitest) | 106 passed |
| Client package (vitest) | 13 passed |
| Playwright e2e | 44 passed (Linux), three on a second server with sign-in on (sign-in, each role, single sign-on), one making and switching workspaces, one writing and running a Test Set case; the screenshot spec runs only with `SCREENSHOTS=1` |
| Template Test Sets | all 13 pass (`tests/test_templates.py`), with the stand-in AI and scripted turns; research-assistant 10/10, inbox-triage 10/10 |
| Lint | ruff check + format clean; tsc clean for web and client |

CI jobs: Python (Linux, Postgres + pgvector), Web, End-to-end (Playwright), Docker image and
Compose, and **Windows** (Python suite, CLI and worker, web checks, the app serving its page).

## Next

1. ~~**5.8 Platform groundwork**~~ (done): database migrations (Alembic, `server/migrations/`),
   Knowledge Base documents read by workers from the job queue, and Postgres LISTEN/NOTIFY
   (`server/listen.py`).
2. **5.6 Users, roles, SSO, audit:**
   - Done: ~~5.6a, sign-in, people, roles on every endpoint and keys for scripts~~,
     ~~5.6b, the role-aware web app~~ ([people.md](../people.md)), and ~~5.6c, the audit
     log~~ ([audit.md](../audit.md)).
   - Done: ~~5.6f, the egress allow-list~~ ([network.md](../network.md)).
   - Done: ~~5.6e, single sign-on~~ ([sso.md](../sso.md)), tested against
     `easychain.testing.oidc_provider`.
   - Done: ~~5.6d, workspaces and per-workspace vaults~~ ([workspaces.md](../workspaces.md),
     decision 5.15). Secrets reach runs through a per-run layer over `os.environ`
     (`scoped_env.py`), which 5.4's environments will reuse (decision 5.13).
3. ~~**5.1, Test Sets editor and Checks**~~ (done): the Tests tab, `checks.py` (values, JSON
   schema, the run, the order of tool calls, budgets, the AI judge), and the Test Sets API
   ([testing.md](../testing.md), decision 5.16).
4. **Next: 5.2, Test Runs and baselines**, then 5.3 (CI gate), 5.4–5.5 (environments,
   Publish, model gateway) and 5.7 (dashboards and tracing).

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
- Sign-in and roles are in (5.6a and 5.6b, [people.md](../people.md)). Sign-in is on when the
  server answers the network (Docker), and off for `easychain dev` on this computer. The web app
  shows each role what it can do. The audit log ([audit.md](../audit.md)) records every change.
  On a shared server, flows reach internal addresses only when an Owner allows them
  ([network.md](../network.md)). People can sign in with an OpenID Connect provider
  ([sso.md](../sso.md)). One server holds several workspaces, each with its own flows, runs,
  secrets and people ([workspaces.md](../workspaces.md)); without the sandbox, a Code step could
  still read another workspace's files (README Fix later F30).
- Knowledge Base documents are read by workers from the job queue (5.8); no OCR yet.
- Schema migrations are in place (Alembic, 5.8); every schema change needs one
  (`python/src/easychain/server/migrations/`).
- Between processes, events and jobs wake each other at once on Postgres (LISTEN/NOTIFY, 5.8); SQLite polls (up to 0.25 s).
- Real models are never called in CI. The template Test Sets run with the stand-in AI and
  scripted turns, so agent behaviour with real models must be checked by hand before a release.
