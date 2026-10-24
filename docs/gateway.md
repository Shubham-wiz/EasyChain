# The model gateway

Every model call a flow makes on an Easy Chain server goes through its **model gateway**. The
gateway records what each call cost, and it can:

- keep spending inside **budgets**, with alerts;
- **cache** answers;
- **fall back** to other models when a provider fails;
- **retry** failed calls;
- **smooth** bursts of calls.

Its settings are per workspace, in **Settings → Model gateway**. Editors see them. Owners
change them, and every change is in the [audit log](audit.md).

Exported code doesn't have the gateway. It calls the providers directly, with LangChain's own
`init_chat_model`.

## What model calls cost

The top of the page adds up the cost of model calls. You can group the total by:

- **flow**;
- **person**;
- **environment**;
- **model**;
- **run**.

Pick the period: today, the last 7 or 30 days, or this month. Each row shows its calls,
input and output tokens, and cost.

**What counts as one call.** Every call is counted once. That covers:

- runs, whether started from the editor, by triggers, or through
  [published flows](publish.md);
- kept Test Runs (shown as *flow · Test Runs*);
- Test Sets run in the Tests tab (*flow · Tests tab*);
- the AI judge;
- the copilot.

**Who is shown as the person.** A run shows the person who started it. A published flow's run
shows its access key or chat link. A run started by a trigger or a schedule shows as
*Triggers and schedules*.

**How the cost is worked out.** It uses the provider's list prices, which Easy Chain knows for
the models it lists. Under **Prices** you can set your own price, in dollars per million input
and output tokens. Use it for a model Easy Chain doesn't know (a fine-tuned one, say), or for
the price you actually pay. A call with no known price shows **+?**. Its cost is unknown, not
made up, and it doesn't count toward budgets.

**Calls that cost nothing.** Calls to the stand-in AI are free and not recorded. Answers from
the cache cost nothing and use no tokens.

## Budgets and alerts

A budget limits what model calls may cost **a day or a month** (in UTC). You can set one for:

| Budget | Counts |
|---|---|
| **Workspace** | every call in the workspace |
| **Drafts** | runs of flows that aren't published, the Tests tab, Test Runs not in an environment, the copilot |
| **Development**, **Staging**, **Production** | runs of flows published there, and Test Runs started in them |

A call counts toward the Workspace budget and toward its own scope's budget.

**Alerts.** An alert is sent when a budget reaches its **alert at** share (80% by default),
and again when it's used up. Alerts go to the channels set in
[Notifications](runs.md#ask-a-human-and-the-inbox): a webhook (`"event": "budget"`), Slack or email.
Each one is sent once per budget, period and level, however many servers there are.

**Stopping calls.** With **stop model calls when it's used up**, a budget that is used up
stops new model calls until the next day or month. The run ends with an error:

> The Production budget is used up: $50.20 of $50.00 this month. Model calls stop until
> 1 November (UTC).

The error has a button to these settings. A budget without **stop** only sends alerts.

**When the check happens.** The gateway checks budgets before each call, against what every
process has recorded. A call already under way when the budget runs out still finishes. So
parallel steps, or several workers, can go a little over the limit. The amount over is at
most what those calls cost.

## Caching answers

Answers can be cached for drafts and for each environment, for a number of hours (24 by
default). A call gets the cached answer when it has the same model, the same settings (tools
and structured output included) and the same messages as an earlier call. The provider
isn't asked, so the call is free.

Answers are kept in the run database and shared by every server process of the workspace.
Another workspace never sees them. **Forget cached answers** empties the cache.

Caching suits drafts, Test Sets and repeated batch work. Real conversations rarely repeat
exactly. The copilot isn't cached.

## Fallbacks, retries and smoothing

**Fallbacks.** For any model, list other models to try in order when its provider fails or
can't be reached, for example `openai:gpt-4.1` → `anthropic:claude-sonnet-4-5`.

- A fallback is tried when the provider answers with an error (rate limits, overload, outages,
  bad requests) or can't be reached.
- It is not tried for Easy Chain's own errors: a budget used up, or a reply that doesn't fit
  its structured output.
- The step's temperature, maximum tokens, top-p and timeout carry over to the fallback. Tools
  and structured output are set up on each model.
- Each call is recorded as the model that made it. The failed one shows as failed.
- A fallback whose provider has no key on this server is skipped.

Agents also have their own per-step **fallback models** add-on (see [agents](agents.md)).

**Retries.** This sets how many times a failed call is tried again, with growing waits in
between. Leave it blank to keep the provider package's own default (two for OpenAI and
Anthropic). Retries happen before fallbacks.

**Smoothing.** This caps the requests a second sent to a provider. A burst then waits its turn
instead of hitting the provider's rate limit. The cap applies to each server process: with
several workers, each one has its own.

## The API

```
GET    /api/gateway             (Editors)  → {settings, budgets: [{scope, budget, spent, period, …}], alerts}
PUT    /api/gateway             (Owners)   ← {budgets, cache, fallbacks, retries, per_second, prices}
DELETE /api/gateway/cache       (Owners)   → {forgotten}
GET    /api/spending?period=today|7d|30d|month&by=flow|person|environment|model|run   (Editors)
```

The settings:

```json
{
  "budgets": {"production": {"limit": 50, "period": "month", "alert_at": 0.8, "stop": true}},
  "cache": {"scopes": ["drafts", "dev"], "hours": 24},
  "fallbacks": {"openai:gpt-4.1": ["anthropic:claude-sonnet-4-5"]},
  "retries": 3,
  "per_second": {"openai": 5},
  "prices": {"openai:ft:gpt-4.1:acme": {"input": 3, "output": 12}}
}
```

A run's summary (`GET /api/runs/{id}`) says who started it (`started_by`). Each call is a row
in the `model_calls` table, with:

- its run, flow, environment and person;
- its step and model;
- its tokens and cost;
- whether it was cached;
- its error, if any.
