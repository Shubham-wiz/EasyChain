// Types mirroring the Python flow spec (python/src/easychain/spec/models.py)
// and the API payloads. Settings stay loosely typed: the step catalog drives the forms.

export type FieldType = "text" | "number" | "yes_no" | "list" | "object" | "file" | "messages" | "any";
export type UpdateRule = "replace" | "append" | "merge" | "add" | "custom";
export type StepType =
  | "input"
  | "output"
  | "ai_model"
  | "instructions"
  | "http_request"
  | "code"
  | "decision"
  | "ask_human"
  | "for_each"
  | "subflow"
  | "jump"
  | "agent"
  | "deep_agent"
  | "helper"
  | "knowledge_search"
  | "memory"
  | "sql_query"
  | "mcp_tool";

export interface DataField {
  name: string;
  type: FieldType;
  update: UpdateRule;
  description: string;
  combine?: string;
}

/** How a step runs: retries, time limit, cache and waiting for parallel branches. */
export interface RunPolicy {
  retries?: number;
  retry_wait?: number;
  timeout?: number | null;
  cache?: boolean;
  cache_ttl?: number | null;
  wait_for_all?: boolean;
}

export interface Step {
  id: string;
  type: StepType;
  name: string;
  description: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  settings: Record<string, any>;
  run?: RunPolicy;
}

export interface FlowSettings {
  max_steps?: number;
  max_parallel?: number | null;
  double_texting?: "reject" | "queue" | "interrupt" | "rollback";
  max_concurrent_runs?: number | null;
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
  settings?: FlowSettings;
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
  installed?: boolean;
  install?: string | null;
  credentials?: string | null;
  settings?: { env: string; label: string; set: boolean }[];
  models: ModelOption[];
}

export interface EmbeddingModelInfo {
  id: string;
  label: string;
  dims: number;
  provider: string | null;
}

export interface Catalog {
  categories: { id: string; label: string }[];
  steps: StepTypeInfo[];
  providers: ProviderInfo[];
  embedding_models?: EmbeddingModelInfo[];
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
  private?: boolean;
}

export interface Analysis {
  chat: boolean;
  fields: FieldInfo[];
  reads: Record<string, string[]>;
  writes: Record<string, Record<string, string>>;
  reachable: string[];
  upstream: Record<string, string | null>;
  exits: Record<string, string[]>;
  foreach_body?: Record<string, string>;
  /** Steps used as an Agent's tools -> that Agent. */
  tool_of?: Record<string, string>;
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

export type RunStatus = "queued" | "running" | "paused" | "ok" | "error" | "cancelled";

/** An Ask a Human step waiting for an answer. */
export interface Waiting {
  id: string;
  step: string | null;
  path: string[];
  request: AskRequest;
}

export interface AskRequest {
  step?: string;
  kind: "approve" | "edit" | "answer" | "choose" | "approve_tool";
  question: string;
  show?: Record<string, unknown>;
  field?: string;
  value?: unknown;
  options?: string[];
  /** approve_tool: the tool calls an agent wants to make. */
  actions?: { tool: string; args: Record<string, unknown> }[];
  allowed?: string[];
}

/** A Deep Agent's tool events: which Helper made the call (and inside which task), or which
 * Helper a `task` call hands work to. */
export interface HelperInfo {
  helper?: string;
  within?: string | null;
  to_helper?: string;
}

/** One item of a Deep Agent's to-do list. */
export interface Todo {
  content: string;
  status: "pending" | "in_progress" | "completed";
}

interface EventBase {
  run_id: string;
  ts: number;
  event_id?: number;
  /** Sub-flow steps leading to the step this event is about. */
  path?: string[];
}

export type RunEvent = EventBase &
  (
    | { type: "run_queued"; thread_id?: string; action?: string }
    | { type: "run_started"; thread_id: string; flow: string; stand_in: boolean; action?: string }
    | { type: "step_started"; step: string; input: Record<string, unknown>; item?: number }
    | { type: "token"; step: string; text: string }
    | {
        type: "step_finished";
        step: string;
        output: Record<string, unknown>;
        duration_ms: number;
        usage?: Usage;
        cost?: number | null;
        model?: string | null;
        item?: number;
      }
    | { type: "route"; step: string; exit: string }
    | ({ type: "tool_started"; step: string; tool: string; args: Record<string, unknown>; call_id?: string | null } & HelperInfo)
    | ({
        type: "tool_finished";
        step: string;
        tool: string | null;
        call_id?: string | null;
        result: unknown;
        status: string;
        duration_ms: number;
        /** A Deep Agent's task: the Helper's tokens and cost. */
        usage?: Usage;
        cost?: number | null;
        model?: string | null;
      } & HelperInfo)
    | ({ type: "todos"; step: string; todos: Todo[] } & HelperInfo)
    | ({ type: "files"; step: string; files: Record<string, string | null> } & HelperInfo)
    | { type: "progress"; step: string; done: number; total: number }
    | { type: "step_paused"; step: string; interrupt_id: string; request: AskRequest }
    | { type: "save_point"; checkpoint_id: string; next: string[]; step_number: number | null }
    | { type: "step_failed"; step: string; error: RunError }
    | { type: "paused"; reason: "ask_human" | "breakpoint"; interrupts: Waiting[]; next: string[] }
    | { type: "notified"; inbox_id: string; results: { channel: string; ok: boolean; error?: string }[] }
    | { type: "custom"; data: unknown; step?: string }
    | {
        type: "run_finished";
        status: Exclude<RunStatus, "queued" | "running">;
        duration_ms?: number;
        output?: Record<string, unknown>;
        reply?: string | null;
        usage?: Usage;
        cost?: number | null;
        error?: RunError;
        issues?: Issue[];
        step?: string | null;
        thread_id?: string;
        checkpoint_id?: string | null;
        reason?: "ask_human" | "breakpoint";
        interrupts?: Waiting[];
        next?: string[];
      }
  );

export interface RunSummary {
  run_id: string;
  flow_id: string | null;
  flow: string;
  version_id?: string;
  thread_id?: string;
  status: RunStatus;
  trigger?: string;
  parent_run_id?: string | null;
  inputs: Record<string, unknown>;
  output?: Record<string, unknown> | null;
  error?: RunError | null;
  pending?: { reason: string; interrupts: Waiting[]; next: string[] } | null;
  started: number;
  finished?: number | null;
  duration_ms?: number | null;
  cost?: number | null;
  usage?: Usage | null;
}

export interface SavePoint {
  checkpoint_id: string;
  parent_id: string | null;
  run_id: string | null;
  step_number: number | null;
  source: string | null;
  next: string[];
  created_at: string;
  values: Record<string, unknown>;
  waiting: AskRequest[];
}

export interface InboxItem {
  id: string;
  run_id: string;
  thread_id: string;
  flow_id: string | null;
  flow_name: string;
  step: string;
  step_name?: string;
  path: string[];
  interrupt_id: string;
  request: AskRequest;
  status: "open" | "answered" | "cancelled";
  answer?: unknown;
  answered_by?: string | null;
  created_at: number;
  answered_at?: number | null;
}

export type TriggerKind = "webhook" | "schedule" | "upload" | "after_flow" | "email";

export interface Trigger {
  id: string;
  flow_id: string;
  kind: TriggerKind;
  name: string;
  config: Record<string, unknown>;
  enabled: boolean;
  token: string;
  url?: string;
  describe?: string;
  next_fire_at?: number | null;
  last_fired_at?: number | null;
  last_run_id?: string | null;
  created_at: number;
}

export interface NotificationSettings {
  webhook: { enabled: boolean; url: string };
  slack: { enabled: boolean; webhook_url: string };
  email: {
    enabled: boolean;
    smtp_host: string;
    smtp_port: number;
    starttls: boolean;
    username: string;
    password: string;
    sender: string;
    to: string[];
  };
  public_url: string;
}

export interface FlowVersion {
  id: string;
  flow_id: string;
  name: string;
  note: string;
  created_at: number;
}

export interface ThreadInfo {
  thread_id: string;
  flow_id: string | null;
  runs: number;
  last_at: number;
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
  /** A multi-agent pattern the canvas can insert into a flow (supervisor, handoffs, plan-and-execute). */
  pattern?: string;
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

// ── knowledge ─────────────────────────────────────────────────────────────

export interface KnowledgeDocument {
  id: string;
  kb_id: string;
  title: string;
  source: string;
  kind: string;
  status: "queued" | "processing" | "ready" | "error";
  error?: string | null;
  chunks: number;
  chars: number;
  created_at: number;
  updated_at: number;
}

export interface KnowledgeBase {
  id: string;
  name: string;
  description: string;
  embedding_model: string;
  embedding_label?: string;
  dims: number;
  chunk_size: number;
  chunk_overlap: number;
  created_at: number;
  updated_at: number;
  documents?: KnowledgeDocument[] | number;
  chunks?: number;
}

export interface KnowledgeHit {
  n: number;
  id: string;
  doc_id?: string;
  text: string;
  title?: string | null;
  source?: string | null;
  page?: number | null;
  heading?: string | null;
  score?: number;
  similarity?: number;
  matched?: "both" | "meaning" | "words";
}

export interface ChunkPreview {
  title: string;
  kind: string;
  count: number;
  chars: number;
  chunks: { position: number; text: string; page: number | null; heading: string | null }[];
}

// ── MCP and OpenAPI ───────────────────────────────────────────────────────

export interface McpServer {
  id: string;
  name: string;
  transport: "http" | "sse" | "stdio";
  url: string;
  headers: Record<string, string>;
  command: string;
  args: string[];
  env: Record<string, string>;
}

export interface McpSettings {
  servers: McpServer[];
  allowed_commands: string[];
}

export interface McpTool {
  name: string;
  description: string;
  args: Record<string, { type?: string; description?: string; title?: string }>;
}

export interface OpenApiParam {
  name: string;
  in: "path" | "query" | "header" | "body";
  required: boolean;
  type: FieldType;
  description: string;
}

export interface OpenApiOperation {
  id: string;
  method: string;
  path: string;
  summary: string;
  description: string;
  params: OpenApiParam[];
  body: OpenApiParam[];
}

export interface OpenApiInspection {
  title: string;
  server: string;
  operations: OpenApiOperation[];
}

/** How Code steps are sandboxed (Settings → Sandbox). */
export interface SandboxSettings {
  mode: "auto" | "docker" | "off";
  image: string;
  runtime: "auto" | "runc" | "runsc";
  memory_mb: number;
  cpus: number;
  pids: number;
  timeout: number;
}

export interface SandboxStatus {
  available: boolean;
  reason: string | null;
  version: string | null;
  gvisor: boolean;
  mode: SandboxSettings["mode"];
  /** "docker": Code steps run in the sandbox; "off": in Easy Chain's process; "unavailable": required but no Docker. */
  effective: "docker" | "off" | "unavailable";
}

/** A skill in the Skills library (Agent Skills format: a SKILL.md and the files it uses). */
export interface SkillSummary {
  name: string;
  description: string;
  files: string[];
  built_in: boolean;
  /** A workspace skill named like a built-in one, which it replaces. */
  replaces_built_in: boolean;
}

export interface Skill extends Omit<SkillSummary, "files"> {
  files: Record<string, string>;
}

// ── people (Phase 5.6) ───────────────────────────────────────────────────────

export type Role = "viewer" | "runner" | "editor" | "owner";

/** Who is signed in. With sign-in off, everyone is the Owner (via "local"). */
export interface Me {
  id: string | null;
  email: string | null;
  name: string;
  role: Role;
  role_label: string;
  via: "local" | "session" | "key";
  workspace: string;
  /** An Owner of the default workspace: changes what every workspace shares, and makes workspaces. */
  server_owner?: boolean;
}

/** A workspace someone can switch to (server/auth_api.py); role is null where they aren't in it. */
export interface WorkspaceInfo {
  id: string;
  name: string;
  people: number;
  created_at: number;
  role: Role | null;
  role_label: string | null;
  current?: boolean;
}

export interface AuthStatus {
  mode: "on" | "off";
  signed_in: boolean;
  /** Sign-in is on and nobody has an account yet: the first one is made with the code from the server's log. */
  setup_needed: boolean;
  me: Me | null;
  roles: { id: Role; label: string }[];
  /** Single sign-on providers to offer on the sign-in page. */
  sso?: { id: string; name: string }[];
}

/** One single sign-on provider (Settings → Single sign-on, server/sso.py). */
export interface SsoProvider {
  id: string;
  name: string;
  issuer: string;
  client_id: string;
  /** "{secret:NAME}", or a new secret to keep in the vault. */
  client_secret: string;
  new_people: "none" | Role;
  domains: string[];
  scopes?: string;
  /** The address to give the provider ("redirect URI"). */
  callback?: string;
}

export interface SsoSettings {
  providers: SsoProvider[];
  source: "settings" | "environment";
}

export interface Person {
  id: string;
  email: string;
  name: string;
  role: Role;
  role_label: string;
  disabled: boolean;
  created_at: number;
  last_login_at: number | null;
}

export interface ApiKeyInfo {
  id: string;
  user_id: string;
  name: string;
  role: Role;
  created_at: number;
  last_used_at: number | null;
  mine: boolean;
}

/** Settings → Network: which addresses Easy Chain may reach (egress.py). */
export interface NetworkSettings {
  mode: "open" | "public" | "allow-list";
  allow: string[];
  /** "environment": EASYCHAIN_EGRESS sets it, so it can't be changed in the app. */
  source: "settings" | "environment" | "default";
  modes?: string[];
}

/** One entry of the audit log (server/audit.py): who changed what, and when. */
export interface AuditEntry {
  id: number;
  at: number;
  actor: string;
  actor_id: string | null;
  via: "session" | "key" | "local" | "trigger" | "cli" | "none";
  action: string;
  target: string | null;
  detail: Record<string, unknown>;
  address: string | null;
  /** "Ann saved flow support-bot" */
  text: string;
}

export interface AuditPage {
  entries: AuditEntry[];
  /** Pass as `before` for older entries; null when there are none. */
  next: number | null;
  kinds: { id: string; label: string }[];
}

/** A key just made: `key` is shown this once. */
export interface NewApiKey {
  id: string;
  name: string;
  role: Role;
  key: string;
  created_at: number;
}
