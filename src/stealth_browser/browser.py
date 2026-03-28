"""High-level stealth browser API for interactive automation."""

from __future__ import annotations

import logging
import random
import time
import warnings
from pathlib import Path
from typing import Any, List, Optional

from stealth_browser.config import LocaleConfig, PlatformConfig
from stealth_browser.element import StealthElement
from stealth_browser.engine_selection import create_engine
from stealth_browser.exceptions import (
    BrowserError,
    ElementNotFoundError,
)
from stealth_browser.selectors import (
    CSS_SELECTOR,
    translate_key,
    translate_selector,
)

logger = logging.getLogger(__name__)


class StealthBrowser:
    """High-level stealth browser for interactive automation.

    Provides human-like interactions with comprehensive anti-detection:
    - WebDriver detection hiding
    - Fingerprint spoofing (platform, hardware, GPU, network)
    - Timezone/locale spoofing
    - Human-like delays and behavior
    - Canvas/audio fingerprint protection

    The underlying engine is selected automatically — Patchright (CDP-leak-free)
    when installed, falling back to Selenium.  Override with ``engine="selenium"``
    or ``engine="patchright"``.

    Usage:
        with StealthBrowser() as browser:
            browser.navigate("https://example.com")
            browser.fill("#username", "user@example.com")
            browser.fill("#password", "secret")
            browser.click("#login")
    """

    def __init__(
        self,
        output_dir: Optional[Path] = None,
        headless: bool = True,
        platform: Optional[str] = None,
        locale: Optional[LocaleConfig] = None,
        auto_detect_locale: bool = False,
        profile_dir: Optional[Path] = None,
        engine: Optional[str] = None,
        cdp_endpoint: Optional[str] = None,
    ):
        """Initialize stealth browser.

        Args:
            output_dir: Directory for downloads/screenshots
            headless: Run browser in headless mode
            platform: Target platform ('windows', 'macos') or None for random
            locale: Locale configuration (defaults to California)
            auto_detect_locale: Detect locale from IP address
            profile_dir: Directory for persistent browser profile
            engine: ``"patchright"``, ``"selenium"``, or ``None`` (auto-detect)
            cdp_endpoint: Connect to existing Chrome via CDP instead of launching.
                Start Chrome with ``--remote-debugging-port=9222``, then pass
                ``cdp_endpoint="http://localhost:9222"``.  Most stealth — TLS
                fingerprints are 100% genuine.  Patchright engine only.
        """
        if locale is None and not auto_detect_locale:
            locale = LocaleConfig.california()

        self._output_dir = output_dir or Path.home() / ".cache" / "stealth-browser"
        self._output_dir.mkdir(parents=True, exist_ok=True)

        self._engine = create_engine(
            engine,
            output_dir=output_dir,
            headless=headless,
            platform=platform,
            locale=locale,
            auto_detect_locale=auto_detect_locale,
            profile_dir=profile_dir,
            cdp_endpoint=cdp_endpoint,
        )
        self._engine.start()

    # ------------------------------------------------------------------
    # Backward-compat escape hatches
    # ------------------------------------------------------------------

    @property
    def driver(self):
        """Get the underlying Selenium WebDriver (Selenium engine only)."""
        if hasattr(self._engine, "native_driver"):
            return self._engine.native_driver
        warnings.warn(
            "The .driver property is only available with engine='selenium'. "
            "Use .execute_script() or the StealthBrowser API instead.",
            stacklevel=2,
        )
        return None

    @property
    def config(self) -> PlatformConfig:
        """Get the platform configuration."""
        return self._engine.config

    @property
    def engine_name(self) -> str:
        """Return the name of the active engine (``"selenium"`` or ``"patchright"``)."""
        return type(self._engine).__name__.replace("Engine", "").lower()

    def __enter__(self) -> "StealthBrowser":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        self.close()
        return False

    def close(self) -> None:
        """Close the browser and cleanup resources."""
        self._engine.close()
        logger.info("StealthBrowser closed")

    # ========================================
    # Navigation
    # ========================================

    def navigate(self, url: str, wait_for_load: bool = True, timeout: float = 30) -> None:
        """Navigate to a URL."""
        self._human_delay()
        logger.info(f"Navigating to {url[:80]}...")
        self._engine.navigate(url, wait_for_load=wait_for_load, timeout=timeout)
        self._human_delay()

    def refresh(self) -> None:
        """Refresh the current page."""
        self._engine.refresh_page()

    def back(self) -> None:
        """Go back to the previous page."""
        self._engine.back()

    def forward(self) -> None:
        """Go forward to the next page."""
        self._engine.forward()

    def get_url(self) -> str:
        """Get the current URL."""
        return self._engine.get_url()

    def get_title(self) -> str:
        """Get the current page title."""
        return self._engine.get_title()

    # ========================================
    # Element Interaction
    # ========================================

    def click(
        self,
        selector: str,
        by: str = CSS_SELECTOR,
        timeout: float = 10,
        scroll_into_view: bool = True,
    ) -> None:
        """Click an element with human-like behavior."""
        self._human_delay()
        native = translate_selector(selector, by)
        self._engine.click_element(native, timeout=timeout, scroll=scroll_into_view)
        logger.debug(f"Clicked element: {selector}")

    def fill(
        self,
        selector: str,
        value: str,
        by: str = CSS_SELECTOR,
        clear_first: bool = True,
        human_typing: bool = True,
        timeout: float = 10,
    ) -> None:
        """Fill a form field with human-like typing."""
        self._human_delay()
        native = translate_selector(selector, by)
        self._engine.fill_element(
            native,
            value,
            clear=clear_first,
            human_typing=human_typing,
            timeout=timeout,
        )
        logger.debug(f"Filled field: {selector}")

    def fill_fast(self, selector: str, value: str, by: str = CSS_SELECTOR) -> None:
        """Fill a form field quickly using JavaScript."""
        self._human_delay(short=True)
        native = translate_selector(selector, by)
        self._engine.fill_fast(native, value)
        logger.debug(f"Fast-filled field: {selector}")

    def type_text(self, text: str) -> None:
        """Type text into the currently focused element."""
        self._human_delay(short=True)
        self._engine.type_text(text)

    def press_key(self, key: str) -> None:
        """Press a keyboard key (e.g., Keys.ENTER, Keys.TAB)."""
        self._engine.press_key(key)

    def hover(self, selector: str, by: str = CSS_SELECTOR) -> None:
        """Hover over an element."""
        native = translate_selector(selector, by)
        self._engine.hover_element(native)

    # ========================================
    # Element Queries
    # ========================================

    def wait_for(
        self,
        selector: str,
        by: str = CSS_SELECTOR,
        timeout: float = 10,
        visible: bool = True,
    ) -> StealthElement:
        """Wait for an element to be present/visible.

        Returns:
            StealthElement (use ``.native`` for the underlying engine object)
        """
        native = translate_selector(selector, by)
        return self._engine.wait_for_element(native, timeout=timeout, visible=visible)

    def is_visible(self, selector: str, by: str = CSS_SELECTOR) -> bool:
        """Check if an element is visible."""
        native = translate_selector(selector, by)
        return self._engine.is_visible(native)

    def exists(self, selector: str, by: str = CSS_SELECTOR) -> bool:
        """Check if an element exists in the DOM."""
        native = translate_selector(selector, by)
        return self._engine.exists(native)

    def get_text(self, selector: str, by: str = CSS_SELECTOR) -> str:
        """Get text content of an element."""
        native = translate_selector(selector, by)
        return self._engine.get_text(native)

    def get_attribute(
        self,
        selector: str,
        attribute: str,
        by: str = CSS_SELECTOR,
    ) -> Optional[str]:
        """Get attribute value of an element."""
        native = translate_selector(selector, by)
        return self._engine.get_attribute(native, attribute)

    def get_value(self, selector: str, by: str = CSS_SELECTOR) -> str:
        """Get the value of a form field."""
        return self.get_attribute(selector, "value", by) or ""

    def find_all(self, selector: str, by: str = CSS_SELECTOR) -> List[StealthElement]:
        """Find all elements matching a selector."""
        native = translate_selector(selector, by)
        return self._engine.find_all(native)

    # ========================================
    # Convenience Methods
    # ========================================

    def try_click(
        self,
        selectors: List[str],
        by: str = CSS_SELECTOR,
        timeout: float = 3,
    ) -> bool:
        """Try clicking elements from a list of selectors until one works."""
        for selector in selectors:
            try:
                self.click(selector, by=by, timeout=timeout)
                return True
            except (ElementNotFoundError, BrowserError):
                continue
        return False

    def try_fill(
        self,
        selectors: List[str],
        value: str,
        by: str = CSS_SELECTOR,
    ) -> bool:
        """Try filling elements from a list of selectors until one works."""
        for selector in selectors:
            try:
                if self.is_visible(selector, by):
                    self.fill(selector, value, by=by)
                    return True
            except (ElementNotFoundError, BrowserError):
                continue
        return False

    def inject_credentials(
        self,
        username_selector: str,
        password_selector: str,
        username: str,
        password: str,
        by: str = CSS_SELECTOR,
    ) -> None:
        """Inject credentials — human typing for username, fast fill for password."""
        logger.info("Injecting credentials")
        self.fill(username_selector, username, by=by, human_typing=True)
        self._human_delay()
        self.fill_fast(password_selector, password, by=by)
        logger.info("Credentials injected")

    # ========================================
    # Wait Utilities
    # ========================================

    def wait_for_url(self, substring: str, timeout: float = 30) -> bool:
        """Wait for URL to contain a substring."""
        return self._engine.wait_for_url(substring, timeout=timeout)

    def wait_for_url_change(self, original_url: Optional[str] = None, timeout: float = 30) -> bool:
        """Wait for URL to change from the original."""
        if original_url is None:
            original_url = self.get_url()
        return self._engine.wait_for_url_change(original_url, timeout=timeout)

    def wait_for_text(self, selector: str, text: str, timeout: float = 10) -> bool:
        """Wait for element to contain specific text."""
        return self._engine.wait_for_text(selector, text, timeout=timeout)

    def sleep(self, seconds: float) -> None:
        """Sleep for a specified duration."""
        time.sleep(seconds)

    # ========================================
    # Screenshots and Debug
    # ========================================

    def screenshot(self, path: Optional[str] = None) -> str:
        """Take a screenshot."""
        if path is None:
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            path = str(self._output_dir / f"screenshot_{timestamp}.png")
        result = self._engine.screenshot(path)
        logger.info(f"Screenshot saved: {result}")
        return result

    def get_page_source(self) -> str:
        """Get the current page HTML source."""
        return self._engine.get_page_source()

    def execute_script(self, script: str, *args: Any) -> Any:
        """Execute JavaScript in the page context."""
        return self._engine.execute_script(script, *args)

    # ========================================
    # Session Management
    # ========================================

    def clear_state(self) -> None:
        """Clear browser state (cookies, cache)."""
        self._engine.clear_state()
        logger.info("Browser state cleared")

    def new_session(self) -> None:
        """Create a new browser session with fresh fingerprint."""
        self._engine.new_session()
        logger.info("New browser session created")

    # ========================================
    # Internal Methods
    # ========================================

    def _human_delay(self, short: bool = False) -> None:
        """Add a human-like delay."""
        if short:
            delay = random.uniform(0.1, 0.3)
        else:
            delay = random.uniform(0.2, 0.5)
            if random.random() < 0.1:
                delay += random.uniform(0.3, 0.8)
        time.sleep(delay)
