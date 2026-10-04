# Code review of 2026-10-04: findings and their status

A full read-through of `main` at `f853377` (the end of Phase 3): every Python, TypeScript, test,
infrastructure and docs file. Findings were checked against the code, most of them by running a
small flow or script. **Done** means fixed and covered by a test. Paths are under
`python/src/easychain/` unless they start with `apps/`, `docs/` or `python/tests/`.

The verdict: the code is well built (careful SQL and queue code, a sound at-most-once design,
strong durability tests, thorough docs), but some bugs lost data silently, the local-only
security model had gaps, running from source on Windows was broken, and Phase 3's "Done when" is
proven only with the scripted stand-in AI.

## Fix first

| # | Finding | Where | Status |
|---|---|---|---|
| 1 | A step or flow **name with a line break becomes code** in the generated module (it runs when the module loads) | `compiler/codegen.py`, `steps/base.py`, `steps/flow_control.py`, `steps/actions.py` | Done: names are one-line labels (`spec/models.py`), and comments flatten what they're given |
| 2 | **Local MCP servers got every saved secret** in their environment | `integrations/mcp.py` | Done: they get only their own `env` (plus PATH and the like) |
| 3 | **A wrong `EASYCHAIN_SECRET_KEY` wiped the vault**: it loaded empty and the next save overwrote `secrets.enc`; a passphrase crashed startup | `server/secrets.py` | Done: the vault refuses to write (409 with how to recover); a clear message for a non-Fernet key; documented in `docker-compose.yml` |
| 4 | **Database query writes that return rows were rolled back** (`INSERT … RETURNING`) | `integrations/sql.py` | Done |
| 5 | **Read-only SQL holes**: settings PRAGMAs leaked into later writes; other databases only had a prefix regex | `integrations/sql.py` | Done: only describing PRAGMAs; MySQL gets a read-only transaction; other databases a strict keyword check |
| 6 | **Worker shutdown lost jobs** (re-queued as a bare `continue`) | `server/worker.py` | Done: handed back as they were; the next worker works out how far they got |
| 7 | **An Inbox answer can be applied to the wrong pause**, and an answer without `action` means approve | `runtime/runner.py`, `server/hub.py`, `steps/flow_control.py` | Open |
| 8 | **A second Inbox answer for For Each items is lost** | `server/hub.py`, `server/db.py` | Open |
| 9 | **No Host or Origin check** (DNS rebinding, cross-site form posts); the Dockerfile hint published the port on all interfaces | `server/app.py`, `Dockerfile` | Done: `server/hostguard.py`, `EASYCHAIN_ALLOWED_HOSTS`; `-p 127.0.0.1:8000:8000` |
| 10 | Web: **copy/paste with the Input step** made a connection with no source; a pasted agent pointed at the original tools | `apps/web/src/lib/spec.ts` | Done |
| 11 | Web: **moving Decision exits swapped their destinations**; a deleted exit left a dead connection; Ask a Human switched to "answer" kept labelled connections | `apps/web/src/lib/spec.ts` | Done: connections follow labels |
| 12 | Web: **autosave was cancelled on navigation** (the last 0.7 s of edits lost) | `apps/web/src/components/Editor.tsx` | Done: flushed on leaving the flow and on `pagehide` |
| 13 | Windows: **the sample shop database was never built**, so `run`, `test` and `worker` crashed | `templates/samples/__init__.py` | Done |

## Windows (decision R.4)

All done; CI runs the Python suite, the CLI, a worker and the web checks on Windows.

- Event loop: psycopg's async mode needs a selector loop (`_platform.py`); `easychain worker`
  couldn't start (`add_signal_handler`); the CLI crashed on ✓/✗ when output was redirected.
- Tests: `os.geteuid`, `SIGKILL`, files read and written without UTF-8, a hang starting Postgres,
  pgvector wrongly required, golden files with CRLF.
- Repo: no `.gitattributes` (CRLF scripts), no `.python-version` (uv picked 3.13), `make` only.
  Now `pnpm <task>` works everywhere (`scripts/tasks.mjs`).
- MCP: commands were split with POSIX rules (`C:\Tools\srv.exe` became `C:Toolssrv.exe`), and one
  unreadable command failed every run.
- `easychain dev` from a git checkout now serves `apps/web/dist` after `pnpm build`.

## Other findings

### Compiler, spec and steps

Done:

- Python keywords as Flow Data or reply field names (`from`, `class`) are a clear error; Code
  tools reading keys like `order-id` no longer produce invalid code.
- A docstring ending in `"` is escaped.
- Every name the helper code defines or imports is reserved (`Names.reserved()`), plus the
  names steps import (`interrupt`, `Command`, `Send`…).
- A custom update rule may start with blank lines; top-level code other than imports and
  `combine` is refused (it would run when the flow loads).
- Safety net: `compile_flow` compiles its own output; anything Python can't read becomes a
  check error before the run, not a crash in it.

Open:

- A sub-flow containing MCP steps is invoked synchronously (`TypeError`).
- A shared-data sub-flow as an agent tool or For Each body generates broken code.
- Run policy (retries, timeout, cache) is dropped for steps used as agent tools; the docs say it
  applies.
- For Each `concurrency` is ignored inside sub-flows and becomes run-wide on the root.
- AI Decision and Ask a Human "choose" fall back to substring matching ("None of the above" picks
  `No`; "no" picks `Refund` from `[Refund, No refund]`).
- `fill_url` keeps `/` in path values, so `../admin` reaches another endpoint with the same
  auth headers (matters when an agent picks the arguments).
- `{secret:NAME}` in Instructions text goes to the model literally; docs say it works.
- Saving strips trailing spaces from every multi-line string.

### Runtime, server and workers (open)

- `/resume` marks Inbox items answered before checking the run's status.
- The SSE stream closes after 1 s of quiet, before slow notifications and `run_finished`.
- A crash between `update_run(ok)` and `finish_job` skips after-flow triggers and notifications;
  the exception path never writes `run_finished`.
- Cancelling a finished run records "cancelled" in its events; `cancel_requested` isn't honoured
  on re-lease.
- The IMAP trigger treats sequence numbers as UIDs; one bad charset blocks it.
- A line break in an Ask a Human question breaks email notifications (Subject header).
- Settings `GET` returns the SMTP password, Slack webhook and MCP headers in plain text;
  `/savepoints` returns Flow Data unredacted.
- Long `thread_id`, `trigger` or names give a 500 on Postgres but work on SQLite.
- `chmod 0600` does nothing on Windows (now documented in environment.md).
- Done: deleted secrets now leave a separate worker's environment on reload.

### Knowledge, integrations, CLI and export (open unless marked)

- The MCP allow-list checks only the program name: approving `npx` or `python` allows anything.
- pgvector's HNSW index supports at most 2,000 dimensions; the 3072-dimension embedding models
  break Knowledge Bases on Postgres.
- The exported `.env.example` misses Agent, Memory and embedding models and SQL/MCP secrets.
- The docs give the wrong shape for `EASYCHAIN_MCP_SERVERS`.
- Text decoding tries UTF-16 before Latin-1, so accented Latin-1 files ingest as garbage.
- URL fetching has no size cap and follows redirects; `/api/knowledge-preview` returns what it
  fetched.
- `describe_database` counts every table's rows on each call.
- Done: `max_rows` now streams plain reads; Postgres queries have a 60 s statement timeout;
  `SELECT 'a;b'` is no longer refused.

### Web app and client (open)

- Run streams outlive their flow (switching flows mid-run shows the old run's results; Replay
  twice runs two loops).
- `?try=1` is never cleared, so reload starts a new run; "Try it" turns the stand-in on for
  every flow.
- Uncontrolled inputs in index-keyed rows show stale values after a row is removed.
- Keyboard delete of a connected step makes two undo entries.
- `@easychain/client` `wait()` returns a stale "paused" event for resumed runs.
- SMTP/IMAP passwords, the Slack webhook and MCP auth headers are plain-text inputs.
- A dropped event stream leaves the run panel on "Running"; the client leaves the stream open.
- `aria-live` on streamed tokens; field errors not linked with `aria-describedby`.

### Tests and docs (open unless marked)

- Phase 3's "Done when" is proven only with the scripted stand-in AI (`testsets.py` forces it
  when a Test Set sets `stand_in: true`, and both template Test Sets do).
- Test Sets drop unused answers and never check that a pause happened.
- The "day later" durability test is really a restart test.
- Some Playwright checks can't fail (Replay, "panning", "build under 300 s").
- Done: tests no longer touch the developer's own `~/.easychain` or database; the web build
  docs; `EASYCHAIN_SECRET_KEY` documented.
