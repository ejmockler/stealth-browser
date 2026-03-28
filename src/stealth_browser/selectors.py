"""Selector and key translation between Selenium and Playwright conventions."""

from __future__ import annotations

from typing import Dict

# ---------------------------------------------------------------------------
# Selector-strategy constants (mirror Selenium's By.* string values so
# consumers can ``from stealth_browser.selectors import XPATH`` without
# pulling in Selenium).
# ---------------------------------------------------------------------------
CSS_SELECTOR = "css selector"
XPATH = "xpath"
ID = "id"
CLASS_NAME = "class name"
NAME = "name"
TAG_NAME = "tag name"
LINK_TEXT = "link text"
PARTIAL_LINK_TEXT = "partial link text"


def translate_selector(selector: str, by: str = CSS_SELECTOR) -> str:
    """Convert a Selenium *(by, selector)* pair to a Playwright selector string.

    Playwright's default locator strategy is CSS, so CSS selectors pass
    through unchanged.  Other strategies get the appropriate prefix or
    are rewritten into CSS equivalents.
    """
    if by == CSS_SELECTOR:
        return selector
    if by == XPATH:
        return f"xpath={selector}"
    if by == ID:
        return f"#{selector}"
    if by == CLASS_NAME:
        return f".{selector}"
    if by == NAME:
        return f'[name="{selector}"]'
    if by == TAG_NAME:
        return selector  # Playwright accepts bare tag names
    if by == LINK_TEXT:
        # Playwright: quoted string → exact match
        return f'text="{selector}"'
    if by == PARTIAL_LINK_TEXT:
        # Playwright: unquoted string → substring match
        return f"text={selector}"
    raise ValueError(f"Unknown selector strategy: {by!r}")


# ---------------------------------------------------------------------------
# Key translation — Selenium's Keys.* are Unicode PUA code-points; Playwright
# uses human-readable string names.
# ---------------------------------------------------------------------------

# Selenium Keys values (unicode PUA chars) → Playwright key names.
# Only the keys actually used by real consumers are included; extend as needed.
_SELENIUM_KEY_MAP: Dict[str, str] = {
    "\ue004": "Tab",
    "\ue006": "Enter",
    "\ue007": "Enter",       # Keys.RETURN
    "\ue00d": "Space",
    "\ue003": "Backspace",
    "\ue017": "Delete",
    "\ue00c": "Escape",
    "\ue012": "ArrowLeft",
    "\ue013": "ArrowUp",
    "\ue014": "ArrowRight",
    "\ue015": "ArrowDown",
    "\ue010": "End",
    "\ue011": "Home",
    "\ue016": "Insert",
    "\ue002": "F1",  # Actually Help but mapped for compat
    "\ue031": "F1",
    "\ue032": "F2",
    "\ue033": "F3",
    "\ue034": "F4",
    "\ue035": "F5",
    "\ue036": "F6",
    "\ue037": "F7",
    "\ue038": "F8",
    "\ue039": "F9",
    "\ue03a": "F10",
    "\ue03b": "F11",
    "\ue03c": "F12",
    "\ue009": "Control",
    "\ue008": "Shift",
    "\ue00a": "Alt",
    "\ue03d": "Meta",
}


def translate_key(key: str) -> str:
    """Convert a Selenium ``Keys.*`` value to a Playwright key name.

    If *key* is already a Playwright-style string (e.g. ``"Enter"``), it is
    returned unchanged.
    """
    return _SELENIUM_KEY_MAP.get(key, key)
