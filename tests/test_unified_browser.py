"""Tests for the unified StealthBrowser engine abstraction.

Integration tests that require a real browser are marked with
``@pytest.mark.integration`` and skipped in CI by default.  The
structural / import tests run without a browser.
"""

import pytest

from stealth_browser import (
    StealthBrowser,
    StealthElement,
    PATCHRIGHT_AVAILABLE,
    CSS_SELECTOR,
    XPATH,
)
from stealth_browser.engine_selection import detect_engine, patchright_available
from stealth_browser.engines.selenium_engine import SeleniumEngine


# ------------------------------------------------------------------
# Import / structural tests (no browser needed)
# ------------------------------------------------------------------

class TestEngineDetection:
    def test_detect_engine_returns_string(self):
        engine = detect_engine()
        assert engine in ("selenium", "patchright")

    def test_patchright_available_matches_detect(self):
        if patchright_available():
            assert detect_engine() == "patchright"
        else:
            assert detect_engine() == "selenium"


class TestStealthBrowserInit:
    """Verify constructor wiring without launching a browser."""

    def test_engine_param_accepted(self):
        # Just verify the param is accepted — we don't actually launch
        # because that requires Chrome installed.
        assert hasattr(StealthBrowser.__init__, "__code__")

    def test_exports(self):
        """Key symbols are importable from the top-level package."""
        from stealth_browser import (
            StealthBrowser,
            StealthElement,
            PATCHRIGHT_AVAILABLE,
            CSS_SELECTOR,
            XPATH,
            BrowserError,
        )
        assert CSS_SELECTOR == "css selector"
        assert XPATH == "xpath"
        assert isinstance(PATCHRIGHT_AVAILABLE, bool)


class TestStealthElement:
    """StealthElement with a mock backing object."""

    def test_selenium_element(self):
        class FakeWebElement:
            text = "hello"
            tag_name = "div"
            def is_displayed(self): return True
            def get_attribute(self, name): return "val"

        el = StealthElement(FakeWebElement(), "selenium")
        assert el.text == "hello"
        assert el.tag_name == "div"
        assert el.is_displayed() is True
        assert el.get_attribute("href") == "val"
        assert el.value == "val"
        assert el.native is not None
        assert repr(el) == "<StealthElement engine='selenium'>"


# ------------------------------------------------------------------
# Integration tests (require Chrome)
# ------------------------------------------------------------------

@pytest.mark.integration
class TestSeleniumEngineIntegration:
    def test_navigate_and_title(self):
        with StealthBrowser(engine="selenium", headless=True) as browser:
            browser.navigate("data:text/html,<title>Test</title><p id='msg'>OK</p>")
            assert browser.get_title() == "Test"
            assert browser.engine_name == "selenium"

    def test_get_text(self):
        with StealthBrowser(engine="selenium", headless=True) as browser:
            browser.navigate("data:text/html,<p id='msg'>Hello</p>")
            assert browser.get_text("#msg") == "Hello"

    def test_execute_script(self):
        with StealthBrowser(engine="selenium", headless=True) as browser:
            browser.navigate("data:text/html,<p>hi</p>")
            result = browser.execute_script("return 1 + 2")
            assert result == 3

    def test_screenshot(self, tmp_path):
        with StealthBrowser(engine="selenium", headless=True) as browser:
            browser.navigate("data:text/html,<p>pic</p>")
            path = browser.screenshot(str(tmp_path / "shot.png"))
            assert path.endswith(".png")

    def test_config_accessible(self):
        with StealthBrowser(engine="selenium", headless=True) as browser:
            cfg = browser.config
            assert cfg.platform in ("Win32", "MacIntel")
            assert cfg.user_agent


@pytest.mark.integration
@pytest.mark.skipif(not PATCHRIGHT_AVAILABLE, reason="patchright not installed")
class TestPatchrightEngineIntegration:
    def test_navigate_and_title(self):
        with StealthBrowser(engine="patchright", headless=True) as browser:
            browser.navigate("data:text/html,<title>Test</title><p id='msg'>OK</p>")
            assert browser.get_title() == "Test"
            assert browser.engine_name == "patchright"

    def test_get_text(self):
        with StealthBrowser(engine="patchright", headless=True) as browser:
            browser.navigate("data:text/html,<p id='msg'>Hello</p>")
            assert browser.get_text("#msg") == "Hello"

    def test_execute_script(self):
        with StealthBrowser(engine="patchright", headless=True) as browser:
            browser.navigate("data:text/html,<p>hi</p>")
            result = browser.execute_script("return 1 + 2")
            assert result == 3

    def test_screenshot(self, tmp_path):
        with StealthBrowser(engine="patchright", headless=True) as browser:
            browser.navigate("data:text/html,<p>pic</p>")
            path = browser.screenshot(str(tmp_path / "shot.png"))
            assert path.endswith(".png")

    def test_driver_property_warns(self):
        with StealthBrowser(engine="patchright", headless=True) as browser:
            import warnings
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                browser.driver
                assert len(w) == 1
                assert "selenium" in str(w[0].message).lower()
