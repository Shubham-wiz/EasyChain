# Environments and Publish

A flow is drawn and tried as a **draft**. Publishing gives one saved version of it a stable
address, so other apps can call it and people can chat with it, while you keep changing the
draft.

## Environments

Every workspace has three environments:

| Environment | A version goes there when… | Who publishes |
|---|---|---|
| **Development** (`dev`) | it is saved | Editors and Owners |
| **Staging** (`staging`) | a kept [Test Run](testing.md#test-runs-and-baselines) of that version, run **in Staging**, passed every case | Editors and Owners |
| **Production** (`production`) | it is the version in Staging | Owners only |

So nothing reaches production without passing its Test Set in staging first, and an Editor
can't put it there. Every publish, and every key made, is in the [audit log](audit.md).

Each environment can have, in **Settings → Environments** (Owners change them; Editors see
them):

- **Secrets**, laid over the workspace's. A secret set for Production wins there over the
  workspace's secret of the same name, so production can use another account's key.
- **Model swaps**: for example `openai:gpt-4.1` → `openai:gpt-4.1-mini` in Development, to
  spend less.
- **Knowledge Base swaps**: for example `help_docs` → `help_docs_live` in Production. A swap
  must name a Knowledge Base of the workspace.

Runs of a published flow use their environment's secrets and swaps. So does a Test Run
started in an environment, which is how a version is checked before it's promoted. Drafts
run exactly as drawn, with the workspace's secrets.

## Publishing

Open a flow and press **Publish**. For each environment, the dialog shows what is published
there now, and a button to publish:

- **Publish to Development**: the flow as it is saved now.
- **Publish to Staging**: the same, when a Test Run of this version passed in Staging. If
  there's none, the dialog says so and offers **Open Tests**. In the Tests tab, under **Kept
  Test Runs**, choose **In Staging** and press **Run and keep**.
- **Publish what's in Staging** (Owners): makes the Staging version the Production one.

**Stop** takes a flow out of an environment: its keys stop working there until it is
published again. The dialog's **History** lists every publish.

A version is the flow exactly as it was saved (Sub-flows included). Publishing pins it, so
later edits don't change what is published until you publish again.

## Access keys: other apps

Under **Access**, make an **access key** for one environment. It is shown once:

```bash
curl -X POST https://easychain.example.com/api/published/support-bot/production \
  -H "Authorization: Bearer epk_…" -H "Content-Type: application/json" \
  -d '{"inputs": {"question": "How long do refunds take?"}}'
```

```json
{"run_id": "…", "thread_id": "…", "status": "ok", "output": {"answer": "…"}}
```

- `thread_id` continues a conversation (chat flows remember it).
- `wait` (seconds, up to 120, 60 by default) is how long the call waits for the answer. A run
  still going comes back with `"status": "running"`; ask again with
  `GET /api/published/{flow}/{environment}/runs/{run_id}` and the same key.
- A flow that waits for someone answers `"status": "paused"`, and the item is in the Inbox.
- Each key has a **limit of requests a minute**. Over it, the answer is `429` with
  `Retry-After`. Every API server counts in the database, so the limit holds however many
  there are.
- A key works for its own flow and environment, and nothing else.
- A key belongs to the workspace it was made in. Revoke it under Access.
- Keys for Production are made and revoked by Owners.

From JavaScript (on a server, not a web page):

```ts
const easy = new EasyChainClient({ baseUrl, apiKey: process.env.SUPPORT_BOT_KEY });
const { output } = await easy.runPublished("support-bot", "production", { question });
```

Each run of a published flow is in the flow's runs, marked with its environment and the key
that started it (trigger `api` or `chat`).

## Chat links: people

A **chat link** gives a published chat flow a hosted chat page, `/chat/<link>`, and a widget
for your own site:

```html
<script src="https://easychain.example.com/widget.js" data-chat="LINK"></script>
```

The widget adds a chat button in the corner, which opens the chat page in a frame. Optional
attributes:

- `data-button`: the button's text;
- `data-color`: its colour;
- `data-title`: the frame's title.

- A chat link has no secret, so it can be in a web page. List the **sites that may show the
  widget**; the chat page tells browsers not to show it on other sites (`frame-ancestors`).
- Its limit of requests a minute counts for the link, and (up to 20 a minute) for each
  visitor's address.
- The page keeps the conversation for the browser tab.
- A message is sent to the flow as `{"messages": [{"role": "user", "content": …}]}`, the
  input of chat flows. The page shows the last message the flow answers with.

## Self-hosting: `easychain deploy`

```bash
easychain deploy compose ./shop-bundle --flows ./flows   # a folder for `docker compose up -d`
easychain deploy image shop-flows:1 --flows ./flows      # or an image, built with Docker
```

A **Compose bundle** has:

- a `Dockerfile` that puts the flows (with their Test Sets, baselines and skills) into the
  Easy Chain image;
- a `docker-compose.yml` with Easy Chain, a worker and Postgres;
- a `.env.example`;
- a README with the steps.

Runs, secrets and people live in the volumes, so rebuilding with new flows keeps them.

Both build on the Easy Chain image `easychain:<version>`. Build it from Easy Chain's source
first: `docker build -t easychain:0.5.0 .`.

## Publishing from CI: `easychain publish`

```bash
easychain publish support-bot --to staging --server https://easychain.example.com --key "$EASYCHAIN_KEY"
easychain publish support-bot --to production --server https://easychain.example.com --key "$OWNER_KEY"
```

`--to staging` starts a kept Test Run in Staging on the server, waits for it, and publishes
only when every case passed. Otherwise it lists the failing cases and exits with 1.
`--to production` publishes what is in Staging, so it needs an Owner's key.

The key is a key for scripts (`eck_…`, Settings → Keys for scripts) with the role needed. Other
options:

- `--workspace` (a key works in its own workspace);
- `--repeat`;
- `--var NAME=VALUE`;
- `--timeout`.

## The API

```
GET    /api/environments                               (Editors)
PUT    /api/environments/{env}                         (Owners)   ← {models: {...}, knowledge: {...}}
PUT    /api/environments/{env}/secrets/{name}          (Owners)   ← {value}
DELETE /api/environments/{env}/secrets/{name}          (Owners)
GET    /api/flows/{id}/publish                         (Viewers)  → {environments, history, keys}
POST   /api/flows/{id}/publish                         (Editors; production: Owners) ← {environment, version_id?, test_run_id?}
DELETE /api/flows/{id}/publish/{env}                   (Editors; production: Owners)
POST   /api/flows/{id}/keys                            (Editors; production: Owners) ← {environment, kind: secret|chat, name, per_minute, origins}
DELETE /api/flows/{id}/keys/{key_id}
POST   /api/published/{id}/{env}                       (the access key)  ← {inputs, thread_id?, wait?}
GET    /api/published/{id}/{env}/runs/{run_id}         (the access key)
GET    /api/chat/{link}, POST /api/chat/{link}         (the chat link)   ← {message, thread_id?}
GET    /chat/{link}, GET /widget.js
POST   /api/flows/{id}/test-runs                       ← … environment?
```

A refused publish is a `409` that says what's missing (`"action": "test_run"` or
`"staging"`); a missing role is a `403`.
