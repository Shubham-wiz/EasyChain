# Agents and tools

An **Agent** step is an AI that works in rounds: it reads the conversation, calls tools, reads
what they return, and goes on until it can answer. Use it when the path to the answer depends on
what the AI finds along the way. When you know the path, plain steps (an AI Model, a Decision)
are faster, cheaper and easier to test.

![The SQL analyst template: an Agent with three tools](images/agent.png)

This page covers giving an agent tools, the Add-ons, MCP servers, importing an API, and
structured answers. The settings are listed on the [Agent step page](steps/agent.md).

## Tools are steps

Any Web request, Code, Sub-flow, Knowledge Base search, Database query, MCP tool or Memory step
can be a tool. Drag its purple top handle to the agent's **Tools** handle (or use **Add a new
tool** in the agent's inspector). The step then:

- runs only when the agent calls it, so it has no connections of its own;
- is offered to the model under its id, with its **description** as the explanation. Write the
  description for the model: what it does, what it returns, when to use it;
- takes as arguments the Flow Data fields it reads that the agent can't already see. A Web
  request to `…/orders/{order_id}` becomes `get_order(order_id)`. Describe `order_id` in the
  Flow Data panel and the model sees that description too;
- keeps its own run policy (retries, time limit) and its "send at most once" protection, so an
  agent retrying a POST doesn't send it twice. A tool that runs out of retries or time fails that
  call, and the agent is told (or the run stops, if **When a tool fails** says so). "Reuse
  results" and "Wait for all branches" don't apply to tools; the checks point them out.

While a run is going, each call appears under the agent with its arguments, status and time, and
the dashed line to the tool lights up. Click the agent in the trace to see every call and result.

## Add-ons

Add-ons are LangChain agent middleware, each behind a toggle. The ones you will use most:

- **Ask a person before …**: pick the tools that need a person's OK. The run pauses before each
  call; in the run panel or the Inbox a person approves it, edits the arguments, or rejects it
  with a comment the agent reads. Use it for anything that sends, buys, deletes or changes data.
- **Limits**: most model calls and most tool calls per run. An agent that hits a limit stops
  with a clear message.
- **When a tool fails**: tell the agent (it can often recover) or stop the run.
- **Long-term memory**: `remember` and `recall` tools that keep facts about a user across
  conversations (in the flow's store, by `user_id`).

The rest (fallback models, retries, summarising long chats, clearing old tool results, personal
data filters, tool selection for large toolsets, a to-do list, pretend tools for testing) are
described on the [step page](steps/agent.md#add-ons).

## MCP servers

[MCP](https://modelcontextprotocol.io) servers offer ready-made tools. Connect them once in
**Settings → MCP servers**:

1. **Add a server**, give it a name and an id.
2. **Over the web**: paste its URL; if it needs a token, put `Bearer {secret:NAME}` in the
   Authorization header and add the secret under Settings. **A program on this machine** (Pro):
   the command to start it; only commands on the **approved list** can start, because such a
   server can do anything that program can. Approve the whole command line
   (`npx -y @modelcontextprotocol/server-filesystem /data`), which must then match exactly.
   Approving a program on its own (`npx`, `uvx`, `python`, `node`) lets it start with any
   arguments, so it approves anything that program can download or run.
3. **Show its tools** checks the connection and lists what it offers. **Save**.

Then tick the server's tools in an agent's **MCP tools**, or call one tool from an
[MCP tool step](steps/mcp_tool.md). In exported code the servers come from the
`EASYCHAIN_MCP_SERVERS` environment variable: JSON with one connection per server id, as on the
[MCP tool step page](steps/mcp_tool.md).

## Import an API

**Import an API** (in the editor's top bar) turns an OpenAPI (Swagger) description into Web
request steps:

1. Paste the description's address (`https://…/openapi.json`) or the JSON/YAML itself, and
   **Read it**. A description read from an address may be up to 25 MB.
2. Tick the operations you need. Choose to add them **as steps on the canvas** or **as tools of
   an agent**, the server address, and optionally a header and secret for the API key. A server
   address the description gives relative to itself (like `/api/v3`) is completed from the
   description's own address.
3. Each operation becomes a Web request with its summary as the description, path and required
   query parameters as `{fields}`, and a JSON body. Their parameters become Flow Data fields with
   the API's types and descriptions, so an agent knows how to fill them.

Optional query parameters are not sent; the step's description lists them so you can add them by
hand.

## Structured answers

Set **Answer format** to **Fixed fields** and build the shape of the answer: text, numbers,
yes/no, one of a list, lists and groups of fields, each with a description. The agent must
answer in that shape (LangChain's `ToolStrategy`); the answer is saved as an object, and
optionally each field on its own so later steps can use `answer.total` as `total`. The AI Model
step has the same builder (**Reply format**).

## Testing agents

Agent runs aren't deterministic, so test what matters: which tools were called and what came
out. Test Sets (Phase 5 brings the full editor) can already check that:

```yaml
flow: order-helper.flow.yaml
cases:
  - name: Looks up the order before answering
    inputs: {question: "Where is order 1042?"}
    script:                              # the stand-in AI's turns (ignored by real models)
      - {call: get_order, args: {order_id: 1042}}
      - "Order 1042 shipped yesterday."
    expect:
      status: ok
      tools: [get_order]                 # these tools were called
      not_tools: [cancel_order]          # these were not
      tool_calls: {max: 3}               # at most 3 calls (or an exact number)
      output.answer: {contains: shipped}
```

```bash
uv run easychain test order-helper.tests.yaml --stand-in   # scripted: no model, no key
uv run easychain test order-helper.tests.yaml              # with the real model
```

A **script** plays the model's turns (a tool call, several calls at once with `{calls: [...]}`,
a structured answer with `{answer: {...}}`, or plain text), so the flow's own logic (tools,
approvals, limits, what gets saved) is tested without a model or a key. The SQL analyst
template's Test Set (`python/src/easychain/templates/sql-analyst.tests.yaml`) has examples.
