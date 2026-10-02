import * as Popover from "@radix-ui/react-popover";
import { AlertTriangle, CheckCircle2, CircleAlert, Cloud, CloudOff, Code2, FileJson, Inbox, KeyRound, LayoutGrid, Loader2, Moon, Play, Redo2, Sun, Undo2, Zap } from "lucide-react";
import { useStore } from "zustand";
import { autoLayout } from "../lib/spec";
import { cn, modKey } from "../lib/utils";
import { useCheck } from "../state/check";
import { redo, undo, useFlow } from "../state/flow";
import { useRun } from "../state/run";
import { useUi } from "../state/ui";
import { useInboxCount } from "./Inbox";
import { KnowledgeLink } from "./Knowledge";
import { IssueList } from "./inspector/Inspector";
import { Button, Tooltip } from "./ui";

function SaveIndicator() {
  const state = useFlow((s) => s.saveState);
  const error = useFlow((s) => s.saveError);
  const label = { saved: "Saved", saving: "Saving…", unsaved: "Unsaved changes", error: `Not saved: ${error ?? ""}` }[state];
  const Icon = state === "error" ? CloudOff : state === "saving" ? Loader2 : Cloud;
  return (
    <span className={cn("flex items-center gap-1 text-xs", state === "error" ? "text-danger" : "text-faint")} aria-live="polite" data-testid="save-state">
      <Icon size={13} className={cn(state === "saving" && "animate-spin")} />
      {label}
    </span>
  );
}

function ProblemsButton() {
  const issues = useCheck((s) => s.issues);
  const errors = issues.filter((i) => i.level === "error").length;
  const warnings = issues.length - errors;
  return (
    <Popover.Root>
      <Popover.Trigger asChild>
        <Button variant="ghost" size="sm" aria-label="Problems" data-testid="problems-button">
          {errors ? (
            <CircleAlert size={14} className="text-danger" />
          ) : warnings ? (
            <AlertTriangle size={14} className="text-warn" />
          ) : (
            <CheckCircle2 size={14} className="text-ok" />
          )}
          <span className="hidden lg:inline">{errors || warnings ? `${errors} errors, ${warnings} warnings` : "No problems"}</span>
        </Button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content align="end" sideOffset={6} className="z-50 max-h-[70vh] w-96 overflow-y-auto rounded-xl border border-border bg-surface p-3 shadow-xl">
          <p className="mb-2 text-sm font-semibold">Checks before a run</p>
          {issues.length ? <IssueList issues={issues} /> : <p className="text-sm text-muted">Everything looks good.</p>}
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

export function InboxLink() {
  const count = useInboxCount();
  return (
    <Tooltip content="Runs waiting for your answer">
      <a href="#/inbox" className="relative flex h-8 items-center gap-1 rounded-md px-2 text-sm text-muted hover:bg-surface-2 hover:text-text" data-testid="inbox-link">
        <Inbox size={15} />
        <span className="hidden md:inline">Inbox</span>
        {count > 0 && (
          <span className="rounded-full bg-warn px-1.5 text-[10px] leading-4 font-semibold text-white" data-testid="inbox-count">
            {count}
          </span>
        )}
      </a>
    </Tooltip>
  );
}

export function TopBar({ onHome }: { onHome: () => void }) {
  const name = useFlow((s) => s.spec?.name);
  const apply = useFlow((s) => s.apply);
  const canUndo = useStore(useFlow.temporal, (s) => s.pastStates.length > 0);
  const canRedo = useStore(useFlow.temporal, (s) => s.futureStates.length > 0);
  const { mode, setMode, theme, toggleTheme, openSettings, setExportOpen, setRightTab, select, setTriggersOpen, setImportOpen } = useUi();
  const running = useRun((s) => s.status === "running" || s.status === "queued");
  const mod = modKey();
  return (
    <header className="flex h-12 shrink-0 items-center gap-2 border-b border-border bg-surface px-3">
      <button type="button" onClick={onHome} className="flex items-center gap-2 rounded-md px-1 py-1 hover:bg-surface-2" aria-label="All flows">
        <img src="/favicon.svg" alt="" className="h-6 w-6" />
        <span className="hidden text-sm font-semibold sm:inline">Easy Chain</span>
      </button>
      <span className="text-faint">/</span>
      <button type="button" className="max-w-[28ch] truncate rounded px-1 text-sm font-medium hover:bg-surface-2" onClick={() => select([])} title="Flow settings">
        {name}
      </button>
      <SaveIndicator />
      <div className="ml-2 flex items-center">
        <Tooltip content={`Undo (${mod}+Z)`}>
          <Button variant="ghost" size="icon-sm" aria-label="Undo" disabled={!canUndo} onClick={undo}>
            <Undo2 size={15} />
          </Button>
        </Tooltip>
        <Tooltip content={`Redo (${mod}+Shift+Z)`}>
          <Button variant="ghost" size="icon-sm" aria-label="Redo" disabled={!canRedo} onClick={redo}>
            <Redo2 size={15} />
          </Button>
        </Tooltip>
        <Tooltip content="Tidy up the layout">
          <Button variant="ghost" size="icon-sm" aria-label="Auto-layout" onClick={() => apply(autoLayout)}>
            <LayoutGrid size={15} />
          </Button>
        </Tooltip>
      </div>
      <div className="ml-auto flex items-center gap-1.5">
        <ProblemsButton />
        <KnowledgeLink />
        <InboxLink />
        <Tooltip content="Import an API (OpenAPI) as steps or agent tools">
          <Button variant="ghost" size="icon-sm" aria-label="Import an API" onClick={() => setImportOpen(true)}>
            <FileJson size={15} />
          </Button>
        </Tooltip>
        <Tooltip content="Start this flow from a webhook, a schedule, an upload or another flow">
          <Button variant="ghost" size="icon-sm" aria-label="Triggers" onClick={() => setTriggersOpen(true)}>
            <Zap size={15} />
          </Button>
        </Tooltip>
        <div className="flex items-center rounded-md bg-surface-2 p-0.5 text-xs" role="group" aria-label="Mode">
          {(["beginner", "pro"] as const).map((m) => (
            <button
              key={m}
              type="button"
              aria-pressed={mode === m}
              onClick={() => setMode(m)}
              className={cn("rounded px-2 py-1 font-medium capitalize", mode === m ? "bg-surface text-text shadow-sm" : "text-muted")}
            >
              {m}
            </button>
          ))}
        </div>
        <Tooltip content={theme === "dark" ? "Light theme" : "Dark theme"}>
          <Button variant="ghost" size="icon-sm" aria-label="Toggle theme" onClick={toggleTheme}>
            {theme === "dark" ? <Sun size={15} /> : <Moon size={15} />}
          </Button>
        </Tooltip>
        <Tooltip content="Settings: keys, MCP servers, notifications">
          <Button variant="ghost" size="icon-sm" aria-label="Settings" onClick={() => openSettings()}>
            <KeyRound size={15} />
          </Button>
        </Tooltip>
        <Button variant="outline" size="sm" onClick={() => setExportOpen(true)} data-testid="export-button">
          <Code2 size={14} /> Export
        </Button>
        <Tooltip content={`Run (${mod}+Enter)`}>
          <Button variant="primary" size="sm" onClick={() => setRightTab("run")} data-testid="open-run">
            {running ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />} Run
          </Button>
        </Tooltip>
      </div>
    </header>
  );
}
