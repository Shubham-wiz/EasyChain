# MCP tool

**Calls a tool on an MCP server you connected in Settings.** An Action. LangChain-stack term:
an MCP tool, via `langchain-mcp-adapters`.

[MCP](https://modelcontextprotocol.io) servers offer tools such as searching company docs,
opening tickets or reading files. Connect them once in **Settings → MCP servers**; then this
step calls one of their tools, and an **Agent** can use any of them (its **MCP tools** setting).

## Settings

| Setting | What it does |
|---|---|
| MCP server | One of the servers connected in Settings. |
| Tool | One of that server's tools; its description shows underneath. Picking it fills in its arguments. |
| Arguments | What to send. Put Flow Data in with `{field}`; a lone `{field}` keeps its type (a number stays a number). |
| Save the result as | The field for the tool's result (default `result`). |

## Connecting servers

In **Settings → MCP servers**, add a server with a name, an id and how to reach it:

- **Over the web**: its URL (streamable HTTP, or server-sent events for older servers) and, if
  it needs one, an `Authorization` header such as `Bearer {secret:DOCS_TOKEN}`.
- **A program on this server** (Pro): a command such as `npx -y @modelcontextprotocol/server-filesystem /data`.
  Such a server can do anything that program can, so only commands on the **approved list** may
  start.

**Show its tools** connects and lists the server's tools, to check it works.

Once saved, a header or environment value written out in full is shown as `••••••`; leave it
as it is to keep it, or type a new one. A value that only names a secret
(`Bearer {secret:DOCS_TOKEN}`) is shown as written.

## Example

```yaml
- id: facts
  type: mcp_tool
  settings:
    server: company_docs
    tool: search
    arguments:
      query: "{question}"
    save_as: found
```

Exported code reads how to reach each server from the `EASYCHAIN_MCP_SERVERS` environment
variable, so no secret is written into the export. It is JSON with one entry per server id, in
the shape `langchain-mcp-adapters` takes:

```json
{
  "docs": {"transport": "streamable_http", "url": "https://example.com/mcp",
           "headers": {"Authorization": "Bearer …"}},
  "files": {"transport": "stdio", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]}
}
```

## Errors you might see

| Message | What to do |
|---|---|
| Pick the MCP server / Pick the tool to call | Choose them in the inspector. |
| The MCP server "…" runs `…` on this machine, and that command isn't on the approved list | Add the command to the approved list in Settings → MCP servers (Pro), if you trust it. |
| No connection for the MCP server(s) … | The flow uses a server id that isn't in Settings (or in `EASYCHAIN_MCP_SERVERS`). |
