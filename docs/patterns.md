# Agent team patterns

Three well-known ways for several agents to work together, each built from **ordinary steps**,
so every part stays on the canvas, can be changed, and exports to plain LangGraph. Each one is a
template with a Test Set, and you can also drop it into a flow you are building: in the step
library, under **Agent team patterns**, click one. Its steps are added below your flow, with the
Flow Data fields they use. Connect them to your flow, and change any step you like.

| Pattern | Template | Use it when |
|---|---|---|
| **Supervisor** | Support team (supervisor) | One request may need several specialists, one after another. |
| **Handoffs** | Travel desk (handoffs) | A conversation moves between desks, and the desk that is helping keeps the chat. |
| **Plan-and-execute** | Plan and do (plan-and-execute) | A goal breaks down into steps that can be done one by one. |

For one agent that plans, keeps files and hands sub-tasks to helpers of its own, use a
[Deep Agent](steps/deep_agent.md) with [Helpers](steps/helper.md) instead.

## Supervisor

```
Input → Supervisor (AI Decision) ─Billing→ Billing specialist ─┐
             ▲      ├─Technical→ Technical specialist ─────────┤
             │      ├─Accounts→ Accounts specialist ───────────┤
             └──────┴──────────────────────────────────────────┘
                    └─Finish→ Write the reply → Reply → Output
```

- The **supervisor** is an AI [Decision](steps/decision.md) with one exit per specialist and
  *Finish*. Its guidance shows the specialists' notes (`{billing_note}`…), filled in at each
  visit, so it can tell when the request is covered.
- Each **specialist** is an [Agent](steps/agent.md) with its own tools, writing its notes to its
  own field, and leads back to the supervisor.
- The supervisor's **round limit** (4) ends the loop even if the AI never picks *Finish*.
- LangGraph: the supervisor is a node plus a conditional edge; the specialists are agent nodes
  with edges back to it.
- **Inbox triage** is a bigger supervisor: an AI Model sorts the email first (topic, urgency,
  summary as Flow Data), the supervisor's guidance shows them with the helpers' notes, the
  helpers look up the sender, draft a reply from the help centre (a Knowledge Base search tool)
  and open a ticket (run at most once), and an urgent email waits in the Inbox for a person to
  check the reply.

## Handoffs

```
Chat → Which desk is helping? ─Flights→ Flights desk ──┐
                              ├─Hotels→ Hotels desk ───┤
                              └─Front desk→ Front desk ┤
                                                       ▼
          ┌──── To the flights desk ◄─Flights─ Hand over? ─Stay→ Output
          │     To the hotels desk  ◄─Hotels──┘
          └──→ the desk it hands over to
```

- Every desk is an [Agent](steps/agent.md) that reads and writes the same chat (`messages`).
- After each reply, **Hand over?** (an AI Decision) picks the desk that should help next.
- A [Jump](steps/jump.md) records the desk in Flow Data (`desk`) and goes to it, in one move
  (LangGraph's `Command(update=…, goto=…)`). It hands over only when the desk changes, so a desk
  never answers twice in a row.
- The next message goes straight to the desk that is helping, because `desk` carries over to the
  next turn of the chat.

## Plan-and-execute

```
Input → Ask for a plan → Planner (structured: steps) → For each step ─Each item→ Do the step (Agent)
                                                                    └─When done→ Write it up → Answer → Output
```

- The **planner** is an AI Model with a [structured answer](agents.md#structured-answers): a
  list of steps, saved to Flow Data.
- A [For Each](steps/for_each.md) runs the **executor** Agent on each step, one at a time
  (*at most 1 at once*), so the results keep the plan's order.
- The **write-up** answers the goal from the results.

## Testing them

Each template's Test Set (`python/src/easychain/templates/<id>.tests.yaml`) scripts the
stand-in AI's turns: the supervisor's picks, each specialist's tool calls and replies, the
handovers, the plan. What the flow does with them (routes, tools, round limits, Jumps, the For
Each) is real. One case in each runs with no script at all.
