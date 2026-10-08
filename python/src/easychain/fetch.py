"""Fetching a web address for Easy Chain itself (Knowledge Base pages, OpenAPI imports).

The response is read as it arrives and reading stops past a size limit, so a huge (or
endless) response can't fill the memory. Compressed responses count at their full size.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx


class FetchError(ValueError):
    """The address couldn't be fetched; the message is shown to people."""


class TooLarge(FetchError):
    """The response is bigger than the limit."""


@dataclass
class Fetched:
    url: str  # where the content came from, after any redirects
    content: bytes
    content_type: str | None


def size_text(n: int) -> str:
    """A size for people: 50 MB, 512 KB, 300 bytes."""
    if n >= 1024 * 1024:
        return f"{n / (1024 * 1024):g} MB"
    if n >= 1024:
        return f"{n / 1024:g} KB"
    return f"{n} bytes"


def fetch(
    url: str, *, limit: int, timeout: float = 30, headers: dict[str, str] | None = None
) -> Fetched:
    """GET a URL (following redirects), reading at most ``limit`` bytes of it."""
    too_large = TooLarge(f"{url} is larger than {size_text(limit)}, so I stopped reading it.")
    try:
        with httpx.stream(
            "GET", url, timeout=timeout, follow_redirects=True, headers=headers
        ) as response:
            response.raise_for_status()
            declared = response.headers.get("content-length", "")
            if declared.isdigit() and int(declared) > limit:
                raise too_large
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > limit:
                    raise too_large
                chunks.append(chunk)
            return Fetched(
                str(response.url), b"".join(chunks), response.headers.get("content-type")
            )
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        raise FetchError(f"Couldn't fetch {url}: {exc}") from exc
