"""The stand-in AI: a deterministic chat model that needs no API key.

It lets people (and tests) run a flow end to end before they add a key. Replies
are clearly labelled and built from the prompt itself, so nothing is invented.

It also calls tools, so agents work without a key: it picks the tool whose name
and description best match the request, fills the arguments from the request,
and then answers from what the tools returned. Structured output (a forced tool
call) is filled the same way. A Test Set can script its turns exactly.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterator, Sequence
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.messages.ai import UsageMetadata
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool

LABEL = "[Stand-in AI: add an API key for real answers]"


def _text(message: BaseMessage) -> str:
    return message.text if hasattr(message, "text") else str(message.content)


def _sentences(text: str) -> list[str]:
    flat = re.sub(r"\s+", " ", text).strip()
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9“\"\[])", flat)
    return [p.strip() for p in parts if len(p.strip()) > 2]


_STOPWORDS = {
    "about",
    "above",
    "after",
    "again",
    "also",
    "because",
    "been",
    "before",
    "being",
    "could",
    "does",
    "doing",
    "from",
    "have",
    "here",
    "into",
    "just",
    "like",
    "more",
    "most",
    "none",
    "other",
    "over",
    "please",
    "same",
    "should",
    "some",
    "such",
    "tell",
    "than",
    "that",
    "their",
    "them",
    "then",
    "there",
    "these",
    "they",
    "this",
    "very",
    "want",
    "were",
    "what",
    "when",
    "where",
    "which",
    "while",
    "with",
    "would",
    "your",
}


def words(text: str) -> set[str]:
    """Content words of a text, crudely stemmed (first five letters) so "crashing" matches "crash"."""
    return {w[:5] for w in re.findall(r"[a-z]{4,}", text.lower()) if w not in _STOPWORDS}


def _pick_exit(system: str, human: str) -> str:
    exits = re.findall(r"^- ([^:\n]+)(?::\s*(.*))?$", system, flags=re.M)
    if not exits:
        return "Otherwise"
    found = words(human)
    best, best_score = exits[-1][0], 0
    for label, desc in exits[:-1]:
        keys = words(f"{label} {desc}")
        score = len(keys & found)
        if score > best_score:
            best, best_score = label, score
    return best.strip()


def stand_in_reply(messages: list[BaseMessage]) -> str:
    system = "\n".join(_text(m) for m in messages if m.type == "system")
    humans = [_text(m) for m in messages if m.type == "human"]
    human = humans[-1] if humans else ""
    if "Reply with the exit name only" in system:
        return _pick_exit(system, human)
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", human) if p.strip()]
    if len(paragraphs) > 1:
        material = " ".join(paragraphs[1:])
        bullets = _sentences(material)[:3]
        if bullets:
            return LABEL + "\n" + "\n".join(f"- {b[:240]}" for b in bullets)
    if not human:
        return f"{LABEL}\nHello! I'm a stand-in, so I can only repeat what I'm given."
    short = human if len(human) < 300 else human[:297] + "..."
    return f"{LABEL}\nYou said: “{short}”"


# ── tools and structured output ──────────────────────────────────────────────


class Script:
    """Scripted turns for the stand-in AI, shared by every model call in one run.

    Each turn is one of::

        "Some text"                                  # a plain reply
        {"say": "Some text"}
        {"call": "tool_name", "args": {...}}         # one tool call
        {"calls": [{"name": ..., "args": {...}}]}    # several at once
        {"answer": {...}}                            # the structured answer

    When the script runs out, the stand-in carries on by itself.
    """

    def __init__(self, turns: Sequence[Any] | None = None):
        self.turns = list(turns or [])

    def next(self) -> Any:
        return self.turns.pop(0) if self.turns else None

    def __bool__(self) -> bool:
        return bool(self.turns)


def _tool_spec(tool: Any) -> dict[str, Any]:
    spec = tool if isinstance(tool, dict) and "function" in tool else convert_to_openai_tool(tool)
    fn = spec.get("function", spec)
    return {
        "name": fn.get("name", ""),
        "description": fn.get("description", "") or "",
        "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
    }


def _forced_name(tool_choice: Any) -> str | None:
    if isinstance(tool_choice, dict):
        return (tool_choice.get("function") or {}).get("name") or tool_choice.get("name")
    if isinstance(tool_choice, str) and tool_choice not in ("auto", "any", "required", "none"):
        return tool_choice
    return None


_QUERY_NAMES = re.compile(
    r"^(query|question|q|search|search_query|text|input|topic|message|prompt|request|term)s?$"
)


def _numbers(text: str) -> list[str]:
    return re.findall(r"-?\d+(?:\.\d+)?", text)


def _best_option(options: list[Any], text: str) -> Any:
    found = words(text)
    best, best_score = options[0], 0
    for option in options:
        score = len(words(str(option)) & found)
        if str(option).lower() in text.lower():
            score += 2
        if score > best_score:
            best, best_score = option, score
    return best


def _named_value(name: str, text: str) -> str | None:
    """`name: value` or `name = value` written in the request."""
    label = name.replace("_", "[ _]")
    match = re.search(rf"\b{label}\s*[:=]\s*[\"“']?([^\"”'\n,;]+)", text, flags=re.I)
    return match.group(1).strip() if match else None


def _schema_value(name: str, schema: dict[str, Any], ctx: dict[str, Any], defs: dict) -> Any:
    if "$ref" in schema:
        schema = defs.get(schema["$ref"].split("/")[-1], {})
    if "anyOf" in schema:
        options = [s for s in schema["anyOf"] if s.get("type") != "null"]
        schema = options[0] if options else {}
    kind = schema.get("type")
    question, answer = ctx["question"], ctx["answer"]
    if "enum" in schema:
        return _best_option(list(schema["enum"]), f"{answer} {question}")
    if kind in ("number", "integer"):
        found = _numbers(ctx["results"]) or _numbers(question)
        if not found:
            return 0 if ctx["final"] else 1
        return int(float(found[0])) if kind == "integer" else float(found[0])
    if kind == "boolean":
        return not re.search(r"\b(no|not|never|cannot|can't|isn't)\b", answer.lower())
    if kind == "array":
        item = schema.get("items") or {}
        if item.get("type") == "object" or "$ref" in item:
            return []
        if ctx["final"]:
            return [s for s in _sentences(answer.removeprefix(LABEL))[:3]] or [answer]
        return [question]
    if kind == "object":
        return {}
    named = _named_value(name, question)
    if named:
        return named
    if ctx["final"]:
        lowered = name.lower()
        if any(k in lowered for k in ("sql", "query", "code")):
            for args in reversed(ctx["calls"]):
                for key, value in args.items():
                    if any(k in key.lower() for k in ("sql", "query", "code")):
                        return value
        return answer
    quoted = re.search(r"[\"“']([^\"”']{2,})[\"”']", question)
    if _QUERY_NAMES.match(name.lower()) or ctx["single_string"] or not quoted:
        return question
    return quoted.group(1)


def fill_arguments(
    parameters: dict[str, Any],
    question: str,
    *,
    answer: str = "",
    results: str = "",
    calls: list[dict[str, Any]] | None = None,
    final: bool = False,
) -> dict[str, Any]:
    """Fill a JSON schema's required properties (all of them for a final answer)."""
    props: dict[str, Any] = parameters.get("properties") or {}
    required = parameters.get("required") or list(props)
    defs = parameters.get("$defs") or parameters.get("definitions") or {}
    strings = [n for n, s in props.items() if s.get("type") == "string" and "enum" not in s]
    ctx = {
        "question": question,
        "answer": answer or question,
        "results": results,
        "calls": calls or [],
        "final": final,
        "single_string": len(strings) == 1,
    }
    names = list(props) if final else [n for n in props if n in required]
    return {name: _schema_value(name, props[name], ctx, defs) for name in names}


def _blocks(text: str) -> list[tuple[str, str]]:
    """Split tool output into (citation marker, text) blocks; "[1] ..." starts a source."""
    parts = re.split(r"(?m)^(?=\[\d+\])", text)
    out = []
    for part in parts:
        match = re.match(r"\[(\d+)\]\s*", part)
        out.append((f"[{match.group(1)}]" if match else "", part[match.end() :] if match else part))
    return out


def answer_from_results(question: str, results: list[tuple[str, str]]) -> str:
    """Answer from what the tools returned, citing numbered sources when there are any."""
    if not results:
        return stand_in_reply([])
    found = words(question)
    scored: list[tuple[int, int, str]] = []
    order = 0
    for _tool, text in results:
        for marker, block in _blocks(text):
            lines = block.split("\n", 1)
            body = lines[1] if len(lines) > 1 and marker else block
            for sentence in _sentences(body):
                score = len(words(sentence) & found)
                cited = f"{sentence.rstrip()} {marker}".strip() if marker else sentence
                scored.append((score, -order, cited))
                order += 1
    if not scored:
        tool, text = results[-1]
        return f"{LABEL}\n{tool} returned: {text.strip()[:400]}"
    best = [b for b in sorted(scored, reverse=True)[:2] if b[0] > 0]
    if not best:
        tool, text = results[-1]
        short = text.strip()
        return f"{LABEL}\n{tool} returned: {short[:400]}{'…' if len(short) > 400 else ''}"
    picked = [s for _score, _o, s in sorted(best, key=lambda t: -t[1])]
    return f"{LABEL}\n" + " ".join(picked)


def _tool_call(name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "args": args, "id": f"call_{uuid.uuid4().hex[:12]}", "type": "tool_call"}


def stand_in_turn(
    messages: list[BaseMessage],
    tools: list[Any] | None = None,
    tool_choice: Any = None,
    script: Script | None = None,
) -> AIMessage:
    """The stand-in's next message: a reply, or tool calls when tools are bound."""
    specs = [_tool_spec(t) for t in tools or []]
    by_name = {s["name"]: s for s in specs}
    humans = [i for i, m in enumerate(messages) if m.type == "human"]
    start = humans[-1] + 1 if humans else 0
    question = _text(messages[humans[-1]]) if humans else ""
    turn = messages[start:]
    calls = [tc for m in turn if isinstance(m, AIMessage) for tc in (m.tool_calls or [])]
    results = [(getattr(m, "name", "") or "tool", _text(m)) for m in turn if m.type == "tool"]
    forced = _forced_name(tool_choice)
    must_call = forced is not None or tool_choice in ("any", "required")
    output_tool = by_name.get(forced or "") or (specs[-1] if specs and must_call else None)

    def structured(answer: str) -> AIMessage:
        assert output_tool is not None
        args = fill_arguments(
            output_tool["parameters"],
            question,
            answer=answer,
            results="\n".join(t for _n, t in results),
            calls=[c["args"] for c in calls],
            final=True,
        )
        return AIMessage(content="", tool_calls=[_tool_call(output_tool["name"], args)])

    scripted = script.next() if script else None
    if scripted is not None:
        if isinstance(scripted, str):
            scripted = {"say": scripted}
        if "call" in scripted or "calls" in scripted:
            wanted = scripted.get("calls") or [
                {"name": scripted["call"], "args": scripted.get("args") or {}}
            ]
            return AIMessage(
                content="",
                tool_calls=[_tool_call(c["name"], dict(c.get("args") or {})) for c in wanted],
            )
        if "answer" in scripted and output_tool is not None:
            args = dict(scripted["answer"])
            return AIMessage(content="", tool_calls=[_tool_call(output_tool["name"], args)])
        text = str(scripted.get("say", scripted.get("answer", "")))
        if must_call and output_tool is not None:
            return structured(text)
        return AIMessage(content=text)

    if forced is not None and output_tool is not None:
        return structured(answer_from_results(question, results) if results else question)
    regular = specs[:-1] if (must_call and specs) else specs
    if regular and not calls:
        found = words(question)
        best, best_score = None, 0
        for spec in regular:
            score = len(words(f"{spec['name'].replace('_', ' ')} {spec['description']}") & found)
            if score > best_score:
                best, best_score = spec, score
        if best is None:
            # Nothing matches by name: a tool that takes a search query is the safest bet.
            best = next(
                (
                    s
                    for s in regular
                    if any(
                        _QUERY_NAMES.match(p.lower()) for p in s["parameters"].get("properties", {})
                    )
                ),
                None,
            )
        if best is not None and question.strip():
            return AIMessage(
                content="",
                tool_calls=[_tool_call(best["name"], fill_arguments(best["parameters"], question))],
            )
    answer = answer_from_results(question, results) if results else stand_in_reply(messages)
    if must_call and output_tool is not None:
        return structured(answer)
    return AIMessage(content=answer)


def _usage(messages: list[BaseMessage], reply: str) -> UsageMetadata:
    prompt_chars = sum(len(_text(m)) for m in messages)
    inp, out = max(1, prompt_chars // 4), max(1, len(reply) // 4)
    return UsageMetadata(input_tokens=inp, output_tokens=out, total_tokens=inp + out)


class StandInChatModel(BaseChatModel):
    """Answers from the prompt itself, word by word, so streaming looks real."""

    model_name: str = "stand-in"
    # Scripted turns (a Script object shared by every model call in the run).
    script: Any = None

    @property
    def _llm_type(self) -> str:
        return "easychain-stand-in"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {"model_name": self.model_name}

    def _get_ls_params(self, stop: list[str] | None = None, **kwargs: Any) -> Any:
        params = super()._get_ls_params(stop=stop, **kwargs)
        params["ls_provider"] = "easychain-stand-in"
        params["ls_model_name"] = "stand-in"
        return params

    def bind_tools(self, tools: Sequence[Any], *, tool_choice: Any = None, **kwargs: Any) -> Any:
        formatted = [convert_to_openai_tool(t) for t in tools]
        return self.bind(tools=formatted, tool_choice=tool_choice, **kwargs)

    def _message(self, messages: list[BaseMessage], **kwargs: Any) -> AIMessage:
        tools = kwargs.get("tools")
        if not tools and not self.script:
            reply = stand_in_reply(messages)
            return AIMessage(content=reply, usage_metadata=_usage(messages, reply))
        message = stand_in_turn(messages, tools, kwargs.get("tool_choice"), self.script)
        size = message.text + json.dumps([c["args"] for c in message.tool_calls], default=str)
        message.usage_metadata = _usage(messages, size)
        return message

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._message(messages, **kwargs))])

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        message = self._message(messages, **kwargs)
        if message.tool_calls:
            chunks = [
                {"name": c["name"], "args": json.dumps(c["args"]), "id": c["id"], "index": i}
                for i, c in enumerate(message.tool_calls)
            ]
            yield ChatGenerationChunk(
                message=AIMessageChunk(
                    content="", tool_call_chunks=chunks, usage_metadata=message.usage_metadata
                )
            )
            return
        reply = message.text
        pieces = re.findall(r"\S+\s*|\s+", reply) or [""]
        for i, piece in enumerate(pieces):
            last = i == len(pieces) - 1
            chunk = AIMessageChunk(
                content=piece, usage_metadata=message.usage_metadata if last else None
            )
            yield ChatGenerationChunk(message=chunk)
