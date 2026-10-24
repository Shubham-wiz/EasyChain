"""The ``easychain`` command line.

easychain new my-flow --template summarise-url
easychain validate my-flow.flow.yaml
easychain run my-flow.flow.yaml --input url=https://example.com
easychain export my-flow.flow.yaml -o out/
easychain test summarise-url.tests.yaml --var base_url=http://localhost:8765
easychain dev
easychain worker --database-url postgresql://…
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from . import __version__, _platform
from .compiler import CompileError, compile_flow, validate
from .spec import SpecError, dumps_spec, load_spec


def _err(message: str) -> None:
    print(message, file=sys.stderr)


def file_resolver(path: str) -> Any:
    """Sub-flows are found next to the flow file, as ``<id>.flow.yaml``."""
    folder = Path(path).resolve().parent

    def resolve(flow_id: str) -> Any:
        candidate = folder / f"{flow_id}.flow.yaml"
        try:
            return load_spec(candidate) if candidate.exists() else None
        except SpecError:
            return None

    return resolve


def _flow_id(path: str) -> str:
    return Path(path).name.removesuffix(".flow.yaml")


def _load(path: str) -> Any:
    try:
        return load_spec(path)
    except FileNotFoundError:
        _err(f"No such file: {path}")
        raise SystemExit(2) from None
    except SpecError as exc:
        _err(str(exc))
        raise SystemExit(2) from None


def cmd_new(args: argparse.Namespace) -> int:
    from .spec.models import FlowSpec
    from .templates import load_template

    name = args.name
    path = Path(args.dir) / (name if name.endswith(".flow.yaml") else f"{name}.flow.yaml")
    if path.exists() and not args.force:
        _err(f"{path} already exists (use --force to overwrite).")
        return 1
    if args.template:
        try:
            spec = load_template(args.template)
        except KeyError:
            _err(f"No template called '{args.template}'. Run `easychain templates` to see them.")
            return 2
    else:
        spec = FlowSpec.model_validate(
            {
                "name": name.replace("-", " ").replace("_", " ").capitalize(),
                "steps": [
                    {
                        "id": "input",
                        "type": "input",
                        "name": "Input",
                        "settings": {"fields": [{"name": "question"}]},
                    },
                    {
                        "id": "ask_ai",
                        "type": "ai_model",
                        "name": "Ask the AI",
                        "settings": {"prompt": "question"},
                    },
                    {
                        "id": "output",
                        "type": "output",
                        "name": "Output",
                        "settings": {"fields": ["answer"]},
                    },
                ],
                "connections": [
                    {"from": "input", "to": "ask_ai"},
                    {"from": "ask_ai", "to": "output"},
                ],
            }
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps_spec(spec), encoding="utf-8", newline="\n")
    print(f"Created {path}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    spec = _load(args.file)
    from .compiler import FlowAnalysis

    an = FlowAnalysis(spec, resolve=file_resolver(args.file), flow_id=_flow_id(args.file))
    issues = validate(spec, analysis=an)
    for issue in issues:
        print(issue)
    errors = sum(1 for i in issues if i.level == "error")
    warnings = len(issues) - errors
    print(f"{'✗' if errors else '✓'} {spec.name}: {errors} error(s), {warnings} warning(s)")
    return 1 if errors else 0


def cmd_compile(args: argparse.Namespace) -> int:
    spec = _load(args.file)
    try:
        compiled = compile_flow(spec, resolve=file_resolver(args.file), flow_id=_flow_id(args.file))
    except CompileError as exc:
        _err(str(exc))
        return 1
    if args.output:
        Path(args.output).write_text(compiled.source, encoding="utf-8", newline="\n")
        print(f"Wrote {args.output}")
    else:
        sys.stdout.write(compiled.source)
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    from .compiler import module_name
    from .export import export_to_dir, export_zip

    spec = _load(args.file)
    try:
        if args.zip:
            out = Path(args.output or f"{module_name(spec.name)}.zip")
            out.write_bytes(export_zip(spec, file_resolver(args.file), _flow_id(args.file)))
            print(f"Wrote {out}")
        else:
            out_dir = Path(args.output or module_name(spec.name))
            for path in export_to_dir(spec, out_dir, file_resolver(args.file), _flow_id(args.file)):
                print(f"Wrote {path}")
    except CompileError as exc:
        _err(str(exc))
        return 1
    return 0


def _parse_inputs(args: argparse.Namespace) -> dict[str, Any]:
    inputs: dict[str, Any] = {}
    if args.json:
        inputs.update(json.loads(args.json))
    for item in args.input or []:
        key, sep, value = item.partition("=")
        if not sep:
            _err(f"--input expects key=value, got '{item}'")
            raise SystemExit(2)
        inputs[key.strip()] = value
    return inputs


def _sandbox(args: argparse.Namespace) -> Any:
    """--sandbox auto|docker|off, else EASYCHAIN_SANDBOX (by default auto)."""
    from .runtime.sandbox import SandboxSettings

    mode = getattr(args, "sandbox", None)
    return SandboxSettings.from_dict({"mode": mode} if mode else {})


def cmd_run(args: argparse.Namespace) -> int:
    from .runtime import RunOptions, stream_run
    from .templates.samples import ensure_samples

    ensure_samples()
    spec = _load(args.file)
    inputs = _parse_inputs(args)
    options = RunOptions(
        thread_id=args.thread,
        stand_in=args.stand_in,
        resolve=file_resolver(args.file),
        flow_id=_flow_id(args.file),
        sandbox=_sandbox(args),
    )
    printer = _RunPrinter(spec, args)

    async def go() -> int:
        final: dict[str, Any] = {}
        events = stream_run(spec, inputs, options)
        while True:
            async for ev in events:
                printer.show(ev)
                final = ev
            if final.get("status") != "paused" or final.get("reason") != "ask_human":
                break
            if args.events or not sys.stdin.isatty():
                _err(
                    "The run is waiting for an answer (Ask a Human). Run it in a terminal to answer."
                )
                return 3
            answers = {i["id"]: _ask_in_terminal(i["request"]) for i in final["interrupts"]}
            options.action, options.resume, options.thread_id = (
                "resume",
                answers,
                final.get("thread_id"),
            )
            events = stream_run(spec, None, options)
        return printer.finish(final)

    return _platform.run(go())


def _ask_in_terminal(request: dict[str, Any]) -> Any:
    from .compiler.helpers import HELPERS

    if request.get("kind") == "approve_tool":
        print(f"\n{request['question']}")
        for action in request.get("actions") or []:
            print(f"  {action['tool']}({json.dumps(action['args'], ensure_ascii=False)})")
        if input("approve? [y/n]> ").strip().lower().startswith("y"):
            return {"action": "approve"}
        return {"action": "reject", "comment": input("why not?> ")}

    namespace: dict[str, Any] = {"Any": Any}
    exec(HELPERS["ask_in_terminal"].code, namespace)  # the same prompt exported flows use
    return namespace["ask_in_terminal"](request)


class _RunPrinter:
    """Prints run progress to stderr and the result to stdout."""

    def __init__(self, spec: Any, args: argparse.Namespace):
        self.spec = spec
        self.args = args
        self.streaming: str | None = None

    def _name(self, step_id: str) -> str:
        try:
            step = self.spec.step(step_id)
        except KeyError:
            return step_id
        return step.name or step.id

    def show(self, ev: dict[str, Any]) -> None:
        kind = ev["type"]
        if self.args.events:
            print(json.dumps(ev, ensure_ascii=False, default=str))
            return
        if ev.get("path"):
            return  # steps inside a Sub-flow: the Sub-flow step reports for them
        quiet = self.args.quiet
        if kind == "step_started" and not quiet:
            if self.spec.step(ev["step"]).type not in ("input", "output"):
                item = f" (item {ev['item'] + 1})" if isinstance(ev.get("item"), int) else ""
                print(f"▶ {self._name(ev['step'])}{item}", file=sys.stderr)
        elif kind == "token" and not quiet:
            self.streaming = ev["step"]
            sys.stderr.write(ev["text"])
            sys.stderr.flush()
        elif kind == "step_finished" and not quiet:
            if self.streaming == ev["step"]:
                sys.stderr.write("\n")
                self.streaming = None
            if self.spec.step(ev["step"]).type not in ("input", "output"):
                extra = ""
                if ev.get("usage"):
                    u = ev["usage"]
                    extra = f", {u['input_tokens']}+{u['output_tokens']} tokens"
                print(
                    f"✓ {self._name(ev['step'])} ({ev['duration_ms']:.0f} ms{extra})",
                    file=sys.stderr,
                )
        elif kind == "route" and not quiet:
            print(f"↳ {self._name(ev['step'])}: took “{ev['exit']}”", file=sys.stderr)
        elif kind == "tool_started" and not quiet:
            args = json.dumps(ev.get("args") or {}, ensure_ascii=False)
            print(f"  ⚙ {ev['tool']} {args[:120]}", file=sys.stderr)
        elif kind == "tool_finished" and not quiet and ev.get("status") == "error":
            print(f"  ✗ {ev.get('tool') or 'tool'}: {str(ev.get('result'))[:200]}", file=sys.stderr)
        elif kind == "progress" and not quiet:
            print(
                f"  {self._name(ev['step'])}: {ev['done']} of {ev['total']} done", file=sys.stderr
            )
        elif kind == "step_failed":
            err = ev["error"]
            _err(f"✗ {self._name(ev['step'])}: {err['message']}")
            if err.get("hint"):
                _err(f"  {err['hint']}")

    def finish(self, final: dict[str, Any]) -> int:
        if final.get("status") == "ok":
            if not self.args.events:
                print(json.dumps(final.get("output"), indent=2, ensure_ascii=False, default=str))
            return 0
        if not self.args.events:
            err = final.get("error") or {}
            if err.get("kind") == "bad_input":
                for problem in err.get("problems", []):
                    _err(problem["message"])
            elif err.get("kind") == "invalid_flow":
                _err(err.get("message", "The run failed."))
                for issue in final.get("issues", []):
                    _err(
                        f"  - {issue.get('message')}"
                        + (f" [{issue['step']}]" if issue.get("step") else "")
                    )
        return 1


def cmd_worker(args: argparse.Namespace) -> int:
    """Run jobs from the queue until stopped.

    Ctrl+C, SIGTERM (or Ctrl+Break on Windows) hands running jobs back to the queue.
    """
    import logging

    from .runtime.resources import open_resources
    from .server.app import default_database_url
    from .server.db import Database, checkpoint_url
    from .server.hub import Hub
    from .server.listen import listen
    from .server.secrets import SecretStore
    from .server.store import FlowStore
    from .server.worker import Worker

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING, format="%(message)s"
    )
    home = Path(args.home or os.environ.get("EASYCHAIN_HOME") or Path.home() / ".easychain")
    workspace = Path(args.workspace or os.environ.get("EASYCHAIN_WORKSPACE") or home / "flows")
    url = args.database_url or default_database_url(home)
    from .server.knowledge_api import use_database
    from .skills import SkillLibrary, use_library
    from .templates.samples import ensure_samples

    use_database(url)
    use_library(SkillLibrary(workspace / "skills"))
    ensure_samples(home)

    async def go() -> int:
        db = await Database.connect(url)
        resources = await open_resources(checkpoint_url(url))
        # home: other workspaces' flows and vaults are under it (server/spaces.py).
        hub = Hub(db, resources, FlowStore(workspace), SecretStore(home), home=home)
        listener = listen(url, hub.bus)
        worker = Worker(
            hub,
            concurrency=args.concurrency,
            lease_seconds=args.lease,
            poll=args.poll,
            schedules=not args.no_schedules,
        )
        stop = asyncio.Event()
        _platform.on_stop(asyncio.get_running_loop(), stop.set)
        print(f"Easy Chain worker {worker.name} ({db.dialect}) waiting for jobs", flush=True)
        try:
            await worker.run(stop)
        finally:
            if listener is not None:
                await listener.aclose()
            await resources.aclose()
            await db.close()
        return 0

    return _platform.run(go())


def cmd_db(args: argparse.Namespace) -> int:
    """Look at or upgrade the run database (Easy Chain also upgrades it when it starts)."""
    import sqlalchemy as sa
    from sqlalchemy.ext.asyncio import create_async_engine

    from .server import migrations
    from .server.app import default_database_url
    from .server.db import Database, async_url

    home = Path(args.home or os.environ.get("EASYCHAIN_HOME") or Path.home() / ".easychain")
    url = args.database_url or default_database_url(home)
    shown = sa.engine.make_url(url).render_as_string(hide_password=True)
    print(f"Database: {shown}")
    if args.action == "status" and url.startswith("sqlite") and ":///" in url:
        path = url.split(":///", 1)[1]
        if path != ":memory:" and not Path(path).exists():
            print("There is no database here yet. Easy Chain makes it when it first starts.")
            return 0

    async def status() -> int:
        engine = create_async_engine(async_url(url))
        try:
            async with engine.connect() as conn:
                at = await conn.run_sync(migrations.current)
                todo = await conn.run_sync(migrations.pending)
        except migrations.NewerDatabase as exc:
            print(exc)
            return 1
        finally:
            await engine.dispose()
        print(f"At migration: {at or 'none (made before migrations, or empty)'}")
        print(f"Latest migration: {migrations.head()}")
        if not todo:
            print("Up to date.")
        else:
            print(f"{len(todo)} migration(s) to run (`easychain db upgrade`, or start Easy Chain):")
            for step in todo:
                print(f"  {step}")
        return 0

    async def upgrade() -> int:
        try:
            db = await Database.connect(url)
        except migrations.NewerDatabase as exc:
            print(exc)
            return 1
        await db.close()
        if db.migrated:
            print("Upgraded:")
            for step in db.migrated:
                print(f"  {step}")
        else:
            print("Already up to date.")
        return 0

    return _platform.run(status() if args.action == "status" else upgrade())


def _with_accounts(args: argparse.Namespace, work: Any) -> int:
    """Open the run database and call ``work(accounts)``; an AccountError is printed."""
    from .server.accounts import AccountError, Accounts
    from .server.app import default_database_url
    from .server.db import Database

    home = Path(args.home or os.environ.get("EASYCHAIN_HOME") or Path.home() / ".easychain")
    url = args.database_url or default_database_url(home)

    async def go() -> int:
        db = await Database.connect(url)
        try:
            return int(await work(Accounts(db)) or 0)
        except AccountError as exc:
            print(exc, file=sys.stderr)
            return 1
        finally:
            await db.close()

    return _platform.run(go())


async def _cli_audit(
    accounts: Any, action: str, target: str, workspace: str = "default", **detail: Any
) -> None:
    """Changes made on the command line are in the audit log too (by "the command line")."""
    await accounts.db.add_audit(
        workspace_id=workspace,
        actor_id=None,
        actor="the command line",
        via="cli",
        action=action,
        target=target,
        detail=detail,
        address=None,
    )


def _new_password(args: argparse.Namespace) -> str | None:
    """From stdin (--password-stdin), or asked for twice; None (and a message) if unusable."""
    from .server.auth import password_problem

    if args.password_stdin:
        password = sys.stdin.readline().rstrip("\r\n")
    else:
        import getpass

        password = getpass.getpass("Password: ")
        if getpass.getpass("Again: ") != password:
            print("The two passwords don't match.", file=sys.stderr)
            return None
    if problem := password_problem(password):
        print(problem, file=sys.stderr)
        return None
    return password


def cmd_users(args: argparse.Namespace) -> int:
    """People who can sign in (when sign-in is on: see docs/people.md)."""
    from .server.auth import ROLE_LABELS

    async def find(accounts: Any) -> dict[str, Any]:
        from .server.accounts import AccountError

        person = await accounts.by_email(args.email)
        if person is None:
            raise AccountError(f"There's no account for {args.email}.")
        return person

    async def work(accounts: Any) -> int:
        from .server.accounts import AccountError

        ws = args.workspace
        if await accounts.workspace(ws) is None:
            raise AccountError(f"There's no workspace {ws} (easychain workspaces list).")
        if args.action == "list":
            people = await accounts.members(ws)
            if not people:
                print("Nobody has an account yet." if ws == "default" else f"Nobody is in {ws}.")
            for p in people:
                off = "  (switched off)" if p["disabled"] else ""
                print(f"{p['email']:<36} {ROLE_LABELS[p['role']]:<8} {p['name']}{off}")
            return 0
        if args.action == "setup-code":
            code = await accounts.setup_code()
            if code is None:
                print("There are accounts already: sign in, or ask an Owner.", file=sys.stderr)
                return 1
            # Replaces the code the server printed when it started.
            await _cli_audit(accounts, "auth.setup_code", "the first account")
            print(code)
            return 0
        if args.action == "add":
            found = await accounts.by_email(args.email)
            if found is not None:
                # Someone with an account already: into this workspace too, same password.
                made = await accounts.add_member(found["id"], args.role, ws)
            else:
                password = _new_password(args)
                if password is None:
                    return 1
                made = await accounts.create(args.email, args.name or "", password, args.role, ws)
            await _cli_audit(accounts, "person.add", made["email"], ws, role=args.role)
            where = "" if ws == "default" else f" in {ws}"
            print(f"Added {args.email.strip().lower()} as {ROLE_LABELS[args.role]}{where}.")
            return 0
        person = await find(accounts)
        if args.action == "role":
            await accounts.update(person["id"], role=args.role, workspace=ws)
            await _cli_audit(accounts, "person.change", person["email"], ws, role=args.role)
            print(f"{person['email']} is now {ROLE_LABELS[args.role]}.")
        elif args.action in ("disable", "enable"):
            await accounts.update(person["id"], disabled=args.action == "disable")
            await _cli_audit(
                accounts, "person.change", person["email"], disabled=args.action == "disable"
            )
            state = "switched off and signed out" if args.action == "disable" else "switched on"
            print(f"{person['email']} is {state}.")
        elif args.action == "password":
            password = _new_password(args)
            if password is None:
                return 1
            await accounts.set_password(person["id"], password)
            await _cli_audit(accounts, "person.change", person["email"], password="set")
            print(f"New password set for {person['email']}; their browsers are signed out.")
        elif args.action == "remove":
            await accounts.remove(person["id"], ws)
            await _cli_audit(accounts, "person.remove", person["email"], ws)
            print(f"Removed {person['email']} from {ws}.")
        return 0

    needs_email = args.action not in ("list", "setup-code")
    if needs_email and not args.email:
        print(f"Say whose account: easychain users {args.action} EMAIL", file=sys.stderr)
        return 2
    if args.action in ("add", "role") and not args.role:
        print("Say which role: --role viewer|runner|editor|owner", file=sys.stderr)
        return 2
    return _with_accounts(args, work)


def cmd_keys(args: argparse.Namespace) -> int:
    """API keys for scripts and other apps (Authorization: Bearer eck_…)."""

    async def work(accounts: Any) -> int:
        from .server.accounts import AccountError

        if args.action == "revoke":
            if not await accounts.revoke_key(args.target):
                raise AccountError(f"There's no key {args.target} (or it's already revoked).")
            await _cli_audit(accounts, "key.revoke", args.target)
            print(f"Revoked key {args.target}.")
            return 0
        person = await accounts.by_email(args.target) if args.target else None
        if args.target and person is None:
            raise AccountError(f"There's no account for {args.target}.")
        ws = args.workspace
        if args.action == "list":
            emails = {p["id"]: p["email"] for p in await accounts.members(ws)}
            keys = await accounts.keys(person["id"] if person else None, workspace=ws)
            if not keys:
                print("No keys.")
            for k in keys:
                print(f"{k['id']}  {emails.get(k['user_id'], '?'):<36} {k['role']:<8} {k['name']}")
            return 0
        if person is None:
            raise AccountError("Say whose key it is: easychain keys create EMAIL")
        member = await accounts.member(person["id"], ws)
        made = await accounts.create_key(
            person["id"], args.name, args.role or (member or {}).get("role", "viewer"), ws
        )
        await _cli_audit(
            accounts,
            "key.create",
            made["name"],
            ws,
            key=made["id"],
            role=made["role"],
            person=person["email"],
        )
        # Only the key on stdout, so a script can capture it; it isn't shown again.
        print(made["key"])
        print(
            f"Key {made['id']} ({made['role']}) for {person['email']}. Copy it now.",
            file=sys.stderr,
        )
        return 0

    if args.action in ("create", "revoke") and not args.target:
        what = "EMAIL" if args.action == "create" else "KEY_ID"
        print(f"easychain keys {args.action} {what}", file=sys.stderr)
        return 2
    return _with_accounts(args, work)


def cmd_audit(args: argparse.Namespace) -> int:
    """What people changed, newest first (the audit log: docs/audit.md)."""
    import datetime as dt

    from .server.audit import describe

    def when(text: str) -> float:
        try:
            moment = dt.datetime.fromisoformat(text)
        except ValueError:
            raise SystemExit(
                f"--since takes a date or time, like 2026-10-01 or 2026-10-01T09:00 ({text!r})"
            ) from None
        return (moment if moment.tzinfo else moment.astimezone()).timestamp()

    async def work(accounts: Any) -> int:
        person = await accounts.by_email(args.person) if args.person else None
        if args.person and person is None:
            print(f"There's no account for {args.person}.", file=sys.stderr)
            return 1
        entries = await accounts.db.list_audit(
            actor_id=person["id"] if person else None,
            action=args.kind,
            target=args.target,
            since=when(args.since) if args.since else None,
            limit=args.limit,
            workspace=args.workspace,
        )
        for entry in reversed(entries):
            if args.json:
                print(json.dumps(entry, ensure_ascii=False, default=str))
            else:
                at = dt.datetime.fromtimestamp(entry["at"]).isoformat(sep=" ", timespec="seconds")
                print(f"{at}  {describe(entry)}")
        if not entries and not args.json:
            print("Nothing in the audit log matches.")
        return 0

    return _with_accounts(args, work)


def cmd_workspaces(args: argparse.Namespace) -> int:
    """Workspaces: each has its own flows, runs, secrets and people (docs/workspaces.md)."""

    async def work(accounts: Any) -> int:
        from .server.accounts import AccountError
        from .server.auth_api import _workspace_id
        from .server.spaces import ID_RE

        if args.action == "list":
            for found in await accounts.workspaces():
                people = f"{found['people']} {'person' if found['people'] == 1 else 'people'}"
                print(f"{found['id']:<24} {found['name']:<32} {people}")
            return 0
        if not args.name:
            raise AccountError(f"Give the name: easychain workspaces {args.action} … --name NAME")
        if args.action == "rename":
            changed = await accounts.rename_workspace(args.id or "", args.name)
            await _cli_audit(accounts, "workspace.rename", changed["id"], name=changed["name"])
            print(f"{changed['id']} is now called {changed['name']}.")
            return 0
        workspace_id = args.id or _workspace_id(args.name)
        if not ID_RE.match(workspace_id):
            raise AccountError(
                "A workspace id is 2 to 40 lowercase letters, digits and dashes, starting with "
                "a letter."
            )
        owner = await accounts.by_email(args.owner) if args.owner else None
        if args.owner and owner is None:
            raise AccountError(f"There's no account for {args.owner}.")
        made = await accounts.create_workspace(
            workspace_id, args.name, owner["id"] if owner else None
        )
        await _cli_audit(accounts, "workspace.create", made["id"], name=made["name"])
        print(f"Made workspace {made['id']} ({made['name']}).")
        if owner is None:
            print(
                f"Nobody is in it yet: easychain users add EMAIL --role owner --workspace "
                f"{made['id']}",
                file=sys.stderr,
            )
        return 0

    if args.action == "rename" and not args.id:
        print("Say which workspace: easychain workspaces rename ID --name NAME", file=sys.stderr)
        return 2
    return _with_accounts(args, work)


def cmd_test(args: argparse.Namespace) -> int:
    from .testsets import run_test_set

    variables = dict(v.split("=", 1) for v in args.var or [])
    stand_in = True if args.stand_in else False if args.real_model else None
    results = _platform.run(
        run_test_set(args.file, variables, stand_in=stand_in, sandbox=_sandbox(args))
    )
    passed = sum(r.passed for r in results)
    for r in results:
        print(f"{'✓' if r.passed else '✗'} {r.name}")
        for failure in r.failures:
            print(f"    {failure}")
        for note in r.skipped:
            print(f"    – {note}")
    skipped = sum(bool(r.skipped) for r in results)
    note = f" ({skipped} with checks skipped)" if skipped else ""
    print(f"{passed}/{len(results)} passed{note}")
    return 0 if passed == len(results) else 1


def cmd_eval(args: argparse.Namespace) -> int:
    """The CI gate: run a Test Set and compare it with its baseline (docs/ci.md)."""
    from .evaluate import gate, load_baseline, markdown, run_evaluation, save_baseline

    path = Path(args.file)
    default = path.with_name(path.name.removesuffix(".tests.yaml") + ".baseline.json")
    baseline_path = Path(args.baseline) if args.baseline else default
    variables = dict(v.split("=", 1) for v in args.var or [])
    stand_in = True if args.stand_in else False if args.real_model else None
    try:
        baseline = None if args.save_baseline else load_baseline(baseline_path)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    evaluation = _platform.run(
        run_evaluation(
            path,
            variables=variables,
            stand_in=stand_in,
            repeat=args.repeat,
            budget=args.budget,
            sandbox=_sandbox(args),
        )
    )
    gate(evaluation, baseline, max_slower=args.max_slower, max_costlier=args.max_costlier)
    changes = {r["name"]: r for r in (evaluation.comparison or {}).get("cases", [])}
    for case in evaluation.cases:
        mark = "✓" if case["passed"] == case["runs"] else "✗"
        runs = f" ({case['passed']}/{case['runs']})" if case["runs"] > 1 else ""
        change = changes.get(case["name"], {}).get("change")
        print(f"{mark} {case['name']}{runs}" + (f"  [{change}]" if change else ""))
        for failure in case["failures"]:
            print(f"    {failure}")
    s = evaluation.summary
    against = f", against {baseline_path.name}" if baseline is not None else ", no baseline"
    print(f"{s['passed']}/{s['total']} passed{against}")
    for problem in evaluation.problems:
        print(problem)
    if args.summary:
        with open(args.summary, "a", encoding="utf-8", newline="\n") as out:
            out.write(markdown(evaluation) + "\n")
    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {
                    "ok": evaluation.ok,
                    "problems": evaluation.problems,
                    "summary": evaluation.summary,
                    "cases": evaluation.cases,
                    "not_run": evaluation.not_run,
                    "comparison": evaluation.comparison,
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
    if args.save_baseline:
        save_baseline(baseline_path, evaluation)
        print(f"Saved the baseline to {baseline_path}")
        return 0
    return 0 if evaluation.ok else 1


def cmd_publish(args: argparse.Namespace) -> int:
    """Publish a flow on a server, from CI: staging after a passing Test Run there."""
    import httpx

    from .deploy import DeployError, publish_flow

    server = args.server or os.environ.get("EASYCHAIN_SERVER")
    key = args.key or os.environ.get("EASYCHAIN_KEY")
    if not server:
        print("Say which server: --server https://… (or EASYCHAIN_SERVER)", file=sys.stderr)
        return 2
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    if args.workspace:
        headers["X-EasyChain-Workspace"] = args.workspace
    variables = dict(v.split("=", 1) for v in args.var or [])
    with httpx.Client(base_url=server.rstrip("/"), headers=headers, timeout=60) as client:
        try:
            publish_flow(
                client,
                args.flow,
                args.to,
                repeat=args.repeat,
                variables=variables,
                timeout=args.timeout,
            )
        except (DeployError, httpx.HTTPError) as exc:
            print(exc, file=sys.stderr)
            return 1
    return 0


def cmd_deploy(args: argparse.Namespace) -> int:
    """Pack flows for a self-hosted server: a Compose bundle, or a Docker image."""
    from .deploy import DeployError, build_image, compose_bundle

    home = Path(os.environ.get("EASYCHAIN_HOME") or Path.home() / ".easychain")
    flows = Path(args.flows or os.environ.get("EASYCHAIN_WORKSPACE") or home / "flows")
    try:
        if args.kind == "compose":
            count = compose_bundle(flows, Path(args.target), image=args.image, base=args.base)
            print(
                f"Wrote a Compose bundle with {count} flow(s) to {args.target}; see its README.md."
            )
        else:
            count = build_image(flows, args.target, base=args.base)
            print(f"Built {args.target} with {count} flow(s).")
    except DeployError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


def cmd_schema(args: argparse.Namespace) -> int:
    from .spec.schema import flow_json_schema_text

    text = flow_json_schema_text()
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8", newline="\n")
        print(f"Wrote {args.output}")
    else:
        sys.stdout.write(text)
    return 0


def cmd_templates(args: argparse.Namespace) -> int:
    from .templates import list_templates

    for t in list_templates():
        keys = ", ".join(k["env"] for k in t["keys"]) or "none"
        print(f"{t['id']:<20} {t['name']} (keys: {keys})")
    return 0


def cmd_dev(args: argparse.Namespace) -> int:
    import uvicorn

    if args.workspace:
        os.environ["EASYCHAIN_WORKSPACE"] = str(Path(args.workspace).resolve())
    if args.home:
        os.environ["EASYCHAIN_HOME"] = str(Path(args.home).resolve())
    if args.database_url:
        os.environ["EASYCHAIN_DATABASE_URL"] = args.database_url
    if args.no_worker:
        os.environ["EASYCHAIN_WORKER"] = "off"
    # Sign-in is on by default when the server answers the network (server/auth.py).
    os.environ["EASYCHAIN_HOST"] = args.host
    print(f"Easy Chain {__version__} running at http://{args.host}:{args.port}", flush=True)
    uvicorn.run(
        "easychain.server.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
        loop=_platform.uvicorn_loop(),
        log_level="warning" if not args.verbose else "info",
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="easychain", description="Easy Chain: draw your AI app, press Run, ship it."
    )
    parser.add_argument("--version", action="version", version=f"easychain {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("new", help="create a flow file (blank or from a template)")
    p.add_argument("name")
    p.add_argument("--template", "-t")
    p.add_argument("--dir", default=".")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("validate", help="check a flow for problems")
    p.add_argument("file")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("compile", help="print the LangGraph code for a flow")
    p.add_argument("file")
    p.add_argument("-o", "--output")
    p.set_defaults(func=cmd_compile)

    p = sub.add_parser("export", help="export a flow as a standalone LangGraph project")
    p.add_argument("file")
    p.add_argument("-o", "--output")
    p.add_argument("--zip", action="store_true")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("run", help="run a flow and print the result")
    p.add_argument("file")
    p.add_argument("-i", "--input", action="append", metavar="KEY=VALUE")
    p.add_argument("--json", help="inputs as a JSON object")
    p.add_argument("--thread", help="conversation/thread id (keeps chat history)")
    p.add_argument(
        "--stand-in", action="store_true", help="use the stand-in AI (no API key needed)"
    )
    p.add_argument("--events", action="store_true", help="print every run event as JSON lines")
    p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument(
        "--sandbox",
        choices=["auto", "docker", "off"],
        help="where Code steps run: auto (Docker when it's there; the default, or "
        "EASYCHAIN_SANDBOX), docker (always), off (in this process)",
    )
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("test", help="run a Test Set file against its flow")
    p.add_argument("file")
    p.add_argument("--var", action="append", metavar="NAME=VALUE")
    which = p.add_mutually_exclusive_group()
    which.add_argument(
        "--stand-in", action="store_true", help="use the stand-in AI (and the cases' scripts)"
    )
    which.add_argument(
        "--real-model",
        action="store_true",
        help="use the flow's real model even if the file says stand_in: true (needs a key)",
    )
    p.add_argument(
        "--sandbox",
        choices=["auto", "docker", "off"],
        help="where Code steps run: auto (Docker when it's there; the default, or "
        "EASYCHAIN_SANDBOX), docker (always), off (in this process)",
    )
    p.set_defaults(func=cmd_test)

    p = sub.add_parser(
        "eval", help="the CI gate: run a Test Set and fail on a regression against its baseline"
    )
    p.add_argument("file", help="a Test Set (*.tests.yaml)")
    p.add_argument(
        "--baseline", help="the baseline file (default: <name>.baseline.json next to the file)"
    )
    p.add_argument(
        "--save-baseline", action="store_true", help="save this run as the baseline (always passes)"
    )
    p.add_argument("--repeat", type=int, default=1, help="run each case this many times")
    p.add_argument("--var", action="append", metavar="NAME=VALUE")
    which = p.add_mutually_exclusive_group()
    which.add_argument("--stand-in", action="store_true", help="use the stand-in AI (no keys)")
    which.add_argument(
        "--real-model", action="store_true", help="use the flows' real models (needs their keys)"
    )
    p.add_argument(
        "--max-slower", type=float, metavar="PERCENT", help="fail when the run is this much slower"
    )
    p.add_argument(
        "--max-costlier",
        type=float,
        metavar="PERCENT",
        help="fail when the run costs this much more",
    )
    p.add_argument(
        "--budget", type=float, metavar="DOLLARS", help="stop starting cases once this is spent"
    )
    p.add_argument("--summary", help="append a Markdown report here (e.g. $GITHUB_STEP_SUMMARY)")
    p.add_argument("--json", help="write the results here as JSON")
    p.add_argument("--sandbox", choices=["auto", "docker", "off"], help="where Code steps run")
    p.set_defaults(func=cmd_eval)

    p = sub.add_parser(
        "publish", help="publish a flow on a server (staging after a passing Test Run there)"
    )
    p.add_argument("flow", help="the flow's id on the server")
    p.add_argument("--to", required=True, choices=["dev", "staging", "production"])
    p.add_argument("--server", help="the server's address (or EASYCHAIN_SERVER)")
    p.add_argument("--key", help="a key for scripts, eck_… (or EASYCHAIN_KEY)")
    p.add_argument("--workspace", help="the workspace (a key works in its own)")
    p.add_argument("--repeat", type=int, default=1, help="run each case this many times")
    p.add_argument("--var", action="append", metavar="NAME=VALUE")
    p.add_argument("--timeout", type=float, default=1800, help="seconds to wait for the Test Run")
    p.set_defaults(func=cmd_publish)

    p = sub.add_parser("deploy", help="pack flows for a self-hosted server (Compose or an image)")
    p.add_argument("kind", choices=["compose", "image"])
    p.add_argument("target", help="compose: a new folder for the bundle; image: the image name")
    p.add_argument(
        "--flows", help="the flows folder (default: EASYCHAIN_WORKSPACE or ~/.easychain/flows)"
    )
    p.add_argument(
        "--image", default="easychain-flows:latest", help="the bundle's image name (compose)"
    )
    p.add_argument("--base", help="the Easy Chain image to build on (default: easychain:VERSION)")
    p.set_defaults(func=cmd_deploy)

    p = sub.add_parser("schema", help="print the flow spec JSON Schema")
    p.add_argument("-o", "--output")
    p.set_defaults(func=cmd_schema)

    p = sub.add_parser("templates", help="list the starter templates")
    p.set_defaults(func=cmd_templates)

    p = sub.add_parser("dev", help="start the Easy Chain app (API + canvas)")
    p.add_argument("--host", default=os.environ.get("EASYCHAIN_HOST", "127.0.0.1"))
    p.add_argument("--port", type=int, default=int(os.environ.get("EASYCHAIN_PORT", "8000")))
    p.add_argument("--workspace", help="folder where flows are saved")
    p.add_argument("--home", help="folder for the database, secrets and uploads (~/.easychain)")
    p.add_argument("--database-url", help="sqlite:///path.db (default) or postgresql://…")
    p.add_argument(
        "--no-worker", action="store_true", help="don't run jobs here (use `easychain worker`)"
    )
    p.add_argument("--reload", action="store_true")
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=cmd_dev)

    p = sub.add_parser("db", help="look at or upgrade the run database (migrations)")
    p.add_argument("action", choices=["status", "upgrade"])
    p.add_argument("--home", help="folder for secrets and uploads (~/.easychain)")
    p.add_argument("--database-url", help="sqlite:///path.db or postgresql://… (as the API uses)")
    p.set_defaults(func=cmd_db)

    from .server.auth import ROLES

    p = sub.add_parser("users", help="people who can sign in, and their roles")
    p.add_argument(
        "action",
        choices=["list", "add", "role", "disable", "enable", "password", "remove", "setup-code"],
        help="setup-code: a new code for the first account, if the server's log is gone",
    )
    p.add_argument("email", nargs="?")
    p.add_argument("--role", choices=ROLES)
    p.add_argument("--name", help="how they're shown (add)")
    p.add_argument(
        "--password-stdin", action="store_true", help="read the password from stdin (scripts)"
    )
    p.add_argument("--workspace", default="default", help="the workspace (default: default)")
    p.add_argument("--home", help="folder for secrets and uploads (~/.easychain)")
    p.add_argument("--database-url", help="sqlite:///path.db or postgresql://… (as the API uses)")
    p.set_defaults(func=cmd_users)

    p = sub.add_parser("keys", help="API keys for scripts and other apps")
    p.add_argument("action", choices=["list", "create", "revoke"])
    p.add_argument("target", nargs="?", help="an email (list, create) or a key id (revoke)")
    p.add_argument("--name", default="API key", help="what the key is for (create)")
    p.add_argument("--role", choices=ROLES, help="at most the person's role (create)")
    p.add_argument("--workspace", default="default", help="the workspace (default: default)")
    p.add_argument("--home", help="folder for secrets and uploads (~/.easychain)")
    p.add_argument("--database-url", help="sqlite:///path.db or postgresql://… (as the API uses)")
    p.set_defaults(func=cmd_keys)

    p = sub.add_parser("audit", help="what people changed, newest last (the audit log)")
    p.add_argument("--person", help="only what this email did")
    p.add_argument("--kind", help="an action, or its start: flow. run. person. secret. …")
    p.add_argument("--target", help="only entries about this flow, person, secret, …")
    p.add_argument("--since", help="a date or time, like 2026-10-01 or 2026-10-01T09:00")
    p.add_argument("--limit", type=int, default=100, help="at most this many (default 100)")
    p.add_argument("--json", action="store_true", help="one JSON object a line, to keep or search")
    p.add_argument("--workspace", default="default", help="the workspace (default: default)")
    p.add_argument("--home", help="folder for secrets and uploads (~/.easychain)")
    p.add_argument("--database-url", help="sqlite:///path.db or postgresql://… (as the API uses)")
    p.set_defaults(func=cmd_audit)

    p = sub.add_parser("workspaces", help="workspaces: each with its own flows, secrets, people")
    p.add_argument("action", choices=["list", "create", "rename"])
    p.add_argument("id", nargs="?", help="the workspace's id (create: made from the name)")
    p.add_argument("--name", help="what it's called (create, rename)")
    p.add_argument("--owner", help="an account's email: its first Owner (create)")
    p.add_argument("--home", help="folder for secrets and uploads (~/.easychain)")
    p.add_argument("--database-url", help="sqlite:///path.db or postgresql://… (as the API uses)")
    p.set_defaults(func=cmd_workspaces)

    p = sub.add_parser("worker", help="run queued flow runs (scale out with more workers)")
    p.add_argument("--home", help="folder for secrets and uploads (~/.easychain)")
    p.add_argument("--workspace", help="folder where flows are saved")
    p.add_argument("--database-url", help="sqlite:///path.db or postgresql://… (as the API uses)")
    p.add_argument("--concurrency", type=int, default=4, help="runs at once (default 4)")
    p.add_argument(
        "--lease", type=float, default=30, help="seconds before a silent worker's job is taken over"
    )
    p.add_argument("--poll", type=float, default=0.5, help="seconds between queue checks")
    p.add_argument("--no-schedules", action="store_true", help="don't fire scheduled triggers")
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=cmd_worker)
    return parser


def main(argv: list[str] | None = None) -> int:
    _platform.utf8_console()
    args = build_parser().parse_args(argv)
    if isinstance(getattr(args, "file", None), str):
        # Skills, like Sub-flows, are found next to the flow (or Test Set) file.
        from .skills import SkillLibrary, use_library

        use_library(SkillLibrary(Path(args.file).resolve().parent / "skills"))
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
