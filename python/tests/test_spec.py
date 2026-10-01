from __future__ import annotations

import json

import jsonschema
import pytest
import yaml

from easychain.spec import SpecError, dumps_spec, load_spec, loads_spec, parse_spec, spec_json
from easychain.spec.schema import flow_json_schema, flow_json_schema_text

from .conftest import ROOT, TEMPLATES


def test_roundtrip_is_stable_for_every_template():
    for path in TEMPLATES.glob("*.flow.yaml"):
        spec = load_spec(path)
        text = dumps_spec(spec)
        again = loads_spec(text)
        assert again == spec, path.name
        assert dumps_spec(again) == text, path.name


def test_yaml_layout_puts_canvas_last_and_orders_step_keys():
    spec = load_spec(TEMPLATES / "summarise-url.flow.yaml")
    data = yaml.safe_load(dumps_spec(spec))
    assert list(data)[0] == "version"
    assert list(data)[-1] == "canvas"
    assert list(data["steps"][1])[:3] == ["id", "type", "name"]


def test_defaults_are_left_out_and_multiline_text_is_a_block():
    spec = load_spec(TEMPLATES / "summarise-url.flow.yaml")
    text = dumps_spec(spec)
    assert "max_chars" not in text  # equals its default
    assert "user: |-" in text or "user: |" in text


def test_json_form_has_all_defaults_and_aliases():
    spec = load_spec(TEMPLATES / "summarise-url.flow.yaml")
    data = spec_json(spec)
    assert data["connections"][0]["from"] == "input"
    fetch = next(s for s in data["steps"] if s["id"] == "fetch_page")
    assert fetch["settings"]["max_chars"] == 20000
    assert parse_spec(data) == spec


@pytest.mark.parametrize(
    "data,expected",
    [
        ({"name": "x", "steps": [{"id": "Bad Id", "type": "input"}]}, "lowercase letters"),
        ({"name": "x", "steps": [{"id": "a", "type": "teleport"}]}, "unknown step type 'teleport'"),
        ({"name": "x", "steps": [{"id": "a", "type": "input", "colour": 1}]}, "not recognised"),
        (
            {"name": "x", "steps": [{"id": "a", "type": "input"}, {"id": "a", "type": "output"}]},
            "share the id 'a'",
        ),
        ({"steps": []}, "name"),
        ([1, 2], "mapping"),
    ],
)
def test_friendly_spec_errors(data, expected):
    with pytest.raises(SpecError) as info:
        parse_spec(data)
    assert expected in str(info.value)


def test_error_location_names_the_step():
    with pytest.raises(SpecError) as info:
        parse_spec(
            {
                "name": "x",
                "steps": [{"id": "ask", "type": "ai_model", "settings": {"temperature": 9}}],
            }
        )
    assert "step 'ask' › settings › temperature" in info.value.problems[0]


def test_bad_yaml_is_reported():
    with pytest.raises(SpecError) as info:
        loads_spec("name: [unclosed")
    assert "not valid YAML" in str(info.value)


def test_json_schema_validates_every_template():
    schema = flow_json_schema()
    for path in TEMPLATES.glob("*.flow.yaml"):
        jsonschema.validate(yaml.safe_load(path.read_text()), schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"name": "x", "steps": [{"id": "a", "type": "nope"}]}, schema)


def test_published_schema_is_up_to_date():
    published = ROOT / "spec" / "flow.schema.json"
    assert published.exists(), "run `make schema` to generate spec/flow.schema.json"
    assert json.loads(published.read_text()) == json.loads(flow_json_schema_text())
