"""The stand-in AI calls tools and fills structured answers, so agents run without a key."""

from __future__ import annotations

from typing import Literal

import httpx
from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from pydantic import BaseModel, Field

from easychain.runtime.standin import (
    LABEL,
    Script,
    StandInChatModel,
    answer_from_results,
    fill_arguments,
)
from easychain.testing.fake_openai import FakeOpenAI


@tool
def search_docs(query: str) -> str:
    """Search the product documentation."""
    return (
        "[1] Resetting your password\nOpen Settings and choose Reset password. "
        "You get an email within five minutes.\n\n[2] Billing\nInvoices are sent monthly."
    )


@tool
def get_weather(city: str) -> str:
    """Get the weather for a city."""
    return f"It is sunny in {city}."


class Answer(BaseModel):
    """The final answer."""

    answer: str = Field(description="The answer")
    topic: Literal["account", "billing", "other"]
    confident: bool


def _ask(agent, text: str) -> dict:
    return agent.invoke({"messages": [{"role": "user", "content": text}]})


def test_picks_the_matching_tool_and_cites_what_it_found():
    agent = create_agent(StandInChatModel(), tools=[search_docs, get_weather])
    out = _ask(agent, "How do I reset my password?")
    call = out["messages"][1].tool_calls[0]
    assert call["name"] == "search_docs"
    assert call["args"] == {"query": "How do I reset my password?"}
    final = out["messages"][-1].text
    assert final.startswith(LABEL)
    assert "Reset password" in final and "[1]" in final
    assert "Invoices" not in final


def test_tool_matched_by_name_and_arguments_from_the_request():
    agent = create_agent(StandInChatModel(), tools=[search_docs, get_weather])
    out = _ask(agent, "What's the weather? city: Lisbon")
    call = out["messages"][1].tool_calls[0]
    assert call == {**call, "name": "get_weather", "args": {"city": "Lisbon"}}
    assert "sunny in Lisbon" in out["messages"][-1].text


def test_structured_answer_through_tool_strategy_and_with_structured_output():
    agent = create_agent(
        StandInChatModel(), tools=[search_docs], response_format=ToolStrategy(Answer)
    )
    result = _ask(agent, "How do I reset my password for my account?")["structured_response"]
    assert isinstance(result, Answer)
    assert result.topic == "account" and "Reset password" in result.answer

    billing = StandInChatModel().with_structured_output(Answer).invoke("A billing question")
    assert billing.topic == "billing"


def test_scripted_turns_come_first_then_the_stand_in_carries_on():
    script = Script([{"call": "get_weather", "args": {"city": "Paris"}}, "Sunny in Paris."])
    agent = create_agent(StandInChatModel(script=script), tools=[search_docs, get_weather])
    out = _ask(agent, "weather?")
    assert out["messages"][1].tool_calls[0]["args"] == {"city": "Paris"}
    assert out["messages"][-1].text == "Sunny in Paris."
    assert not script
    # Out of script: back to the stand-in's own behaviour.
    assert _ask(agent, "reset password")["messages"][1].tool_calls[0]["name"] == "search_docs"


def test_scripted_structured_answer():
    script = Script([{"answer": {"answer": "Done", "topic": "other", "confident": False}}])
    agent = create_agent(
        StandInChatModel(script=script), tools=[search_docs], response_format=ToolStrategy(Answer)
    )
    assert _ask(agent, "anything")["structured_response"] == Answer(
        answer="Done", topic="other", confident=False
    )


def test_streams_tool_calls():
    agent = create_agent(StandInChatModel(), tools=[search_docs])
    chunks = list(
        agent.stream(
            {"messages": [{"role": "user", "content": "reset password"}]}, stream_mode="messages"
        )
    )
    assert any(getattr(m, "tool_call_chunks", None) for m, _meta in chunks)


def test_fill_arguments_by_type():
    schema = {
        "type": "object",
        "properties": {
            "limit": {"type": "integer"},
            "country": {"type": "string", "enum": ["Germany", "France"]},
            "verbose": {"type": "boolean"},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["limit", "country", "verbose", "tags"],
    }
    args = fill_arguments(schema, "Top 5 customers in France")
    assert args == {
        "limit": 5,
        "country": "France",
        "verbose": True,
        "tags": ["Top 5 customers in France"],
    }


def test_answer_without_matching_sentences_quotes_the_tool():
    text = answer_from_results("zebra", [("run_query", '[{"count": 3}]')])
    assert "run_query returned" in text and "count" in text


def test_fake_openai_server_does_tools_json_and_embeddings():
    with FakeOpenAI() as fake:
        model = ChatOpenAI(model="gpt-4o-mini", base_url=fake.base_url, api_key="sk-test")
        agent = create_agent(model, tools=[search_docs])
        out = _ask(agent, "How do I reset my password?")
        assert out["messages"][1].tool_calls[0]["name"] == "search_docs"
        assert out["messages"][-1].text.startswith("[fake gpt-4o-mini]")
        assert model.with_structured_output(Answer).invoke("billing please").topic == "billing"

        httpx.post(f"{fake.url}/__script", json=["Scripted hello."])
        assert model.invoke("hi").text == "Scripted hello."
        assert model.invoke("hi").text.startswith("[fake")

        embeddings = OpenAIEmbeddings(
            model="text-embedding-3-small",
            base_url=fake.base_url,
            api_key="sk-test",
            check_embedding_ctx_length=False,
        )
        vectors = embeddings.embed_documents(["reset password", "monthly invoices"])
        query = embeddings.embed_query("password reset")
        dot = [sum(a * b for a, b in zip(query, v, strict=True)) for v in vectors]
        assert len(vectors[0]) == 256 and dot[0] > dot[1]
