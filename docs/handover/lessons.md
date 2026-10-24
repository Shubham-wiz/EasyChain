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

## Agents, Knowledge Bases and integrations (Phase 3)

| Symptom | Cause | Fix |
|---|---|---|
| `UnboundLocalError: mcp_tools` in a generated agent | A local variable shadowed the `mcp_tools` helper | The local is `server_tools`. Generated names go in `Names.RESERVED` (`compiler/pycode.py`). |
| A step id like `text` or `tool` breaks generated code | Step ids become function names and shadow imports | Search helpers use `sa.`/`np.` prefixes, and new helper and import names are reserved |
| An agent's structured answer is missing (`None`) | A limit add-on ended the agent before it answered | Generated code raises "The agent stopped before it answered: …" |
| An agent stopped with "25 rounds" | Each model call, tool call and add-on is a LangGraph step | The agent's own `max_steps` (default 100) applies to its `invoke`; the error names the agent |
| OpenAI embeddings fail offline | `OpenAIEmbeddings` counts tokens with tiktoken, which downloads its tables | Easy Chain passes `check_embedding_ctx_length=False` (chunks are small) |
| An off-topic question still "found" passages | Meaning search always returns the nearest chunks, and common words match everything | `min_similarity` drops weak meaning-only matches; full-text search ignores common words |
| Duplicate dict key in generated code | A spread field had the same name as `save_as` | Check `schema_field_is_save_as`, with a fix |
| Code steps importing `AIMessage` were refused | New names were reserved for generated code | Importing the same object is allowed (`_SAFE_IMPORTS` in `steps/actions.py`) |
| YAML Test Set failed to parse | `?` or `:` inside a flow mapping (`{content: How?}`) | Quote chat texts in Test Sets |
| A connection out of a Web request or Code step looped over the card | The step got a second source handle ("use as a tool", on top); React Flow attaches an edge without a handle id to the first source handle in the DOM | Render the right-hand handle first (`StepNode.tsx`); the e2e MCP journey checks the edge leaves from the right |
| e2e `boundingBox` strict-mode error on `.react-flow__handle.source` | Same cause: two source handles | Scope selectors: `.source:not(.tool-handle)`, `.target:not(.tools-handle)` |
| Deleting a tool step left `approve_tools` pointing at it | `removeSteps` cleaned `tools` only | It now cleans `addons.approve_tools` too (unit tested) |
| Passage snippets showed `## Refunds` | Markdown heading lines are kept in chunk text for the model | `passageText()` in `Citations.tsx` drops heading lines when showing passages |
| A document stayed "processing" for ever | The API server restarted while reading it (ingestion runs in-process) | On startup, unfinished documents are marked failed with "The server restarted while reading this…" |
| `easychain test sql-analyst.tests.yaml` gave 9/10 by hand | One case posts to `${base_url}/effects`; without `--var base_url` and the fake server the request fails | Start `python -m easychain.testing.fake_openai --port N` and pass `--var base_url=http://127.0.0.1:N` |
| The "Tools" label sat on top of the tool lines | It was centred under the Agent's bottom handle, where the lines leave | It sits beside the handle now |
| An error raised by a callback doesn't stop a sync `model.invoke` (a trap found reading LangChain, before it bit) | LangChain runs an *async* handler from sync code in a new event loop and only logs its errors | The gateway's callback is a sync `BaseCallbackHandler` with `raise_error = True`; it hands database work to the server's loop with `run_coroutine_threadsafe` (never waiting on the loop's own thread) |
| A model call's span sat outside its step's span | A step's `step_started` time is when the run heard of it, which can be after a fast model call ended | Build spans in passes and widen each step to hold its model calls and children (`server/tracing.py`) |
| A cost of `<$0.0001` matched `not.toContainText("$0.00")` | The text contains the substring | Give the model a price in tests that check costs (Settings → Model gateway → Prices) |

## GitHub access

| Symptom | Cause | Fix |
|---|---|---|
| `git push` gets HTTP 403: "Claude doesn't have GitHub access to OWNER/REPO" while the GitHub account shows as connected and the app as installed | The Claude GitHub App was installed with access to selected repositories only, and this repo wasn't one of them | On GitHub, go to Settings → Applications → Claude → Configure → Repository access, and add the repo (or allow all repositories). Then check claude.ai → Connectors → GitHub → **Check repository status**. |

## Database, queue and workers

| Symptom | Cause | Fix |
|---|---|---|
| A For Each item's Inbox answer was asked again | LangGraph keeps listing a parallel item's interrupt (with the answer stored) until its siblings are answered | The worker doesn't reopen questions that already have an answer, and sends answers given while the run was busy (`worker._finish`). An agent's next approval gets a new interrupt id; an Ask a Human "choose" that asks again (the answer wasn't an option) keeps its id, and the worker opens a new Inbox item for it because its answer was already sent. |
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
| An old journey failed on `getByRole("status")` (two elements) | A new live region (the step library announces an inserted pattern) also has `role="status"` | Scope status locators to their region (`getByTestId("canvas").getByRole("status")`), and run the whole e2e suite before pushing a UI change, not just the new spec |
| An unscripted stand-in researcher never read its pages | The stand-in fills a lone string argument with the whole request, so a `url` got the question text, and no tool matched the request's words | A tool with a `url`/`link`/`address` argument reads each web address the request names, in order (`standin._unread_urls`) |
| A stand-in Helper's reply showed up as "task returned: [Stand-in AI…" | Text starting with `[` was taken for data rows | The stand-in's label is removed from tool results before answering |
| The deep agent crash test timed out on Windows (Postgres half) | The worker's start-up imports take far longer on the Windows runner | The test waits up to 150 s for the slow tool, and its failure names the run's status and events |
| `RuntimeError: … is bound to a different event loop` in a two-browser auth test | Two `TestClient(app)` context managers start the app twice, each on its own event loop, sharing one database engine | Use one `TestClient` and swap its cookie jar for the second browser (`Browser` in `tests/test_auth.py`); the jar is `type(client.cookies)()` (Starlette's TestClient uses `httpx2`, not `httpx`) |
| A test flow that ran in the API test went to `error` from a webhook | Input straight to Output is a check error ("nothing would happen"); a background run only says so when it finishes | Put a step between them, and wait for the run in the test |
| An e2e test needed something only the second web server knew (its home folder) | `playwright.config.ts` is loaded again in each worker, so a `mkdtemp` there gives each process a different folder | Set it once in `process.env` when unset (`E2E_AUTH_HOME`); workers inherit the environment |
| An axe check failed with "target-size" (a link 22 px from an input) after wrapping a form in a `<fieldset>` | Tailwind's `space-y-*` puts its margin on the children; a `display: contents` fieldset drops it, and `m-0` overrides it (the utility has zero specificity) | `Locked` in `ui.tsx`: a block fieldset with `mx-0 min-w-0 border-0 p-0` |
| An egress test passed here and failed in CI ("Connection refused" to `localhost`) | The runner resolves `localhost` to `::1` first; the connect-time guard connected only to the first address, and the test server listens on 127.0.0.1 | `GuardedBackend` tries each checked address in turn, as a plain connection does (`egress._candidates`); a test resolves a name to `::1` then `127.0.0.1` |
| A check meant to skip a bad workspace never ran (`try: space = spaces.enter(…) except KeyError`) | `enter` is a `@contextmanager`: calling it only makes the generator, and its body (and the `KeyError`) runs at `with` | Check with `spaces.get(…)` first, then `with spaces.enter(…)` (`worker.check_schedules`) |
| A type error passed a quick check and broke `pnpm build` | `npx tsc --noEmit -p .` in `apps/web` checks nothing: its `tsconfig.json` only lists references | Check with `npx tsc -b` (what `pnpm lint` and the build run) |
| A web unit test failed with "Body is unusable: Body has already been read" on the second request | `vi.fn().mockResolvedValue(new Response(…))` hands the same `Response` to every call, and a body can be read once | `mockImplementation(async () => new Response(…))`, a new one per call |
| (Not a bug: how workspaces reach the code) A context variable set in an `async` app-level FastAPI dependency is seen by the route, by sync routes (Starlette copies the context into the thread pool), by `asyncio.to_thread`, and by a `StreamingResponse`'s generator | Dependencies run in the request's own task | `spaces.in_workspace` sets the workspace that way; a value set in a *sync* dependency would be lost (it runs in a thread) |

## Windows

| Symptom | Cause | Fix |
|---|---|---|
| `psycopg.InterfaceError: Psycopg cannot use the 'ProactorEventLoop'` | Windows' default event loop; psycopg's async mode needs a selector loop | Every entry point uses `easychain._platform` (`run`, `uvicorn_loop`); tests set the selector loop policy in `conftest.py` (also for `TestClient`) |
| `easychain worker` crashed with `NotImplementedError` | `loop.add_signal_handler` doesn't exist on Windows | `_platform.on_stop` uses plain signal handlers there; Ctrl+Break (`SIGBREAK`) also stops a worker. Tests start workers with `CREATE_NEW_PROCESS_GROUP` and send `CTRL_BREAK_EVENT` |
| The sample shop database was never built (`WinError 32`), and the templates broke | `with sqlite3.connect()` commits but doesn't close; Windows can't move or delete an open file | `contextlib.closing(...)` around each connection (`templates/samples`) |
| `UnicodeEncodeError: 'charmap'` / garbled golden files | Text files and redirected output default to the ANSI code page (cp1252) | Always pass `encoding="utf-8"`; the CLI reconfigures stdout/stderr to UTF-8 (`_platform.utf8_console`); child processes in tests get `PYTHONIOENCODING=utf-8` |
| Golden files and scripts got CRLF line endings | Git's `autocrlf`, and `write_text` translating `\n` on Windows | `.gitattributes` (`eol=lf`); write generated files with `newline="\n"` |
| The test suite hung starting Postgres | `subprocess.run(pg_ctl start, capture_output=True)`: the server inherits the pipes and Windows waits for it to exit | Send the server's output to `DEVNULL` (it logs to a file) (`tests/pg.py`) |
| `os.geteuid` / `signal.SIGKILL` missing | POSIX-only | `hasattr(os, "geteuid")`; `proc.kill()` instead of `SIGKILL` |
| `pnpm` couldn't be started from Node (`EINVAL`) | Node only runs `.cmd` files through a shell | `scripts/tasks.mjs` spawns with `shell: true` on Windows and stops process trees with `taskkill /T` |
| `initdb` failed on GitHub's Windows runner | The runner's environment (runs as an administrator) | CI starts the runner's own Postgres service and sets `EASYCHAIN_TEST_POSTGRES_URL`; `tests/pg.py` now shows initdb's own message |
| An axe check hung, or found contrast problems neither theme has | It measured colours mid-transition after a theme toggle; waiting for *all* animations never ends (spinners loop) | Wait only for animations with a finite number of iterations (`e2e/quality.spec.ts`) |
| uv picked Python 3.13 | No `.python-version` | `python/.python-version` pins 3.12, the version CI and Docker use |
| `easychain dev` crashed on Windows (`BaseSelectorEventLoop.close() missing … 'self'`) | uvicorn treats a custom `loop` value as the loop factory itself (it isn't called with `use_subprocess`) | `_platform.uvicorn_loop()` names `new_event_loop`; the Windows CI job starts `easychain dev` |

## Environment

| Symptom | Cause | Fix |
|---|---|---|
| `docker compose up` failed pulling `postgres:16-alpine` | Docker Hub rate limit (HTTP 429) in the build sandbox, with the ECR mirror blocked | Test the image with `--network host` against a host Postgres. CI runs the full stack. |
| `git push` returned HTTP 403 | The Claude GitHub App has no access to the repository | The owner connects GitHub and installs the app (see [status.md](status.md#github) and "GitHub access" above) |
| A commit went in with unformatted code | The lint check was piped through `tail` (which hid its exit code), and the commit didn't depend on lint passing | Chain the commit on lint: `pnpm lint && git commit …`; never pipe a check whose exit code matters |
| The 13 sandbox tests were skipped after the build container restarted | Its Docker daemon wasn't running (`Cannot connect to the Docker daemon`) | Start it (`dockerd &`) and run `tests/test_sandbox.py` again; CI's Linux runners have Docker |
| The e2e job failed at "playwright install" (`Playwright does not support chromium on ubuntu26.04-x64`) | GitHub moved `ubuntu-latest` to Ubuntu 26.04; Playwright 1.56 has no browser build for it | The e2e job runs on `ubuntu-24.04`. Move it back once the pinned Playwright supports the newer image |
