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
data: []                   # optional: declared Flow Data fields
steps: []                  # the boxes
connections: []            # the arrows
canvas: {}                 # positions and notes: visual only, always last
```

Settings that equal their default are left out when Easy Chain writes a file. Defaults are part
of the spec version, so they never change under a saved flow.

## Flow Data (`data`)

Flow Data is the set of named fields that steps read and write (the LangGraph state). Most
fields are created automatically from Input fields and from each step's **Save as** setting.
Declare a field only to give it a type, a description or an update rule:

```yaml
data:
- name: notes
  type: list          # text | number | yes_no | list | object | file | messages | any
  update: append      # replace (default) | append | merge | add
  description: One note per round
```

| Update rule | What happens when a step sets the field | LangGraph |
|---|---|---|
| `replace` | The new value replaces the old one | plain key |
| `append` | Lists are joined and text is concatenated; messages are added to the conversation | `operator.add` / `add_messages` |
| `merge` | Object keys are merged | `merge_dicts` reducer |
| `add` | Numbers are added | `operator.add` |

Names use lowercase letters, digits and underscores, and start with a letter. A step id can't be
the same as a field name.

## Steps

```yaml
- id: fetch_page          # unique; becomes the LangGraph node name
  type: http_request      # one of the step types below
  name: Fetch the page    # label on the canvas
  description: ""         # optional
  settings: {…}           # depends on the type
```

| `type` | Docs |
|---|---|
| `input` | [Input](steps/input.md) |
| `output` | [Output](steps/output.md) |
| `instructions` | [Instructions](steps/instructions.md) |
| `ai_model` | [AI Model](steps/ai_model.md) |
| `http_request` | [Web request](steps/http_request.md) |
| `code` | [Code](steps/code.md) |
| `decision` | [Decision](steps/decision.md) |

## Connections

```yaml
connections:
- from: input
  to: fetch_page
- from: is_long        # connections out of a Decision name the exit they leave from
  exit: Long
  to: detailed
```

- A step with several outgoing connections runs the next steps in parallel.
- A step with no outgoing connection ends the run there.
- A connection into an Output step ends the run, and the Output picks what to return.
- Each Decision exit leads to at most one step. An unconnected exit ends the run.

## Placeholders

Text settings of Instructions and Web request steps can use:

| Placeholder | Becomes |
|---|---|
| `{field}` | The value of a Flow Data field |
| `{secret:NAME}` | A secret from Settings (or the `NAME` environment variable in exported code) |

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
