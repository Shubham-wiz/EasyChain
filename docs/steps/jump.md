# Jump

**Sets some Flow Data and picks the next step in one move.** Handy for counters, flags and
retry loops. A Pro step. LangGraph: a node that returns `Command(update=…, goto=…)`.

## Settings

| Setting | What it does |
|---|---|
| Set these fields | Each field gets a value: text with `{field}` placeholders (a lone `{field}` keeps its type), or in Pro an expression such as `(count or 0) + 1`. Later fields see the earlier ones. |
| Then go to | Exit rules, checked on the **updated** data, top to bottom; the first match wins. Same rules as a Decision. |
| When nothing matches, take | The fallback exit (default `Next`). |

## Example

```yaml
- id: bump
  type: jump
  settings:
    updates:
    - {field: count, expression: "(count or 0) + 1"}
    - {field: note, value: "round {count}"}
    exits:
    - label: Again
      when: {expression: "count < limit"}
    otherwise: Done
```

Compiles to:

```python
def bump(data: FlowData) -> Command:
    data = {**data}  # later fields see the earlier ones
    data["count"] = (data.get("count") or 0) + 1
    data["note"] = fill("round {count}", data)
    update = {name: data[name] for name in ("count", "note")}
    if data.get("count") < data.get("limit"):
        return Command(update=update, goto="check")
    return Command(update=update, goto=END)

builder.add_node("bump", bump, destinations=("check", END))
```
