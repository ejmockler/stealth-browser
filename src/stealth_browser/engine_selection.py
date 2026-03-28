"""Engine auto-detection and factory."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from stealth_browser.config import LocaleConfig

logger = logging.getLogger(__name__)


def patchright_available() -> bool:
    """Return ``True`` if the ``patchright`` package is importable."""
    try:
        import patchright  # noqa: F401
        return True
    except ImportError:
        return False


def detect_engine() -> str:
    """Return the name of the best available engine.

    Prefers ``"patchright"`` (stronger anti-detection) when installed,
    falls back to ``"selenium"``.
    """
    if patchright_available():
        return "patchright"
    return "selenium"


def create_engine(
    engine: Optional[str] = None,
    *,
    output_dir: Optional[Path] = None,
    headless: bool = True,
    platform: Optional[str] = None,
    locale: Optional[LocaleConfig] = None,
    auto_detect_locale: bool = False,
    profile_dir: Optional[Path] = None,
    cdp_endpoint: Optional[str] = None,
):
    """Instantiate a :class:`BrowserEngine` backend.

    Parameters
    ----------
    engine:
        ``"patchright"``, ``"selenium"``, or ``None`` (auto-detect).
    cdp_endpoint:
        Connect to an existing Chrome via CDP (e.g. ``"http://localhost:9222"``).
        Most stealth — genuine TLS fingerprint since Chrome was started normally.
        Only supported with the Patchright engine.
    """
    if engine is None:
        engine = detect_engine()
        logger.info(f"Auto-selected engine: {engine}")

    kwargs = dict(
        output_dir=output_dir,
        headless=headless,
        platform=platform,
        locale=locale,
        auto_detect_locale=auto_detect_locale,
        profile_dir=profile_dir,
    )

    if engine == "patchright":
        from stealth_browser.engines.patchright_engine import PatchrightEngine
        kwargs["cdp_endpoint"] = cdp_endpoint
        return PatchrightEngine(**kwargs)

    if engine == "selenium":
        if cdp_endpoint:
            logger.warning("cdp_endpoint is only supported with the Patchright engine, ignoring")
        from stealth_browser.engines.selenium_engine import SeleniumEngine
        return SeleniumEngine(**kwargs)

    if engine == "native":
        if cdp_endpoint:
            logger.warning("cdp_endpoint is not used with the native engine, ignoring")
        from stealth_browser.engines.native_engine import NativeEngine
        kwargs.pop("cdp_endpoint", None)
        return NativeEngine(**kwargs)

    raise ValueError(f"Unknown engine: {engine!r} (expected 'patchright', 'selenium', or 'native')")
