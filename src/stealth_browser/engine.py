"""Browser engine protocol — the backend contract.

Both ``SeleniumEngine`` and ``PatchrightEngine`` implement this protocol so
that ``StealthBrowser`` can delegate to either one transparently.

All selectors arriving here are already in the engine's native format
(translated by ``selectors.translate_selector`` at the ``StealthBrowser``
call-site).  All methods are synchronous — the Patchright backend uses
``SyncBridge`` internally to satisfy this.
"""

from __future__ import annotations

from typing import Any, List, Optional, Protocol, runtime_checkable

from stealth_browser.config import PlatformConfig
from stealth_browser.element import StealthElement


@runtime_checkable
class BrowserEngine(Protocol):
    """Protocol every browser backend must satisfy."""

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Launch the browser and prepare a usable page/tab."""
        ...

    def close(self) -> None:
        """Tear down the browser and free resources."""
        ...

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def navigate(self, url: str, wait_for_load: bool = True, timeout: float = 30) -> None: ...
    def refresh_page(self) -> None: ...
    def back(self) -> None: ...
    def forward(self) -> None: ...
    def get_url(self) -> str: ...
    def get_title(self) -> str: ...

    # ------------------------------------------------------------------
    # Element interaction
    # ------------------------------------------------------------------

    def click_element(
        self, selector: str, timeout: float = 10, scroll: bool = True
    ) -> None: ...

    def fill_element(
        self,
        selector: str,
        value: str,
        clear: bool = True,
        human_typing: bool = True,
        timeout: float = 10,
    ) -> None: ...

    def fill_fast(self, selector: str, value: str) -> None: ...
    def type_text(self, text: str) -> None: ...
    def press_key(self, key: str) -> None: ...
    def hover_element(self, selector: str) -> None: ...

    # ------------------------------------------------------------------
    # Element queries
    # ------------------------------------------------------------------

    def wait_for_element(
        self, selector: str, timeout: float = 10, visible: bool = True
    ) -> StealthElement: ...

    def is_visible(self, selector: str) -> bool: ...
    def exists(self, selector: str) -> bool: ...
    def get_text(self, selector: str) -> str: ...
    def get_attribute(self, selector: str, attribute: str) -> Optional[str]: ...
    def find_all(self, selector: str) -> List[StealthElement]: ...

    # ------------------------------------------------------------------
    # Waits
    # ------------------------------------------------------------------

    def wait_for_url(self, substring: str, timeout: float = 30) -> bool: ...
    def wait_for_url_change(self, original_url: Optional[str] = None, timeout: float = 30) -> bool: ...
    def wait_for_text(self, selector: str, text: str, timeout: float = 10) -> bool: ...

    # ------------------------------------------------------------------
    # Debug / utility
    # ------------------------------------------------------------------

    def screenshot(self, path: str) -> str: ...
    def get_page_source(self) -> str: ...
    def execute_script(self, script: str, *args: Any) -> Any: ...

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def clear_state(self) -> None: ...
    def new_session(self) -> None: ...

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    @property
    def config(self) -> PlatformConfig: ...
