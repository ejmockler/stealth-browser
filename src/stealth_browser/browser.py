"""High-level stealth browser API for interactive automation."""

from __future__ import annotations

import logging
import random
import time
from pathlib import Path
from typing import Any, List, Optional

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.remote.webelement import WebElement
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    ElementNotInteractableException,
    StaleElementReferenceException,
)

from stealth_browser.driver import DriverManager
from stealth_browser.config import LocaleConfig, PlatformConfig
from stealth_browser.exceptions import (
    BrowserError,
    NavigationError,
    ElementNotFoundError,
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
    ):
        """Initialize stealth browser.

        Args:
            output_dir: Directory for downloads/screenshots
            headless: Run browser in headless mode
            platform: Target platform ('windows', 'macos') or None for random
            locale: Locale configuration (defaults to California)
            auto_detect_locale: Detect locale from IP address
            profile_dir: Directory for persistent browser profile (preserves cookies/sessions)
        """
        # Default to California locale if not auto-detecting
        if locale is None and not auto_detect_locale:
            locale = LocaleConfig.california()

        self._dm = DriverManager(
            output_dir=output_dir,
            headless=headless,
            platform=platform,
            locale=locale,
            auto_detect_locale=auto_detect_locale,
            profile_dir=profile_dir,
        )

        self._output_dir = output_dir or Path.home() / ".cache" / "stealth-browser"
        self._output_dir.mkdir(parents=True, exist_ok=True)

    @property
    def driver(self):
        """Get the underlying Selenium WebDriver."""
        return self._dm.driver

    @property
    def config(self) -> PlatformConfig:
        """Get the platform configuration."""
        return self._dm.config

    def __enter__(self) -> "StealthBrowser":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        self.close()
        return False

    def close(self) -> None:
        """Close the browser and cleanup resources."""
        self._dm.close()
        logger.info("StealthBrowser closed")

    # ========================================
    # Navigation
    # ========================================

    def navigate(self, url: str, wait_for_load: bool = True, timeout: float = 30) -> None:
        """Navigate to a URL.

        Args:
            url: URL to navigate to
            wait_for_load: Wait for page to fully load
            timeout: Load timeout in seconds
        """
        self._human_delay()
        logger.info(f"Navigating to {url[:80]}...")

        try:
            self.driver.get(url)

            if wait_for_load:
                self._wait_for_page_load(timeout)

            self._human_delay()

        except Exception as e:
            raise NavigationError(f"Failed to navigate to {url}: {e}") from e

    def refresh(self) -> None:
        """Refresh the current page."""
        self.driver.refresh()
        self._wait_for_page_load()

    def back(self) -> None:
        """Go back to the previous page."""
        self.driver.back()
        self._wait_for_page_load()

    def forward(self) -> None:
        """Go forward to the next page."""
        self.driver.forward()
        self._wait_for_page_load()

    def get_url(self) -> str:
        """Get the current URL."""
        return self.driver.current_url

    def get_title(self) -> str:
        """Get the current page title."""
        return self.driver.title

    # ========================================
    # Element Interaction
    # ========================================

    def click(
        self,
        selector: str,
        by: str = By.CSS_SELECTOR,
        timeout: float = 10,
        scroll_into_view: bool = True,
    ) -> None:
        """Click an element with human-like behavior.

        Args:
            selector: Element selector
            by: Selenium By type (CSS_SELECTOR, XPATH, ID, etc.)
            timeout: Wait timeout in seconds
            scroll_into_view: Scroll element into view before clicking
        """
        self._human_delay()

        element = self._wait_for_element(selector, by, timeout)

        if scroll_into_view:
            self._scroll_to_element(element)
            self._human_delay(short=True)

        # Human-like click with ActionChains
        actions = ActionChains(self.driver)
        actions.move_to_element(element)
        actions.pause(random.uniform(0.1, 0.3))
        actions.click()
        actions.perform()

        logger.debug(f"Clicked element: {selector}")

    def fill(
        self,
        selector: str,
        value: str,
        by: str = By.CSS_SELECTOR,
        clear_first: bool = True,
        human_typing: bool = True,
        timeout: float = 10,
    ) -> None:
        """Fill a form field with human-like typing.

        Args:
            selector: Element selector
            value: Value to enter
            by: Selenium By type
            clear_first: Clear existing content first
            human_typing: Type character by character with delays
            timeout: Wait timeout in seconds
        """
        self._human_delay()

        element = self._wait_for_element(selector, by, timeout)
        self._scroll_to_element(element)
        self._human_delay(short=True)

        element.click()

        if clear_first:
            element.clear()
            # Backup clear method
            element.send_keys(Keys.CONTROL + "a")
            element.send_keys(Keys.DELETE)

        if human_typing:
            for char in value:
                element.send_keys(char)
                time.sleep(random.uniform(0.03, 0.12))
        else:
            element.send_keys(value)

        logger.debug(f"Filled field: {selector}")

    def fill_fast(self, selector: str, value: str, by: str = By.CSS_SELECTOR) -> None:
        """Fill a form field quickly using JavaScript.

        Uses direct DOM manipulation to avoid typing detection.
        Good for passwords or when speed is needed.
        """
        self._human_delay(short=True)

        element = self._wait_for_element(selector, by)

        self.driver.execute_script(
            """
            arguments[0].value = arguments[1];
            arguments[0].dispatchEvent(new Event('input', { bubbles: true }));
            arguments[0].dispatchEvent(new Event('change', { bubbles: true }));
            """,
            element,
            value,
        )

        logger.debug(f"Fast-filled field: {selector}")

    def type_text(self, text: str) -> None:
        """Type text into the currently focused element."""
        self._human_delay(short=True)

        for char in text:
            ActionChains(self.driver).send_keys(char).perform()
            time.sleep(random.uniform(0.03, 0.1))

    def press_key(self, key: str) -> None:
        """Press a keyboard key (e.g., Keys.ENTER, Keys.TAB)."""
        ActionChains(self.driver).send_keys(key).perform()

    def hover(self, selector: str, by: str = By.CSS_SELECTOR) -> None:
        """Hover over an element."""
        element = self._wait_for_element(selector, by)
        ActionChains(self.driver).move_to_element(element).perform()

    # ========================================
    # Element Queries
    # ========================================

    def wait_for(
        self,
        selector: str,
        by: str = By.CSS_SELECTOR,
        timeout: float = 10,
        visible: bool = True,
    ) -> WebElement:
        """Wait for an element to be present/visible.

        Args:
            selector: Element selector
            by: Selenium By type
            timeout: Wait timeout in seconds
            visible: Wait for visibility (not just presence)

        Returns:
            WebElement
        """
        return self._wait_for_element(selector, by, timeout, visible)

    def is_visible(self, selector: str, by: str = By.CSS_SELECTOR) -> bool:
        """Check if an element is visible."""
        try:
            element = self.driver.find_element(by, selector)
            return element.is_displayed()
        except (NoSuchElementException, StaleElementReferenceException):
            return False

    def exists(self, selector: str, by: str = By.CSS_SELECTOR) -> bool:
        """Check if an element exists in the DOM."""
        try:
            self.driver.find_element(by, selector)
            return True
        except NoSuchElementException:
            return False

    def get_text(self, selector: str, by: str = By.CSS_SELECTOR) -> str:
        """Get text content of an element."""
        element = self._wait_for_element(selector, by)
        return element.text

    def get_attribute(
        self,
        selector: str,
        attribute: str,
        by: str = By.CSS_SELECTOR,
    ) -> Optional[str]:
        """Get attribute value of an element."""
        element = self._wait_for_element(selector, by)
        return element.get_attribute(attribute)

    def get_value(self, selector: str, by: str = By.CSS_SELECTOR) -> str:
        """Get the value of a form field."""
        return self.get_attribute(selector, "value", by) or ""

    def find_all(self, selector: str, by: str = By.CSS_SELECTOR) -> List[WebElement]:
        """Find all elements matching a selector."""
        return self.driver.find_elements(by, selector)

    # ========================================
    # Convenience Methods
    # ========================================

    def try_click(
        self,
        selectors: List[str],
        by: str = By.CSS_SELECTOR,
        timeout: float = 3,
    ) -> bool:
        """Try clicking elements from a list of selectors until one works.

        Args:
            selectors: List of selectors to try
            by: Selenium By type
            timeout: Timeout per selector

        Returns:
            True if any selector was clicked successfully
        """
        for selector in selectors:
            try:
                self.click(selector, by=by, timeout=timeout)
                return True
            except (TimeoutException, NoSuchElementException, ElementNotFoundError):
                continue
        return False

    def try_fill(
        self,
        selectors: List[str],
        value: str,
        by: str = By.CSS_SELECTOR,
    ) -> bool:
        """Try filling elements from a list of selectors until one works."""
        for selector in selectors:
            try:
                if self.is_visible(selector, by):
                    self.fill(selector, value, by=by)
                    return True
            except (TimeoutException, NoSuchElementException, ElementNotFoundError):
                continue
        return False

    def inject_credentials(
        self,
        username_selector: str,
        password_selector: str,
        username: str,
        password: str,
        by: str = By.CSS_SELECTOR,
    ) -> None:
        """Securely inject credentials into a login form.

        Uses human-like typing for username and fast injection for password.
        """
        logger.info("Injecting credentials")

        # Username with human-like typing
        self.fill(username_selector, username, by=by, human_typing=True)
        self._human_delay()

        # Password via fast injection (avoids keylogger-style detection)
        self.fill_fast(password_selector, password, by=by)

        logger.info("Credentials injected")

    # ========================================
    # Wait Utilities
    # ========================================

    def wait_for_url(self, substring: str, timeout: float = 30) -> bool:
        """Wait for URL to contain a substring."""
        try:
            WebDriverWait(self.driver, timeout).until(EC.url_contains(substring))
            return True
        except TimeoutException:
            return False

    def wait_for_url_change(self, original_url: Optional[str] = None, timeout: float = 30) -> bool:
        """Wait for URL to change from the original."""
        if original_url is None:
            original_url = self.get_url()

        try:
            WebDriverWait(self.driver, timeout).until(
                lambda d: d.current_url != original_url
            )
            return True
        except TimeoutException:
            return False

    def wait_for_text(self, selector: str, text: str, timeout: float = 10) -> bool:
        """Wait for element to contain specific text."""
        try:
            WebDriverWait(self.driver, timeout).until(
                EC.text_to_be_present_in_element((By.CSS_SELECTOR, selector), text)
            )
            return True
        except TimeoutException:
            return False

    def sleep(self, seconds: float) -> None:
        """Sleep for a specified duration."""
        time.sleep(seconds)

    # ========================================
    # Screenshots and Debug
    # ========================================

    def screenshot(self, path: Optional[str] = None) -> str:
        """Take a screenshot.

        Args:
            path: File path (auto-generated if not provided)

        Returns:
            Path to saved screenshot
        """
        if path is None:
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            path = str(self._output_dir / f"screenshot_{timestamp}.png")

        self.driver.save_screenshot(path)
        logger.info(f"Screenshot saved: {path}")
        return path

    def get_page_source(self) -> str:
        """Get the current page HTML source."""
        return self.driver.page_source

    def execute_script(self, script: str, *args: Any) -> Any:
        """Execute JavaScript in the page context."""
        return self.driver.execute_script(script, *args)

    # ========================================
    # Session Management
    # ========================================

    def clear_state(self) -> None:
        """Clear browser state (cookies, cache)."""
        self._dm.clear_state()
        logger.info("Browser state cleared")

    def new_session(self) -> None:
        """Create a new browser session with fresh fingerprint."""
        self._dm.refresh()
        logger.info("New browser session created")

    # ========================================
    # Internal Methods
    # ========================================

    def _wait_for_element(
        self,
        selector: str,
        by: str = By.CSS_SELECTOR,
        timeout: float = 10,
        visible: bool = True,
    ) -> WebElement:
        """Internal wait for element."""
        try:
            wait = WebDriverWait(self.driver, timeout)

            if visible:
                condition = EC.visibility_of_element_located((by, selector))
            else:
                condition = EC.presence_of_element_located((by, selector))

            return wait.until(condition)

        except TimeoutException:
            raise ElementNotFoundError(
                f"Element not found: {selector} (by={by}, timeout={timeout}s)"
            )

    def _wait_for_page_load(self, timeout: float = 30) -> None:
        """Wait for page to finish loading."""
        WebDriverWait(self.driver, timeout).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )

    def _scroll_to_element(self, element: WebElement) -> None:
        """Scroll element into view."""
        self.driver.execute_script(
            "arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});",
            element,
        )
        time.sleep(0.3)  # Wait for scroll animation

    def _human_delay(self, short: bool = False) -> None:
        """Add a human-like delay."""
        if short:
            delay = random.uniform(0.1, 0.3)
        else:
            # Normal delays: 200-500ms base + occasional longer pauses
            delay = random.uniform(0.2, 0.5)
            if random.random() < 0.1:  # 10% chance of "thinking" pause
                delay += random.uniform(0.3, 0.8)

        time.sleep(delay)
