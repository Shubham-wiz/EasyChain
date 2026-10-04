"""What differs between Windows and Linux/macOS, kept in one place.

- **Event loop.** psycopg's async mode (Postgres runs, LangGraph's Postgres Save Points) can't
  use Windows' default Proactor event loop, so Easy Chain runs on a selector loop there.
- **Stop signals.** ``loop.add_signal_handler`` isn't available on Windows; plain signal
  handlers are used instead, and Ctrl+Break (``SIGBREAK``) also asks a worker to stop.
- **Console output.** Redirected output on Windows defaults to the ANSI code page, which can't
  print ✓ or ✗; the CLI writes UTF-8 instead.
"""

from __future__ import annotations

import asyncio
import signal
import sys
from collections.abc import Callable, Coroutine
from typing import Any

IS_WINDOWS = sys.platform == "win32"


def new_event_loop() -> asyncio.AbstractEventLoop:
    """A new event loop that every part of Easy Chain can use on this system."""
    return asyncio.SelectorEventLoop() if IS_WINDOWS else asyncio.new_event_loop()


def run[T](main: Coroutine[Any, Any, T]) -> T:
    """``asyncio.run`` on the right kind of event loop."""
    return asyncio.run(main, loop_factory=new_event_loop if IS_WINDOWS else None)


def uvicorn_loop_factory(use_subprocess: bool = False) -> Callable[[], asyncio.AbstractEventLoop]:
    """Loop factory for uvicorn on Windows (``loop="easychain._platform:uvicorn_loop_factory"``)."""
    return asyncio.SelectorEventLoop


def uvicorn_loop() -> str:
    return "easychain._platform:uvicorn_loop_factory" if IS_WINDOWS else "auto"


def on_stop(loop: asyncio.AbstractEventLoop, callback: Callable[[], None]) -> None:
    """Call ``callback`` on the loop when the process is asked to stop."""
    if not IS_WINDOWS:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, callback)
        return

    def handler(signum: int, frame: Any) -> None:
        loop.call_soon_threadsafe(callback)

    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGBREAK):
        signal.signal(sig, handler)


def utf8_console() -> None:
    """Write UTF-8 when output goes to a file or pipe that would otherwise use a code page."""
    for stream in (sys.stdout, sys.stderr):
        encoding = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
        if encoding != "utf8" and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
