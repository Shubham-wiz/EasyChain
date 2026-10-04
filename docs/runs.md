# Runs: workers, Save Points, the Inbox, triggers and the API

This page explains what happens when a flow runs, how runs survive crashes and wait for people,
and how to start and follow runs from other apps.

## The pieces

```
 web app / API client / trigger
        │  POST /api/runs
        ▼
 ┌──────────────┐   queued job    ┌──────────────┐  LangGraph checkpointer + store
 │ API server   │ ──────────────► │  worker(s)   │ ───────────────────────────────►  Save Points
 │ (FastAPI)    │ ◄────────────── │              │                                  side-effect records
 └──────────────┘   run events    └──────────────┘
        │  SSE / WebSocket                 all of it in SQLite (local) or Postgres
        ▼
 the canvas, your app
```

- **The database** holds runs, their events, the job queue, Inbox items, triggers, flow versions
  and settings. Flows themselves stay as YAML files in the workspace folder.
- **Save Points** are LangGraph checkpoints, written after every step by the
  `langgraph-checkpoint-sqlite` / `-postgres` savers into the same database (a sibling
  `*.savepoints.db` file for SQLite).
- **Workers** take jobs from the queue and run them. `easychain dev` runs one inside the server
  (fine for one person); `easychain worker` runs one on its own. Docker Compose starts Postgres,
  the API (which only queues) and a worker; add more with `docker compose up --scale worker=3`.

| Setting | Default | |
|---|---|---|
| `EASYCHAIN_DATABASE_URL` | `sqlite:///~/.easychain/easychain.db` | or `postgresql://user:pass@host/db` |
| `EASYCHAIN_WORKER` | `inline` | `off`: the API only queues; run `easychain worker` processes |
| `EASYCHAIN_HOME` | `~/.easychain` | secrets vault, uploads, the SQLite database |
| `EASYCHAIN_WORKSPACE` | `$EASYCHAIN_HOME/flows` | the flow files |
| `EASYCHAIN_PUBLIC_URL` | | where notification links point (its host name is also allowed) |
| `EASYCHAIN_ALLOWED_HOSTS` | `localhost`, `127.0.0.1`, `::1`, `*.localhost` | more host names the API answers to, e.g. behind a reverse proxy (`*` for any) |

Use Postgres when more than one worker runs. SQLite allows one writer at a time, which suits one
person on one machine.

## What happens to a run

1. **Queued.** The API saves the exact flow version (and every flow it uses as a Sub-flow), creates
   the run and queues a job. Editing the flow afterwards doesn't change a run that already started.
2. **Leased.** A worker takes the job and keeps a lease on it, renewed every few seconds. Only one
   job per conversation (thread) runs at a time, and a flow's *most runs at once* setting is
   respected; other jobs wait their turn.
3. **Running.** The worker runs the compiled LangGraph module with the database checkpointer, so a
   Save Point is written after every step (`durability="sync"`). Events go to the database in small
   batches and stream to anyone watching.
4. **Finished, paused or stopped.** `ok`, `error`, `cancelled`, or `paused` (waiting for a person,
   or at a breakpoint). A paused run creates Inbox items and sends notifications.

### When a worker dies

Its lease runs out (30 seconds by default, `easychain worker --lease`), and another worker takes
the job. It looks at the run's last Save Point and carries on from there:

- Steps that finished are **not** run again: their results are in the Save Point.
- The step that was running when the worker died runs again.
- **Side effects run at most once.** A Web request with POST, PUT, PATCH or DELETE (or *Send it at
  most once*) and a Code step with *Run it at most once* record their result in the LangGraph store
  under a key made from the run and the step's task. If the key is already there, the recorded
  result is used instead of sending again. The key also goes out as an `Idempotency-Key` header,
  so a service that honours it ignores a repeat that happened in the instant between sending and
  recording.

`tests/test_durability.py` kills a worker with SIGKILL in the middle of a run and checks all of
this, on SQLite and on Postgres.

A worker that is stopped normally (Ctrl+C, SIGTERM, `docker compose stop`) hands its runs back to
the queue straight away.

## Ask a Human and the Inbox

An [Ask a Human](steps/ask_human.md) step pauses the run (a LangGraph `interrupt`). Nothing is
kept in memory: the Save Point holds everything, so the answer can come a minute or a month
later, after restarts and redeploys.

- The **run panel** shows the question with the right controls (approve, reject, edit, answer,
  choose) while you are watching.
- The **Inbox** (`/#/inbox`) lists everything waiting, from every flow, including runs started by
  triggers and the API.
- **Notifications** (Settings → Notifications): a webhook (JSON POST), Slack (incoming webhook)
  and email (SMTP). Each links to the Inbox item. Use `{secret:NAME}` for URLs and passwords.

Answering resumes the run with `Command(resume=…)` on a worker. When several steps wait at once
(Ask a Human inside a For Each), each can be answered on its own.

## Debugging: breakpoints, Save Points and time travel

- **Breakpoints** (*More options → Pause before / after this step*) pause test runs from the canvas
  there (LangGraph `interrupt_before` / `interrupt_after`). **Continue** carries on.
- **Save Points** under each run list every checkpoint ("Before *Summarise*"), with the Flow Data
  at that point. Change any value and **Run again from here**: a new run forks from that point
  (`update_state` + resume) and the original stays as it was. A re-run starts past the breakpoint
  at the point it was taken from; later breakpoints still pause it.
- **Stop** ends a run where it is; **Carry on** continues it later. **Try the failed step again**
  re-runs a failed step without repeating the ones before it.
- Reloading the page re-attaches to a run that is still going or waiting.

## Conversations (threads) and second messages

Chat flows keep each conversation in a thread. When a new message arrives while the previous one
is still running, the flow's `double_texting` setting decides: queue it (default), reject it,
interrupt the current run, or roll the current run back and start over from before it.

## Triggers

| Trigger | How it starts the flow |
|---|---|
| **Webhook** | `POST /api/hooks/<id>` with header `X-Easychain-Token: <token>` (or `?token=`). The JSON body becomes the inputs; map fields with `{"inputs": {"question": "{ticket.text}"}}`. |
| **Schedule** | Cron (`minute hour day month weekday`, names, ranges, steps, `@daily`) in a time zone. Each slot fires once even with several workers. |
| **File upload** | `POST /api/hooks/<id>/upload?filename=report.pdf` with the file as the body. The saved file's path goes into the chosen input field. |
| **After another flow** | When the other flow finishes (or fails too, if chosen), with its results as inputs. |

Manage them in the editor (the ⚡ button) or with `/api/triggers`.

## The API

All endpoints are under `/api`; the OpenAPI document is at `/api/openapi.json` and browsable at
`/api/docs`.

| | |
|---|---|
| `POST /runs` | Start a run: `{flow_id, inputs, thread_id?, stand_in?, pause_before?, pause_after?, background?}`. Streams its events (SSE) unless `background: true`, which returns `{run_id, thread_id}`. |
| `GET /runs?flow_id=&thread_id=&status=` | Recent runs. |
| `GET /runs/{id}` | A run with its events (without tokens). |
| `GET /runs/{id}/events?after=` | Follow a run (SSE). Each event has an `id`; reconnect with `Last-Event-ID` to pick up where you left off. |
| `WS /runs/{id}/ws` | The same over a WebSocket; send `{"type": "cancel"}` to stop the run. |
| `POST /runs/{id}/resume` | Answer waiting steps: `{answers: {waiting_id: {action, value, comment}}}` (or `{answer}` when one step waits). |
| `POST /runs/{id}/continue` | Carry on after a breakpoint, an error or a stop. |
| `POST /runs/{id}/fork` | Re-run from a Save Point: `{checkpoint_id, update?}`. |
| `POST /runs/{id}/cancel` | Stop a run. |
| `GET /runs/{id}/savepoints` | The Save Points of the run's conversation, newest first. |
| `GET /inbox`, `POST /inbox/{id}/answer` | What waits for a person, and answering it. |
| `GET/POST /triggers`, `PATCH/DELETE /triggers/{id}` | Triggers. |
| `GET/PUT /settings/notifications`, `POST /settings/notifications/test` | Notifications. |
| `GET /flows/{id}/versions`, `GET /versions/{id}` | Flow versions (saved on every save and run). |
| `GET /threads?flow_id=` | Conversations of a flow. |

### Events

Every event has `type`, `run_id`, `ts` and (from the database) `event_id`. Events from steps
inside a Sub-flow also have `path`: the Sub-flow steps leading to them.

| `type` | Extra fields |
|---|---|
| `run_queued` | `action` |
| `run_started` | `thread_id`, `flow`, `stand_in`, `action` (start, resume, continue, fork) |
| `step_started` | `step`, `input` (the fields it reads), `item` (For Each) |
| `token` | `step`, `text` |
| `step_finished` | `step`, `output`, `duration_ms`, `usage`, `cost`, `model`, `item` |
| `route` | `step`, `exit` (a Decision, Jump, Ask a Human or For Each took an exit) |
| `progress` | `step`, `done`, `total` (For Each) |
| `step_paused` | `step`, `interrupt_id`, `request` (Ask a Human is waiting) |
| `save_point` | `checkpoint_id`, `next`, `step_number` |
| `step_failed` | `step`, `error` |
| `paused` | `reason` (ask_human, breakpoint), `interrupts`, `next` |
| `notified` | `inbox_id`, `results` |
| `run_finished` | `status` (ok, error, paused, cancelled), `output`, `reply`, `usage`, `cost`, `error`, `checkpoint_id` |

### From your own app

[`@easychain/client`](../packages/client) wraps all of this for TypeScript, with a React hook:

```ts
const easy = new EasyChainClient({ baseUrl: "http://localhost:8000" });
for await (const event of easy.run({ flowId: "summarise-url", inputs: { url } })) { … }
```
