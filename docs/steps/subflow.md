# Sub-flow

**Runs another flow as one step**, so a big flow can be built from small ones and the same piece
reused. LangGraph: a subgraph.

## Settings

| Setting | What it does |
|---|---|
| Flow to run | Any other flow in the workspace. **Double-click the step** (or click *Open the sub-flow*) to open it. |
| Share Flow Data | **On:** the sub-flow reads and writes this flow's fields directly (same names); it is added as a subgraph node. **Off (default):** it gets its own Flow Data; you choose what goes in and what comes back. |
| Give it *(when not sharing)* | Its input field → the value to give it, e.g. `url: {page_url}`. Empty means: this flow's fields with the same names. |
| Save its results as *(when not sharing)* | A field here → the sub-flow's result it comes from. Empty means: its Output fields, same names. |

## How it runs

- The sub-flow's steps run inside the parent run. They show in the trace under the Sub-flow
  step, their tokens and cost count toward it, and an Ask a Human inside it pauses the parent.
- Save Points cover the sub-flow too, so a crash or a pause inside it resumes correctly.
- The exported file contains the sub-flow's code as well (prefixed names), so it still runs
  without Easy Chain.
- A flow can't run itself, directly or through other flows; the check catches it.
- A sub-flow that shares Flow Data is added as a subgraph node, except where a function has to
  call it: as an agent's tool, as the step a For Each runs per item, or with a time limit. There
  a small function runs its graph on this flow's data and returns its results (for a For Each,
  each item's result is the object with the sub-flow's results).
- A sub-flow with an MCP tool, an agent with MCP tools or a time limit inside it (at any depth)
  is awaited (`await …ainvoke(...)`), wherever it runs: as a step, per item or as a tool.

## Checks

| Problem | Meaning |
|---|---|
| There's no flow called … | The flow was renamed or deleted. |
| The flow … has N problems to fix first | Open it and fix them. |
| … has no input called … / doesn't produce … | A mapping names a field the sub-flow doesn't have. |
| With shared Flow Data, the sub-flow's default for … isn't used | Input defaults only apply when data isn't shared. |
| … adds new values to old ones, and a shared sub-flow hands back the whole list | Shared data returns full lists, so *append* fields would double. Map the result instead. |

## Example

```yaml
- id: tidy
  type: subflow
  settings:
    flow: tidy-text
    inputs: {text: "{raw}"}
    outputs: {clean: tidied}
```

Compiles to:

```python
def tidy(data: FlowData) -> dict[str, Any]:
    result = tidy_text_graph.invoke({**TIDY_TEXT__INPUT_DEFAULTS, "text": data.get("raw")})
    return {"clean": result.get("tidied")}
```
