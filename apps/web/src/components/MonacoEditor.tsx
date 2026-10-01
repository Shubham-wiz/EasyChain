// Loaded lazily: Monaco is large, and most of the app doesn't need it.
import { Editor, loader, type OnMount } from "@monaco-editor/react";
import * as monaco from "monaco-editor/esm/vs/editor/editor.api";
import "monaco-editor/esm/vs/editor/editor.all.js";
import "monaco-editor/esm/vs/basic-languages/python/python.contribution";
import "monaco-editor/esm/vs/basic-languages/yaml/yaml.contribution";
import EditorWorker from "monaco-editor/esm/vs/editor/editor.worker?worker";
import { useEffect } from "react";
import { useCheck } from "../state/check";
import { useUi } from "../state/ui";

self.MonacoEnvironment = { getWorker: () => new EditorWorker() };
loader.config({ monaco });

let completionsRegistered = false;

/** Suggest data["field"] for every Flow Data field while writing Code steps. */
function registerFlowDataCompletions() {
  if (completionsRegistered) return;
  completionsRegistered = true;
  monaco.languages.registerCompletionItemProvider("python", {
    triggerCharacters: ['"', "'", "["],
    provideCompletionItems(model, position) {
      const word = model.getWordUntilPosition(position);
      const range = {
        startLineNumber: position.lineNumber,
        endLineNumber: position.lineNumber,
        startColumn: word.startColumn,
        endColumn: word.endColumn,
      };
      const fields = useCheck.getState().analysis?.fields ?? [];
      return {
        suggestions: fields.map((f) => ({
          label: `data["${f.name}"]`,
          kind: monaco.languages.CompletionItemKind.Field,
          detail: `Flow Data · ${f.type}`,
          documentation: f.description || `Set by ${f.written_by.join(", ") || "Input"}`,
          insertText: `data.get("${f.name}")`,
          range,
        })),
      };
    },
  });
}

export default function MonacoEditor({
  value,
  onChange,
  language = "python",
  readOnly = false,
  height = "100%",
  label,
}: {
  value: string;
  onChange?: (value: string) => void;
  language?: string;
  readOnly?: boolean;
  height?: string | number;
  label?: string;
}) {
  const theme = useUi((s) => s.theme);
  useEffect(registerFlowDataCompletions, []);
  const onMount: OnMount = (editor) => {
    editor.getDomNode()?.setAttribute("aria-label", label ?? "Code editor");
  };
  return (
    <Editor
      height={height}
      language={language}
      value={value}
      theme={theme === "dark" ? "vs-dark" : "vs"}
      onChange={(v) => onChange?.(v ?? "")}
      onMount={onMount}
      options={{
        readOnly,
        minimap: { enabled: false },
        fontSize: 12.5,
        lineNumbers: readOnly ? "on" : "on",
        scrollBeyondLastLine: false,
        wordWrap: "on",
        tabSize: 4,
        automaticLayout: true,
        renderLineHighlight: readOnly ? "none" : "line",
        padding: { top: 8, bottom: 8 },
      }}
    />
  );
}
