import { Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Dashboard } from "./components/Dashboard";
import { Editor } from "./components/Editor";
import { Home } from "./components/Home";
import { Inbox } from "./components/Inbox";
import { Knowledge } from "./components/Knowledge";
import { SignIn } from "./components/SignIn";
import { Skills } from "./components/Skills";
import { Button } from "./components/ui";
import { useAuth } from "./state/auth";
import { useCatalog } from "./state/catalog";

interface Route {
  flowId: string | null;
  tryIt: boolean;
  inbox: string | null | undefined;
  knowledge?: string | null;
  skills?: string | null;
  dashboard?: boolean;
}

function parseHash(): Route {
  const inbox = /^#\/inbox(?:\/([a-f0-9]+))?/.exec(window.location.hash);
  if (inbox) return { flowId: null, tryIt: false, inbox: inbox[1] ?? null };
  const knowledge = /^#\/knowledge(?:\/([a-z0-9_]+))?/.exec(window.location.hash);
  if (knowledge) return { flowId: null, tryIt: false, inbox: undefined, knowledge: knowledge[1] ?? null };
  if (/^#\/dashboard/.test(window.location.hash)) return { flowId: null, tryIt: false, inbox: undefined, dashboard: true };
  const skills = /^#\/skills(?:\/([a-z0-9-]+))?/.exec(window.location.hash);
  if (skills) return { flowId: null, tryIt: false, inbox: undefined, skills: skills[1] ?? null };
  const match = /^#\/flows\/([a-z0-9-]+)(\?try=1)?/.exec(window.location.hash);
  return match ? { flowId: match[1], tryIt: !!match[2], inbox: undefined } : { flowId: null, tryIt: false, inbox: undefined };
}

/** Links in notifications point at /inbox/<id>; the app routes with the hash. */
function redirectPath() {
  const match = /^\/inbox(?:\/([a-f0-9]+))?\/?$/.exec(window.location.pathname);
  if (match) window.history.replaceState(null, "", `/#/inbox${match[1] ? `/${match[1]}` : ""}`);
}
redirectPath();

export function navigate(flowId: string | null, opts: { tryIt?: boolean } = {}) {
  window.location.hash = flowId ? `#/flows/${flowId}${opts.tryIt ? "?try=1" : ""}` : "#/";
}

export default function App() {
  const [route, setRoute] = useState<Route>(parseHash);
  const catalog = useCatalog((s) => s.catalog);
  const error = useCatalog((s) => s.error);
  const load = useCatalog((s) => s.load);
  const auth = useAuth((s) => s.status);
  const authError = useAuth((s) => s.error);
  const loadAuth = useAuth((s) => s.load);
  // With sign-in on, nothing else is asked for until someone is signed in.
  const signedIn = !!auth && (auth.mode === "off" || auth.signed_in);

  useEffect(() => {
    void loadAuth();
    const onHash = () => setRoute(parseHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, [loadAuth]);
  useEffect(() => {
    if (signedIn && !useCatalog.getState().catalog) void load();
  }, [signedIn, load]);

  if (authError || error) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center">
        <p className="font-medium">Can't reach the Easy Chain server.</p>
        <p className="max-w-md text-sm text-muted">
          Start it with <span className="font-mono">make dev</span> (or <span className="font-mono">easychain dev</span>) and try again. ({authError ?? error})
        </p>
        <Button onClick={() => void (authError ? loadAuth() : load())}>Try again</Button>
      </div>
    );
  }
  if (auth && !signedIn && !catalog) return <SignIn setup={auth.setup_needed} />;
  if (!catalog) {
    return (
      <div className="flex h-full items-center justify-center text-muted">
        <Loader2 className="mr-2 animate-spin" size={18} /> Loading…
      </div>
    );
  }
  // Signed out while working (the session ran out): sign in over the page, keeping what's open.
  return (
    <>
      <Page route={route} />
      {auth && !signedIn && <SignIn setup={false} overlay />}
    </>
  );
}

function Page({ route }: { route: Route }) {
  if (route.inbox !== undefined) return <Inbox focus={route.inbox} onHome={() => navigate(null)} />;
  if (route.knowledge !== undefined) return <Knowledge id={route.knowledge} />;
  if (route.skills !== undefined) return <Skills name={route.skills} />;
  if (route.dashboard) return <Dashboard />;
  return route.flowId ? (
    <Editor key={route.flowId} flowId={route.flowId} tryIt={route.tryIt} onHome={() => navigate(null)} />
  ) : (
    <Home open={(id, opts) => navigate(id, opts)} />
  );
}
