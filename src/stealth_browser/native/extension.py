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
        "stealth.js": _build_stealth_file(stealth_js),
        "content.js": _build_content(),
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

// --- WebSocket connection ---
// Try offscreen document first (persistent). If offscreen API is blocked
// (snap Chromium, older Chrome), fall back to direct SW WebSocket.
var wsReady = false;

async function ensureOffscreen() {
  if (typeof chrome.offscreen === 'undefined') return false;
  try {
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
    return true;
  } catch(e) {
    return false;
  }
}

// Direct SW WebSocket fallback (used when offscreen is unavailable)
var swWs = null;
function connectDirectWs() {
  swWs = new WebSocket('ws://127.0.0.1:' + __WS_PORT__);
  swWs.onopen = function() { wsReady = true; };
  swWs.onmessage = function(event) {
    var msg = JSON.parse(event.data);
    handleCommand(msg).then(function(result) {
      swWs.send(JSON.stringify({id: msg.id, result: result}));
    }).catch(function(err) {
      swWs.send(JSON.stringify({id: msg.id, error: {code: 'ERROR', message: err.message}}));
    });
  };
  swWs.onclose = function() { wsReady = false; setTimeout(connectDirectWs, 1000); };
  swWs.onerror = function() {};
}

ensureOffscreen().then(function(ok) {
  if (!ok) connectDirectWs();
});

// --- Register stealth JS in MAIN world (bypasses CSP) ---
// <script> tag injection from content scripts is blocked by CSP on many sites.
// chrome.scripting.registerContentScripts injects at the engine level.
chrome.scripting.registerContentScripts([{
  id: 'stealth-main-world',
  matches: ['<all_urls>'],
  js: ['stealth.js'],
  runAt: 'document_start',
  world: 'MAIN',
  allFrames: true,
  matchOriginAsFallback: true
}]).catch(function() {
  // Already registered (service worker restarted)
});

// Keepalive to prevent SW termination
chrome.alarms.create('keepalive', {periodInMinutes: 0.25});
chrome.alarms.onAlarm.addListener(function() {
  ensureOffscreen().then(function(ok) {
    if (!ok && !wsReady) connectDirectWs();
  });
});

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
# stealth.js (MAIN world — registered via chrome.scripting)
# ------------------------------------------------------------------

def _build_stealth_file(stealth_js: str) -> str:
    """Build the MAIN world script: dialog handler + stealth JS.

    This file is injected into the page's MAIN world via
    chrome.scripting.registerContentScripts (not a <script> tag),
    which bypasses CSP restrictions.
    """
    dialog_handler = """\
// Dialog auto-accept (before any page script caches a reference)
window.__dialogLog = [];
window.confirm = function(msg) {
  window.__dialogLog.push({type:'confirm', message:msg, time:Date.now()});
  return true;
};
window.alert = function(msg) {
  window.__dialogLog.push({type:'alert', message:msg, time:Date.now()});
};
window.prompt = function(msg, def) {
  window.__dialogLog.push({type:'prompt', message:msg, time:Date.now()});
  return def || '';
};
"""
    return dialog_handler + "\n" + stealth_js


# ------------------------------------------------------------------
# content.js (isolated world — DOM commands only)
# ------------------------------------------------------------------

def _build_content() -> str:
    return _CONTENT_TEMPLATE


_CONTENT_TEMPLATE = r"""
// content.js — Stealth Bridge content script (isolated world)
// Stealth JS runs in MAIN world via chrome.scripting.registerContentScripts.
// This script handles DOM queries and coordinate mapping only.

function __getScreenCoords(el) {
  var rect = el.getBoundingClientRect();
  var chromeHeight = window.outerHeight - window.innerHeight;
  return {
    x: Math.round(window.screenX + rect.left + rect.width / 2),
    y: Math.round(window.screenY + chromeHeight + rect.top + rect.height / 2),
    width: rect.width,
    height: rect.height
  };
}

chrome.runtime.sendMessage({type: 'content_ready'});

chrome.runtime.onMessage.addListener(function(msg, sender, sendResponse) {
  var handler = async function() {
    var method = msg.method;
    var params = msg.params || {};

    switch (method) {
      case 'get_url':
        return {url: window.location.href};
      case 'get_title':
        return {title: document.title};
      case 'page_source':
        return {html: document.documentElement.outerHTML};
      case 'locate': {
        var el = document.querySelector(params.selector);
        if (!el) return {exists: false, visible: false, x: 0, y: 0, width: 0, height: 0};
        var vis = !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
        var coords = __getScreenCoords(el);
        return {exists: true, visible: vis, x: coords.x, y: coords.y, width: coords.width, height: coords.height};
      }
      case 'query': {
        var el = document.querySelector(params.selector);
        if (!el) throw new Error('No element: ' + params.selector);
        return {text: el.textContent||'', tagName: el.tagName.toLowerCase(), value: el.value||'',
          visible: !!(el.offsetWidth||el.offsetHeight||el.getClientRects().length),
          attributes: Object.fromEntries(Array.from(el.attributes).map(function(a){return [a.name,a.value]}))};
      }
      case 'query_all': {
        var els = document.querySelectorAll(params.selector);
        return Array.from(els).map(function(el, i) {
          return {index:i, text:el.textContent||'', tagName:el.tagName.toLowerCase(), value:el.value||'',
            visible:!!(el.offsetWidth||el.offsetHeight||el.getClientRects().length),
            attributes:Object.fromEntries(Array.from(el.attributes).map(function(a){return [a.name,a.value]}))};
        });
      }
      case 'wait_for': {
        var sel = params.selector, tmo = params.timeout||10000, wantVis = params.visible !== false;
        var start = Date.now();
        while (Date.now() - start < tmo) {
          var el = document.querySelector(sel);
          if (el) {
            var isVis = !!(el.offsetWidth||el.offsetHeight||el.getClientRects().length);
            if (!wantVis || isVis) {
              return {found:true, text:el.textContent||'', tagName:el.tagName.toLowerCase(),
                value:el.value||'', visible:isVis,
                attributes:Object.fromEntries(Array.from(el.attributes).map(function(a){return [a.name,a.value]}))};
            }
          }
          await new Promise(function(r){setTimeout(r,100)});
        }
        return {found: false};
      }
      case 'scroll_to': {
        var el = document.querySelector(params.selector);
        if (!el) throw new Error('No element: ' + params.selector);
        el.scrollIntoView({behavior:'smooth', block:'center'});
        await new Promise(function(r){setTimeout(r,300)});
        return {ok: true};
      }
      case 'fill_fast': {
        var el = document.querySelector(params.selector);
        if (!el) throw new Error('No element: ' + params.selector);
        el.focus();
        el.value = params.value;
        el.dispatchEvent(new Event('input', {bubbles:true}));
        el.dispatchEvent(new Event('change', {bubbles:true}));
        return {ok: true};
      }
      case 'focus': {
        var el = document.querySelector(params.selector);
        if (!el) throw new Error('No element: ' + params.selector);
        el.focus();
        return {ok: true};
      }
      case 'is_visible': {
        var el = document.querySelector(params.selector);
        return {visible: el ? !!(el.offsetWidth||el.offsetHeight||el.getClientRects().length) : false};
      }
      case 'exists':
        return {exists: !!document.querySelector(params.selector)};
      case 'get_text': {
        var el = document.querySelector(params.selector);
        if (!el) throw new Error('No element: ' + params.selector);
        return {text: el.textContent||''};
      }
      case 'get_attribute': {
        var el = document.querySelector(params.selector);
        if (!el) throw new Error('No element: ' + params.selector);
        return {value: el.getAttribute(params.attribute)};
      }
      case 'wait_for_url': {
        var sub = params.substring, tmo = params.timeout||30000, start = Date.now();
        while (Date.now() - start < tmo) {
          if (window.location.href.includes(sub)) return {matched:true, url:window.location.href};
          await new Promise(function(r){setTimeout(r,100)});
        }
        return {matched:false, url:window.location.href};
      }
      case 'wait_for_text': {
        var sel = params.selector, txt = params.text, tmo = params.timeout||10000, start = Date.now();
        while (Date.now() - start < tmo) {
          var el = document.querySelector(sel);
          if (el && (el.textContent||'').includes(txt)) return {found:true};
          await new Promise(function(r){setTimeout(r,100)});
        }
        return {found:false};
      }
      default:
        throw new Error('Unknown method: ' + method);
    }
  };
  handler().then(sendResponse).catch(function(err) {
    sendResponse({__error:true, code:'CONTENT_ERROR', message:err.message});
  });
  return true;
});
""".strip()
