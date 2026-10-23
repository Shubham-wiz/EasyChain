import { Loader2, LogIn, UserPlus } from "lucide-react";
import { useState, type FormEvent, type ReactNode } from "react";
import { api } from "../lib/api";
import { cn } from "../lib/utils";
import { useAuth } from "../state/auth";
import { Button, Field, Input } from "./ui";

function Card({ title, children, overlay }: { title: string; children: ReactNode; overlay?: boolean }) {
  return (
    <div className={cn("flex h-full items-center justify-center overflow-y-auto bg-bg p-6", overlay && "fixed inset-0 z-[60] bg-bg/95 backdrop-blur-sm")}>
      <main className="w-full max-w-sm space-y-5 rounded-xl border border-border bg-surface p-6 shadow-xl" aria-label={title}>
        <div className="flex items-center gap-2">
          <img src="/favicon.svg" alt="" className="h-7 w-7" />
          <h1 className="text-lg font-semibold">{title}</h1>
        </div>
        {children}
      </main>
    </div>
  );
}

function useSubmit(work: () => Promise<unknown>) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await work();
      await useAuth.getState().load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  };
  return { busy, error, submit };
}

/** The first account on a server with sign-in on: an Owner, made with the code from the server's log. */
function Setup() {
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const { busy, error, submit } = useSubmit(() => api.setup({ code: code.trim(), email: email.trim(), name: name.trim(), password }));
  return (
    <Card title="Make the first account">
      <p className="text-sm text-muted">
        This server answers the network, so people sign in. The first account is an <strong>Owner</strong>, who can add everyone else.
      </p>
      <form className="space-y-3" onSubmit={submit}>
        <Field label="Code from the server's log" htmlFor="setup-code">
          <Input id="setup-code" autoComplete="one-time-code" placeholder="ABCD-EFGH-JKLM" className="font-mono uppercase" value={code} onChange={(e) => setCode(e.target.value)} autoFocus />
          <p className="text-xs text-faint">
            Easy Chain prints it when it starts (“enter the code …”). With Docker: <span className="font-mono">docker compose logs easychain</span>. Restarting prints a new one.
          </p>
        </Field>
        <Field label="Your name" htmlFor="setup-name">
          <Input id="setup-name" autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label="Email" htmlFor="setup-email">
          <Input id="setup-email" type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} />
        </Field>
        <Field label="Password (at least 10 characters)" htmlFor="setup-password">
          <Input id="setup-password" type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
        {error && (
          <p className="text-sm text-danger" role="alert">
            {error}
          </p>
        )}
        <Button type="submit" variant="primary" className="w-full justify-center" disabled={busy || !code.trim() || !email.trim() || !password}>
          {busy ? <Loader2 size={15} className="animate-spin" /> : <UserPlus size={15} />} Make the account
        </Button>
      </form>
    </Card>
  );
}

function SignInForm({ overlay }: { overlay?: boolean }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const { busy, error, submit } = useSubmit(() => api.signIn(email.trim(), password));
  return (
    <Card title={overlay ? "Sign in again" : "Sign in to Easy Chain"} overlay={overlay}>
      {overlay && <p className="text-sm text-muted">You were signed out. Sign in to carry on where you were.</p>}
      <form className="space-y-3" onSubmit={submit}>
        <Field label="Email" htmlFor="signin-email">
          <Input id="signin-email" type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} autoFocus />
        </Field>
        <Field label="Password" htmlFor="signin-password">
          <Input id="signin-password" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
        {error && (
          <p className="text-sm text-danger" role="alert">
            {error}
          </p>
        )}
        <Button type="submit" variant="primary" className="w-full justify-center" disabled={busy || !email.trim() || !password}>
          {busy ? <Loader2 size={15} className="animate-spin" /> : <LogIn size={15} />} Sign in
        </Button>
      </form>
      <p className="text-xs text-faint">
        Forgot your password? An Owner can set a new one in Settings → People, or on the server with{" "}
        <span className="font-mono">easychain users password you@example.com</span>.
      </p>
    </Card>
  );
}

/** What someone sees before they're signed in: the first account, or signing in. */
export function SignIn({ setup, overlay }: { setup: boolean; overlay?: boolean }) {
  return setup ? <Setup /> : <SignInForm overlay={overlay} />;
}
