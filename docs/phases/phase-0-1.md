# Phases 0 and 1: report

**Status: both phases meet their "Done when".** Work has stopped here for review before
Phase 2, as the build prompt asks.

## Done when

| Phase | Done when | Evidence |
|---|---|---|
| 0. Foundations | One command starts the app | `docker compose up` (tested: health check, web app, a chat run inside the container) or `make dev` |
| 0. Foundations | A 3-step flow written in YAML compiles to LangGraph code and runs from the CLI | `easychain run examples/hello.flow.yaml -i question=… --stand-in` (test: `test_phase0_example_flow_runs_from_cli`) |
| 1. Visual MVP | A new user builds and runs a summarise-this-URL flow in under 5 minutes without docs | Playwright `core-journey.spec.ts` builds it from a blank canvas by dragging steps, filling forms and connecting handles, then runs it: about 6 s scripted, with a 5-minute ceiling asserted. A scripted run is a proxy; a real five-person usability test is still worth doing (see gaps). |
| 1. Visual MVP | The exported Python runs unchanged | The same e2e test downloads the zip and runs `my_flow.py` with plain Python against the real `langchain-openai` package, with the `easychain` package hidden. `test_exported_python_runs_unchanged`, `test_exported_chat_runs_in_the_terminal` and `test_exported_decision_flow_runs` do the same in pytest. |

## What was built

**Phase 0**

- Monorepo: `python/` (uv, Python 3.12) and `apps/web/` (pnpm), with a Makefile, Dockerfile,
  `docker-compose.yml`, GitHub Actions CI (lint, tests, compiler coverage gate, schema freshness,
  web build, Playwright, Docker smoke test), and the Apache 2.0 licence.
- Flow spec v1: Pydantic models, diff-friendly YAML I/O, readable validation errors, published
  JSON Schema (`spec/flow.schema.json`, checked in CI).
- Compiler: analysis, checks before a run, and code generation for idiomatic LangGraph modules.
  Golden tests; generated code is lint-clean; 95% coverage on compiler code.
- Runtime with in-memory Save Points; CLI commands `new`, `validate`, `compile`, `export`, `run`,
  `test`, `schema`, `templates` and `dev`.
- `ARCHITECTURE.md`.

**Phase 1**

- Canvas: drag and drop from a searchable Step library (or click to add after the selected
  step), connections checked as you draw them with the reason shown when refused, quick-add when
  a connection is dropped on empty canvas, Decision diamonds with labelled exits, sticky notes,
  auto-layout, minimap, zoom controls, snap, undo/redo (typing groups into one step),
  copy/paste across flows, and keyboard shortcuts (`⌘/Ctrl+Z`, `⇧+⌘/Ctrl+Z`, `⌘/Ctrl+C/V`,
  `Delete`, `/` to search, `⌘/Ctrl+Enter` to run, `Esc`).
- Steps: Input, Output, AI Model, Instructions, Action (Web request and Code), Decision (rules,
  safe expressions, AI classification).
- Providers: OpenAI, Anthropic, Ollama, with key checks, cost estimates and a **stand-in AI**
  for keyless runs.
- Inspector forms generated from the step catalog, with tooltips, examples, More options, Pro
  mode (LangChain terms, step ids, expressions) and a per-step Code tab.
- Run: an auto-generated form or a streaming chat panel; live glow, token streaming, flowing
  connections and the exit taken; per-step time, token and cost badges; a trace with
  inputs and outputs; plain-language errors with one-click fixes; run history with Run Replay.
- Export: a zip with the module, `requirements.txt`, `langgraph.json`, `.env.example`, a README
  and the flow file. Also `.py` and `.yaml` downloads, and copy to clipboard.
- Home: a templates gallery (Try it / Use template, with key status), flows list, open a flow
  file. Light and dark themes. Settings: API keys and secrets (encrypted, write-only).
- Templates: Summarise a URL, Chat assistant, Reply to customer feedback (AI Decision), Smart
  summary length (Code and rules Decision). Each has a Test Set of 10 to 12 cases, and all pass.

**Tests:** 170 Python tests, 14 web unit tests and 11 Playwright journeys, all passing.
Performance: about 1 ms per step of overhead on top of LangGraph (budget 10 ms); a 300-step
flow checks in about 40 ms and compiles in about 30 ms; on the canvas it opens in about 0.7 s and
pans at about 60 fps. Accessibility: the axe-core WCAG 2.2 AA scan reports no serious or critical
issues on the home page or the editor, in both themes.

## Demo script (about 5 minutes)

1. `docker compose up` and open http://localhost:8000. (Or `make install && make dev` and open
   http://localhost:5173.)
2. **Try it** on *Reply to customer feedback*. With no key, the stand-in AI switches on
   automatically. Watch the Decision light up **Complaint**, the Instructions and AI Model steps
   glow in turn, and the reply stream in. Click a step in the trace to see what it read and
   saved.
3. Open **Settings → API keys** and paste an OpenAI key, switch off the stand-in AI, and run
   again: real answers, with tokens and cost on each step.
4. Back to all flows → **New blank flow**:
   - Select Input and rename its field to `url`.
   - Drag in **Web request**: URL `{url}`, save as `page`.
   - Drag in **Instructions**: message `Summarise this page in three bullet points: {page}`.
     Misspell it as `{pgae}` first to see "Did you mean `page`?" and the one-click fix.
   - Drag in **AI Model**: save the reply as `summary`.
   - Connect Input → Web request → Instructions → AI Model → Output, and tick `summary` on the
     Output step.
   - **Run** with a real URL. Then try a URL that 404s to see the error pinned to the Web request
     step.
5. Switch to **Pro**: technical names appear in grey. Open the AI Model's **Code** tab.
6. **Export** → **Download project (.zip)**, then:
   `pip install -r requirements.txt && OPENAI_API_KEY=… python my_flow.py '{"url": "https://…"}'`.
7. CLI: `easychain run examples/hello.flow.yaml -i question="Hi" --stand-in`.

## Known gaps and risks

- **Code steps are not sandboxed.** They run in the server process. Don't expose a Phase 1
  server to untrusted users. The default bind is 127.0.0.1, and compose publishes on localhost
  only. Sandboxing is Phase 4.
- **No authentication or multi-user support.** It is single-user and local. Roles, SSO, the audit
  log and per-workspace vaults are Phase 5.
- **Runs and Save Points are in memory.** They are lost on restart, and runs execute in the API
  process. Flows themselves are saved to disk. (Phase 2: Postgres, queue, workers.)
- **No Flow Data panel editing yet.** The panel lists fields, types and who sets them, but declaring
  fields and update rules is done in YAML for now. Parallel branches work but have no
  join-wait control. There are no For Each, Sub-flows, loop limits or Ask a Human steps (Phase 2).
- **The "under 5 minutes" target is shown by a scripted journey,** not by observing new users.
  A short usability test with 3 to 5 target users is recommended before Phase 2 UI work.
- **Canvas gaps:** no labelled frames and no version history or visual diff yet. Real-time
  collaboration is Phase 6. Phone and tablet layouts aren't tuned.
- **Model prices** are list prices for a few known models; other models show tokens but no cost.
- Only **OpenAI, Anthropic and Ollama** are supported; Phase 3 adds the rest.
- The **stand-in AI** is deliberately simple: it echoes and extracts, and picks Decision exits
  by keyword overlap.
- Not yet run against live provider APIs in CI (no keys in CI by design). The real
  `langchain-openai` client is exercised against the fake server. Anthropic and Ollama are covered
  by compile and lint tests and gateway checks, but have no live call test.

## Phase 2 plan: Real runtime

Goal ("Done when"): killing a worker mid-run and restarting resumes from the last step with
no duplicate side effects, and a paused approval can be resumed a day later.

1. **Storage.** Add Postgres (and pgvector, for later) to Docker Compose. Store flows, flow
   versions, runs and run events in Postgres (SQLAlchemy + Alembic). Save Points use
   `langgraph-checkpoint-postgres` (SQLite for local development, with a plugin interface).
   Keep "save as YAML in a folder" as a sync option for git users.
2. **Workers.** A Postgres-backed job queue (`SELECT … FOR UPDATE SKIP LOCKED`, so no Redis is
   needed yet), a worker process (`easychain worker`), per-flow concurrency limits, cancel and
   status. The API streams events from the run log (SSE resume via `Last-Event-ID`) so the UI
   survives reloads.
3. **Durability.** Resume from the last checkpoint on worker restart. Idempotency keys for Web
   request and other side-effecting actions (recorded per thread, step and attempt), so completed
   steps never re-run.
4. **Flow Data panel.** Add and edit fields with type and update rule (replace, append, merge,
   add, custom function in Pro), and mark fields as input, output or private.
5. **Graph features.**
   - Loops with a max-iterations guard (a counter field plus a check).
   - Parallel fan-out and join (deferred steps that wait for all branches).
   - **For Each** with a concurrency limit (LangGraph `Send`).
   - **Jump** (`Command(update, goto)`).
   - **Sub-flows** (a flow as a step, with shared or separate data; double-click to drill in).
   - Per-step retry policy, timeout and cache.
6. **Ask a Human.** The step uses `interrupt()`; pause-before and pause-after breakpoints work on
   any step. An **Inbox** lists paused runs with approve, edit, reject and free-text answers.
   Notifications go by webhook, then email and Slack.
7. **Time travel.** A Save Point timeline under each run. Open a checkpoint, edit its Flow Data,
   and fork a re-run from it.
8. **Streaming.** Expose every LangGraph stream mode (values, updates, messages, custom, debug),
   including inside sub-flows. Add a WebSocket transport and a small React hook for apps built
   on top.
9. **Triggers and background runs.** API call, webhook, schedule (cron), file upload, another
   flow; double-texting policies (reject, queue, interrupt, roll back); thread locking.
10. **Tests.** A kill-the-worker chaos test (the Done-when) and an approval resumed after a
    simulated day. Golden tests for every new construct. Playwright for the Inbox and time
    travel.

## Questions for you

1. **Storage for flows:** keep YAML-in-a-folder as the primary store (git-friendly), with
   Postgres for runs and versions? Or move flows into Postgres and offer git sync? I recommend
   the former for Phase 2.
2. **Queue:** a Postgres-backed queue first (one fewer service), and Redis Streams only if load
   needs it? Section 11 lists both as options.
3. **Code step safety before Phase 4:** is "local, single-user, trusted code" acceptable for now,
   or should Phase 2 add a basic subprocess sandbox (resource limits, no network) as a stopgap?
4. **Licence:** Apache 2.0 is in place, as suggested.
