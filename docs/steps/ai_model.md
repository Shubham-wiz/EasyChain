# AI Model

**Sends text or a prompt to an AI model and saves the reply.**
LangChain: a chat model created with `init_chat_model("provider:model")`.

## Settings

| Setting | What it does |
|---|---|
| Model | Provider and model (14 providers, see below). Pick from the list or type any model name the provider offers. |
| Send this field | What the model reads. Empty means what the previous step saved, usually the `prompt` from an Instructions step. A plain text field works too. |
| Save the reply as | The field that holds the reply text. If the field is a messages field (such as `messages` in a chat flow), the reply is added to the conversation instead. |
| Reply format | **Free text**, or **Fixed fields**: build the reply's shape with the field builder (text, number, whole number, yes/no, one of a list, lists, groups of fields; each with a description for the model). The reply is checked against it and saved as an object; **Also save each field on its own** puts each field in Flow Data too, so a Decision can read `sentiment` directly. A reply that doesn't fit is asked for again (**Ask again**, default once). |
| Creativity *(More options)* | `temperature`. Some newer models don't accept it, and the checks will tell you. |
| Longest reply *(More options)* | `max_tokens` |
| Thinking effort, Stop at, Time limit, Retries, Custom endpoint, Key for the custom endpoint *(Pro)* | `reasoning_effort` (OpenAI), `stop`, `timeout`, `max_retries`, `base_url`, `api_key` (the name of a secret, read from the environment in exported code) |

## Keys

| Provider | Model prefix | Key or settings |
|---|---|---|
| OpenAI | `openai:` | `OPENAI_API_KEY` |
| Anthropic | `anthropic:` | `ANTHROPIC_API_KEY` |
| Ollama (local) | `ollama:` | none; set `OLLAMA_HOST` if it isn't on localhost |
| Google Gemini | `google_genai:` | `GOOGLE_API_KEY` |
| Google Vertex AI | `google_vertexai:` | Google Cloud credentials (`gcloud auth application-default login`); `pip install 'easychain[vertex]'` |
| AWS Bedrock | `bedrock_converse:` | AWS credentials (`AWS_PROFILE` or `AWS_ACCESS_KEY_ID` …) and `AWS_REGION` |
| Azure OpenAI | `azure_openai:` | `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `OPENAI_API_VERSION`; the model name is your deployment |
| Mistral | `mistralai:` | `MISTRAL_API_KEY` |
| Groq | `groq:` | `GROQ_API_KEY` |
| Together AI | `together:` | `TOGETHER_API_KEY` |
| Fireworks AI | `fireworks:` | `FIREWORKS_API_KEY` |
| OpenRouter | `openrouter:` | `OPENROUTER_API_KEY` |
| DeepSeek | `deepseek:` | `DEEPSEEK_API_KEY` |
| xAI | `xai:` | `XAI_API_KEY` |

OpenAI, Anthropic and Ollama are always installed; the others come with
`pip install 'easychain[providers]'` (the Docker image and `make install` include them). Add
keys under **Settings → Keys and providers** (**More providers** lists the rest). Without a key
the step fails with **Add your API key** and **Try with the stand-in AI** buttons.

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

With **Fixed fields**, the step uses `model.with_structured_output(Reply)`, where `Reply` is a
Pydantic model generated from your fields, with retries for replies that don't fit.

## After a run

The step shows the time it took, tokens in and out, and an estimated cost (from list prices).
While it runs, the reply streams inside the box.
