# @easychain/web (the editor)

The Easy Chain web app: the home page with templates, the canvas editor, the run panel, the
Inbox and the Knowledge page. It is a client of the API in [`python/`](../../python); it has no
step forms or step rules of its own, because those come from the API's step catalog. For the
project as a whole, see the [project README](../../README.md).

React 19, TypeScript, Vite, React Flow (xyflow) 12, Tailwind 4, Radix primitives in the
shadcn/ui style, Zustand (with zundo for undo/redo), Monaco (bundled locally, loaded lazily),
and dagre for auto-layout.

## Run it

```bash
pnpm install                          # from the repo root
pnpm dev                              # API on :8000 + this app on http://localhost:5173
# or just the app, against an API somewhere else:
EASYCHAIN_API=http://127.0.0.1:8000 pnpm --filter @easychain/web dev
pnpm --filter @easychain/web build    # production build into dist/ (served by `easychain dev`)
```

In development, Vite proxies `/api` to `EASYCHAIN_API` (default `http://127.0.0.1:8000`).

## Pages

| Route | Page |
|---|---|
| `#/` | Home: templates (**Try it** / **Use template**), your flows, links to the Inbox and Knowledge |
| `#/flows/{id}` | The editor: Step library, canvas, inspector, run panel |
| `#/inbox`, `#/inbox/{id}` | Everything waiting for a person: approvals, answers, tool approvals (works on phones) |
| `#/knowledge`, `#/knowledge/{id}` | Knowledge Bases: documents, chunk preview, test search |

## How the code is organised

```
src/
  App.tsx, main.tsx     routing (hash routes) and start-up
  components/
    Home.tsx            templates and flows
    Editor.tsx          the editor layout: Step library | canvas | inspector and run panel
    TopBar.tsx          flow name, save state, undo/redo, problems, Pro mode, theme, export, run
    StepLibrary.tsx     searchable list of step types (from the catalog)
    Inbox.tsx           the Inbox page
    Knowledge.tsx       the Knowledge page
    CodeView.tsx, MonacoEditor.tsx   code views and editors (Monaco, lazy)
    ui.tsx              the small UI kit (buttons, inputs, dialogs, tabs, tooltips…)
    canvas/             React Flow canvas: step and note nodes, edges (incl. dashed tool edges), icons
    inspector/          the step form (one control per form field kind), Agent tools and add-ons,
                        the reply format builder, Knowledge/SQL/MCP pickers, flow settings and Flow Data
    run/                run panel: form or chat, live trace, tool calls, citations, answers to
                        Ask a Human and tool approvals, Save Points and time travel
    dialogs/            Settings (keys and providers, MCP servers, notifications), export,
                        triggers, Import an API
  state/                Zustand stores (below)
  lib/
    spec.ts             pure flow-editing helpers (spec → new spec): add, connect (with a reason
                        when refused), tools, rename, copy/paste, layout…  Unit tested.
    types.ts            TypeScript mirror of the Python flow spec and API types
    api.ts              the API client (REST, SSE parsing)
    fixes.ts            one-click fixes for problems and run errors
  index.css             design tokens (light and dark themes, WCAG AA contrast)
e2e/                    Playwright journeys against the real app and a fake model server
```

### State

| Store | Holds |
|---|---|
| `flow` | The open flow's spec, save state (autosave) and undo history. Typing in one field merges into one undo step. |
| `check` | Problems, analysis and compiled code, refreshed in the background after each edit. |
| `run` | Per-step run state built from events: status, tokens, tool calls, progress, Sub-flow detail, waiting requests, Save Points; and the chat. |
| `ui` | Selection, Beginner/Pro mode, theme, open dialogs, breakpoints (kept per flow in the browser). |
| `catalog` | Step types and their forms, providers and models, embedding models, templates. |

Canvas nodes subscribe only to their own step, run state and problems, so a streaming token
re-renders one node. That keeps a 300-step flow responsive.

## Tests

```bash
pnpm --filter @easychain/web typecheck
pnpm --filter @easychain/web test         # vitest: editing helpers, undo, SSE parsing, citations
pnpm --filter @easychain/web build && pnpm --filter @easychain/web e2e   # Playwright
SCREENSHOTS=1 pnpm --filter @easychain/web exec playwright test e2e/zz-screenshots.spec.ts  # refresh docs images
```

The Playwright config starts the fake OpenAI-compatible server, the test MCP server and
`easychain dev` with a temporary data folder, so the journeys run without keys. They cover:

- building, running, debugging and exporting a flow;
- templates and chat;
- Ask a Human and the Inbox;
- For Each, breakpoints and Save Points;
- triggers;
- agent tools and tool approval;
- Knowledge Bases and citations;
- MCP and API import;
- axe WCAG 2.2 AA scans;
- a 300-step canvas.

Locators must be exact (`{ exact: true }`), because several labels repeat. Tool-capable
steps have two source handles, so scope handle selectors with `:not(.tool-handle)` (see
`e2e/helpers.ts`).
