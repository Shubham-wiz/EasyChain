"""A tiny OpenAI-compatible server for tests and offline demos.

It speaks enough of ``/v1/chat/completions`` (streaming and not, with tool calls
and JSON-schema replies) and ``/v1/embeddings`` for ``langchain-openai`` to work
unchanged, and serves sample web pages under ``/pages/<name>``. Point
``OPENAI_BASE_URL`` at it and set any ``OPENAI_API_KEY`` (the key ``sk-bad`` is
rejected, to test error handling). Replies come from the stand-in AI;
``POST /__script`` with a list of turns scripts them (see ``standin.Script``).

    python -m easychain.testing.fake_openai --port 8765
"""

from __future__ import annotations

import argparse
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from ..knowledge.embeddings import KeywordEmbeddings
from ..runtime.standin import Script, fill_arguments, stand_in_reply, stand_in_turn

PAGES = {
    "langchain": """<!doctype html><html><head><title>LangChain</title>
<style>body{font-family:sans-serif}</style><script>console.log('menu')</script></head>
<body><nav>Home | About | Contact</nav>
<h1>LangChain</h1>
<p>LangChain is an open-source framework for building applications with large language models.
It provides standard interfaces for chat models, prompts, tools and retrievers.
LangGraph, its sister project, runs stateful agents as graphs with durable execution.
Developers use LangSmith to trace and evaluate what their apps do.</p>
<p>The project started in 2022 and has a large community of contributors.</p>
</body></html>""",
    "pricing": """<html><body><h1>Pricing</h1><p>The starter plan costs ten dollars a month.
The team plan adds shared workspaces. Enterprise plans include single sign-on.</p></body></html>""",
}


def _page(title: str, *sentences: str) -> str:
    body = " ".join(sentences)
    return f"<html><head><title>{title}</title></head><body><h1>{title}</h1><p>{body}</p></body></html>"


PAGES.update(
    {
        "bees": _page(
            "Honey bees",
            "Honey bees live in colonies of thousands.",
            "Worker bees collect nectar and pollen.",
            "The queen lays all the eggs.",
        ),
        "volcano": _page(
            "Volcanoes",
            "A volcano is an opening in the crust.",
            "Magma rises from below and erupts as lava.",
            "Some volcanoes sleep for centuries.",
        ),
        "coffee": _page(
            "Coffee",
            "Coffee is brewed from roasted beans.",
            "Arabica and robusta are the main species.",
            "Brazil grows the most coffee.",
        ),
        "chess": _page(
            "Chess",
            "Chess is a board game for two players.",
            "Each side starts with sixteen pieces.",
            "The goal is to checkmate the king.",
        ),
        "mars": _page(
            "Mars",
            "Mars is the fourth planet from the Sun.",
            "Its red colour comes from iron oxide.",
            "Rovers have explored its surface.",
        ),
        "bread": _page(
            "Sourdough",
            "Sourdough rises with wild yeast.",
            "A starter is fed with flour and water.",
            "Long fermentation gives a tangy taste.",
        ),
        "python": _page(
            "Python",
            "Python is a programming language.",
            "It is known for readable code.",
            "Guido van Rossum created it.",
        ),
        "ocean": _page(
            "Oceans",
            "Oceans cover most of the Earth.",
            "The Pacific is the largest ocean.",
            "Currents move heat around the planet.",
        ),
        "long": "<html><body><h1>A very long article</h1>"
        + "".join(
            f"<p>Paragraph {i} explains another detail of the long article in plain words. "
            "It keeps going so that the page has well over eight hundred words in total.</p>"
            for i in range(60)
        )
        + "</body></html>",
    }
)

requests_log: list[dict[str, Any]] = []
# Scripted turns for the next requests (POST /__script).
script = Script()
# Side effects received at /effects: every attempt, and the ones that took effect.
effects: dict[str, list[dict[str, Any]]] = {"calls": [], "applied": []}


def _to_messages(raw: list[dict[str, Any]]) -> list[Any]:
    out: list[Any] = []
    names: dict[str, str] = {}
    for m in raw:
        content = m.get("content") or ""
        if isinstance(content, list):
            content = " ".join(part.get("text", "") for part in content if isinstance(part, dict))
        role = m.get("role")
        if role == "system" or role == "developer":
            out.append(SystemMessage(content))
        elif role == "assistant":
            calls = []
            for call in m.get("tool_calls") or []:
                fn = call.get("function") or {}
                names[call.get("id", "")] = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                calls.append({"name": fn.get("name", ""), "args": args, "id": call.get("id")})
            out.append(AIMessage(content, tool_calls=calls))
        elif role == "tool":
            call_id = m.get("tool_call_id", "")
            out.append(ToolMessage(content, tool_call_id=call_id, name=names.get(call_id)))
        else:
            out.append(HumanMessage(content))
    return out


def _relabel(text: str, model: str) -> str:
    return text.replace("[Stand-in AI: add an API key for real answers]", f"[fake {model}]")


def fake_reply(raw_messages: list[dict[str, Any]], model: str) -> str:
    return _relabel(stand_in_reply(_to_messages(raw_messages)), model)


def fake_message(body: dict[str, Any]) -> dict[str, Any]:
    """The assistant message for a chat completion request: text, tool calls or JSON."""
    model = body.get("model", "gpt-4o-mini")
    messages = _to_messages(body.get("messages", []))
    tools = body.get("tools") or None
    fmt = body.get("response_format") or {}
    if fmt.get("type") == "json_schema":
        schema = (fmt.get("json_schema") or {}).get("schema") or {}
        humans = [m.text for m in messages if m.type == "human"]
        answer = _relabel(stand_in_reply(messages), model)
        args = fill_arguments(schema, humans[-1] if humans else "", answer=answer, final=True)
        return {"role": "assistant", "content": json.dumps(args)}
    if not tools and not script:
        return {"role": "assistant", "content": fake_reply(body.get("messages", []), model)}
    reply = stand_in_turn(messages, tools, body.get("tool_choice"), script)
    if reply.tool_calls:
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": call["id"],
                    "type": "function",
                    "function": {
                        "name": call["name"],
                        "arguments": json.dumps(
                            {
                                k: _relabel(v, model) if isinstance(v, str) else v
                                for k, v in call["args"].items()
                            }
                        ),
                    },
                }
                for call in reply.tool_calls
            ],
        }
    return {"role": "assistant", "content": _relabel(reply.text, model)}


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args: Any) -> None:  # keep test output quiet
        pass

    def _send(self, status: int, body: bytes, content_type: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        match = re.match(r"^/pages/([a-z0-9_-]+)", self.path)
        if match and match.group(1) in PAGES:
            self._send(200, PAGES[match.group(1)].encode(), "text/html; charset=utf-8")
        elif self.path.startswith("/__requests"):
            self._send(200, json.dumps(requests_log).encode())
        elif self.path.startswith("/effects"):
            self._send(200, json.dumps(effects).encode())
        elif self.path.startswith("/health"):
            self._send(200, b'{"ok": true}')
        else:
            self._send(404, b'{"error": "not found"}')

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path.startswith("/effects"):
            self._effect(body)
            return
        if self.path.startswith("/__script"):
            script.turns[:] = list(body if isinstance(body, list) else body.get("turns", []))
            self._send(200, b'{"ok": true}')
            return
        if self.path.rstrip("/").endswith("/embeddings"):
            self._embeddings(body)
            return
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._send(404, b'{"error": {"message": "not found"}}')
            return
        requests_log.append({"path": self.path, "body": body})
        if self.headers.get("Authorization", "") == "Bearer sk-bad":
            err = {
                "error": {
                    "message": "Incorrect API key provided",
                    "type": "invalid_request_error",
                    "code": "invalid_api_key",
                }
            }
            self._send(401, json.dumps(err).encode())
            return
        model = body.get("model", "gpt-4o-mini")
        message = fake_message(body)
        reply = message.get("content") or json.dumps(message.get("tool_calls"))
        finish_reason = "tool_calls" if message.get("tool_calls") else "stop"
        prompt_tokens = (
            sum(len(str(m.get("content", ""))) for m in body.get("messages", [])) // 4 + 1
        )
        usage = {"prompt_tokens": prompt_tokens, "completion_tokens": len(reply) // 4 + 1}
        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
        created = int(time.time())
        if not body.get("stream"):
            payload = {
                "id": "chatcmpl-fake",
                "object": "chat.completion",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
                "usage": usage,
            }
            self._send(200, json.dumps(payload).encode())
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

        def chunk(
            delta: dict[str, Any], finish: str | None = None, extra: dict | None = None
        ) -> None:
            data = {
                "id": "chatcmpl-fake",
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]
                if delta is not None
                else [],
            }
            if extra:
                data.update(extra)
            self.wfile.write(f"data: {json.dumps(data)}\n\n".encode())
            self.wfile.flush()

        chunk({"role": "assistant", "content": ""})
        if message.get("tool_calls"):
            calls = [{**call, "index": i} for i, call in enumerate(message["tool_calls"])]
            chunk({"tool_calls": calls})
        else:
            for piece in re.findall(r"\S+\s*|\s+", reply):
                chunk({"content": piece})
        chunk({}, finish_reason)
        # Real OpenAI only sends usage when asked; sending it always lets tests check costs.
        chunk(None, extra={"usage": usage})  # type: ignore[arg-type]
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()
        self.close_connection = True

    def _embeddings(self, body: dict[str, Any]) -> None:
        """Keyword embeddings (see knowledge.embeddings), so search works offline."""
        texts = body.get("input") or []
        if isinstance(texts, str):
            texts = [texts]
        embedder = KeywordEmbeddings(int(body.get("dimensions") or 256))
        data = [
            {"object": "embedding", "index": i, "embedding": embedder.embed_query(str(text))}
            for i, text in enumerate(texts)
        ]
        tokens = sum(len(str(t)) // 4 + 1 for t in texts)
        payload = {
            "object": "list",
            "data": data,
            "model": body.get("model", "text-embedding-3-small"),
            "usage": {"prompt_tokens": tokens, "total_tokens": tokens},
        }
        self._send(200, json.dumps(payload).encode())

    def _effect(self, body: Any) -> None:
        """A side effect (like sending an email) that honours Idempotency-Key, as Stripe does.

        ``?fail=N`` fails the first N attempts for a key with a 500, to exercise retries.
        """
        if self.path.startswith("/effects/reset"):
            effects["calls"].clear()
            effects["applied"].clear()
            self._send(200, b'{"ok": true}')
            return
        key = self.headers.get("Idempotency-Key")
        effects["calls"].append({"key": key, "body": body})
        match = re.search(r"[?&]fail=(\d+)", self.path)
        attempts = sum(1 for c in effects["calls"] if c["key"] == key)
        if match and attempts <= int(match.group(1)):
            self._send(500, b'{"error": "try again"}')
            return
        for applied in effects["applied"]:
            if key and applied["key"] == key:
                self._send(200, json.dumps({**applied["receipt"], "replayed": True}).encode())
                return
        receipt = {"id": f"effect-{len(effects['applied']) + 1}", "received": body}
        effects["applied"].append({"key": key, "body": body, "receipt": receipt})
        self._send(200, json.dumps(receipt).encode())


class FakeOpenAI:
    """Run the fake server in a background thread: ``with FakeOpenAI() as fake: fake.url``."""

    def __init__(self, port: int = 0):
        self.server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    @property
    def base_url(self) -> str:
        return self.url + "/v1"

    def __enter__(self) -> FakeOpenAI:
        requests_log.clear()
        script.turns.clear()
        effects["calls"].clear()
        effects["applied"].clear()
        self.thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.server.shutdown()
        self.server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), _Handler)
    print(f"Fake OpenAI listening on http://127.0.0.1:{args.port}/v1", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
