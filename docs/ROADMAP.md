# Roadmap: what's done, and the plan for what's left

_Written at the end of Phase 3 (version 0.3.0). The live status is in
[handover/status.md](handover/status.md); this page is the plan. Update both as work lands._

## Where things stand

| Phase | Scope | State |
|---|---|---|
| 0. Foundations | Monorepo, flow spec, compiler, runtime, CLI, API | ✅ Done ([report](phases/phase-0-1.md)) |
| 1. Visual MVP | Canvas, inspector, run panel, chat, export, templates | ✅ Done ([report](phases/phase-0-1.md)) |
| 2. Real runtime | Durable runs, workers, Ask a Human, Inbox, Save Points, time travel, triggers | ✅ Done ([report](phases/phase-2.md)) |
| 3. Agents and knowledge | Agent + add-ons, MCP, OpenAPI import, structured replies, Knowledge Bases, memory, 14 providers | ✅ Done ([report](phases/phase-3.md)) |
| 4. Autopilot and teams | Sandboxes, Deep Agents, Helpers, Skills, multi-agent patterns, describe-it copilot | Next |
| 5. Platform | Test Sets and Checks, Test Runs, CI gate, dashboards, model gateway, Publish, environments, roles, SSO, audit, tracing | Planned |
| 6. Ecosystem | LangGraph.js export, custom step registry, import, collaboration, prompt optimisation, Helm | Planned |

At the end of Phase 3 the tests were:

| Suite | Passing |
|---|---|
| Python | 320 |
| Web unit | 32 |
| Client package | 4 |
| Playwright journeys | 31 |
| Template Test Sets | all 8 |

## Step 0: for the owner, before more building

1. ~~**Push the code.**~~ Done. `main` is on GitHub with the full history.
2. **Watch the first CI run.** CI runs on every push to `main`, and the first one started when
   `main` was pushed.
   CI runs these jobs:
   - lint;
   - Python tests on Postgres with pgvector;
   - web and client tests;
   - Playwright;
   - a Docker Compose smoke test.

   Expect the first run to need small fixes for the CI environment (paths, browser install,
   image pulls). Getting CI green is the first job of the next session.
3. **Answer the three Phase 4 questions** (from the [Phase 3 report](phases/phase-3.md#questions-for-you)).
   "Whatever you think is right" means the recommended option of each:
   - **Phase 4's "Done when".** Recommended: the proposal below.
   - **Sandbox backend.** Recommended: Docker, plus gVisor where it is installed. The
     alternative is a hosted sandbox (E2B).
   - **The copilot's model.** Recommended: any model you have a key for. The alternative is to
     require a strong model.
4. **Optional:** commit the original build brief as `docs/handover/build-prompt.original.md`.
   The "Done when" for Phases 4–6 below are proposals reconstructed without it.
5. **Before a release:** try the agent templates and the copilot with a real key. CI never calls
   paid models.

## Phase 4: Autopilot and teams

**Proposed "Done when"**:

1. A **Research assistant** template passes its 10-case Test Set. It is a Deep Agent that plans
   with a to-do list, hands sub-tasks to two Helpers, and writes a report to its files.
2. A Code step can't read the host's files, reach the network outside its allow-list, or
   outlive its limits. Escape tests prove each of these.

| # | Milestone | What it delivers | How it's tested | Size |
|---|---|---|---|---|
| 4.1 | **Code sandbox** | Code steps (and Code tools of agents) run in a container.<br>**Limits:** no host file system, network off or limited to an allow-list, CPU, memory, time and process limits, read-only image.<br>**Interface:** a `SandboxBackend` with a Docker backend now; gVisor (`runsc`) when installed; hosted backends (E2B, Modal) as plugins.<br>**Packages:** per flow, from an allow-list, cached in an image layer.<br>**Settings:** a "Run Code steps in a sandbox" setting, on by default when Docker is available. | Escape tests: read `/etc/passwd` of the host, open a socket, fork-bomb, allocate past the limit, sleep past the time limit, write outside the work folder. All must fail cleanly with a plain-language error. Existing Code step tests pass inside the sandbox. | L |
| 4.2 | **Deep Agent step** | `deepagents.create_deep_agent`: planning (to-do list), a virtual file area kept in the LangGraph store (survives restarts and shows in the run panel), the Phase 3 tools and add-ons, approvals on chosen tools. | Golden code; behaviour tests with scripted turns; files survive a worker crash; approval inside a Deep Agent goes to the Inbox. | L |
| 4.3 | **Helpers (sub-agents)** | Helpers of a Deep Agent, drawn as cards under it, each with its own role, tools and model. Their steps are nested in the trace, with tokens and cost per Helper. | Trace nesting and usage attribution; a Helper's approval pauses the parent run. | M |
| 4.4 | **Skills** | Reusable bundles of instructions, files and tools (Agent Skills format: `SKILL.md` and resources) in a workspace Skills library. An agent loads a skill when its description matches. | Loading and selection with the stand-in; skills ship inside exports. | M |
| 4.5 | **Multi-agent patterns** | Templates and canvas helpers for **supervisor** (route to specialist agents), **handoffs/swarm** (`Command(goto)` between agents, sharing the chat) and **plan-and-execute**. Each is built from ordinary steps, so it stays editable and exports to plain LangGraph. | One Test Set per pattern; exported code runs on its own. | M |
| 4.6 | **Describe-it copilot** | A side panel: describe a flow in words; the copilot proposes a reviewable diff on the canvas: steps, settings, Flow Data, and Test Set cases. Accept or reject per change. "Fix this" on any problem. Built on the flow spec, the checks and the catalog, so every proposal is valid before it's shown. | Proposals for ten written requests validate with no errors (stand-in scripts in CI; a real model by hand). Accept/undo keeps one undo step per proposal. | L |
| 4.7 | **Templates and report** | **Research assistant** (Deep Agent + Helpers) and **Inbox triage** (supervisor), 10-case Test Sets each; docs pages; Phase 4 report; handover update. | `tests/test_templates.py`; Playwright journeys for the Deep Agent canvas, Helpers trace, Skills library, copilot panel; axe scans. | M |

**Order:** 4.1 first, because agents can already call Code tools. Then 4.2 → 4.3 → 4.4 → 4.5,
then 4.6 (it needs every step type to exist), then 4.7.

**Risks:**
- `deepagents` moves quickly. Pin it exactly, and keep golden tests on the generated code.
- Docker-in-Docker for the Compose setup: the worker container needs the host's Docker socket,
  or a sidecar sandbox service. Prefer the sidecar.
- The copilot's quality depends on the model. Scope it to what the checks can verify.

## Phase 5: Platform

**Proposed "Done when"**:

1. A flow moves from **draft → staging → production** only when its Test Run passes in CI.
2. A published flow serves an API endpoint and a chat widget with per-key rate limits.
3. Roles stop a Viewer from editing and an Editor from publishing to production.
4. Every change and publish is in the audit log.

| # | Milestone | What it delivers | Size |
|---|---|---|---|
| 5.1 | **Test Sets editor and Checks** | Edit cases in the app (inputs, scripted answers, expected values).<br>**Checks:** contains, equals, JSON schema, tool calls, route taken, cost and latency budgets, **LLM-as-judge** with a rubric, and **trajectory** checks (the order of tool calls). Test Sets live next to flows in git. | L |
| 5.2 | **Test Runs and baselines** | Run a Test Set against a flow version and keep the results. Compare with a baseline (per case: better, same or worse; cost and latency deltas). Repeat runs to measure flakiness with real models. | M |
| 5.3 | **CI gate and `easychain eval`** | `easychain eval` exits non-zero on a regression against the baseline. A GitHub Action and a guide for other CI systems. Results posted as a summary. | M |
| 5.4 | **Environments and Publish** | Environments (dev, staging, prod) with their own secrets, models and Knowledge Bases.<br>**Publish** pins a flow version to an environment and gives it a stable API endpoint, a hosted chat page and an embeddable widget, with API keys and rate limits.<br>**`easychain deploy`** builds a Docker image or Compose bundle for self-hosting. | L |
| 5.5 | **Model gateway** | Per-environment budgets and alerts, response caching, provider fallbacks and retries, rate-limit smoothing, cost tracking per flow, run and user. | M |
| 5.6 | **Users, roles, SSO, audit** | Login (local accounts), roles (Owner, Editor, Runner, Viewer) per workspace, OIDC SSO (tested against a local test identity provider), per-workspace secret vaults, and an append-only **audit log** of edits, publishes, secret changes and approvals. Egress allow-list for server-side fetches. | L |
| 5.7 | **Dashboards and tracing** | Runs, errors, latency, tokens and cost over time, per flow and environment. **OpenTelemetry** export (a span per step and per model/tool call) and **LangSmith** tracing (with your key). | M |
| 5.8 | **Platform debt** | **Alembic** migrations (the first schema change lands here). Knowledge Base ingestion moves onto the job queue. Optional Postgres `LISTEN/NOTIFY` for events. | M |

**Order:**
1. 5.8 (migrations are needed before the other schema changes).
2. 5.6 (users exist before environments and publish).
3. 5.1 → 5.2 → 5.3.
4. 5.4 → 5.5.
5. 5.7.

**Risks:** this phase turns a single-user tool into a multi-user server, so security review is
part of each milestone, not left to the end. SSO with real identity providers (Okta, Google,
Entra) needs your accounts for a final check.

## Phase 6: Ecosystem

**Proposed "Done when"**:

1. The exported **LangGraph.js** project passes the same Test Set as the Python export for
   every template.
2. A custom step published to the registry installs into another workspace and runs.
3. Two people edit the same flow at once without losing changes.
4. The Helm chart deploys to a local Kubernetes (kind) cluster and passes the smoke test.

| # | Milestone | What it delivers | Size |
|---|---|---|---|
| 6.1 | **LangGraph.js export** | A second code generator emitting TypeScript for `@langchain/langgraph`. It shares the analysis and checks. Steps that have no JS equivalent are flagged with a check before export (e.g. Python Code steps). | L |
| 6.2 | **Custom step registry** | Package a step type (handler, form, code template, icon, docs, tests) as a Python package with an entry point. Install it from a registry or from git. Versioned, with a compatibility check against the spec version. | L |
| 6.3 | **Import** | Import flows from LangGraph code (common shapes), and from other builders' exports (e.g. Langflow and Flowise JSON) where they map cleanly. Unsupported parts become notes on the canvas. | M |
| 6.4 | **Real-time collaboration** | Several people edit one flow at once: Yjs over WebSocket, presence and cursors, comments on steps, conflict-free merges with the undo history. | L |
| 6.5 | **Prompt optimisation** | Use a Test Set to suggest better Instructions (few-shot examples, wording). Each suggestion is shown as a diff with its Test Run score; never applied silently. | M |
| 6.6 | **Helm chart** | API, workers, Postgres (or external), ingress, secrets, horizontal worker scaling. A smoke test on kind in CI. | M |

## Carried-over gaps

These are scheduled inside the phases above, or kept here until they are.

| Gap | Where it's handled |
|---|---|
| Code steps unsandboxed | 4.1 |
| No login, roles or audit | 5.6 |
| Schema migrations (`create_all` only) | 5.8 |
| Knowledge Base ingestion runs in the API process | 5.8 |
| Event streaming polls the database | 5.8 (optional) |
| No OCR for scanned PDFs | backlog: optional OCR backend (Tesseract or a provider) |
| One vector store (Easy Chain's tables) | backlog: Qdrant / Chroma / Pinecone adapters if needed |
| MCP: tools only, no OAuth, reconnect per run | backlog: MCP prompts and resources, OAuth, connection pooling |
| OpenAPI import: optional query params not sent | backlog: optional params as optional tool arguments |
| Server-side fetches have no egress allow-list | 5.6 |
| `docker compose up` not run end to end by the builder | Step 0 (CI) |
| Real models never called in CI | Step 0 (manual check per release); 5.2 (repeat runs with real models) |

## How each phase is run

These rules come from the build prompt, and [AGENTS.md](../AGENTS.md) has the details.

1. Work one phase at a time. Don't start a phase until the previous one meets its
   "Done when".
2. Before each commit, run `make lint` and the relevant tests. Never commit failing tests.
   Never call paid APIs in tests.
3. Keep [handover/status.md](handover/status.md) and [handover/state.yaml](handover/state.yaml)
   current, in the same commit as the work.
4. Each phase ends with `docs/phases/phase-N.md`, containing:
   - what was done, with the "Done when" evidence;
   - a demo script;
   - known gaps and risks;
   - the next phase's plan;
   - questions for the owner.
5. Then stop for review. The owner's answers go in [handover/decisions.md](handover/decisions.md).
