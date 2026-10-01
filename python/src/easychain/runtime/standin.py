"""The stand-in AI: a deterministic chat model that needs no API key.

It lets people (and tests) run a flow end to end before they add a key. Replies
are clearly labelled and built from the prompt itself, so nothing is invented.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.messages.ai import UsageMetadata
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

LABEL = "[Stand-in AI: add an API key for real answers]"


def _text(message: BaseMessage) -> str:
    return message.text if hasattr(message, "text") else str(message.content)


def _sentences(text: str) -> list[str]:
    flat = re.sub(r"\s+", " ", text).strip()
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9“\"])", flat)
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


def _words(text: str) -> set[str]:
    # Crude stemming (first five letters) so "crashing" matches "crash".
    return {w[:5] for w in re.findall(r"[a-z]{4,}", text.lower()) if w not in _STOPWORDS}


def _pick_exit(system: str, human: str) -> str:
    exits = re.findall(r"^- ([^:\n]+)(?::\s*(.*))?$", system, flags=re.M)
    if not exits:
        return "Otherwise"
    words = _words(human)
    best, best_score = exits[-1][0], 0
    for label, desc in exits[:-1]:
        keys = _words(f"{label} {desc}")
        score = len(keys & words)
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


def _usage(messages: list[BaseMessage], reply: str) -> UsageMetadata:
    prompt_chars = sum(len(_text(m)) for m in messages)
    inp, out = max(1, prompt_chars // 4), max(1, len(reply) // 4)
    return UsageMetadata(input_tokens=inp, output_tokens=out, total_tokens=inp + out)


class StandInChatModel(BaseChatModel):
    """Answers from the prompt itself, word by word, so streaming looks real."""

    model_name: str = "stand-in"

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

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        reply = stand_in_reply(messages)
        message = AIMessage(content=reply, usage_metadata=_usage(messages, reply))
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        reply = stand_in_reply(messages)
        pieces = re.findall(r"\S+\s*|\s+", reply)
        for i, piece in enumerate(pieces):
            last = i == len(pieces) - 1
            chunk = AIMessageChunk(
                content=piece, usage_metadata=_usage(messages, reply) if last else None
            )
            yield ChatGenerationChunk(message=chunk)
