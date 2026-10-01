"""The ``easychain`` command line.

easychain new my-flow --template summarise-url
easychain validate my-flow.flow.yaml
easychain run my-flow.flow.yaml --input url=https://example.com
easychain export my-flow.flow.yaml -o out/
easychain test summarise-url.tests.yaml --var base_url=http://localhost:8765
easychain dev
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .compiler import CompileError, compile_flow, validate
from .spec import SpecError, dumps_spec, load_spec


def _err(message: str) -> None:
    print(message, file=sys.stderr)


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
    path.write_text(dumps_spec(spec), encoding="utf-8")
    print(f"Created {path}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    spec = _load(args.file)
    issues = validate(spec)
    for issue in issues:
        print(issue)
    errors = sum(1 for i in issues if i.level == "error")
    warnings = len(issues) - errors
    print(f"{'✗' if errors else '✓'} {spec.name}: {errors} error(s), {warnings} warning(s)")
    return 1 if errors else 0


def cmd_compile(args: argparse.Namespace) -> int:
    spec = _load(args.file)
    try:
        compiled = compile_flow(spec)
    except CompileError as exc:
        _err(str(exc))
        return 1
    if args.output:
        Path(args.output).write_text(compiled.source, encoding="utf-8")
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
            out.write_bytes(export_zip(spec))
            print(f"Wrote {out}")
        else:
            out_dir = Path(args.output or module_name(spec.name))
            for path in export_to_dir(spec, out_dir):
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

    spec = _load(args.file)
    inputs = _parse_inputs(args)
    options = RunOptions(thread_id=args.thread, stand_in=args.stand_in)

    async def go() -> int:
        final: dict[str, Any] = {}
        streaming_step = None
        async for ev in stream_run(spec, inputs, options):
            kind = ev["type"]
            if args.events:
                print(json.dumps(ev, ensure_ascii=False, default=str))
            elif kind == "step_started" and not args.quiet:
                step = spec.step(ev["step"])
                if step.type not in ("input", "output"):
                    print(f"▶ {step.name or step.id}", file=sys.stderr)
            elif kind == "token" and not args.quiet:
                if streaming_step != ev["step"]:
                    streaming_step = ev["step"]
                sys.stderr.write(ev["text"])
                sys.stderr.flush()
            elif kind == "step_finished" and not args.quiet:
                if streaming_step == ev["step"]:
                    sys.stderr.write("\n")
                    streaming_step = None
                step = spec.step(ev["step"])
                if step.type not in ("input", "output"):
                    extra = ""
                    if ev.get("usage"):
                        u = ev["usage"]
                        extra = f", {u['input_tokens']}+{u['output_tokens']} tokens"
                    print(
                        f"✓ {step.name or step.id} ({ev['duration_ms']:.0f} ms{extra})",
                        file=sys.stderr,
                    )
            elif kind == "route" and not args.quiet:
                print(
                    f"↳ {spec.step(ev['step']).name or ev['step']}: took “{ev['exit']}”",
                    file=sys.stderr,
                )
            elif kind == "step_failed":
                err = ev["error"]
                _err(f"✗ {spec.step(ev['step']).name or ev['step']}: {err['message']}")
                if err.get("hint"):
                    _err(f"  {err['hint']}")
            final = ev
        if final.get("status") == "ok":
            if not args.events:
                print(json.dumps(final.get("output"), indent=2, ensure_ascii=False, default=str))
            return 0
        if not args.events:
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

    return asyncio.run(go())


def cmd_test(args: argparse.Namespace) -> int:
    from .testsets import run_test_set

    variables = dict(v.split("=", 1) for v in args.var or [])
    results = asyncio.run(run_test_set(args.file, variables, stand_in=args.stand_in))
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
        Path(args.output).write_text(text, encoding="utf-8")
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
    print(f"Easy Chain {__version__} running at http://{args.host}:{args.port}", flush=True)
    uvicorn.run(
        "easychain.server.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
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
    p.add_argument("--stand-in", action="store_true")
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
    p.add_argument("--reload", action="store_true")
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=cmd_dev)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
