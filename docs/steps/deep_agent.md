# Deep Agent

**An agent for longer tasks: it plans with a to-do list, keeps notes and drafts in files, and
hands sub-tasks to Helpers.** In the AI group (Pro library, or search for it). LangChain term:
`create_deep_agent` from [`deepagents`](https://github.com/langchain-ai/deepagents).

An [Agent](agent.md) is good at a question that takes a few tool calls. A Deep Agent is for
work that takes many: research a topic and write a report, go through a list and draft replies,
plan a trip. It has everything an Agent has (tools, MCP tools, Add-ons, an answer format), and
also:

- **a to-do list.** It writes its plan (`write_todos`) and keeps it up to date as it works.
  The run panel shows the list as it changes.
- **files.** It can `ls`, `read_file`, `write_file`, `edit_file`, `delete`, `glob` and `grep`
  files in its own file area: notes, drafts, the report it is asked for. The run panel shows
  each file as it is written.
- **Helpers.** It hands focused sub-tasks to [Helpers](helper.md) with its `task` tool. A
  Helper works on its own (its own role, model and tools), so the Deep Agent's conversation
  stays short, and replies with what it found. It can also hand a task to a **general helper**
  that has the Deep Agent's own tools.

## Settings

The settings of an [Agent](agent.md#settings), and:

| Setting | What it does |
|---|---|
| Helpers | The Helpers it can hand tasks to. Connect a Helper's top handle to the Deep Agent's **Helpers** handle (the round one under the card), or use **Add a Helper** in the inspector. |
| Keep its files in | The Flow Data field for its files (default `files`): `{path: text}`, for example `{"/report.md": "# Otters…"}`. The files are there when it starts, so in a chat it finds the files of earlier turns. Empty: the files are gone when it finishes. |
| Keep its to-do list in *(Pro, More options)* | Also save its to-do list (a list of `{content, status}`) for later steps. |
| Most rounds *(More options)* | Default 500: each model call, tool call and add-on of the Deep Agent and its Helpers counts. |

## The files

The files live in the agent's own state, not on a disk:

- They are kept with every **Save Point**, so a run that a crash cut off carries on with the
  files it had (tested by killing a worker in the middle of a Deep Agent).
- Rewinding to an earlier Save Point rewinds the files too.
- There is no shell: the `execute` tool is left out. Code that needs to run belongs in a
  [Code](code.md) step used as a tool, which runs in the [sandbox](../sandbox.md).
- A later step reads a file like any Flow Data: `{files}` in a prompt, or
  `data["files"]["/report.md"]` in code.

## Approvals

**Ask a person before …** (under Add-ons) can name the Deep Agent's own tools as well as the
steps it uses: `write_file`, `edit_file`, `delete` and `task` (hand a task to a Helper). The run
waits in the [Inbox](../runs.md#ask-a-human-and-the-inbox) as it does for an Agent. The general
helper has the Deep Agent's approvals and limits too, so it can't do without asking what the
Deep Agent would have to ask about.

## In the run panel

The Deep Agent's line in **Steps** shows its to-do list, its tool calls and its files. A task
handed to a Helper shows as **Helper researcher** with the task, what the Helper replied, and its
own tokens and cost; the Helper's own tool calls are under it. The step's total includes its
Helpers' tokens and cost.

## Testing

A Test Set can script the turns of the Deep Agent and its Helpers with `script:`. The turns are
taken in the order the model calls happen: the Deep Agent's, then (after it calls `task`) the
Helper's until it replies, then the Deep Agent's again. For example:

```yaml
script:
  - call: write_todos
    args: {todos: [{content: Find facts, status: in_progress}]}
  - call: task
    args: {subagent_type: researcher, description: Three facts about otters}
  - call: web_search          # the researcher's turn
    args: {query: otters}
  - Otters hold hands while they sleep.   # the researcher's reply
  - call: write_file
    args: {file_path: /report.md, content: "# Otters"}
  - The report is in /report.md.
```

When the Deep Agent hands out several tasks at once, the Helpers run side by side and the order
of their turns isn't fixed; script one task at a time.

## Generated code

```python
model = init_chat_model("openai:gpt-4o-mini")
tools = [read_page_tool(data)]
addons = [ToolErrorMiddleware(tell_agent)]
agent = create_deep_agent(
    model=model,
    tools=tools,
    system_prompt=LEAD_INSTRUCTIONS,
    middleware=[TodoListMiddleware(), FilesystemMiddleware(tools=FILE_TOOLS), *addons],
    subagents=[
        general_helper(model, tools, addons),
        researcher_helper(data),
    ],
    name="lead",
)
result = agent.invoke(
    {"messages": [...], "files": agent_files(data.get("files"))},
    {"recursion_limit": 500},
)
```

Each Helper is a function that returns its `SubAgent` spec. The exported project needs
`deepagents` (it is in its `requirements.txt`).
