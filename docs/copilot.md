# The copilot

Describe a flow, or a change to the open one, in your own words. The copilot proposes the
steps, their settings, the Flow Data and a few Test Set cases, checks them, and shows them on
the canvas **before anything changes**. You keep the changes you want, and applying them is one
step you can undo.

## Using it

1. Open a flow and pick the **Copilot** tab on the right.
2. Describe what you want: *"Summarise a web page in three bullet points"*, or for a flow that
   already has steps, *"Also save a one-line summary, and send it to Slack"*. Press **Propose**
   (or Ctrl/⌘+Enter).
3. The canvas shows the proposal. New steps are marked **New**, changed ones **Changed**, and
   nothing can be edited until you apply or discard it. The panel lists each change on its own
   line, with a box to keep or leave it:
   - *Add the AI Model step "Summarise"*, *Change "Output" (settings)*
   - *Add the Flow Data field `summary`*, *Remove the Flow Data field `draft`*
   - *Connect "Ask for a summary" to "Summarise"*, *Remove the connection from …*
   - *Move "Output" right, to make room* (the steps that come after new ones)
   - *Change the flow's name and settings*

   Untick a change and the canvas shows the flow without it. A connection to a step you left
   out is left out too.
4. **Test cases.** The copilot also writes two or three cases for the flow's Test Set. Kept
   cases are added to `<flow>.tests.yaml` (a case whose name is taken gets a number).
5. **Apply N changes** applies the kept changes as one undo step. **Discard** leaves the flow as
   it was.

**Fix it with the copilot.** A problem that has no fix button of its own (in the Inspector or
under the problems count at the top) has **Ask the copilot**. The copilot gets the flow and the
problem, and proposes a change in the same way.

## Which model it uses

Any model you have a key for (decision 4.3). With no choice made, it uses the first of these
whose API key is set: Claude Sonnet, GPT-4.1, Gemini 2.5 Pro, Mistral Large, DeepSeek, Grok,
Groq, OpenRouter, Together, Azure OpenAI. **Change** in the panel picks another model, Ollama
included; the choice is kept in the workspace (`GET`/`PUT /api/settings/copilot`).

With no key at all, the **stand-in copilot** shows an example: the template closest to your
words, as a proposal, with three of its Test Set cases. It can't fix problems; use the
problem's own fix button, or add an API key.

## How a proposal is made

```
your words + the open flow (YAML) + the problem, if any
        │
        ▼
the model writes the whole flow back as YAML: summary, flow, tests
        │
        ▼
read it with the flow spec and run the checks ──errors──► back to the model (twice at most)
        │ no errors
        ▼
the changes between the open flow and the proposal, placed on the canvas
```

- The model is told every step type and its settings (from the catalog the editor uses), the
  rules of the flow spec, and one example flow. It writes the **whole** flow, so its answer is
  checked exactly like a flow you drew.
- A proposal is shown only when it has **no errors**. If the checks find any, they go back to
  the model with what to change, at most twice; if errors are left after that, the panel says
  what they are and changes nothing. Warnings are shown with the proposal ("Worth a look").
- Steps that were there keep their place on the canvas. A new step goes to the right of the
  step that leads into it (an agent's new tool goes under the agent), and the steps after it
  move right to make room. No step lands on another.

## The API

`POST /api/copilot`

```json
{ "request": "Summarise a web page", "spec": { ...the open flow, or null... },
  "issue": { ...a problem from the checks, optional... }, "model": "openai:gpt-4.1" }
```

returns

```json
{ "ok": true, "summary": "Sums up the page in three bullet points.",
  "spec": { ...the proposed flow, with canvas positions... },
  "changes": [ { "id": "c1", "op": "add_step", "label": "Add the AI Model step “Summarise”",
                 "step": { ... }, "position": { "x": 300, "y": 100 } }, ... ],
  "test_cases": [ { "name": "A short page", "inputs": { ... }, "expect": { ... } } ],
  "issues": [ ...warnings... ], "model": "openai:gpt-4.1", "attempts": 1, "problems": [] }
```

Change operations: `set_flow`, `set_data`, `remove_data`, `add_step`, `update_step`,
`remove_step`, `move_steps`, `add_connection`, `remove_connection`. `POST
/api/flows/{id}/tests` with `{"cases": [...]}` adds cases to the flow's Test Set.

## Testing it

`python/tests/test_copilot.py` scripts the model's replies: ten written requests (from a
one-step summary to a supervisor team) each give a proposal with no errors; a reply with
problems goes back until it is fixed, or the copilot gives up and says why; a change to a flow
becomes separate changes with plain labels; new steps never land on old ones. The web tests
check that kept changes apply as one undo step, and a Playwright journey does it in the real
app against the fake model server. Try the copilot with a real model by hand before a release:
its proposals are only as good as the model.
