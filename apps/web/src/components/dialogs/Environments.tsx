import { Layers3, Plus, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import type { EnvironmentInfo } from "../../lib/types";
import { useCan } from "../../state/auth";
import { Badge, Button, Input, Locked } from "../ui";

type Pairs = [string, string][];

function message(err: unknown) {
  return err instanceof Error ? err.message : String(err);
}

function SwapRows({ label, from, to, rows, onChange }: { label: string; from: string; to: string; rows: Pairs; onChange: (rows: Pairs) => void }) {
  return (
    <div className="space-y-1">
      <p className="text-xs font-medium">{label}</p>
      {rows.map(([a, b], i) => (
        <div key={i} className="flex items-center gap-1.5">
          <Input aria-label={`${label}: ${from} ${i + 1}`} className="h-8 font-mono text-xs" placeholder={from} value={a} onChange={(e) => onChange(rows.map((r, j) => (j === i ? [e.target.value, r[1]] : r)))} />
          <span aria-hidden className="text-muted">
            →
          </span>
          <Input aria-label={`${label}: ${to} ${i + 1}`} className="h-8 font-mono text-xs" placeholder={to} value={b} onChange={(e) => onChange(rows.map((r, j) => (j === i ? [r[0], e.target.value] : r)))} />
          <Button size="icon-sm" variant="ghost" aria-label={`Remove ${label.toLowerCase()} ${i + 1}`} onClick={() => onChange(rows.filter((_, j) => j !== i))}>
            <Trash2 size={12} />
          </Button>
        </div>
      ))}
      <Button size="sm" variant="ghost" onClick={() => onChange([...rows, ["", ""]])}>
        <Plus size={12} /> Add a swap
      </Button>
    </div>
  );
}

function EnvironmentCard({ env, reload }: { env: EnvironmentInfo; reload: () => void }) {
  const owner = useCan("owner");
  const [models, setModels] = useState<Pairs>(Object.entries(env.models));
  const [knowledge, setKnowledge] = useState<Pairs>(Object.entries(env.knowledge));
  const [secret, setSecret] = useState({ name: "", value: "" });
  const [error, setError] = useState<string[] | null>(null);
  const [saved, setSaved] = useState(false);
  const save = async () => {
    try {
      await api.saveEnvironment(env.id, {
        models: Object.fromEntries(models.filter(([a, b]) => a.trim() && b.trim()).map(([a, b]) => [a.trim(), b.trim()])),
        knowledge: Object.fromEntries(knowledge.filter(([a, b]) => a.trim() && b.trim()).map(([a, b]) => [a.trim(), b.trim()])),
      });
      setError(null);
      setSaved(true);
      reload();
    } catch (err) {
      const problems = (err as { problems?: string[] }).problems;
      setError(problems?.length ? problems : [message(err)]);
    }
  };
  return (
    <li className="space-y-3 rounded-lg border border-border p-3" data-testid={`environment-${env.id}`}>
      <p className="text-sm font-semibold">{env.label}</p>
      <Locked locked={!owner} className="space-y-3">
        <SwapRows label="Model swaps" from="the flow's model" to="the model to use" rows={models} onChange={(r) => (setModels(r), setSaved(false))} />
        <SwapRows label="Knowledge Base swaps" from="the flow's Knowledge Base" to="the one to search" rows={knowledge} onChange={(r) => (setKnowledge(r), setSaved(false))} />
        <Button size="sm" variant="outline" onClick={() => void save()}>
          {saved ? "Saved" : `Save ${env.label}'s swaps`}
        </Button>
        {error && (
          <ul className="list-disc pl-4 text-xs text-danger" role="alert">
            {error.map((e) => (
              <li key={e}>{e}</li>
            ))}
          </ul>
        )}
        <div className="space-y-1">
          <p className="text-xs font-medium">Secrets, over the workspace's</p>
          <div className="flex flex-wrap gap-1">
            {env.secrets.length === 0 && <span className="text-xs text-faint">None: the workspace's apply.</span>}
            {env.secrets.map((name) => (
              <Badge key={name}>
                <span className="font-mono">{name}</span>
                <button type="button" aria-label={`Delete ${name} from ${env.label}`} onClick={() => void api.deleteEnvironmentSecret(env.id, name).then(reload)}>
                  <Trash2 size={10} />
                </button>
              </Badge>
            ))}
          </div>
          <form
            className="flex gap-1.5"
            onSubmit={async (e) => {
              e.preventDefault();
              try {
                await api.setEnvironmentSecret(env.id, secret.name.trim(), secret.value);
                setSecret({ name: "", value: "" });
                reload();
              } catch (err) {
                setError([message(err)]);
              }
            }}
          >
            <Input aria-label={`Secret name for ${env.label}`} className="h-8 font-mono text-xs" placeholder="NAME" value={secret.name} onChange={(e) => setSecret({ ...secret, name: e.target.value })} />
            <Input aria-label={`Secret value for ${env.label}`} type="password" autoComplete="off" className="h-8 text-xs" placeholder="value" value={secret.value} onChange={(e) => setSecret({ ...secret, value: e.target.value })} />
            <Button type="submit" size="sm" variant="outline" disabled={!secret.name.trim() || !secret.value}>
              Set
            </Button>
          </form>
        </div>
      </Locked>
    </li>
  );
}

/** Settings → Environments: what a published flow's runs use in dev, staging and production. */
export function EnvironmentsSection() {
  const [envs, setEnvs] = useState<EnvironmentInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = () => {
    api
      .environments()
      .then(setEnvs)
      .catch((err) => setError(message(err)));
  };
  useEffect(load, []);
  return (
    <section className="space-y-3" aria-label="Environments">
      <h3 className="flex items-center gap-1.5 text-sm font-semibold">
        <Layers3 size={14} /> Environments
      </h3>
      <p className="text-xs text-muted">
        A published flow runs in Development, Staging or Production. Each can use its own secrets (a production account's key), swap models (a cheaper one in
        development) and swap Knowledge Bases. Drafts run as drawn.
      </p>
      {error && <p className="text-xs text-danger">{error}</p>}
      <ul className="space-y-2">
        {(envs ?? []).map((env) => (
          <EnvironmentCard key={`${env.id}:${env.secrets.join()}`} env={env} reload={load} />
        ))}
      </ul>
    </section>
  );
}
