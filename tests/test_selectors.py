"""Unit tests for selector and key translation."""

import pytest

from stealth_browser.selectors import (
    CSS_SELECTOR,
    XPATH,
    ID,
    CLASS_NAME,
    NAME,
    TAG_NAME,
    LINK_TEXT,
    PARTIAL_LINK_TEXT,
    translate_selector,
    translate_key,
)


# ------------------------------------------------------------------
# Selector translation
# ------------------------------------------------------------------

class TestTranslateSelector:
    def test_css_passthrough(self):
        assert translate_selector("div.foo > span", CSS_SELECTOR) == "div.foo > span"

    def test_xpath(self):
        assert translate_selector("//div[@id='x']", XPATH) == "xpath=//div[@id='x']"

    def test_id(self):
        assert translate_selector("myId", ID) == "#myId"

    def test_class_name(self):
        assert translate_selector("active", CLASS_NAME) == ".active"

    def test_name(self):
        assert translate_selector("email", NAME) == '[name="email"]'

    def test_tag_name(self):
        assert translate_selector("button", TAG_NAME) == "button"

    def test_link_text_exact(self):
        assert translate_selector("Click me", LINK_TEXT) == 'text="Click me"'

    def test_partial_link_text(self):
        assert translate_selector("Click", PARTIAL_LINK_TEXT) == "text=Click"

    def test_unknown_strategy_raises(self):
        with pytest.raises(ValueError, match="Unknown selector strategy"):
            translate_selector("foo", "bogus")

    def test_default_is_css(self):
        assert translate_selector("#foo") == "#foo"


# ------------------------------------------------------------------
# Key translation
# ------------------------------------------------------------------

class TestTranslateKey:
    def test_selenium_enter(self):
        assert translate_key("\ue006") == "Enter"

    def test_selenium_return(self):
        assert translate_key("\ue007") == "Enter"

    def test_selenium_tab(self):
        assert translate_key("\ue004") == "Tab"

    def test_selenium_escape(self):
        assert translate_key("\ue00c") == "Escape"

    def test_selenium_backspace(self):
        assert translate_key("\ue003") == "Backspace"

    def test_playwright_string_passthrough(self):
        # Already a Playwright-style name → returned as-is
        assert translate_key("Enter") == "Enter"

    def test_regular_char_passthrough(self):
        assert translate_key("a") == "a"

    def test_arrow_keys(self):
        assert translate_key("\ue012") == "ArrowLeft"
        assert translate_key("\ue014") == "ArrowRight"
        assert translate_key("\ue013") == "ArrowUp"
        assert translate_key("\ue015") == "ArrowDown"
