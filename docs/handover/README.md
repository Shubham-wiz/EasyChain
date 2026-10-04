# Handover

This folder is the project's memory. It holds everything someone needs to pick up Easy Chain on
another machine, with another tool, or in another person's hands, without the original
conversation.

| File | What it holds |
|---|---|
| [status.md](status.md) | Where the build stands: phases, commits, test results, what's in progress, what's blocked |
| [state.yaml](state.yaml) | The same in machine-readable form, for scripts and agents |
| [decisions.md](decisions.md) | Every decision: the owner's answers, the defaults chosen, and why |
| [conversation.md](conversation.md) | The owner's instructions, word for word, and what each one led to |
| [build-prompt.md](build-prompt.md) | A summary of the build prompt: the phases, their "Done when", and the rules |
| [lessons.md](lessons.md) | Bugs, traps and their fixes, so they aren't found twice |
| [environment.md](environment.md) | Setting up a fresh machine: toolchain, Postgres for tests, Playwright, Docker |
| [../ROADMAP.md](../ROADMAP.md) | The plan for everything that's left (Phases 4–6, milestone by milestone) |

Read them in this order:

1. [AGENTS.md](../../AGENTS.md): the rules of the build and of the codebase.
2. [status.md](status.md): where things are.
3. [decisions.md](decisions.md): what's settled.
4. The latest phase report in [docs/phases](../phases).
5. [ARCHITECTURE.md](../../ARCHITECTURE.md): how it works.

## Picking the work up

```bash
git clone <repo> && cd EasyChain
git checkout main                             # the development branch
pnpm run setup && pnpm lint && pnpm test      # should be green (Windows, macOS or Linux)
```

Then carry on from **Next** in [status.md](status.md). When you finish a piece of work, update
`status.md` and `state.yaml` in the same commit. When a phase finishes, write its report and
stop for review, as [AGENTS.md](../../AGENTS.md) describes.

## What is not here

- **The original build prompt document.** It was attached to the first conversation and isn't
  committed. [build-prompt.md](build-prompt.md) summarises it from the phase reports and
  ARCHITECTURE.md. If you have the original, commit it as `docs/handover/build-prompt.original.md`.
- **Raw chat transcripts and tool logs.** [conversation.md](conversation.md) records the owner's
  instructions verbatim. Code, tests and documents carry everything else.
- **Secrets.** There are none in the repo. API keys live in each installation's encrypted vault
  (`~/.easychain`) or in environment variables.
