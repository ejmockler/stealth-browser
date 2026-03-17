"""JavaScript injection scripts for browser stealth."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from stealth_browser.config import PlatformConfig


class StealthScripts:
    """JavaScript injection scripts for evading bot detection."""

    @staticmethod
    def get_stealth_scripts(config: "PlatformConfig") -> str:
        """
        Generate JavaScript code to inject for stealth browsing.

        Args:
            config: Platform configuration with fingerprint values

        Returns:
            JavaScript code string to execute in browser
        """
        languages_js = str(config.locale.languages).replace("'", '"')

        return f"""
            // ============================================
            // WEBDRIVER DETECTION HIDING
            // ============================================

            // Remove webdriver property
            delete Object.getPrototypeOf(navigator).webdriver;
            Object.defineProperty(navigator, 'webdriver', {{
                get: () => undefined,
                configurable: true
            }});

            // Hide automation-related properties
            const automationProps = [
                '__webdriver_script_fn',
                '__driver_evaluate',
                '__webdriver_evaluate',
                '__selenium_evaluate',
                '__fxdriver_evaluate',
                '__driver_unwrapped',
                '__webdriver_unwrapped',
                '__selenium_unwrapped',
                '__fxdriver_unwrapped',
                '_Selenium_IDE_Recorder',
                '_selenium',
                'calledSelenium',
                '$chrome_asyncScriptInfo',
                '$cdc_asdjflasutopfhvcZLmcfl_',
                '$wdc_',
                'webdriver',
                '__nightmare',
                '__puppeteer_evaluation_script__',
                '__playwright',
            ];

            for (const prop of automationProps) {{
                if (prop in window) delete window[prop];
                if (prop in document) delete document[prop];
            }}

            // ============================================
            // PLATFORM FINGERPRINTING
            // ============================================

            Object.defineProperty(navigator, 'platform', {{
                get: () => '{config.platform}',
                configurable: true
            }});

            Object.defineProperty(navigator, 'hardwareConcurrency', {{
                get: () => {config.hardware.cores},
                configurable: true
            }});

            Object.defineProperty(navigator, 'deviceMemory', {{
                get: () => {config.hardware.memory},
                configurable: true
            }});

            // ============================================
            // NETWORK SIMULATION
            // ============================================

            Object.defineProperty(navigator, 'connection', {{
                get: () => ({{
                    downlink: {config.network.downlink},
                    rtt: {config.network.rtt},
                    effectiveType: '4g',
                    saveData: false,
                    type: 'wifi',
                    downlinkMax: Infinity,
                    onchange: null
                }}),
                configurable: true
            }});

            // ============================================
            // WEBGL FINGERPRINT MASKING
            // ============================================

            const getParameterProxy = new Proxy(WebGLRenderingContext.prototype.getParameter, {{
                apply: function(target, thisArg, args) {{
                    try {{
                        const param = args[0];
                        const debugInfo = thisArg.getExtension('WEBGL_debug_renderer_info');
                        if (debugInfo) {{
                            if (param === debugInfo.UNMASKED_RENDERER_WEBGL) {{
                                return '{config.hardware.gpu}';
                            }}
                            if (param === debugInfo.UNMASKED_VENDOR_WEBGL) {{
                                return 'Google Inc.';
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
            WebGLRenderingContext.prototype.getParameter = getParameterProxy;

            if (typeof WebGL2RenderingContext !== 'undefined') {{
                WebGL2RenderingContext.prototype.getParameter = getParameterProxy;
            }}

            // ============================================
            // PERFORMANCE TIMING RANDOMIZATION
            // ============================================

            const originalNow = Performance.prototype.now;
            Performance.prototype.now = function() {{
                return originalNow.call(this) + (Math.random() * 0.1);
            }};

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
            // PLUGINS AND MIME TYPES
            // ============================================

            Object.defineProperty(navigator, 'plugins', {{
                get: () => {{
                    const plugins = [
                        {{ name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' }},
                        {{ name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' }},
                        {{ name: 'Native Client', filename: 'internal-nacl-plugin', description: '' }}
                    ];
                    const pluginArray = Object.create(PluginArray.prototype);
                    plugins.forEach((p, i) => {{ pluginArray[i] = p; }});
                    pluginArray.length = plugins.length;
                    pluginArray.item = (i) => pluginArray[i];
                    pluginArray.namedItem = (name) => plugins.find(p => p.name === name);
                    pluginArray.refresh = () => {{}};
                    return pluginArray;
                }},
                configurable: true
            }});

            // ============================================
            // LANGUAGE AND LOCALE
            // ============================================

            Object.defineProperty(navigator, 'language', {{
                get: () => '{config.locale.locale}',
                configurable: true
            }});

            Object.defineProperty(navigator, 'languages', {{
                get: () => {languages_js},
                configurable: true
            }});

            // ============================================
            // TIMEZONE SPOOFING
            // ============================================

            const targetTimezone = '{config.locale.timezone}';
            const targetOffset = {config.locale.timezone_offset};

            Date.prototype.getTimezoneOffset = function() {{
                return targetOffset;
            }};

            const OriginalDateTimeFormat = Intl.DateTimeFormat;
            Intl.DateTimeFormat = function(locales, options) {{
                options = options || {{}};
                if (!options.timeZone) {{
                    options.timeZone = targetTimezone;
                }}
                return new OriginalDateTimeFormat(locales, options);
            }};
            Intl.DateTimeFormat.prototype = OriginalDateTimeFormat.prototype;
            Intl.DateTimeFormat.supportedLocalesOf = OriginalDateTimeFormat.supportedLocalesOf;

            const originalResolvedOptions = OriginalDateTimeFormat.prototype.resolvedOptions;
            Intl.DateTimeFormat.prototype.resolvedOptions = function() {{
                const options = originalResolvedOptions.call(this);
                options.timeZone = targetTimezone;
                return options;
            }};

            // ============================================
            // PERMISSIONS API
            // ============================================

            const originalQuery = Permissions.prototype.query;
            Permissions.prototype.query = async function(parameters) {{
                if (parameters.name === 'notifications') {{
                    return {{ state: 'denied', onchange: null }};
                }}
                return originalQuery.call(this, parameters);
            }};

            // ============================================
            // HUMAN-LIKE BEHAVIOR INJECTION
            // ============================================

            let lastInteraction = Date.now();
            const updateInteraction = () => {{ lastInteraction = Date.now(); }};

            ['mousemove', 'mousedown', 'mouseup', 'scroll', 'keydown', 'touchstart', 'click'].forEach(event => {{
                document.addEventListener(event, updateInteraction, {{ passive: true }});
            }});

            // Periodic micro-movements when idle
            let lastMoveTime = Date.now();
            const moveInterval = 1000 + Math.random() * 3000;

            setInterval(() => {{
                const now = Date.now();
                const timeSinceLastMove = now - lastMoveTime;
                const timeSinceLastInteraction = now - lastInteraction;

                if (timeSinceLastInteraction > 3000 &&
                    timeSinceLastMove > moveInterval &&
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
                    lastMoveTime = now;
                }}
            }}, 500);

            // ============================================
            // CANVAS FINGERPRINT PROTECTION
            // ============================================

            const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
            HTMLCanvasElement.prototype.toDataURL = function(type) {{
                if (this.width === 0 || this.height === 0) {{
                    return originalToDataURL.apply(this, arguments);
                }}
                const ctx = this.getContext('2d');
                if (ctx) {{
                    const imageData = ctx.getImageData(0, 0, this.width, this.height);
                    for (let i = 0; i < imageData.data.length; i += 4) {{
                        imageData.data[i] ^= (Math.random() < 0.01 ? 1 : 0);
                    }}
                    ctx.putImageData(imageData, 0, 0);
                }}
                return originalToDataURL.apply(this, arguments);
            }};

            // ============================================
            // AUDIO FINGERPRINT PROTECTION
            // ============================================

            const originalGetChannelData = AudioBuffer.prototype.getChannelData;
            AudioBuffer.prototype.getChannelData = function(channel) {{
                const data = originalGetChannelData.call(this, channel);
                for (let i = 0; i < data.length; i++) {{
                    if (Math.random() < 0.0001) {{
                        data[i] += (Math.random() - 0.5) * 0.0001;
                    }}
                }}
                return data;
            }};

            console.log('[StealthBrowser] Fingerprint protection initialized');
        """

    @staticmethod
    def get_minimal_stealth_scripts(config: "PlatformConfig") -> str:
        """
        Generate minimal stealth scripts for faster execution.

        Only includes essential WebDriver hiding, platform spoofing, and timezone.
        """
        languages_js = str(config.locale.languages).replace("'", '"')

        return f"""
            delete Object.getPrototypeOf(navigator).webdriver;
            Object.defineProperty(navigator, 'platform', {{
                get: () => '{config.platform}'
            }});
            Object.defineProperty(navigator, 'hardwareConcurrency', {{
                get: () => {config.hardware.cores}
            }});
            Object.defineProperty(navigator, 'language', {{
                get: () => '{config.locale.locale}'
            }});
            Object.defineProperty(navigator, 'languages', {{
                get: () => {languages_js}
            }});
            Date.prototype.getTimezoneOffset = function() {{
                return {config.locale.timezone_offset};
            }};
        """
