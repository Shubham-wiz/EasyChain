# Agent

**An AI that decides which tools to use, step by step, until it has an answer.** In the AI
group. LangChain term: `create_agent` (a model + tools loop, with middleware).

Give the agent a job in **Role and rules**, connect the steps it may use to its **Tools**
handle (the purple square under the card), and describe each tool in a sentence. On each round
the model reads the conversation, either calls one or more tools or answers, and the tool
results go back to it. The canvas shows each tool call as it happens, under the Agent card and
along the dashed tool line.

## Settings

| Setting | What it does |
|---|---|
| Model | The model that thinks and picks tools. Pick one that is good at tool use (for example `openai:gpt-4o-mini`, `anthropic:claude-sonnet-4-5`). |
| Role and rules | The system prompt: who the agent is and how it should work. Put Flow Data in with `{field}`. |
| Work on | A text field (a question) or a messages field (a chat). Empty means: what the previous step saved. |
| Tools | Steps the agent can call (see below). Each tool's own settings, retries, time limit and "send at most once" still apply. |
| MCP tools | Tools from servers connected in **Settings → MCP servers**: all of a server's tools, or the ones you tick. |
| Save the answer as | The field for the final answer (default `answer`). |
| Answer format | Free text, or **Fixed fields** built with the field builder: the answer is checked against them and saved as an object (and, if you like, each field on its own). |
| Add-ons | Middleware you switch on with a toggle; see below. |
| Creativity *(More options)* | Temperature. |
| Most rounds *(More options)* | Stops an agent that goes round in circles (default 100; each model call and tool call counts). |
| Keep the whole conversation in *(More options)* | Also save every message, tool calls included, in a messages field. |

## Tools

Web request, Code, Sub-flow, Knowledge Base search, Database query, MCP tool and Memory steps
can be tools. A step used as a tool:

- has no connections of its own; it runs only when the agent calls it ("Tool of Helper" shows
  on its card);
- is described to the model by its **description** (the check before a run warns when it is
  empty);
- takes as **arguments** the Flow Data fields it reads that the agent can't already see. Give
  those fields a description in the Flow Data panel: it becomes the argument's description.
  For example a Web request to `https://api.example.com/orders/{order_id}` becomes the tool
  `get_order(order_id)`;
- returns its main result to the agent as text;
- keeps its own **retries** and **time limit** (under *When it runs*): a call is tried again
  after a failure that can pass (a dropped connection, a 5xx reply) and stopped when it takes
  too long, and then the agent is told it failed. "Reuse results" and "Wait for all branches"
  don't apply to a tool; the checks say so.

Add a tool from the inspector (**Add a new tool**, or **Use a step on the canvas**), by
dragging a step's purple top handle onto the agent's Tools handle, or by importing an API (see
[Agents and tools](../agents.md#import-an-api)).

## Add-ons

| Add-on | LangChain middleware | What it does |
|---|---|---|
| Ask a person before … | `HumanInTheLoopMiddleware` | Per tool: the run pauses before each call; a person approves it, edits the arguments, or rejects it with a comment for the agent. The request shows in the run panel and the Inbox. |
| Limits | `ModelCallLimitMiddleware`, `ToolCallLimitMiddleware` | Most model calls and tool calls in one run. |
| When a tool fails | `ToolErrorMiddleware`-style handling | Tell the agent (so it can try another way) or stop the run. |
| Try again | `ToolRetryMiddleware`, `ModelRetryMiddleware` | Retry failing tool calls or model calls. |
| Fallback models | `ModelFallbackMiddleware` | Models to try, in order, when the main one fails. |
| Summarise long chats | `SummarizationMiddleware` | Summarise older messages past a token budget, keeping the latest ones. |
| Clear old tool results | `ContextEditingMiddleware` (`ClearToolUsesEdit`) | Drop old tool output when the conversation gets long. |
| Personal data | `PIIMiddleware` | Redact, mask, hash or block email addresses, card numbers, IP and MAC addresses, web addresses. |
| Pick tools | `LLMToolSelectorMiddleware` | With many tools, a model first picks the few that matter for each request. |
| To-do list | `TodoListMiddleware` | Gives the agent a to-do list for longer tasks. |
| Long-term memory | LangGraph store | The agent gets `remember` and `recall` tools for facts about the user (see [Memory](memory.md)). |
| Pretend tools *(testing)* | `LLMToolEmulator` | A model makes up tool results; nothing really runs. The check before a run warns while it is on. |

## Example

```yaml
- id: helper
  type: agent
  name: Order helper
  settings:
    model: openai:gpt-4o-mini
    instructions: You help customers with their orders. Look the order up before answering.
    input: question
    tools: [get_order, cancel_order]
    addons:
      approve_tools: [cancel_order]
      max_tool_calls: 10
- id: get_order
  type: http_request
  description: Looks up an order by its number and returns its status.
  settings:
    url: https://api.example.com/orders/{order_id}
    response: json
    save_as: order
```

## In the exported code

The agent is built with `langchain.agents.create_agent` inside the step's node, so it shares
the flow's checkpointer and store: approvals, retries and resumes work the same as in Easy
Chain. Each tool step becomes a small `@tool` function that calls the step's own node; a
tool with retries or a time limit calls it through `with_run_policy`, which applies them the
way LangGraph does for a step (so the agent's node is `async`).

## Errors you might see

| Message | What to do |
|---|---|
| This agent has no tools yet | Connect a step to its Tools handle, or use an AI Model step instead. |
| Describe what this tool does | Fill in the tool step's description; the agent reads it to decide when to call it. |
| A step used as a tool … can't also be connected | Remove its connections, or take it off the agent's tools. |
| "Reuse results" doesn't apply when an agent uses this step as a tool | Switch it off under *When it runs*; the tool runs every time it is called. |
| … took longer than its time limit | A tool ran out of time; the agent is told. Raise the tool's time limit if it needs longer. |
| An agent can't use a … step as a tool | Only the step types listed above can be tools. |
| Approval is set for "…", which isn't one of this agent's tools | Remove the approval, or add the tool. |
| The agent "…" took more than … rounds | It went round in circles: make the role and rules clearer, add limits, or raise **Most rounds**. |
| The agent stopped (a limit was reached) before giving its answer | A model or tool call limit ended the run before a structured answer was ready. Raise the limit. |
