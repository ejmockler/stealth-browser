"""Native engine — Chrome Extension + OS input, zero CDP.

Launches Chrome as a normal process (no --remote-debugging-port), communicates
with a bundled extension for DOM access, and uses OS-level input injection for
mouse/keyboard events.  Indistinguishable from a human-operated browser.
"""

from __future__ import annotations

import base64
import logging
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, List, Optional

from stealth_browser.config import BrowserConfig, LocaleConfig, PlatformConfig
from stealth_browser.element import StealthElement
from stealth_browser.exceptions import BrowserError, ElementNotFoundError
from stealth_browser.native.bridge import ExtensionBridge
from stealth_browser.native.chrome import detect_chrome_version, launch_chrome
from stealth_browser.native.human import click_at, move_to, press_key, select_all, type_text
from stealth_browser.native.input import create_backend
from stealth_browser.stealth_js import build_chrome_ua, build_stealth_scripts

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Element handle for native engine
# ------------------------------------------------------------------

class NativeElementHandle:
    """Snapshot-based element handle.

    Captures element data at query time (text, tag, visibility, attributes).
    Property access is local — no round-trips to the extension.
    """

    __slots__ = ("text", "tag_name", "_visible", "_attrs")

    def __init__(self, data: dict) -> None:
        self.text: str = data.get("text", "")
        self.tag_name: str = data.get("tagName", "")
        self._visible: bool = data.get("visible", False)
        self._attrs: dict = data.get("attributes", {})

    def is_displayed(self) -> bool:
        return self._visible

    def get_attribute(self, name: str) -> Optional[str]:
        return self._attrs.get(name)

    @property
    def value(self) -> str:
        return self._attrs.get("value", "")


# ------------------------------------------------------------------
# NativeEngine
# ------------------------------------------------------------------

class NativeEngine:
    """BrowserEngine backed by Chrome Extension + OS input (no CDP)."""

    def __init__(
        self,
        output_dir: Optional[Path] = None,
        headless: bool = False,
        platform: Optional[str] = None,
        locale: Optional[LocaleConfig] = None,
        auto_detect_locale: bool = False,
        profile_dir: Optional[Path] = None,
    ) -> None:
        if headless:
            raise BrowserError(
                "Native engine requires a visible window (headless=False). "
                "Use Xvfb on Linux for headless-like behavior."
            )

        self._output_dir = output_dir
        self._platform_arg = platform
        self._locale_arg = locale
        self._auto_detect_locale = auto_detect_locale
        self._profile_dir = profile_dir

        self._config: Optional[PlatformConfig] = None
        self._backend = create_backend()
        self._bridge = ExtensionBridge()
        self._chrome_process = None
        self._ext_dir: Optional[str] = None
        self._temp_profile: Optional[str] = None
        self._cursor_x: int = 0
        self._cursor_y: int = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Launch Chrome with the extension and wait for connection."""
        from stealth_browser.native.extension import generate_extension

        # 1. Generate config
        self._config = BrowserConfig.get_config(
            platform=self._platform_arg,
            locale=self._locale_arg,
            auto_detect_locale=self._auto_detect_locale,
        )

        # 2. Detect Chrome version and update UA for coherence
        from stealth_browser.native.chrome import find_chrome
        chrome_path = find_chrome()
        major = detect_chrome_version(chrome_path)
        self._config.user_agent = build_chrome_ua(major, self._config.platform_key)

        # 3. Build stealth JS (skip screen overrides — we need real coords)
        stealth_js = build_stealth_scripts(
            self._config,
            chrome_version=major,
            skip_screen_overrides=True,
        )

        # 4. Start WebSocket server
        ws_port = self._bridge.start()

        # 5. Generate extension files and write to temp dir
        ext_files = generate_extension(ws_port=ws_port, stealth_js=stealth_js)
        self._ext_dir = tempfile.mkdtemp(prefix="stealth_ext_")
        for filename, content in ext_files.items():
            (Path(self._ext_dir) / filename).write_text(content, encoding="utf-8")

        # 6. Determine profile directory
        if self._profile_dir:
            self._profile_dir.mkdir(parents=True, exist_ok=True)
            user_data = str(self._profile_dir)
        else:
            self._temp_profile = tempfile.mkdtemp(prefix="stealth_profile_")
            user_data = self._temp_profile

        # 7. Launch Chrome
        self._chrome_process = launch_chrome(
            extension_dir=self._ext_dir,
            user_data_dir=user_data,
            window_size=(self._config.viewport_width, self._config.viewport_height),
        )

        # 8. Wait for extension to connect
        self._bridge.wait_for_connection(timeout=30)
        logger.info("Native engine started — extension connected")

    def close(self) -> None:
        """Kill Chrome, stop WebSocket server, clean up temp dirs."""
        if self._chrome_process:
            try:
                self._chrome_process.terminate()
                self._chrome_process.wait(timeout=5)
            except Exception:
                try:
                    self._chrome_process.kill()
                except Exception:
                    pass
            self._chrome_process = None

        self._bridge.close()

        if self._ext_dir:
            shutil.rmtree(self._ext_dir, ignore_errors=True)
            self._ext_dir = None

        if self._temp_profile:
            shutil.rmtree(self._temp_profile, ignore_errors=True)
            self._temp_profile = None

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def navigate(self, url: str, wait_for_load: bool = True, timeout: float = 30) -> None:
        self._bridge.send("navigate", {"url": url, "wait_for_load": wait_for_load}, timeout=timeout)

    def refresh_page(self) -> None:
        self._bridge.send("reload", {})

    def back(self) -> None:
        self._bridge.send("back", {})

    def forward(self) -> None:
        self._bridge.send("forward", {})

    def get_url(self) -> str:
        result = self._bridge.send("get_url", {}, timeout=5)
        return result.get("url", "")

    def get_title(self) -> str:
        result = self._bridge.send("get_title", {}, timeout=5)
        return result.get("title", "")

    # ------------------------------------------------------------------
    # Element interaction
    # ------------------------------------------------------------------

    def click_element(self, selector: str, timeout: float = 10, scroll: bool = True) -> None:
        self.wait_for_element(selector, timeout=timeout, visible=True)

        if scroll:
            self._bridge.send("scroll_to", {"selector": selector}, timeout=timeout)
            time.sleep(0.3)

        result = self._bridge.send("locate", {"selector": selector}, timeout=timeout)
        if not result.get("exists"):
            raise ElementNotFoundError(f"Element not found: {selector}")

        x, y = result["x"], result["y"]
        click_at(self._backend, x, y, self._cursor_x, self._cursor_y)
        self._cursor_x, self._cursor_y = x, y

    def fill_element(
        self,
        selector: str,
        value: str,
        clear: bool = True,
        human_typing: bool = True,
        timeout: float = 10,
    ) -> None:
        self.wait_for_element(selector, timeout=timeout)

        if clear:
            # JS clear is reliable and instant; OS input for typing is what
            # matters for detection evasion.
            self._bridge.send("fill_fast", {"selector": selector, "value": ""})

        # Click to focus, then pause for focus to settle
        self.click_element(selector, timeout=timeout)
        time.sleep(0.15)

        if human_typing:
            type_text(self._backend, value)
        else:
            self._bridge.send("fill_fast", {"selector": selector, "value": value})

    def fill_fast(self, selector: str, value: str) -> None:
        self._bridge.send("fill_fast", {"selector": selector, "value": value})

    def type_text(self, text: str) -> None:
        type_text(self._backend, text)

    def press_key(self, key: str) -> None:
        press_key(self._backend, key)

    def hover_element(self, selector: str) -> None:
        result = self._bridge.send("locate", {"selector": selector}, timeout=10)
        if not result.get("exists"):
            raise ElementNotFoundError(f"Element not found: {selector}")
        x, y = result["x"], result["y"]
        move_to(self._backend, self._cursor_x, self._cursor_y, x, y)
        self._cursor_x, self._cursor_y = x, y

    # ------------------------------------------------------------------
    # Element queries
    # ------------------------------------------------------------------

    def wait_for_element(
        self, selector: str, timeout: float = 10, visible: bool = True,
    ) -> StealthElement:
        result = self._bridge.send(
            "wait_for",
            {"selector": selector, "timeout": int(timeout * 1000), "visible": visible},
            timeout=timeout + 2,
        )
        if not result.get("found"):
            raise ElementNotFoundError(
                f"Element not found within {timeout}s: {selector}"
            )
        return StealthElement(NativeElementHandle(result), "native")

    def is_visible(self, selector: str) -> bool:
        result = self._bridge.send("is_visible", {"selector": selector}, timeout=5)
        return result.get("visible", False)

    def exists(self, selector: str) -> bool:
        result = self._bridge.send("exists", {"selector": selector}, timeout=5)
        return result.get("exists", False)

    def get_text(self, selector: str) -> str:
        result = self._bridge.send("get_text", {"selector": selector}, timeout=5)
        return result.get("text", "")

    def get_attribute(self, selector: str, attribute: str) -> Optional[str]:
        result = self._bridge.send(
            "get_attribute", {"selector": selector, "attribute": attribute}, timeout=5,
        )
        return result.get("value")

    def find_all(self, selector: str) -> List[StealthElement]:
        results = self._bridge.send("query_all", {"selector": selector}, timeout=10)
        if isinstance(results, list):
            return [
                StealthElement(NativeElementHandle(data), "native")
                for data in results
            ]
        return []

    # ------------------------------------------------------------------
    # Waits
    # ------------------------------------------------------------------

    def wait_for_url(self, substring: str, timeout: float = 30) -> bool:
        result = self._bridge.send(
            "wait_for_url",
            {"substring": substring, "timeout": int(timeout * 1000)},
            timeout=timeout + 2,
        )
        return result.get("matched", False)

    def wait_for_url_change(
        self, original_url: Optional[str] = None, timeout: float = 30,
    ) -> bool:
        if original_url is None:
            original_url = self.get_url()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.get_url() != original_url:
                return True
            time.sleep(0.2)
        return False

    def wait_for_text(self, selector: str, text: str, timeout: float = 10) -> bool:
        result = self._bridge.send(
            "wait_for_text",
            {"selector": selector, "text": text, "timeout": int(timeout * 1000)},
            timeout=timeout + 2,
        )
        return result.get("found", False)

    # ------------------------------------------------------------------
    # Debug / utility
    # ------------------------------------------------------------------

    def screenshot(self, path: str) -> str:
        result = self._bridge.send("screenshot", {}, timeout=10)
        data_url = result.get("dataUrl", "")
        if data_url.startswith("data:image/png;base64,"):
            b64 = data_url.split(",", 1)[1]
            Path(path).write_bytes(base64.b64decode(b64))
        return path

    def get_page_source(self) -> str:
        result = self._bridge.send("page_source", {}, timeout=10)
        return result.get("html", "")

    def execute_script(self, script: str, *args: Any) -> Any:
        result = self._bridge.send("execute_script", {"code": script}, timeout=30)
        return result.get("result")

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def clear_state(self) -> None:
        self._bridge.send("clear_state", {}, timeout=10)

    def new_session(self) -> None:
        self.close()
        self._config = None
        self._backend = create_backend()
        self._bridge = ExtensionBridge()
        self.start()

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    @property
    def config(self) -> PlatformConfig:
        if self._config is None:
            raise BrowserError("Engine not started — call start() first")
        return self._config
