# Network: which addresses Easy Chain may reach

People give Easy Chain web addresses:

- a Web request step's URL, or an imported API's;
- a page to add to a Knowledge Base;
- an OpenAPI document to import;
- an MCP server.

On a shared server, any of these could point inside your network instead: the machine
itself, a database, an admin page, or the cloud's metadata service (`169.254.169.254`), which
can hand out the server's credentials. **Settings → Network** says what Easy Chain may reach.

## The three modes

| Mode | Easy Chain reaches | Used for |
|---|---|---|
| **Public sites, and internal addresses on the list** (`public`) | any public address; internal ones only when they're on the allow-list | a shared server: the default when sign-in is on |
| **Only the sites on the list** (`allow-list`) | only what's on the allow-list, public or not | the strictest setup |
| **Anything** (`open`) | everything | one person on their own computer: the default when sign-in is off |

Internal addresses are:

- this machine (`127.0.0.1`, `::1`);
- private networks (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `fc00::/7`);
- link-local addresses, including the cloud metadata service (`169.254.0.0/16`, `fe80::/10`);
- shared address space (`100.64.0.0/10`), and any other address that isn't public.

An IPv4 address written as IPv6 (`::ffff:10.0.0.1`) counts as the IPv4 one.

## The allow-list

Write one entry a line:

| Entry | Allows |
|---|---|
| `docs.example.com` | that host |
| `*.intranet.example.com` | that name and every name ending in it |
| `10.1.2.3` | that address |
| `10.0.0.0/8` | that network |

A **name** on the list is allowed whatever address it points to. An **address or network**
allows whatever resolves to it. So to let flows call one internal service, list its name
(`billing.internal`) or its address. Don't list a whole private network unless everything on
it is fine to reach.

## What is checked, and how

- **Web request steps**, and the tools an Agent makes from them (including imported APIs).
  This covers every run Easy Chain runs. Exported code isn't checked: it uses plain `httpx`.
- **Knowledge Base pages**, both when they're added (read by a worker) and in the preview.
- **OpenAPI documents**, when inspected or imported.
- **MCP servers** over HTTP, both in runs and when listing their tools in Settings.

Every request is checked, and so is every redirect it follows. On a direct connection, Easy
Chain also checks the address it actually connects to, and connects to exactly that address.
So a name that points to a public address when it's checked, and to an internal one a moment
later (DNS rebinding), is still refused. Through an HTTP proxy, the proxy looks up the name, so
only the first check applies.

**Not checked:**

- calls to AI model providers, whose addresses come from the server's own settings and keys
  (Ollama on `localhost` keeps working);
- notifications, whose webhook, Slack and email addresses are set by Owners;
- [single sign-on](sso.md) providers, which Owners set up.

When something is refused, the run (or the page) says so in words. For example: "Easy Chain
didn't connect to 10.0.0.5 (billing.internal): it's an internal address, and this server only
reaches internal addresses on its allow-list. An Owner can add it under Settings → Network."
A failed run offers a **Network settings** button.

## Who can change it

Owners change it. Editors can see it, because it explains why a request was refused. Each
change is in the [audit log](audit.md).

To set it for the whole server instead, use environment variables:

```bash
EASYCHAIN_EGRESS=allow-list             # open, public or allow-list
EASYCHAIN_EGRESS_ALLOW="api.example.com,*.intranet.example.com,10.20.0.0/16"
```

These win over Settings, which then shows the policy read-only. Every API server and worker
should get the same values.

## The API

```
GET /api/settings/network   (Editors)  → {mode, allow, source: "settings"|"environment"|"default", modes}
PUT /api/settings/network   (Owners)   ← {mode, allow: [...]}
```

An entry that isn't a host name, a name ending, an address or a network gets a `422` that
says what's wrong with it.
