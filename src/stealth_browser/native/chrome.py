"""Chrome process management for the native engine.

Provides functions to locate the Chrome binary, detect its version,
and launch it as a normal (non-CDP) process with a messaging extension.
"""

import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from stealth_browser.exceptions import BrowserError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Platform-specific Chrome search paths
# ---------------------------------------------------------------------------

_MACOS_PATHS = [
    # Chrome for Testing (supports --load-extension, unlike retail Chrome)
    "/Applications/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
    # Retail Chrome last — blocks --load-extension on stable channel
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    str(Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
]

_LINUX_WHICH_NAMES = [
    "google-chrome",
    "google-chrome-stable",
    "chromium-browser",
    "chromium",
]

_LINUX_FALLBACK = "/usr/bin/google-chrome"

_FALLBACK_VERSION = "122"


def _find_playwright_chrome() -> Optional[str]:
    """Search Playwright/Patchright browser cache for Chrome for Testing.

    Chrome for Testing is an automation-friendly build that supports
    --load-extension (unlike retail Chrome stable which blocks it).
    """
    cache_dirs = []
    if sys.platform == "darwin":
        cache_dirs.append(Path.home() / "Library" / "Caches" / "ms-playwright")
    else:
        cache_dirs.append(Path.home() / ".cache" / "ms-playwright")

    for cache_dir in cache_dirs:
        if not cache_dir.is_dir():
            continue
        # Look for chromium-* directories (sorted descending for newest first)
        for chromium_dir in sorted(cache_dir.glob("chromium-*"), reverse=True):
            if sys.platform == "darwin":
                # macOS: look for .app bundle
                for app in chromium_dir.rglob("*.app"):
                    binary = app / "Contents" / "MacOS" / app.stem
                    if binary.is_file() and os.access(str(binary), os.X_OK):
                        logger.info("Found Chrome for Testing at %s", binary)
                        return str(binary)
            else:
                # Linux: look for chrome or chromium binary
                for name in ("chrome", "chromium", "Google Chrome for Testing"):
                    for binary in chromium_dir.rglob(name):
                        if binary.is_file() and os.access(str(binary), os.X_OK):
                            logger.info("Found Chrome for Testing at %s", binary)
                            return str(binary)
    return None


# ---------------------------------------------------------------------------
# find_chrome
# ---------------------------------------------------------------------------


def find_chrome() -> str:
    """Find the Chrome binary on the system.

    Returns the absolute path to the Chrome executable.

    Raises:
        BrowserError: If Chrome cannot be found on this system.
    """
    if sys.platform == "darwin":
        return _find_chrome_macos()
    elif sys.platform == "win32":
        return _find_chrome_windows()
    else:
        return _find_chrome_linux()


def _find_chrome_macos() -> str:
    # Prefer Playwright/Patchright Chrome for Testing (supports --load-extension)
    cft = _find_playwright_chrome()
    if cft:
        return cft

    # Then check standard paths (Chrome for Testing, Chromium, Canary, retail)
    for path in _MACOS_PATHS:
        if os.path.isfile(path):
            logger.info("Found Chrome at %s", path)
            return path

    raise BrowserError(
        "Chrome for Testing or Chromium not found. Retail Google Chrome "
        "blocks --load-extension. Install via: npx @playwright/test install chromium"
    )


def _find_chrome_windows() -> str:
    # Standard installation directories
    env_dirs = [
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        os.environ.get("LOCALAPPDATA", ""),
    ]
    for base in env_dirs:
        if not base:
            continue
        candidate = os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")
        if os.path.isfile(candidate):
            logger.info("Found Chrome at %s", candidate)
            return candidate

    # Fall back to the Windows registry
    try:
        import winreg  # type: ignore[import-not-found]

        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe",
        )
        chrome_path, _ = winreg.QueryValueEx(key, "")
        winreg.CloseKey(key)
        if chrome_path and os.path.isfile(chrome_path):
            logger.info("Found Chrome via registry at %s", chrome_path)
            return chrome_path
    except Exception:
        pass

    raise BrowserError(
        "Chrome not found. Install Google Chrome on Windows."
    )


def _find_chrome_linux() -> str:
    for name in _LINUX_WHICH_NAMES:
        path = shutil.which(name)
        if path:
            logger.info("Found Chrome at %s", path)
            return path

    if os.path.isfile(_LINUX_FALLBACK):
        logger.info("Found Chrome at %s", _LINUX_FALLBACK)
        return _LINUX_FALLBACK

    # Check Playwright/Patchright cache
    cft = _find_playwright_chrome()
    if cft:
        return cft

    raise BrowserError(
        "Chrome not found. Install google-chrome, chromium, or run: "
        "npx @playwright/test install chromium"
    )


# ---------------------------------------------------------------------------
# detect_chrome_version
# ---------------------------------------------------------------------------


def detect_chrome_version(chrome_path: str) -> str:
    """Detect the major version of Chrome at *chrome_path*.

    Runs ``chrome_path --version`` and parses the output.

    Returns:
        The major version as a string (e.g. ``"133"``).
        Falls back to ``"122"`` on any error.
    """
    try:
        result = subprocess.run(
            [chrome_path, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        output = result.stdout.strip()
        # Handles both "Google Chrome 133.0.6943.2" and "Chromium 133.0.6943.2"
        match = re.search(r"(\d+)\.\d+\.\d+", output)
        if match:
            version = match.group(1)
            logger.info("Detected Chrome version %s from: %s", version, output)
            return version
    except Exception as exc:
        logger.warning("Failed to detect Chrome version: %s", exc)

    logger.info("Using fallback Chrome version %s", _FALLBACK_VERSION)
    return _FALLBACK_VERSION


# ---------------------------------------------------------------------------
# launch_chrome
# ---------------------------------------------------------------------------


def launch_chrome(
    extension_dir: str,
    user_data_dir: str,
    window_size: Tuple[int, int] = (1920, 1080),
    extra_args: Optional[List[str]] = None,
) -> subprocess.Popen:
    """Launch Chrome as a normal process — no CDP flags.

    Args:
        extension_dir: Path to the unpacked Chrome extension directory.
        user_data_dir: Path to the user-data directory for this session.
        window_size: ``(width, height)`` tuple for the browser window.
        extra_args: Additional command-line flags to pass to Chrome.

    Returns:
        A :class:`subprocess.Popen` handle for the Chrome process.
    """
    chrome_path = find_chrome()
    w, h = window_size

    args = [
        chrome_path,
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-default-apps",
        "--disable-background-networking",
        "--disable-sync",
        "--disable-translate",
        "--disable-blink-features=AutomationControlled",
        "--disable-hang-monitor",
        "--disable-ipc-flooding-protection",
        "--disable-renderer-backgrounding",
        "--disable-backgrounding-occluded-windows",
        "--disable-background-timer-throttling",
        "--metrics-recording-only",
        "--password-store=basic",
        "--use-mock-keychain",
        f"--window-size={w},{h}",
        f"--load-extension={extension_dir}",
        f"--user-data-dir={user_data_dir}",
    ]

    if extra_args:
        args.extend(extra_args)

    # The initial page to load
    args.append("about:blank")

    version = detect_chrome_version(chrome_path)
    logger.info(
        "Launching Chrome %s: %s (window %dx%d)",
        version,
        chrome_path,
        w,
        h,
    )

    process = subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    logger.info("Chrome launched with PID %d", process.pid)

    # Bring Chrome to the foreground so OS-level input events reach it.
    _activate_chrome_window(process.pid)

    return process


def _activate_chrome_window(pid: int) -> None:
    """Bring the Chrome window to the foreground."""
    import time
    time.sleep(1)  # Wait for Chrome to create its window

    if sys.platform == "darwin":
        try:
            # Use AppleScript to activate by PID
            subprocess.run(
                ["osascript", "-e",
                 f'tell application "System Events" to set frontmost of '
                 f'(first process whose unix id is {pid}) to true'],
                timeout=3, capture_output=True,
            )
            logger.debug("Activated Chrome window (PID %d)", pid)
        except Exception as exc:
            logger.debug("Could not activate Chrome window: %s", exc)
    elif sys.platform == "win32":
        try:
            import ctypes
            # EnumWindows to find the Chrome window by PID
            user32 = ctypes.windll.user32

            def callback(hwnd, _):
                wid = ctypes.c_ulong()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wid))
                if wid.value == pid and user32.IsWindowVisible(hwnd):
                    user32.SetForegroundWindow(hwnd)
                    return False
                return True

            WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
            user32.EnumWindows(WNDENUMPROC(callback), 0)
        except Exception as exc:
            logger.debug("Could not activate Chrome window: %s", exc)
    else:
        try:
            subprocess.run(
                ["xdotool", "search", "--pid", str(pid), "--onlyvisible",
                 "windowactivate"],
                timeout=3, capture_output=True,
            )
        except Exception as exc:
            logger.debug("Could not activate Chrome window: %s", exc)
