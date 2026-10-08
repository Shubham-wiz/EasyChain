# Web request

**Fetches a web page or calls an API, and saves what comes back.** An Action.
LangChain term: an HTTP request tool.

## Settings

| Setting | What it does |
|---|---|
| URL | The address to call. Use `{field}` for Flow Data, for example `{url}` or `https://api.example.com/search?q={question}`. Values are URL-encoded for you, `/` included, so a value (one an agent picked, say) can't point the request at another address: `{order_id}` set to `../admin` stays inside its part of the path. A value that would leave a part of the path empty, `.` or `..` stops the step with an error. Only a field that starts the URL, like `{base_url}/orders/{order_id}`, is used as it is. |
| Method | GET reads a page; POST, PUT, PATCH and DELETE send data. |
| Keep | **Readable text** (drops menus, scripts and HTML tags; best for web pages), **the raw response**, or **JSON data** (for APIs). |
| Save the result as | The field that holds the result. |
| Headers *(More options)* | Extra headers. Put API keys in as `{secret:MY_API_KEY}` so they never appear in the flow. |
| Body *(More options)* | Data to send. If it looks like JSON, values are inserted as properly escaped JSON, so quotes and newlines can't break it. |
| Keep at most, Time limit *(More options)* | Cut long pages (default 20,000 characters) and set the timeout (default 30 s). |
| Send it at most once *(More options)* | For requests that change something. On by default for POST, PUT, PATCH and DELETE. A retry or a resume after a crash reuses the first answer instead of sending again, and the request carries an `Idempotency-Key` header that stays the same, so services that honour it never act twice. |

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
| {field} in the URL is empty (or “..”), which would call a different address | Give the field a real value. To call a path with several parts from one field, start the URL with it (`{page_url}`) or keep the parts in separate fields. |
| The response wasn't JSON | Set **Keep** to the raw response or readable text. |
