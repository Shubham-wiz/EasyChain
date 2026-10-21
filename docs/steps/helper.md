# Helper

**A specialist a [Deep Agent](deep_agent.md) hands sub-tasks to: its own role, tools and
model.** In the AI group (Pro library, or search for it). LangChain term: a `deepagents`
sub-agent (`SubAgent`).

A Helper isn't a step that runs in the flow, so it has no connections. Its Deep Agent starts it
when it hands it a task, gives it the task in words, and gets its reply back. The Helper works
with a fresh conversation, so long research doesn't fill up the Deep Agent's own.

Put a Helper on a Deep Agent's team by connecting the Helper's top handle (pink, on top of the
card) to the Deep Agent's **Helpers** handle, or with **Add a Helper** in the Deep Agent's
inspector. A Helper works for one Deep Agent; "Helper of …" shows on its card.

## Settings

| Setting | What it does |
|---|---|
| What this Helper does | Its description. The Deep Agent reads it to decide which tasks to hand it, so say what it is good at (the check before a run warns when it is empty). |
| Model | Its own model. Empty: its Deep Agent's model. A smaller, cheaper model is often enough for a focused task. |
| Role and rules | Its system prompt. Put Flow Data in with `{field}`: it sees what its Deep Agent sees. |
| Tools | Steps it can use, connected to its own **Tools** handle. They take as arguments what the Deep Agent can't see in Flow Data, as for an [Agent](agent.md#tools). |
| MCP tools *(More options)* | Tools from MCP servers. |
| Skills | [Skills](../skills.md) from the Skills library this Helper can follow. |
| Add-ons | The Agent [Add-ons](agent.md#add-ons), for this Helper's own work. **Ask a person before …** can also name `write_file`, `edit_file` and `delete`. |
| Creativity *(More options)* | Temperature. |

A Helper has the same files as its Deep Agent (`ls`, `read_file`, `write_file`, …), so it can
read the Deep Agent's notes and leave its own.

## Approvals and the trace

When a Helper's tool needs approval, the whole run pauses and the request waits in the Inbox;
the answer goes back to the Helper, which carries on. In the run panel, everything a Helper did
is under its task in the Deep Agent's line, with the Helper's tokens and cost.
