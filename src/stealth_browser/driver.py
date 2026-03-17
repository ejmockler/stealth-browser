"""WebDriver management for stealth browsing."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

from stealth_browser.config import BrowserConfig, PlatformConfig, LocaleConfig
from stealth_browser.scripts import StealthScripts
from stealth_browser.exceptions import BrowserError

logger = logging.getLogger(__name__)


class DriverManager:
    """
    Manages Selenium WebDriver lifecycle with stealth configuration.

    Handles driver creation, configuration, and cleanup. Each instance
    manages a single driver - use multiple instances for parallel operations.
    """

    def __init__(
        self,
        output_dir: Optional[Path] = None,
        headless: bool = True,
        platform: Optional[str] = None,
        locale: Optional[LocaleConfig] = None,
        auto_detect_locale: bool = True,
        profile_dir: Optional[Path] = None,
    ):
        """
        Initialize DriverManager.

        Args:
            output_dir: Directory for downloads (default: temp dir)
            headless: Run browser in headless mode
            platform: Force platform ("windows", "macos") or None for random
            locale: Explicit locale config, or None for auto-detection
            auto_detect_locale: Detect locale from IP if locale not provided
            profile_dir: Directory for persistent browser profile (preserves cookies/sessions)
        """
        self.output_dir = Path(output_dir) if output_dir else Path.home() / ".cache" / "stealth-browser"
        self.headless = headless
        self.platform = platform
        self.locale = locale
        self.auto_detect_locale = auto_detect_locale
        self.profile_dir = Path(profile_dir) if profile_dir else None
        self._temp_profile_dir = None  # Will be set if using temp profile

        self._driver: Optional[webdriver.Chrome] = None
        self._config: Optional[PlatformConfig] = None

    @property
    def driver(self) -> webdriver.Chrome:
        """Get or create the WebDriver instance."""
        if self._driver is None:
            self._create_driver()
        return self._driver

    @property
    def config(self) -> PlatformConfig:
        """Get the current platform configuration."""
        if self._config is None:
            self._config = BrowserConfig.get_config(
                platform=self.platform,
                locale=self.locale,
                auto_detect_locale=self.auto_detect_locale,
            )
        return self._config

    def _create_driver(self) -> None:
        """Create and configure the WebDriver."""
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Clean up any orphaned temp profiles from previous crashed runs
        self._cleanup_stale_temp_profiles()

        chrome_options = Options()

        # Headless mode
        if self.headless:
            chrome_options.add_argument("--headless=new")

        # Use persistent profile if specified, otherwise create temp directory
        # This ensures complete isolation from any cached session state
        if self.profile_dir:
            self.profile_dir.mkdir(parents=True, exist_ok=True)
            chrome_options.add_argument(f"--user-data-dir={self.profile_dir}")
            logger.debug(f"Using persistent browser profile: {self.profile_dir}")
        else:
            import tempfile
            self._temp_profile_dir = tempfile.mkdtemp(prefix="stealth_browser_")
            chrome_options.add_argument(f"--user-data-dir={self._temp_profile_dir}")
            logger.debug(f"Using fresh temp browser profile: {self._temp_profile_dir}")

        # Core stealth options
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_argument("--disable-features=IsolateOrigins,site-per-process")
        chrome_options.add_argument("--disable-site-isolation-trials")
        chrome_options.add_argument("--disable-features=UserAgentClientHint")

        # Performance options
        chrome_options.add_argument("--disable-renderer-backgrounding")
        chrome_options.add_argument("--disable-backgrounding-occluded-windows")
        chrome_options.add_argument("--disable-background-timer-throttling")
        chrome_options.add_argument("--disable-hang-monitor")
        chrome_options.add_argument("--disable-ipc-flooding-protection")
        chrome_options.add_argument("--disable-popup-blocking")
        chrome_options.add_argument("--disable-prompt-on-repost")
        chrome_options.add_argument("--disable-sync")
        chrome_options.add_argument("--disable-translate")
        chrome_options.add_argument("--metrics-recording-only")
        chrome_options.add_argument("--no-first-run")
        chrome_options.add_argument("--password-store=basic")
        chrome_options.add_argument("--use-mock-keychain")

        # Apply platform config
        config = self.config
        chrome_options.add_argument(config.window_size)
        chrome_options.add_argument(f"user-agent={config.user_agent}")

        # Download and privacy preferences
        prefs = {
            "download.default_directory": str(self.output_dir.absolute()),
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "safebrowsing.enabled": True,
            "plugins.always_open_pdf_externally": True,
            "download.open_pdf_in_system_reader": False,
            "profile.default_content_settings.popups": 0,
            "profile.default_content_setting_values.notifications": 2,
            "profile.default_content_setting_values.geolocation": 2,
            "profile.managed_default_content_settings.images": 1,
            "profile.default_content_setting_values.cookies": 1,
            "profile.password_manager_enabled": False,
            "credentials_enable_service": False,
        }
        chrome_options.add_experimental_option("prefs", prefs)
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option("useAutomationExtension", False)

        try:
            driver = webdriver.Chrome(options=chrome_options)
            driver.set_page_load_timeout(60)
            driver.set_script_timeout(60)

            # Set download behavior via CDP
            driver.execute_cdp_cmd(
                "Page.setDownloadBehavior",
                {
                    "behavior": "allow",
                    "downloadPath": str(self.output_dir.absolute()),
                },
            )

            # Set user agent override
            driver.execute_cdp_cmd(
                "Network.setUserAgentOverride",
                {
                    "userAgent": config.user_agent,
                    "platform": config.platform,
                    "acceptLanguage": f"{config.locale.locale},en;q=0.9",
                    "platformVersion": config.platform_version,
                },
            )

            # Clear initial state only if not using persistent profile
            if not self.profile_dir:
                driver.execute_cdp_cmd("Network.clearBrowserCache", {})
                driver.execute_cdp_cmd("Network.clearBrowserCookies", {})
                # Clear all storage types for fresh session
                try:
                    driver.execute_cdp_cmd("Storage.clearDataForOrigin", {
                        "origin": "*",
                        "storageTypes": "all",
                    })
                except Exception:
                    pass  # May fail on some Chrome versions

            # Inject stealth scripts
            driver.execute_script(StealthScripts.get_stealth_scripts(config))

            # Start with blank page
            driver.get("about:blank")

            self._driver = driver
            logger.debug(f"Created stealth driver with platform: {config.platform_key}")

        except Exception as e:
            logger.error(f"Failed to create WebDriver: {e}")
            raise BrowserError(f"Failed to create browser: {e}") from e

    @staticmethod
    def _cleanup_stale_temp_profiles() -> None:
        """Remove leftover temp profile directories from previous crashed runs."""
        import glob
        import shutil
        import os

        for path in glob.glob("/tmp/stealth_browser_*"):
            try:
                # Check if it has a lock file indicating active use
                lock_file = os.path.join(path, "SingletonLock")
                if os.path.exists(lock_file):
                    # Check if the PID in the lock is still alive
                    try:
                        link_target = os.readlink(lock_file)
                        pid = int(link_target.split("-")[0])
                        os.kill(pid, 0)  # Check if process exists
                        continue  # Process alive, skip this profile
                    except (ValueError, OSError, ProcessLookupError):
                        pass  # Process dead, safe to clean up

                shutil.rmtree(path, ignore_errors=True)
                logger.debug(f"Cleaned up stale temp profile: {path}")
            except Exception:
                pass

    def clear_state(self) -> None:
        """Clear ALL browser state: cache, cookies, storage, and navigate to blank page."""
        if self._driver is None:
            return

        try:
            # Clear network cache and cookies
            self._driver.execute_cdp_cmd("Network.clearBrowserCache", {})
            self._driver.execute_cdp_cmd("Network.clearBrowserCookies", {})

            # Clear all storage types (Local Storage, Session Storage, IndexedDB, etc.)
            # This is critical for preventing Microsoft SSO session persistence
            self._driver.execute_cdp_cmd("Storage.clearDataForOrigin", {
                "origin": "*",
                "storageTypes": "all",
            })

            # Navigate to blank page
            self._driver.get("about:blank")

            # Also clear via JavaScript for any remaining storage
            self._driver.execute_script("""
                try { localStorage.clear(); } catch(e) {}
                try { sessionStorage.clear(); } catch(e) {}
                try {
                    indexedDB.databases().then(dbs => {
                        dbs.forEach(db => indexedDB.deleteDatabase(db.name));
                    });
                } catch(e) {}
            """)

            logger.debug("Browser state cleared (cache, cookies, all storage)")
        except Exception as e:
            logger.debug(f"Error clearing browser state: {e}")

    def close_extra_tabs(self) -> None:
        """Close all tabs except the first one."""
        if self._driver is None:
            return

        try:
            original_window = self._driver.window_handles[0]
            for handle in self._driver.window_handles[1:]:
                self._driver.switch_to.window(handle)
                self._driver.close()
            self._driver.switch_to.window(original_window)
        except Exception as e:
            logger.debug(f"Error closing extra tabs: {e}")

    def refresh(self) -> None:
        """Close and recreate the driver with new fingerprint."""
        self.close()
        self._config = None  # Get new random config
        self._create_driver()

    def close(self) -> None:
        """Close the WebDriver and release resources."""
        if self._driver is not None:
            try:
                self._driver.quit()
            except Exception as e:
                logger.debug(f"Error closing driver: {e}")
            finally:
                self._driver = None

        # Clean up temp profile directory if we created one
        if self._temp_profile_dir:
            import shutil
            try:
                shutil.rmtree(self._temp_profile_dir, ignore_errors=True)
                logger.debug(f"Cleaned up temp profile: {self._temp_profile_dir}")
            except Exception as e:
                logger.debug(f"Error cleaning temp profile: {e}")
            finally:
                self._temp_profile_dir = None

    def __enter__(self) -> "DriverManager":
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        """Context manager exit - ensures driver is closed."""
        self.close()
        return False
