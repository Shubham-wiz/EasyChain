// One-click fixes offered next to problems and run errors.

import { stepInfo } from "../state/catalog";
import { useCheck } from "../state/check";
import { useFlow } from "../state/flow";
import { useUi } from "../state/ui";
import { addStep, createStep, getStep, insertBefore, nextFreePosition, renameStepId, renameVariable, updateSettings } from "./spec";
import type { Fix } from "./types";

export function applyFix(fix: Fix, stepId?: string, rerun?: () => void) {
  const { apply } = useFlow.getState();
  const ui = useUi.getState();
  const p = fix.params as Record<string, unknown>;
  switch (fix.kind) {
    case "add_key":
      ui.openSettings({ provider: String(p.provider ?? "") });
      return;
    case "add_secret":
      ui.openSettings({ secret: String(p.name ?? "") });
      return;
    case "use_stand_in":
      ui.setStandIn(true);
      rerun?.();
      return;
    case "retry":
      rerun?.();
      return;
    case "focus_setting":
      if (stepId) ui.focus(stepId, String(p.key));
      return;
    case "set_setting":
      if (stepId) apply((s) => updateSettings(s, stepId, { [String(p.key)]: p.value }));
      return;
    case "rename_variable":
      if (stepId) apply((s) => renameVariable(s, stepId, String(p.setting), String(p.from), String(p.to)));
      return;
    case "rename_step":
      if (stepId) {
        apply((s) => renameStepId(s, stepId, String(p.to)));
        ui.select([String(p.to)]);
      }
      return;
    case "add_step": {
      const info = stepInfo(String(p.type));
      const spec = useFlow.getState().spec;
      if (!info || !spec) return;
      const fields = useCheck.getState().analysis?.fields.map((f) => f.name) ?? [];
      const step = createStep(info, spec, fields);
      const before = p.before ? String(p.before) : null;
      if (before && getStep(spec, before)) apply((s) => insertBefore(s, before, step));
      else {
        const pos = info.type === "input" ? { x: -300, y: 120 } : nextFreePosition(spec);
        apply((s) => addStep(s, step, pos));
      }
      ui.select([step.id]);
      return;
    }
  }
}
