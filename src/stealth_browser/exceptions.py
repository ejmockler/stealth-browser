"""Exceptions for stealth browser operations."""


class BrowserError(Exception):
    """Base exception for browser errors."""
    pass


class NavigationError(BrowserError):
    """Error during page navigation."""
    pass


class ElementNotFoundError(BrowserError):
    """Element not found on page."""
    pass


class AuthenticationError(BrowserError):
    """Authentication failed."""
    pass


class TimeoutError(BrowserError):
    """Operation timed out."""
    pass
