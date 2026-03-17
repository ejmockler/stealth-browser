"""Stealth Browser - Anti-detection browser automation.

Two parallel drivers:
- Selenium (StealthBrowser) — existing, proven path
- Patchright (create_stealth_browser) — CDP-leak-free path via patched Playwright

Both share config.py (fingerprint pools) and scripts.py (JS injection).

Selenium usage:
    from stealth_browser import StealthBrowser

    with StealthBrowser() as browser:
        browser.navigate("https://example.com")

Patchright usage:
    from stealth_browser.patchright import create_stealth_browser, create_stealth_context

    async with await create_stealth_browser() as browser:
        context = await create_stealth_context(browser)
        page = await context.new_page()
        await page.goto("https://example.com")
"""

from stealth_browser.browser import StealthBrowser
from stealth_browser.driver import DriverManager
from stealth_browser.config import (
    BrowserConfig,
    PlatformConfig,
    HardwareConfig,
    NetworkConfig,
    LocaleConfig,
)
from stealth_browser.scripts import StealthScripts
from stealth_browser.exceptions import BrowserError, NavigationError, ElementNotFoundError

__version__ = "0.2.0"

__all__ = [
    # Selenium path
    "StealthBrowser",
    "DriverManager",
    # Shared config
    "BrowserConfig",
    "PlatformConfig",
    "HardwareConfig",
    "NetworkConfig",
    "LocaleConfig",
    "StealthScripts",
    # Exceptions
    "BrowserError",
    "NavigationError",
    "ElementNotFoundError",
]
