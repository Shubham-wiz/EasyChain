import { Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Editor } from "./components/Editor";
import { Home } from "./components/Home";
import { Inbox } from "./components/Inbox";
import { Button } from "./components/ui";
import { useCatalog } from "./state/catalog";

interface Route {
  flowId: string | null;
  tryIt: boolean;
  inbox: string | null | undefined;
}

function parseHash(): Route {
  const inbox = /^#\/inbox(?:\/([a-f0-9]+))?/.exec(window.location.hash);
  if (inbox) return { flowId: null, tryIt: false, inbox: inbox[1] ?? null };
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

  useEffect(() => {
    void load();
    const onHash = () => setRoute(parseHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, [load]);

  if (error) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center">
        <p className="font-medium">Can't reach the Easy Chain server.</p>
        <p className="max-w-md text-sm text-muted">
          Start it with <span className="font-mono">make dev</span> (or <span className="font-mono">easychain dev</span>) and try again. ({error})
        </p>
        <Button onClick={() => void load()}>Try again</Button>
      </div>
    );
  }
  if (!catalog) {
    return (
      <div className="flex h-full items-center justify-center text-muted">
        <Loader2 className="mr-2 animate-spin" size={18} /> Loading…
      </div>
    );
  }
  if (route.inbox !== undefined) return <Inbox focus={route.inbox} onHome={() => navigate(null)} />;
  return route.flowId ? (
    <Editor key={route.flowId} flowId={route.flowId} tryIt={route.tryIt} onHome={() => navigate(null)} />
  ) : (
    <Home open={(id, opts) => navigate(id, opts)} />
  );
}
