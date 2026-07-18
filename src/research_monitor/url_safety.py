"""Shared strict parsing for externally stored HTTP(S) locators."""

from __future__ import annotations

from urllib.parse import SplitResult, urlsplit


def parse_http_url(locator: str) -> SplitResult:
    """Parse an absolute HTTP(S) URL and force lazy host/port validation."""
    if not locator or any(ord(character) < 0x21 or ord(character) == 0x7F for character in locator):
        raise ValueError("URL contains whitespace or control characters")
    try:
        parsed = urlsplit(locator)
        hostname = parsed.hostname
        _port = parsed.port
    except ValueError as exc:
        raise ValueError("URL authority is malformed") from exc
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc or not hostname:
        raise ValueError("URL must be an absolute HTTP(S) URL")
    return parsed

