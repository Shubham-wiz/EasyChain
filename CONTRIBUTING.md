# Contributing to Easy Chain

## Set up

```bash
make install        # uv sync (Python 3.12) + pnpm install
make dev            # API on :8000 + web dev server on http://localhost:5173
```

No API key is needed for development: use the stand-in AI in the Run panel, or run the fake
OpenAI-compatible server and point the app at it:

```bash
cd python && uv run python -m easychain.testing.fake_openai --port 8765
OPENAI_API_KEY=sk-test OPENAI_BASE_URL=http://127.0.0.1:8765/v1 uv run easychain dev
```

## Checks

```bash
make lint           # ruff (check + format) and TypeScript
make test           # pytest (with coverage) + vitest
make e2e            # Playwright (builds the web app, starts the app and the fake server)
```

CI runs all of these, plus a Docker build. Compiler code (`compiler/`, `steps/`) must stay at
or above 90% coverage.

## Golden files

`python/tests/golden/expected/*.py` pins the exact code each flow compiles to. After an intended
compiler change, run `make golden` and review the diff like any other code: it is what users
will export.

## Adding a step type

1. **Settings model**: add `XSettings` and `XStep` to `python/src/easychain/spec/models.py`,
   include it in the `Step` union and `STEP_MODELS`, then run `make schema`.
2. **Handler**: subclass `StepHandler` in `python/src/easychain/steps/`. It needs a plain-language
   `label` and `summary`, the LangChain/LangGraph `technical` term, a `form` (each field with help
   text and an example), `reads`/`writes`, `check` (messages written for people, each pinned to
   a setting, with a `Fix` where one is possible), and `emit` (readable code with a docstring).
   Register it in `steps/__init__.py`.
3. **Tests**: a golden case in `tests/golden/cases/`, behaviour tests in `test_compiler_run.py`,
   and checks in `test_validate.py`.
4. **Web**: usually nothing, because forms come from the catalog. Add an icon in
   `apps/web/src/components/canvas/stepMeta.ts` and a one-line summary in `stepSummary`.
5. **Docs**: `docs/steps/<type>.md` (what it does, settings, an example, the generated code, and
   errors you might see).

## Writing for users

Use the plain-language names from the build prompt (Flow, Step, Connection, Decision, Flow Data,
AI Model, Instructions, Action…) in the UI, docs and errors. Show the technical term only in Pro
mode or tooltips. Every error should say what happened and what to do next.
