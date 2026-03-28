"""Shared stealth JS generation — used by both Patchright and Native engines.

Extracted from patchright.py so the native engine can reuse the stealth JS
without requiring the patchright package to be installed. All functions here
depend only on stdlib (json, re) and config.py types.
"""

from __future__ import annotations

import json
import re
from typing import Dict, List, Optional

from stealth_browser.config import PlatformConfig


def extract_chrome_version(user_agent: str) -> Optional[str]:
    """Extract Chrome major version from User-Agent string."""
    match = re.search(r"Chrome/(\d+)", user_agent)
    return match.group(1) if match else None


def build_chrome_ua(major_version: str, platform_key: str) -> str:
    """Build a Chrome User-Agent string matching a specific major version.

    Used to synchronize the UA with the actual browser binary's version,
    so TLS fingerprint (JA3/JA4) and UA are coherent.
    """
    ver = f"{major_version}.0.0.0"
    if platform_key == "macos":
        return (
            f"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/{ver} Safari/537.36"
        )
    elif platform_key == "linux":
        return (
            f"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/{ver} Safari/537.36"
        )
    # Default: Windows
    return (
        f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        f"(KHTML, like Gecko) Chrome/{ver} Safari/537.36"
    )


def build_client_hints_brands(chrome_version: str) -> List[Dict[str, str]]:
    """Build Sec-CH-UA brand list matching Chrome's format.

    Chrome uses a rotating "Not A Brand" pattern that changes per major version.
    The brand list order and "Not A Brand" variant must match the claimed version.
    """
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


def platform_to_sec_ch(platform_key: str) -> str:
    """Map our platform key to Sec-CH-UA-Platform value."""
    return {
        "windows": "Windows",
        "macos": "macOS",
        "linux": "Linux",
    }.get(platform_key, "Windows")


def build_worker_overrides(config: PlatformConfig) -> str:
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
    chrome_version = extract_chrome_version(config.user_agent) or "122"
    brands = build_client_hints_brands(chrome_version)
    brands_json = json.dumps(brands)
    full_version_list = [
        {"brand": b["brand"], "version": f"{b['version']}.0.0.0"}
        for b in brands
    ]
    full_version_json = json.dumps(full_version_list)
    sec_ch_platform = platform_to_sec_ch(config.platform_key)
    platform_version = config.platform_version.replace("_", ".")

    # Compute appVersion (UA string with "Mozilla/" prefix removed)
    app_version = (
        config.user_agent.split("Mozilla/", 1)[1]
        if "Mozilla/" in config.user_agent
        else config.user_agent
    )

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


def _build_screen_overrides_js(config: PlatformConfig) -> str:
    """Build the screen property override JS (for headless detection resistance).

    Skipped by the native engine which needs real screen coordinates for OS input.
    """
    return f"""
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
    """


def _build_iframe_screen_overrides_js() -> str:
    """Build iframe screen property overrides (skipped when skip_screen_overrides=True)."""
    return """
                // Screen properties
                try {
                    Object.defineProperty(win.Screen.prototype, 'availHeight', {
                        get: () => window.screen.availHeight, configurable: true, enumerable: true
                    });
                } catch {}

                // screenX/screenY/outerHeight — delegate to main window, on prototype
                try {
                    const winProto = Object.getPrototypeOf(win);
                    Object.defineProperty(winProto, 'screenX', {
                        get: () => window.screenX, configurable: true, enumerable: true
                    });
                    Object.defineProperty(winProto, 'screenY', {
                        get: () => window.screenY, configurable: true, enumerable: true
                    });
                    Object.defineProperty(winProto, 'outerHeight', {
                        get: () => window.outerHeight, configurable: true, enumerable: true
                    });
                } catch {}
    """


def build_stealth_scripts(
    config: PlatformConfig,
    chrome_version: Optional[str] = None,
    sec_ch_platform: Optional[str] = None,
    skip_screen_overrides: bool = False,
) -> str:
    """Build comprehensive stealth JS for injection into page context.

    Args:
        config: Platform fingerprint configuration
        chrome_version: Chrome major version (auto-detected from UA if None)
        sec_ch_platform: Sec-CH-UA-Platform value (auto-detected if None)
        skip_screen_overrides: If True, skip screenX/screenY/outerHeight/availHeight
            overrides. Used by the native engine which needs real screen coordinates
            for OS-level input targeting.
    """
    languages_js = str(config.locale.languages).replace("'", '"')

    # Build Client Hints brands for userAgentData override
    if chrome_version is None:
        chrome_version = extract_chrome_version(config.user_agent) or "122"
    if sec_ch_platform is None:
        sec_ch_platform = platform_to_sec_ch(config.platform_key)

    brands = build_client_hints_brands(chrome_version)
    brands_json = json.dumps(brands)
    full_version_list = [
        {"brand": b["brand"], "version": f"{b['version']}.0.0.0"}
        for b in brands
    ]
    full_version_json = json.dumps(full_version_list)
    platform_version = config.platform_version.replace("_", ".")

    # Extract GPU vendor from config for WebGL vendor string.
    gpu_vendor_match = re.search(r"ANGLE \((\w+)", config.hardware.gpu)
    webgl_vendor = (
        f"Google Inc. ({gpu_vendor_match.group(1)})"
        if gpu_vendor_match
        else "Google Inc. (Google)"
    )

    # Compute appVersion (UA string with "Mozilla/" prefix removed)
    app_version = (
        config.user_agent.split("Mozilla/", 1)[1]
        if "Mozilla/" in config.user_agent
        else config.user_agent
    )

    # Conditionally build screen overrides
    screen_overrides_js = "" if skip_screen_overrides else _build_screen_overrides_js(config)
    iframe_screen_js = "" if skip_screen_overrides else _build_iframe_screen_overrides_js()

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
        // USER AGENT DATA
        // ============================================
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

            const __plugins = Object.create(PluginArray.prototype);
            __pluginData.forEach((p, i) => {{
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
        // TIMEZONE SPOOFING
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
        // PERMISSIONS API
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
        {screen_overrides_js}

        // document.hasFocus() always true
        Document.prototype.hasFocus = function() {{ return true; }};
        __protectFn(Document.prototype, 'hasFocus',
            'function hasFocus() {{ [native code] }}');

        // ============================================
        // IFRAME PROPAGATION
        // ============================================
        function __patchIframe(iframe) {{
            try {{
                const win = iframe.contentWindow;
                if (!win) return;
                try {{ win.document; }} catch {{ return; }}

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

                Object.defineProperty(Nav, 'userAgent', {{
                    get: () => navigator.userAgent, configurable: true, enumerable: true
                }});
                Object.defineProperty(Nav, 'appVersion', {{
                    get: () => navigator.appVersion, configurable: true, enumerable: true
                }});
                Object.defineProperty(Nav, 'pdfViewerEnabled', {{
                    get: () => true, configurable: true, enumerable: true
                }});

                try {{
                    Object.defineProperty(Nav, 'userAgentData', {{
                        get: () => navigator.userAgentData, configurable: true, enumerable: true
                    }});
                }} catch {{}}

                Object.defineProperty(Nav, 'plugins', {{
                    get: () => navigator.plugins, configurable: true, enumerable: true
                }});
                Object.defineProperty(Nav, 'mimeTypes', {{
                    get: () => navigator.mimeTypes, configurable: true, enumerable: true
                }});

                win.chrome = window.chrome;

                try {{
                    const origQuery = win.Permissions.prototype.query;
                    win.Permissions.prototype.query = async function(p) {{
                        if (p.name === 'notifications') return {{ state: 'denied', onchange: null }};
                        return origQuery.call(this, p);
                    }};
                }} catch {{}}

                {iframe_screen_js}

                try {{
                    win.Document.prototype.hasFocus = function() {{ return true; }};
                }} catch {{}}

                try {{
                    Object.defineProperty(Nav, 'connection', {{
                        get: () => navigator.connection, configurable: true, enumerable: true
                    }});
                }} catch {{}}

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

        const __origContentWindow = Object.getOwnPropertyDescriptor(
            HTMLIFrameElement.prototype, 'contentWindow'
        ).get;

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

        document.querySelectorAll('iframe').forEach((iframe) => {{
            try {{ __patchIframe(iframe); }} catch {{}}
        }});

        // ============================================
        // WORKER NAVIGATOR CONSISTENCY
        // ============================================
        const __workerOverrides = `
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
                        if (scriptURL.startsWith('blob:')) {{
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
        // SERVICE WORKER CLEANUP
        // ============================================
        if (typeof window !== 'undefined' && navigator.serviceWorker) {{
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
        // CANVAS FINGERPRINT PROTECTION
        // ============================================
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

        const __origToBlob = HTMLCanvasElement.prototype.toBlob;
        HTMLCanvasElement.prototype.toBlob = function(callback, type, quality) {{
            const clone = __applyCanvasNoise(this);
            if (clone) return __origToBlob.apply(clone, arguments);
            return __origToBlob.apply(this, arguments);
        }};
        __protectFn(HTMLCanvasElement.prototype, 'toBlob',
            'function toBlob() {{ [native code] }}');

        // ============================================
        // AUDIO FINGERPRINT PROTECTION
        // ============================================
        const __noisedChannels = new WeakMap();
        const __origGetChannelData = AudioBuffer.prototype.getChannelData;
        AudioBuffer.prototype.getChannelData = function(channel) {{
            const data = __origGetChannelData.call(this, channel);
            if (!__noisedChannels.has(this)) __noisedChannels.set(this, new Set());
            const noised = __noisedChannels.get(this);
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
        __protectFn(AudioBuffer.prototype, 'getChannelData',
            'function getChannelData() {{ [native code] }}');
    """
