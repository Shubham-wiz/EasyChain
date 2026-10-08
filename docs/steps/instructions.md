# Instructions

**Writes the prompt for an AI Model**, filling in `{variables}` from Flow Data.
LangChain: `ChatPromptTemplate`.

## Settings

| Setting | What it does |
|---|---|
| Role and rules | The system message: who the AI is and how to behave. |
| Message | The request itself, for example `Summarise this page in 3 bullet points: {page}`. |
| Include chat history from *(More options)* | A messages field, usually `messages`, inserted before the message. Use this in chat flows. |
| Examples *(More options)* | Sample turns ("The user says…", "The AI answers…") that show the model what you want (few-shot). |
| Save the prompt as *(More options)* | The field the next AI Model reads. Default: `prompt`. |

## Example

```yaml
- id: write_prompt
  type: instructions
  settings:
    system: You summarise web pages for busy people.
    user: |-
      Summarise this page in three bullet points.

      {page}
```

Compiles to:

```python
WRITE_PROMPT_MESSAGE = """\
Summarise this page in three bullet points.

{page}"""

write_prompt_template = ChatPromptTemplate.from_messages(
    [
        ("system", "You summarise web pages for busy people."),
        ("human", WRITE_PROMPT_MESSAGE),
    ]
)
```

## Tips

- Click **+ Insert field** to add a variable. Variables that aren't Flow Data fields show in
  red, and the checks suggest the closest name ("Did you mean `{page}`?").
- Only `{name}` is a variable. Other braces, such as JSON examples, are kept as written.
- `{secret:NAME}` can't be used here: everything in Instructions is sent to the AI model, so the
  checks refuse it. Keep keys in the step that uses them (a Web request header, a database URL).
