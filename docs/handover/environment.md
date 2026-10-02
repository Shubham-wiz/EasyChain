# Setting up a machine

## Toolchain

| Tool | Version used | Notes |
|---|---|---|
| Python | 3.12 (managed by uv) | `requires-python >= 3.12`; the system Python can be older, since uv fetches 3.12 |
| uv | 0.8.17 | `cd python && uv sync` creates `python/.venv` from `uv.lock` |
| Node | 22 (20+ works) | |
| pnpm | 10 | Workspace: `apps/*`, `packages/*` |
| Postgres | 16 | Optional locally; needed for the Postgres half of the queue and durability tests |
| Playwright | 1.56.1 with Chromium | e2e only |
| Docker | any recent | Compose file: Postgres + API + worker |

```bash
make install                 # uv sync + pnpm install
make lint && make test       # all green on a good checkout
pnpm --filter @easychain/web build && pnpm --filter @easychain/web e2e
```

## Postgres for tests

`python/tests/pg.py` decides which Postgres the tests use:

1. If `EASYCHAIN_TEST_POSTGRES_URL` is set (CI sets it), it uses that server:
   `export EASYCHAIN_TEST_POSTGRES_URL=postgresql://postgres:postgres@localhost:5432/postgres`.
2. Otherwise, if the server binaries are installed (`/usr/lib/postgresql/{17,16,15}/bin`), it
   starts a throwaway cluster on a free port. As root, it runs `initdb` as the `postgres` user
   through `runuser`.
3. Otherwise the Postgres variants are skipped, and the SQLite variants still run.

On Debian or Ubuntu: `apt-get install postgresql` is enough for option 2.

## Playwright

`apps/web/playwright.config.ts` starts the fake model server and `easychain dev` itself, with a
throwaway `EASYCHAIN_HOME`. Build the web app first. Install the browser with
`pnpm --filter @easychain/web exec playwright install chromium`. On machines that ship browsers
(for example `PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers`), skip the install. Screenshots for the
docs: `SCREENSHOTS=1 pnpm --filter @easychain/web e2e zz-screenshots`.

## Running the app

```bash
make dev                                   # API :8000 (with an inline worker) + web :5173
cd python && uv run easychain dev          # one process, serving the built web app
EASYCHAIN_WORKER=off uv run easychain dev  # API only; then run workers:
uv run easychain worker --database-url postgresql://user:pass@host/db --verbose
docker compose up --scale worker=3
```

Environment variables are listed in [docs/runs.md](../runs.md#the-pieces). No model key is
needed to try things: use the stand-in AI, or the fake OpenAI-compatible server:

```bash
cd python && uv run python -m easychain.testing.fake_openai --port 8765
OPENAI_API_KEY=sk-test OPENAI_BASE_URL=http://127.0.0.1:8765/v1 uv run easychain dev
```

## Moving to another machine or tool

- Everything needed is in git. Clone it, check out the branch, `make install`.
- Local runtime data lives in `~/.easychain` (or `$EASYCHAIN_HOME`): the SQLite run database,
  Save Points, the encrypted secrets vault and uploads. It is not in git. To carry it across,
  copy that folder. Secrets are encrypted with `secret.key` in the same folder, or with
  `EASYCHAIN_SECRET_KEY` when that is set.
- Other coding agents read [AGENTS.md](../../AGENTS.md). Claude Code also loads
  [CLAUDE.md](../../CLAUDE.md), which imports AGENTS.md.
