"""Selenium backend implementing ``BrowserEngine``."""

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
    StaleElementReferenceException,
)

from stealth_browser.config import LocaleConfig, PlatformConfig
from stealth_browser.driver import DriverManager
from stealth_browser.element import StealthElement
from stealth_browser.exceptions import (
    BrowserError,
    ElementNotFoundError,
    NavigationError,
)
from stealth_browser.selectors import translate_key

logger = logging.getLogger(__name__)


class SeleniumEngine:
    """``BrowserEngine`` implementation backed by Selenium WebDriver."""

    def __init__(
        self,
        output_dir: Optional[Path] = None,
        headless: bool = True,
        platform: Optional[str] = None,
        locale: Optional[LocaleConfig] = None,
        auto_detect_locale: bool = False,
        profile_dir: Optional[Path] = None,
    ) -> None:
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

    # ------------------------------------------------------------------
    # Escape hatch for backward compat
    # ------------------------------------------------------------------

    @property
    def native_driver(self):
        """Return the underlying Selenium ``WebDriver``."""
        return self._dm.driver

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        # DriverManager lazy-initialises; force it now.
        _ = self._dm.driver

    def close(self) -> None:
        self._dm.close()

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def navigate(self, url: str, wait_for_load: bool = True, timeout: float = 30) -> None:
        try:
            self._dm.driver.get(url)
            if wait_for_load:
                self._wait_for_page_load(timeout)
        except Exception as e:
            raise NavigationError(f"Failed to navigate to {url}: {e}") from e

    def refresh_page(self) -> None:
        self._dm.driver.refresh()
        self._wait_for_page_load()

    def back(self) -> None:
        self._dm.driver.back()
        self._wait_for_page_load()

    def forward(self) -> None:
        self._dm.driver.forward()
        self._wait_for_page_load()

    def get_url(self) -> str:
        return self._dm.driver.current_url

    def get_title(self) -> str:
        return self._dm.driver.title

    # ------------------------------------------------------------------
    # Element interaction
    # ------------------------------------------------------------------

    def click_element(self, selector: str, timeout: float = 10, scroll: bool = True) -> None:
        element = self._wait_for_raw(selector, timeout)
        if scroll:
            self._scroll_to(element)
            time.sleep(random.uniform(0.1, 0.3))
        actions = ActionChains(self._dm.driver)
        actions.move_to_element(element)
        actions.pause(random.uniform(0.1, 0.3))
        actions.click()
        actions.perform()

    def fill_element(
        self,
        selector: str,
        value: str,
        clear: bool = True,
        human_typing: bool = True,
        timeout: float = 10,
    ) -> None:
        element = self._wait_for_raw(selector, timeout)
        self._scroll_to(element)
        time.sleep(random.uniform(0.1, 0.3))
        element.click()
        if clear:
            element.clear()
            element.send_keys(Keys.CONTROL + "a")
            element.send_keys(Keys.DELETE)
        if human_typing:
            for char in value:
                element.send_keys(char)
                time.sleep(random.uniform(0.03, 0.12))
        else:
            element.send_keys(value)

    def fill_fast(self, selector: str, value: str) -> None:
        element = self._wait_for_raw(selector)
        self._dm.driver.execute_script(
            """
            arguments[0].value = arguments[1];
            arguments[0].dispatchEvent(new Event('input', { bubbles: true }));
            arguments[0].dispatchEvent(new Event('change', { bubbles: true }));
            """,
            element,
            value,
        )

    def type_text(self, text: str) -> None:
        for char in text:
            ActionChains(self._dm.driver).send_keys(char).perform()
            time.sleep(random.uniform(0.03, 0.1))

    def press_key(self, key: str) -> None:
        ActionChains(self._dm.driver).send_keys(key).perform()

    def hover_element(self, selector: str) -> None:
        element = self._wait_for_raw(selector)
        ActionChains(self._dm.driver).move_to_element(element).perform()

    # ------------------------------------------------------------------
    # Element queries
    # ------------------------------------------------------------------

    def wait_for_element(
        self, selector: str, timeout: float = 10, visible: bool = True
    ) -> StealthElement:
        return StealthElement(self._wait_for_raw(selector, timeout, visible), "selenium")

    def is_visible(self, selector: str) -> bool:
        try:
            el = self._dm.driver.find_element(By.CSS_SELECTOR, selector)
            return el.is_displayed()
        except (NoSuchElementException, StaleElementReferenceException):
            return False

    def exists(self, selector: str) -> bool:
        try:
            self._dm.driver.find_element(By.CSS_SELECTOR, selector)
            return True
        except NoSuchElementException:
            return False

    def get_text(self, selector: str) -> str:
        return self._wait_for_raw(selector).text

    def get_attribute(self, selector: str, attribute: str) -> Optional[str]:
        return self._wait_for_raw(selector).get_attribute(attribute)

    def find_all(self, selector: str) -> List[StealthElement]:
        elements = self._dm.driver.find_elements(By.CSS_SELECTOR, selector)
        return [StealthElement(e, "selenium") for e in elements]

    # ------------------------------------------------------------------
    # Waits
    # ------------------------------------------------------------------

    def wait_for_url(self, substring: str, timeout: float = 30) -> bool:
        try:
            WebDriverWait(self._dm.driver, timeout).until(EC.url_contains(substring))
            return True
        except TimeoutException:
            return False

    def wait_for_url_change(self, original_url: Optional[str] = None, timeout: float = 30) -> bool:
        if original_url is None:
            original_url = self.get_url()
        try:
            WebDriverWait(self._dm.driver, timeout).until(
                lambda d: d.current_url != original_url
            )
            return True
        except TimeoutException:
            return False

    def wait_for_text(self, selector: str, text: str, timeout: float = 10) -> bool:
        try:
            WebDriverWait(self._dm.driver, timeout).until(
                EC.text_to_be_present_in_element((By.CSS_SELECTOR, selector), text)
            )
            return True
        except TimeoutException:
            return False

    # ------------------------------------------------------------------
    # Debug / utility
    # ------------------------------------------------------------------

    def screenshot(self, path: str) -> str:
        self._dm.driver.save_screenshot(path)
        return path

    def get_page_source(self) -> str:
        return self._dm.driver.page_source

    def execute_script(self, script: str, *args: Any) -> Any:
        return self._dm.driver.execute_script(script, *args)

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def clear_state(self) -> None:
        self._dm.clear_state()

    def new_session(self) -> None:
        self._dm.refresh()

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    @property
    def config(self) -> PlatformConfig:
        return self._dm.config

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _wait_for_raw(
        self, selector: str, timeout: float = 10, visible: bool = True
    ) -> WebElement:
        """Wait for a raw Selenium WebElement (not wrapped)."""
        try:
            wait = WebDriverWait(self._dm.driver, timeout)
            if visible:
                cond = EC.visibility_of_element_located((By.CSS_SELECTOR, selector))
            else:
                cond = EC.presence_of_element_located((By.CSS_SELECTOR, selector))
            return wait.until(cond)
        except TimeoutException:
            raise ElementNotFoundError(
                f"Element not found: {selector} (timeout={timeout}s)"
            )

    def _wait_for_page_load(self, timeout: float = 30) -> None:
        WebDriverWait(self._dm.driver, timeout).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )

    def _scroll_to(self, element: WebElement) -> None:
        self._dm.driver.execute_script(
            "arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});",
            element,
        )
        time.sleep(0.3)
