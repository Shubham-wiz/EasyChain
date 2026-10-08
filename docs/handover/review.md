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
| 7 | **An Inbox answer can be applied to the wrong pause**, and an answer without `action` means approve | `runtime/runner.py`, `server/hub.py`, `steps/flow_control.py` | Done: answers go only to the question they were given for; an older run can't take answers or continue once a newer run happened in its conversation; a tool approval needs a clear "approve" |
| 8 | **A second Inbox answer for For Each items is lost** | `server/hub.py`, `server/db.py` | Done: answers given while the run was busy are used when it pauses again; nobody is asked twice |
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

### Runtime, server and workers

Done:

- Settings no longer return secrets: the SMTP password, Slack webhook and MCP header/env values
  are shown as `••••••` (a `{secret:NAME}` reference is shown as written), and sending the mask
  back keeps the saved value. Save Points hide vault secrets like run events do.
- A line break in an Ask a Human question no longer breaks email notifications.
- Too-long `thread_id`, trigger and names are a 422 with a plain message on every database
  (they were a 500 on Postgres); a very long step name fits the Inbox.
- Cancelling a finished run changes nothing; a run whose stop was asked for while its worker
  was gone isn't run again.
- The event stream waits for `run_finished` while notifications go out.
- A worker crash before writing the end, or an unexpected error, still ends the run once.
- The IMAP trigger uses real UIDs and skips a message it can't decode.
- `/resume` checks the run before marking Inbox items answered; deleted secrets leave a
  separate worker's environment on reload; `chmod 0600` on Windows is documented.

Open:

- The generic notification webhook URL and the MCP server URL are still shown in full (they may
  carry a token in the query string).

### Knowledge, integrations, CLI and export

Done:

- 3072-dimension embedding models work on Postgres with pgvector (a `halfvec` index above 2,000
  dimensions; the first ingest never fails on the index).
- Accented files in cp1252/Latin-1 are read correctly (UTF-16 only with a byte order mark or
  clear UTF-16 data).
- Web pages and OpenAPI specs are read with a size cap (50 MB / 25 MB); files that can't be
  parsed give a clear 4xx error.
- A relative OpenAPI `servers` URL is resolved against the spec's address.
- The exported `.env.example` lists every model key, every `{secret:…}`, and the MCP and
  Knowledge Base variables the flow needs; the README's run hints quote correctly in bash and
  PowerShell.
- `describe_database` quotes table names per database and caps its row counts.
- The MCP approved list can hold a whole command line (matched exactly); the UI and docs say
  plainly that approving `npx` or `python` approves anything they can run.
- `max_rows` streams plain reads; Postgres queries time out after 60 s; `SELECT 'a;b'` works.
- The docs give the right shape for `EASYCHAIN_MCP_SERVERS`.

Open:

- Server-side fetches (Knowledge Base web pages, OpenAPI import) have no address filter, so they
  can reach internal addresses, and `/api/knowledge-preview` returns what it fetched (planned
  egress allow-list: Phase 5).
- The pre-run check for missing secrets looks only at Database query and Web request steps.

### Web app and client

Done:

- Run streams belong to the panel that started them: opening another flow or replaying stops
  the old stream, and a late-ending stream can't take over the new one.
- `?try=1` is removed once the run starts (reload and Back don't start another run); "Try it"
  turns the stand-in on for that flow only.
- Inputs in rows follow the saved value (`DraftInput`, stable row keys): removing a row no
  longer renames the next field, and undo refreshes them.
- Deleting a connected step with the keyboard is one undo step.
- `@easychain/client`: `wait()` returns the end of the newest part; streams close on `break`;
  `approve_tool` types. Both the web app and the React hook reconnect a dropped event stream
  from the last event, then say the connection was lost.
- Passwords, webhooks and auth headers are password inputs with Show/Hide and a nudge to
  `{secret:NAME}`; a hidden saved value stays hidden and is kept.
- Screen readers hear one status line (`RunAnnouncer`), not every streamed token; field
  problems are linked to their inputs.
- The code editor's completion inserts what it shows.

Open:

- The generic Webhook URL in Notifications and header values in the Web request step's
  key-value editor are still plain inputs.
- Field problems of composite editors (input-field rows, the reply builder, the code editor)
  aren't linked to a single input.

### Tests and docs

Done:

- Test Sets fail a case whose `answers` weren't all asked for, so an approval case can't pass
  without the run pausing.
- `easychain test --real-model` runs a Test Set with the flow's real model even when the file
  says `stand_in: true` (the template Test Sets' notes said this happened by itself; it didn't).
- The "day later" durability test now also runs the workers' clean-up of old run data on the
  day-old run, and no longer checks its own timestamp edit.
- Tests no longer touch the developer's own `~/.easychain` or database; the web build docs;
  `EASYCHAIN_SECRET_KEY` documented.

Open:

- Phase 3's "Done when" is still proven only with the scripted stand-in AI in CI. Run
  `easychain test python/src/easychain/templates/sql-analyst.tests.yaml --real-model` (and the
  support bot's) with a key before a release.
- Some Playwright checks can't fail (Replay, "panning", "build under 300 s").
