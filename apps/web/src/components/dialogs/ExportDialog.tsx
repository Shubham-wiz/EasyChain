import { Check, Copy, Download, FileCode2, FileText, Package } from "lucide-react";
import { useEffect, useState } from "react";
import { api, download } from "../../lib/api";
import type { CompileResult } from "../../lib/types";
import { useFlow } from "../../state/flow";
import { useUi } from "../../state/ui";
import { CodeView } from "../CodeView";
import { IssueList } from "../inspector/Inspector";
import { Button, Dialog } from "../ui";

export function ExportDialog() {
  const open = useUi((s) => s.exportOpen);
  const setOpen = useUi((s) => s.setExportOpen);
  const spec = useFlow((s) => s.spec);
  const flowId = useFlow((s) => s.flowId);
  const [result, setResult] = useState<CompileResult | null>(null);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open || !spec) return;
    setResult(null);
    setError(null);
    api.compile(spec).then(setResult).catch((e) => setError(String(e.message ?? e)));
  }, [open, spec]);

  if (!spec) return null;
  const errors = result?.issues.filter((i) => i.level === "error") ?? [];
  const module = result?.module_name ?? "flow";
  const ready = !!result?.source && !errors.length;

  return (
    <Dialog open={open} onOpenChange={setOpen} title="Export as Python" description="Plain LangChain + LangGraph code. It runs without Easy Chain." wide>
      <div className="space-y-4">
        {error && <p className="text-sm text-danger">{error}</p>}
        {errors.length > 0 && (
          <div className="space-y-2">
            <p className="text-sm font-medium text-danger">Fix these problems to export:</p>
            <IssueList issues={errors} />
          </div>
        )}
        <div className="flex flex-wrap gap-2">
          <Button
            variant="primary"
            disabled={!ready || busy}
            data-testid="export-zip"
            onClick={async () => {
              setBusy(true);
              try {
                download(await api.exportZip(spec), `${module}.zip`);
              } catch (e) {
                setError(e instanceof Error ? e.message : String(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            <Package size={15} /> Download project (.zip)
          </Button>
          <Button variant="outline" disabled={!ready} onClick={() => download(new Blob([result!.source!], { type: "text/x-python" }), `${module}.py`)}>
            <FileCode2 size={15} /> {module}.py
          </Button>
          <Button
            variant="outline"
            disabled={!flowId}
            onClick={async () => download(new Blob([await api.flowYaml(flowId!)], { type: "text/yaml" }), `${module}.flow.yaml`)}
          >
            <FileText size={15} /> Flow file (.yaml)
          </Button>
          <Button
            variant="ghost"
            disabled={!result?.source}
            onClick={async () => {
              await navigator.clipboard.writeText(result!.source!);
              setCopied(true);
              setTimeout(() => setCopied(false), 1500);
            }}
          >
            {copied ? <Check size={15} /> : <Copy size={15} />} {copied ? "Copied" : "Copy code"}
          </Button>
        </div>
        {result?.source ? (
          <CodeView value={result.source} readOnly height="52vh" label="Exported Python" />
        ) : (
          !errors.length && <p className="text-sm text-muted">Generating code…</p>
        )}
        {result?.requirements && result.source && (
          <p className="text-xs text-muted">
            <Download size={12} className="mr-1 inline" />
            requirements.txt: <span className="font-mono">{result.requirements.join("  ")}</span>
          </p>
        )}
      </div>
    </Dialog>
  );
}
