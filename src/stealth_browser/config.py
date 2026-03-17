"""Browser configuration with platform-specific fingerprints."""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo


@dataclass
class HardwareConfig:
    """Hardware fingerprint configuration."""

    cores: int
    memory: int
    gpu: str


@dataclass
class NetworkConfig:
    """Network simulation configuration."""

    downlink: int
    rtt: int


@dataclass
class LocaleConfig:
    """Locale and timezone configuration based on IP geolocation."""

    timezone: str
    timezone_offset: int  # Minutes, positive = west of UTC (JS convention)
    locale: str
    languages: List[str]

    @classmethod
    def default(cls) -> "LocaleConfig":
        """Return default US locale with EST offset."""
        return cls(
            timezone="America/New_York",
            timezone_offset=300,  # EST (UTC-5)
            locale="en-US",
            languages=["en-US", "en"],
        )

    @classmethod
    def for_timezone(cls, timezone: str, locale: str = "en-US") -> "LocaleConfig":
        """Create locale config for a specific timezone."""
        try:
            tz = ZoneInfo(timezone)
            now = datetime.now(tz)
            offset = now.utcoffset()
            offset_minutes = int(offset.total_seconds() / 60) if offset else 0
            # JS convention: positive = west of UTC
            js_offset = -offset_minutes

            return cls(
                timezone=timezone,
                timezone_offset=js_offset,
                locale=locale,
                languages=[locale, locale.split("-")[0]] if "-" in locale else [locale, "en"],
            )
        except Exception:
            return cls.default()

    @classmethod
    def california(cls) -> "LocaleConfig":
        """Return California (Pacific Time) locale."""
        return cls.for_timezone("America/Los_Angeles", "en-US")


@dataclass
class PlatformConfig:
    """Complete platform configuration."""

    platform: str
    platform_key: str
    user_agent: str
    window_size: str
    viewport_width: int
    viewport_height: int
    platform_version: str
    hardware: HardwareConfig
    network: NetworkConfig
    locale: LocaleConfig
    touch_enabled: bool = False


class BrowserConfig:
    """Platform-specific browser configurations for stealth browsing."""

    PLATFORMS: Dict[str, Dict[str, Any]] = {
        "windows": {
            "user_agents": [
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) "
                "Gecko/20100101 Firefox/122.0",
            ],
            "platform": "Win32",
            "window_size": "--window-size=1920,1080",
            "viewport": (1920, 1080),
            "touch_enabled": False,
            "platform_version": "10.0",
            "hardware": {
                "cores": list(range(4, 17)),  # 4-16 cores
                "memory": list(range(8, 33)),  # 8-32 GB
                "gpu": [
                    "ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0)",
                    "ANGLE (Intel, Intel(R) UHD Graphics Direct3D11 vs_5_0 ps_5_0)",
                    "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0)",
                    "ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 Direct3D11 vs_5_0 ps_5_0)",
                    "ANGLE (AMD, AMD Radeon RX 580 Direct3D11 vs_5_0 ps_5_0)",
                ],
            },
            "network": {
                "downlink": list(range(5, 101)),  # 5-100 Mbps
                "rtt": list(range(20, 101)),  # 20-100ms
            },
        },
        "macos": {
            "user_agents": [
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
                "(KHTML, like Gecko) Version/17.2 Safari/605.1.15",
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:122.0) "
                "Gecko/20100101 Firefox/122.0",
            ],
            "platform": "MacIntel",
            "window_size": "--window-size=1680,1050",
            "viewport": (1680, 1050),
            "touch_enabled": False,
            "platform_version": "10_15_7",
            "hardware": {
                "cores": list(range(4, 17)),  # 4-16 cores (M1/M2 have many)
                "memory": list(range(8, 65)),  # 8-64 GB
                "gpu": [
                    "ANGLE (Apple, Apple M1)",
                    "ANGLE (Apple, Apple M1 Pro)",
                    "ANGLE (Apple, Apple M2)",
                    "ANGLE (Apple, Apple M3)",
                    "ANGLE (Intel, Intel(R) Iris(TM) Plus Graphics 655)",
                ],
            },
            "network": {
                "downlink": list(range(10, 151)),  # 10-150 Mbps
                "rtt": list(range(10, 51)),  # 10-50ms
            },
        },
    }

    @classmethod
    def get_config(
        cls,
        platform: Optional[str] = None,
        locale: Optional[LocaleConfig] = None,
        auto_detect_locale: bool = True,
    ) -> PlatformConfig:
        """
        Get a platform configuration with randomized fingerprint values.

        Args:
            platform: Specific platform ("windows", "macos") or None for random
            locale: Explicit LocaleConfig, or None for auto-detection
            auto_detect_locale: If True and locale is None, detect from external IP

        Returns:
            PlatformConfig with randomized fingerprint values and locale
        """
        if platform is None:
            platform_key = random.choice(list(cls.PLATFORMS.keys()))
        else:
            platform_key = platform.lower()
            if platform_key not in cls.PLATFORMS:
                raise ValueError(
                    f"Unknown platform: {platform}. "
                    f"Valid options: {list(cls.PLATFORMS.keys())}"
                )

        config = cls.PLATFORMS[platform_key]

        hardware = HardwareConfig(
            cores=random.choice(config["hardware"]["cores"]),
            memory=random.choice(config["hardware"]["memory"]),
            gpu=random.choice(config["hardware"]["gpu"]),
        )

        network = NetworkConfig(
            downlink=random.choice(config["network"]["downlink"]),
            rtt=random.choice(config["network"]["rtt"]),
        )

        # Determine locale configuration
        if locale is not None:
            locale_config = locale
        elif auto_detect_locale:
            locale_config = cls._detect_locale_from_ip()
        else:
            locale_config = LocaleConfig.default()

        viewport = config["viewport"]

        return PlatformConfig(
            platform=config["platform"],
            platform_key=platform_key,
            user_agent=random.choice(config["user_agents"]),
            window_size=config["window_size"],
            viewport_width=viewport[0],
            viewport_height=viewport[1],
            platform_version=config["platform_version"],
            hardware=hardware,
            network=network,
            locale=locale_config,
            touch_enabled=config["touch_enabled"],
        )

    @classmethod
    def _detect_locale_from_ip(cls) -> LocaleConfig:
        """Detect locale configuration from external IP geolocation."""
        try:
            from stealth_browser.geolocation import get_geolocation

            geo = get_geolocation(timeout=5.0)
            return LocaleConfig(
                timezone=geo.timezone,
                timezone_offset=geo.timezone_offset,
                locale=geo.locale,
                languages=geo.languages,
            )
        except Exception:
            return LocaleConfig.default()

    @classmethod
    def get_random_platform(cls) -> PlatformConfig:
        """Get a random platform configuration (alias for get_config(None))."""
        return cls.get_config(None)

    @classmethod
    def available_platforms(cls) -> List[str]:
        """Get list of available platform names."""
        return list(cls.PLATFORMS.keys())
