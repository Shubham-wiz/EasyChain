# @easychain/client

Start, follow and answer [Easy Chain](../../README.md) runs from your own app.

```ts
import { EasyChainClient } from "@easychain/client";

const easy = new EasyChainClient({ baseUrl: "http://localhost:8000" });

// Start a run and stream what happens.
for await (const event of easy.run({ flowId: "summarise-url", inputs: { url: "https://example.com" } })) {
  if (event.type === "token") process.stdout.write(String(event.text));
  if (event.type === "run_finished") console.log(event.status, event.output);
}

// Or start it in the background and come back later (events survive disconnects).
const { run_id } = await easy.start({ flowId: "approve-reply", inputs: { email: "Where is my order?" } });
const final = await easy.wait(run_id); // status "paused": a person needs to answer
for await (const _ of easy.resume(run_id, { [final.interrupts![0].id]: { action: "approve" } })) {}
```

| Method | What it does |
|---|---|
| `run(options)` | Start a run; yields its events until it finishes, fails, stops or waits for a person. |
| `start(options)` | Start a run in the background; returns `{ run_id, thread_id }`. |
| `follow(runId, after?)` | Events of a run from an `event_id` on (safe to call again after a disconnect). |
| `wait(runId)` | Follow a run to the end of its current part; returns the final event. For a run that was answered or continued, that is the end of its newest part. |
| `resume(runId, answers)` | Answer waiting Ask a Human steps and tool approvals, `{ waitingId: { action, value, comment } }`. |
| `continue(runId)` | Carry on after a breakpoint, an error or a stop. |
| `fork(runId, checkpointId, update?)` | Re-run from a Save Point, optionally changing Flow Data there. |
| `cancel(runId)` | Stop a run (its Save Points are kept). |
| `inbox()`, `answer(inboxId, answer)` | Everything waiting for a person, and answering it. |
| `socket(runId)` | A WebSocket with every event as JSON; send `{"type": "cancel"}` to stop the run. |

Leaving a `for await` loop early (`break`) closes the connection; the run carries on on the
server, and `follow(runId, after)` picks it up again. A waiting request has `kind` `approve`,
`edit`, `answer` or `choose` (Ask a Human), or `approve_tool`: an Agent asks before using a tool,
and `actions` lists the calls it wants to make (`{ tool, args }`).

## React

```tsx
import { EasyChainClient } from "@easychain/client";
import { useEasyChainRun } from "@easychain/client/react";

const easy = new EasyChainClient({ baseUrl: "http://localhost:8000" });

export function Reply({ email }: { email: string }) {
  const run = useEasyChainRun(easy, { flowId: "approve-reply" });
  return (
    <div>
      <button onClick={() => run.start({ email })} disabled={run.status === "running"}>Draft a reply</button>
      <p>{run.text}</p>
      {run.waiting.map((w) => (
        <button key={w.id} onClick={() => run.answer({ [w.id]: { action: "approve" } })}>
          {w.request.question} Approve
        </button>
      ))}
      {run.status === "ok" && <pre>{JSON.stringify(run.output, null, 2)}</pre>}
    </div>
  );
}
```

The hook returns `status`, `text` and `tokens` (streamed model output), `steps` (each step's state),
`output`, `error`, `waiting` (Ask a Human steps) and `start`, `answer`, `cancel` and `reset`.
If the event stream drops mid-run, the hook picks the run up again from the last event it saw
(four tries, waiting 0.5 to 4 seconds); after that `status` is `"error"` with `error.kind`
`"disconnected"`, and the run may still be going on the server.
