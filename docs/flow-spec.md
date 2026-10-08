# The flow spec (version 1)

Every Easy Chain flow is a YAML file (`*.flow.yaml`). The canvas edits it, and the compiler turns
it into LangGraph code. It is meant to be read, reviewed in pull requests, and edited by hand
when that's quicker.

- JSON Schema: [`spec/flow.schema.json`](../spec/flow.schema.json) (add
  `# yaml-language-server: $schema=…` at the top of a file for editor completion)
- Models: [`python/src/easychain/spec/models.py`](../python/src/easychain/spec/models.py)
- Check a file: `easychain validate my.flow.yaml`

## Top level

```yaml
version: 1                 # spec version (required)
name: Summarise a URL      # required
description: …             # optional, goes into the exported code's docstring
settings: {}               # optional: how runs behave (see below)
data: []                   # optional: declared Flow Data fields
steps: []                  # the boxes
connections: []            # the arrows
canvas: {}                 # positions and notes: visual only, always last
```

Settings that equal their default are left out when Easy Chain writes a file. Defaults are part
of the spec version, so they never change under a saved flow.

## How runs behave (`settings`)

```yaml
settings:
  max_steps: 25              # a run stops with an error after this many rounds of steps (recursion_limit)
  max_parallel: 4            # most steps at the same time across a run (max_concurrency); default: no limit
  max_concurrent_runs: 2     # most runs of this flow at once; more wait in the queue; default: no limit
  double_texting: queue      # chat flows, a new message while busy: queue | reject | interrupt | rollback
```

| `double_texting` | When a second message arrives on a conversation that is still running |
|---|---|
| `queue` (default) | It runs after the current one. |
| `reject` | It is refused (HTTP 409) until the current one finishes. |
| `interrupt` | The current run stops where it is (its work so far is kept) and the new one starts. |
| `rollback` | The current run stops and is undone; the new one starts from before it. |

## Flow Data (`data`)

Flow Data is the set of named fields that steps read and write (the LangGraph state). Most
fields are created automatically from Input fields and from each step's **Save as** setting.
Declare a field only to give it a type, a description or an update rule:

```yaml
data:
- name: notes
  type: list          # text | number | yes_no | list | object | file | messages | any
  update: append      # replace (default) | append | merge | add | custom
  description: One note per round
- name: tags
  type: list
  update: custom      # Pro: your own rule
  combine: |
    def combine(old, new):
        return list(dict.fromkeys((old or []) + (new or [])))
```

| Update rule | What happens when a step sets the field | LangGraph |
|---|---|---|
| `replace` | The new value replaces the old one | plain key |
| `append` | Lists are joined and text is concatenated; messages are added to the conversation | `operator.add` / `add_messages` |
| `merge` | Object keys are merged | `merge_dicts` reducer |
| `add` | Numbers are added | `operator.add` |
| `custom` | `combine(old, new)` returns the new value | the function, as the field's reducer |

Easy Chain adds a few private fields of its own (loop round counters, For Each bookkeeping).
They are hidden in the Flow Data panel and reset at the start of every run.

Names use lowercase letters, digits and underscores, and start with a letter. A step id can't be
the same as a field name.

## Steps

```yaml
- id: fetch_page          # unique; becomes the LangGraph node name
  type: http_request      # one of the step types below
  name: Fetch the page    # label on the canvas
  description: ""         # optional
  settings: {…}           # depends on the type
  run:                    # optional: how the step runs (any step but Input and Output)
    retries: 2            # try again this many times if it fails (RetryPolicy)
    retry_wait: 1.0       # seconds before the first retry; doubles each time
    timeout: 30           # seconds before the step is stopped
    cache: true           # reuse the result for the same inputs (CachePolicy)
    cache_ttl: 3600       # seconds a cached result stays valid
    wait_for_all: true    # wait until every parallel branch leading here is done (defer)
```

Step ids can't contain two underscores in a row (`__`); Easy Chain keeps those for its own nodes.

| `type` | Docs |
|---|---|
| `input` | [Input](steps/input.md) |
| `output` | [Output](steps/output.md) |
| `instructions` | [Instructions](steps/instructions.md) |
| `ai_model` | [AI Model](steps/ai_model.md) |
| `http_request` | [Web request](steps/http_request.md) |
| `code` | [Code](steps/code.md) |
| `decision` | [Decision](steps/decision.md) |
| `ask_human` | [Ask a Human](steps/ask_human.md) |
| `for_each` | [For Each](steps/for_each.md) |
| `subflow` | [Sub-flow](steps/subflow.md) |
| `jump` | [Jump](steps/jump.md) |
| `agent` | [Agent](steps/agent.md) |
| `knowledge_search` | [Knowledge Base search](steps/knowledge_search.md) |
| `memory` | [Memory](steps/memory.md) |
| `sql_query` | [Database query](steps/sql_query.md) |
| `mcp_tool` | [MCP tool](steps/mcp_tool.md) |

An Agent's tools are listed in its `settings.tools` by step id; tool steps have no connections.

## Connections

```yaml
connections:
- from: input
  to: fetch_page
- from: is_long        # connections out of a step with exits name the exit they leave from
  exit: Long
  to: detailed
```

Steps with exits: Decision (its exits and *otherwise*), Jump (the same), Ask a Human (*Approved*
and *Rejected*, or its options) and For Each (*Each item* and *When done*).

- A step with several outgoing connections runs the next steps in parallel.
- A step with no outgoing connection ends the run there.
- A connection into an Output step ends the run, and the Output picks what to return.
- Each exit leads to at most one step. An unconnected exit ends the run.

## Placeholders

Text settings such as Instructions, a Web request's URL, headers and body, or an Ask a Human
question can use:

| Placeholder | Becomes |
|---|---|
| `{field}` | The value of a Flow Data field |
| `{secret:NAME}` | A secret from Settings (or the `NAME` environment variable in exported code) |

Secrets only go where a service needs them: a Web request's URL, headers and body, a Database
query's connection, an MCP tool's arguments. Text that is sent to an AI model (Instructions, an
Agent's role and rules, an AI Decision's guidance) or shown to a person (an Ask a Human question)
can't use them; the checks stop the flow with an error instead of sending the secret.

Other braces are kept as literal text, so you can paste JSON into a prompt or a request body
without escaping anything.

## Canvas

```yaml
canvas:
  steps:
    fetch_page: {x: 260, y: 120}
  notes:
  - {id: n1, text: "Remember to add a key", x: 0, y: -100, width: 220, height: 120}
```

Everything here is visual. Moving boxes around only changes this section, so diffs of the logic
above stay clean.

## A complete example

```yaml
version: 1
name: Hello
description: Ask an AI model a question.
steps:
- id: input
  type: input
  name: Input
  settings:
    fields:
    - name: question
      description: What to ask
- id: ask_ai
  type: ai_model
  name: Ask the AI
  settings:
    prompt: question
- id: output
  type: output
  name: Output
  settings:
    fields: [answer]
connections:
- {from: input, to: ask_ai}
- {from: ask_ai, to: output}
```

```bash
easychain run hello.flow.yaml -i question="What is LangGraph?"
easychain compile hello.flow.yaml        # print the LangGraph code
easychain export hello.flow.yaml -o out/ # standalone project
```
