# Output

**Where a run ends.** Picks which Flow Data fields to hand back.
LangGraph: `END` plus the graph's output schema.

## Settings

| Setting | What it does |
|---|---|
| Return these fields | The fields a run returns. Leave it empty to return all Flow Data (the checks will suggest picking one). |

Chat flows always return `messages`, and the chat panel shows the last AI message.

## Example

```yaml
- id: output
  type: output
  settings:
    fields: [summary]
```

## Tips

- A flow can have several Output steps, one at the end of each branch. The returned fields are
  the union of all of them.
- "Output returns `x`, but no step before it saves that field" means the field is set on another
  branch, or not at all.
