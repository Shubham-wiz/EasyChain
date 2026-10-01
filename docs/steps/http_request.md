# Web request

**Fetches a web page or calls an API, and saves what comes back.** An Action.
LangChain term: an HTTP request tool.

## Settings

| Setting | What it does |
|---|---|
| URL | The address to call. Use `{field}` for Flow Data, for example `{url}` or `https://api.example.com/search?q={question}`. Values are URL-encoded for you. |
| Method | GET reads a page; POST, PUT, PATCH and DELETE send data. |
| Keep | **Readable text** (drops menus, scripts and HTML tags; best for web pages), **the raw response**, or **JSON data** (for APIs). |
| Save the result as | The field that holds the result. |
| Headers *(More options)* | Extra headers. Put API keys in as `{secret:MY_API_KEY}` so they never appear in the flow. |
| Body *(More options)* | Data to send. If it looks like JSON, values are inserted as properly escaped JSON, so quotes and newlines can't break it. |
| Keep at most, Time limit *(More options)* | Cut long pages (default 20,000 characters) and set the timeout (default 30 s). |

## Example

```yaml
- id: search
  type: http_request
  settings:
    method: POST
    url: https://api.example.com/search
    headers:
      Authorization: Bearer {secret:SEARCH_API_KEY}
    body: '{"query": "{question}"}'
    response: json
    save_as: results
```

## Errors you might see

| Message | What to do |
|---|---|
| The web request got "404 Not Found" from … | Check the URL. |
| … "401" or "403" … | The site needs a login or API key, or blocks automated requests. |
| Couldn't reach … / took too long | Check the address and your connection, or raise the time limit. |
| A header value is empty or invalid | A `{secret:NAME}` it uses isn't set. Add it in Settings → Secrets. |
| The response wasn't JSON | Set **Keep** to the raw response or readable text. |
