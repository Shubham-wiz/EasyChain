# Tracing: OpenTelemetry and LangSmith

Easy Chain can send each run of a workspace to your tracing tools. You set this up in
**Settings → Tracing**, for each workspace. Editors can see the settings; Owners change them,
and each change is in the [audit log](audit.md). For trends across days, use the
[dashboard](dashboard.md).

## OpenTelemetry

Turn on **OpenTelemetry**, then give:

- the **collector's address**, for OTLP over HTTP, such as `http://collector:4318`
  (`/v1/traces` is added for you);
- **headers**, such as an API key. A value can be `{secret:NAME}`, so the key stays in
  Secrets. Saved values are shown hidden;
- a **service name** (`easychain` by default).

**Send a test span** sends one span and says whether the collector took it.

This works with any OpenTelemetry collector, and with Jaeger, Grafana Tempo, Honeycomb,
Datadog and others that take OTLP.

**What is sent.** Each run is one trace:

| Span | Under | Attributes |
|---|---|---|
| `run <flow>` | (the trace's root) | `easychain.run_id`, `.flow_id`, `.flow`, `.environment`, `.trigger`, `.thread_id`, `.status`, `.cost`; `gen_ai.usage.input_tokens` / `output_tokens` |
| `step <id>` | the run, or the Sub-flow step that runs it | `easychain.step`, `.item` (For Each), `.duration_ms`, `.cost`, `.paused` (Ask a Human); `gen_ai.request.model`, tokens |
| `tool <name>` | its Agent step | `gen_ai.tool.name`, `easychain.step` |
| `chat <model>` | its step | `gen_ai.operation.name` = `chat`, `gen_ai.system` (the provider), `gen_ai.request.model`, `gen_ai.usage.input_tokens` / `output_tokens`, `easychain.cost`, `easychain.cached` |

- A step, tool call or model call that failed has an error status with the message.
- A run that waits for someone is sent in parts: up to the pause, then after the answer.
  Every part has the same trace id (it comes from the run id), so the run is still one
  trace.
- Spans are built from the run's events once each part has ended. Tracing never slows a run
  down, and a collector that is down doesn't break runs.
- Model calls come from the [model gateway](gateway.md)'s records.

## LangSmith

1. Add your LangSmith API key as the secret **`LANGSMITH_API_KEY`** in Settings → Keys and
   providers. Each workspace has its own secrets, so each can use its own key.
2. Turn on **LangSmith** in Settings → Tracing.
3. Pick a **project** (`easychain` by default). Change the **address** for a self-hosted
   LangSmith or another region.

Each run then appears in that LangSmith project, traced by LangChain itself: the run, each
step, and every model and tool call. Its metadata includes:

- `easychain_run_id`;
- `easychain_flow_id`;
- `easychain_environment`;
- `easychain_workspace`.

## Exported code

Exported code has no Easy Chain server, so these settings don't reach it. To trace it with
LangSmith, use LangChain's own environment variables:

```bash
export LANGSMITH_TRACING=true
export LANGSMITH_API_KEY=lsv2_…
export LANGSMITH_PROJECT=my-flow
python main.py
```

For OpenTelemetry, use any OpenTelemetry instrumentation for LangChain in your own app.

## The API

```
GET  /api/settings/tracing        (Editors)  → {otlp: {enabled, endpoint, headers, service_name},
                                               langsmith: {enabled, project, endpoint}, langsmith_key}
PUT  /api/settings/tracing        (Owners)   ← the same, without langsmith_key
POST /api/settings/tracing/test   (Owners)   → {ok, error?}
```
