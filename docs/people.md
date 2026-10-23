# People, sign-in and keys

Easy Chain can be used by one person on their own computer, or by a team on a shared server.
On a shared server, people **sign in**, each with a **role**, and scripts and other apps use
**keys**.

## When sign-in is on

Sign-in is on or off for the whole server.

| How Easy Chain is started | Sign-in |
|---|---|
| `easychain dev` (it answers only this computer, on 127.0.0.1) | **off**: everyone who can reach it is the Owner, as before |
| The Docker image, or `easychain dev --host 0.0.0.0` (it answers the network) | **on** |
| `EASYCHAIN_AUTH=on` or `EASYCHAIN_AUTH=off` | as you say |

Turning sign-in off on a server that answers the network takes `EASYCHAIN_AUTH=off`, said out
loud. Only do that behind your own access control (a VPN, or a proxy that signs people in).

People can also sign in with your company's identity provider: see
[single sign-on](sso.md).

## The first account

A server with sign-in on and no accounts prints a code in its log when it starts:

```
Sign-in is on and there are no accounts yet. To make the first one (an Owner), open
http://localhost:8000 and enter the code ABCD-EFGH-JKLM.
```

Open Easy Chain and enter the code with your name, email and a password. That account is the
**Owner**. Only someone who can see the server's log can do this. With Docker Compose, the log
is `docker compose logs easychain`.

If the log line is gone, `easychain users setup-code` prints a new code. That replaces the
old one, and it only works while there are no accounts. A script can make the first Owner
directly instead:

```bash
echo "$OWNER_PASSWORD" | easychain users add you@example.com --role owner --password-stdin
```

## Roles

Each person has one role. Each role can do everything the roles above it in this table can.

| Role | Can |
|---|---|
| **Viewer** | see flows, runs, the Inbox, Knowledge Bases and skills |
| **Runner** | start and stop runs of saved flows, and answer the Inbox |
| **Editor** | change flows, Knowledge Bases, triggers and skills; run a flow that isn't saved; use the copilot and Import an API; see which secrets are set |
| **Owner** | change secrets and server settings (MCP servers, the sandbox, notifications); add people and change their roles |

The server checks the role on every request. Every part of the API declares the least role it
needs (`PERMISSIONS` in `python/src/easychain/server/auth.py`). A test fails if a new part has
no entry, and until it has one, it is closed to everyone.

Webhook triggers don't need a sign-in. Their secret token is the check, as before.

When you answer the Inbox while signed in, your name is recorded as who answered, whatever the
app or script says.

## What each role sees in the app

The app shows each person what their role can do. Anything their role can't do is either
hidden or greyed out, with a note that names the role it needs.

- **Viewer**
  - Flows open in a view-only editor: there is no step library or copilot, the canvas can't
    be changed, and the Inspector shows every setting but can't change it.
  - There is no Run button. The Run panel says why, and shows runs that others start.
  - Inbox items show what they're waiting for, but can't be answered.
- **Runner**: the same view-only editor, but with **Run**. Runs use the saved flow. Inbox
  items can be answered.
- **Editor**: everything to do with flows, Knowledge Bases, skills and triggers. Settings show
  the providers and keys that are set, the MCP servers, notifications and the sandbox, but only
  an Owner can change them.
- **Owner**: everything, including **Settings → People**.

On Home, Viewers and Runners see the flows but not **New blank flow**, the templates or delete.
In Settings, they see only **Keys for scripts**.

This is so the app makes sense to each person. The server checks every request on its own,
whatever the app shows.

## Adding people

An Owner adds people in **Settings → People** (or from the account menu, top right), with:

- their email and name;
- a role;
- a first password, which you tell them. They can change it from the account menu, under
  **Change password**.

The same page can:

- change someone's role;
- switch someone off (they are signed out at once, and their keys stop working until they are
  switched on again);
- set a new password for someone who forgot theirs;
- remove someone (their keys stop working for good).

The workspace always keeps at least one Owner. You can't demote, switch off or remove the last
one.

From the command line on the server:

```bash
easychain users list
easychain users add ann@example.com --role editor --name Ann     # asks for a password
easychain users role ann@example.com --role runner
easychain users disable ann@example.com                         # and enable
easychain users password ann@example.com                        # a new password
easychain users remove ann@example.com
```

These commands use the same database as the server (`--database-url`, or
`EASYCHAIN_DATABASE_URL`, or `~/.easychain/easychain.db`), so anyone who can run them is in
charge of the server.

## Keys for scripts and other apps

Make a key in **Settings → Keys for scripts**. Give it a name that says what it's for, and a
role: yours, or a lower one. The key is shown **once**, so copy it then. Send it with every
request:

```bash
curl -H "Authorization: Bearer eck_…" http://localhost:8000/api/flows
```

With `@easychain/client`:

```ts
const easy = new EasyChainClient({ baseUrl: "https://easychain.example.com", apiKey: process.env.EASYCHAIN_KEY });
```

- A key acts as its person, with at most the role it was made with. For an app that starts runs
  of saved flows and answers the Inbox, a **Runner** key is enough.
- If its person is switched off or removed, the key stops working.
- Keys can't make other keys.
- Revoke a key in the same place. An Owner sees everyone's keys and can revoke any of them.
- Keep keys on a server. In a web page, anyone can read them.
- WebSockets can't carry a key. A script follows a run with `GET /api/runs/{id}/events`
  (Server-Sent Events), or with the client's `follow()`.

From the command line: `easychain keys create ann@example.com --role runner --name "Support site"`
prints only the key, so a script can capture it. Also `easychain keys list [EMAIL]` and
`easychain keys revoke KEY_ID`.

## How sign-in is kept safe

- Passwords are stored as scrypt hashes, and must be at least 10 characters long.
- The browser gets a session cookie:
  - it is `HttpOnly` and `SameSite=Lax`, and also `Secure` when the server is reached over HTTPS
    (or a proxy says so with `X-Forwarded-Proto: https`);
  - it lasts 14 days;
  - the server keeps only a hash of it, and keys are stored the same way.
- Changing a password signs out that person's other browsers.
- After 5 wrong passwords for one email in 15 minutes, signing in to that email waits until the
  15 minutes are up. One address gets 30 wrong tries, and wrong first-account codes count too.
- The first-account code is 12 random letters and digits, and is shown only in the server's log.
- Put a shared server behind HTTPS (a reverse proxy such as Caddy or nginx), and set
  `EASYCHAIN_PUBLIC_URL` to its address.

## The API

| Endpoint | Who | What |
|---|---|---|
| `GET /api/auth/status` | anyone | `{mode, signed_in, setup_needed, me, roles}` |
| `POST /api/auth/setup` | anyone, with the code | `{code, email, name, password}`: the first Owner |
| `POST /api/auth/login` / `logout` | anyone | `{email, password}`; sets or clears the cookie |
| `GET /api/auth/me` | signed in | who you are: `{id, email, name, role, via}` (`via` is `session`, `key` or `local`) |
| `POST /api/auth/password` | signed in | `{current, new}` |
| `GET/POST /api/people`, `PATCH/DELETE /api/people/{id}` | Owner | people and their roles: `{email, name, role, password}`; `{role, disabled, name, password}` |
| `GET/POST /api/keys`, `DELETE /api/keys/{id}` | signed in | your keys (an Owner sees everyone's): `{name, role}` |

A request without a sign-in gets `401` with `"action": "sign_in"`. A role that's too low gets
`403` with `"action": "role"`, and the message says which role is needed.
