"""Tests for the native engine (Chrome Extension + OS input)."""

from __future__ import annotations

import json
import math
import time
from unittest.mock import MagicMock, call

import pytest

from stealth_browser.exceptions import BrowserError


# =====================================================================
# Unit tests (no browser required)
# =====================================================================


class TestNativeEngineInit:
    """Constructor and engine selection tests."""

    def test_rejects_headless(self):
        from stealth_browser.engines.native_engine import NativeEngine

        with pytest.raises(BrowserError, match="visible window"):
            NativeEngine(headless=True)

    def test_engine_selection_routes_native(self):
        from stealth_browser.engine_selection import create_engine

        with pytest.raises(BrowserError, match="visible window"):
            # It should instantiate NativeEngine which rejects headless
            create_engine("native", headless=True)

    def test_engine_selection_unknown_raises(self):
        from stealth_browser.engine_selection import create_engine

        with pytest.raises(ValueError, match="Unknown engine"):
            create_engine("nonexistent")


class TestExtensionGeneration:
    """Extension file generation tests."""

    def test_produces_all_files(self):
        from stealth_browser.native.extension import generate_extension

        files = generate_extension(ws_port=12345, stealth_js="console.log(1)")
        assert set(files.keys()) == {
            "manifest.json",
            "background.js",
            "offscreen.html",
            "offscreen.js",
            "content.js",
        }

    def test_manifest_is_valid_json(self):
        from stealth_browser.native.extension import generate_extension

        files = generate_extension(ws_port=12345, stealth_js="")
        manifest = json.loads(files["manifest.json"])
        assert manifest["manifest_version"] == 3
        assert "offscreen" in manifest["permissions"]
        assert manifest["content_scripts"][0]["run_at"] == "document_start"

    def test_port_embedded_in_offscreen(self):
        from stealth_browser.native.extension import generate_extension

        files = generate_extension(ws_port=54321, stealth_js="")
        assert "54321" in files["offscreen.js"]
        assert "__WS_PORT__" not in files["offscreen.js"]

    def test_stealth_js_embedded_in_content(self):
        from stealth_browser.native.extension import generate_extension

        marker = "UNIQUE_TEST_MARKER_12345"
        files = generate_extension(ws_port=1, stealth_js=marker)
        assert marker in files["content.js"]

    def test_stealth_js_special_chars_escaped(self):
        from stealth_browser.native.extension import generate_extension

        tricky = 'backtick ` template ${expr} quotes "\' newline\n end'
        files = generate_extension(ws_port=1, stealth_js=tricky)
        # Should not cause JS syntax errors — the stealth JS is JSON-encoded
        assert "JSON.parse(" in files["content.js"]


class TestHumanMovement:
    """Minimum-jerk trajectory tests with mock backend."""

    def _make_backend(self):
        backend = MagicMock()
        backend.mouse_move = MagicMock()
        return backend

    def test_move_to_trivial_distance(self):
        from stealth_browser.native.human import move_to

        backend = self._make_backend()
        move_to(backend, 100, 100, 100, 100)
        # Should just move to target directly
        backend.mouse_move.assert_called_once_with(100, 100)

    def test_move_to_generates_points(self):
        from stealth_browser.native.human import move_to

        backend = self._make_backend()
        move_to(backend, 0, 0, 200, 200)
        # Should generate multiple intermediate points
        assert backend.mouse_move.call_count > 5

    def test_move_to_ends_at_target(self):
        from stealth_browser.native.human import move_to

        backend = self._make_backend()
        move_to(backend, 0, 0, 500, 300)
        # Last call should be at or near target
        last_call = backend.mouse_move.call_args_list[-1]
        x, y = last_call[0]
        assert abs(x - 500) <= 8  # allow overshoot correction
        assert abs(y - 300) <= 8

    def test_min_jerk_profile(self):
        from stealth_browser.native.human import _min_jerk

        assert _min_jerk(0.0) == pytest.approx(0.0, abs=0.001)
        assert _min_jerk(0.5) == pytest.approx(0.5, abs=0.001)
        assert _min_jerk(1.0) == pytest.approx(1.0, abs=0.001)
        # Monotonically increasing
        prev = 0
        for i in range(1, 101):
            val = _min_jerk(i / 100)
            assert val >= prev
            prev = val

    def test_fitts_duration_scales_with_distance(self):
        from stealth_browser.native.human import _fitts_duration

        short = _fitts_duration(50)
        long = _fitts_duration(500)
        assert long > short
        # Both should be in reasonable range
        assert 0.03 < short < 0.5
        assert 0.1 < long < 2.0


class TestHumanTyping:
    """Typing simulation tests with mock backend."""

    def test_type_text_calls_type_char(self):
        from stealth_browser.native.human import type_text

        backend = MagicMock()
        type_text(backend, "abc")
        assert backend.type_char.call_count == 3
        backend.type_char.assert_any_call("a")
        backend.type_char.assert_any_call("b")
        backend.type_char.assert_any_call("c")

    def test_type_text_empty(self):
        from stealth_browser.native.human import type_text

        backend = MagicMock()
        type_text(backend, "")
        backend.type_char.assert_not_called()

    def test_press_key_down_up(self):
        from stealth_browser.native.human import press_key

        backend = MagicMock()
        press_key(backend, "Enter")
        backend.key_down.assert_called_once()
        backend.key_up.assert_called_once()

    def test_select_all_uses_modifier(self):
        import sys
        from stealth_browser.native.human import select_all

        backend = MagicMock()
        select_all(backend)
        # Should press modifier + a
        assert backend.key_down.call_count == 2
        assert backend.key_up.call_count == 2


class TestBridge:
    """WebSocket bridge lifecycle tests."""

    def test_start_returns_port(self):
        from stealth_browser.native.bridge import ExtensionBridge

        bridge = ExtensionBridge()
        port = bridge.start()
        assert isinstance(port, int)
        assert 1024 <= port <= 65535
        bridge.close()

    def test_close_is_idempotent(self):
        from stealth_browser.native.bridge import ExtensionBridge

        bridge = ExtensionBridge()
        bridge.start()
        bridge.close()
        bridge.close()  # Should not raise

    def test_send_without_start_raises(self):
        from stealth_browser.native.bridge import ExtensionBridge

        bridge = ExtensionBridge()
        with pytest.raises(BrowserError):
            bridge.send("test", {})


class TestStealthJS:
    """Stealth JS generation tests."""

    def test_build_stealth_scripts(self):
        from stealth_browser.config import BrowserConfig
        from stealth_browser.stealth_js import build_stealth_scripts

        config = BrowserConfig.get_config()
        js = build_stealth_scripts(config)
        assert "__STEALTH_SEED" in js
        assert "Navigator.prototype" in js
        assert len(js) > 10000

    def test_skip_screen_overrides(self):
        from stealth_browser.config import BrowserConfig
        from stealth_browser.stealth_js import build_stealth_scripts

        config = BrowserConfig.get_config()
        js_full = build_stealth_scripts(config, skip_screen_overrides=False)
        js_skip = build_stealth_scripts(config, skip_screen_overrides=True)
        assert len(js_full) > len(js_skip)
        # screenX override should be absent when skipped
        assert "screenX" not in js_skip


# =====================================================================
# Integration tests (require Chrome for Testing)
# =====================================================================


@pytest.mark.integration
class TestNativeIntegration:
    """Live browser tests — require Chrome for Testing."""

    @pytest.fixture
    def browser(self):
        from stealth_browser import StealthBrowser

        b = StealthBrowser(engine="native", headless=False)
        yield b
        b.close()

    def test_navigate_and_title(self, browser):
        browser.navigate("https://example.com")
        assert browser.get_url() == "https://example.com/"
        assert "Example" in browser.get_title()

    def test_execute_script(self, browser):
        browser.navigate("https://example.com")
        result = browser.execute_script("return 2 + 2")
        assert result == 4

    def test_get_text(self, browser):
        browser.navigate("https://example.com")
        text = browser.get_text("h1")
        assert "Example" in text

    def test_exists_and_visible(self, browser):
        browser.navigate("https://example.com")
        assert browser.exists("h1")
        assert browser.is_visible("h1")
        assert not browser.exists("#nonexistent")
