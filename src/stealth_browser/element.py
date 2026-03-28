"""Engine-agnostic element wrapper."""

from __future__ import annotations

from typing import Any, Optional


class StealthElement:
    """Thin wrapper around a Selenium ``WebElement`` or Playwright ``ElementHandle``.

    Provides a uniform read-only interface for the attributes that
    ``StealthBrowser`` consumers typically access.  The underlying engine
    object is always available via :pyattr:`native` for power users.
    """

    __slots__ = ("_obj", "_engine_type")

    def __init__(self, obj: Any, engine_type: str) -> None:
        self._obj = obj
        self._engine_type = engine_type  # "selenium" | "patchright" | "native"

    # ------------------------------------------------------------------
    # Escape hatch
    # ------------------------------------------------------------------

    @property
    def native(self) -> Any:
        """Return the underlying engine object (WebElement or ElementHandle)."""
        return self._obj

    # ------------------------------------------------------------------
    # Uniform API
    # ------------------------------------------------------------------

    @property
    def text(self) -> str:
        if self._engine_type in ("selenium", "native"):
            return self._obj.text
        # Playwright ElementHandle
        return self._obj.text_content() or ""

    @property
    def tag_name(self) -> str:
        if self._engine_type in ("selenium", "native"):
            return self._obj.tag_name
        return self._obj.evaluate("el => el.tagName.toLowerCase()")

    def is_displayed(self) -> bool:
        if self._engine_type in ("selenium", "native"):
            return self._obj.is_displayed()
        return self._obj.is_visible()

    def get_attribute(self, name: str) -> Optional[str]:
        return self._obj.get_attribute(name)

    @property
    def value(self) -> str:
        return self.get_attribute("value") or ""

    # ------------------------------------------------------------------
    # Dunder helpers so the wrapper behaves naturally in common patterns.
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return f"<StealthElement engine={self._engine_type!r}>"

    def __bool__(self) -> bool:
        return self._obj is not None
