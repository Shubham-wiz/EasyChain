"""Generate the published JSON Schema for flow spec files."""

from __future__ import annotations

import json
from typing import Any

from .models import SPEC_VERSION, FlowSpec

SCHEMA_ID = f"https://easychain.dev/schema/flow/v{SPEC_VERSION}.json"


def flow_json_schema() -> dict[str, Any]:
    schema = FlowSpec.model_json_schema(by_alias=True, mode="validation")
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_ID,
        "title": "Easy Chain flow",
        "description": (
            "An Easy Chain flow spec (*.flow.yaml). Each flow compiles to a LangGraph graph. "
            f"Spec version {SPEC_VERSION}."
        ),
        **{k: v for k, v in schema.items() if k not in ("title", "description")},
    }


def flow_json_schema_text() -> str:
    return json.dumps(flow_json_schema(), indent=2, sort_keys=False) + "\n"
