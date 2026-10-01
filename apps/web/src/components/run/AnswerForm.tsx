import { Check, Send, X } from "lucide-react";
import { useState } from "react";
import type { AskRequest } from "../../lib/types";
import { Button, Input, Textarea } from "../ui";

export interface Answer {
  action: "approve" | "reject";
  value?: unknown;
  comment?: string;
}

function Shown({ show }: { show?: Record<string, unknown> }) {
  const entries = Object.entries(show ?? {});
  if (!entries.length) return null;
  return (
    <div className="space-y-1.5">
      {entries.map(([k, v]) => (
        <div key={k} className="rounded-md bg-surface px-2 py-1.5">
          <p className="font-mono text-[10.5px] text-faint">{k}</p>
          <p className="text-sm whitespace-pre-wrap">{typeof v === "string" ? v : JSON.stringify(v, null, 2)}</p>
        </div>
      ))}
    </div>
  );
}

/** Approve, reject, edit, type an answer or choose an option for an Ask a Human step. */
export function AnswerForm({ request, onAnswer, busy, testId }: { request: AskRequest; onAnswer: (answer: Answer) => void; busy?: boolean; testId?: string }) {
  const kind = request.kind ?? "approve";
  const [text, setText] = useState(kind === "edit" ? String(request.value ?? "") : "");
  const [comment, setComment] = useState("");
  const withComment = (answer: Answer): Answer => (comment.trim() ? { ...answer, comment: comment.trim() } : answer);
  return (
    <div className="space-y-2.5" data-testid={testId}>
      <p className="text-sm font-medium">{request.question}</p>
      <Shown show={request.show} />
      {kind === "edit" && (
        <label className="block space-y-1">
          <span className="text-xs text-muted">
            Edit <span className="font-mono">{request.field}</span>
          </span>
          <Textarea aria-label={`Edit ${request.field ?? "the value"}`} rows={4} value={text} onChange={(e) => setText(e.target.value)} />
        </label>
      )}
      {kind === "answer" && (
        <form
          className="flex gap-1.5"
          onSubmit={(e) => {
            e.preventDefault();
            if (text.trim()) onAnswer({ action: "approve", value: text.trim() });
          }}
        >
          <Input aria-label="Your answer" placeholder="Type your answer" value={text} onChange={(e) => setText(e.target.value)} />
          <Button type="submit" variant="primary" disabled={busy || !text.trim()}>
            <Send size={13} /> Send
          </Button>
        </form>
      )}
      {kind === "choose" && (
        <div className="flex flex-wrap gap-1.5">
          {(request.options ?? []).map((option) => (
            <Button key={option} variant="outline" size="sm" disabled={busy} onClick={() => onAnswer(withComment({ action: "approve", value: option }))}>
              {option}
            </Button>
          ))}
        </div>
      )}
      {kind !== "answer" && <Input aria-label="Comment (optional)" placeholder="Comment (optional)" value={comment} onChange={(e) => setComment(e.target.value)} />}
      {(kind === "approve" || kind === "edit") && (
        <div className="flex gap-1.5">
          <Button
            variant="primary"
            size="sm"
            disabled={busy}
            onClick={() => onAnswer(withComment(kind === "edit" && text !== String(request.value ?? "") ? { action: "approve", value: text } : { action: "approve" }))}
          >
            <Check size={13} /> Approve
          </Button>
          <Button variant="outline" size="sm" disabled={busy} onClick={() => onAnswer(withComment({ action: "reject" }))}>
            <X size={13} /> Reject
          </Button>
        </div>
      )}
    </div>
  );
}
