// Small UI kit in the shadcn/ui style: Radix primitives + Tailwind, themed by the tokens in index.css.

import * as DialogPrimitive from "@radix-ui/react-dialog";
import * as SwitchPrimitive from "@radix-ui/react-switch";
import * as TabsPrimitive from "@radix-ui/react-tabs";
import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import { CircleHelp, Eye, EyeOff, X } from "lucide-react";
import {
  createContext,
  forwardRef,
  useContext,
  useId,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";
import { cn } from "../lib/utils";

type Variant = "primary" | "secondary" | "ghost" | "danger" | "outline";
type Size = "sm" | "md" | "icon" | "icon-sm";

const variants: Record<Variant, string> = {
  primary: "bg-accent text-accent-text hover:opacity-90 shadow-sm",
  secondary: "bg-surface-2 text-text hover:bg-border/70",
  ghost: "text-muted hover:bg-surface-2 hover:text-text",
  danger: "bg-danger text-white hover:opacity-90",
  outline: "border border-border bg-surface text-text hover:bg-surface-2",
};

const sizes: Record<Size, string> = {
  sm: "h-7 px-2.5 text-xs gap-1.5",
  md: "h-9 px-3.5 text-sm gap-2",
  icon: "h-9 w-9 justify-center",
  "icon-sm": "h-7 w-7 justify-center",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { className, variant = "secondary", size = "md", type = "button", ...props },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      className={cn(
        "inline-flex shrink-0 items-center rounded-md font-medium transition-colors disabled:pointer-events-none disabled:opacity-50",
        variants[variant],
        sizes[size],
        className,
      )}
      {...props}
    />
  );
});

const fieldBase =
  "w-full rounded-md border border-border bg-surface px-2.5 text-sm text-text placeholder:text-faint focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/20 disabled:opacity-60";

/** What a Field tells the control it labels: the id of its problem message, if it has one. */
const FieldContext = createContext<{ id?: string; describedBy?: string; invalid: boolean } | null>(null);

/** aria-describedby / aria-invalid for the control a Field labels (matched by its id). */
function useFieldAria(id: string | undefined): { "aria-describedby"?: string; "aria-invalid"?: boolean } {
  const field = useContext(FieldContext);
  if (!field?.describedBy || !id || field.id !== id) return {};
  return { "aria-describedby": field.describedBy, "aria-invalid": field.invalid || undefined };
}

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function Input(
  { className, ...props },
  ref,
) {
  const aria = useFieldAria(props.id);
  return <input ref={ref} className={cn(fieldBase, "h-9", className)} {...aria} {...props} />;
});

/**
 * Text being edited that is saved later (on leaving the box). It follows the saved value
 * whenever that changes from outside: undo, a one-click fix, or a row above it removed.
 */
export function useDraft(value: string): [string, (text: string) => void] {
  const [draft, setDraft] = useState(value);
  const [shown, setShown] = useState(value);
  if (value !== shown) {
    setShown(value);
    setDraft(value);
  }
  return [draft, setDraft];
}

type DraftInputProps = Omit<InputHTMLAttributes<HTMLInputElement>, "value" | "defaultValue" | "onChange"> & {
  value: string;
  /** Called with the (cleaned) text when you leave the box or press Enter, if it changed. */
  onCommit: (value: string) => void;
  /** Tidy what was typed before saving it, e.g. into a field name. */
  clean?: (text: string) => string;
};

/** A text box that saves when you leave it (or press Enter) rather than on every key. */
export const DraftInput = forwardRef<HTMLInputElement, DraftInputProps>(function DraftInput({ value, onCommit, clean, onBlur, onKeyDown, ...props }, ref) {
  const [draft, setDraft] = useDraft(value);
  return (
    <Input
      ref={ref}
      {...props}
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={(e) => {
        const next = clean ? clean(draft) : draft;
        setDraft(next);
        if (next !== value) onCommit(next);
        onBlur?.(e);
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter") e.currentTarget.blur();
        onKeyDown?.(e);
      }}
    />
  );
});

/** A saved secret the server sends back hidden ("••••••"): sending it back unchanged keeps it. */
export function isMasked(value: unknown): value is string {
  return typeof value === "string" && /^•{3,}$/.test(value);
}

const SECRET_REF = /\{secret:[A-Za-z_][A-Za-z0-9_]*\}/;

type SecretInputProps = Omit<InputHTMLAttributes<HTMLInputElement>, "value" | "defaultValue" | "onChange" | "type"> & {
  value: string;
  onChange: (value: string) => void;
};

/**
 * A password-style box for passwords, webhook URLs and auth headers. A value the server hid
 * ("••••••") shows as saved, not as text, and stays as it is unless you type a new one.
 * Typing a plain value nudges you to keep it as a {secret:NAME} instead.
 */
export function SecretInput({ value, onChange, placeholder, className, ...props }: SecretInputProps) {
  const [visible, setVisible] = useState(false);
  // The hidden value as the server sent it, to send back when nothing new is typed.
  const [saved, setSaved] = useState(isMasked(value) ? value : null);
  if (isMasked(value) && value !== saved) setSaved(value);
  const hintId = useId();
  const hidden = isMasked(value);
  const plain = !!value && !hidden && !SECRET_REF.test(value);
  const fieldAria = useFieldAria(props.id);
  const describedBy = [fieldAria["aria-describedby"], plain ? hintId : null].filter(Boolean).join(" ") || undefined;
  return (
    <div className="space-y-1">
      <div className="relative">
        <Input
          {...props}
          type={visible ? "text" : "password"}
          autoComplete="off"
          spellCheck={false}
          className={cn("pr-16", className)}
          value={hidden ? "" : value}
          placeholder={hidden ? "Saved (hidden). Type to replace it." : placeholder}
          aria-describedby={describedBy}
          onChange={(e) => onChange(e.target.value === "" && saved ? saved : e.target.value)}
        />
        <div className="absolute inset-y-0 right-1 flex items-center gap-0.5">
          {hidden ? (
            <button
              type="button"
              className="h-6 min-w-6 rounded px-1.5 text-[11px] text-muted hover:text-text"
              onClick={() => {
                setSaved(null);
                onChange("");
              }}
            >
              Clear
            </button>
          ) : (
            <button
              type="button"
              className="flex h-6 w-6 items-center justify-center rounded text-faint hover:text-muted"
              aria-label={visible ? "Hide" : "Show"}
              aria-pressed={visible}
              onClick={() => setVisible(!visible)}
            >
              {visible ? <EyeOff size={14} /> : <Eye size={14} />}
            </button>
          )}
        </div>
      </div>
      {plain && (
        <p id={hintId} className="text-[11px] text-muted">
          Saved as you typed it. Safer: add it under Settings › Keys and providers › Other secrets, then type{" "}
          <span className="font-mono">{"{secret:NAME}"}</span> here.
        </p>
      )}
    </div>
  );
}

/**
 * React keys for the rows of a list edited in place, so a row keeps its key (and what is
 * typed in it) when a row above it is removed or moved. Call `remove`/`move` along with the edit.
 */
export function useRowKeys(length: number) {
  const counter = useRef(0);
  const keys = useRef<number[]>([]);
  while (keys.current.length < length) keys.current.push(counter.current++);
  if (keys.current.length > length) keys.current.length = length;
  return {
    keys: keys.current,
    remove: (i: number) => void keys.current.splice(i, 1),
    move: (i: number, by: number) => {
      const [key] = keys.current.splice(i, 1);
      keys.current.splice(i + by, 0, key);
    },
  };
}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(function Textarea(
  { className, ...props },
  ref,
) {
  const aria = useFieldAria(props.id);
  return <textarea ref={ref} className={cn(fieldBase, "min-h-20 py-2 leading-relaxed", className)} {...aria} {...props} />;
});

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(function Select(
  { className, children, ...props },
  ref,
) {
  const aria = useFieldAria(props.id);
  return (
    <select ref={ref} className={cn(fieldBase, "h-9 cursor-pointer pr-7", className)} {...aria} {...props}>
      {children}
    </select>
  );
});

export function Switch({
  checked,
  onCheckedChange,
  label,
  id,
}: {
  checked: boolean;
  onCheckedChange: (v: boolean) => void;
  label?: string;
  id?: string;
}) {
  return (
    <SwitchPrimitive.Root
      id={id}
      checked={checked}
      onCheckedChange={onCheckedChange}
      aria-label={label}
      className="relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full bg-border transition-colors data-[state=checked]:bg-accent"
    >
      <SwitchPrimitive.Thumb className="block h-4 w-4 translate-x-0.5 rounded-full bg-white shadow transition-transform data-[state=checked]:translate-x-[18px]" />
    </SwitchPrimitive.Root>
  );
}

export const TooltipProvider = TooltipPrimitive.Provider;

export function Tooltip({ content, children, side = "top" }: { content: ReactNode; children: ReactNode; side?: "top" | "bottom" | "left" | "right" }) {
  if (!content) return <>{children}</>;
  return (
    <TooltipPrimitive.Root delayDuration={250}>
      <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
      <TooltipPrimitive.Portal>
        <TooltipPrimitive.Content
          side={side}
          sideOffset={6}
          className="z-50 max-w-xs rounded-md bg-text px-2.5 py-1.5 text-xs leading-relaxed text-bg shadow-lg"
        >
          {content}
        </TooltipPrimitive.Content>
      </TooltipPrimitive.Portal>
    </TooltipPrimitive.Root>
  );
}

export function Help({ text, example, technical }: { text?: string; example?: string; technical?: string }) {
  if (!text && !example) return null;
  return (
    <Tooltip
      content={
        <span className="block space-y-1">
          {text && <span className="block">{text}</span>}
          {example && <span className="block opacity-80">Example: {example}</span>}
          {technical && <span className="block font-mono opacity-60">{technical}</span>}
        </span>
      }
    >
      <button type="button" className="inline-flex text-faint hover:text-muted" aria-label={`Help: ${text ?? ""}`}>
        <CircleHelp size={13} />
      </button>
    </Tooltip>
  );
}

export function Dialog({
  open,
  onOpenChange,
  title,
  description,
  children,
  wide,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: string;
  children: ReactNode;
  wide?: boolean;
}) {
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-black/40 backdrop-blur-[1px]" />
        <DialogPrimitive.Content
          className={cn(
            "fixed top-1/2 left-1/2 z-50 flex max-h-[88vh] w-[94vw] -translate-x-1/2 -translate-y-1/2 flex-col rounded-xl border border-border bg-surface shadow-2xl",
            wide ? "max-w-5xl" : "max-w-lg",
          )}
        >
          <div className="flex items-start justify-between gap-4 border-b border-border px-5 py-4">
            <div>
              <DialogPrimitive.Title className="text-base font-semibold">{title}</DialogPrimitive.Title>
              {description ? (
                <DialogPrimitive.Description className="mt-0.5 text-sm text-muted">{description}</DialogPrimitive.Description>
              ) : (
                <DialogPrimitive.Description className="sr-only">{title}</DialogPrimitive.Description>
              )}
            </div>
            <DialogPrimitive.Close asChild>
              <Button variant="ghost" size="icon-sm" aria-label="Close">
                <X size={16} />
              </Button>
            </DialogPrimitive.Close>
          </div>
          <div className="min-h-0 flex-1 overflow-auto px-5 py-4">{children}</div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

export const Tabs = TabsPrimitive.Root;

export function TabsList({ children, className }: { children: ReactNode; className?: string }) {
  return <TabsPrimitive.List className={cn("flex gap-1 border-b border-border px-3", className)}>{children}</TabsPrimitive.List>;
}

export function TabsTrigger({ value, children }: { value: string; children: ReactNode }) {
  return (
    <TabsPrimitive.Trigger
      value={value}
      className="-mb-px border-b-2 border-transparent px-2.5 py-2 text-sm font-medium text-muted hover:text-text data-[state=active]:border-accent data-[state=active]:text-text"
    >
      {children}
    </TabsPrimitive.Trigger>
  );
}

export const TabsContent = TabsPrimitive.Content;

export function Badge({ children, tone = "neutral", className }: { children: ReactNode; tone?: "neutral" | "accent" | "ok" | "warn" | "danger"; className?: string }) {
  const tones = {
    neutral: "bg-surface-2 text-muted",
    accent: "bg-accent-soft text-accent",
    ok: "bg-ok-soft text-ok",
    warn: "bg-warn-soft text-warn",
    danger: "bg-danger-soft text-danger",
  };
  return (
    <span className={cn("inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-medium leading-none", tones[tone], className)}>
      {children}
    </span>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="rounded border border-border bg-surface-2 px-1 py-px font-mono text-[10px] text-muted">{children}</kbd>
  );
}

export function Field({
  label,
  help,
  example,
  technical,
  htmlFor,
  children,
  pro,
  issue,
}: {
  label: string;
  help?: string;
  example?: string;
  technical?: string;
  htmlFor?: string;
  children: ReactNode;
  pro?: boolean;
  issue?: { level: "error" | "warning"; message: string } | null;
}) {
  const issueId = useId();
  const context = { id: htmlFor, describedBy: issue ? issueId : undefined, invalid: issue?.level === "error" };
  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-1.5">
        <label htmlFor={htmlFor} className="text-xs font-medium text-text">
          {label}
        </label>
        {pro && technical && <span className="font-mono text-[10px] text-faint">{technical}</span>}
        <Help text={help} example={example} technical={pro ? undefined : technical} />
      </div>
      <FieldContext.Provider value={context}>{children}</FieldContext.Provider>
      {issue && (
        <p id={issueId} className={cn("text-xs", issue.level === "error" ? "text-danger" : "text-warn")}>
          {issue.message}
        </p>
      )}
    </div>
  );
}
