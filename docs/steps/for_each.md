# For Each

**Runs a step once for every item in a list, side by side, and collects the results in order.**
LangGraph: a conditional edge that returns one `Send` per item, and a node that waits for all
of them (map-reduce).

## Settings

| Setting | What it does |
|---|---|
| Go through | A list field. Empty means: what the previous step saved. |
| Call each item | The field that holds the current item for the step that runs per item (default `item`). |
| Save the results as | A list with what that step produced for each item, in the original order (default `results`). |
| At most this many at once *(More options)* | Limits how many items run at the same time (LangGraph `max_concurrency`). |

## Exits

- **Each item** leads to the step to run for every item: an AI Model, Instructions, Web request,
  Code or a **Sub-flow** (to do several things per item, put them in a Sub-flow). That step can't
  lead anywhere else, and nothing else can lead into it.
- **When done** leads on once every item has finished.

On the canvas a dashed *results* line goes from the per-item step back to the For Each. While it
runs, the For Each shows a progress bar (“3 of 10 done”) and the trace lists each item's result.

## What each item produces

The result for an item is what the per-item step saves (its **Save as** field; for a Code step,
the first field it returns; for a Sub-flow, the first mapped result, or the object with all its
results when it shares Flow Data). An empty list skips straight to **When done** with an empty
result list.

## Example

```yaml
- id: each_page
  type: for_each
  settings: {items: urls, item_name: url, save_as: pages, concurrency: 4}
- id: fetch_page
  type: http_request
  settings: {url: "{url}", save_as: page}
connections:
- {from: each_page, exit: Each item, to: fetch_page}
- {from: each_page, exit: When done, to: write_prompt}
```

Compiles to (shortened):

```python
def send_each_page(data: FlowData) -> list[Send] | str:
    items = data.get("urls") or []
    if not items:
        return "each_page__done"
    return [Send("fetch_page", {**data, "url": item, "each_page_index": number}) for number, item in enumerate(items)]

def fetch_page_for_each_page(data: FlowData) -> dict[str, Any]:
    result = fetch_page(data) or {}
    return {"each_page_results": [(data["each_page_index"], result.get("page"))]}

builder.add_conditional_edges("each_page", send_each_page, ["fetch_page", "each_page__done"])
builder.add_node("each_page__done", each_page_done, defer=True)
```
