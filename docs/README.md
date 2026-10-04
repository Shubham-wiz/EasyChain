# Easy Chain documentation

Start with the [project README](../README.md). This page lists every document.

## Using Easy Chain

| Page | What it covers |
|---|---|
| [flow-spec.md](flow-spec.md) | The flow file format (YAML): settings, Flow Data, steps, connections, placeholders |
| [runs.md](runs.md) | How runs work: workers, Save Points, Ask a Human and the Inbox, triggers, notifications, the runs API and events |
| [agents.md](agents.md) | Agents and tools: steps as tools, Add-ons, MCP servers, Import an API, structured answers, testing agents |
| [knowledge.md](knowledge.md) | Knowledge Bases: adding documents, search modes, citations, storage, the API |
| [steps/](steps) | One page per step type (below) |

### Steps

| Step | Page | Step | Page |
|---|---|---|---|
| Input | [input.md](steps/input.md) | Ask a Human | [ask_human.md](steps/ask_human.md) |
| Output | [output.md](steps/output.md) | For Each | [for_each.md](steps/for_each.md) |
| Instructions | [instructions.md](steps/instructions.md) | Sub-flow | [subflow.md](steps/subflow.md) |
| AI Model | [ai_model.md](steps/ai_model.md) | Jump | [jump.md](steps/jump.md) |
| Agent | [agent.md](steps/agent.md) | Knowledge Base search | [knowledge_search.md](steps/knowledge_search.md) |
| Web request | [http_request.md](steps/http_request.md) | Memory | [memory.md](steps/memory.md) |
| Code | [code.md](steps/code.md) | Database query | [sql_query.md](steps/sql_query.md) |
| Decision | [decision.md](steps/decision.md) | MCP tool | [mcp_tool.md](steps/mcp_tool.md) |

## Building Easy Chain

| Document | What it covers |
|---|---|
| [ARCHITECTURE.md](../ARCHITECTURE.md) | How it works, every design decision, and where it differs from the build prompt |
| [CONTRIBUTING.md](../CONTRIBUTING.md) | Setting up, conventions, adding a step type |
| [AGENTS.md](../AGENTS.md) | Rules of the build, repo map, commands, traps (read first if you're a coding agent) |
| [python/README.md](../python/README.md) | The Python package: CLI, modules, API endpoints, environment variables, tests |
| [apps/web/README.md](../apps/web/README.md) | The web app: pages, code layout, state stores, tests |
| [packages/client/README.md](../packages/client/README.md) | `@easychain/client`: the TypeScript client and React hook |
| [ROADMAP.md](ROADMAP.md) | What's done and the plan for Phases 4–6, step by step |

## Project history and handover

| Document | What it covers |
|---|---|
| [phases/phase-0-1.md](phases/phase-0-1.md) | Phases 0 and 1 report: foundations and the visual editor |
| [phases/phase-2.md](phases/phase-2.md) | Phase 2 report: the durable runtime |
| [phases/phase-3.md](phases/phase-3.md) | Phase 3 report: agents and knowledge |
| [handover/](handover/README.md) | The project's memory: [status](handover/status.md), [decisions](handover/decisions.md), [conversation](handover/conversation.md), [lessons](handover/lessons.md), [environment](handover/environment.md), [build prompt summary](handover/build-prompt.md), [state.yaml](handover/state.yaml) |
