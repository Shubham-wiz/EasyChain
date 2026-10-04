"""Memory, Database query, MCP servers and OpenAPI import."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from easychain.compiler import compile_flow
from easychain.compiler.validate import validate
from easychain.integrations import mcp as mcp_module
from easychain.integrations import openapi
from easychain.integrations.sql import SQL_ENGINES, describe_database, run_sql
from easychain.runtime import RunOptions, run_flow
from easychain.runtime.resources import open_resources
from easychain.runtime.standin import Script
from easychain.server.app import create_app
from easychain.templates.samples import ensure_samples

from . import pg
from .conftest import input_step, make_spec, output_step


def of(events: list[dict], kind: str) -> list[dict]:
    return [e for e in events if e["type"] == kind]


@pytest.fixture
def shop(tmp_path, monkeypatch):
    monkeypatch.setenv("EASYCHAIN_HOME", str(tmp_path))
    path = ensure_samples(tmp_path)
    yield "sqlite:///{home}/samples/shop.db"
    SQL_ENGINES.clear()
    assert path.exists()


# ── Database query ───────────────────────────────────────────────────────────


def test_sample_shop_is_built_once(tmp_path):
    first = ensure_samples(tmp_path)
    stamp = first.stat().st_mtime_ns
    assert ensure_samples(tmp_path).stat().st_mtime_ns == stamp


def test_run_sql_reads_safely(shop):
    rows = run_sql(
        shop, "SELECT country, COUNT(*) AS n FROM customers GROUP BY country ORDER BY country"
    )
    assert rows[0] == {"country": "France", "n": 3}
    assert (
        run_sql(shop, "SELECT * FROM customers WHERE country = :c", {"c": "Spain"}, max_rows=2)[1][
            "country"
        ]
        == "Spain"
    )
    assert len(run_sql(shop, "SELECT * FROM orders", max_rows=5)) == 5
    with pytest.raises(ValueError, match="read-only"):
        run_sql(shop, "DELETE FROM orders")
    with pytest.raises(ValueError, match="one SQL statement"):
        run_sql(shop, "SELECT 1; DROP TABLE orders")
    # A write hidden behind WITH is stopped by the read-only transaction itself.
    with pytest.raises(Exception, match="readonly|read-only|attempt to write"):
        run_sql(shop, "WITH x AS (SELECT 1) INSERT INTO products SELECT 99, 'x', 'y', 1 FROM x")
    assert run_sql(shop, "SELECT COUNT(*) AS n FROM products") == [{"n": 10}]
    changed = run_sql(shop, "UPDATE products SET price = price WHERE id = 1", read_only=False)
    assert changed == [{"rows_changed": 1}]
    schema = describe_database(shop)
    assert "orders (45 rows): id INTEGER primary key" in schema
    assert "customer_id references customers.id" in schema


def test_changes_that_return_rows_are_saved(shop):
    added = run_sql(
        shop,
        "INSERT INTO products (id, name, category, price) VALUES (99, 'Mug', 'gear', 9) "
        "RETURNING id",
        read_only=False,
    )
    assert added == [{"id": 99}]
    assert run_sql(shop, "SELECT name FROM products WHERE id = 99") == [{"name": "Mug"}]


def test_read_only_queries_cant_change_settings(shop):
    for pragma in ("PRAGMA ignore_check_constraints = ON", "PRAGMA foreign_keys = OFF"):
        with pytest.raises(ValueError, match="PRAGMA"):
            run_sql(shop, pragma)
    with pytest.raises(ValueError, match="PRAGMA"):
        run_sql(shop, "PRAGMA writable_schema")
    assert run_sql(shop, "PRAGMA table_info(products)")[0]["name"] == "id"
    # Semicolons and keywords inside quoted text are just text.
    assert run_sql(shop, "SELECT 'a;b' AS t") == [{"t": "a;b"}]


def test_read_only_is_strict_where_there_is_no_read_only_transaction():
    from easychain.integrations.sql import check_read_only_sql

    check_read_only_sql("SELECT name FROM t WHERE note = 'please delete'", "mssql")
    with pytest.raises(ValueError, match="read-only"):
        check_read_only_sql("WITH x AS (SELECT 1) DELETE FROM orders", "mssql")
    # SQLite, Postgres and MySQL have a read-only transaction, which stops such a write.
    check_read_only_sql("WITH x AS (SELECT 1) DELETE FROM orders", "mysql")


@pytest.mark.skipif(not pg.available(), reason="Postgres isn't installed")
def test_run_sql_read_only_on_postgres():
    with pg.server() as url:
        run_sql(url, "CREATE TABLE notes (id int, body text)", read_only=False)
        run_sql(url, "INSERT INTO notes VALUES (1, 'hi')", read_only=False)
        assert run_sql(url, "SELECT body FROM notes") == [{"body": "hi"}]
        with pytest.raises(Exception, match="read-only transaction"):
            run_sql(url, "WITH x AS (DELETE FROM notes RETURNING 1) SELECT * FROM x")
        # A change that returns rows is committed.
        added = run_sql(url, "INSERT INTO notes VALUES (2, 'yo') RETURNING id", read_only=False)
        assert added == [{"id": 2}]
        assert run_sql(url, "SELECT COUNT(*) AS n FROM notes") == [{"n": 2}]
        SQL_ENGINES.clear()


def _sql_flow(connection: str, query: str, **settings):
    return make_spec(
        [
            input_step("country"),
            {
                "id": "lookup",
                "type": "sql_query",
                "settings": {"connection": connection, "query": query, **settings},
            },
            output_step("rows"),
        ],
        [("input", "lookup"), ("lookup", "output")],
    )


async def test_sql_step_sends_fields_as_parameters(shop):
    spec = _sql_flow(shop, "SELECT name FROM customers WHERE country = '{country}' ORDER BY name")
    source = compile_flow(spec).source
    assert "WHERE country = :country" in source
    final, _ = await run_flow(spec, {"country": "Germany' OR '1'='1"}, RunOptions())
    assert final["status"] == "ok" and final["output"]["rows"] == []
    final, _ = await run_flow(spec, {"country": "Germany"}, RunOptions())
    assert len(final["output"]["rows"]) == 3


def test_sql_checks(shop):
    spec = _sql_flow("postgresql://me:hunter2@db/shop", "SELECT {missing}")
    codes = {i.code for i in validate(spec)}
    assert {"sql_password_in_url", "missing_field"} <= codes
    assert "sql_no_connection" in {i.code for i in validate(_sql_flow("", "SELECT 1"))}


async def test_agent_writes_sql_with_a_schema_tool(shop):
    spec = make_spec(
        [
            input_step("question"),
            {
                "id": "analyst",
                "type": "agent",
                "settings": {"input": "question", "tools": ["tables", "run_query"]},
            },
            {
                "id": "tables",
                "type": "sql_query",
                "description": "Lists the tables and columns.",
                "settings": {"connection": shop, "mode": "schema", "save_as": "schema"},
            },
            {
                "id": "run_query",
                "type": "sql_query",
                "description": "Runs one read-only SQLite query.",
                "settings": {"connection": shop, "query": "{sql}"},
            },
            output_step("answer"),
        ],
        [("input", "analyst"), ("analyst", "output")],
        data=[{"name": "sql", "description": "One SQLite SELECT statement."}],
    )
    assert (
        'def run_tool(sql: Annotated[str, "One SQLite SELECT statement."])'
        in compile_flow(spec).source
    )
    script = Script(
        [
            {"call": "tables"},
            {
                "call": "run_query",
                "args": {"sql": "SELECT COUNT(*) AS n FROM customers WHERE country = 'Germany'"},
            },
            "There are 3 customers in Germany.",
        ]
    )
    final, events = await run_flow(
        spec,
        {"question": "How many customers in Germany?"},
        RunOptions(stand_in=True, script=script),
    )
    assert final["status"] == "ok", final
    results = [e["result"] for e in of(events, "tool_finished")]
    assert "customers (20 rows)" in results[0]
    assert json.loads(results[1]) == {"n": 3}


# ── Memory ───────────────────────────────────────────────────────────────────


async def test_memory_steps_remember_and_recall_per_user(tmp_path):
    remember = make_spec(
        [
            input_step("fact", "user_id"),
            {"id": "keep", "type": "memory", "settings": {"action": "remember", "text": "fact"}},
            output_step(),
        ],
        [("input", "keep"), ("keep", "output")],
    )
    recall = make_spec(
        [
            input_step("question", "user_id"),
            {"id": "look", "type": "memory", "settings": {"action": "recall", "limit": 1}},
            output_step("memories"),
        ],
        [("input", "look"), ("look", "output")],
    )
    res = await open_resources(f"sqlite:///{tmp_path / 'mem.db'}")
    try:
        for fact in ("Prefers email", "Lives in Porto"):
            final, _ = await run_flow(
                remember, {"fact": fact, "user_id": "u1"}, RunOptions(resources=res)
            )
            assert final["status"] == "ok", final
        final, _ = await run_flow(
            recall, {"question": "where does she live", "user_id": "u1"}, RunOptions(resources=res)
        )
        assert final["output"]["memories"] == "- Lives in Porto"
        final, _ = await run_flow(
            recall, {"question": "where", "user_id": "u2"}, RunOptions(resources=res)
        )
        assert final["output"]["memories"] == ""
    finally:
        await res.aclose()


async def test_trim_and_summarise_keep_a_chat_short():
    spec = make_spec(
        [
            input_step(mode="chat"),
            {
                "id": "shorten",
                "type": "memory",
                "settings": {"action": "summarise", "keep": 4, "save_as": "summary"},
            },
            {
                "id": "reply",
                "type": "ai_model",
                "settings": {"prompt": "messages", "save_as": "messages"},
            },
            output_step("summary"),
        ],
        [("input", "shorten"), ("shorten", "reply"), ("reply", "output")],
    )
    opts = RunOptions(stand_in=True, thread_id="long-chat")
    final = None
    for n in range(4):
        final, _ = await run_flow(
            spec, {"messages": [{"role": "user", "content": f"message {n}"}]}, opts
        )
        assert final["status"] == "ok", final
    from easychain.runtime.resources import memory_resources

    state = await memory_resources().checkpointer.aget({"configurable": {"thread_id": "long-chat"}})
    messages = state["channel_values"]["messages"]
    assert len(messages) == 5  # 4 kept, plus the newest reply
    assert final["output"]["summary"]


# ── MCP ──────────────────────────────────────────────────────────────────────


def _stdio_server() -> dict:
    return {
        "id": "facts",
        "name": "Test tools",
        "transport": "stdio",
        "command": f"{sys.executable} -m easychain.testing.mcp_server",
    }


def _mcp_flow():
    return make_spec(
        [
            input_step("city"),
            {
                "id": "facts",
                "type": "mcp_tool",
                "settings": {
                    "server": "facts",
                    "tool": "city_facts",
                    "arguments": {"city": "{city}"},
                },
            },
            {
                "id": "helper",
                "type": "agent",
                "settings": {
                    "input": "city",
                    "mcp": [{"server": "facts", "tools": ["add"]}],
                    "save_as": "sum",
                },
            },
            output_step("result", "sum"),
        ],
        [("input", "facts"), ("facts", "helper"), ("helper", "output")],
    )


async def test_mcp_tool_step_and_agent_tools_over_stdio():
    settings = {"servers": [_stdio_server()], "allowed_commands": [sys.executable]}
    connections = mcp_module.connections(settings)
    script = Script([{"call": "add", "args": {"a": 2, "b": 3}}, "It is 5."])
    final, events = await run_flow(
        _mcp_flow(), {"city": "Lisbon"}, RunOptions(stand_in=True, mcp=connections, script=script)
    )
    assert final["status"] == "ok", final
    assert final["output"]["result"].startswith("Lisbon is the capital of Portugal")
    assert [e["tool"] for e in of(events, "tool_started")] == ["add"]
    assert of(events, "tool_finished")[0]["result"] == "5.0"


async def test_local_mcp_servers_must_be_approved():
    connections = mcp_module.connections({"servers": [_stdio_server()], "allowed_commands": []})
    final, _ = await run_flow(
        _mcp_flow(), {"city": "Lisbon"}, RunOptions(stand_in=True, mcp=connections)
    )
    assert final["status"] == "error"
    assert final["error"]["kind"] == "mcp_not_allowed"
    assert "approved list" in final["error"]["message"]


def test_local_mcp_servers_get_only_their_own_secrets(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-not-for-mcp")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp-for-mcp")
    server = {**_stdio_server(), "env": {"TOKEN": "{secret:GITHUB_TOKEN}"}}
    conn = mcp_module.connections({"servers": [server], "allowed_commands": [sys.executable]})
    assert conn["facts"]["env"] == {"TOKEN": "ghp-for-mcp"}


def test_a_broken_mcp_command_is_reported_not_raised():
    server = {**_stdio_server(), "command": 'npx "unclosed'}
    conn = mcp_module.connections({"servers": [server], "allowed_commands": ["npx"]})
    assert "can't be read" in conn["facts"]["error"]


def test_mcp_commands_split_like_this_systems_shell():
    if os.name == "nt":
        assert mcp_module.split_command(r'C:\Tools\srv.exe --root "C:\My Files"') == [
            r"C:\Tools\srv.exe",
            "--root",
            r"C:\My Files",
        ]
    else:
        assert mcp_module.split_command("npx -y '@scope/server' --dir 'My Files'") == [
            "npx",
            "-y",
            "@scope/server",
            "--dir",
            "My Files",
        ]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def http_mcp():
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "easychain.testing.mcp_server", "--http", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}/mcp"
    for _ in range(100):
        try:
            httpx.get(url, timeout=0.5)
            break
        except httpx.HTTPError:
            time.sleep(0.1)
    yield url
    proc.terminate()
    proc.wait(10)


def test_mcp_settings_and_tool_listing_api(tmp_path, http_mcp):
    app = create_app(
        workspace=tmp_path / "flows",
        home=tmp_path / "home",
        static_dir=tmp_path / "web",
        database_url=f"sqlite:///{tmp_path / 'app.db'}",
        worker=False,
    )
    with TestClient(app) as client:
        assert client.get("/api/settings/mcp").json() == {"servers": [], "allowed_commands": []}
        saved = client.put(
            "/api/settings/mcp",
            json={
                "servers": [
                    {"id": "web", "name": "Over HTTP", "transport": "http", "url": http_mcp},
                    _stdio_server(),
                ],
                "allowed_commands": [" ", sys.executable],
            },
        ).json()
        assert saved["allowed_commands"] == [sys.executable]
        tools = client.post("/api/mcp/tools", json={"server_id": "web"}).json()["tools"]
        assert {t["name"] for t in tools} == {"add", "city_facts"}
        assert tools[0]["args"]
        local = client.post("/api/mcp/tools", json={"server_id": "facts"}).json()["tools"]
        assert len(local) == 2
        client.put("/api/settings/mcp", json={"servers": [_stdio_server()], "allowed_commands": []})
        refused = client.post("/api/mcp/tools", json={"server_id": "facts"})
        assert refused.status_code == 403
        dupes = client.put(
            "/api/settings/mcp", json={"servers": [_stdio_server(), _stdio_server()]}
        )
        assert dupes.status_code == 422


# ── OpenAPI import ───────────────────────────────────────────────────────────

PETSTORE = {
    "openapi": "3.0.3",
    "info": {"title": "Pet store", "version": "1"},
    "servers": [
        {"url": "https://pets.example.com/{version}", "variables": {"version": {"default": "v2"}}}
    ],
    "paths": {
        "/pets/{petId}": {
            "parameters": [
                {
                    "name": "petId",
                    "in": "path",
                    "required": True,
                    "description": "The pet's id.",
                    "schema": {"type": "integer"},
                }
            ],
            "get": {"operationId": "getPetById", "summary": "Get a pet by its id"},
            "delete": {"operationId": "deletePet", "summary": "Delete a pet"},
        },
        "/pets": {
            "get": {
                "operationId": "listPets",
                "summary": "List pets",
                "parameters": [
                    {"$ref": "#/components/parameters/Species"},
                    {"name": "limit", "in": "query", "schema": {"type": "integer"}},
                ],
            },
            "post": {
                "operationId": "addPet",
                "summary": "Add a pet",
                "requestBody": {
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/NewPet"}}
                    }
                },
            },
        },
    },
    "components": {
        "parameters": {
            "Species": {
                "name": "species",
                "in": "query",
                "required": True,
                "description": "cat or dog",
                "schema": {"type": "string"},
            }
        },
        "schemas": {
            "NewPet": {
                "type": "object",
                "required": ["name"],
                "properties": {
                    "name": {"type": "string", "description": "The pet's name."},
                    "age": {"type": "integer", "description": "Age in years."},
                    "vaccinated": {"type": "boolean"},
                },
            }
        },
    },
}


def test_openapi_operations_become_typed_steps():
    doc = openapi.load(json.dumps(PETSTORE))
    assert openapi.base_url(doc) == "https://pets.example.com/v2"
    ops = {op["id"]: op for op in openapi.operations(doc)}
    assert set(ops) == {"get_pet_by_id", "delete_pet", "list_pets", "add_pet"}
    assert ops["list_pets"]["params"][0] == {
        "name": "species",
        "in": "query",
        "required": True,
        "type": "text",
        "description": "cat or dog",
    }
    made = openapi.to_steps(
        doc,
        ["get_pet_by_id", "list_pets", "add_pet"],
        auth_header="Authorization",
        auth_secret="PETS_KEY",
        taken={"list_pets"},
    )
    steps = {s["id"]: s for s in made["steps"]}
    assert steps["get_pet_by_id"]["settings"]["url"] == "https://pets.example.com/v2/pets/{pet_id}"
    assert (
        steps["list_pets_2"]["settings"]["url"]
        == "https://pets.example.com/v2/pets?species={species}"
    )
    assert "Optional settings not sent: limit" in steps["list_pets_2"]["description"]
    assert (
        steps["add_pet"]["settings"]["body"]
        == '{"name": "{name}", "age": {age}, "vaccinated": {vaccinated}}'
    )
    assert steps["add_pet"]["settings"]["method"] == "POST"
    assert steps["add_pet"]["settings"]["headers"] == {"Authorization": "{secret:PETS_KEY}"}
    fields = {f["name"]: f for f in made["data"]}
    assert fields["pet_id"] == {"name": "pet_id", "type": "number", "description": "The pet's id."}
    assert fields["vaccinated"]["type"] == "yes_no"

    # As an agent's tools, the parameters become typed, described arguments.
    spec = make_spec(
        [
            input_step("question"),
            {"id": "vet", "type": "agent", "settings": {"input": "question", "tools": list(steps)}},
            *made["steps"],
            output_step("answer"),
        ],
        [("input", "vet"), ("vet", "output")],
        data=made["data"],
    )
    source = compile_flow(spec).source
    assert 'pet_id: Annotated[float, "The pet\'s id."]' in source
    assert "def add_pet_tool" in source

    with pytest.raises(openapi.OpenApiError, match="doesn't look like an OpenAPI"):
        openapi.load('{"hello": "world"}')
    with pytest.raises(openapi.OpenApiError, match="isn't JSON or YAML"):
        openapi.load("{not json")


def test_openapi_api(tmp_path, fake_server):
    app = create_app(
        workspace=tmp_path / "flows",
        home=tmp_path / "home",
        static_dir=tmp_path / "web",
        database_url=f"sqlite:///{tmp_path / 'app.db'}",
        worker=False,
    )
    with TestClient(app) as client:
        inspected = client.post(
            "/api/openapi/inspect", json={"source": json.dumps(PETSTORE)}
        ).json()
        assert inspected["title"] == "Pet store" and len(inspected["operations"]) == 4
        made = client.post(
            "/api/openapi/steps",
            json={"source": json.dumps(PETSTORE), "operations": ["delete_pet"]},
        ).json()
        assert made["steps"][0]["settings"]["method"] == "DELETE"
        bad = client.post("/api/openapi/inspect", json={"source": f"{fake_server.url}/nothing"})
        assert bad.status_code == 422 and "Couldn't fetch" in bad.json()["message"]
