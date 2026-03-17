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

logger = logging.getLogger(__name__)


def _extract_chrome_version(user_agent: str) -> Optional[str]:
    """Extract Chrome major version from User-Agent string."""
    match = re.search(r"Chrome/(\d+)", user_agent)
    return match.group(1) if match else None


def _build_client_hints_brands(chrome_version: str) -> List[Dict[str, str]]:
    """Build Sec-CH-UA brand list matching Chrome's format.

    Chrome uses a rotating "Not A Brand" pattern that changes per major version.
    The brand list order and "Not A Brand" variant must match the claimed version.
    """
    # Chrome rotates the "Not A Brand" string and position across versions.
    # This covers common patterns — extend as needed.
    v = int(chrome_version)
    not_a_brand_variants = [
        "Not_A Brand",
        "Not A(Brand",
        "Not/A)Brand",
        "Not;A Brand",
    ]
    variant = not_a_brand_variants[v % len(not_a_brand_variants)]

    return [
        {"brand": "Chromium", "version": chrome_version},
        {"brand": "Google Chrome", "version": chrome_version},
        {"brand": variant, "version": "99"},
    ]


def _platform_to_sec_ch(platform_key: str) -> str:
    """Map our platform key to Sec-CH-UA-Platform value."""
    return {
        "windows": "Windows",
        "macos": "macOS",
        "linux": "Linux",
    }.get(platform_key, "Windows")


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
) -> Any:
    """Launch a Patchright Chromium browser with stealth configuration.

    Args:
        headless: Run in headless mode (--headless=new)
        platform: Force platform ("windows", "macos") or None for random
        locale: Locale config, or None for California default
        auto_detect_locale: Detect locale from IP
        extra_args: Additional Chromium launch arguments
        slow_mo: Slow down operations by this many ms (for debugging)

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

    # Patchright drives Chromium — reject non-Chrome UAs from the config pool.
    # Retry until we get a Chrome UA (config randomises from a mixed pool that
    # includes Firefox/Safari which are invalid for Chromium engine).
    for _ in range(20):
        if "Chrome/" in config.user_agent and "Edg/" not in config.user_agent:
            break
        config = BrowserConfig.get_config(
            platform=platform,
            locale=locale,
            auto_detect_locale=auto_detect_locale,
        )
    else:
        logger.warning(
            "Could not get Chrome UA after 20 tries, using fallback"
        )
        # Hard fallback — guaranteed Chrome UA
        config.user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )

    args = _build_launch_args(headless, config, extra_args)

    pw = await async_playwright().start()
    # Use "chromium" channel to force the headed Chromium binary with new
    # headless mode (--headless flag on headed binary).  The default without
    # a channel resolves to chromium-headless-shell, which bakes
    # "HeadlessChrome" into sec-ch-ua HTTP headers and worker contexts.
    browser = await pw.chromium.launch(
        headless=headless,
        channel="chromium",
        args=args,
        slow_mo=slow_mo,
    )

    # Attach metadata for downstream use
    browser._stealth_config = config
    browser._stealth_pw = pw

    logger.info(
        f"Stealth browser launched: platform={config.platform_key}, "
        f"ua={config.user_agent[:60]}..."
    )

    return browser


def _build_worker_overrides(config: PlatformConfig) -> str:
    """Build navigator + WebGL overrides JS for worker contexts.

    Workers have their own WorkerNavigator and WebGLRenderingContext prototypes.
    OffscreenCanvas in workers exposes the real GPU (SwiftShader in headless),
    which differs from our main-thread spoofed values — instant detection.
    """
    languages_js = str(config.locale.languages).replace("'", '"')

    # Extract vendor for WebGL (same logic as main thread)
    gpu_vendor_match = re.search(r"ANGLE \((\w+)", config.hardware.gpu)
    webgl_vendor = (
        f"Google Inc. ({gpu_vendor_match.group(1)})"
        if gpu_vendor_match
        else "Google Inc. (Google)"
    )

    # Build Client Hints brands for userAgentData override
    chrome_version = _extract_chrome_version(config.user_agent) or "122"
    brands = _build_client_hints_brands(chrome_version)
    brands_json = json.dumps(brands)
    full_version_list = [
        {"brand": b["brand"], "version": f"{b['version']}.0.0.0"}
        for b in brands
    ]
    full_version_json = json.dumps(full_version_list)
    sec_ch_platform = _platform_to_sec_ch(config.platform_key)
    platform_version = config.platform_version.replace("_", ".")

    # Compute appVersion (UA string with "Mozilla/" prefix removed)
    app_version = config.user_agent.split("Mozilla/", 1)[1] if "Mozilla/" in config.user_agent else config.user_agent

    return f"""
        // toString() protection for service worker context
        const __wkOrigToString = Function.prototype.toString;
        const __wkProtectedFns = new Map();
        Function.prototype.toString = function() {{
            if (__wkProtectedFns.has(this)) return __wkProtectedFns.get(this);
            return __wkOrigToString.call(this);
        }};
        __wkProtectedFns.set(Function.prototype.toString, 'function toString() {{ [native code] }}');

        const __frozenLangs = Object.freeze({languages_js});
        Object.defineProperty(Object.getPrototypeOf(navigator), 'hardwareConcurrency', {{
            get: () => {config.hardware.cores}, configurable: true, enumerable: true
        }});
        __wkProtectedFns.set(
            Object.getOwnPropertyDescriptor(Object.getPrototypeOf(navigator), 'hardwareConcurrency').get,
            'function get hardwareConcurrency() {{ [native code] }}'
        );
        Object.defineProperty(Object.getPrototypeOf(navigator), 'deviceMemory', {{
            get: () => {config.hardware.memory}, configurable: true, enumerable: true
        }});
        __wkProtectedFns.set(
            Object.getOwnPropertyDescriptor(Object.getPrototypeOf(navigator), 'deviceMemory').get,
            'function get deviceMemory() {{ [native code] }}'
        );
        Object.defineProperty(Object.getPrototypeOf(navigator), 'platform', {{
            get: () => '{config.platform}', configurable: true, enumerable: true
        }});
        __wkProtectedFns.set(
            Object.getOwnPropertyDescriptor(Object.getPrototypeOf(navigator), 'platform').get,
            'function get platform() {{ [native code] }}'
        );
        Object.defineProperty(Object.getPrototypeOf(navigator), 'language', {{
            get: () => '{config.locale.locale}', configurable: true, enumerable: true
        }});
        __wkProtectedFns.set(
            Object.getOwnPropertyDescriptor(Object.getPrototypeOf(navigator), 'language').get,
            'function get language() {{ [native code] }}'
        );
        Object.defineProperty(Object.getPrototypeOf(navigator), 'languages', {{
            get: () => __frozenLangs, configurable: true, enumerable: true
        }});
        __wkProtectedFns.set(
            Object.getOwnPropertyDescriptor(Object.getPrototypeOf(navigator), 'languages').get,
            'function get languages() {{ [native code] }}'
        );
        Object.defineProperty(Object.getPrototypeOf(navigator), 'webdriver', {{
            get: () => false, configurable: true, enumerable: true
        }});
        __wkProtectedFns.set(
            Object.getOwnPropertyDescriptor(Object.getPrototypeOf(navigator), 'webdriver').get,
            'function get webdriver() {{ [native code] }}'
        );
        Object.defineProperty(Object.getPrototypeOf(navigator), 'pdfViewerEnabled', {{
            get: () => true, configurable: true, enumerable: true
        }});
        __wkProtectedFns.set(
            Object.getOwnPropertyDescriptor(Object.getPrototypeOf(navigator), 'pdfViewerEnabled').get,
            'function get pdfViewerEnabled() {{ [native code] }}'
        );
        Object.defineProperty(Object.getPrototypeOf(navigator), 'userAgent', {{
            get: () => '{config.user_agent}', configurable: true, enumerable: true
        }});
        __wkProtectedFns.set(
            Object.getOwnPropertyDescriptor(Object.getPrototypeOf(navigator), 'userAgent').get,
            'function get userAgent() {{ [native code] }}'
        );
        Object.defineProperty(Object.getPrototypeOf(navigator), 'appVersion', {{
            get: () => '{app_version}',
            configurable: true, enumerable: true
        }});
        __wkProtectedFns.set(
            Object.getOwnPropertyDescriptor(Object.getPrototypeOf(navigator), 'appVersion').get,
            'function get appVersion() {{ [native code] }}'
        );
        if (typeof NavigatorUAData !== 'undefined' || navigator.userAgentData) {{
            const __brands = {brands_json};
            const __fullVersionList = {full_version_json};
            const __wkUaData = (typeof NavigatorUAData !== 'undefined')
                ? Object.create(NavigatorUAData.prototype)
                : {{}};
            Object.defineProperties(__wkUaData, {{
                brands: {{ get: () => __brands.map(b => ({{...b}})), enumerable: true, configurable: true }},
                mobile: {{ get: () => false, enumerable: true, configurable: true }},
                platform: {{ get: () => '{sec_ch_platform}', enumerable: true, configurable: true }},
                getHighEntropyValues: {{ value: async function(hints) {{
                    const r = {{ brands: __brands.map(b => ({{...b}})), mobile: false, platform: '{sec_ch_platform}' }};
                    if (hints.includes('platformVersion')) r.platformVersion = '{platform_version}';
                    if (hints.includes('architecture')) r.architecture = 'x86';
                    if (hints.includes('bitness')) r.bitness = '64';
                    if (hints.includes('model')) r.model = '';
                    if (hints.includes('fullVersionList')) r.fullVersionList = __fullVersionList.map(b => ({{...b}}));
                    if (hints.includes('uaFullVersion')) r.uaFullVersion = '{chrome_version}.0.0.0';
                    if (hints.includes('wow64')) r.wow64 = false;
                    return r;
                }}, enumerable: true, configurable: true }},
                toJSON: {{ value: function() {{ return {{ brands: __brands.map(b => ({{...b}})), mobile: false, platform: '{sec_ch_platform}' }}; }}, enumerable: true, configurable: true }}
            }});
            Object.defineProperty(Object.getPrototypeOf(navigator), 'userAgentData', {{
                get: () => __wkUaData,
                configurable: true,
                enumerable: true
            }});
            __wkProtectedFns.set(
                Object.getOwnPropertyDescriptor(Object.getPrototypeOf(navigator), 'userAgentData').get,
                'function get userAgentData() {{ [native code] }}'
            );
            __wkProtectedFns.set(__wkUaData.getHighEntropyValues,
                'function getHighEntropyValues() {{ [native code] }}');
            __wkProtectedFns.set(__wkUaData.toJSON,
                'function toJSON() {{ [native code] }}');
        }}

        // WebGL override for OffscreenCanvas in workers — must match main thread
        if (typeof WebGLRenderingContext !== 'undefined') {{
            const __origGetParam = WebGLRenderingContext.prototype.getParameter;
            WebGLRenderingContext.prototype.getParameter = function(param) {{
                try {{
                    const dbg = this.getExtension('WEBGL_debug_renderer_info');
                    if (dbg) {{
                        if (param === dbg.UNMASKED_RENDERER_WEBGL) return '{config.hardware.gpu}';
                        if (param === dbg.UNMASKED_VENDOR_WEBGL) return '{webgl_vendor}';
                    }}
                }} catch {{}}
                return __origGetParam.call(this, param);
            }};
            __wkProtectedFns.set(WebGLRenderingContext.prototype.getParameter, 'function getParameter() {{ [native code] }}');
        }}
        if (typeof WebGL2RenderingContext !== 'undefined') {{
            const __origGetParam2 = WebGL2RenderingContext.prototype.getParameter;
            WebGL2RenderingContext.prototype.getParameter = function(param) {{
                try {{
                    const dbg = this.getExtension('WEBGL_debug_renderer_info');
                    if (dbg) {{
                        if (param === dbg.UNMASKED_RENDERER_WEBGL) return '{config.hardware.gpu}';
                        if (param === dbg.UNMASKED_VENDOR_WEBGL) return '{webgl_vendor}';
                    }}
                }} catch {{}}
                return __origGetParam2.call(this, param);
            }};
            __wkProtectedFns.set(WebGL2RenderingContext.prototype.getParameter, 'function getParameter() {{ [native code] }}');
        }}

        // Timezone and DateTimeFormat — must match main thread
        const __swTargetTimezone = '{config.locale.timezone}';
        Date.prototype.getTimezoneOffset = function() {{
            return {config.locale.timezone_offset};
        }};
        __wkProtectedFns.set(Date.prototype.getTimezoneOffset, 'function getTimezoneOffset() {{ [native code] }}');

        const __swOrigDTF = Intl.DateTimeFormat;
        Intl.DateTimeFormat = function(locales, options) {{
            options = options || {{}};
            if (!options.timeZone) options.timeZone = __swTargetTimezone;
            return new __swOrigDTF(locales, options);
        }};
        Intl.DateTimeFormat.prototype = __swOrigDTF.prototype;
        Intl.DateTimeFormat.supportedLocalesOf = __swOrigDTF.supportedLocalesOf;
        Object.defineProperty(Intl.DateTimeFormat, 'name', {{ value: 'DateTimeFormat' }});
        Object.defineProperty(Intl.DateTimeFormat, 'length', {{ value: 0, configurable: true }});
        __wkProtectedFns.set(Intl.DateTimeFormat, 'function DateTimeFormat() {{ [native code] }}');

        const __swOrigResOpts = __swOrigDTF.prototype.resolvedOptions;
        Intl.DateTimeFormat.prototype.resolvedOptions = function() {{
            const opts = __swOrigResOpts.call(this);
            opts.timeZone = __swTargetTimezone;
            return opts;
        }};
        __wkProtectedFns.set(Intl.DateTimeFormat.prototype.resolvedOptions, 'function resolvedOptions() {{ [native code] }}');

        // performance.now — noise + monotonicity
        const __swOrigNow = Performance.prototype.now;
        let __swLastNow = 0;
        Performance.prototype.now = function() {{
            const real = __swOrigNow.call(this);
            const noisy = Math.round(real * 100) / 100;
            if (noisy > __swLastNow) __swLastNow = noisy;
            return __swLastNow;
        }};
        __wkProtectedFns.set(Performance.prototype.now, 'function now() {{ [native code] }}');
    """


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



def _build_patchright_stealth_scripts(
    config: PlatformConfig,
    chrome_version: Optional[str] = None,
    sec_ch_platform: Optional[str] = None,
) -> str:
    """Build stealth JS adapted for Patchright's execution model.

    Differences from the Selenium version:
    - Session-consistent canvas/audio noise (seeded PRNG, not Math.random per call)
    - toString() protection on overridden functions
    - Patchright handles timezone natively, but we keep JS override as fallback
    - Console.enable is removed by Patchright, so no console.log at end
    - add_init_script runs in all frames (upgrade over Selenium's main-only)

    We also add screen property spoofing for headless detection resistance:
    - screen.availHeight < screen.height (simulates taskbar)
    - screenX/screenY non-zero (simulates window position)
    - document.hasFocus() returns true
    """
    languages_js = str(config.locale.languages).replace("'", '"')

    # Build Client Hints brands for userAgentData override
    if chrome_version is None:
        chrome_version = _extract_chrome_version(config.user_agent) or "122"
    if sec_ch_platform is None:
        sec_ch_platform = _platform_to_sec_ch(config.platform_key)

    brands = _build_client_hints_brands(chrome_version)
    brands_json = json.dumps(brands)
    full_version_list = [
        {"brand": b["brand"], "version": f"{b['version']}.0.0.0"}
        for b in brands
    ]
    full_version_json = json.dumps(full_version_list)
    platform_version = config.platform_version.replace("_", ".")

    # Extract GPU vendor from config for WebGL vendor string.
    # Config GPU format: "ANGLE (Vendor, Specific Model ...)"
    # Chrome returns: "Google Inc. (Vendor)"
    gpu_vendor_match = re.search(r"ANGLE \((\w+)", config.hardware.gpu)
    webgl_vendor = f"Google Inc. ({gpu_vendor_match.group(1)})" if gpu_vendor_match else "Google Inc. (Google)"

    # Compute appVersion (UA string with "Mozilla/" prefix removed) for worker overrides
    app_version = config.user_agent.split("Mozilla/", 1)[1] if "Mozilla/" in config.user_agent else config.user_agent

    return f"""
        // ============================================
        // SESSION SEED (deterministic noise per session)
        // ============================================
        const __STEALTH_SEED = Math.floor(Math.random() * 2147483647);

        function __stealthHash(x, y, seed) {{
            let h = seed;
            h = Math.imul(h ^ x, 2654435761);
            h = Math.imul(h ^ y, 2246822519);
            h ^= h >>> 16;
            return h;
        }}

        // ============================================
        // toString() PROTECTION
        // ============================================
        const __origToString = Function.prototype.toString;
        const __protectedFns = new Map();

        function __protectFn(obj, prop, nativeSig) {{
            const fn = obj[prop];
            if (fn) __protectedFns.set(fn, nativeSig);
        }}

        Function.prototype.toString = function() {{
            if (__protectedFns.has(this)) {{
                return __protectedFns.get(this);
            }}
            return __origToString.call(this);
        }};
        __protectedFns.set(Function.prototype.toString, 'function toString() {{ [native code] }}');

        // ============================================
        // WEBDRIVER HANDLING
        // ============================================
        // --disable-blink-features=AutomationControlled removes the property
        // entirely, making typeof navigator.webdriver === 'undefined'. But in
        // real Chrome the property exists on Navigator.prototype as a getter
        // returning false. Rebrowser flags the missing property.
        // Fix: define it on the prototype to match native Chrome behavior.
        //
        // NOTE: The OLD fpscanner (used by bot.incolumitas.com) reports
        // "WEBDRIVER: FAIL" because its check is simply:
        //   fingerprint.webDriver = ('webdriver' in navigator)
        //   testResult = fingerprint.webDriver ? INCONSISTENT : CONSISTENT;
        // This fires for ALL modern Chrome browsers (v90+) where the property
        // exists on Navigator.prototype returning false. A real Chrome 145
        // also fails this check — confirmed false positive. The NEW fpscanner
        // (antoinevastel/fpscanner rewrite) correctly checks
        //   navigator.webdriver === true
        // which passes. Do NOT delete the property to appease the old fpscanner
        // — that would cause Rebrowser and modern detectors to flag us.
        Object.defineProperty(Navigator.prototype, 'webdriver', {{
            get: () => false,
            set: undefined,
            enumerable: true,
            configurable: true
        }});
        __protectedFns.set(
            Object.getOwnPropertyDescriptor(Navigator.prototype, 'webdriver').get,
            'function get webdriver() {{ [native code] }}'
        );

        // ============================================
        // AUTOMATION PROPERTY CLEANUP
        // ============================================
        const automationProps = [
            '__webdriver_script_fn', '__driver_evaluate',
            '__webdriver_evaluate', '__selenium_evaluate',
            '__fxdriver_evaluate', '__driver_unwrapped',
            '__webdriver_unwrapped', '__selenium_unwrapped',
            '__fxdriver_unwrapped', '_Selenium_IDE_Recorder',
            '_selenium', 'calledSelenium',
            '$chrome_asyncScriptInfo',
            '$cdc_asdjflasutopfhvcZLmcfl_', '$wdc_',
            'webdriver', '__nightmare',
            '__puppeteer_evaluation_script__',
            '__playwright', '__pwInitScripts',
        ];

        for (const prop of automationProps) {{
            if (prop in window) delete window[prop];
            if (prop in document) delete document[prop];
        }}

        // ============================================
        // PLATFORM FINGERPRINTING
        // ============================================
        // All navigator overrides go on Navigator.prototype (not navigator instance)
        // so Object.getOwnPropertyNames(navigator) returns [] like real Chrome.
        Object.defineProperty(Navigator.prototype, 'platform', {{
            get: () => '{config.platform}',
            configurable: true,
            enumerable: true
        }});
        __protectedFns.set(
            Object.getOwnPropertyDescriptor(Navigator.prototype, 'platform').get,
            'function get platform() {{ [native code] }}'
        );

        Object.defineProperty(Navigator.prototype, 'hardwareConcurrency', {{
            get: () => {config.hardware.cores},
            configurable: true,
            enumerable: true
        }});
        __protectedFns.set(
            Object.getOwnPropertyDescriptor(Navigator.prototype, 'hardwareConcurrency').get,
            'function get hardwareConcurrency() {{ [native code] }}'
        );

        Object.defineProperty(Navigator.prototype, 'deviceMemory', {{
            get: () => {config.hardware.memory},
            configurable: true,
            enumerable: true
        }});
        __protectedFns.set(
            Object.getOwnPropertyDescriptor(Navigator.prototype, 'deviceMemory').get,
            'function get deviceMemory() {{ [native code] }}'
        );

        Object.defineProperty(Navigator.prototype, 'pdfViewerEnabled', {{
            get: () => true,
            configurable: true,
            enumerable: true
        }});
        __protectedFns.set(
            Object.getOwnPropertyDescriptor(Navigator.prototype, 'pdfViewerEnabled').get,
            'function get pdfViewerEnabled() {{ [native code] }}'
        );

        // ============================================
        // NETWORK SIMULATION
        // ============================================
        // Cache the connection object so identity check passes:
        // navigator.connection === navigator.connection → true
        const __connectionObj = (typeof NetworkInformation !== 'undefined')
            ? Object.create(NetworkInformation.prototype)
            : {{}};
        Object.defineProperties(__connectionObj, {{
            downlink: {{ get: () => {config.network.downlink}, enumerable: true, configurable: true }},
            rtt: {{ get: () => {config.network.rtt}, enumerable: true, configurable: true }},
            effectiveType: {{ get: () => '4g', enumerable: true, configurable: true }},
            saveData: {{ get: () => false, enumerable: true, configurable: true }},
            type: {{ get: () => 'wifi', enumerable: true, configurable: true }},
            downlinkMax: {{ get: () => Infinity, enumerable: true, configurable: true }},
            onchange: {{ get: () => null, set: () => {{}}, enumerable: true, configurable: true }}
        }});
        Object.defineProperty(Navigator.prototype, 'connection', {{
            get: () => __connectionObj,
            configurable: true,
            enumerable: true
        }});
        __protectedFns.set(
            Object.getOwnPropertyDescriptor(Navigator.prototype, 'connection').get,
            'function get connection() {{ [native code] }}'
        );

        // ============================================
        // WEBGL FINGERPRINT MASKING
        // ============================================
        const __getParamProxy = new Proxy(WebGLRenderingContext.prototype.getParameter, {{
            apply: function(target, thisArg, args) {{
                try {{
                    const param = args[0];
                    const debugInfo = thisArg.getExtension('WEBGL_debug_renderer_info');
                    if (debugInfo) {{
                        if (param === debugInfo.UNMASKED_RENDERER_WEBGL) {{
                            return '{config.hardware.gpu}';
                        }}
                        if (param === debugInfo.UNMASKED_VENDOR_WEBGL) {{
                            return '{webgl_vendor}';
                        }}
                    }}
                    if (param === thisArg.ALIASED_LINE_WIDTH_RANGE) {{
                        return new Float32Array([1, 1]);
                    }}
                    return target.apply(thisArg, args);
                }} catch (e) {{
                    return target.apply(thisArg, args);
                }}
            }}
        }});
        WebGLRenderingContext.prototype.getParameter = __getParamProxy;
        __protectFn(WebGLRenderingContext.prototype, 'getParameter',
            'function getParameter() {{ [native code] }}');

        if (typeof WebGL2RenderingContext !== 'undefined') {{
            WebGL2RenderingContext.prototype.getParameter = __getParamProxy;
            __protectFn(WebGL2RenderingContext.prototype, 'getParameter',
                'function getParameter() {{ [native code] }}');
        }}

        // ============================================
        // PERFORMANCE TIMING RANDOMIZATION
        // ============================================
        const __origNow = Performance.prototype.now;
        let __lastPerfNow = 0;
        Performance.prototype.now = function() {{
            const real = __origNow.call(this);
            const noisy = real + (Math.random() * 0.1);
            if (noisy > __lastPerfNow) __lastPerfNow = noisy;
            return __lastPerfNow;
        }};
        __protectFn(Performance.prototype, 'now',
            'function now() {{ [native code] }}');

        // ============================================
        // CHROME-SPECIFIC PROPERTIES
        // ============================================
        window.chrome = {{
            runtime: {{
                onConnect: null,
                onMessage: null,
                connect: function() {{ return null; }},
                sendMessage: function() {{ return null; }},
                id: undefined
            }},
            loadTimes: function() {{ return {{}}; }},
            csi: function() {{ return {{}}; }},
            app: {{
                isInstalled: false,
                InstallState: {{ INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' }},
                RunningState: {{ RUNNING: 'running', CANNOT_RUN: 'cannot_run' }}
            }}
        }};

        // ============================================
        // USER AGENT DATA (replaces CDP Emulation.setUserAgentOverride)
        // ============================================
        // Pure JS override so we don't need any CDP calls, which are detectable.
        // This covers navigator.userAgentData.brands, .platform, .mobile,
        // and getHighEntropyValues() for detailed version info.
        (() => {{
            const __brands = {brands_json};
            const __fullVersionList = {full_version_json};
            const __secPlatform = '{sec_ch_platform}';
            const __platformVer = '{platform_version}';
            const __chromeVer = '{chrome_version}';

            const __uaData = (typeof NavigatorUAData !== 'undefined')
                ? Object.create(NavigatorUAData.prototype)
                : {{}};
            Object.defineProperties(__uaData, {{
                brands: {{ get: () => __brands.map(b => ({{...b}})), enumerable: true, configurable: true }},
                mobile: {{ get: () => false, enumerable: true, configurable: true }},
                platform: {{ get: () => __secPlatform, enumerable: true, configurable: true }},
                getHighEntropyValues: {{ value: async function(hints) {{
                    const result = {{
                        brands: __brands.map(b => ({{...b}})),
                        mobile: false,
                        platform: __secPlatform,
                    }};
                    if (hints.includes('platformVersion')) result.platformVersion = __platformVer;
                    if (hints.includes('architecture')) result.architecture = 'x86';
                    if (hints.includes('bitness')) result.bitness = '64';
                    if (hints.includes('model')) result.model = '';
                    if (hints.includes('fullVersionList')) result.fullVersionList = __fullVersionList.map(b => ({{...b}}));
                    if (hints.includes('uaFullVersion')) result.uaFullVersion = __chromeVer + '.0.0.0';
                    if (hints.includes('wow64')) result.wow64 = false;
                    return result;
                }}, enumerable: true, configurable: true }},
                toJSON: {{ value: function() {{
                    return {{ brands: __brands.map(b => ({{...b}})), mobile: false, platform: __secPlatform }};
                }}, enumerable: true, configurable: true }},
            }});

            Object.defineProperty(Navigator.prototype, 'userAgentData', {{
                get: () => __uaData,
                configurable: true,
                enumerable: true,
            }});
            __protectedFns.set(
                Object.getOwnPropertyDescriptor(Navigator.prototype, 'userAgentData').get,
                'function get userAgentData() {{ [native code] }}'
            );
            __protectedFns.set(__uaData.getHighEntropyValues,
                'function getHighEntropyValues() {{ [native code] }}');
            __protectedFns.set(__uaData.toJSON,
                'function toJSON() {{ [native code] }}');
        }})();

        // ============================================
        // PLUGINS AND MIME TYPES
        // ============================================
        (() => {{
            const __pluginData = [
                {{ name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' }},
                {{ name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' }},
                {{ name: 'Native Client', filename: 'internal-nacl-plugin', description: '' }}
            ];

            // Build a frozen plugin array-like object that survives iteration
            const __plugins = Object.create(PluginArray.prototype);
            __pluginData.forEach((p, i) => {{
                // Each plugin entry needs Plugin prototype for instanceof checks
                const plug = Object.create(Plugin.prototype);
                Object.defineProperties(plug, {{
                    name: {{ value: p.name, enumerable: true }},
                    filename: {{ value: p.filename, enumerable: true }},
                    description: {{ value: p.description, enumerable: true }},
                    length: {{ value: 0 }},
                }});
                Object.defineProperty(__plugins, i, {{ value: plug, enumerable: true }});
            }});

            Object.defineProperties(__plugins, {{
                length: {{ value: __pluginData.length }},
                item: {{ value: function(i) {{ return this[i] || null; }} }},
                namedItem: {{ value: function(name) {{
                    for (let i = 0; i < this.length; i++) {{
                        if (this[i] && this[i].name === name) return this[i];
                    }}
                    return null;
                }} }},
                refresh: {{ value: function() {{}} }},
                [Symbol.iterator]: {{ value: function*() {{
                    for (let i = 0; i < this.length; i++) yield this[i];
                }} }},
            }});

            Object.defineProperty(Navigator.prototype, 'plugins', {{
                get: () => __plugins,
                configurable: true,
                enumerable: true
            }});
            __protectedFns.set(
                Object.getOwnPropertyDescriptor(Navigator.prototype, 'plugins').get,
                'function get plugins() {{ [native code] }}'
            );

            // Also override mimeTypes to match plugins
            const __mimeTypes = Object.create(MimeTypeArray.prototype);
            const mimeData = [
                {{ type: 'application/pdf', suffixes: 'pdf', description: 'Portable Document Format' }},
            ];
            mimeData.forEach((m, i) => {{
                const mime = Object.create(MimeType.prototype);
                Object.defineProperties(mime, {{
                    type: {{ value: m.type, enumerable: true }},
                    suffixes: {{ value: m.suffixes, enumerable: true }},
                    description: {{ value: m.description, enumerable: true }},
                    enabledPlugin: {{ value: __plugins[0] }},
                }});
                Object.defineProperty(__mimeTypes, i, {{ value: mime, enumerable: true }});
            }});
            Object.defineProperties(__mimeTypes, {{
                length: {{ value: mimeData.length }},
                item: {{ value: function(i) {{ return this[i] || null; }} }},
                namedItem: {{ value: function(type) {{
                    for (let i = 0; i < this.length; i++) {{
                        if (this[i] && this[i].type === type) return this[i];
                    }}
                    return null;
                }} }},
                [Symbol.iterator]: {{ value: function*() {{
                    for (let i = 0; i < this.length; i++) yield this[i];
                }} }},
            }});

            Object.defineProperty(Navigator.prototype, 'mimeTypes', {{
                get: () => __mimeTypes,
                configurable: true,
                enumerable: true
            }});
            __protectedFns.set(
                Object.getOwnPropertyDescriptor(Navigator.prototype, 'mimeTypes').get,
                'function get mimeTypes() {{ [native code] }}'
            );
        }})();

        // ============================================
        // LANGUAGE AND LOCALE
        // ============================================
        Object.defineProperty(Navigator.prototype, 'language', {{
            get: () => '{config.locale.locale}',
            configurable: true,
            enumerable: true
        }});
        __protectedFns.set(
            Object.getOwnPropertyDescriptor(Navigator.prototype, 'language').get,
            'function get language() {{ [native code] }}'
        );

        const __frozenLangs = Object.freeze({languages_js});
        Object.defineProperty(Navigator.prototype, 'languages', {{
            get: () => __frozenLangs,
            configurable: true,
            enumerable: true
        }});
        __protectedFns.set(
            Object.getOwnPropertyDescriptor(Navigator.prototype, 'languages').get,
            'function get languages() {{ [native code] }}'
        );

        // ============================================
        // TIMEZONE SPOOFING (belt-and-suspenders with Patchright native)
        // ============================================
        const __targetTimezone = '{config.locale.timezone}';
        const __targetOffset = {config.locale.timezone_offset};

        Date.prototype.getTimezoneOffset = function() {{
            return __targetOffset;
        }};
        __protectFn(Date.prototype, 'getTimezoneOffset',
            'function getTimezoneOffset() {{ [native code] }}');

        const __OrigDTF = Intl.DateTimeFormat;
        Intl.DateTimeFormat = function(locales, options) {{
            options = options || {{}};
            if (!options.timeZone) {{
                options.timeZone = __targetTimezone;
            }}
            return new __OrigDTF(locales, options);
        }};
        Intl.DateTimeFormat.prototype = __OrigDTF.prototype;
        Intl.DateTimeFormat.supportedLocalesOf = __OrigDTF.supportedLocalesOf;
        Object.defineProperty(Intl.DateTimeFormat, 'name', {{ value: 'DateTimeFormat' }});
        Object.defineProperty(Intl.DateTimeFormat, 'length', {{ value: 0, configurable: true }});
        __protectedFns.set(Intl.DateTimeFormat, 'function DateTimeFormat() {{ [native code] }}');

        const __origResolvedOpts = __OrigDTF.prototype.resolvedOptions;
        Intl.DateTimeFormat.prototype.resolvedOptions = function() {{
            const options = __origResolvedOpts.call(this);
            options.timeZone = __targetTimezone;
            return options;
        }};
        __protectedFns.set(Intl.DateTimeFormat.prototype.resolvedOptions, 'function resolvedOptions() {{ [native code] }}');

        // ============================================
        // PERMISSIONS API (prevent headless contradiction)
        // ============================================
        const __origQuery = Permissions.prototype.query;
        Permissions.prototype.query = async function(parameters) {{
            if (parameters.name === 'notifications') {{
                return {{ state: 'denied', onchange: null }};
            }}
            return __origQuery.call(this, parameters);
        }};
        __protectFn(Permissions.prototype, 'query',
            'function query() {{ [native code] }}');

        // ============================================
        // HEADLESS DETECTION RESISTANCE
        // ============================================

        // screen.availHeight < screen.height (simulates taskbar/dock)
        const __realHeight = window.screen.height || {config.viewport_height};
        const __taskbarHeight = 40 + Math.floor(__stealthHash(1, 1, __STEALTH_SEED) % 30);
        Object.defineProperty(Screen.prototype, 'availHeight', {{
            get: () => __realHeight - __taskbarHeight,
            configurable: true,
            enumerable: true
        }});
        __protectedFns.set(
            Object.getOwnPropertyDescriptor(Screen.prototype, 'availHeight').get,
            'function get availHeight() {{ [native code] }}'
        );

        // screenX/screenY non-zero (simulates window position)
        const __screenX = 50 + (__stealthHash(2, 2, __STEALTH_SEED) & 0xFF);
        const __screenY = 20 + (__stealthHash(3, 3, __STEALTH_SEED) & 0x7F);
        Object.defineProperty(Object.getPrototypeOf(window), 'screenX', {{
            get: () => __screenX,
            configurable: true,
            enumerable: true
        }});
        __protectedFns.set(
            Object.getOwnPropertyDescriptor(Object.getPrototypeOf(window), 'screenX').get,
            'function get screenX() {{ [native code] }}'
        );
        Object.defineProperty(Object.getPrototypeOf(window), 'screenY', {{
            get: () => __screenY,
            configurable: true,
            enumerable: true
        }});
        __protectedFns.set(
            Object.getOwnPropertyDescriptor(Object.getPrototypeOf(window), 'screenY').get,
            'function get screenY() {{ [native code] }}'
        );

        // document.hasFocus() always true
        Document.prototype.hasFocus = function() {{ return true; }};
        __protectFn(Document.prototype, 'hasFocus',
            'function hasFocus() {{ [native code] }}');

        // outerHeight > innerHeight (window chrome)
        Object.defineProperty(Object.getPrototypeOf(window), 'outerHeight', {{
            get: () => window.innerHeight + 85 + (__stealthHash(4, 4, __STEALTH_SEED) & 0x1F),
            configurable: true,
            enumerable: true
        }});
        __protectedFns.set(
            Object.getOwnPropertyDescriptor(Object.getPrototypeOf(window), 'outerHeight').get,
            'function get outerHeight() {{ [native code] }}'
        );

        // ============================================
        // IFRAME PROPAGATION
        // ============================================
        // Dynamically created iframes (about:blank) don't trigger document
        // loads, so the route handler can't inject into them. Each iframe gets
        // a fresh JS realm with its own prototypes, so we must override them.
        function __patchIframe(iframe) {{
            try {{
                const win = iframe.contentWindow;
                if (!win) return;
                // Only patch same-origin iframes
                try {{ win.document; }} catch {{ return; }}

                // Navigator prototype overrides (match main window)
                const Nav = win.Navigator.prototype;
                Object.defineProperty(Nav, 'webdriver', {{
                    get: () => false, configurable: true, enumerable: true
                }});
                Object.defineProperty(Nav, 'platform', {{
                    get: () => '{config.platform}', configurable: true, enumerable: true
                }});
                Object.defineProperty(Nav, 'hardwareConcurrency', {{
                    get: () => {config.hardware.cores}, configurable: true, enumerable: true
                }});
                Object.defineProperty(Nav, 'deviceMemory', {{
                    get: () => {config.hardware.memory}, configurable: true, enumerable: true
                }});
                Object.defineProperty(Nav, 'language', {{
                    get: () => '{config.locale.locale}', configurable: true, enumerable: true
                }});
                Object.defineProperty(Nav, 'languages', {{
                    get: () => __frozenLangs, configurable: true, enumerable: true
                }});

                // userAgent, appVersion, pdfViewerEnabled — match main window
                Object.defineProperty(Nav, 'userAgent', {{
                    get: () => navigator.userAgent, configurable: true, enumerable: true
                }});
                Object.defineProperty(Nav, 'appVersion', {{
                    get: () => navigator.appVersion, configurable: true, enumerable: true
                }});
                Object.defineProperty(Nav, 'pdfViewerEnabled', {{
                    get: () => true, configurable: true, enumerable: true
                }});

                // userAgentData — share parent's override
                try {{
                    Object.defineProperty(Nav, 'userAgentData', {{
                        get: () => navigator.userAgentData, configurable: true, enumerable: true
                    }});
                }} catch {{}}

                // Share parent's plugins and mimeTypes objects
                Object.defineProperty(Nav, 'plugins', {{
                    get: () => navigator.plugins, configurable: true, enumerable: true
                }});
                Object.defineProperty(Nav, 'mimeTypes', {{
                    get: () => navigator.mimeTypes, configurable: true, enumerable: true
                }});

                // Chrome object
                win.chrome = window.chrome;

                // Permissions
                try {{
                    const origQuery = win.Permissions.prototype.query;
                    win.Permissions.prototype.query = async function(p) {{
                        if (p.name === 'notifications') return {{ state: 'denied', onchange: null }};
                        return origQuery.call(this, p);
                    }};
                }} catch {{}}

                // Screen properties
                try {{
                    Object.defineProperty(win.Screen.prototype, 'availHeight', {{
                        get: () => window.screen.availHeight, configurable: true, enumerable: true
                    }});
                }} catch {{}}

                // Document.hasFocus
                try {{
                    win.Document.prototype.hasFocus = function() {{ return true; }};
                }} catch {{}}

                // navigator.connection — share parent's connection object
                try {{
                    Object.defineProperty(Nav, 'connection', {{
                        get: () => navigator.connection, configurable: true, enumerable: true
                    }});
                }} catch {{}}

                // WebGL renderer/vendor — override iframe's WebGLRenderingContext
                try {{
                    const iframeWebGL = win.WebGLRenderingContext;
                    if (iframeWebGL) {{
                        const origGP = iframeWebGL.prototype.getParameter;
                        iframeWebGL.prototype.getParameter = function(param) {{
                            try {{
                                const dbg = this.getExtension('WEBGL_debug_renderer_info');
                                if (dbg) {{
                                    if (param === dbg.UNMASKED_RENDERER_WEBGL) return '{config.hardware.gpu}';
                                    if (param === dbg.UNMASKED_VENDOR_WEBGL) return '{webgl_vendor}';
                                }}
                            }} catch {{}}
                            return origGP.call(this, param);
                        }};
                    }}
                }} catch {{}}

                // WebGL2 renderer/vendor
                try {{
                    const iframeWebGL2 = win.WebGL2RenderingContext;
                    if (iframeWebGL2) {{
                        const origGP2 = iframeWebGL2.prototype.getParameter;
                        iframeWebGL2.prototype.getParameter = function(param) {{
                            try {{
                                const dbg = this.getExtension('WEBGL_debug_renderer_info');
                                if (dbg) {{
                                    if (param === dbg.UNMASKED_RENDERER_WEBGL) return '{config.hardware.gpu}';
                                    if (param === dbg.UNMASKED_VENDOR_WEBGL) return '{webgl_vendor}';
                                }}
                            }} catch {{}}
                            return origGP2.call(this, param);
                        }};
                    }}
                }} catch {{}}

                // Timezone
                try {{
                    win.Date.prototype.getTimezoneOffset = function() {{
                        return {config.locale.timezone_offset};
                    }};
                }} catch {{}}
                try {{
                    const iframeOrigDTF = win.Intl.DateTimeFormat;
                    win.Intl.DateTimeFormat = function(locales, options) {{
                        options = options || {{}};
                        if (!options.timeZone) options.timeZone = '{config.locale.timezone}';
                        return new iframeOrigDTF(locales, options);
                    }};
                    win.Intl.DateTimeFormat.prototype = iframeOrigDTF.prototype;
                    win.Intl.DateTimeFormat.supportedLocalesOf = iframeOrigDTF.supportedLocalesOf;
                }} catch {{}}

                // Performance.now noise — monotonic
                try {{
                    const iframeOrigNow = win.Performance.prototype.now;
                    let iframeLastNow = 0;
                    win.Performance.prototype.now = function() {{
                        const real = iframeOrigNow.call(this);
                        const noisy = real + (Math.random() * 0.1);
                        if (noisy > iframeLastNow) iframeLastNow = noisy;
                        return iframeLastNow;
                    }};
                }} catch {{}}

                // screenX/screenY/outerHeight — delegate to main window, on prototype
                try {{
                    const winProto = Object.getPrototypeOf(win);
                    Object.defineProperty(winProto, 'screenX', {{
                        get: () => window.screenX, configurable: true, enumerable: true
                    }});
                    Object.defineProperty(winProto, 'screenY', {{
                        get: () => window.screenY, configurable: true, enumerable: true
                    }});
                    Object.defineProperty(winProto, 'outerHeight', {{
                        get: () => window.outerHeight, configurable: true, enumerable: true
                    }});
                }} catch {{}}

                // Canvas noise — share parent's noise functions
                try {{
                    const iframeOrigToDataURL = win.HTMLCanvasElement.prototype.toDataURL;
                    win.HTMLCanvasElement.prototype.toDataURL = function(type) {{
                        const clone = __applyCanvasNoise(this);
                        if (clone) return iframeOrigToDataURL.apply(clone, arguments);
                        return iframeOrigToDataURL.apply(this, arguments);
                    }};
                    const iframeOrigToBlob = win.HTMLCanvasElement.prototype.toBlob;
                    win.HTMLCanvasElement.prototype.toBlob = function(callback, type, quality) {{
                        const clone = __applyCanvasNoise(this);
                        if (clone) return iframeOrigToBlob.apply(clone, arguments);
                        return iframeOrigToBlob.apply(this, arguments);
                    }};
                }} catch {{}}

                // AudioBuffer.getChannelData — share parent's noise pattern
                try {{
                    const iframeNoisedChannels = new WeakMap();
                    const iframeOrigGetChannelData = win.AudioBuffer.prototype.getChannelData;
                    win.AudioBuffer.prototype.getChannelData = function(channel) {{
                        const data = iframeOrigGetChannelData.call(this, channel);
                        if (!iframeNoisedChannels.has(this)) iframeNoisedChannels.set(this, new Set());
                        const noised = iframeNoisedChannels.get(this);
                        if (!noised.has(channel)) {{
                            noised.add(channel);
                            for (let i = 0; i < data.length; i++) {{
                                const h = __stealthHash(i, channel, __STEALTH_SEED);
                                if ((h & 0xFFF) < 5) {{
                                    data[i] += ((h >> 12) & 0xFF) / 2550000 - 0.00005;
                                }}
                            }}
                        }}
                        return data;
                    }};
                }} catch {{}}
            }} catch {{}}
        }}

        // Proxy contentWindow getter so patching happens synchronously
        // on first access — MutationObserver is async and fires too late.
        const __origContentWindow = Object.getOwnPropertyDescriptor(
            HTMLIFrameElement.prototype, 'contentWindow'
        ).get;

        // Use Symbol for patch marker — undetectable via string property enumeration
        const __patchedSym = Symbol();

        Object.defineProperty(HTMLIFrameElement.prototype, 'contentWindow', {{
            get: function() {{
                const win = __origContentWindow.call(this);
                if (win && !win[__patchedSym]) {{
                    try {{
                        __patchIframe(this);
                        win[__patchedSym] = true;
                    }} catch {{}}
                }}
                return win;
            }},
            configurable: true,
            enumerable: true
        }});
        __protectedFns.set(
            Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype, 'contentWindow').get,
            'function get contentWindow() {{ [native code] }}'
        );

        // Proxy contentDocument getter — detection scripts that access
        // iframe.contentDocument instead of contentWindow bypass the
        // contentWindow proxy above, leaving the iframe realm unpatched.
        const __origContentDocument = Object.getOwnPropertyDescriptor(
            HTMLIFrameElement.prototype, 'contentDocument'
        ).get;

        Object.defineProperty(HTMLIFrameElement.prototype, 'contentDocument', {{
            get: function() {{
                const doc = __origContentDocument.call(this);
                if (doc && doc.defaultView && !doc.defaultView[__patchedSym]) {{
                    try {{
                        __patchIframe(this);
                        doc.defaultView[__patchedSym] = true;
                    }} catch {{}}
                }}
                return doc;
            }},
            configurable: true,
            enumerable: true
        }});
        __protectedFns.set(
            Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype, 'contentDocument').get,
            'function get contentDocument() {{ [native code] }}'
        );

        // Also patch any existing iframes
        document.querySelectorAll('iframe').forEach((iframe) => {{
            try {{ __patchIframe(iframe); }} catch {{}}
        }});

        // ============================================
        // WORKER NAVIGATOR CONSISTENCY
        // ============================================
        // Web workers have their own WorkerNavigator prototypes that don't
        // inherit our main-thread overrides. Proxy Worker/SharedWorker
        // constructors to wrap loaded scripts with navigator overrides.
        const __workerOverrides = `
            // toString() protection for worker context
            const __wkOrigToString = Function.prototype.toString;
            const __wkProtectedFns = new Map();
            Function.prototype.toString = function() {{
                if (__wkProtectedFns.has(this)) return __wkProtectedFns.get(this);
                return __wkOrigToString.call(this);
            }};
            __wkProtectedFns.set(Function.prototype.toString, 'function toString() {{ [native code] }}');

            const __frozenLangs = Object.freeze({languages_js});
            Object.defineProperty(Object.getPrototypeOf(navigator), 'hardwareConcurrency', {{
                get: () => {config.hardware.cores}, configurable: true, enumerable: true
            }});
            __wkProtectedFns.set(
                Object.getOwnPropertyDescriptor(Object.getPrototypeOf(navigator), 'hardwareConcurrency').get,
                'function get hardwareConcurrency() {{ [native code] }}'
            );
            Object.defineProperty(Object.getPrototypeOf(navigator), 'deviceMemory', {{
                get: () => {config.hardware.memory}, configurable: true, enumerable: true
            }});
            __wkProtectedFns.set(
                Object.getOwnPropertyDescriptor(Object.getPrototypeOf(navigator), 'deviceMemory').get,
                'function get deviceMemory() {{ [native code] }}'
            );
            Object.defineProperty(Object.getPrototypeOf(navigator), 'platform', {{
                get: () => '{config.platform}', configurable: true, enumerable: true
            }});
            __wkProtectedFns.set(
                Object.getOwnPropertyDescriptor(Object.getPrototypeOf(navigator), 'platform').get,
                'function get platform() {{ [native code] }}'
            );
            Object.defineProperty(Object.getPrototypeOf(navigator), 'language', {{
                get: () => '{config.locale.locale}', configurable: true, enumerable: true
            }});
            __wkProtectedFns.set(
                Object.getOwnPropertyDescriptor(Object.getPrototypeOf(navigator), 'language').get,
                'function get language() {{ [native code] }}'
            );
            Object.defineProperty(Object.getPrototypeOf(navigator), 'languages', {{
                get: () => __frozenLangs, configurable: true, enumerable: true
            }});
            __wkProtectedFns.set(
                Object.getOwnPropertyDescriptor(Object.getPrototypeOf(navigator), 'languages').get,
                'function get languages() {{ [native code] }}'
            );
            Object.defineProperty(Object.getPrototypeOf(navigator), 'webdriver', {{
                get: () => false, configurable: true, enumerable: true
            }});
            __wkProtectedFns.set(
                Object.getOwnPropertyDescriptor(Object.getPrototypeOf(navigator), 'webdriver').get,
                'function get webdriver() {{ [native code] }}'
            );
            Object.defineProperty(Object.getPrototypeOf(navigator), 'pdfViewerEnabled', {{
                get: () => true, configurable: true, enumerable: true
            }});
            __wkProtectedFns.set(
                Object.getOwnPropertyDescriptor(Object.getPrototypeOf(navigator), 'pdfViewerEnabled').get,
                'function get pdfViewerEnabled() {{ [native code] }}'
            );
            Object.defineProperty(Object.getPrototypeOf(navigator), 'userAgent', {{
                get: () => '{config.user_agent}', configurable: true, enumerable: true
            }});
            __wkProtectedFns.set(
                Object.getOwnPropertyDescriptor(Object.getPrototypeOf(navigator), 'userAgent').get,
                'function get userAgent() {{ [native code] }}'
            );
            Object.defineProperty(Object.getPrototypeOf(navigator), 'appVersion', {{
                get: () => '{app_version}',
                configurable: true, enumerable: true
            }});
            __wkProtectedFns.set(
                Object.getOwnPropertyDescriptor(Object.getPrototypeOf(navigator), 'appVersion').get,
                'function get appVersion() {{ [native code] }}'
            );
            if (typeof NavigatorUAData !== 'undefined' || navigator.userAgentData) {{
                const __brands = {brands_json};
                const __fullVersionList = {full_version_json};
                const __wkUaData = (typeof NavigatorUAData !== 'undefined')
                    ? Object.create(NavigatorUAData.prototype)
                    : {{}};
                Object.defineProperties(__wkUaData, {{
                    brands: {{ get: () => __brands.map(b => ({{...b}})), enumerable: true, configurable: true }},
                    mobile: {{ get: () => false, enumerable: true, configurable: true }},
                    platform: {{ get: () => '{sec_ch_platform}', enumerable: true, configurable: true }},
                    getHighEntropyValues: {{ value: async function(hints) {{
                        const r = {{ brands: __brands.map(b => ({{...b}})), mobile: false, platform: '{sec_ch_platform}' }};
                        if (hints.includes('platformVersion')) r.platformVersion = '{platform_version}';
                        if (hints.includes('architecture')) r.architecture = 'x86';
                        if (hints.includes('bitness')) r.bitness = '64';
                        if (hints.includes('model')) r.model = '';
                        if (hints.includes('fullVersionList')) r.fullVersionList = __fullVersionList.map(b => ({{...b}}));
                        if (hints.includes('uaFullVersion')) r.uaFullVersion = '{chrome_version}.0.0.0';
                        if (hints.includes('wow64')) r.wow64 = false;
                        return r;
                    }}, enumerable: true, configurable: true }},
                    toJSON: {{ value: function() {{ return {{ brands: __brands.map(b => ({{...b}})), mobile: false, platform: '{sec_ch_platform}' }}; }}, enumerable: true, configurable: true }}
                }});
                Object.defineProperty(Object.getPrototypeOf(navigator), 'userAgentData', {{
                    get: () => __wkUaData,
                    configurable: true,
                    enumerable: true
                }});
                __wkProtectedFns.set(
                    Object.getOwnPropertyDescriptor(Object.getPrototypeOf(navigator), 'userAgentData').get,
                    'function get userAgentData() {{ [native code] }}'
                );
                __wkProtectedFns.set(__wkUaData.getHighEntropyValues,
                    'function getHighEntropyValues() {{ [native code] }}');
                __wkProtectedFns.set(__wkUaData.toJSON,
                    'function toJSON() {{ [native code] }}');
            }}

            // WebGL override for OffscreenCanvas — must match main thread
            if (typeof WebGLRenderingContext !== 'undefined') {{
                const __origGP = WebGLRenderingContext.prototype.getParameter;
                WebGLRenderingContext.prototype.getParameter = function(p) {{
                    try {{
                        const d = this.getExtension('WEBGL_debug_renderer_info');
                        if (d) {{
                            if (p === d.UNMASKED_RENDERER_WEBGL) return '{config.hardware.gpu}';
                            if (p === d.UNMASKED_VENDOR_WEBGL) return '{webgl_vendor}';
                        }}
                    }} catch {{}}
                    return __origGP.call(this, p);
                }};
                __wkProtectedFns.set(WebGLRenderingContext.prototype.getParameter, 'function getParameter() {{ [native code] }}');
            }}
            if (typeof WebGL2RenderingContext !== 'undefined') {{
                const __origGP2 = WebGL2RenderingContext.prototype.getParameter;
                WebGL2RenderingContext.prototype.getParameter = function(p) {{
                    try {{
                        const d = this.getExtension('WEBGL_debug_renderer_info');
                        if (d) {{
                            if (p === d.UNMASKED_RENDERER_WEBGL) return '{config.hardware.gpu}';
                            if (p === d.UNMASKED_VENDOR_WEBGL) return '{webgl_vendor}';
                        }}
                    }} catch {{}}
                    return __origGP2.call(this, p);
                }};
                __wkProtectedFns.set(WebGL2RenderingContext.prototype.getParameter, 'function getParameter() {{ [native code] }}');
            }}

            // Timezone and DateTimeFormat — must match main thread
            const __wkTargetTimezone = '{config.locale.timezone}';
            Date.prototype.getTimezoneOffset = function() {{
                return {config.locale.timezone_offset};
            }};
            __wkProtectedFns.set(Date.prototype.getTimezoneOffset, 'function getTimezoneOffset() {{ [native code] }}');

            const __wkOrigDTF = Intl.DateTimeFormat;
            Intl.DateTimeFormat = function(locales, options) {{
                options = options || {{}};
                if (!options.timeZone) options.timeZone = __wkTargetTimezone;
                return new __wkOrigDTF(locales, options);
            }};
            Intl.DateTimeFormat.prototype = __wkOrigDTF.prototype;
            Intl.DateTimeFormat.supportedLocalesOf = __wkOrigDTF.supportedLocalesOf;
            Object.defineProperty(Intl.DateTimeFormat, 'name', {{ value: 'DateTimeFormat' }});
            Object.defineProperty(Intl.DateTimeFormat, 'length', {{ value: 0, configurable: true }});
            __wkProtectedFns.set(Intl.DateTimeFormat, 'function DateTimeFormat() {{ [native code] }}');

            const __wkOrigResOpts = __wkOrigDTF.prototype.resolvedOptions;
            Intl.DateTimeFormat.prototype.resolvedOptions = function() {{
                const opts = __wkOrigResOpts.call(this);
                opts.timeZone = __wkTargetTimezone;
                return opts;
            }};
            __wkProtectedFns.set(Intl.DateTimeFormat.prototype.resolvedOptions, 'function resolvedOptions() {{ [native code] }}');

            // performance.now — noise + monotonicity, must match main thread pattern
            const __wkOrigNow = Performance.prototype.now;
            let __wkLastNow = 0;
            Performance.prototype.now = function() {{
                const real = __wkOrigNow.call(this);
                const noisy = Math.round(real * 100) / 100;
                if (noisy > __wkLastNow) __wkLastNow = noisy;
                return __wkLastNow;
            }};
            __wkProtectedFns.set(Performance.prototype.now, 'function now() {{ [native code] }}');
        `;

        if (typeof window !== 'undefined' && window.Worker) {{
            const __OrigWorker = window.Worker;
            window.Worker = function(scriptURL, options) {{
                const type = (options && options.type) || 'classic';
                let resolvedURL;
                try {{
                    if (scriptURL instanceof URL) resolvedURL = scriptURL.href;
                    else if (typeof scriptURL === 'string') {{
                        // blob: URLs — fetch the blob content and re-wrap with overrides
                        if (scriptURL.startsWith('blob:')) {{
                            // Create a wrapper blob that prepends overrides then imports the original
                            // For classic workers, use importScripts (blob: URLs work with importScripts)
                            // For module workers, use dynamic import()
                            const loader = type === 'module'
                                ? 'import("' + scriptURL + '");'
                                : 'importScripts("' + scriptURL + '");';
                            const wrapBlob = new Blob(
                                [__workerOverrides + '\\n' + loader],
                                {{ type: 'application/javascript' }}
                            );
                            const wrapURL = URL.createObjectURL(wrapBlob);
                            const w = new __OrigWorker(wrapURL, options);
                            setTimeout(() => URL.revokeObjectURL(wrapURL), 5000);
                            return w;
                        }}
                        // data: URLs can't be re-wrapped (no importScripts for data: URLs)
                        if (scriptURL.startsWith('data:')) {{
                            return new __OrigWorker(scriptURL, options);
                        }}
                        resolvedURL = new URL(scriptURL, location.href).href;
                    }} else {{
                        return new __OrigWorker(scriptURL, options);
                    }}
                }} catch {{
                    return new __OrigWorker(scriptURL, options);
                }}

                const loader = type === 'module'
                    ? 'import("' + resolvedURL + '");'
                    : 'importScripts("' + resolvedURL + '");';
                const blob = new Blob(
                    [__workerOverrides + '\\n' + loader],
                    {{ type: 'application/javascript' }}
                );
                const blobURL = URL.createObjectURL(blob);
                const w = new __OrigWorker(blobURL, options);
                setTimeout(() => URL.revokeObjectURL(blobURL), 5000);
                return w;
            }};
            window.Worker.prototype = __OrigWorker.prototype;
            Object.defineProperty(window.Worker.prototype, 'constructor', {{value: window.Worker, configurable: true, writable: true}});
            __protectedFns.set(window.Worker, __origToString.call(__OrigWorker));
            Object.defineProperty(window.Worker, 'name', {{value: 'Worker', configurable: true}});
            Object.defineProperty(window.Worker, 'length', {{value: 1, configurable: true}});
        }}

        if (typeof window !== 'undefined' && window.SharedWorker) {{
            const __OrigSharedWorker = window.SharedWorker;
            window.SharedWorker = function(scriptURL, nameOrOptions) {{
                let resolvedURL;
                try {{
                    if (scriptURL instanceof URL) resolvedURL = scriptURL.href;
                    else if (typeof scriptURL === 'string') {{
                        // blob: URLs — wrap with overrides via importScripts
                        if (scriptURL.startsWith('blob:')) {{
                            const wrapBlob = new Blob(
                                [__workerOverrides + '\\nimportScripts("' + scriptURL + '");'],
                                {{ type: 'application/javascript' }}
                            );
                            const wrapURL = URL.createObjectURL(wrapBlob);
                            const sw = new __OrigSharedWorker(wrapURL, nameOrOptions);
                            setTimeout(() => URL.revokeObjectURL(wrapURL), 5000);
                            return sw;
                        }}
                        // data: URLs can't be re-wrapped
                        if (scriptURL.startsWith('data:')) {{
                            return new __OrigSharedWorker(scriptURL, nameOrOptions);
                        }}
                        resolvedURL = new URL(scriptURL, location.href).href;
                    }} else {{
                        return new __OrigSharedWorker(scriptURL, nameOrOptions);
                    }}
                }} catch {{
                    return new __OrigSharedWorker(scriptURL, nameOrOptions);
                }}

                const blob = new Blob(
                    [__workerOverrides + '\\nimportScripts("' + resolvedURL + '");'],
                    {{ type: 'application/javascript' }}
                );
                const blobURL = URL.createObjectURL(blob);
                const sw = new __OrigSharedWorker(blobURL, nameOrOptions);
                setTimeout(() => URL.revokeObjectURL(blobURL), 5000);
                return sw;
            }};
            window.SharedWorker.prototype = __OrigSharedWorker.prototype;
            Object.defineProperty(window.SharedWorker.prototype, 'constructor', {{value: window.SharedWorker, configurable: true, writable: true}});
            __protectedFns.set(window.SharedWorker, __origToString.call(__OrigSharedWorker));
            Object.defineProperty(window.SharedWorker, 'name', {{value: 'SharedWorker', configurable: true}});
            Object.defineProperty(window.SharedWorker, 'length', {{value: 1, configurable: true}});
        }}

        // ============================================
        // SERVICE WORKER NAVIGATOR OVERRIDE
        // ============================================
        // Service workers run in isolated contexts that bypass both our
        // page-level JS overrides and Patchright's route handler (context.route()
        // only intercepts page-initiated requests, not SW fetches by the browser).
        //
        // Strategy: Intercept navigator.serviceWorker.register() to:
        // 1. Unregister any cached service workers first (forces fresh registration)
        // 2. Wait for the SW to activate
        // 3. Use postMessage to the SW can't help — SWs have their own globals
        //
        // Since we can't inject JS into SW contexts from the page, we accept this
        // as a limitation and instead minimize the leak surface: unregister any
        // pre-existing SWs so detection sites can't compare stale cached values
        // with our current spoofed values.
        if (typeof window !== 'undefined' && navigator.serviceWorker) {{
            // Unregister all pre-existing service workers on page load
            navigator.serviceWorker.getRegistrations().then(registrations => {{
                for (const reg of registrations) {{
                    reg.unregister();
                }}
            }}).catch(() => {{}});
        }}

        // ============================================
        // HUMAN-LIKE BEHAVIOR INJECTION
        // ============================================
        let __lastInteraction = Date.now();
        const __updateInteraction = () => {{ __lastInteraction = Date.now(); }};

        ['mousemove', 'mousedown', 'mouseup', 'scroll', 'keydown', 'touchstart', 'click'].forEach(event => {{
            document.addEventListener(event, __updateInteraction, {{ passive: true }});
        }});

        let __lastMoveTime = Date.now();
        const __moveInterval = 1000 + Math.random() * 3000;

        setInterval(() => {{
            const now = Date.now();
            if (now - __lastInteraction > 3000 &&
                now - __lastMoveTime > __moveInterval &&
                Math.random() < 0.1) {{

                const amplitude = Math.random() * 3 + 1;
                const evt = new MouseEvent('mousemove', {{
                    bubbles: true,
                    cancelable: true,
                    clientX: Math.random() * window.innerWidth,
                    clientY: Math.random() * window.innerHeight,
                    movementX: (Math.random() - 0.5) * amplitude,
                    movementY: (Math.random() - 0.5) * amplitude
                }});
                document.dispatchEvent(evt);
                __lastMoveTime = now;
            }}
        }}, 500);

        // ============================================
        // CANVAS FINGERPRINT PROTECTION (session-consistent, idempotent)
        // ============================================
        // Apply deterministic noise to an offscreen clone — the original
        // canvas is never modified, so repeated calls are idempotent:
        // canvas.toDataURL() === canvas.toDataURL() → true
        function __applyCanvasNoise(srcCanvas) {{
            const w = srcCanvas.width, h = srcCanvas.height;
            if (w === 0 || h === 0) return null;
            const srcCtx = srcCanvas.getContext('2d');
            if (!srcCtx) return null;

            const clone = document.createElement('canvas');
            clone.width = w;
            clone.height = h;
            const cloneCtx = clone.getContext('2d');
            const imageData = srcCtx.getImageData(0, 0, w, h);
            for (let i = 0; i < imageData.data.length; i += 4) {{
                const x = (i / 4) % w;
                const y = Math.floor((i / 4) / w);
                imageData.data[i] ^= (__stealthHash(x, y, __STEALTH_SEED) & 1);
            }}
            cloneCtx.putImageData(imageData, 0, 0);
            return clone;
        }}

        const __origToDataURL = HTMLCanvasElement.prototype.toDataURL;
        HTMLCanvasElement.prototype.toDataURL = function(type) {{
            const clone = __applyCanvasNoise(this);
            if (clone) return __origToDataURL.apply(clone, arguments);
            return __origToDataURL.apply(this, arguments);
        }};
        __protectFn(HTMLCanvasElement.prototype, 'toDataURL',
            'function toDataURL() {{ [native code] }}');

        // Also protect toBlob (CreepJS checks this)
        const __origToBlob = HTMLCanvasElement.prototype.toBlob;
        HTMLCanvasElement.prototype.toBlob = function(callback, type, quality) {{
            const clone = __applyCanvasNoise(this);
            if (clone) return __origToBlob.apply(clone, arguments);
            return __origToBlob.apply(this, arguments);
        }};
        __protectFn(HTMLCanvasElement.prototype, 'toBlob',
            'function toBlob() {{ [native code] }}');

        // ============================================
        // AUDIO FINGERPRINT PROTECTION (session-consistent, idempotent)
        // ============================================
        // Track which AudioBuffer+channel pairs have been noised so
        // repeated getChannelData() calls don't accumulate drift.
        const __noisedChannels = new WeakMap();
        const __origGetChannelData = AudioBuffer.prototype.getChannelData;
        AudioBuffer.prototype.getChannelData = function(channel) {{
            const data = __origGetChannelData.call(this, channel);
            if (!__noisedChannels.has(this)) __noisedChannels.set(this, new Set());
            const noised = __noisedChannels.get(this);
            if (!noised.has(channel)) {{
                noised.add(channel);
                for (let i = 0; i < data.length; i++) {{
                    // Deterministic noise: same seed + sample index = same offset
                    const h = __stealthHash(i, channel, __STEALTH_SEED);
                    if ((h & 0xFFF) < 5) {{  // ~0.1% of samples
                        data[i] += ((h >> 12) & 0xFF) / 2550000 - 0.00005;
                    }}
                }}
            }}
            return data;
        }};
        __protectFn(AudioBuffer.prototype, 'getChannelData',
            'function getChannelData() {{ [native code] }}');
    """


async def inject_stealth(page: Any, context: Any = None) -> None:
    """Re-inject stealth scripts into a page after SPA navigation.

    For normal navigations (page.goto, page.reload), the context's route
    handler injects stealth JS automatically into the HTML response.
    Use this function only for SPA navigations (pushState/replaceState)
    where the route handler doesn't fire.

    Args:
        page: Patchright Page object (already navigated)
        context: BrowserContext (auto-detected from page if not provided)
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
