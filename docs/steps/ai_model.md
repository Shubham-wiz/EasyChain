# AI Model

**Sends text or a prompt to an AI model and saves the reply.**
LangChain: a chat model created with `init_chat_model("provider:model")`.

## Settings

| Setting | What it does |
|---|---|
| Model | Provider and model: OpenAI, Anthropic or Ollama (local). Pick from the list or type any model name the provider offers. |
| Send this field | What the model reads. Empty means what the previous step saved, usually the `prompt` from an Instructions step. A plain text field works too. |
| Save the reply as | The field that holds the reply text. If the field is a messages field (such as `messages` in a chat flow), the reply is added to the conversation instead. |
| Creativity *(More options)* | `temperature`. Some newer models don't accept it, and the checks will tell you. |
| Longest reply *(More options)* | `max_tokens` |
| Thinking effort, Stop at, Time limit, Retries, Custom endpoint *(Pro)* | `reasoning_effort` (OpenAI), `stop`, `timeout`, `max_retries`, `base_url` |

## Keys

| Provider | Key | Get one |
|---|---|---|
| OpenAI | `OPENAI_API_KEY` | platform.openai.com/api-keys |
| Anthropic | `ANTHROPIC_API_KEY` | console.anthropic.com |
| Ollama | none (runs locally; set `OLLAMA_HOST` if it isn't on localhost) | ollama.com |

Add keys under **Settings → API keys**. Without a key the step fails with **Add your API key**
and **Try with the stand-in AI** buttons.

## Example

```yaml
- id: summarise
  type: ai_model
  settings:
    model: openai:gpt-4o-mini
    temperature: 0.2
    save_as: summary
```

Compiles to:

```python
def summarise(data: FlowData) -> dict[str, Any]:
    """AI Model · Summarise

    Sends `prompt` to openai:gpt-4o-mini and saves the reply as `summary`.
    """
    model = init_chat_model("openai:gpt-4o-mini", temperature=0.2)
    reply = model.invoke(data["prompt"])
    return {"summary": reply.text}
```

## After a run

The step shows the time it took, tokens in and out, and an estimated cost (from list prices).
While it runs, the reply streams inside the box.
