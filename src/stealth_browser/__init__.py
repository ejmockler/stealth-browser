"""Stealth Browser - Anti-detection browser automation.

Two engines, one API:
- **Patchright** (default when installed) — CDP-leak-free via patched Playwright
- **Selenium** — fallback, proven path

Install the stronger engine::

    pip install 'stealth-browser[patchright]'

Usage::

    from stealth_browser import StealthBrowser

    with StealthBrowser() as browser:          # auto-selects best engine
        browser.navigate("https://example.com")

    with StealthBrowser(engine="selenium") as browser:  # force Selenium
        ...

Direct async Patchright access is still available::

    from stealth_browser.patchright import create_stealth_browser, ...
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
from stealth_browser.element import StealthElement
from stealth_browser.selectors import (
    CSS_SELECTOR,
    XPATH,
    ID,
    CLASS_NAME,
    NAME,
    TAG_NAME,
    LINK_TEXT,
    PARTIAL_LINK_TEXT,
)
from stealth_browser.engine_selection import patchright_available

PATCHRIGHT_AVAILABLE: bool = patchright_available()

__version__ = "0.3.0"

__all__ = [
    # Core
    "StealthBrowser",
    "StealthElement",
    # Engine detection
    "PATCHRIGHT_AVAILABLE",
    # Legacy — Selenium-specific
    "DriverManager",
    # Config
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
    # Selector strategies
    "CSS_SELECTOR",
    "XPATH",
    "ID",
    "CLASS_NAME",
    "NAME",
    "TAG_NAME",
    "LINK_TEXT",
    "PARTIAL_LINK_TEXT",
]
