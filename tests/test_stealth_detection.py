"""Stealth detection benchmark — visits bot detection test pages and produces a report.

Runs against the Wave 0 test matrix (10 pages, priority order).
Produces a timestamped JSON report + screenshots for manual review.

Usage:
    python -m pytest tests/test_stealth_detection.py -v -s
    # Or directly:
    python tests/test_stealth_detection.py [--headed] [--platform windows|macos]
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

# Add package to path for direct execution
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from stealth_browser.patchright import (
    create_stealth_browser,
    create_stealth_context,
    new_stealth_page,
    stealth_goto,
    close_stealth_browser,
)


REPORT_DIR = Path(__file__).parent.parent / "reports"

# Test pages in priority order (from wave-0-plan.md)
TEST_PAGES = [
    {
        "name": "rebrowser",
        "url": "https://bot-detector.rebrowser.net/",
        "timeout": 10_000,
        "wait_for": "rebrowser-bot-detector",
    },
    {
        "name": "deviceandbrowserinfo",
        "url": "https://deviceandbrowserinfo.com/are_you_a_bot",
        "timeout": 15_000,
        "wait_for": None,
    },
    {
        "name": "sannysoft",
        "url": "https://bot.sannysoft.com/",
        "timeout": 10_000,
        "wait_for": None,
    },
    {
        "name": "creepjs",
        "url": "https://abrahamjuliot.github.io/creepjs/",
        "timeout": 45_000,
        "wait_for": "fingerprint-data",
    },
    {
        "name": "browserscan_bot",
        "url": "https://www.browserscan.net/bot-detection",
        "timeout": 15_000,
        "wait_for": None,
    },
    {
        "name": "browserscan_tls",
        "url": "https://www.browserscan.net/tls",
        "timeout": 15_000,
        "wait_for": None,
    },
    {
        "name": "pixelscan",
        "url": "https://pixelscan.net/bot-check",
        "timeout": 15_000,
        "wait_for": None,
    },
    {
        "name": "incolumitas",
        "url": "https://bot.incolumitas.com/",
        "timeout": 20_000,
        "wait_for": None,
    },
    {
        "name": "fingerprint_botd",
        "url": "https://demo.fingerprint.com/bot-firewall",
        "timeout": 15_000,
        "wait_for": None,
    },
    {
        "name": "cloudflare_turnstile",
        "url": "https://seleniumbase.io/apps/turnstile",
        "timeout": 20_000,
        "wait_for": None,
    },
]

# Extraction scripts per test page — returns structured results
EXTRACTORS: Dict[str, str] = {
    "rebrowser": """
        () => {
            const results = {};
            const rows = document.querySelectorAll('tr');
            for (const row of rows) {
                const cells = row.querySelectorAll('td');
                if (cells.length >= 2) {
                    const name = cells[0]?.textContent?.trim();
                    const value = cells[1]?.textContent?.trim();
                    if (name) results[name] = value;
                }
            }
            // Also try to get structured data if available
            if (window.__BOT_DETECTION_RESULTS__) {
                results._raw = window.__BOT_DETECTION_RESULTS__;
            }
            return results;
        }
    """,
    "sannysoft": """
        () => {
            const results = {};
            const rows = document.querySelectorAll('table tr');
            for (const row of rows) {
                const cells = row.querySelectorAll('td');
                if (cells.length >= 2) {
                    const name = cells[0]?.textContent?.trim();
                    const value = cells[1]?.textContent?.trim();
                    const passed = !cells[1]?.classList?.contains('failed') &&
                                   !cells[1]?.style?.color?.includes('red');
                    if (name) results[name] = { value, passed };
                }
            }
            return results;
        }
    """,
    "deviceandbrowserinfo": """
        () => {
            const results = {};
            // Try to find the JSON results on the page
            const pre = document.querySelector('pre');
            if (pre) {
                try { return JSON.parse(pre.textContent); } catch {}
            }
            // Fallback: parse visible results
            const items = document.querySelectorAll('[class*="result"], [class*="test"], li');
            for (const item of items) {
                const text = item.textContent?.trim();
                if (text) {
                    const isBot = text.toLowerCase().includes('true') ||
                                  text.toLowerCase().includes('bot') ||
                                  text.toLowerCase().includes('detected');
                    results[text.substring(0, 80)] = { detected: isBot };
                }
            }
            return results;
        }
    """,
    "creepjs": """
        () => {
            const results = {};
            // Trust score
            const trustEl = document.querySelector('[class*="trust"], [id*="trust"]');
            if (trustEl) results.trust_score = trustEl.textContent?.trim();

            // Headless percentage
            const headlessEl = document.querySelector('[class*="headless"]');
            if (headlessEl) results.headless = headlessEl.textContent?.trim();

            // Lies count
            const liesEl = document.querySelector('[class*="lies"]');
            if (liesEl) results.lies = liesEl.textContent?.trim();

            // Get all fingerprint sections
            const sections = document.querySelectorAll('div[class], section');
            for (const section of sections) {
                const header = section.querySelector('h1, h2, h3, h4, .header');
                if (header) {
                    const name = header.textContent?.trim()?.substring(0, 40);
                    const content = section.textContent?.trim()?.substring(0, 200);
                    if (name) results[name] = content;
                }
            }
            return results;
        }
    """,
}


async def run_test_page(
    context: Any,
    test: Dict[str, Any],
    report_dir: Path,
) -> Dict[str, Any]:
    """Visit a test page, extract results, take screenshot."""
    name = test["name"]
    result: Dict[str, Any] = {
        "name": name,
        "url": test["url"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "unknown",
        "error": None,
        "results": {},
        "duration_ms": 0,
    }

    start = time.monotonic()
    page = await new_stealth_page(context)

    try:
        # Navigate + inject stealth scripts
        await stealth_goto(page, test["url"], wait_until="domcontentloaded", timeout=test["timeout"])

        # Wait for results to render
        if test.get("wait_for"):
            try:
                await page.wait_for_selector(
                    f'[class*="{test["wait_for"]}"], [id*="{test["wait_for"]}"]',
                    timeout=test["timeout"],
                )
            except Exception:
                pass  # Proceed anyway — partial results are useful

        # Give dynamic content time to render
        await page.wait_for_timeout(3000)

        # Take screenshot
        screenshot_path = report_dir / f"{name}.png"
        await page.screenshot(path=str(screenshot_path), full_page=True)

        # Extract results
        extractor = EXTRACTORS.get(name)
        if extractor:
            try:
                result["results"] = await page.evaluate(extractor)
            except Exception as e:
                result["results"] = {"extraction_error": str(e)}

        # Also grab basic page text for manual review
        try:
            body_text = await page.evaluate("() => document.body?.innerText?.substring(0, 5000)")
            result["page_text"] = body_text
        except Exception:
            pass

        result["status"] = "completed"

    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)
        # Try to screenshot even on error
        try:
            screenshot_path = report_dir / f"{name}_error.png"
            await page.screenshot(path=str(screenshot_path))
        except Exception:
            pass

    finally:
        result["duration_ms"] = int((time.monotonic() - start) * 1000)
        await page.close()

    return result


async def run_benchmark(
    headless: bool = True,
    platform: Optional[str] = None,
    pages: Optional[list] = None,
) -> Dict[str, Any]:
    """Run the full detection benchmark suite.

    Returns a report dict with results per test page.
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = REPORT_DIR / timestamp
    report_dir.mkdir(parents=True, exist_ok=True)

    report: Dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "headless": headless,
        "platform": platform,
        "config": {},
        "results": [],
    }

    browser = await create_stealth_browser(
        headless=headless,
        platform=platform,
    )

    try:
        config = getattr(browser, "_stealth_config", None)
        if config:
            report["config"] = {
                "platform": config.platform,
                "platform_key": config.platform_key,
                "user_agent": config.user_agent,
                "viewport": f"{config.viewport_width}x{config.viewport_height}",
                "gpu": config.hardware.gpu,
                "cores": config.hardware.cores,
                "memory": config.hardware.memory,
                "timezone": config.locale.timezone,
            }

        context = await create_stealth_context(browser, config)

        test_pages = pages or TEST_PAGES
        for test in test_pages:
            print(f"  Testing {test['name']}...", end=" ", flush=True)
            result = await run_test_page(context, test, report_dir)
            report["results"].append(result)
            status = "OK" if result["status"] == "completed" else result["status"]
            print(f"{status} ({result['duration_ms']}ms)")

        await context.close()

    finally:
        await close_stealth_browser(browser)

    # Write report
    report_path = report_dir / "report.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, default=str)

    print(f"\nReport: {report_path}")
    print(f"Screenshots: {report_dir}/")

    return report


def print_summary(report: Dict[str, Any]) -> None:
    """Print a human-readable summary of test results."""
    print("\n" + "=" * 60)
    print("STEALTH DETECTION BENCHMARK RESULTS")
    print("=" * 60)
    print(f"Platform: {report['config'].get('platform_key', '?')}")
    print(f"UA: {report['config'].get('user_agent', '?')[:70]}...")
    print(f"GPU: {report['config'].get('gpu', '?')}")
    print(f"Headless: {report['headless']}")
    print("-" * 60)

    for result in report["results"]:
        status_icon = "PASS" if result["status"] == "completed" else "FAIL"
        print(f"  [{status_icon}] {result['name']:25s} {result['duration_ms']:6d}ms")
        if result.get("error"):
            print(f"         Error: {result['error'][:80]}")

    print("=" * 60)


async def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Stealth detection benchmark")
    parser.add_argument("--headed", action="store_true", help="Run in headed mode")
    parser.add_argument("--platform", choices=["windows", "macos"], help="Force platform")
    parser.add_argument(
        "--pages", nargs="*",
        help="Specific page names to test (default: all)",
    )
    args = parser.parse_args()

    headless = not args.headed

    pages = None
    if args.pages:
        pages = [t for t in TEST_PAGES if t["name"] in args.pages]
        if not pages:
            print(f"No matching pages. Available: {[t['name'] for t in TEST_PAGES]}")
            sys.exit(1)

    print(f"Running stealth benchmark ({'headed' if not headless else 'headless'})...")
    report = await run_benchmark(headless=headless, platform=args.platform, pages=pages)
    print_summary(report)


if __name__ == "__main__":
    asyncio.run(main())
