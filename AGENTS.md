# Working on Easy Chain

This file is the entry point for anyone picking up the project: a coding agent (Claude Code,
Codex, Cursor, Aider and so on) or a new developer. It covers the rules of the build, how the
repo is laid out, the commands, and the traps already found. **Where the project stands now**
is in [docs/handover/status.md](docs/handover/status.md). Read that next.

## What Easy Chain is

An open-source, drag-and-drop builder for LLM apps and agents, for low-code builders. A flow is
drawn on a canvas and saved as a YAML **flow spec**. The compiler turns the spec into **plain
LangGraph Python**, and that same code runs when you press Run and is what you export. Easy
Chain does not reimplement an agent or graph engine.

## How the build works

The project follows a written build prompt that splits the work into **Phases 0 to 6**. Each
phase has a **"Done when"**. Its scope is summarised in
[docs/handover/build-prompt.md](docs/handover/build-prompt.md).

1. Don't start a phase until the previous one meets its "Done when".
2. Each phase ends with a report in `docs/phases/phase-N.md` with these sections: what was done
   (with the "Done when" evidence), a demo script, known gaps and risks, the plan for the next
   phase, and questions for the owner.
3. After the report, **stop for review**. The owner's answers go into
   [docs/handover/decisions.md](docs/handover/decisions.md).
4. Keep [docs/handover/status.md](docs/handover/status.md) and
   [docs/handover/state.yaml](docs/handover/state.yaml) current as work lands. They are the
   project's memory for the next person or agent.

## Repo map

```
python/                    the product: compiler, runtime, API server, workers, CLI (uv, Python 3.12)
  src/easychain/spec/      Pydantic models of the flow spec, YAML I/O, JSON Schema
  src/easychain/steps/     one handler per step type: form, reads/writes, checks, code emitter
  src/easychain/compiler/  analysis → validate → codegen, producing LangGraph Python source
  src/easychain/runtime/   load compiled modules, checkpointer/store resources, model gateway,
                           stand-in AI, stream_run (start, resume, continue, fork, breakpoints)
  src/easychain/server/    FastAPI app, run database, Hub, worker (queue, leases, Inbox,
                           triggers, notifications), cron, secrets vault
  src/easychain/templates/ template flows (*.flow.yaml) and their Test Sets (*.tests.yaml)
  src/easychain/testing/   fake OpenAI-compatible server used by tests and e2e
  tests/                   pytest; golden compiler cases in tests/golden/{cases,expected}
apps/web/                  React 19 + TypeScript + Vite + Zustand + React Flow editor
  e2e/                     Playwright journeys (run against the real app + fake model server)
packages/client/           @easychain/client: TypeScript client and React hook for the runs API
spec/flow.schema.json      published JSON Schema (generated: `make schema`)
docs/                      user docs (flow spec, steps, runs), phase reports, handover notes
examples/                  example flows
```

[ARCHITECTURE.md](ARCHITECTURE.md) explains the design, and its section 10 lists each place
where the build deliberately differs from the build prompt, and why.

## Commands

```bash
make install        # cd python && uv sync; pnpm install
make dev            # API :8000 + web dev server :5173
make test           # pytest (+coverage) + web unit tests + client tests
make lint           # ruff check + ruff format --check + tsc (web, client)
make e2e            # builds the web app, runs Playwright against the real app
make schema         # regenerate spec/flow.schema.json after changing spec/models.py
make golden         # regenerate compiler golden files; review the diff
```

Running parts of the suite:

```bash
cd python && uv run pytest tests/test_compiler_golden.py -q
cd python && uv run pytest tests/test_durability.py -q    # needs Postgres for the PG half
pnpm --filter @easychain/web test        # vitest
pnpm --filter @easychain/web e2e         # Playwright (build first: pnpm --filter @easychain/web build)
pnpm --filter @easychain/client test
```

[docs/handover/environment.md](docs/handover/environment.md) covers setting up a fresh machine,
including Postgres for the tests, the Playwright browser, and Docker notes.

## Rules of the codebase

- **Plain language in the product.** Use the build prompt's names in the UI, docs and errors:
  Flow, Step, Connection, Decision, Flow Data, AI Model, Instructions, Action, Save Point, Ask a
  Human, Inbox. Technical LangChain/LangGraph terms appear only in Pro mode and tooltips.
  Every error says what happened and what to do next. Where a fix is possible, it carries one.
- **The spec is the source of truth.** `spec/models.py` defines it. Defaults are left out of
  files, so changing a default means bumping the spec `version`. Step ids and field names match
  `^[a-z][a-z0-9_]*$`. A step id may not equal a Flow Data field, because LangGraph forbids a
  node and a state key sharing a name.
- **Generated code is a product.** Golden files pin it. After a compiler change, run
  `make golden` and read the diff. The exported code must run with no Easy Chain runtime.
- **Compiler coverage stays at or above 90%.** CI enforces this for `compiler/` and `steps/`.
- **Pinned versions.** LangGraph, LangChain and the provider packages are pinned exactly in
  `python/pyproject.toml` and locked in `uv.lock`. Upgrade them deliberately (ARCHITECTURE §9).
- **Tests never call a paid API.** Use the stand-in AI, or the fake OpenAI-compatible server
  (`python -m easychain.testing.fake_openai`).
- **Adding a step type** follows a checklist in [CONTRIBUTING.md](CONTRIBUTING.md): the model,
  the handler, a golden case, behaviour tests, checks, an icon, and a docs page.
- **Side effects run at most once.** Anything that changes the outside world goes through
  `run_once` with an idempotency key (see `compiler/helpers.py` and [docs/runs.md](docs/runs.md)).

## Traps already found

The full list is in [docs/handover/lessons.md](docs/handover/lessons.md). These are the ones
most likely to bite:

- `aupdate_state` on a fork needs `checkpoint_ns: ""` in the config.
- A blocking advisory lock around LangGraph's Postgres `setup()` deadlocks with
  `CREATE INDEX CONCURRENTLY`. Poll `pg_try_advisory_lock` and retry instead
  (`runtime/resources.py`).
- The worker must write the final `run_finished` event **after** creating Inbox items and
  updating the run's status. Clients wait on that event.
- Resuming from a checkpoint with `None` input passes a breakpoint at that exact point (this is
  LangGraph's behaviour). Later breakpoints still pause the run.
- SQLite runs in WAL mode with `BEGIN IMMEDIATE` for writes. Reads use a deferred `BEGIN`
  (the `easychain_read` execution option).
- Playwright locators must be exact (`{ exact: true }`). Several labels repeat ("Add",
  "Triggers", "email").
