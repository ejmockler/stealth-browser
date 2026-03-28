"""WebSocket server for Python <-> Chrome Extension communication.

Uses the ``websockets`` library for a robust WebSocket server.  Runs an
``asyncio`` event loop on a daemon thread so that the synchronous public
API (``send``, ``wait_for_connection``, etc.) can be called from any
thread without an active event loop.
"""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any

from stealth_browser.exceptions import (
    BrowserError,
    ElementNotFoundError,
    NavigationError,
    TimeoutError,
)

# ------------------------------------------------------------------
# Error mapping: extension error codes -> Python exceptions
# ------------------------------------------------------------------

_ERROR_MAP: dict[str, type[BrowserError]] = {
    "ELEMENT_NOT_FOUND": ElementNotFoundError,
    "TIMEOUT": TimeoutError,
    "NAVIGATION_ERROR": NavigationError,
}


def _map_error(error: dict) -> BrowserError:
    """Convert an extension error payload to the appropriate exception."""
    code = error.get("code", "")
    message = error.get("message", code)
    exc_cls = _ERROR_MAP.get(code, BrowserError)
    return exc_cls(message)


# ------------------------------------------------------------------
# Pending request slot
# ------------------------------------------------------------------

class _PendingRequest:
    __slots__ = ("event", "result", "error")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.result: Any = None
        self.error: BrowserError | None = None


# ------------------------------------------------------------------
# ExtensionBridge
# ------------------------------------------------------------------

class ExtensionBridge:
    """WebSocket server for Python <-> Chrome Extension communication."""

    def __init__(self) -> None:
        self._port: int = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._server: Any = None  # websockets server

        # Request-response tracking
        self._next_id = 1
        self._id_lock = threading.Lock()
        self._pending: dict[int, _PendingRequest] = {}

        # Connection state
        self._ws: Any = None  # websockets connection
        self._connected = threading.Event()

        # Events (unsolicited messages from the extension)
        self._events: dict[str, dict] = {}
        self._event_waiters: dict[str, threading.Event] = {}

        self._closed = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> int:
        """Start the WebSocket server on a random port.  Returns the actual port."""
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever,
            daemon=True,
            name="stealth-extension-bridge",
        )
        self._thread.start()

        future = asyncio.run_coroutine_threadsafe(self._start_server(), self._loop)
        self._port = future.result(timeout=10)
        return self._port

    def wait_for_connection(self, timeout: float = 30) -> None:
        """Block until the Chrome extension connects via WebSocket."""
        if not self._connected.wait(timeout=timeout):
            raise TimeoutError(
                f"Extension did not connect within {timeout}s"
            )

    def send(self, method: str, params: dict | None = None, timeout: float = 30) -> Any:
        """Send a command to the extension and wait for the response."""
        if self._closed:
            raise BrowserError("ExtensionBridge is closed")
        if self._ws is None:
            raise BrowserError("Extension disconnected")

        with self._id_lock:
            req_id = self._next_id
            self._next_id += 1

        pending = _PendingRequest()
        self._pending[req_id] = pending

        msg: dict[str, Any] = {"id": req_id, "method": method}
        if params is not None:
            msg["params"] = params
        payload = json.dumps(msg)

        asyncio.run_coroutine_threadsafe(
            self._ws_send(payload), self._loop  # type: ignore[arg-type]
        )

        if not pending.event.wait(timeout=timeout):
            self._pending.pop(req_id, None)
            raise TimeoutError(f"No response for '{method}' within {timeout}s")

        self._pending.pop(req_id, None)

        if pending.error is not None:
            raise pending.error

        return pending.result

    def wait_for_event(self, event_name: str, timeout: float = 30) -> dict:
        """Wait for an unsolicited event from the extension."""
        if event_name in self._events:
            return self._events.pop(event_name)

        waiter = threading.Event()
        self._event_waiters[event_name] = waiter

        if not waiter.wait(timeout=timeout):
            self._event_waiters.pop(event_name, None)
            raise TimeoutError(f"Event '{event_name}' not received within {timeout}s")

        self._event_waiters.pop(event_name, None)
        return self._events.pop(event_name, {})

    def close(self) -> None:
        """Shut down the server and clean up."""
        if self._closed:
            return
        self._closed = True

        if self._loop and not self._loop.is_closed():
            asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop)
            self._loop.call_soon_threadsafe(self._loop.stop)

        if self._thread:
            self._thread.join(timeout=5)

    # ------------------------------------------------------------------
    # Async internals
    # ------------------------------------------------------------------

    async def _start_server(self) -> int:
        """Create the WebSocket server and return the bound port."""
        import websockets.asyncio.server as ws_server

        self._server = await ws_server.serve(
            self._handle_connection,
            host="127.0.0.1",
            port=0,
        )
        sock = self._server.sockets[0]
        return sock.getsockname()[1]

    async def _handle_connection(self, websocket: Any) -> None:
        """Handle a WebSocket connection from the extension."""
        self._ws = websocket
        self._connected.set()

        try:
            async for message in websocket:
                self._handle_message(message)
        except Exception:
            pass
        finally:
            self._ws = None
            for pending in self._pending.values():
                pending.error = BrowserError("Extension disconnected")
                pending.event.set()

    async def _ws_send(self, text: str) -> None:
        """Send a text message to the extension."""
        ws = self._ws
        if ws is None:
            return
        try:
            await ws.send(text)
        except Exception:
            pass

    async def _shutdown(self) -> None:
        """Close the WebSocket connection and server."""
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
        if self._server:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Message dispatch
    # ------------------------------------------------------------------

    def _handle_message(self, text: str) -> None:
        """Dispatch a decoded JSON message from the extension."""
        try:
            msg = json.loads(text)
        except json.JSONDecodeError:
            return

        # Unsolicited event
        if "event" in msg and "id" not in msg:
            event_name = msg["event"]
            self._events[event_name] = msg.get("params", {})
            waiter = self._event_waiters.get(event_name)
            if waiter is not None:
                waiter.set()
            return

        # Response to a request
        req_id = msg.get("id")
        if req_id is None:
            return

        pending = self._pending.get(req_id)
        if pending is None:
            return

        # Check for content script error wrapper
        result = msg.get("result")
        if isinstance(result, dict) and result.get("__error"):
            pending.error = _map_error(result)
        elif "error" in msg and msg["error"]:
            pending.error = _map_error(msg["error"])
        else:
            pending.result = result

        pending.event.set()
