"""Unit tests for the async-to-sync bridge."""

import asyncio

import pytest

from stealth_browser.sync_bridge import SyncBridge
from stealth_browser.exceptions import BrowserError


class TestSyncBridge:
    def test_run_simple_coroutine(self):
        bridge = SyncBridge()
        try:
            async def add(a, b):
                return a + b

            assert bridge.run(add(2, 3)) == 5
        finally:
            bridge.close()

    def test_run_propagates_exception(self):
        bridge = SyncBridge()
        try:
            async def fail():
                raise RuntimeError("boom")

            with pytest.raises(BrowserError, match="boom"):
                bridge.run(fail())
        finally:
            bridge.close()

    def test_run_propagates_browser_error_directly(self):
        bridge = SyncBridge()
        try:
            async def fail():
                raise BrowserError("direct")

            with pytest.raises(BrowserError, match="direct"):
                bridge.run(fail())
        finally:
            bridge.close()

    def test_run_after_close_raises(self):
        bridge = SyncBridge()
        bridge.close()

        async def noop():
            pass

        with pytest.raises(BrowserError, match="closed"):
            bridge.run(noop())

    def test_close_is_idempotent(self):
        bridge = SyncBridge()
        bridge.close()
        bridge.close()  # should not raise

    def test_timeout(self):
        bridge = SyncBridge()
        try:
            async def slow():
                await asyncio.sleep(10)

            with pytest.raises(BrowserError, match="timed out"):
                bridge.run(slow(), timeout=0.1)
        finally:
            bridge.close()

    def test_multiple_sequential_calls(self):
        bridge = SyncBridge()
        try:
            async def double(n):
                return n * 2

            results = [bridge.run(double(i)) for i in range(5)]
            assert results == [0, 2, 4, 6, 8]
        finally:
            bridge.close()
