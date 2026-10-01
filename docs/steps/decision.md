# Decision

**Sends the flow down one of several paths**, by a rule or by asking an AI.
LangGraph: a node plus a conditional edge.

On the canvas a Decision is a diamond with labelled exits. Drag from an exit's dot to the step it
should lead to. After a run, the exit it took is highlighted.

## Deciding by rules

Each exit has a rule: **field**, **check**, **value**. Rules are checked top to bottom and the
first match wins. If nothing matches, the run takes the **otherwise** exit.

| Check | Meaning |
|---|---|
| contains / doesn't contain | Text includes the value (ignoring case); for lists, the list includes it |
| is / is not | Equals the value (ignoring case for text) |
| starts with / ends with | Text starts or ends with the value |
| matches pattern | A regular expression |
| is empty / is not empty | Has no value, or has one |
| is more than / is less than | Number comparison |
| is longer than / is shorter than | Length of text or list |
| is yes / is no | For Yes/No fields |

**Pro:** switch an exit to an expression such as `len(page) > 5000 and "error" not in page`.
Bare names are Flow Data fields. Only comparisons, `and`/`or`/`not`, arithmetic and simple
functions (`len`, `str`, `int`, `float`, `min`, `max`, `any`, `all`, plus methods like `.lower()`)
are allowed.

## Deciding by asking an AI

Set **Decide by** to *Asking an AI to pick an exit*. Give each exit a short description ("the
customer is unhappy or wants a refund"). The AI reads the field you choose (by default what the
previous step saved) and replies with an exit name. The choice is saved in **Save the chosen exit
as** (default `choice`).

## Example

```yaml
- id: is_long
  type: decision
  settings:
    exits:
    - label: Long
      when: {field: word_count, op: greater_than, value: 800}
    otherwise: Short
connections:
- {from: is_long, exit: Long, to: detailed}
- {from: is_long, exit: Short, to: brief}
```

Compiles to:

```python
def route_is_long(data: FlowData) -> str:
    """Pick the exit for the Decision "Is the page long?"."""
    if float(data.get("word_count") or 0) > 800:
        return "Long"
    return "Short"

builder.add_conditional_edges("is_long", route_is_long, {"Long": "detailed", "Short": "brief"})
```

## Loops

An exit may lead back to an earlier step to make a loop. A loop needs a Decision with an exit
that leaves it. A run stops with a clear error after 25 steps if it never leaves. Configurable
loop limits come in Phase 2.
