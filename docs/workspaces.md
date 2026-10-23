# Workspaces

One Easy Chain server can hold several **workspaces**, for different teams, clients or
projects. Each workspace keeps its own:

- flows, their versions and Test Sets;
- runs, conversations and Save Points;
- the Inbox;
- triggers (webhooks, schedules, uploads, mailboxes, after another flow);
- Knowledge Bases;
- skills;
- secrets and API keys (its own vault);
- MCP servers, notifications and the copilot's model;
- long-term memory;
- people, each with a role in it;
- audit log.

People in one workspace can't see or change another's. A webhook or a schedule runs its flow
in the trigger's workspace, with that workspace's secrets.

Some things apply to **every workspace**, and only the server's Owners change them:

- the [sandbox](sandbox.md) for Code steps;
- the [network](network.md) Easy Chain may reach;
- [single sign-on](sso.md);
- which commands MCP servers may run on the server.

The **server's Owners** are the Owners of the default workspace. With sign-in off, the one
person using Easy Chain is the server's Owner.

## The default workspace

Every server has one workspace from the start, the **Default workspace** (its id is
`default`). Everything made before workspaces existed is in it, and its folders are where they
always were: flows in `~/.easychain/flows` (or `EASYCHAIN_WORKSPACE`), secrets in
`~/.easychain/secrets.enc`.

Other workspaces keep their files under `~/.easychain/workspaces/<id>/`: flows and skills in
`flows/`, secrets in `secrets.enc`. Every vault is encrypted with the server's one key.
Runs, the Inbox and triggers are rows in the run database with a `workspace_id`.

## Making a workspace

In the app, a server Owner opens **Settings → Workspaces**, gives the new workspace a name and
presses **Make it**. Whoever makes it is its first Owner. Its id comes from the name ("Sales
team" becomes `sales-team`) and doesn't change; the name can.

From the command line:

```bash
easychain workspaces create --name "Sales team" --owner ann@example.com
easychain workspaces list
easychain workspaces rename sales-team --name "Sales"
```

## Switching

When you're in more than one workspace, the workspace menu at the top of the home page lists
them with your role in each. Picking one starts the app afresh in it.

Each browser tab remembers its own workspace, so two tabs can work in two workspaces. A new
tab starts in the one you used last.

## People

Each person has one account, and can be in several workspaces with a role in each. Their
password, name and sign-in are the account's. Their role is the workspace's.

- **Settings → People** shows the people in the workspace you're in.
- Adding someone who already has an account puts them in this workspace too. They keep their
  password, so you don't set one.
- Removing someone takes them out of this workspace. Their account goes when they're in no
  workspace.
- An Owner of a workspace can change roles there. Changing an account itself (switching it
  off, a new password, the name) affects every workspace it is in. So when the person is in
  other workspaces too, only the server's Owners can do it.

People who sign in for the first time with [single sign-on](sso.md) join the default
workspace.

From the command line, `easychain users` and `easychain keys` take `--workspace`:

```bash
easychain users add bea@example.com --role editor --workspace sales-team
easychain users list --workspace sales-team
easychain keys create bea@example.com --role runner --workspace sales-team
```

## Keys for scripts

A key belongs to the workspace it was made in, and only works there.
[`@easychain/client`](../packages/client/README.md) picks a workspace for a signed-in browser
with its `workspace` option.

## The API

Every request names its workspace in a header:

```
X-EasyChain-Workspace: sales-team
```

WebSockets can't send headers, so they use `?workspace=sales-team`. Without either, the
request is in the default workspace, or in the first workspace the person is in. Naming a
workspace you aren't in gets a `403` with `"action": "workspace"`. Routes that need no sign-in
(the sign-in page, webhooks) ignore it.

```
GET   /api/workspaces                  (anyone signed in) → {workspaces: [{id, name, people, role, …}], current}
POST  /api/workspaces                  (server Owners)    ← {name, id?}
PATCH /api/workspaces/{workspace_id}   (server Owners)    ← {name}
GET   /api/auth/me                     → … "workspace", "server_owner", "workspaces"
```

Rows of another workspace look as if they aren't there. Asking for its run, Inbox item,
trigger, version or Knowledge Base by id gets a `404`, and a conversation id that is in use in
another workspace can't be continued here.

## Knowledge Bases and memory

- **Knowledge Bases.** A flow searches only its own workspace's Knowledge Bases. A Knowledge
  Base search step that names another workspace's base fails with "There's no Knowledge Base …
  in this workspace". Checks warn about it before the run. Knowledge Base ids are unique across
  the server.
  - The sample help centre that the Support bot template searches is shared by every
    workspace. Only the default workspace can change it.
- **Long-term memory.** A flow's memories (and its records of side effects already done) are
  kept per workspace. In every workspace except the default one, Easy Chain puts their
  namespaces under `("easychain-workspace", <id>)`. Flows still see the namespaces they wrote.
  The default workspace's namespaces are as they always were.

## What workspaces don't do (yet)

- A workspace can't be deleted, from the app or the command line.
- A server Owner sees every workspace in Settings, but can only switch into the ones they're
  in. To step into another, add yourself with
  `easychain users add YOU --role owner --workspace ID`.
- Workspaces keep data apart inside Easy Chain. A Code step that runs **without** the
  [sandbox](sandbox.md) runs in the server's own process, so it could read another workspace's
  files. On a server shared by teams that don't trust each other, set the sandbox to
  **required**.
- Exported code, `easychain run` and `easychain test` use the default workspace's secrets.

These are in the README's "Fix later" list.
