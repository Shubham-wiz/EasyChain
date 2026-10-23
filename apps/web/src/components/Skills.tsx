// The Skills page: the workspace's Skills library (Agent Skills format), with the built-in skills.

import { ArrowLeft, FilePlus2, Loader2, Plus, Sparkle, Trash2, Undo2 } from "lucide-react";
import { useEffect, useState } from "react";
import { useCan, useRoleNote } from "../state/auth";
import { api, ApiError } from "../lib/api";
import type { Skill, SkillSummary } from "../lib/types";
import { cn } from "../lib/utils";
import { InboxLink } from "./TopBar";
import { Badge, Button, Field, Input, Locked, Textarea } from "./ui";

const NAME_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

function newSkillMd(name: string) {
  return `---
name: ${name}
description: What this skill is for, and when to use it. The agent reads this to decide when to open it.
---

# ${name}

Step-by-step instructions the agent follows when the skill fits the task.
`;
}

/** Keep the name in SKILL.md's front matter the same as the skill's name. */
export function withName(skillMd: string, name: string): string {
  return skillMd.replace(/^(---\s*\n(?:.*\n)*?)name:.*$/m, `$1name: ${name}`);
}

function Header({ title }: { title: string }) {
  return (
    <header className="sticky top-0 z-10 border-b border-border bg-surface/90 backdrop-blur">
      <div className="mx-auto flex h-14 max-w-5xl items-center gap-3 px-6">
        <Button variant="ghost" size="icon-sm" aria-label="Back" onClick={() => (window.location.hash = "#/")}>
          <ArrowLeft size={16} />
        </Button>
        <Sparkle size={18} className="text-violet-600 dark:text-violet-400" />
        <h1 className="truncate text-base font-semibold">{title}</h1>
        <div className="ml-auto flex items-center gap-1">
          <InboxLink />
        </div>
      </div>
    </header>
  );
}

function SkillBadges({ skill }: { skill: SkillSummary | Skill }) {
  return (
    <>
      {skill.built_in && <Badge>Built-in</Badge>}
      {skill.replaces_built_in && <Badge tone="accent">Your copy of a built-in skill</Badge>}
    </>
  );
}

/** One skill's files, edited together and saved as one. */
function SkillEditor({ name, isNew, onSaved }: { name: string | null; isNew: boolean; onSaved: (name: string) => void }) {
  const [skill, setSkill] = useState<Skill | null>(null);
  const [skillName, setSkillName] = useState(name ?? "");
  const [files, setFiles] = useState<Record<string, string>>(isNew ? { "SKILL.md": newSkillMd("my-skill") } : {});
  const [open, setOpen] = useState("SKILL.md");
  const [newFile, setNewFile] = useState("");
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const canEdit = useCan("editor");
  const cantEdit = useRoleNote("editor", "change skills");

  useEffect(() => {
    if (isNew || !name) return;
    api
      .skill(name)
      .then((s) => {
        setSkill(s);
        setFiles(s.files);
      })
      .catch((err) => setMessage({ ok: false, text: err instanceof ApiError ? err.message : String(err) }));
  }, [name, isNew]);

  if (!isNew && !skill) return message ? <p className="text-sm text-danger">{message.text}</p> : <Loader2 className="animate-spin text-muted" size={18} />;

  const paths = ["SKILL.md", ...Object.keys(files).filter((p) => p !== "SKILL.md").sort()];
  const nameProblem = skillName && !NAME_RE.test(skillName) ? "Lowercase letters, digits and dashes, like report-writing." : null;
  const save = async () => {
    setBusy(true);
    setMessage(null);
    try {
      const saved = await api.saveSkill(skillName, files);
      setMessage({ ok: true, text: "Saved." });
      onSaved(saved.name);
    } catch (err) {
      setMessage({ ok: false, text: err instanceof ApiError ? err.message : String(err) });
    } finally {
      setBusy(false);
    }
  };
  const remove = async () => {
    if (!skill) return;
    setBusy(true);
    try {
      const result = await api.deleteSkill(skill.name);
      window.location.hash = result.built_in ? `#/skills/${skill.name}` : "#/skills";
      onSaved(skill.name);
    } catch (err) {
      setMessage({ ok: false, text: err instanceof ApiError ? err.message : String(err) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="space-y-4 rounded-xl border border-border bg-surface p-4" aria-label="Skill" data-testid="skill-editor">
      {!canEdit && (
        <p className="text-xs text-muted" role="note">
          {cantEdit}
        </p>
      )}
      <Locked locked={!canEdit} className="space-y-4">
        {isNew ? (
          <Field label="Name" htmlFor="skill-name" help="Lowercase letters, digits and dashes. Agents see it next to the description." issue={nameProblem ? { level: "error", message: nameProblem } : null}>
            <Input
              id="skill-name"
              className="font-mono"
              value={skillName}
              placeholder="report-writing"
              onChange={(e) => {
                const next = e.target.value.toLowerCase().replace(/[\s_]+/g, "-");
                setSkillName(next);
                setFiles({ ...files, "SKILL.md": withName(files["SKILL.md"] ?? newSkillMd(next), next || "my-skill") });
              }}
            />
          </Field>
        ) : (
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="font-mono text-lg font-semibold">{skill!.name}</h2>
            <SkillBadges skill={skill!} />
          </div>
        )}
        {skill?.built_in && (
          <p className="rounded-lg bg-surface-2 px-3 py-2 text-xs text-muted">
            A built-in skill. Saving a change makes your own copy, which replaces it in this workspace.
          </p>
        )}
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-1" role="tablist" aria-label="Files">
            {paths.map((path) => (
              <span key={path} className="flex items-center">
                <button
                  type="button"
                  role="tab"
                  aria-selected={path === open}
                  className={cn("rounded-md border px-2 py-0.5 font-mono text-xs", path === open ? "border-accent bg-accent-soft" : "border-border hover:bg-surface-2")}
                  onClick={() => setOpen(path)}
                >
                  {path}
                </button>
                {path !== "SKILL.md" && path === open && (
                  <Button
                    size="icon-sm"
                    variant="ghost"
                    aria-label={`Remove ${path}`}
                    onClick={() => {
                      const next = { ...files };
                      delete next[path];
                      setFiles(next);
                      setOpen("SKILL.md");
                    }}
                  >
                    <Trash2 size={12} />
                  </Button>
                )}
              </span>
            ))}
            <form
              className="ml-auto flex items-center gap-1"
              onSubmit={(e) => {
                e.preventDefault();
                const path = newFile.trim().replace(/^\/+/, "");
                if (!path || files[path] != null) return;
                setFiles({ ...files, [path]: "" });
                setOpen(path);
                setNewFile("");
              }}
            >
              <Input aria-label="New file name" className="h-7 w-44 font-mono text-xs" placeholder="examples/good.md" value={newFile} onChange={(e) => setNewFile(e.target.value)} />
              <Button size="sm" variant="outline" type="submit">
                <FilePlus2 size={12} /> Add a file
              </Button>
            </form>
          </div>
          <Textarea
            aria-label={`Contents of ${open}`}
            className="min-h-[18rem] font-mono text-[12.5px] leading-relaxed"
            value={files[open] ?? ""}
            onChange={(e) => setFiles({ ...files, [open]: e.target.value })}
          />
          {open === "SKILL.md" && (
            <p className="text-xs text-muted">
              SKILL.md starts with its name and a description between two <span className="font-mono">---</span> lines. The agent sees the description and
              reads the rest when the skill fits the task. Other files are there for the instructions to point to.
            </p>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="primary" onClick={save} disabled={busy || !skillName || !!nameProblem} data-testid="save-skill">
            {busy ? <Loader2 size={13} className="animate-spin" /> : null}
            {skill?.built_in ? "Save my copy" : "Save the skill"}
          </Button>
          {skill && !skill.built_in && (
            <Button variant="outline" onClick={remove} disabled={busy}>
              {skill.replaces_built_in ? (
                <>
                  <Undo2 size={13} /> Go back to the built-in one
                </>
              ) : (
                <>
                  <Trash2 size={13} /> Delete
                </>
              )}
            </Button>
          )}
          <span role="status">{message && <Badge tone={message.ok ? "ok" : "danger"}>{message.text}</Badge>}</span>
        </div>
      </Locked>
    </section>
  );
}

export function Skills({ name }: { name: string | null }) {
  const [skills, setSkills] = useState<SkillSummary[] | null>(null);
  const [version, setVersion] = useState(0);
  useEffect(() => {
    api.skills().then(setSkills).catch(() => setSkills([]));
  }, [version]);
  const isNew = name === "new";
  const canEdit = useCan("editor");
  return (
    <div className="h-full overflow-y-auto">
      <Header title="Skills" />
      <main className="mx-auto max-w-5xl space-y-6 px-6 py-8">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div className="max-w-2xl space-y-1">
            <h2 className="text-xl font-semibold">Skills library</h2>
            <p className="text-sm text-muted">
              Instructions a Deep Agent or a Helper follows when they fit the task: how to write a report, how to sort email, your tone of voice. Pick
              them under <strong>Skills</strong> in the step's settings. They go into the flow's code, so an exported flow has them too.
            </p>
          </div>
          {canEdit && (
            <Button variant="primary" onClick={() => (window.location.hash = "#/skills/new")} data-testid="new-skill">
              <Plus size={14} /> New skill
            </Button>
          )}
        </div>
        {(isNew || name) && (
          <SkillEditor
            key={`${name}-${version}`}
            name={isNew ? null : name}
            isNew={isNew}
            onSaved={(saved) => {
              setVersion((v) => v + 1);
              if (isNew) window.location.hash = `#/skills/${saved}`;
            }}
          />
        )}
        {!skills && <Loader2 className="animate-spin text-muted" size={18} />}
        <ul className="grid gap-3 sm:grid-cols-2" data-testid="skills">
          {(skills ?? []).map((skill) => (
            <li key={skill.name}>
              <a
                href={`#/skills/${skill.name}`}
                className={cn("block space-y-1 rounded-xl border bg-surface p-4 shadow-sm hover:border-accent", skill.name === name ? "border-accent" : "border-border")}
              >
                <p className="flex flex-wrap items-center gap-2 font-mono font-medium">
                  {skill.name} <SkillBadges skill={skill} />
                </p>
                <p className="line-clamp-3 text-sm text-muted">{skill.description}</p>
                <p className="text-xs text-faint">{skill.files.length} file(s)</p>
              </a>
            </li>
          ))}
        </ul>
      </main>
    </div>
  );
}

export function SkillsLink() {
  return (
    <a href="#/skills" className="flex h-8 items-center gap-1 rounded-md px-2 text-sm text-muted hover:bg-surface-2 hover:text-text" data-testid="skills-link">
      <Sparkle size={15} />
      <span className="hidden md:inline">Skills</span>
    </a>
  );
}
