"""Turn exceptions raised during a run into plain-language messages with a next step."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

import httpx

from ..providers import PROVIDERS, split_model
from .gateway import MissingAPIKey, MissingPackage


def _host(url: Any) -> str:
    try:
        return urlparse(str(url)).netloc or str(url)
    except Exception:  # pragma: no cover - defensive
        return str(url)


def _provider_for(step: Any) -> Any:
    model = getattr(getattr(step, "settings", None), "model", None)
    if not model:
        return None
    provider_id, _ = split_model(model)
    return PROVIDERS.get(provider_id or "")


def explain(exc: BaseException, step: Any = None) -> dict[str, Any]:
    """Return {message, hint, kind, fixes, detail} for an error raised by ``step``."""
    detail = f"{type(exc).__name__}: {exc}"[:2000]
    out: dict[str, Any] = {
        "kind": "error",
        "message": str(exc) or type(exc).__name__,
        "detail": detail,
        "fixes": [],
    }
    provider = _provider_for(step)
    status = getattr(exc, "status_code", None)
    name = type(exc).__name__

    if isinstance(exc, MissingAPIKey):
        out.update(
            kind="missing_key",
            message=f"This step needs your {exc.label} before it can run.",
            hint=f"Add it in Settings → API keys (it's saved as {exc.env_var}), or try the stand-in AI.",
            fixes=[
                {
                    "kind": "add_key",
                    "label": "Add your API key",
                    "params": {"provider": exc.provider, "env": exc.env_var},
                },
                {"kind": "use_stand_in", "label": "Try with the stand-in AI", "params": {}},
            ],
        )
        return out

    if isinstance(exc, MissingPackage):
        install = (
            f"pip install 'easychain[{exc.extra}]'" if exc.extra else f"pip install {exc.package}"
        )
        out.update(
            kind="missing_package",
            message=f"{exc.label} isn't installed on this server.",
            hint=f"Install it with `{install}` (the Docker image already has it), then try again.",
        )
        return out

    if provider is not None and (status == 401 or name == "AuthenticationError"):
        out.update(
            kind="bad_key",
            message=f"{provider.label} didn't accept the API key.",
            hint="Check the key in Settings → API keys; it may be mistyped, revoked or for another account.",
            fixes=[
                {
                    "kind": "add_key",
                    "label": "Update your API key",
                    "params": {"provider": provider.id, "env": provider.key_env},
                }
            ],
        )
        return out
    if provider is not None and (status == 429 or name == "RateLimitError"):
        out.update(
            kind="rate_limit",
            message=f"{provider.label} is limiting requests right now, or the account is out of credit.",
            hint="Wait a minute and run again, or check your plan and billing with the provider.",
            fixes=[{"kind": "retry", "label": "Run again", "params": {}}],
        )
        return out
    if provider is not None and (status == 404 or name == "NotFoundError"):
        model = step.settings.model
        out.update(
            kind="model_not_found",
            message=f"{provider.label} doesn't know the model “{split_model(model)[1]}”.",
            hint="Pick a model from the list, or check the spelling.",
            fixes=[
                {"kind": "focus_setting", "label": "Change the model", "params": {"key": "model"}}
            ],
        )
        return out
    if (
        provider is not None
        and provider.id == "ollama"
        and isinstance(exc, httpx.ConnectError | ConnectionError)
    ):
        out.update(
            kind="ollama_down",
            message="Ollama isn't running on this computer (or at the address set).",
            hint="Install Ollama, then run `ollama serve` and `ollama pull <model>`.",
        )
        return out
    if provider is not None and status is not None and status >= 500:
        out.update(
            kind="provider_down",
            message=f"{provider.label} had a problem on their side ({status}).",
            hint="This is usually temporary. Run again in a moment.",
            fixes=[{"kind": "retry", "label": "Run again", "params": {}}],
        )
        return out

    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        reason = exc.response.reason_phrase
        host = _host(exc.request.url)
        hints = {
            401: "The site wants a login or API key. Add an Authorization header using {secret:NAME}.",
            403: "The site refused access. It may block automated requests or need an API key.",
            404: "Check the URL; that page doesn't exist.",
            429: "The site is rate-limiting requests. Wait a little and try again.",
        }
        hint = hints.get(code) or (
            "The site had a problem; try again later."
            if code >= 500
            else "Check the URL and settings."
        )
        out.update(
            kind="http_status",
            message=f"The web request got “{code} {reason}” from {host}.",
            hint=hint,
        )
        return out
    if isinstance(exc, httpx.ProxyError):
        host = _host(exc.request.url) if exc.request else "the site"
        out.update(
            kind="blocked",
            message=f"The network proxy blocked the request to {host} ({exc}).",
            hint="This network doesn't allow that site. Try another URL, or ask your admin to allow it.",
        )
        return out
    if isinstance(exc, httpx.TimeoutException):
        out.update(
            kind="timeout",
            message=f"{_host(exc.request.url) if exc.request else 'The site'} took too long to answer.",
            hint="Try again, or raise the time limit under More options.",
        )
        return out
    if isinstance(exc, httpx.ConnectError):
        host = _host(exc.request.url) if exc.request else "the site"
        out.update(
            kind="connect",
            message=f"Couldn't reach {host}.",
            hint="Check the address and your internet connection.",
        )
        return out
    if isinstance(exc, httpx.UnsupportedProtocol | httpx.InvalidURL) or "Request URL" in str(exc):
        out.update(
            kind="bad_url",
            message="The URL isn't a valid web address.",
            hint="It should start with https://. If it comes from an input, check what was typed.",
            fixes=[{"kind": "focus_setting", "label": "Edit the URL", "params": {"key": "url"}}],
        )
        return out
    if "Illegal header value" in str(exc) or name == "LocalProtocolError":
        out.update(
            kind="bad_header",
            message="A header value is empty or invalid, so the request couldn't be sent.",
            hint="This usually means a {secret:NAME} it uses isn't set. Add the secret in Settings → Secrets.",
            fixes=[
                {
                    "kind": "focus_setting",
                    "label": "Check the headers",
                    "params": {"key": "headers"},
                }
            ],
        )
        return out
    if isinstance(exc, json.JSONDecodeError):
        out.update(
            kind="not_json",
            message="The response wasn't JSON.",
            hint="Switch “Keep” to the raw response or readable text.",
            fixes=[
                {
                    "kind": "set_setting",
                    "label": "Keep the raw response",
                    "params": {"key": "response", "value": "text"},
                }
            ],
        )
        return out
    if isinstance(exc, KeyError) and step is not None:
        field = exc.args[0] if exc.args else "?"
        out.update(
            kind="missing_field",
            message=f"This step needs `{field}`, but nothing has set it yet.",
            hint="Connect a step that saves it before this one, or add it to Input.",
        )
        return out
    if name == "GraphRecursionError":
        out.update(
            kind="too_many_steps",
            message="The run went round a loop too many times (25 steps) and was stopped.",
            hint="Check that a Decision in the loop eventually takes the exit that leaves it.",
        )
        return out
    return out
