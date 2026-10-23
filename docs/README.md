# Easy Chain documentation

Start with the [project README](../README.md). This page lists every document.

## Using Easy Chain

| Page | What it covers |
|---|---|
| [flow-spec.md](flow-spec.md) | The flow file format (YAML): settings, Flow Data, steps, connections, placeholders |
| [runs.md](runs.md) | How runs work: workers, Save Points, Ask a Human and the Inbox, triggers, notifications, the runs API and events |
| [agents.md](agents.md) | Agents and tools: steps as tools, Add-ons, MCP servers, Import an API, structured answers, testing agents; Deep Agents and Helpers |
| [audit.md](audit.md) | The audit log: what is recorded and what never is, append-only, reading it in the app, the CLI and the API |
| [network.md](network.md) | Network: which addresses Easy Chain may reach on a shared server (the egress allow-list), what is checked, DNS rebinding, the API |
| [sso.md](sso.md) | Single sign-on with OpenID Connect: setting it up (Okta, Google, Microsoft Entra), how people are matched, what is checked, the test provider |
| [people.md](people.md) | People and sign-in: when it's on, the first account, roles, adding people, keys for scripts, the API |
| [workspaces.md](workspaces.md) | Workspaces: what each keeps apart, the server's Owners, making and switching, people and keys in several, the API, Knowledge Bases and memory |
| [knowledge.md](knowledge.md) | Knowledge Bases: adding documents, search modes, citations, storage, the API |
| [patterns.md](patterns.md) | Agent team patterns: supervisor, handoffs and plan-and-execute, made of ordinary steps |
| [copilot.md](copilot.md) | The describe-it copilot: describe a flow or a change, review it on the canvas, keep the changes you want |
| [skills.md](skills.md) | Skills: the Skills library, the SKILL.md format, using skills in Deep Agents and Helpers |
| [sandbox.md](sandbox.md) | The Code step sandbox: what code can't do, what it needs, settings, Docker Compose |
| [steps/](steps) | One page per step type (below) |

### Steps

| Step | Page | Step | Page |
|---|---|---|---|
| Input | [input.md](steps/input.md) | Ask a Human | [ask_human.md](steps/ask_human.md) |
| Output | [output.md](steps/output.md) | For Each | [for_each.md](steps/for_each.md) |
| Instructions | [instructions.md](steps/instructions.md) | Sub-flow | [subflow.md](steps/subflow.md) |
| AI Model | [ai_model.md](steps/ai_model.md) | Jump | [jump.md](steps/jump.md) |
| Agent | [agent.md](steps/agent.md) | Knowledge Base search | [knowledge_search.md](steps/knowledge_search.md) |
| Deep Agent | [deep_agent.md](steps/deep_agent.md) | Helper | [helper.md](steps/helper.md) |
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
| [ROADMAP.md](ROADMAP.md) | What's done and the plan for Phases 5 and 6, step by step |

## Project history and handover

| Document | What it covers |
|---|---|
| [report.md](report.md) ([PDF](easy-chain-report.pdf)) | **All phases in one report**: what each phase delivered and its evidence, the plan for Phases 5 and 6, quality over time, decisions, risks, and the open questions |
| [phases/phase-0-1.md](phases/phase-0-1.md) | Phases 0 and 1 report: foundations and the visual editor |
| [phases/phase-2.md](phases/phase-2.md) | Phase 2 report: the durable runtime |
| [phases/phase-3.md](phases/phase-3.md) | Phase 3 report: agents and knowledge |
| [phases/phase-4.md](phases/phase-4.md) | Phase 4 report: autopilot and teams (Deep Agents, Helpers, Skills, agent teams, the sandbox, the copilot) |
| [handover/](handover/README.md) | The project's memory: [status](handover/status.md), [decisions](handover/decisions.md), [conversation](handover/conversation.md), [lessons](handover/lessons.md), [environment](handover/environment.md), [build prompt summary](handover/build-prompt.md), [state.yaml](handover/state.yaml) |
