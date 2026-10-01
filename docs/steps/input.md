# Input

**Where a run starts.** Lists what the flow needs, such as a URL or a question.
LangGraph: `START` plus the graph's input schema.

## Settings

| Setting | What it does |
|---|---|
| Kind of flow | **Form**: the flow takes named fields. **Chat**: the flow takes chat messages, remembered per conversation in the `messages` field, and runs in the chat panel. |
| Fields | Each field has a name, a type (Text, Number, Yes/No, List, Object, File), a description (shown on the run form), an example (used by **Use examples** and **Try it**), an optional default, and whether it is required. |

## Example

```yaml
- id: input
  type: input
  settings:
    fields:
    - name: url
      description: The web page to summarise
      example: https://en.wikipedia.org/wiki/LangChain
```

## Tips

- A flow has exactly one Input.
- Form values arrive as text and are converted to the field's type ("2.5" becomes 2.5,
  "yes" becomes true, and JSON for lists and objects). A wrong value is reported before the run
  starts.
- In exported code, defaults live in `INPUT_DEFAULTS` and are applied in the `__main__` block.
  When you call `graph.invoke(...)` yourself, pass every input.
