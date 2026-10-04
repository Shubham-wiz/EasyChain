# Easy Chain developer commands. Needs: uv (Python), pnpm (Node 20+).
# Every target runs scripts/tasks.mjs, which also works on Windows: `pnpm <task>` there.
TASKS := help install dev web build server worker test test-python test-web test-client e2e lint format schema golden docker clean
.PHONY: $(TASKS)

$(TASKS):
	@node scripts/tasks.mjs $@
