"""Skyscanner browser automation, anti-bot challenge handling, and DOM extraction.

Provides persistent stealth browser launching (Patchright / Playwright),
session resetting, anti-bot verification waiting, and DOM card extraction.
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.async_api import BrowserContext, Page, Playwright

from src.skyscanner_parse import is_challenge_page, money_values

SKYSCANNER_COOKIES = [
    {"name": "ssculture", "value": "locale:::fi-FI&market:::FI&currency:::EUR", "domain": ".skyscanner.net", "path": "/"},
    {"name": "ssculture", "value": "locale:::fi-FI&market:::FI&currency:::EUR", "domain": ".skyscanner.fi", "path": "/"},
]


async def launch_skyscanner_context(
    playwright: Playwright,
    profile_dir: Path | str,
    *,
    executable_path: str | None = None,
    locale: str = "en-GB",
    viewport: dict[str, int] | None = None,
) -> BrowserContext:
    """Launch persistent browser context tuned for Skyscanner anti-bot resistance."""
    context = await playwright.chromium.launch_persistent_context(
        str(profile_dir),
        headless=False,
        executable_path=executable_path,
        locale=locale,
        viewport=viewport or {"width": 1440, "height": 1000},
        ignore_default_args=["--enable-automation"],
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-infobars",
        ],
    )
    try:
        await context.add_cookies(SKYSCANNER_COOKIES)
    except Exception:
        pass
    return context


async def reset_session(page: Page, home_url: str = "https://www.skyscanner.fi/") -> None:
    """Clear cookies/tokens and warm up on homepage to reset anti-bot challenges."""
    try:
        await page.context.clear_cookies()
        await page.context.add_cookies(SKYSCANNER_COOKIES)
        await page.goto(home_url, wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_timeout(1000)
        for btn_name in ("Reject all", "Hylkää kaikki", "Decline all", "Accept all", "Hyväksy kaikki"):
            btn = page.get_by_role("button", name=btn_name, exact=False)
            if await btn.count() > 0 and await btn.first.is_visible():
                await btn.first.click(timeout=2000)
                await page.wait_for_timeout(500)
                break
    except Exception:
        pass


async def extract_dom_candidate_cards(page: Page, min_fare: float = 0.0) -> list[str]:
    """Extract visible flight cards from the rendered Skyscanner DOM."""
    locators = page.locator(
        "div[class*='Ticket'], div[class*='Card'], [aria-label*='Flight option'], "
        "[aria-label*='Lentovaihtoehto'], [data-testid='flight-card'], "
        "[data-testid='itinerary-card'], [data-testid*='itinerary']"
    )
    count = await locators.count()
    cards: list[str] = []
    seen: set[str] = set()
    flight_indicators = (
        "stop", "vaihto", "vaihtoa", "välilasku", "suora", "direct",
        "min", "hr", "tuntia", "tunti", "hel", "han", "lentovaihtoehto", "flight option"
    )
    for i in range(min(count, 40)):
        try:
            txt = await locators.nth(i).inner_text()
            compact = " ".join(txt.split())
            if len(compact) < 20:
                continue
            lower = compact.lower()
            if not any(term in lower for term in flight_indicators):
                continue
            prices = [p for p in money_values(compact) if p > min_fare]
            if prices:
                key = compact[:100].lower()
                if key not in seen:
                    seen.add(key)
                    cards.append(compact[:3000])
        except Exception:
            continue
    return cards


async def wait_for_challenge_resolution(
    page: Page,
    timeout_seconds: int = 25,
) -> bool:
    """Wait for human user to solve anti-bot challenge in visible browser."""
    elapsed = 0
    while elapsed < timeout_seconds:
        await page.wait_for_timeout(2000)
        elapsed += 2
        try:
            text = await page.locator("body").inner_text()
            if not is_challenge_page(page.url, text):
                return True
        except Exception:
            pass
    print("\n[TIMEOUT] Challenge was not resolved within allotted time.", file=sys.stderr, flush=True)
    return False
