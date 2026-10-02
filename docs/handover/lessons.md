# Lessons: traps found and how they were fixed

Each entry gives the symptom, the cause and the fix, with where the fix lives. Add to this list
whenever something takes more than a few minutes to understand.

## LangGraph

| Symptom | Cause | Fix |
|---|---|---|
| `KeyError: 'checkpoint_ns'` when forking a run | `aupdate_state` needs the namespace in the config | Pass `{"configurable": {"thread_id": …, "checkpoint_id": …, "checkpoint_ns": ""}}` (`runtime/runner.py`, `_prepare_action`) |
| Re-running from a Save Point doesn't stop at a breakpoint at that point | LangGraph treats resuming with `None` input as passing an `interrupt_before` at the resumed point | This is by design. Later breakpoints still pause. Documented in `docs/runs.md`, and the e2e test expects it. |
| A node can't be named like a state field | LangGraph forbids a node and a state key sharing a name | Validation error with a rename fix. New step names avoid field names. The Ask a Human default `save_as` became `human_answer`. |
| Step ids containing `__` break namespaces | LangGraph uses `__` in internal names | The validator rejects them, with a rename fix |
| `timeout=` on a node does nothing for sync functions | LangGraph's node timeout applies to async nodes only | Sync step functions are wrapped with the `in_thread` helper (`compiler/helpers.py`) so time limits work |
| Jump's field updates didn't see each other | Updates were computed from the original state | Apply them in order to a copy: `data = {**data}` … then build `Command(update=…, goto=…)` |
| Sub-flow inputs overrode the child's defaults with `None` | Implicit input mapping copied fields the parent didn't have | Only map fields that are available in the parent |
| Sub-flow emitted class names clashed in one module | Child Input/Output classes were named independently | Claim names through `module.names` in codegen |
| A recursion error surfaced as a stack trace | `GraphRecursionError` wasn't mapped | Mapped to the error kind `too_many_steps`, with a message pointing at "Most rounds of steps" |

## Database, queue and workers

| Symptom | Cause | Fix |
|---|---|---|
| `greenlet` missing at runtime | SQLAlchemy async needs the extra | Depend on `sqlalchemy[asyncio]` |
| Token events vanished before a slow tail read them | Old tokens were deleted eagerly | Drop old tokens lazily (`drop_old_tokens`, run from the schedule loop) |
| "Interrupt" double texting was slow to cancel | Cancel waited for the next heartbeat | An in-process `running` stop map on the EventBus, and a heartbeat interval of `min(lease/3, 2s)` |
| `SAWarning: unreturned connection` | The heartbeat task outlived the job, and the WebSocket tail outlived the client | Stop the heartbeat with an event. Stop the WebSocket tail on disconnect or when the run ends. |
| A client waiting for `run_finished` found no Inbox item | The worker wrote `run_finished` before creating Inbox items | Create Inbox items and update the status first. Write the final event last (`worker._finish`). |
| Two processes starting at once raced to create tables | The API and worker containers both ran `create_all` | Take an advisory transaction lock around the schema (`SCHEMA_LOCK` in `server/db.py`) |
| Deadlock in LangGraph's Postgres `setup()` when several processes start | A blocking advisory lock plus `CREATE INDEX CONCURRENTLY` (which waits for all transactions) | Poll `pg_try_advisory_lock`, and retry `setup()` on `DeadlockDetected` (`runtime/resources.py`) |
| SQLite `database is locked` under concurrent writers | A deferred `BEGIN` upgrades to a write lock too late | WAL mode, `BEGIN IMMEDIATE` for writes, and a deferred `BEGIN` only for reads (`easychain_read` execution option) |
| A schedule fired twice with two workers | Both claimed the same slot | `claim_schedule` is a compare-and-set on `next_fire_at` |

## Tests

| Symptom | Cause | Fix |
|---|---|---|
| The run-policies test saw effects from another test | The fake server's effects log is shared | Call `/effects/reset` at the start of the test |
| The Inbox notification test was flaky | It asserted before the notification was sent | Wait for the `notified` event |
| The email trigger test flow was invalid | Step id `label` equalled a Flow Data field | Rename the step (`make_label`) |
| `initdb` refuses to run as root | Postgres safety check | `tests/pg.py` runs it through `runuser -u postgres`, or uses `EASYCHAIN_TEST_POSTGRES_URL` |
| Playwright strict-mode violations | Labels like "Add", "Triggers" and "email" appear more than once | Use `{ exact: true }`, or scope to a dialog or region |
| `__dirname` is not defined in an e2e spec | ESM | Use `process.cwd()` |

## Environment

| Symptom | Cause | Fix |
|---|---|---|
| `docker compose up` failed pulling `postgres:16-alpine` | Docker Hub rate limit (HTTP 429) in the build sandbox, with the ECR mirror blocked | Test the image with `--network host` against a host Postgres. CI runs the full stack. |
| `git push` returned HTTP 403 | The Claude GitHub App has no access to the repository | The owner connects GitHub and installs the app (see [status.md](status.md#blocked)) |
