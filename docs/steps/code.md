# Code

**Runs a small Python function over Flow Data**, for anything the other steps can't do.
An Action. LangGraph: the function becomes a node.

## Writing code

```python
def run(data):
    """Count the words on the page."""
    words = data.get("page", "").split()
    return {"word_count": len(words)}
```

- Read fields with `data["name"]` or `data.get("name")`. Type `data` in the editor to get field
  suggestions.
- Return a dict of the fields to set. Easy Chain reads the returned keys to know which fields
  the step sets. If you build the dict in a variable, list the fields under **Sets these fields**
  (More options), otherwise they are dropped.
- Imports go at the top. In exported code they move into the module's import block.
- Add packages your code needs under **Packages** (Pro). They go into `requirements.txt`.

## Example

```yaml
- id: count_words
  type: code
  name: Count the words
  settings:
    code: |
      def run(data):
          """Count the words on the page."""
          words = data.get("page", "").split()
          return {"word_count": len(words)}
```

## Limits in this version

- `run` must be a normal function (not `async`) that takes one argument.
- Names that the generated file already uses (such as `graph` or `fill`) can't be redefined.
- **Code runs inside the Easy Chain server process, without a sandbox.** Sandboxed execution
  (containers or microVMs with CPU, memory, time and network limits) comes in Phase 4. Only run
  code you trust.
