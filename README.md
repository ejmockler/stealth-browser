# stealth-browser

Browser automation that mass fingerprint detection can't distinguish from a human.

Two engines. One API surface. Every JavaScript-observable property spoofed, protected with `[native code]` toString signatures, and propagated across iframes, workers, and service workers. Built on top of [Patchright](https://github.com/nicoleahmed/patchright) (CDP-leak-free Playwright fork) and Selenium, with ~1,800 lines of custom stealth on top.

## Detection scorecard

| Test | Score | Notes |
|---|---|---|
| [rebrowser](https://bot-detector.rebrowser.net/) | **6/6 GREEN** | 4 gray are interactive triggers, not failures |
| [sannysoft](https://bot.sannysoft.com/) | **56/56** | Every check green |
| [BrowserScan](https://browserscan.net/bot-detection) | **HUMAN** | Webdriver, UA, Navigator all clean |
| [Cloudflare Turnstile](https://turnstile-workers-demo.nickvang.workers.dev/) | **PASSED** | |
| [deviceandbrowserinfo](https://deviceandbrowserinfo.com/are_you_a_bot) | detected | CDP-level signal (inherent to Playwright protocol, not patchable in JS) |

The one remaining detection vector is Chrome DevTools Protocol usage itself — Playwright/Patchright must speak CDP to control Chrome. This lives below the JavaScript layer and can't be spoofed without forking Chromium. Most sites (including enterprise SSO, Workday, Salesforce, etc.) don't check for it.

## Install

```bash
pip install stealth-browser                    # Selenium path
pip install 'stealth-browser[patchright]'      # Patchright path (recommended)
```

Or from source:

```bash
git clone https://github.com/ejmockler/stealth-browser.git
cd stealth-browser
pip install -e '.[patchright]'
```

Requires Python 3.9+. Patchright auto-installs Chromium on first run.

## Quick start

### Patchright (async, strongest stealth)

```python
from stealth_browser.patchright import (
    create_stealth_browser,
    create_stealth_context,
    new_stealth_page,
    stealth_goto,
    close_stealth_browser,
)

browser = await create_stealth_browser(headless=True)
context = await create_stealth_context(browser)
page = await new_stealth_page(context)

await stealth_goto(page, "https://example.com")
await page.click("#login")
await page.fill("#email", "user@example.com")
content = await page.text_content(".result")

await close_stealth_browser(browser)
```

Standard Playwright API after setup — `page.click()`, `page.fill()`, `page.wait_for_selector()`, etc. The stealth layer is invisible.

### Selenium (sync, simpler)

```python
from stealth_browser import StealthBrowser

with StealthBrowser(headless=True) as browser:
    browser.navigate("https://example.com")
    browser.click("#login")
    browser.fill("#email", "user@example.com")
    text = browser.get_text(".result")
```

Built-in human-like behavior: typing cadence (30-120ms per key), mouse movements via ActionChains, random "thinking" pauses.

## Agent integration

stealth-browser is designed to be the browser backend for autonomous agents — login through SSO, interact with SPAs, extract data from authenticated surfaces.

### With an async agent loop

```python
import asyncio
from stealth_browser.patchright import (
    create_stealth_browser, create_stealth_context,
    new_stealth_page, stealth_goto, close_stealth_browser,
)

async def agent_browse(url: str, actions: list[dict]) -> dict:
    """Agent-callable browser tool."""
    browser = await create_stealth_browser(headless=True)
    context = await create_stealth_context(browser)
    page = await new_stealth_page(context)

    try:
        await stealth_goto(page, url)
        results = {}

        for action in actions:
            match action["type"]:
                case "click":
                    await page.click(action["selector"])
                case "fill":
                    await page.fill(action["selector"], action["value"])
                case "extract":
                    results[action["key"]] = await page.text_content(action["selector"])
                case "wait":
                    await page.wait_for_selector(action["selector"])
                case "screenshot":
                    await page.screenshot(path=action.get("path", "screenshot.png"))

        return results
    finally:
        await close_stealth_browser(browser)
```

### Persistent session (reuse across calls)

```python
class BrowserSession:
    """Long-lived browser for agents that need session persistence."""

    def __init__(self):
        self.browser = None
        self.context = None
        self.page = None

    async def start(self):
        self.browser = await create_stealth_browser(headless=True)
        self.context = await create_stealth_context(self.browser)
        self.page = await new_stealth_page(self.context)

    async def goto(self, url: str):
        await stealth_goto(self.page, url)

    async def stop(self):
        if self.browser:
            await close_stealth_browser(self.browser)
```

### With Selenium (sync agent)

```python
from stealth_browser import StealthBrowser

def browse_tool(url: str, extract_selector: str) -> str:
    """Sync browser tool for non-async agents."""
    with StealthBrowser(headless=True) as browser:
        browser.navigate(url)
        browser.wait_for(extract_selector, timeout=10)
        return browser.get_text(extract_selector)
```

## Configuration

### Fingerprint control

Every session gets a randomized but internally consistent fingerprint — platform, GPU, cores, memory, network, timezone, locale, viewport. All values propagate to iframes, workers, and service workers.

```python
from stealth_browser.config import BrowserConfig, LocaleConfig

# Random fingerprint (default)
browser = await create_stealth_browser()

# Force platform
browser = await create_stealth_browser(platform="macos")  # or "windows"

# Custom locale
browser = await create_stealth_browser(
    locale=LocaleConfig.california()  # America/Los_Angeles, en-US
)

# Auto-detect locale from IP
browser = await create_stealth_browser(auto_detect_locale=True)

# Access the generated config
config = browser._stealth_config
print(config.user_agent)       # Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) ...
print(config.hardware.gpu)     # ANGLE (Apple, Apple M1)
print(config.hardware.cores)   # 8
print(config.locale.timezone)  # America/Los_Angeles
```

### Headless vs headed

```python
# Headless (default) — no visible window, full stealth still applied
browser = await create_stealth_browser(headless=True)

# Headed — visible window, useful for debugging
browser = await create_stealth_browser(headless=False)

# Slow motion for debugging
browser = await create_stealth_browser(headless=False, slow_mo=100)
```

### Persistent storage

```python
from pathlib import Path

context = await create_stealth_context(
    browser,
    profile_dir=Path("~/.my-agent/browser-state"),  # cookies/storage persist here
)
```

## Architecture

```
stealth-browser
├── config.py          250 lines   Fingerprint pools (hardware, network, locale, platform)
├── patchright.py    1,918 lines   Async Patchright stealth engine
├── browser.py         539 lines   Sync Selenium StealthBrowser
├── scripts.py         344 lines   JS injection (Selenium path)
├── driver.py          311 lines   Selenium DriverManager
├── geolocation.py     124 lines   IP-based locale detection
├── exceptions.py       26 lines   Error hierarchy
└── __init__.py         53 lines   Public API
```

### What Patchright gives us

- `Runtime.enable` replaced with `Page.createIsolatedWorld` (no CDP leak to page JS)
- `Console.enable` removed entirely
- `--enable-automation` flag stripped

### What we build on top (~1,800 lines)

**Route-based pre-parse injection** — intercepts every HTML response via `context.route("**/*")` and injects stealth JS as the first `<script>` in `<head>`, before the page's own scripts parse. This is stronger than `addInitScript` for properties read at load time.

**Full navigator spoofing** — all overrides on `Navigator.prototype` (not the instance), so `Object.getOwnPropertyNames(navigator)` returns `[]` like real Chrome. Covers: `webdriver`, `platform`, `hardwareConcurrency`, `deviceMemory`, `language`, `languages`, `pdfViewerEnabled`, `userAgent`, `appVersion`, `connection`, `plugins`, `mimeTypes`.

**Client Hints** — `navigator.userAgentData` with rotating `Sec-CH-UA` "Not A Brand" variants keyed to Chrome version. Full `getHighEntropyValues()` implementation (platformVersion, architecture, bitness, fullVersionList, model, wow64).

**WebGL masking** — Proxy on `getParameter()` for both WebGL1 and WebGL2. GPU vendor/renderer matches config. `ALIASED_LINE_WIDTH_RANGE` normalized.

**Canvas fingerprint noise** — seeded deterministic PRNG per session. Same seed = same canvas hash across calls (idempotent). Bit-flips on an offscreen clone, not the visible canvas.

**Audio fingerprint noise** — `AudioBuffer.getChannelData()` override with WeakMap tracking to prevent cumulative drift on repeated calls. Same seeded PRNG.

**Headless detection resistance** — `screen.availHeight` simulates taskbar, `screenX`/`screenY` non-zero, `outerHeight` > `innerHeight`, `document.hasFocus()` true, Permissions API returns `denied` for notifications.

**Cross-context propagation:**
- **Iframes** — Proxy on `contentWindow`/`contentDocument` getters patches `about:blank` realms synchronously on first access (MutationObserver fires too late)
- **Workers** — Proxy on `Worker`/`SharedWorker` constructors wraps blob URLs with full navigator + WebGL + timezone + performance overrides
- **Service workers** — route handler prepends overrides to SW script responses

**toString() armor** — every overridden function registered in a `__protectedFns` Map. `Function.prototype.toString.call(fn)` returns `function name() { [native code] }`. Covers all contexts.

**Domain passthrough** — skips injection for domains like `duosecurity.com` where strict CSP would break the page.

## Selenium API reference

`StealthBrowser` wraps Selenium with human-like behavior and stealth scripts:

```python
with StealthBrowser(headless=True) as browser:
    # Navigation
    browser.navigate(url)
    browser.refresh()
    browser.back()
    browser.forward()
    browser.get_url()
    browser.get_title()

    # Interaction (human-like delays built in)
    browser.click(selector)
    browser.fill(selector, text)           # character-by-character typing
    browser.fill_fast(selector, text)      # JS injection (for passwords)
    browser.type_text(selector, text)
    browser.press_key(key)
    browser.hover(selector)

    # Queries
    browser.wait_for(selector, timeout=10)
    browser.is_visible(selector)
    browser.exists(selector)
    browser.get_text(selector)
    browser.get_attribute(selector, attr)
    browser.get_value(selector)
    browser.find_all(selector)

    # Safe interaction (returns bool, no exceptions)
    browser.try_click(selector)
    browser.try_fill(selector, text)

    # Waits
    browser.wait_for_url(substring, timeout=10)
    browser.wait_for_url_change(current_url, timeout=10)
    browser.wait_for_text(selector, text, timeout=10)
    browser.sleep(seconds)

    # Debug
    browser.screenshot(path)
    browser.get_page_source()
    browser.execute_script(js)

    # Session management
    browser.clear_state()
    browser.new_session()
```

## Known limitations

- **CDP detection**: Playwright/Patchright speaks CDP to Chrome. A handful of advanced detectors (deviceandbrowserinfo) can identify this at the protocol level. Not patchable in JS — would require a Chromium fork. Most production sites don't check for it.
- **Service worker navigator**: SW contexts initialize before we can inject overrides. Our JS unregisters stale SWs on page load, but the SW's own `navigator` reflects real values. Affects creepjs/incolumitas SW checks. Sites that use SW-based detection are rare.
- **BotD headed mode**: Fingerprint.com's BotD performs better in headed mode due to additional rendering checks.

## License

MIT
