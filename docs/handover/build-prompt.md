# The build prompt, summarised

The project is built from a written brief, *Easy Chain: Build Prompt*, which the owner attached
to the first conversation. The original file is not in the repo. This summary is reconstructed
from the phase reports, ARCHITECTURE.md and the README. It covers what later work depends on. If
you have the original, commit it next to this file as `build-prompt.original.md`. Where the two
disagree, the original wins.

## The product

- An open-source, drag-and-drop builder for LLM apps and AI agents, for **low-code builders**
  (analysts, operations and product people, consultants). It is a visual layer on the real
  LangChain/LangGraph stack, not a replacement for it.
- Flows compile to **plain LangGraph code**. That code runs when you press Run, is what you get
  when you export, and needs no Easy Chain runtime.
- **Plain language** everywhere: Flow, Step, Connection, Decision, Flow Data, AI Model,
  Instructions, Action and so on. A **Pro mode** shows the technical names and the code.
- A new user should get a working flow in **under 5 minutes**.
- **Prefer well-known, boring libraries.** Python 3.12+, FastAPI, SQLAlchemy, React, React Flow,
  shadcn/ui style. Postgres for storage, with a Redis or Postgres queue (section 11 of the prompt
  lists both).
- Every error says what happened and what to do next.
- **Section 14 lists templates**, each shipped with a 10-case Test Set. They arrive with the phase
  that provides their features (Support bot over docs and SQL analyst in Phase 3, the approval
  workflow in Phase 2, and so on).

## Rules for the build

- Show ARCHITECTURE.md, the schema and the plan before writing feature code.
- Work **one phase at a time**. Don't start a phase until the previous one meets its **"Done
  when"**.
- Each phase ends with a **report**: what was done, a demo script, known gaps, and the plan for
  the next phase. Then **stop for review**.

## Phases

| Phase | Scope | Done when |
|---|---|---|
| 0. Foundations | Monorepo, CI, Docker Compose, flow spec, compiler, CLI | (see [phase-0-1.md](../phases/phase-0-1.md)) |
| 1. Visual MVP | Canvas, step library, inspector; Input, AI Model, Instructions, Action, Decision and Output steps; three providers; streaming chat; run trace; Python export | (see [phase-0-1.md](../phases/phase-0-1.md)) |
| 2. Real runtime | Flow Data panel and update rules, loops with guards, parallel branches, For Each, Sub-flows, Postgres Save Points, crash recovery, Ask a Human and Inbox, time travel, background runs, triggers | Killing a worker mid-run and restarting resumes from the last step with no duplicate side effects, and a paused approval can be resumed a day later. **Met.** |
| 3. Agents and knowledge | Agent step and add-ons, MCP, OpenAPI import, structured output, Knowledge Base, memory, all providers | The **Support bot over docs** and **SQL analyst** templates pass their Test Sets. **Met.** |
| 4. Autopilot and teams | Deep Agents, Helpers, Skills, **sandboxes** (Code steps), multi-agent patterns, describe-it copilot | (from the original; a proposal is in [phase-3.md](../phases/phase-3.md#phase-4-plan-autopilot-and-teams), waiting for the owner) |
| 5. Platform | Test Sets and Checks, Test Runs, CI gate, dashboards, model gateway, Publish, environments, roles, SSO, audit log, OTel/LangSmith, CLI `eval` and `deploy` | (from the original) |
| 6. Ecosystem | LangGraph.js export, custom module registry, import, real-time collaboration, prompt optimisation, Helm | (from the original) |

The detailed Phase 3 plan, derived from the prompt, is in
[phase-2.md](../phases/phase-2.md#phase-3-plan-agents-and-knowledge); the Phase 4 plan is in
[phase-3.md](../phases/phase-3.md#phase-4-plan-autopilot-and-teams).
