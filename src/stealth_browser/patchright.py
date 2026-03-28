"""Patchright integration for stealth browsing.

Parallel path to the Selenium driver — uses Patchright (patched Playwright)
for CDP leak prevention while reusing our existing config.py and scripts.py.

Patchright avoids Runtime.enable via Page.createIsolatedWorld and removes
Console.enable entirely. Our stealth scripts handle everything else:
fingerprint spoofing, canvas/audio noise, human behavior injection.

Usage:
    from stealth_browser.patchright import (
        create_stealth_browser, create_stealth_context,
        new_stealth_page, stealth_goto, close_stealth_browser,
    )

    browser = await create_stealth_browser()
    context = await create_stealth_context(browser)
    page = await new_stealth_page(context)
    await stealth_goto(page, "https://example.com")
    # ... interact ...
    await close_stealth_browser(browser)
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from stealth_browser.config import BrowserConfig, LocaleConfig, PlatformConfig
from stealth_browser.scripts import StealthScripts
from stealth_browser.stealth_js import (
    extract_chrome_version as _extract_chrome_version,
    build_chrome_ua as _build_chrome_ua,
    build_client_hints_brands as _build_client_hints_brands,
    platform_to_sec_ch as _platform_to_sec_ch,
    build_worker_overrides as _build_worker_overrides,
    build_stealth_scripts as _build_patchright_stealth_scripts,
)

logger = logging.getLogger(__name__)


def _build_launch_args(
    headless: bool,
    config: PlatformConfig,
    extra_args: Optional[List[str]] = None,
) -> List[str]:
    """Build Chromium launch arguments.

    Patchright handles removing --enable-automation and some detection flags.
    We explicitly add --disable-blink-features=AutomationControlled because
    Patchright 1.58.0 doesn't inject it automatically on all platforms.
    """
    args = [
        "--no-sandbox",
        "--disable-dev-shm-usage",
        # Stealth — remove navigator.webdriver at Blink level
        "--disable-blink-features=AutomationControlled",
        # Prevent backgrounding throttle — critical for timed waits
        "--disable-renderer-backgrounding",
        "--disable-backgrounding-occluded-windows",
        "--disable-background-timer-throttling",
        # Misc stability
        "--disable-hang-monitor",
        "--disable-ipc-flooding-protection",
        "--disable-prompt-on-repost",
        "--disable-sync",
        "--disable-translate",
        "--metrics-recording-only",
        "--no-first-run",
        "--password-store=basic",
        "--use-mock-keychain",
        # Window size from config
        f"--window-size={config.viewport_width},{config.viewport_height}",
    ]

    if extra_args:
        args.extend(extra_args)

    return args


async def create_stealth_browser(
    headless: bool = True,
    platform: Optional[str] = None,
    locale: Optional[LocaleConfig] = None,
    auto_detect_locale: bool = False,
    extra_args: Optional[List[str]] = None,
    slow_mo: Optional[int] = None,
    cdp_endpoint: Optional[str] = None,
) -> Any:
    """Launch a stealth browser with genuine TLS fingerprints.

    **Detection resistance strategy:**

    1. **Retail Chrome** (``channel="chrome"``) — uses the system-installed
       Google Chrome binary.  Because it IS real Chrome, TLS (JA3/JA4),
       HTTP/2 SETTINGS, and TCP fingerprints are genuine.  WAFs that compare
       TLS fingerprint against User-Agent will see a perfect match.

    2. **Fallback: bundled Chromium** (``channel="chromium"``) — if retail
       Chrome is not installed.  TLS fingerprint will differ from retail
       Chrome; sophisticated WAFs (Akamai, Cloudflare) may flag this.

    3. **CDP connection** (``cdp_endpoint``) — connects to an already-running
       Chrome instance.  Zero automation artifacts in the binary itself.
       Start Chrome with ``--remote-debugging-port=9222``.

    After launch the actual browser version is detected and the User-Agent
    is rebuilt to match, ensuring UA ↔ TLS coherence.

    Args:
        headless: Run in headless mode (--headless=new)
        platform: Force platform ("windows", "macos") or None for random
        locale: Locale config, or None for California default
        auto_detect_locale: Detect locale from IP
        extra_args: Additional Chromium launch arguments
        slow_mo: Slow down operations by this many ms (for debugging)
        cdp_endpoint: Connect to existing Chrome via CDP (e.g. "http://localhost:9222")

    Returns:
        Patchright Browser instance. Caller must close it.

    Raises:
        ImportError: If patchright is not installed
        BrowserError: If browser launch fails
    """
    try:
        from patchright.async_api import async_playwright
    except ImportError:
        raise ImportError(
            "patchright is not installed. Install with: "
            "pip install 'stealth-browser[patchright]'"
        )

    if locale is None and not auto_detect_locale:
        locale = LocaleConfig.california()

    config = BrowserConfig.get_config(
        platform=platform,
        locale=locale,
        auto_detect_locale=auto_detect_locale,
    )

    pw = await async_playwright().start()

    # ------------------------------------------------------------------
    # Path 1: Connect to an existing Chrome instance via CDP.
    # Most stealth — browser was started by the user, no automation binary.
    # ------------------------------------------------------------------
    if cdp_endpoint:
        browser = await pw.chromium.connect_over_cdp(cdp_endpoint)
        actual_version = browser.version
        major = actual_version.split(".")[0]
        config.user_agent = _build_chrome_ua(major, config.platform_key)
        browser._stealth_config = config
        browser._stealth_pw = pw
        logger.info(
            f"Connected to existing Chrome {actual_version} via CDP: "
            f"platform={config.platform_key}"
        )
        return browser

    # ------------------------------------------------------------------
    # Path 2: Launch retail Chrome (genuine TLS fingerprint).
    # Falls back to bundled Chromium if Chrome is not installed.
    # ------------------------------------------------------------------
    args = _build_launch_args(headless, config, extra_args)

    channel = "chrome"
    try:
        browser = await pw.chromium.launch(
            headless=headless,
            channel=channel,
            args=args,
            slow_mo=slow_mo,
        )
    except Exception as e:
        logger.warning(
            f"Retail Chrome not available ({e}). Falling back to bundled "
            "Chromium — TLS fingerprint will differ from retail Chrome and "
            "may be flagged by sophisticated WAFs (Akamai, Cloudflare)."
        )
        channel = "chromium"
        browser = await pw.chromium.launch(
            headless=headless,
            channel=channel,
            args=args,
            slow_mo=slow_mo,
        )

    # Detect actual version and rebuild UA to match.
    # This ensures the User-Agent string is coherent with the binary's
    # TLS handshake — the #1 signal WAFs use for bot detection.
    actual_version = browser.version  # e.g. "133.0.6943.2"
    major = actual_version.split(".")[0]
    config.user_agent = _build_chrome_ua(major, config.platform_key)

    browser._stealth_config = config
    browser._stealth_pw = pw
    browser._stealth_channel = channel

    logger.info(
        f"Stealth browser launched: channel={channel}, "
        f"version={actual_version}, platform={config.platform_key}, "
        f"ua={config.user_agent[:60]}..."
    )

    return browser


async def _setup_stealth_routes(context: Any, stealth_js: str) -> None:
    """Set up route-based pre-navigation stealth injection.

    Intercepts HTML document responses and injects stealth JS as the first
    <script> in <head>. Also intercepts service worker scripts and prepends
    navigator overrides.

    Non-document requests (images, scripts, XHR, etc.) pass through untouched
    via route.fallback().
    """
    script_tag = f"<script>{stealth_js}</script>"
    config = getattr(context, "_stealth_config", None)
    worker_js = _build_worker_overrides(config) if config else ""

    # Domains where stealth injection is unnecessary and may break pages
    # (e.g., MFA providers that use strict CSP or React SPAs sensitive to
    # inline script injection).
    _passthrough_domains = ("duosecurity.com", "duomobile.com")

    async def _handle_route(route: Any) -> None:
        resource_type = route.request.resource_type
        request_url = route.request.url

        # Skip stealth injection for known-safe domains that don't need it
        if any(domain in request_url for domain in _passthrough_domains):
            await route.fallback()
            return

        # Service worker scripts — prepend navigator overrides
        if resource_type == "serviceworker" and worker_js:
            try:
                response = await route.fetch()
                body = await response.body()
                js = body.decode("utf-8", errors="replace")
                sw_headers = dict(response.headers)
                sw_headers.pop("content-length", None)
                await route.fulfill(
                    status=response.status,
                    headers=sw_headers,
                    body=worker_js + "\n" + js,
                )
                return
            except Exception:
                pass
            try:
                await route.fallback()
            except Exception:
                pass
            return

        # Only intercept document requests (main page + iframes)
        if resource_type not in ("document",):
            await route.fallback()
            return

        try:
            response = await route.fetch()
            body = await response.body()
            content_type = response.headers.get("content-type", "")

            if "html" in content_type:
                html = body.decode("utf-8", errors="replace")

                # Inject as first child of <head>
                head_match = re.search(r"<head[^>]*>", html, re.IGNORECASE)
                if head_match:
                    pos = head_match.end()
                    html = html[:pos] + script_tag + html[pos:]
                else:
                    # No <head> — inject after <html> tag
                    html_match = re.search(r"<html[^>]*>", html, re.IGNORECASE)
                    if html_match:
                        pos = html_match.end()
                        html = html[:pos] + f"<head>{script_tag}</head>" + html[pos:]
                    else:
                        # Bare HTML — prepend
                        html = script_tag + html

                # Remove Content-Length since body size changed after injection.
                # The browser will use chunked transfer or compute it.
                resp_headers = dict(response.headers)
                resp_headers.pop("content-length", None)

                await route.fulfill(
                    status=response.status,
                    headers=resp_headers,
                    body=html,
                )
            else:
                # Non-HTML document (e.g. XML), pass through
                await route.fulfill(response=response)

        except Exception as e:
            logger.debug(f"Route handler error for {route.request.url}: {e}")
            try:
                await route.fallback()
            except Exception:
                pass

    await context.route("**/*", _handle_route)
    logger.debug("Stealth route handler installed on context")


async def create_stealth_context(
    browser: Any,
    config: Optional[PlatformConfig] = None,
    profile_dir: Optional[Path] = None,
) -> Any:
    """Create a browser context with full stealth fingerprint.

    Uses Patchright's native context API for UA, timezone, and locale,
    then injects our stealth scripts for everything else (WebGL, canvas,
    audio, plugins, human behavior, etc.).

    Args:
        browser: Patchright Browser from create_stealth_browser()
        config: Override config (defaults to browser's config)
        profile_dir: Directory for persistent storage state

    Returns:
        Patchright BrowserContext with stealth scripts applied
    """
    if config is None:
        config = getattr(browser, "_stealth_config", None)
        if config is None:
            config = BrowserConfig.get_config()

    chrome_version = _extract_chrome_version(config.user_agent)
    sec_ch_platform = _platform_to_sec_ch(config.platform_key)

    # Context params — Patchright handles Sec-CH-UA sync when we set user_agent
    context_params: Dict[str, Any] = {
        "user_agent": config.user_agent,
        "viewport": {
            "width": config.viewport_width,
            "height": config.viewport_height,
        },
        "locale": config.locale.locale,
        "timezone_id": config.locale.timezone,
        "color_scheme": "light",
        "has_touch": config.touch_enabled,
    }

    if profile_dir:
        profile_dir.mkdir(parents=True, exist_ok=True)
        context_params["storage_state"] = str(profile_dir / "state.json")

    context = await browser.new_context(**context_params)

    # Build stealth JS — injected into HTML documents via route handler
    stealth_js = _build_patchright_stealth_scripts(
        config, chrome_version=chrome_version, sec_ch_platform=sec_ch_platform,
    )

    # Attach config and stealth JS for downstream access
    context._stealth_config = config
    context._stealth_js = stealth_js
    context._stealth_chrome_version = chrome_version
    context._stealth_sec_ch_platform = sec_ch_platform

    # Set up route-based pre-navigation injection.
    # This intercepts HTML document responses and injects our stealth JS
    # as the first <script> in <head>, so it runs before the page's own
    # scripts — critical for properties read at load time (window.chrome,
    # navigator.plugins, navigator.userAgentData, etc.).
    await _setup_stealth_routes(context, stealth_js)

    # NOTE: Service worker navigator overrides are a known limitation.
    # Service workers have their own WorkerNavigator prototypes that
    # can't be patched before the SW initialization code reads them.
    # Playwright/Patchright's addInitScript does NOT apply to worker
    # contexts, and context.route() doesn't intercept SW script fetches.
    # The "serviceworker" event fires after initialization has run.
    # Our stealth JS unregisters cached SWs on page load to minimize
    # stale-value detection, but the SW's own navigator will always
    # reflect real browser values. This affects:
    # - incolumitas: inconsistentServiceWorkerNavigatorPropery
    # - creepjs: ServiceWorkerGlobalScope section
    # Our target (Workday) doesn't use service worker-based detection.

    logger.debug(
        f"Stealth context created: locale={config.locale.timezone}, "
        f"gpu={config.hardware.gpu[:40]}..."
    )

    return context


async def inject_stealth(page: Any, context: Any = None) -> None:
    """Re-inject stealth scripts into a page after SPA navigation.

    For normal navigations (page.goto, page.reload), the context's route
    handler injects stealth JS automatically into the HTML response.
    Use this function only for SPA navigations (pushState/replaceState)
    where the route handler doesn't fire.
    """
    if context is None:
        context = page.context

    stealth_js = getattr(context, "_stealth_js", None)

    if not stealth_js:
        logger.warning("No stealth JS found on context — was create_stealth_context used?")
        return

    try:
        await page.evaluate(stealth_js)
    except Exception as e:
        logger.warning(f"Stealth script re-injection failed: {e}")

    logger.debug("Stealth scripts re-injected into page")


async def stealth_goto(
    page: Any,
    url: str,
    **goto_kwargs: Any,
) -> Any:
    """Navigate with stealth — convenience wrapper around page.goto().

    Stealth JS injection is handled automatically by the context's route
    handler (installed by create_stealth_context). This wrapper exists for
    API compatibility and clarity.

    Args:
        page: Patchright Page object
        url: URL to navigate to
        **goto_kwargs: Passed to page.goto() (timeout, wait_until, etc.)

    Returns:
        Response from page.goto()
    """
    return await page.goto(url, **goto_kwargs)


async def new_stealth_page(context: Any) -> Any:
    """Create a new page in the stealth context.

    Stealth JS injection (including navigator.userAgentData override) happens
    automatically via the context's route handler (set up by create_stealth_context).
    No CDP calls are made — all fingerprint spoofing is pure JS.

    Usage:
        page = await new_stealth_page(context)
        await page.goto("https://example.com")
    """
    page = await context.new_page()
    return page


async def close_stealth_browser(browser: Any) -> None:
    """Close a stealth browser and its Playwright instance."""
    pw = getattr(browser, "_stealth_pw", None)
    try:
        await browser.close()
    except Exception as e:
        logger.debug(f"Error closing browser: {e}")
    if pw:
        try:
            await pw.stop()
        except Exception as e:
            logger.debug(f"Error stopping playwright: {e}")
