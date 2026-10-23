# The audit log

The audit log records who changed what in Easy Chain, and when. Owners can read it in
**Settings → Audit log**, through the API (`GET /api/audit`), or on the server with
`easychain audit`. Nothing in it can be changed or removed.

## What is recorded

Every request that changes something and succeeds is recorded:

| Kind | Recorded |
|---|---|
| Flows | made, saved, deleted, test cases added |
| Runs | started, stopped, answered, carried on, re-run from a Save Point |
| Inbox | answered |
| Triggers | added, changed, removed, and each run a webhook or upload started |
| Knowledge Bases | made, changed, deleted, documents added or removed |
| Skills | saved, deleted |
| Secrets | set, deleted (the name only, never the value) |
| Settings | the copilot, MCP servers, sandbox and notifications changed, the sandbox prepared, test notifications sent |
| People | added, changed (role, switched off or on, a new password), removed |
| Keys for scripts | made, revoked |
| Signing in | the first account, signing in and out, wrong passwords, wrong first-account codes, password changes |

Each entry has:

- the time;
- who, as they were shown then (a person's name or email, "a webhook", "the command line",
  or "the local user (sign-in off)"), and how they came in (signed in, a key, a trigger, the
  command line, this computer);
- what: an action such as `flow.save`;
- what it was about (a flow's id, a person's email, a secret's name);
- a few details, such as the run a webhook started, a new role, or which fields changed;
- the address the request came from.

These are **never** recorded:

- **Request bodies.** So no secret values, passwords, webhook addresses or flow contents.
- **Changes that failed.** A refused or invalid request changed nothing. Wrong passwords and
  wrong first-account codes are the exception: they are recorded, because they matter.
- **Reading.** Opening a flow, checking it, seeing its code or searching a Knowledge Base
  isn't recorded.

**Saving a flow** is recorded at most once every 5 minutes per person and flow, because the
editor saves as you type. Every saved version is kept in the flow's history (the *Versions*
list).

Changes made with `easychain users` and `easychain keys` on the server are recorded as "the
command line".

## Nothing can be changed or removed

Database triggers refuse to update or delete entries, on SQLite and on Postgres. Postgres also
refuses `TRUNCATE`. Easy Chain itself has no way to change the log. Only someone with direct
access to the database could, by dropping the triggers first, and a copy of the log kept
elsewhere would show it. To keep that copy, export the log regularly:

```bash
easychain audit --json --since 2026-10-01 >> audit-2026-10.jsonl
```

The log grows as Easy Chain is used, by one short row per change. Easy Chain doesn't remove
old entries.

## Reading it

In the app, **Settings → Audit log** shows the newest first. You can filter by what changed
and by who. **Show older** goes further back.

On the server:

```bash
easychain audit                              # the last 100, newest last
easychain audit --person ann@example.com     # what Ann did
easychain audit --kind flow.                 # flows only (or run., person., secret., …)
easychain audit --target support-bot         # everything about one flow, person or secret
easychain audit --since 2026-10-01 --json    # one JSON object a line
```

The API, for Owners:

```
GET /api/audit?kind=flow.&person=<account id>&target=<id>&since=<unix time>&limit=100&before=<id>
→ {"entries": [{id, at, actor, actor_id, via, action, target, detail, address, text}], "next": <id or null>, "kinds": [...]}
```

`text` is the entry in words, for example "Ann saved flow support-bot". For the page before,
pass `next` as `before`.

## For developers

`python/src/easychain/server/audit.py` has the list:

- `AUDITED` gives each route that changes something, and the action it records;
- `NOT_AUDITED` gives each POST route that doesn't change anything, and the reason.

`tests/test_audit.py` fails if a route that changes something is missing from both, so a new
endpoint can't be left out. A route can name its entry's target, or add details, with
`audit.note(request, target, **detail)`.
