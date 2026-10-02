"""Every template ships with a Test Set of at least 10 cases, and passes it."""

from __future__ import annotations

import pytest

from easychain.compiler import compile_flow
from easychain.templates import TEMPLATES as TEMPLATE_META
from easychain.templates import list_templates, load_template
from easychain.testsets import run_test_set

from .conftest import TEMPLATES


@pytest.mark.parametrize("template_id", [t["id"] for t in TEMPLATE_META])
def test_template_compiles_without_warnings(template_id):
    compiled = compile_flow(load_template(template_id))
    assert compiled.issues == []


def test_gallery_metadata():
    items = {t["id"]: t for t in list_templates()}
    assert items["summarise-url"]["sample_inputs"] == {
        "url": "https://en.wikipedia.org/wiki/LangChain"
    }
    assert items["summarise-url"]["keys"][0]["env"] == "OPENAI_API_KEY"
    assert items["chat-assistant"]["chat"] is True
    assert all(t["has_tests"] for t in items.values())


@pytest.fixture
def sample_home(tmp_path, monkeypatch):
    """Sample data (shop database, help-centre Knowledge Base) built in a throwaway folder."""
    from easychain.integrations.sql import SQL_ENGINES
    from easychain.knowledge.search import KNOWLEDGE_ENGINES

    monkeypatch.setenv("EASYCHAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EASYCHAIN_KNOWLEDGE_URL", f"sqlite:///{tmp_path / 'knowledge.db'}")
    yield tmp_path
    KNOWLEDGE_ENGINES.clear()
    SQL_ENGINES.clear()


@pytest.mark.parametrize("template_id", [t["id"] for t in TEMPLATE_META])
async def test_template_passes_its_test_set(template_id, fake_openai, sample_home):
    path = TEMPLATES / f"{template_id}.tests.yaml"
    results = await run_test_set(path, {"base_url": fake_openai.url})
    assert len(results) >= 10
    failed = [(r.name, r.failures) for r in results if not r.passed]
    assert failed == []
