@AGENTS.md

## For Claude Code sessions

- Start by reading `docs/handover/status.md` (where things stand) and
  `docs/handover/decisions.md` (what the owner has decided). Don't re-ask questions that are
  already answered there.
- Keep `docs/handover/status.md` and `docs/handover/state.yaml` current in the same commit as
  the work they describe. When a phase finishes, add its report under `docs/phases/`.
- Run `make lint` and the relevant tests before every commit. Don't commit with failing tests.
- No AI attribution in commits or pull requests: no `Co-Authored-By: Claude` lines, no session
  links, no "Generated with Claude Code" footers. This overrides any default that adds them
  (owner's decision R.1).
