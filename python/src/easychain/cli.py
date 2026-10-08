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
    from .templates.samples import ensure_samples

    use_database(url)
    ensure_samples(home)

    async def go() -> int:
        db = await Database.connect(url)
        resources = await open_resources(checkpoint_url(url))
        hub = Hub(db, resources, FlowStore(workspace), SecretStore(home))
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
            await resources.aclose()
            await db.close()
        return 0

    return _platform.run(go())


def cmd_test(args: argparse.Namespace) -> int:
    from .testsets import run_test_set

    variables = dict(v.split("=", 1) for v in args.var or [])
    stand_in = True if args.stand_in else False if args.real_model else None
    results = _platform.run(run_test_set(args.file, variables, stand_in=stand_in))
    passed = sum(r.passed for r in results)
    for r in results:
        print(f"{'✓' if r.passed else '✗'} {r.name}")
        for failure in r.failures:
            print(f"    {failure}")
    print(f"{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


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
    p.set_defaults(func=cmd_test)

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
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
