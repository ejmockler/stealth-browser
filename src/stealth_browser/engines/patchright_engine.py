"""Patchright backend implementing ``BrowserEngine``.

Wraps the async Patchright/Playwright API through :class:`SyncBridge` so
that ``StealthBrowser`` can call it synchronously like the Selenium backend.
"""

from __future__ import annotations

import json
import logging
import random
import time
from pathlib import Path
from typing import Any, List, Optional

from stealth_browser.config import LocaleConfig, PlatformConfig
from stealth_browser.element import StealthElement
from stealth_browser.exceptions import (
    BrowserError,
    ElementNotFoundError,
    NavigationError,
)
from stealth_browser.selectors import translate_key
from stealth_browser.sync_bridge import SyncBridge

logger = logging.getLogger(__name__)


class PatchrightEngine:
    """``BrowserEngine`` implementation backed by Patchright (patched Playwright)."""

    def __init__(
        self,
        output_dir: Optional[Path] = None,
        headless: bool = True,
        platform: Optional[str] = None,
        locale: Optional[LocaleConfig] = None,
        auto_detect_locale: bool = False,
        profile_dir: Optional[Path] = None,
        cdp_endpoint: Optional[str] = None,
    ) -> None:
        self._output_dir = output_dir or Path.home() / ".cache" / "stealth-browser"
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._headless = headless
        self._platform = platform
        self._locale = locale
        self._auto_detect_locale = auto_detect_locale
        self._profile_dir = profile_dir
        self._cdp_endpoint = cdp_endpoint

        self._bridge = SyncBridge()
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._config: Optional[PlatformConfig] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        from stealth_browser.patchright import (
            create_stealth_browser,
            create_stealth_context,
            new_stealth_page,
        )

        self._browser = self._bridge.run(
            create_stealth_browser(
                headless=self._headless,
                platform=self._platform,
                locale=self._locale,
                auto_detect_locale=self._auto_detect_locale,
                cdp_endpoint=self._cdp_endpoint,
            )
        )
        self._config = self._browser._stealth_config

        self._context = self._bridge.run(
            create_stealth_context(
                self._browser,
                config=self._config,
                profile_dir=self._profile_dir,
            )
        )
        self._page = self._bridge.run(new_stealth_page(self._context))

    def close(self) -> None:
        if self._browser is not None:
            try:
                from stealth_browser.patchright import close_stealth_browser

                self._bridge.run(close_stealth_browser(self._browser))
            except Exception:
                logger.debug("Error during Patchright browser close", exc_info=True)
            finally:
                self._browser = None
                self._context = None
                self._page = None
        self._bridge.close()

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def navigate(self, url: str, wait_for_load: bool = True, timeout: float = 30) -> None:
        try:
            wait_until = "load" if wait_for_load else "commit"
            self._bridge.run(
                self._page.goto(url, wait_until=wait_until, timeout=timeout * 1000)
            )
        except BrowserError as e:
            raise NavigationError(f"Failed to navigate to {url}: {e}") from e

    def refresh_page(self) -> None:
        self._bridge.run(self._page.reload(wait_until="load"))

    def back(self) -> None:
        self._bridge.run(self._page.go_back(wait_until="load"))

    def forward(self) -> None:
        self._bridge.run(self._page.go_forward(wait_until="load"))

    def get_url(self) -> str:
        return self._page.url

    def get_title(self) -> str:
        return self._bridge.run(self._page.title())

    # ------------------------------------------------------------------
    # Element interaction
    # ------------------------------------------------------------------

    def click_element(self, selector: str, timeout: float = 10, scroll: bool = True) -> None:
        self._bridge.run(
            self._page.click(selector, timeout=timeout * 1000)
        )

    def fill_element(
        self,
        selector: str,
        value: str,
        clear: bool = True,
        human_typing: bool = True,
        timeout: float = 10,
    ) -> None:
        if human_typing:
            # Click first, optionally clear, then type char-by-char
            self._bridge.run(self._page.click(selector, timeout=timeout * 1000))
            if clear:
                self._bridge.run(self._page.fill(selector, ""))
            for char in value:
                self._bridge.run(self._page.keyboard.type(char))
                time.sleep(random.uniform(0.03, 0.12))
        else:
            if clear:
                self._bridge.run(
                    self._page.fill(selector, value, timeout=timeout * 1000)
                )
            else:
                self._bridge.run(self._page.click(selector, timeout=timeout * 1000))
                self._bridge.run(self._page.keyboard.type(value))

    def fill_fast(self, selector: str, value: str) -> None:
        self._bridge.run(
            self._page.evaluate(
                """([sel, val]) => {
                    const el = document.querySelector(sel);
                    if (!el) throw new Error('Element not found: ' + sel);
                    el.value = val;
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                }""",
                [selector, value],
            )
        )

    def type_text(self, text: str) -> None:
        for char in text:
            self._bridge.run(self._page.keyboard.type(char))
            time.sleep(random.uniform(0.03, 0.1))

    def press_key(self, key: str) -> None:
        self._bridge.run(self._page.keyboard.press(translate_key(key)))

    def hover_element(self, selector: str) -> None:
        self._bridge.run(self._page.hover(selector))

    # ------------------------------------------------------------------
    # Element queries
    # ------------------------------------------------------------------

    def wait_for_element(
        self, selector: str, timeout: float = 10, visible: bool = True
    ) -> StealthElement:
        state = "visible" if visible else "attached"
        handle = self._bridge.run(
            self._page.wait_for_selector(selector, state=state, timeout=timeout * 1000)
        )
        if handle is None:
            raise ElementNotFoundError(f"Element not found: {selector}")
        return StealthElement(handle, "patchright")

    def is_visible(self, selector: str) -> bool:
        return self._bridge.run(self._page.is_visible(selector))

    def exists(self, selector: str) -> bool:
        count = self._bridge.run(self._page.locator(selector).count())
        return count > 0

    def get_text(self, selector: str) -> str:
        return self._bridge.run(self._page.text_content(selector)) or ""

    def get_attribute(self, selector: str, attribute: str) -> Optional[str]:
        return self._bridge.run(self._page.get_attribute(selector, attribute))

    def find_all(self, selector: str) -> List[StealthElement]:
        handles = self._bridge.run(self._page.query_selector_all(selector))
        return [StealthElement(h, "patchright") for h in handles]

    # ------------------------------------------------------------------
    # Waits
    # ------------------------------------------------------------------

    def wait_for_url(self, substring: str, timeout: float = 30) -> bool:
        try:
            self._bridge.run(
                self._page.wait_for_url(f"**/*{substring}*", timeout=timeout * 1000)
            )
            return True
        except BrowserError:
            return False

    def wait_for_url_change(self, original_url: Optional[str] = None, timeout: float = 30) -> bool:
        if original_url is None:
            original_url = self._page.url
        try:
            self._bridge.run(
                self._page.wait_for_url(
                    lambda url: url != original_url, timeout=timeout * 1000
                )
            )
            return True
        except BrowserError:
            return False

    def wait_for_text(self, selector: str, text: str, timeout: float = 10) -> bool:
        try:
            locator = self._page.locator(selector)
            self._bridge.run(
                locator.filter(has_text=text).first.wait_for(
                    state="visible", timeout=timeout * 1000
                )
            )
            return True
        except BrowserError:
            return False

    # ------------------------------------------------------------------
    # Debug / utility
    # ------------------------------------------------------------------

    def screenshot(self, path: str) -> str:
        self._bridge.run(self._page.screenshot(path=path))
        return path

    def get_page_source(self) -> str:
        return self._bridge.run(self._page.content())

    def execute_script(self, script: str, *args: Any) -> Any:
        # Selenium scripts use ``return expr`` and access positional args via
        # ``arguments[0]``, ``arguments[1]``, etc.  Playwright's evaluate()
        # expects an expression (or arrow function) and takes a single arg.
        # We wrap the Selenium-style script in an IIFE to bridge the gap.
        wrapped = f"(arguments) => {{ {script} }}"
        if args:
            return self._bridge.run(self._page.evaluate(wrapped, list(args)))
        return self._bridge.run(self._page.evaluate(wrapped, []))

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def clear_state(self) -> None:
        """Close the current context and create a fresh one (new fingerprint)."""
        from stealth_browser.patchright import create_stealth_context, new_stealth_page

        if self._context is not None:
            try:
                self._bridge.run(self._context.close())
            except Exception:
                pass

        self._context = self._bridge.run(
            create_stealth_context(self._browser, config=self._config)
        )
        self._page = self._bridge.run(new_stealth_page(self._context))

    def new_session(self) -> None:
        """Close everything and re-launch with a new fingerprint."""
        self.close()
        # Re-initialise bridge (previous one was closed)
        self._bridge = SyncBridge()
        self._config = None
        self.start()

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    @property
    def config(self) -> PlatformConfig:
        if self._config is None:
            raise BrowserError("Engine not started — call start() first")
        return self._config
