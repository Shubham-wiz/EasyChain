// Types mirroring the Python flow spec (python/src/easychain/spec/models.py)
// and the API payloads. Settings stay loosely typed: the step catalog drives the forms.

export type FieldType = "text" | "number" | "yes_no" | "list" | "object" | "file" | "messages" | "any";
export type UpdateRule = "replace" | "append" | "merge" | "add";
export type StepType = "input" | "output" | "ai_model" | "instructions" | "http_request" | "code" | "decision";

export interface DataField {
  name: string;
  type: FieldType;
  update: UpdateRule;
  description: string;
}

export interface Step {
  id: string;
  type: StepType;
  name: string;
  description: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  settings: Record<string, any>;
}

export interface Connection {
  from: string;
  to: string;
  exit?: string | null;
}

export interface Position {
  x: number;
  y: number;
}

export interface StickyNote {
  id: string;
  text: string;
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface FlowSpec {
  version: 1;
  name: string;
  description: string;
  data: DataField[];
  steps: Step[];
  connections: Connection[];
  canvas: { steps: Record<string, Position>; notes: StickyNote[]; viewport?: Record<string, number> | null };
}

// ── catalog ───────────────────────────────────────────────────────────────

export interface FormField {
  key: string;
  label: string;
  kind: string;
  help?: string;
  example?: string;
  placeholder?: string;
  options?: { value: string; label: string }[];
  advanced?: boolean;
  pro?: boolean;
  show_if?: Record<string, string | string[]>;
  min?: number;
  max?: number;
  step?: number;
  technical?: string;
}

export interface StepTypeInfo {
  type: StepType;
  label: string;
  technical: string;
  category: string;
  icon: string;
  summary: string;
  beginner: boolean;
  default_name: string;
  defaults: Record<string, unknown>;
  form: FormField[];
  docs: string;
}

export interface ModelOption {
  id: string;
  label: string;
  input_per_m: number | null;
  output_per_m: number | null;
}

export interface ProviderInfo {
  id: string;
  label: string;
  key_env: string | null;
  key_label: string;
  key_url: string | null;
  key_set: boolean;
  key_source: string | null;
  models: ModelOption[];
}

export interface Catalog {
  categories: { id: string; label: string }[];
  steps: StepTypeInfo[];
  providers: ProviderInfo[];
  spec_version: number;
  field_types: FieldType[];
  update_rules: UpdateRule[];
}

// ── checks ────────────────────────────────────────────────────────────────

export interface Fix {
  kind: string;
  label: string;
  params: Record<string, unknown>;
}

export interface Issue {
  level: "error" | "warning";
  code: string;
  message: string;
  step?: string;
  setting?: string;
  hint?: string;
  fix?: Fix;
}

export interface FieldInfo {
  name: string;
  type: FieldType;
  update: UpdateRule;
  description: string;
  declared: boolean;
  role: "input" | "output" | "internal";
  is_input: boolean;
  is_output: boolean;
  written_by: string[];
  read_by: string[];
}

export interface Analysis {
  chat: boolean;
  fields: FieldInfo[];
  reads: Record<string, string[]>;
  writes: Record<string, Record<string, string>>;
  reachable: string[];
  upstream: Record<string, string | null>;
  exits: Record<string, string[]>;
}

export interface CheckResult {
  issues: Issue[];
  analysis: Analysis;
}

export interface CompileResult {
  source: string | null;
  module_name?: string;
  snippets: Record<string, string>;
  requirements: string[];
  issues: Issue[];
  error?: string;
}

// ── runs ──────────────────────────────────────────────────────────────────

export interface Usage {
  input_tokens: number;
  output_tokens: number;
}

export interface RunError {
  kind: string;
  message: string;
  hint?: string;
  detail?: string;
  fixes: Fix[];
  problems?: { field: string; message: string }[];
}

export type RunEvent =
  | { type: "run_started"; run_id: string; ts: number; thread_id: string; flow: string; stand_in: boolean }
  | { type: "step_started"; run_id: string; ts: number; step: string; input: Record<string, unknown> }
  | { type: "token"; run_id: string; ts: number; step: string; text: string }
  | {
      type: "step_finished";
      run_id: string;
      ts: number;
      step: string;
      output: Record<string, unknown>;
      duration_ms: number;
      usage?: Usage;
      cost?: number | null;
      model?: string | null;
    }
  | { type: "route"; run_id: string; ts: number; step: string; exit: string }
  | { type: "step_failed"; run_id: string; ts: number; step: string; error: RunError }
  | {
      type: "run_finished";
      run_id: string;
      ts: number;
      status: "ok" | "error";
      duration_ms: number;
      output?: Record<string, unknown>;
      reply?: string | null;
      usage?: Usage;
      cost?: number | null;
      error?: RunError;
      issues?: Issue[];
      step?: string | null;
      thread_id?: string;
    };

export interface RunSummary {
  run_id: string;
  flow_id: string | null;
  flow: string;
  inputs: Record<string, unknown>;
  started: number;
  status: "running" | "ok" | "error";
  duration_ms?: number;
  cost?: number | null;
  usage?: Usage;
}

export interface FlowListItem {
  id: string;
  name: string;
  description: string;
  steps: number;
  updated: number;
  problem?: string;
}

export interface TemplateInfo {
  id: string;
  name: string;
  description: string;
  category: string;
  proves: string[];
  keys: { provider: string; label: string; env: string; set: boolean }[];
  chat: boolean;
  sample_inputs: Record<string, unknown>;
  steps: number;
  has_tests: boolean;
}

export interface SecretInfo {
  name: string;
  source: "vault" | "environment";
}
