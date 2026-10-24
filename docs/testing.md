# Test Sets and Checks

A **Test Set** is a list of cases for one flow. Each case gives the flow some inputs and says
what should come out. Run it after every change, and you see at once what still works and
what broke.

A flow's Test Set is the file `<flow id>.tests.yaml`, next to the flow's own file. So it is
kept in git with the flow, and reviewed with it.

## In the app

Open a flow and choose the **Tests** tab on the right.

- **Add a case**, give it a name and its inputs. For a chat flow, the input is a message.
- **Answers to Ask a Human** are given in order, each time the run waits for someone.
- **What the stand-in AI says** scripts the model's turns: a tool call, an answer, or plain
  text. It applies only when the case runs with the stand-in AI.
- **Add a check** for each thing the case should show (the kinds are below).
- **Save the Test Set**, then **Run all**, or run one case with its ▶ button.

Each case then shows **Passed**, **Failed** or **Passed, some skipped**. Open the case to see
every check:

- ✓ passed;
- ✗ failed, with what was wanted and what came ("expected 'HELLO!', got 'HELLO'");
- – skipped, with why.

The case also shows its time, tokens, cost and the tools the agent called.

Who can do what:

- Editors and Owners change cases.
- Runners can run them, and Viewers can read them.
- An Editor's run uses the flow as it is on the canvas. A Runner's uses the saved flow.

**Run with the stand-in AI** runs every case without a model or a key. Use it for what the
flow itself does: which way a Decision goes, which tools are called, what is saved. Turn it
off to try the real models, with their keys and their costs.

Saving from the app rewrites the file in a standard layout: comments in a hand-written file
aren't kept.

## The file

```yaml
flow: support-bot.flow.yaml     # the flow, next to this file
stand_in: true                  # run with the stand-in AI unless told otherwise
judge_model: openai:gpt-4.1     # the AI judge's model (optional)
cases:
  - name: Answers a refund question
    inputs: {question: "How long do refunds take?"}
    script:                     # the stand-in AI's turns (real models ignore them)
      - {call: search_help, args: {query: refunds}}
      - "Refunds take 5 to 7 days."
    expect:
      output.answer: {contains: [refund, 7 days], max_length: 600}
      tools: [search_help]
      max_seconds: 30
  - name: A person approves the reply
    inputs: {email: "Where is my order?"}
    answers:                    # given in order to Ask a Human steps
      - {action: approve, comment: Fine}
    expect:
      route: {check: Approved}
  - name: A follow-up in the same chat
    turns:                      # several messages in one conversation
      - {messages: [{role: user, content: "How do I reset my password?"}]}
      - {messages: [{role: user, content: "And how long does the link work?"}]}
    expect:
      output.messages.-1.content: {contains: one hour}
```

`${name}` in a case is filled in when it runs: from `--var name=value` on the command line,
from the values the app asks for, or from the environment.

## Checks

Every check goes under a case's `expect:`.

### Values

A value is named by its path into the run's result: `output.answer`, `output.order.id`,
`output.messages.-1.content` (the last message). A plain value means "is exactly":
`output.category: Billing`.

| Check | Passes when the value… |
|---|---|
| `equals` | is exactly this |
| `contains` | contains this text (or each of a list), ignoring case |
| `not_contains` | doesn't contain it |
| `matches` | matches a regular expression |
| `not_empty: true` | is there and isn't empty |
| `min_length`, `max_length` | has at least / at most this many characters |
| `count` | is a list of this many items |
| `schema` | fits a JSON Schema (an answer given as JSON text counts too) |
| `at_least`, `at_most` | is a number at least / at most this |
| `one_of` | is one of these |

```yaml
output.order: {schema: {type: object, required: [id, status]}}
output.score: {at_least: 0, at_most: 10}
output.category: {one_of: [Billing, Sales, Support]}
```

### How the run went

| Check | Passes when… |
|---|---|
| `status: ok` | the run ends this way: `ok` (the default), `error` or `paused` |
| `steps: [lookup]` | these steps ran |
| `route: {triage: Billing}` | the Decision `triage` took the exit `Billing` |
| `tools: [find_order]` | the agent called these tools |
| `not_tools: [refund]` | … and didn't call these |
| `tool_calls: 2`, `{max: 4}`, `{min: 1}` | the agent made this many tool calls |
| `trajectory: [find_order, reply]` | the tool calls came in this order (others may come between) |
| `trajectory: {tools: [...], order: exact}` | exactly these calls, in this order |
| `trajectory: {tools: [...], order: any_order}` | all of these, in any order |
| `helpers: [researcher]` | a Deep Agent gave a Helper a task |
| `files: {/report.md: {contains: Bees}}` | a Deep Agent wrote this file |

### Budgets

Budgets add up every part of a case: each answer to Ask a Human, and each turn of a chat.

| Check | Passes when the case took… |
|---|---|
| `max_seconds: 20` | at most this long |
| `max_tokens: 4000` | at most this many tokens (in and out) |
| `max_cost: 0.02` | at most this many dollars |

A cost check is skipped when a model's price isn't known, so there's no cost to compare.

### The AI judge

Some answers can't be checked by their words. An **AI judge** reads the case's input and the
flow's output, and grades the output against a **rubric** from 0 (none of it) to 1 (all of it).

```yaml
judge:
  rubric: Polite, says when the refund arrives, and promises nothing else.
  pass_score: 0.7          # the default
  look_at: output.answer   # optional: grade one value instead of the whole output
  model: openai:gpt-4.1    # optional: else the Test Set's judge_model, else the copilot's choice
```

The judge always uses a real model. It is **skipped** in two cases, and the result says
which:

- **No model has a key.** Add one in Settings, or name a model.
- **The case ran with the stand-in AI.** The stand-in makes its answers up, and offline runs
  stay free.

The judge never makes up a grade (decision 5.4). The score and the judge's reason show with
the result: "score 0.90 (needs 0.70) from openai:gpt-4.1: Gives the day."

## Test Runs and baselines

**Run all** shows how the cases do right now. A **Test Run** is kept: it runs the Test Set
against the flow as it is saved (a version in the flow's history), on a worker, and keeps
every case's result. Use it to compare one version of a flow with another.

In the Tests tab, under **Kept Test Runs**:

1. Choose how often to run each case, and press **Run and keep**. The run goes on while you
   work, and shows how far it has got.
2. Open a finished run and press **Make this the baseline**. Each flow has one baseline.
3. Later runs are compared with it, case by case.

| A case is | When |
|---|---|
| **Better** | it passes more often than in the baseline |
| **Worse** | it passes less often: a regression |
| **Same** | as often |
| **New** | the baseline didn't have it |
| **Not run** | only the baseline has it |

Each case also shows the change in its average time and cost.

**Repeats.** Real models don't answer the same way twice. Running each case 3, 5 or 10 times
gives each case a pass rate ("2/3"). A case that passed some times and failed others is
marked **Flaky**. With the stand-in AI, one run is enough.

A Test Run keeps a copy of the cases it ran, so changing the Test Set afterwards doesn't
change what an old run says.

## On the command line

```bash
easychain test support-bot.tests.yaml               # as the file says (stand_in: true)
easychain test support-bot.tests.yaml --real-model  # with the flow's real models
easychain test web.tests.yaml --var base_url=http://127.0.0.1:8765
```

Each case shows ✓ or ✗, what failed, and (with –) what was skipped. The exit code is 0 only
when every case passed. That makes it a check for CI, until `easychain eval` compares against
a baseline (5.3).

## The API

```
GET  /api/flows/{id}/tests       (Viewers)  → {file, exists, flow, stand_in, judge_model, cases, variables}
PUT  /api/flows/{id}/tests       (Editors)  ← {cases, stand_in?, judge_model?}
POST /api/flows/{id}/tests/run   (Runners)  ← {cases?: [names], stand_in?, variables?, spec?}
                                            → {results: [{name, passed, failures, skipped, checks, metrics, output}], passed, total}
POST /api/flows/{id}/test-runs   (Runners)  ← {repeat?: 1-10, stand_in?, cases?, variables?}  → 202 {id, version_id}
GET  /api/flows/{id}/test-runs   (Viewers)  → {runs: [{id, status, progress, summary, baseline, …}]}
GET  /api/test-runs/{run_id}     (Viewers)  → … cases: [{name, runs, passed, pass_rate, flaky, failures, seconds, cost}], comparison
POST /api/test-runs/{run_id}/baseline  (Editors)
```

- Saving checks every case first. A problem gets a `422` that says which case and what is
  wrong: two cases with one name, `inputs` that aren't a mapping, a check that doesn't exist.
- `spec` runs the flow as given instead of the saved one, and needs an Editor.
- Running a Test Set is in the [audit log](audit.md).
