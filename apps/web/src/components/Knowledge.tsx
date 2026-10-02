// The Knowledge page: Knowledge Bases, their documents, a chunk preview and a test search.

import { ArrowLeft, BookOpen, ChevronDown, ChevronRight, FileUp, Library, Link2, Loader2, Plus, Scissors, Search, Trash2, Type } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "../lib/api";
import type { ChunkPreview, KnowledgeBase, KnowledgeDocument, KnowledgeHit } from "../lib/types";
import { cn, timeAgo } from "../lib/utils";
import { useCatalog } from "../state/catalog";
import { passageText, SourceList } from "./run/Citations";
import { InboxLink } from "./TopBar";
import { Badge, Button, Field, Input, Select, Textarea } from "./ui";

function Header({ title, onBack, children }: { title: string; onBack: () => void; children?: React.ReactNode }) {
  return (
    <header className="sticky top-0 z-10 border-b border-border bg-surface/90 backdrop-blur">
      <div className="mx-auto flex h-14 max-w-5xl items-center gap-3 px-6">
        <Button variant="ghost" size="icon-sm" aria-label="Back" onClick={onBack}>
          <ArrowLeft size={16} />
        </Button>
        <Library size={18} className="text-teal-600 dark:text-teal-400" />
        <h1 className="truncate text-base font-semibold">{title}</h1>
        <div className="ml-auto flex items-center gap-1">
          {children}
          <InboxLink />
        </div>
      </div>
    </header>
  );
}

function errorText(err: unknown) {
  return err instanceof ApiError ? err.message : String(err);
}

// ── the list ─────────────────────────────────────────────────────────────────

function NewBase({ onCreated }: { onCreated: (kb: KnowledgeBase) => void }) {
  const models = useCatalog((s) => s.catalog?.embedding_models ?? []);
  const providers = useCatalog((s) => s.catalog?.providers ?? []);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [model, setModel] = useState("");
  const [more, setMore] = useState(false);
  const [size, setSize] = useState(1000);
  const [overlap, setOverlap] = useState(150);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const keyFor = (provider: string | null) => (provider ? providers.find((p) => p.id === provider) : undefined);
  return (
    <form
      className="space-y-3 rounded-xl border border-border bg-surface p-4"
      data-testid="new-knowledge-base"
      onSubmit={async (e) => {
        e.preventDefault();
        if (!name.trim()) return;
        setBusy(true);
        try {
          const kb = await api.createKnowledgeBase({
            name: name.trim(),
            description,
            embedding_model: model || undefined,
            chunk_size: size,
            chunk_overlap: overlap,
          });
          onCreated(kb);
        } catch (err) {
          setError(errorText(err));
          setBusy(false);
        }
      }}
    >
      <p className="text-sm font-semibold">New Knowledge Base</p>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Name" htmlFor="kb-name">
          <Input id="kb-name" placeholder="Product docs" value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field
          label="Embedding model"
          htmlFor="kb-model"
          help="Turns text into numbers that capture its meaning. Pick one now: changing it later means rebuilding the Knowledge Base."
          technical="embeddings"
        >
          <Select id="kb-model" value={model} onChange={(e) => setModel(e.target.value)}>
            <option value="">The best one available here</option>
            {models.map((m) => {
              const provider = keyFor(m.provider);
              const missing = provider && provider.key_env && !provider.key_set;
              return (
                <option key={m.id} value={m.id}>
                  {m.label}
                  {missing ? " (needs a key)" : ""}
                </option>
              );
            })}
          </Select>
        </Field>
      </div>
      <Field label="What's in it" htmlFor="kb-description">
        <Input id="kb-description" placeholder="Our help-centre articles and policies" value={description} onChange={(e) => setDescription(e.target.value)} />
      </Field>
      <button type="button" className="flex items-center gap-1 text-xs font-medium text-muted hover:text-text" aria-expanded={more} onClick={() => setMore(!more)}>
        {more ? <ChevronDown size={14} /> : <ChevronRight size={14} />} How to split documents
      </button>
      {more && (
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Chunk size (characters)" htmlFor="kb-size" help="Passages of about this length are searched and given to the AI." technical="chunk_size">
            <Input id="kb-size" type="number" min={100} max={8000} value={size} onChange={(e) => setSize(Number(e.target.value))} />
          </Field>
          <Field label="Overlap (characters)" htmlFor="kb-overlap" help="Neighbouring passages share this much text, so nothing is cut in half." technical="chunk_overlap">
            <Input id="kb-overlap" type="number" min={0} max={2000} value={overlap} onChange={(e) => setOverlap(Number(e.target.value))} />
          </Field>
        </div>
      )}
      {error && <p className="text-xs text-danger">{error}</p>}
      <Button type="submit" variant="primary" disabled={!name.trim() || busy}>
        {busy ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />} Create
      </Button>
    </form>
  );
}

function KnowledgeList() {
  const [bases, setBases] = useState<KnowledgeBase[] | null>(null);
  const [creating, setCreating] = useState(false);
  useEffect(() => {
    api.knowledgeBases().then(setBases).catch(() => setBases([]));
  }, []);
  return (
    <div className="h-full overflow-y-auto">
      <Header title="Knowledge" onBack={() => (window.location.hash = "#/")} />
      <main className="mx-auto max-w-5xl space-y-6 px-6 py-8">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div className="max-w-2xl space-y-1">
            <h2 className="text-xl font-semibold">Knowledge Bases</h2>
            <p className="text-sm text-muted">
              Add your documents once; a Knowledge Base search step (or an agent) finds the passages that answer each question, so the AI answers
              from your facts and cites them.
            </p>
          </div>
          <Button variant="primary" onClick={() => setCreating(!creating)} data-testid="new-kb-button">
            <Plus size={14} /> New Knowledge Base
          </Button>
        </div>
        {creating && <NewBase onCreated={(kb) => (window.location.hash = `#/knowledge/${kb.id}`)} />}
        {!bases && <Loader2 className="animate-spin text-muted" size={18} />}
        {bases && !bases.length && !creating && (
          <div className="rounded-xl border border-dashed border-border p-10 text-center text-sm text-muted">No Knowledge Bases yet.</div>
        )}
        <ul className="grid gap-3 sm:grid-cols-2" data-testid="knowledge-bases">
          {(bases ?? []).map((kb) => (
            <li key={kb.id}>
              <a href={`#/knowledge/${kb.id}`} className="block space-y-1 rounded-xl border border-border bg-surface p-4 shadow-sm hover:border-accent">
                <p className="flex items-center gap-2 font-medium">
                  <BookOpen size={15} className="text-teal-600 dark:text-teal-400" /> {kb.name}
                </p>
                {kb.description && <p className="line-clamp-2 text-sm text-muted">{kb.description}</p>}
                <p className="text-xs text-faint">
                  {typeof kb.documents === "number" ? kb.documents : 0} documents · {kb.chunks ?? 0} passages · {kb.embedding_label ?? kb.embedding_model}
                </p>
              </a>
            </li>
          ))}
        </ul>
      </main>
    </div>
  );
}

// ── one Knowledge Base ───────────────────────────────────────────────────────

const STATUS_TONE: Record<KnowledgeDocument["status"], "neutral" | "accent" | "ok" | "danger"> = {
  queued: "neutral",
  processing: "accent",
  ready: "ok",
  error: "danger",
};

function AddDocuments({ kb, onAdded }: { kb: KnowledgeBase; onAdded: () => void }) {
  const [tab, setTab] = useState<"files" | "urls" | "text">("files");
  const [urls, setUrls] = useState("");
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const upload = async (files: File[]) => {
    if (!files.length) return;
    try {
      await api.addKnowledgeFiles(kb.id, files);
      setError(null);
      onAdded();
    } catch (err) {
      setError(errorText(err));
    }
  };
  return (
    <section className="space-y-3 rounded-xl border border-border bg-surface p-4" aria-label="Add documents">
      <div className="flex items-center gap-1" role="tablist" aria-label="What to add">
        {(
          [
            ["files", FileUp, "Files"],
            ["urls", Link2, "Web pages"],
            ["text", Type, "Text"],
          ] as const
        ).map(([key, Icon, label]) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={tab === key}
            className={cn("flex items-center gap-1.5 rounded-md px-2.5 py-1 text-sm", tab === key ? "bg-surface-2 font-medium" : "text-muted hover:bg-surface-2")}
            onClick={() => setTab(key)}
          >
            <Icon size={14} /> {label}
          </button>
        ))}
      </div>
      {tab === "files" && (
        <div
          className={cn(
            "flex flex-col items-center gap-2 rounded-lg border-2 border-dashed px-4 py-8 text-center text-sm",
            dragging ? "border-accent bg-accent-soft/40" : "border-border",
          )}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            void upload(Array.from(e.dataTransfer.files));
          }}
        >
          <FileUp size={22} className="text-faint" />
          <p className="text-muted">Drop PDF, Word, HTML, Markdown, CSV or text files here</p>
          <input
            ref={input}
            type="file"
            multiple
            className="hidden"
            accept=".pdf,.docx,.html,.htm,.md,.markdown,.csv,.txt,.json,.yaml,.yml"
            aria-label="Choose files"
            data-testid="kb-file-input"
            onChange={(e) => {
              void upload(Array.from(e.target.files ?? []));
              e.target.value = "";
            }}
          />
          <Button variant="outline" size="sm" onClick={() => input.current?.click()}>
            Choose files
          </Button>
        </div>
      )}
      {tab === "urls" && (
        <form
          className="space-y-2"
          onSubmit={async (e) => {
            e.preventDefault();
            const list = urls.split(/\s+/).map((u) => u.trim()).filter(Boolean);
            if (!list.length) return;
            try {
              await api.addKnowledgeUrls(kb.id, list);
              setUrls("");
              setError(null);
              onAdded();
            } catch (err) {
              setError(errorText(err));
            }
          }}
        >
          <Textarea aria-label="Web addresses" rows={3} placeholder={"https://example.com/help/returns\nhttps://example.com/help/shipping"} value={urls} onChange={(e) => setUrls(e.target.value)} />
          <Button type="submit" variant="primary" size="sm" disabled={!urls.trim()}>
            Add pages
          </Button>
        </form>
      )}
      {tab === "text" && (
        <form
          className="space-y-2"
          onSubmit={async (e) => {
            e.preventDefault();
            try {
              await api.addKnowledgeText(kb.id, title.trim() || "Note", text);
              setTitle("");
              setText("");
              setError(null);
              onAdded();
            } catch (err) {
              setError(errorText(err));
            }
          }}
        >
          <Input aria-label="Title" placeholder="Title" value={title} onChange={(e) => setTitle(e.target.value)} />
          <Textarea aria-label="Text" rows={5} placeholder="Paste the text" value={text} onChange={(e) => setText(e.target.value)} />
          <Button type="submit" variant="primary" size="sm" disabled={!text.trim()}>
            Add text
          </Button>
        </form>
      )}
      {error && <p className="text-xs text-danger">{error}</p>}
    </section>
  );
}

function ChunkPreviewPanel({ kb }: { kb: KnowledgeBase }) {
  const [open, setOpen] = useState(false);
  const [size, setSize] = useState(kb.chunk_size);
  const [overlap, setOverlap] = useState(kb.chunk_overlap);
  const [file, setFile] = useState<File | null>(null);
  const [url, setUrl] = useState("");
  const [preview, setPreview] = useState<ChunkPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const run = async () => {
    setBusy(true);
    try {
      setPreview(await api.previewChunks(file ? { file } : { url }, size, overlap));
      setError(null);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="rounded-xl border border-border bg-surface" aria-label="Chunk preview">
      <button type="button" className="flex w-full items-center gap-2 px-4 py-3 text-left text-sm font-medium" aria-expanded={open} onClick={() => setOpen(!open)}>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <Scissors size={14} /> Preview how a document is split
      </button>
      {open && (
        <div className="space-y-3 border-t border-border p-4">
          <div className="grid gap-2 sm:grid-cols-2">
            <Input type="file" aria-label="File to preview" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
            <Input aria-label="Or a web address" placeholder="or https://…" value={url} onChange={(e) => setUrl(e.target.value)} disabled={!!file} />
          </div>
          <div className="flex flex-wrap items-center gap-3 text-xs text-muted">
            <label className="flex items-center gap-2">
              Size
              <input type="range" min={200} max={4000} step={50} value={size} onChange={(e) => setSize(Number(e.target.value))} aria-label="Chunk size" />
              <span className="w-12 font-mono">{size}</span>
            </label>
            <label className="flex items-center gap-2">
              Overlap
              <input type="range" min={0} max={800} step={10} value={overlap} onChange={(e) => setOverlap(Number(e.target.value))} aria-label="Chunk overlap" />
              <span className="w-10 font-mono">{overlap}</span>
            </label>
            <Button size="sm" variant="outline" disabled={busy || (!file && !url.trim())} onClick={run}>
              {busy ? <Loader2 size={13} className="animate-spin" /> : <Scissors size={13} />} Preview
            </Button>
          </div>
          {error && <p className="text-xs text-danger">{error}</p>}
          {preview && (
            <div className="space-y-2" data-testid="chunk-preview">
              <p className="text-xs text-muted">
                “{preview.title}” splits into <strong>{preview.count}</strong> passages ({preview.chars.toLocaleString()} characters).
                {size !== kb.chunk_size || overlap !== kb.chunk_overlap ? " This Knowledge Base itself splits with its own settings." : ""}
              </p>
              <ol className="max-h-80 space-y-1.5 overflow-y-auto">
                {preview.chunks.map((c) => (
                  <li key={c.position} className="rounded-md border border-border px-2.5 py-1.5 text-xs">
                    <p className="mb-0.5 text-[10.5px] text-faint">
                      #{c.position + 1} · {c.text.length} characters{c.page ? ` · page ${c.page}` : ""}
                      {c.heading ? ` · ${c.heading}` : ""}
                    </p>
                    <p className="line-clamp-4 whitespace-pre-wrap">{c.text}</p>
                  </li>
                ))}
              </ol>
            </div>
          )}
        </div>
      )}
    </section>
  );
}

function TrySearch({ kb }: { kb: KnowledgeBase }) {
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState("hybrid");
  const [hits, setHits] = useState<KnowledgeHit[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  return (
    <section className="space-y-3 rounded-xl border border-border bg-surface p-4" aria-label="Try a search">
      <p className="text-sm font-semibold">Try a search</p>
      <form
        className="flex flex-wrap gap-1.5"
        onSubmit={async (e) => {
          e.preventDefault();
          if (!query.trim()) return;
          setBusy(true);
          try {
            setHits((await api.searchKnowledge(kb.id, query, { mode, top_k: 5 })).hits);
            setError(null);
          } catch (err) {
            setError(errorText(err));
          } finally {
            setBusy(false);
          }
        }}
      >
        <Input aria-label="Question" className="min-w-0 flex-1" placeholder="How do I reset my password?" value={query} onChange={(e) => setQuery(e.target.value)} />
        <Select aria-label="Match" className="w-auto" value={mode} onChange={(e) => setMode(e.target.value)}>
          <option value="hybrid">Meaning and words</option>
          <option value="meaning">Meaning</option>
          <option value="words">Words</option>
        </Select>
        <Button type="submit" variant="primary" disabled={busy || !query.trim()}>
          {busy ? <Loader2 size={14} className="animate-spin" /> : <Search size={14} />} Search
        </Button>
      </form>
      {error && <p className="text-xs text-danger">{error}</p>}
      {hits && !hits.length && <p className="text-sm text-muted">Nothing found.</p>}
      {hits && hits.length > 0 && <SourceList sources={hits} prefix="try" />}
    </section>
  );
}

function DocumentRow({ kb, doc, onChange }: { kb: KnowledgeBase; doc: KnowledgeDocument; onChange: () => void }) {
  const [chunks, setChunks] = useState<KnowledgeHit[] | null>(null);
  const [open, setOpen] = useState(false);
  return (
    <li className="px-3 py-2 text-sm" data-testid="kb-document">
      <div className="flex items-center gap-2">
        <button
          type="button"
          className="flex min-w-0 flex-1 items-center gap-1.5 text-left"
          aria-expanded={open}
          disabled={doc.status !== "ready"}
          onClick={() => {
            setOpen(!open);
            if (!chunks) api.knowledgeChunks(kb.id, doc.id).then(setChunks).catch(() => setChunks([]));
          }}
        >
          {doc.status === "ready" ? open ? <ChevronDown size={13} /> : <ChevronRight size={13} /> : <span className="w-[13px]" />}
          <span className="truncate font-medium">{doc.title}</span>
          {doc.source !== doc.title && <span className="truncate text-xs text-faint">{doc.source}</span>}
        </button>
        <Badge tone={STATUS_TONE[doc.status]}>
          {doc.status === "processing" && <Loader2 size={10} className="animate-spin" />}
          {doc.status === "ready" ? `${doc.chunks} passages` : doc.status}
        </Badge>
        <span className="hidden w-20 text-right text-xs text-faint sm:inline">{timeAgo(doc.created_at)}</span>
        <Button
          size="icon-sm"
          variant="ghost"
          aria-label={`Remove ${doc.title}`}
          onClick={async () => {
            await api.deleteKnowledgeDocument(kb.id, doc.id);
            onChange();
          }}
        >
          <Trash2 size={13} />
        </Button>
      </div>
      {doc.status === "error" && doc.error && <p className="mt-1 pl-5 text-xs text-danger">{doc.error}</p>}
      {open && (
        <ol className="mt-2 max-h-72 space-y-1 overflow-y-auto pl-5">
          {(chunks ?? []).map((c, i) => (
            <li key={c.id} className="rounded-md bg-surface-2/60 px-2 py-1 text-xs">
              <p className="text-[10.5px] text-faint">
                #{i + 1}
                {c.page ? ` · page ${c.page}` : ""}
                {c.heading ? ` · ${c.heading}` : ""}
              </p>
              <p className="line-clamp-3 whitespace-pre-wrap">{passageText(c.text)}</p>
            </li>
          ))}
        </ol>
      )}
    </li>
  );
}

function KnowledgeDetail({ id }: { id: string }) {
  const [kb, setKb] = useState<(KnowledgeBase & { documents: KnowledgeDocument[] }) | null | undefined>(undefined);
  const load = useCallback(() => {
    api.knowledgeBase(id).then(setKb).catch(() => setKb(null));
  }, [id]);
  useEffect(load, [load]);
  const busy = kb?.documents.some((d) => d.status === "queued" || d.status === "processing");
  useEffect(() => {
    if (!busy) return;
    const t = setInterval(load, 1000);
    return () => clearInterval(t);
  }, [busy, load]);
  if (kb === undefined) return <Loader2 className="m-8 animate-spin text-muted" size={18} />;
  if (kb === null)
    return (
      <div className="h-full overflow-y-auto">
        <Header title="Knowledge" onBack={() => (window.location.hash = "#/knowledge")} />
        <p className="p-8 text-sm text-muted">That Knowledge Base doesn't exist (any more).</p>
      </div>
    );
  const passages = kb.documents.reduce((n, d) => n + (d.chunks ?? 0), 0);
  return (
    <div className="h-full overflow-y-auto">
      <Header title={kb.name} onBack={() => (window.location.hash = "#/knowledge")}>
        <Button
          variant="ghost"
          size="sm"
          onClick={async () => {
            if (!window.confirm(`Delete “${kb.name}” and everything in it?`)) return;
            await api.deleteKnowledgeBase(kb.id);
            window.location.hash = "#/knowledge";
          }}
        >
          <Trash2 size={13} /> Delete
        </Button>
      </Header>
      <main className="mx-auto max-w-5xl space-y-5 px-6 py-6">
        <div className="space-y-1">
          {kb.description && <p className="text-sm text-muted">{kb.description}</p>}
          <p className="text-xs text-faint">
            <span className="font-mono">{kb.id}</span> · {kb.embedding_label ?? kb.embedding_model} · passages of about {kb.chunk_size} characters ·{" "}
            {kb.documents.length} documents, {passages} passages
          </p>
        </div>
        <AddDocuments kb={kb} onAdded={load} />
        <section aria-label="Documents">
          <h2 className="mb-1.5 text-xs font-semibold tracking-wide text-faint uppercase">Documents</h2>
          {kb.documents.length ? (
            <ul className="divide-y divide-border rounded-xl border border-border bg-surface" data-testid="kb-documents">
              {kb.documents.map((doc) => (
                <DocumentRow key={doc.id} kb={kb} doc={doc} onChange={load} />
              ))}
            </ul>
          ) : (
            <p className="rounded-xl border border-dashed border-border p-6 text-center text-sm text-muted">No documents yet. Add some above.</p>
          )}
        </section>
        <TrySearch kb={kb} />
        <ChunkPreviewPanel kb={kb} />
      </main>
    </div>
  );
}

export function Knowledge({ id }: { id: string | null }) {
  return id ? <KnowledgeDetail key={id} id={id} /> : <KnowledgeList />;
}

export function KnowledgeLink() {
  return (
    <a href="#/knowledge" className="flex h-8 items-center gap-1 rounded-md px-2 text-sm text-muted hover:bg-surface-2 hover:text-text" data-testid="knowledge-link">
      <Library size={15} />
      <span className="hidden md:inline">Knowledge</span>
    </a>
  );
}
