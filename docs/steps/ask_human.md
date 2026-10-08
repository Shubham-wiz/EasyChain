# Ask a Human

**Pauses the run until a person answers.** They approve, reject, edit a field, type an answer
or pick an option, in the run panel or the **Inbox**. The run waits as long as it takes (days if
need be): it is saved, so restarts and deploys don't lose it.
LangGraph: a node that calls `interrupt()`; the run is resumed with `Command(resume=…)`.

## Settings

| Setting | What it does |
|---|---|
| Ask them to | **Approve or reject**; **Edit a field, then approve or reject**; **Type an answer**; **Pick one of some options**. |
| Question | What the person sees. `{field}` puts Flow Data into it. Secrets can't be used here. |
| Show them | Fields shown next to the question, such as a draft to check. |
| Field they can edit | (Edit) Their edited version replaces this field when they approve. |
| Options | (Pick) The choices; each is also an exit. |
| Save the answer as | Holds `Approved`/`Rejected`, the typed answer or the chosen option (default `human_answer`). A comment is saved as the same name with `_comment` added. |
| Notify people when it pauses *(More options)* | Sends the notifications set up in Settings (webhook, Slack, email). On by default. |

## Exits

| Ask them to | Exits |
|---|---|
| Approve or reject, Edit | **Approved**, **Rejected** |
| Pick an option | one per option |
| Type an answer | none: a plain connection |

## Answering

- **Run panel:** a waiting run shows the question with Approve/Reject (and the edit box, answer
  box or option buttons). The step glows amber on the canvas.
- **Inbox** (top bar): every run waiting for someone, from any flow, including runs started by
  triggers or the API. Notifications link straight to the item.
- **API:** `POST /api/runs/{run_id}/resume` with `{"answers": {"<waiting id>": {"action":
  "approve", "value": "…", "comment": "…"}}}`, or `POST /api/inbox/{id}/answer`.
- **Exported code** asks in the terminal (`ask_in_terminal`), the same way.

An answer is `{"action": "approve" | "reject", "value": …, "comment": "…"}`. A bare value is
also accepted (for answer and pick).

For **Pick one of some options**, the value must be one of the options (case and extra spaces
don't matter). Any other answer isn't guessed at: the step asks again, with the question starting
““no” isn't one of the options.”, and a new Inbox item opens. In exported code the terminal keeps
asking for a number until it gets one in range.

## Example

```yaml
- id: review
  type: ask_human
  name: Check the reply
  settings:
    kind: edit
    question: Send this reply to {email}?
    show: [draft]
    field: draft
    save_as: decision
connections:
- {from: review, exit: Approved, to: send}
- {from: review, exit: Rejected, to: output}
```

Compiles to (shortened):

```python
def review(data: FlowData) -> dict[str, Any]:
    answer = interrupt({"step": "review", "kind": "edit", "question": fill("Send this reply to {email}?", data),
                        "show": {"draft": data.get("draft")}, "field": "draft", "value": data.get("draft")})
    approved = answer.get("action", "approve") == "approve"
    update = {"decision": "Approved" if approved else "Rejected", "decision_comment": str(answer.get("comment") or "")}
    if approved and answer.get("value") is not None:
        update["draft"] = answer["value"]
    return update
```

## Good to know

- The step runs again from its start when the answer arrives (that's how LangGraph resumes), so
  keep anything with side effects in a later step.
- Inside a For Each, each item can wait for its own answer.
- Inside a Sub-flow, the parent run waits; the Inbox shows the path (`parent step › step`).
