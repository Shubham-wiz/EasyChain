# The dashboard

The **Dashboard** (on the home page, or `#/dashboard`) shows how a workspace's flows are
doing over time. Everyone who can see runs can see it, Viewers included.

## Filters

The filters above the dashboard apply to everything below them:

- the **period**: the last 7, 30 or 90 days;
- one **flow**, or every flow;
- one **environment**, or every environment. **Drafts** means runs of flows that aren't
  [published](publish.md).

Days are in UTC, and every day of the period is shown, even a day with no runs.

## What it shows

**Headline numbers:**

| Number | What it is |
|---|---|
| **Runs** | how many runs started in the period, and how many are waiting for someone |
| **Failed** | the share of finished runs that ended with an error |
| **Time to finish (p95)** | 95% of finished runs took at most this long. The note gives the median (p50) |
| **Tokens** | tokens sent to and received from AI models, from the [model gateway](gateway.md) |
| **Cost** | what those model calls cost, from the same record as the spending page |

**Charts, one point per day:**

- **Runs a day.** Failed runs are in red; the rest (succeeded, waiting, stopped) are in gray.
- **Time to finish.** The p50 and p95 lines.
- **Tokens a day.**
- **Cost a day.**

To read a day, point at a chart, or focus it and use the arrow keys.

**Tables:**

- **Flows**: each flow's runs, failure rate, p95 time and cost.
- **Common errors**: the failures that happened most often, by flow and message, with when
  each last happened.
- **Each day, as a table**: every number in the charts.

**Where the numbers come from.**

- Runs, failures and times come from the runs themselves. A run that is waiting for someone
  counts as a run, but not as finished.
- Tokens and cost come from what the [model gateway](gateway.md) recorded for runs, so they
  match the spending page.
- Kept Test Runs, Test Sets run in the Tests tab and the copilot are not runs, so they are
  not on the dashboard. The spending page shows their cost.

## The API

```
GET /api/dashboard?days=7|30|90&flow_id=…&environment=drafts|dev|staging|production   (Viewers)
→ {days, since, totals, series: [{day, runs, ok, errors, paused, cancelled, running,
                                  p50_ms, p95_ms, input_tokens, output_tokens, cost}],
   flows: [...], errors: [...]}
```

To send runs to your own monitoring, see [tracing](tracing.md).
