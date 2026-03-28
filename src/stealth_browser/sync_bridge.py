"""Async-to-sync bridge for Patchright integration.

Runs an ``asyncio`` event loop on a dedicated daemon thread so that the
synchronous ``StealthBrowser`` API can call async Patchright/Playwright
functions without requiring callers to manage an event loop themselves.

This is the same pattern Playwright uses in its own ``sync_api`` module.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Coroutine, TypeVar

from stealth_browser.exceptions import BrowserError

T = TypeVar("T")

_DEFAULT_TIMEOUT = 120  # seconds – browser ops can be slow


class SyncBridge:
    """Run coroutines on a background event loop and block for results."""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever,
            daemon=True,
            name="stealth-sync-bridge",
        )
        self._thread.start()
        self._closed = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, coro: Coroutine[Any, Any, T], timeout: float = _DEFAULT_TIMEOUT) -> T:
        """Post *coro* to the bridge loop and block until it completes.

        Raises whatever exception the coroutine raises, wrapped in
        :class:`BrowserError` for Playwright-specific errors.
        """
        if self._closed:
            raise BrowserError("SyncBridge is closed")

        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout=timeout)
        except TimeoutError:
            future.cancel()
            raise BrowserError(f"Operation timed out after {timeout}s")
        except Exception as exc:
            # Re-raise BrowserError subclasses as-is; wrap anything else.
            if isinstance(exc, BrowserError):
                raise
            raise BrowserError(str(exc)) from exc

    def close(self) -> None:
        """Stop the event loop and join the thread."""
        if self._closed:
            return
        self._closed = True
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)
