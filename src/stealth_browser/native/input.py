"""OS-level input backend for the native engine.

Provides mouse and keyboard injection via platform-native APIs using only
``ctypes`` — zero external dependencies.

Three backends:
- **QuartzBackend** (macOS) — CoreGraphics via ctypes
- **Win32Backend** (Windows) — user32.dll via ctypes
- **X11Backend** (Linux) — libX11 + libXtst via ctypes

Usage::

    from stealth_browser.native.input import create_backend

    backend = create_backend()       # auto-selects for current OS
    backend.mouse_move(500, 300)
    backend.mouse_down(500, 300)
    backend.mouse_up(500, 300)
    backend.key_down(0x24)           # platform keycode
    backend.key_up(0x24)
    backend.type_char("a")           # unicode character
"""

from __future__ import annotations

import ctypes
import ctypes.util
import sys
from typing import Dict, Protocol, runtime_checkable


# =====================================================================
# Protocol
# =====================================================================

@runtime_checkable
class InputBackend(Protocol):
    """Contract that every OS input backend must satisfy."""

    def mouse_move(self, x: int, y: int) -> None: ...
    def mouse_down(self, x: int, y: int) -> None: ...
    def mouse_up(self, x: int, y: int) -> None: ...
    def key_down(self, keycode: int) -> None: ...
    def key_up(self, keycode: int) -> None: ...
    def type_char(self, char: str) -> None:
        """Type a single unicode character using the platform-native method."""
        ...


# =====================================================================
# Key-name-to-keycode mappings
# =====================================================================
# Key names match what ``selectors.translate_key`` produces (Playwright
# names: "Enter", "Tab", "ArrowUp", ...).

# -- macOS: CGKeyCode (kVK_*) values from <Carbon/Events.h> -----------

_MAC_KEYMAP: Dict[str, int] = {
    # Navigation / editing
    "Enter": 0x24,
    "Tab": 0x30,
    "Escape": 0x35,
    "Backspace": 0x33,
    "Delete": 0x75,
    "Space": 0x31,
    # Arrows
    "ArrowUp": 0x7E,
    "ArrowDown": 0x7D,
    "ArrowLeft": 0x7B,
    "ArrowRight": 0x7C,
    # Page keys
    "Home": 0x73,
    "End": 0x77,
    "PageUp": 0x74,
    "PageDown": 0x79,
    # Modifiers
    "Shift": 0x38,
    "Control": 0x3B,
    "Alt": 0x3A,
    "Meta": 0x37,  # Command key
    # Letters (kVK_ANSI_*)
    "a": 0x00, "b": 0x0B, "c": 0x08, "d": 0x02, "e": 0x0E,
    "f": 0x03, "g": 0x05, "h": 0x04, "i": 0x22, "j": 0x26,
    "k": 0x28, "l": 0x25, "m": 0x2E, "n": 0x2D, "o": 0x1F,
    "p": 0x23, "q": 0x0C, "r": 0x0F, "s": 0x01, "t": 0x11,
    "u": 0x20, "v": 0x09, "w": 0x0D, "x": 0x07, "y": 0x10,
    "z": 0x06,
    # Digits (kVK_ANSI_*)
    "0": 0x1D, "1": 0x12, "2": 0x13, "3": 0x14, "4": 0x15,
    "5": 0x17, "6": 0x16, "7": 0x1A, "8": 0x1C, "9": 0x19,
}

# -- Windows: VK_* virtual-key codes ----------------------------------

_WIN_KEYMAP: Dict[str, int] = {
    # Navigation / editing
    "Enter": 0x0D,
    "Tab": 0x09,
    "Escape": 0x1B,
    "Backspace": 0x08,
    "Delete": 0x2E,
    "Space": 0x20,
    # Arrows
    "ArrowUp": 0x26,
    "ArrowDown": 0x28,
    "ArrowLeft": 0x25,
    "ArrowRight": 0x27,
    # Page keys
    "Home": 0x24,
    "End": 0x23,
    "PageUp": 0x21,
    "PageDown": 0x22,
    # Modifiers
    "Shift": 0x10,
    "Control": 0x11,
    "Alt": 0x12,
    "Meta": 0x5B,  # Left Windows key
    # Letters — VK_A..VK_Z are 0x41..0x5A
    **{chr(c): c - 32 for c in range(ord("a"), ord("z") + 1)},  # 'a'→0x41
    # Digits — VK_0..VK_9 are 0x30..0x39
    **{str(d): 0x30 + d for d in range(10)},
}

# Linux keymaps are resolved at runtime via XStringToKeysym, but we keep
# a name→keysym table for the *named* keys so we don't need to call
# XStringToKeysym for common cases.

_LINUX_KEYSYM_MAP: Dict[str, int] = {
    "Enter": 0xFF0D,   # XK_Return
    "Tab": 0xFF09,     # XK_Tab
    "Escape": 0xFF1B,  # XK_Escape
    "Backspace": 0xFF08,  # XK_BackSpace
    "Delete": 0xFFFF,  # XK_Delete
    "Space": 0x0020,   # XK_space
    # Arrows
    "ArrowUp": 0xFF52,
    "ArrowDown": 0xFF54,
    "ArrowLeft": 0xFF51,
    "ArrowRight": 0xFF53,
    # Page keys
    "Home": 0xFF50,
    "End": 0xFF57,
    "PageUp": 0xFF55,
    "PageDown": 0xFF56,
    # Modifiers
    "Shift": 0xFFE1,    # XK_Shift_L
    "Control": 0xFFE3,  # XK_Control_L
    "Alt": 0xFFE9,      # XK_Alt_L
    "Meta": 0xFFEB,     # XK_Super_L
    # Letters — XK_a..XK_z are 0x61..0x7A (same as ASCII)
    **{chr(c): c for c in range(ord("a"), ord("z") + 1)},
    # Digits — XK_0..XK_9 are 0x30..0x39 (same as ASCII)
    **{str(d): 0x30 + d for d in range(10)},
}


# =====================================================================
# macOS — Quartz / CoreGraphics backend
# =====================================================================

class QuartzBackend:
    """Injects input events via macOS CoreGraphics (Quartz Event Services).

    Uses ``ctypes.CDLL`` to load CoreGraphics and CoreFoundation.
    Coordinates are in Quartz *points* (not pixels) — the caller must
    handle DPI scaling if needed.
    """

    # CG event type constants
    _kCGEventLeftMouseDown = 1
    _kCGEventLeftMouseUp = 2
    _kCGEventMouseMoved = 5
    _kCGEventKeyDown = 10
    _kCGEventKeyUp = 11

    # Event tap location
    _kCGHIDEventTap = 0

    def __init__(self) -> None:
        self._cg: ctypes.CDLL | None = None
        self._cf: ctypes.CDLL | None = None

    # -- lazy init ----------------------------------------------------

    def _ensure_loaded(self) -> None:
        if self._cg is not None:
            return

        cg_path = ctypes.util.find_library("CoreGraphics")
        cf_path = ctypes.util.find_library("CoreFoundation")
        if cg_path is None or cf_path is None:
            raise RuntimeError(
                "CoreGraphics or CoreFoundation not found — are you on macOS?"
            )

        self._cg = ctypes.CDLL(cg_path)
        self._cf = ctypes.CDLL(cf_path)

        # CGEventCreateMouseEvent(source, type, point, button) -> CGEventRef
        self._cg.CGEventCreateMouseEvent.argtypes = [
            ctypes.c_void_p,  # source (NULL = default)
            ctypes.c_uint32,  # mouseType
            CGPoint,          # mouseCursorPosition
            ctypes.c_uint32,  # mouseButton
        ]
        self._cg.CGEventCreateMouseEvent.restype = ctypes.c_void_p

        # CGEventPost(tap, event)
        self._cg.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
        self._cg.CGEventPost.restype = None

        # CGEventCreateKeyboardEvent(source, keycode, keyDown) -> CGEventRef
        self._cg.CGEventCreateKeyboardEvent.argtypes = [
            ctypes.c_void_p,  # source
            ctypes.c_uint16,  # virtualKey
            ctypes.c_bool,    # keyDown
        ]
        self._cg.CGEventCreateKeyboardEvent.restype = ctypes.c_void_p

        # CGEventKeyboardSetUnicodeString(event, length, unicodeString)
        self._cg.CGEventKeyboardSetUnicodeString.argtypes = [
            ctypes.c_void_p,   # event
            ctypes.c_ulong,    # stringLength
            ctypes.c_void_p,   # unicodeString (UniChar*)
        ]
        self._cg.CGEventKeyboardSetUnicodeString.restype = None

        # CFRelease(cf)
        self._cf.CFRelease.argtypes = [ctypes.c_void_p]
        self._cf.CFRelease.restype = None

    # -- helpers ------------------------------------------------------

    def _post_mouse(self, event_type: int, x: int, y: int) -> None:
        self._ensure_loaded()
        assert self._cg is not None and self._cf is not None
        point = CGPoint(float(x), float(y))
        event = self._cg.CGEventCreateMouseEvent(
            None, event_type, point, 0  # button 0 = left
        )
        if not event:
            raise RuntimeError("CGEventCreateMouseEvent returned NULL")
        try:
            self._cg.CGEventPost(self._kCGHIDEventTap, event)
        finally:
            self._cf.CFRelease(event)

    def _post_key(self, keycode: int, key_down: bool) -> None:
        self._ensure_loaded()
        assert self._cg is not None and self._cf is not None
        event = self._cg.CGEventCreateKeyboardEvent(None, keycode, key_down)
        if not event:
            raise RuntimeError("CGEventCreateKeyboardEvent returned NULL")
        try:
            self._cg.CGEventPost(self._kCGHIDEventTap, event)
        finally:
            self._cf.CFRelease(event)

    # -- public API ---------------------------------------------------

    def mouse_move(self, x: int, y: int) -> None:
        self._post_mouse(self._kCGEventMouseMoved, x, y)

    def mouse_down(self, x: int, y: int) -> None:
        self._post_mouse(self._kCGEventLeftMouseDown, x, y)

    def mouse_up(self, x: int, y: int) -> None:
        self._post_mouse(self._kCGEventLeftMouseUp, x, y)

    def key_down(self, keycode: int) -> None:
        self._post_key(keycode, True)

    def key_up(self, keycode: int) -> None:
        self._post_key(keycode, False)

    def type_char(self, char: str) -> None:
        """Type a single unicode character by creating a key-down event
        with the unicode string attached via CGEventKeyboardSetUnicodeString,
        followed by a corresponding key-up event.
        """
        self._ensure_loaded()
        assert self._cg is not None and self._cf is not None

        # Encode as UTF-16 (UniChar = uint16)
        buf = (ctypes.c_uint16 * 1)(ord(char))

        for key_down in (True, False):
            event = self._cg.CGEventCreateKeyboardEvent(None, 0, key_down)
            if not event:
                raise RuntimeError("CGEventCreateKeyboardEvent returned NULL")
            try:
                self._cg.CGEventKeyboardSetUnicodeString(
                    event, 1, ctypes.cast(buf, ctypes.c_void_p)
                )
                self._cg.CGEventPost(self._kCGHIDEventTap, event)
            finally:
                self._cf.CFRelease(event)


class CGPoint(ctypes.Structure):
    """CoreGraphics CGPoint — two CGFloat (double on 64-bit) fields."""

    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


# =====================================================================
# Windows — user32.dll backend
# =====================================================================

class Win32Backend:
    """Injects input events via Windows ``user32.dll`` / ``SendInput``.

    All ctypes structures are defined inline to avoid any win32 dependency.
    """

    # SendInput constants
    _INPUT_MOUSE = 0
    _INPUT_KEYBOARD = 1
    _MOUSEEVENTF_MOVE = 0x0001
    _MOUSEEVENTF_LEFTDOWN = 0x0002
    _MOUSEEVENTF_LEFTUP = 0x0004
    _MOUSEEVENTF_ABSOLUTE = 0x8000
    _KEYEVENTF_KEYUP = 0x0002
    _KEYEVENTF_UNICODE = 0x0004

    def __init__(self) -> None:
        self._user32: ctypes.WinDLL | None = None  # type: ignore[name-defined]

    # -- lazy init ----------------------------------------------------

    def _ensure_loaded(self) -> None:
        if self._user32 is not None:
            return

        self._user32 = ctypes.WinDLL("user32", use_last_error=True)  # type: ignore[attr-defined]

        # SetCursorPos(x, y) -> BOOL
        self._user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
        self._user32.SetCursorPos.restype = ctypes.c_int

        # SendInput(nInputs, pInputs, cbSize) -> UINT
        self._user32.SendInput.argtypes = [
            ctypes.c_uint,
            ctypes.c_void_p,
            ctypes.c_int,
        ]
        self._user32.SendInput.restype = ctypes.c_uint

        # GetSystemMetrics(nIndex) -> int  (for coordinate normalisation)
        self._user32.GetSystemMetrics.argtypes = [ctypes.c_int]
        self._user32.GetSystemMetrics.restype = ctypes.c_int

    # -- helpers ------------------------------------------------------

    def _screen_width(self) -> int:
        assert self._user32 is not None
        return self._user32.GetSystemMetrics(0)  # SM_CXSCREEN

    def _screen_height(self) -> int:
        assert self._user32 is not None
        return self._user32.GetSystemMetrics(1)  # SM_CYSCREEN

    def _send_mouse_input(self, flags: int, x: int = 0, y: int = 0) -> None:
        self._ensure_loaded()
        assert self._user32 is not None

        mi = _MOUSEINPUT()
        if flags & self._MOUSEEVENTF_ABSOLUTE:
            # Normalise to 0..65535 coordinate space
            mi.dx = int(x * 65535 / self._screen_width())
            mi.dy = int(y * 65535 / self._screen_height())
        else:
            mi.dx = x
            mi.dy = y
        mi.mouseData = 0
        mi.dwFlags = flags
        mi.time = 0
        mi.dwExtraInfo = 0

        inp = _INPUT()
        inp.type = self._INPUT_MOUSE
        inp.union.mi = mi

        self._user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))

    def _send_key_input(
        self, vk: int, flags: int, scan: int = 0
    ) -> None:
        self._ensure_loaded()
        assert self._user32 is not None

        ki = _KEYBDINPUT()
        ki.wVk = vk
        ki.wScan = scan
        ki.dwFlags = flags
        ki.time = 0
        ki.dwExtraInfo = 0

        inp = _INPUT()
        inp.type = self._INPUT_KEYBOARD
        inp.union.ki = ki

        self._user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))

    # -- public API ---------------------------------------------------

    def mouse_move(self, x: int, y: int) -> None:
        self._send_mouse_input(
            self._MOUSEEVENTF_MOVE | self._MOUSEEVENTF_ABSOLUTE, x, y
        )

    def mouse_down(self, x: int, y: int) -> None:
        # Move first, then press — matches real user behaviour
        self.mouse_move(x, y)
        self._send_mouse_input(self._MOUSEEVENTF_LEFTDOWN)

    def mouse_up(self, x: int, y: int) -> None:
        self.mouse_move(x, y)
        self._send_mouse_input(self._MOUSEEVENTF_LEFTUP)

    def key_down(self, keycode: int) -> None:
        self._send_key_input(keycode, 0)

    def key_up(self, keycode: int) -> None:
        self._send_key_input(keycode, self._KEYEVENTF_KEYUP)

    def type_char(self, char: str) -> None:
        """Type a single unicode character via KEYEVENTF_UNICODE.

        This sends a scan-code-based keystroke that Windows translates to
        the character directly, bypassing the keyboard layout.
        """
        self._ensure_loaded()
        assert self._user32 is not None
        code = ord(char)

        for flags in (self._KEYEVENTF_UNICODE, self._KEYEVENTF_UNICODE | self._KEYEVENTF_KEYUP):
            ki = _KEYBDINPUT()
            ki.wVk = 0
            ki.wScan = code
            ki.dwFlags = flags
            ki.time = 0
            ki.dwExtraInfo = 0

            inp = _INPUT()
            inp.type = self._INPUT_KEYBOARD
            inp.union.ki = ki
            self._user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))


# -- Win32 ctypes structures ------------------------------------------
# Defined at module level so the class bodies stay readable.  These are
# only instantiated on Windows (or when the backend is explicitly
# constructed).

class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", ctypes.c_ulong),
        ("wParamL", ctypes.c_ushort),
        ("wParamH", ctypes.c_ushort),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", _MOUSEINPUT),
        ("ki", _KEYBDINPUT),
        ("hi", _HARDWAREINPUT),
    ]


class _INPUT(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_ulong),
        ("union", _INPUT_UNION),
    ]


# =====================================================================
# Linux — X11 / Xtst backend
# =====================================================================

class X11Backend:
    """Injects input events via X11's XTest extension.

    Requires ``libX11`` and ``libXtst`` to be installed (they are present
    on virtually every X11 desktop).
    """

    def __init__(self) -> None:
        self._x11: ctypes.CDLL | None = None
        self._xtst: ctypes.CDLL | None = None
        self._display: ctypes.c_void_p | None = None

    # -- lazy init ----------------------------------------------------

    def _ensure_loaded(self) -> None:
        if self._x11 is not None:
            return

        x11_path = ctypes.util.find_library("X11")
        xtst_path = ctypes.util.find_library("Xtst")
        if x11_path is None or xtst_path is None:
            raise RuntimeError(
                "libX11 or libXtst not found — are you on an X11-based Linux desktop?"
            )

        self._x11 = ctypes.CDLL(x11_path)
        self._xtst = ctypes.CDLL(xtst_path)

        # XOpenDisplay(display_name) -> Display*
        self._x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        self._x11.XOpenDisplay.restype = ctypes.c_void_p

        # XFlush(display) -> int
        self._x11.XFlush.argtypes = [ctypes.c_void_p]
        self._x11.XFlush.restype = ctypes.c_int

        # XStringToKeysym(string) -> KeySym
        self._x11.XStringToKeysym.argtypes = [ctypes.c_char_p]
        self._x11.XStringToKeysym.restype = ctypes.c_ulong

        # XKeysymToKeycode(display, keysym) -> KeyCode
        self._x11.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        self._x11.XKeysymToKeycode.restype = ctypes.c_ubyte

        # XTestFakeMotionEvent(display, screen, x, y, delay) -> int
        self._xtst.XTestFakeMotionEvent.argtypes = [
            ctypes.c_void_p,  # display
            ctypes.c_int,     # screen (-1 = current)
            ctypes.c_int,     # x
            ctypes.c_int,     # y
            ctypes.c_ulong,   # delay (ms, 0 = immediate)
        ]
        self._xtst.XTestFakeMotionEvent.restype = ctypes.c_int

        # XTestFakeButtonEvent(display, button, is_press, delay) -> int
        self._xtst.XTestFakeButtonEvent.argtypes = [
            ctypes.c_void_p,  # display
            ctypes.c_uint,    # button (1 = left)
            ctypes.c_int,     # is_press (Bool)
            ctypes.c_ulong,   # delay
        ]
        self._xtst.XTestFakeButtonEvent.restype = ctypes.c_int

        # XTestFakeKeyEvent(display, keycode, is_press, delay) -> int
        self._xtst.XTestFakeKeyEvent.argtypes = [
            ctypes.c_void_p,  # display
            ctypes.c_uint,    # keycode
            ctypes.c_int,     # is_press (Bool)
            ctypes.c_ulong,   # delay
        ]
        self._xtst.XTestFakeKeyEvent.restype = ctypes.c_int

        # Open the default display
        self._display = self._x11.XOpenDisplay(None)
        if not self._display:
            raise RuntimeError("XOpenDisplay(None) failed — is DISPLAY set?")

    # -- helpers ------------------------------------------------------

    def _flush(self) -> None:
        assert self._x11 is not None and self._display is not None
        self._x11.XFlush(self._display)

    def _keysym_to_keycode(self, keysym: int) -> int:
        """Convert an X11 keysym to a hardware keycode for the current display."""
        assert self._x11 is not None and self._display is not None
        keycode = self._x11.XKeysymToKeycode(self._display, keysym)
        if keycode == 0:
            raise ValueError(f"No keycode found for keysym 0x{keysym:04X}")
        return keycode

    # -- public API ---------------------------------------------------

    def mouse_move(self, x: int, y: int) -> None:
        self._ensure_loaded()
        assert self._xtst is not None and self._display is not None
        self._xtst.XTestFakeMotionEvent(self._display, -1, x, y, 0)
        self._flush()

    def mouse_down(self, x: int, y: int) -> None:
        self.mouse_move(x, y)
        assert self._xtst is not None and self._display is not None
        self._xtst.XTestFakeButtonEvent(self._display, 1, True, 0)
        self._flush()

    def mouse_up(self, x: int, y: int) -> None:
        self.mouse_move(x, y)
        assert self._xtst is not None and self._display is not None
        self._xtst.XTestFakeButtonEvent(self._display, 1, False, 0)
        self._flush()

    def key_down(self, keycode: int) -> None:
        self._ensure_loaded()
        assert self._xtst is not None and self._display is not None
        self._xtst.XTestFakeKeyEvent(self._display, keycode, True, 0)
        self._flush()

    def key_up(self, keycode: int) -> None:
        self._ensure_loaded()
        assert self._xtst is not None and self._display is not None
        self._xtst.XTestFakeKeyEvent(self._display, keycode, False, 0)
        self._flush()

    def type_char(self, char: str) -> None:
        """Type a single unicode character by resolving its keysym and
        synthesising key-down + key-up via XTest.
        """
        self._ensure_loaded()
        assert self._x11 is not None

        # Try to get the keysym via XStringToKeysym first (works for
        # ASCII and named keys).  Fall back to the Unicode keysym
        # convention (0x01000000 + codepoint) for everything else.
        keysym = self._x11.XStringToKeysym(char.encode("ascii"))
        if keysym == 0:
            # X11 Unicode keysym: 0x01000000 | unicode codepoint
            keysym = 0x01000000 | ord(char)

        keycode = self._keysym_to_keycode(keysym)
        self.key_down(keycode)
        self.key_up(keycode)


# =====================================================================
# Convenience helpers
# =====================================================================

def get_keymap() -> Dict[str, int]:
    """Return the key-name-to-keycode mapping for the current platform.

    Key names match the Playwright convention used throughout this project
    (e.g. ``"Enter"``, ``"ArrowUp"``, ``"a"``).

    On Linux the values are X11 keysyms — call
    ``X11Backend._keysym_to_keycode`` to get hardware keycodes.
    """
    if sys.platform == "darwin":
        return dict(_MAC_KEYMAP)
    elif sys.platform == "win32":
        return dict(_WIN_KEYMAP)
    else:
        return dict(_LINUX_KEYSYM_MAP)


def create_backend() -> InputBackend:
    """Auto-select and return the right backend for the current OS."""
    if sys.platform == "darwin":
        return QuartzBackend()
    elif sys.platform == "win32":
        return Win32Backend()
    else:
        return X11Backend()
