"""Chrome extension generator for the native engine.

Generates a Manifest V3 Chrome extension that provides DOM access and stealth
JS injection WITHOUT using Chrome DevTools Protocol.  Python generates all
three extension files at launch time and writes them to a temp directory.
Chrome loads the extension via ``--load-extension``.

Extension files:
    manifest.json  — static MV3 manifest
    _config.json   — dynamic: contains ws_port for the background worker
    background.js  — service worker: WebSocket client to Python, chrome.* API cmds
    content.js     — dynamic: stealth JS embedded, DOM queries, coordinate mapping
"""

from __future__ import annotations

import json


def generate_extension(ws_port: int, stealth_js: str) -> dict[str, str]:
    """Generate Chrome extension files for the native engine.

    Args:
        ws_port: WebSocket server port for Python communication.
        stealth_js: The stealth JS to inject into every page.

    Returns:
        Dict mapping filenames to file contents:
        ``{"manifest.json": "...", "background.js": "...", "content.js": "...", "_config.json": "..."}``.
    """
    return {
        "manifest.json": _build_manifest(),
        "background.js": _build_background(ws_port),
        "offscreen.html": _build_offscreen_html(),
        "offscreen.js": _build_offscreen_js(ws_port),
        "content.js": _build_content(stealth_js),
    }


# ------------------------------------------------------------------
# manifest.json (static)
# ------------------------------------------------------------------

def _build_manifest() -> str:
    manifest = {
        "manifest_version": 3,
        "name": "Stealth Bridge",
        "version": "1.0",
        "permissions": [
            "activeTab",
            "tabs",
            "scripting",
            "browsingData",
            "alarms",
            "offscreen",
        ],
        "host_permissions": ["<all_urls>"],
        "background": {
            "service_worker": "background.js",
        },
        "content_scripts": [
            {
                "matches": ["<all_urls>"],
                "js": ["content.js"],
                "run_at": "document_start",
                "all_frames": True,
                "match_about_blank": True,
            }
        ],
    }
    return json.dumps(manifest, indent=2)


# ------------------------------------------------------------------
# _config.json (dynamic — carries ws_port)
# ------------------------------------------------------------------

def _build_config(ws_port: int) -> str:
    return json.dumps({"ws_port": ws_port})


# ------------------------------------------------------------------
# background.js (static template)
# ------------------------------------------------------------------

def _build_background(ws_port: int) -> str:
    return _BG_TEMPLATE.replace("__WS_PORT__", str(ws_port))


# The background service worker creates an offscreen document (which maintains
# the WebSocket) and relays commands between offscreen.js and content scripts.
_BG_TEMPLATE = r"""
var activeTabId = null;
var readyTabs = new Set();

// --- Create the offscreen document for persistent WebSocket ---
async function ensureOffscreen() {
  var contexts = await chrome.runtime.getContexts({
    contextTypes: ['OFFSCREEN_DOCUMENT']
  });
  if (contexts.length === 0) {
    await chrome.offscreen.createDocument({
      url: 'offscreen.html',
      reasons: ['WORKERS'],
      justification: 'Maintain WebSocket connection'
    });
  }
}
ensureOffscreen();

// Keepalive to prevent SW termination
chrome.alarms.create('keepalive', {periodInMinutes: 0.25});
chrome.alarms.onAlarm.addListener(function() { ensureOffscreen(); });

// Track active tab
chrome.tabs.onActivated.addListener(function(info) { activeTabId = info.tabId; });
chrome.tabs.query({active: true, currentWindow: true}).then(function(tabs) {
  if (tabs[0]) activeTabId = tabs[0].id;
});

// Content script readiness
chrome.runtime.onMessage.addListener(function(msg, sender, sendResponse) {
  if (msg.type === 'content_ready' && sender.tab) {
    readyTabs.add(sender.tab.id);
    return;
  }

  // Messages from offscreen.js (WebSocket commands from Python)
  if (msg.type === 'ws_command') {
    handleCommand(msg.data).then(function(result) {
      sendResponse({result: result});
    }).catch(function(err) {
      sendResponse({error: {code: 'ERROR', message: err.message}});
    });
    return true; // async response
  }
});

function waitForContentScript(tabId, timeout) {
  timeout = timeout || 10000;
  if (readyTabs.has(tabId)) return Promise.resolve();
  return new Promise(function(resolve, reject) {
    var start = Date.now();
    function check() {
      if (readyTabs.has(tabId)) resolve();
      else if (Date.now() - start > timeout) reject(new Error('Content script not ready'));
      else setTimeout(check, 50);
    }
    check();
  });
}

async function handleCommand(msg) {
  var method = msg.method;
  var params = msg.params || {};
  switch (method) {
    case 'navigate': {
      readyTabs.delete(activeTabId);
      await chrome.tabs.update(activeTabId, {url: params.url});
      if (params.wait_for_load !== false) {
        await new Promise(function(resolve) {
          function listener(tabId, info) {
            if (tabId === activeTabId && info.status === 'complete') {
              chrome.tabs.onUpdated.removeListener(listener);
              resolve();
            }
          }
          chrome.tabs.onUpdated.addListener(listener);
        });
        await waitForContentScript(activeTabId);
      }
      return {ok: true};
    }
    case 'back':
      await chrome.tabs.goBack(activeTabId);
      return {ok: true};
    case 'forward':
      await chrome.tabs.goForward(activeTabId);
      return {ok: true};
    case 'reload':
      readyTabs.delete(activeTabId);
      await chrome.tabs.reload(activeTabId);
      return {ok: true};
    case 'screenshot': {
      var dataUrl = await chrome.tabs.captureVisibleTab(null, {format: 'png'});
      return {dataUrl: dataUrl};
    }
    case 'clear_state':
      await chrome.browsingData.remove({}, {
        cookies: true, cache: true, localStorage: true,
        sessionStorage: true, indexedDB: true
      });
      return {ok: true};
    case 'execute_script': {
      var results = await chrome.scripting.executeScript({
        target: {tabId: activeTabId},
        world: 'MAIN',
        func: function(code) { return new Function(code)(); },
        args: [params.code]
      });
      return {result: results[0] ? results[0].result : null};
    }
    default: {
      await waitForContentScript(activeTabId);
      return await chrome.tabs.sendMessage(activeTabId, msg);
    }
  }
}
""".strip()


def _build_offscreen_html() -> str:
    return '<html><body><script src="offscreen.js"></script></body></html>'


def _build_offscreen_js(ws_port: int) -> str:
    return _OFFSCREEN_TEMPLATE.replace("__WS_PORT__", str(ws_port))


# The offscreen document maintains the WebSocket to Python.
# It relays messages through chrome.runtime.sendMessage to the background SW.
_OFFSCREEN_TEMPLATE = r"""
var ws = null;

function connect() {
  ws = new WebSocket('ws://127.0.0.1:__WS_PORT__');

  ws.onopen = function() {
    ws.send(JSON.stringify({event: 'connected', params: {}}));
  };

  ws.onmessage = function(event) {
    var msg = JSON.parse(event.data);
    // Forward to background service worker for dispatch
    chrome.runtime.sendMessage(
      {type: 'ws_command', data: msg},
      function(response) {
        if (chrome.runtime.lastError) {
          ws.send(JSON.stringify({
            id: msg.id,
            error: {code: 'ERROR', message: chrome.runtime.lastError.message}
          }));
          return;
        }
        if (response && response.error) {
          ws.send(JSON.stringify({id: msg.id, error: response.error}));
        } else {
          ws.send(JSON.stringify({id: msg.id, result: response ? response.result : null}));
        }
      }
    );
  };

  ws.onclose = function() {
    setTimeout(connect, 1000);
  };

  ws.onerror = function() {};
}

connect();
""".strip()


# ------------------------------------------------------------------
# content.js (dynamic — stealth JS embedded)
# ------------------------------------------------------------------

def _build_content(stealth_js: str) -> str:
    # JSON-encode the stealth JS so all special characters (backticks,
    # quotes, template literals, newlines, etc.) are safely escaped.
    # In content.js we use: script.textContent = JSON.parse(<encoded>);
    encoded_stealth = json.dumps(stealth_js)

    return f'''\
// content.js — Stealth Bridge content script
// Runs at document_start in all frames.

// =============================================
// 1. SCREEN COORDINATE HELPER
//    Queries LIVE values each time (not cached from document_start,
//    because the window may not be positioned yet at that point).
// =============================================
function __getScreenCoords(el) {{
  const rect = el.getBoundingClientRect();
  // Chrome UI height: tabs + address bar + bookmarks bar
  const chromeHeight = window.outerHeight - window.innerHeight;
  return {{
    x: Math.round(window.screenX + rect.left + rect.width / 2),
    y: Math.round(window.screenY + chromeHeight + rect.top + rect.height / 2),
    width: rect.width,
    height: rect.height
  }};
}}

// =============================================
// 2. INJECT STEALTH JS INTO PAGE CONTEXT
// =============================================
// Content scripts run in an isolated world. To override Navigator.prototype etc.,
// we must inject into the MAIN world via a <script> tag.
try {{
  const script = document.createElement('script');
  script.textContent = JSON.parse({encoded_stealth});
  (document.head || document.documentElement).prepend(script);
  script.remove();
}} catch (e) {{
  // CSP may block inline scripts on some pages — stealth won't apply there
}}

// =============================================
// 3. SIGNAL READINESS
// =============================================
chrome.runtime.sendMessage({{type: 'content_ready'}});

// =============================================
// 4. COMMAND HANDLER
// =============================================
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {{
  const handler = async () => {{
    const {{method, params}} = msg;

    switch (method) {{
      case 'get_url':
        return {{url: window.location.href}};

      case 'get_title':
        return {{title: document.title}};

      case 'page_source':
        return {{html: document.documentElement.outerHTML}};

      case 'locate': {{
        const el = document.querySelector(params.selector);
        if (!el) return {{exists: false, visible: false, x: 0, y: 0, width: 0, height: 0}};
        const visible = !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
        const coords = __getScreenCoords(el);
        return {{
          exists: true,
          visible,
          x: coords.x,
          y: coords.y,
          width: coords.width,
          height: coords.height
        }};
      }}

      case 'query': {{
        const el = document.querySelector(params.selector);
        if (!el) throw new Error(`No element matches selector: ${{params.selector}}`);
        return {{
          text: el.textContent || '',
          tagName: el.tagName.toLowerCase(),
          value: el.value || '',
          visible: !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length),
          attributes: Object.fromEntries(
            Array.from(el.attributes).map(a => [a.name, a.value])
          )
        }};
      }}

      case 'query_all': {{
        const els = document.querySelectorAll(params.selector);
        return Array.from(els).map((el, index) => ({{
          index,
          text: el.textContent || '',
          tagName: el.tagName.toLowerCase(),
          value: el.value || '',
          visible: !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length),
          attributes: Object.fromEntries(
            Array.from(el.attributes).map(a => [a.name, a.value])
          )
        }}));
      }}

      case 'wait_for': {{
        const {{selector, timeout = 10000, visible = true}} = params;
        const start = Date.now();
        while (Date.now() - start < timeout) {{
          const el = document.querySelector(selector);
          if (el) {{
            const isVisible = !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
            if (!visible || isVisible) {{
              return {{
                found: true,
                text: el.textContent || '',
                tagName: el.tagName.toLowerCase(),
                value: el.value || '',
                visible: isVisible,
                attributes: Object.fromEntries(
                  Array.from(el.attributes).map(a => [a.name, a.value])
                )
              }};
            }}
          }}
          await new Promise(r => setTimeout(r, 100));
        }}
        return {{found: false}};
      }}

      case 'scroll_to': {{
        const el = document.querySelector(params.selector);
        if (!el) throw new Error(`No element matches selector: ${{params.selector}}`);
        el.scrollIntoView({{behavior: 'smooth', block: 'center'}});
        await new Promise(r => setTimeout(r, 300));
        return {{ok: true}};
      }}

      case 'fill_fast': {{
        const el = document.querySelector(params.selector);
        if (!el) throw new Error(`No element matches selector: ${{params.selector}}`);
        el.focus();
        el.value = params.value;
        el.dispatchEvent(new Event('input', {{bubbles: true}}));
        el.dispatchEvent(new Event('change', {{bubbles: true}}));
        return {{ok: true}};
      }}

      case 'focus': {{
        const el = document.querySelector(params.selector);
        if (!el) throw new Error(`No element matches selector: ${{params.selector}}`);
        el.focus();
        return {{ok: true}};
      }}

      case 'is_visible': {{
        const el = document.querySelector(params.selector);
        return {{visible: el ? !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length) : false}};
      }}

      case 'exists': {{
        return {{exists: !!document.querySelector(params.selector)}};
      }}

      case 'get_text': {{
        const el = document.querySelector(params.selector);
        if (!el) throw new Error(`No element matches selector: ${{params.selector}}`);
        return {{text: el.textContent || ''}};
      }}

      case 'get_attribute': {{
        const el = document.querySelector(params.selector);
        if (!el) throw new Error(`No element matches selector: ${{params.selector}}`);
        return {{value: el.getAttribute(params.attribute)}};
      }}

      case 'wait_for_url': {{
        const {{substring, timeout = 30000}} = params;
        const start = Date.now();
        while (Date.now() - start < timeout) {{
          if (window.location.href.includes(substring)) {{
            return {{matched: true, url: window.location.href}};
          }}
          await new Promise(r => setTimeout(r, 100));
        }}
        return {{matched: false, url: window.location.href}};
      }}

      case 'wait_for_text': {{
        const {{selector, text, timeout = 10000}} = params;
        const start = Date.now();
        while (Date.now() - start < timeout) {{
          const el = document.querySelector(selector);
          if (el && (el.textContent || '').includes(text)) {{
            return {{found: true}};
          }}
          await new Promise(r => setTimeout(r, 100));
        }}
        return {{found: false}};
      }}

      default:
        throw new Error(`Unknown content method: ${{method}}`);
    }}
  }};

  handler().then(sendResponse).catch(err => {{
    sendResponse({{__error: true, code: 'CONTENT_ERROR', message: err.message}});
  }});
  return true; // Keep sendResponse channel open for async
}});
'''
