"""Geolocation utilities for locale detection."""

from __future__ import annotations

import urllib.request
import json
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional
from zoneinfo import ZoneInfo


@dataclass
class GeoLocation:
    """Geolocation result from IP lookup."""

    ip: str
    country: str
    region: str
    city: str
    timezone: str
    timezone_offset: int  # Minutes, JS convention (positive = west)
    locale: str
    languages: List[str]


def get_external_ip(timeout: float = 5.0) -> str:
    """Get external IP address."""
    services = [
        "https://api.ipify.org",
        "https://ifconfig.me/ip",
        "https://icanhazip.com",
    ]

    for service in services:
        try:
            req = urllib.request.Request(service, headers={"User-Agent": "curl/7.68.0"})
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return response.read().decode().strip()
        except Exception:
            continue

    raise RuntimeError("Could not determine external IP")


def get_geolocation(timeout: float = 5.0) -> GeoLocation:
    """Get geolocation information from external IP.

    Uses ipinfo.io for geolocation lookup.
    """
    try:
        ip = get_external_ip(timeout)

        req = urllib.request.Request(
            f"https://ipinfo.io/{ip}/json",
            headers={"User-Agent": "curl/7.68.0"},
        )

        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = json.loads(response.read().decode())

        timezone = data.get("timezone", "America/New_York")
        country = data.get("country", "US")

        # Calculate timezone offset
        tz = ZoneInfo(timezone)
        now = datetime.now(tz)
        offset = now.utcoffset()
        offset_minutes = int(offset.total_seconds() / 60) if offset else 0
        js_offset = -offset_minutes  # JS convention

        # Determine locale and languages
        locale, languages = get_locale_for_country(country)

        return GeoLocation(
            ip=ip,
            country=country,
            region=data.get("region", ""),
            city=data.get("city", ""),
            timezone=timezone,
            timezone_offset=js_offset,
            locale=locale,
            languages=languages,
        )

    except Exception as e:
        # Return defaults on failure
        return GeoLocation(
            ip="unknown",
            country="US",
            region="",
            city="",
            timezone="America/New_York",
            timezone_offset=300,  # EST
            locale="en-US",
            languages=["en-US", "en"],
        )


def get_locale_for_country(country_code: str) -> tuple[str, List[str]]:
    """Get locale and language list for a country code."""
    # Common country -> locale mappings
    country_locales = {
        "US": ("en-US", ["en-US", "en"]),
        "GB": ("en-GB", ["en-GB", "en"]),
        "CA": ("en-CA", ["en-CA", "en", "fr-CA"]),
        "AU": ("en-AU", ["en-AU", "en"]),
        "DE": ("de-DE", ["de-DE", "de", "en"]),
        "FR": ("fr-FR", ["fr-FR", "fr", "en"]),
        "ES": ("es-ES", ["es-ES", "es", "en"]),
        "IT": ("it-IT", ["it-IT", "it", "en"]),
        "JP": ("ja-JP", ["ja-JP", "ja", "en"]),
        "CN": ("zh-CN", ["zh-CN", "zh", "en"]),
        "KR": ("ko-KR", ["ko-KR", "ko", "en"]),
        "BR": ("pt-BR", ["pt-BR", "pt", "en"]),
        "MX": ("es-MX", ["es-MX", "es", "en"]),
        "IN": ("en-IN", ["en-IN", "hi-IN", "en"]),
    }

    if country_code in country_locales:
        return country_locales[country_code]

    # Default to en-US
    return ("en-US", ["en-US", "en"])
