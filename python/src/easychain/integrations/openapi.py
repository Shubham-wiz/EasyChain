"""OpenAPI import: a service's operations become Web request steps (or agent tools).

Each operation becomes an ``http_request`` step whose URL and body carry {field}
placeholders for its parameters, plus Flow Data declarations with each parameter's
type and description, so an agent gets a typed, described tool.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx
import yaml

METHODS = ("get", "post", "put", "patch", "delete")
_TYPES = {"string": "text", "integer": "number", "number": "number", "boolean": "yes_no"}


class OpenApiError(ValueError):
    """A spec that can't be read; the message is shown to people."""


def load(source: str) -> dict[str, Any]:
    """An OpenAPI document from a URL, or from JSON or YAML text."""
    text = source.strip()
    if re.match(r"^https?://", text):
        try:
            response = httpx.get(text, timeout=30, follow_redirects=True)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise OpenApiError(f"Couldn't fetch the spec: {exc}") from exc
        text = response.text
    try:
        doc = json.loads(text) if text.startswith("{") else yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise OpenApiError(f"That isn't JSON or YAML I can read: {exc}") from exc
    if not isinstance(doc, dict) or not ("openapi" in doc or "swagger" in doc):
        raise OpenApiError("That doesn't look like an OpenAPI (or Swagger) document.")
    if not isinstance(doc.get("paths"), dict) or not doc["paths"]:
        raise OpenApiError("The spec has no operations (paths).")
    return doc


def snake(name: str) -> str:
    s = re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", name)
    s = re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_").lower()
    s = re.sub(r"_{2,}", "_", s)
    if not s or not s[0].isalpha():
        s = f"p_{s}" if s else "value"
    return s[:62]


def _resolve(doc: dict[str, Any], node: Any, depth: int = 0) -> Any:
    if isinstance(node, dict) and "$ref" in node and depth < 10:
        target: Any = doc
        for part in node["$ref"].lstrip("#/").split("/"):
            target = target.get(part, {}) if isinstance(target, dict) else {}
        return _resolve(doc, target, depth + 1)
    return node


def base_url(doc: dict[str, Any]) -> str:
    servers = doc.get("servers") or []
    if servers and isinstance(servers[0], dict):
        url = servers[0].get("url", "")
        for name, var in (servers[0].get("variables") or {}).items():
            url = url.replace("{" + name + "}", str(var.get("default", "")))
        return url.rstrip("/")
    if doc.get("host"):  # Swagger 2
        scheme = (doc.get("schemes") or ["https"])[0]
        return f"{scheme}://{doc['host']}{doc.get('basePath', '')}".rstrip("/")
    return ""


def operations(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Every operation, with its parameters (path, query, header, and JSON body fields)."""
    out = []
    for path, item in doc["paths"].items():
        if not isinstance(item, dict):
            continue
        shared = item.get("parameters") or []
        for method in METHODS:
            op = item.get(method)
            if not isinstance(op, dict):
                continue
            params = []
            for raw in [*shared, *(op.get("parameters") or [])]:
                p = _resolve(doc, raw)
                if not isinstance(p, dict) or p.get("in") not in ("path", "query", "header"):
                    continue
                schema = _resolve(doc, p.get("schema") or {}) or {}
                params.append(
                    {
                        "name": p.get("name", ""),
                        "in": p["in"],
                        "required": bool(p.get("required") or p["in"] == "path"),
                        "type": _TYPES.get(schema.get("type") or p.get("type") or "", "text"),
                        "description": (p.get("description") or "").strip(),
                    }
                )
            body = []
            content = (_resolve(doc, op.get("requestBody") or {}) or {}).get("content") or {}
            schema = _resolve(doc, (content.get("application/json") or {}).get("schema") or {})
            if isinstance(schema, dict):
                required = set(schema.get("required") or [])
                for name, prop in (schema.get("properties") or {}).items():
                    prop = _resolve(doc, prop) or {}
                    kind = prop.get("type")
                    body.append(
                        {
                            "name": name,
                            "in": "body",
                            "required": name in required,
                            "type": _TYPES.get(
                                kind or "",
                                "list"
                                if kind == "array"
                                else "object"
                                if kind == "object"
                                else "text",
                            ),
                            "description": (prop.get("description") or "").strip(),
                        }
                    )
            op_id = op.get("operationId") or f"{method}_{path}"
            out.append(
                {
                    "id": snake(op_id),
                    "method": method.upper(),
                    "path": path,
                    "summary": (op.get("summary") or "").strip(),
                    "description": (op.get("description") or "").strip(),
                    "params": params,
                    "body": body,
                }
            )
    return out


def to_steps(
    doc: dict[str, Any],
    wanted: list[str],
    *,
    server: str | None = None,
    auth_header: str | None = None,
    auth_secret: str | None = None,
    taken: set[str] | None = None,
) -> dict[str, Any]:
    """Web request steps for the chosen operations, and the Flow Data fields they use."""
    root = (server or base_url(doc)).rstrip("/")
    used = set(taken or ())
    steps: list[dict[str, Any]] = []
    fields: dict[str, dict[str, Any]] = {}
    for op in operations(doc):
        if op["id"] not in wanted:
            continue
        step_id, n = op["id"], 2
        while step_id in used:
            step_id, n = f"{op['id']}_{n}", n + 1
        used.add(step_id)

        def field(param: dict[str, Any]) -> str:
            name = snake(param["name"])
            if name in used:  # a field can't share a name with a step
                name = f"{name}_value"
            fields.setdefault(
                name, {"name": name, "type": param["type"], "description": param["description"]}
            )
            return name

        path = op["path"]
        for p in op["params"]:
            if p["in"] == "path":
                path = path.replace("{" + p["name"] + "}", "{" + field(p) + "}")
        query = [
            f"{p['name']}={{{field(p)}}}"
            for p in op["params"]
            if p["in"] == "query" and p["required"]
        ]
        url = root + path + ("?" + "&".join(query) if query else "")
        headers = {
            p["name"]: "{" + field(p) + "}"
            for p in op["params"]
            if p["in"] == "header" and p["required"]
        }
        if auth_header and auth_secret:
            headers[auth_header] = f"{{secret:{auth_secret}}}"
        body = ""
        if op["body"]:
            parts = []
            for p in op["body"]:
                value = "{" + field(p) + "}"
                parts.append(f'"{p["name"]}": ' + (f'"{value}"' if p["type"] == "text" else value))
            body = "{" + ", ".join(parts) + "}"
        optional = [p["name"] for p in op["params"] if p["in"] == "query" and not p["required"]]
        description = op["summary"] or op["description"] or f"{op['method']} {op['path']}"
        if optional:
            description += f" (Optional settings not sent: {', '.join(optional)}.)"
        settings: dict[str, Any] = {"method": op["method"], "url": url, "response": "json"}
        if headers:
            settings["headers"] = headers
        if body:
            settings["body"] = body
        steps.append(
            {
                "id": step_id,
                "type": "http_request",
                "name": (op["summary"] or op["id"].replace("_", " ").capitalize())[:80],
                "description": description[:500],
                "settings": {**settings, "save_as": f"{step_id}_result"},
            }
        )
    return {"steps": steps, "data": list(fields.values())}
