"""Playwright helpers shared by the Google Flights scanners.

Every scanner drives the same rendered page, so consent handling, result
waiting, cheapest-tab switching and card extraction live here once. The pure
parsing these helpers delegate to lives in :mod:`src.google_parse`.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from playwright.async_api import BrowserContext, Page, Playwright

from src.google_parse import is_blocked, parse_card_details

# Pre-seeded so the dedicated profile never sees an interstitial consent wall.
GOOGLE_CONSENT_COOKIES = [
    {"name": "SOCS", "value": "CAESEwgDEgk1ODEzNzI3NDQaAmVuIAEaBgiAo_mwBg", "domain": ".google.com", "path": "/"},
    {"name": "CONSENT", "value": "PENDING+999", "domain": ".google.com", "path": "/"},
]

CONSENT_BUTTON_NAMES = ("Reject all", "Accept all")

DEFAULT_CARD_SELECTOR = "ul.Rk10dc > li, li.pIav2d"
LISTITEM_CARD_SELECTOR = "ul.Rk10dc > li, li[role='listitem'], [role='listitem'], li.pIav2d"

# Resolves once results have rendered, or immediately if the page is an
# anti-automation interstitial (so callers can classify and stop).
_RESULTS_READY_JS = """() => {
    const text = document.body?.innerText || '';
    if (/unusual traffic|captcha|verify you are human/i.test(text)) {
        return true;
    }
    const hasResults = /\\b\\d+\\s+results returned\\b|departing flights|cheapest|no flights/i.test(text);
    const isLoading = /loading results|fetching results/i.test(text);
    return hasResults && !isLoading;
}"""


async def launch_google_context(
    playwright: Playwright,
    profile_dir: Path | str,
    *,
    headless: bool = False,
    executable_path: str | None = None,
    viewport: dict[str, int] | None = None,
) -> BrowserContext:
    """Open a persistent Chromium profile pre-seeded with Google consent cookies."""
    context = await playwright.chromium.launch_persistent_context(
        str(profile_dir),
        headless=headless,
        executable_path=executable_path,
        locale="en-US",
        viewport=viewport or {"width": 1440, "height": 1000},
        args=["--disable-blink-features=AutomationControlled"],
    )
    try:
        await context.add_cookies(GOOGLE_CONSENT_COOKIES)
    except Exception:
        pass
    return context


async def page_text(page: Page) -> str:
    """Body text of the page, or an empty string if it could not be read."""
    try:
        return await page.locator("body").inner_text()
    except Exception:
        return ""


async def dismiss_consent(page: Page, target_url: str | None = None, timeout_ms: int = 3_000) -> bool:
    """Dismiss the first-run cookie banner, preferring the privacy-preserving choice.

    Each button name is matched on its own locator: a combined selector would
    match both buttons at once and trip Playwright's strict-mode check.
    """
    for name in CONSENT_BUTTON_NAMES:
        try:
            button = page.get_by_role("button", name=name, exact=True).first
            if await button.count() == 0 or not await button.is_visible(timeout=timeout_ms):
                continue
            await button.click(timeout=timeout_ms)
            await page.wait_for_timeout(500)
            if target_url:
                try:
                    await page.wait_for_url(lambda url: "travel/flights" in url, timeout=10_000)
                except Exception:
                    await page.goto(target_url, wait_until="domcontentloaded")
            return True
        except Exception:
            continue
    return False


async def wait_for_results(page: Page, timeout_ms: int, settle_ms: int = 2_500) -> str:
    """Wait for rendered results, then let cards and the cheapest banner settle.

    Never raises: a timeout leaves whatever the page currently shows for the
    caller to classify, which keeps a supervised browser open for intervention.
    """
    try:
        await page.wait_for_function(_RESULTS_READY_JS, timeout=timeout_ms)
    except Exception:
        pass
    await page.wait_for_timeout(settle_ms)
    return await page_text(page)


async def click_reload_if_present(page: Page, settle_ms: int = 3_000) -> bool:
    """Click Google's own 'Reload' button after a rendering glitch."""
    try:
        reload_button = page.get_by_role("button", name="Reload", exact=True)
        if await reload_button.count() > 0 and await reload_button.first.is_visible():
            await reload_button.first.click(timeout=2_000)
            await page.wait_for_timeout(settle_ms)
            return True
    except Exception:
        pass
    return False


async def switch_to_cheapest_tab(page: Page) -> bool:
    """Activate the Cheapest results tab. Returns True if it is now active."""
    selectors = (
        "[role='tab']:has-text('Cheapest')",
        "button:has-text('Cheapest')",
        "[aria-label*='Cheapest']",
        "div[role='tab']:has-text('Cheapest')",
    )
    try:
        for selector in selectors:
            locator = page.locator(selector)
            if await locator.count() > 0 and await locator.first.is_visible():
                if await locator.first.get_attribute("aria-selected") != "true":
                    await locator.first.click(timeout=2_000)
                    await page.wait_for_timeout(2_500)
                return True
    except Exception:
        pass
    return False


async def sort_by_price(page: Page) -> bool:
    """Switch the sort dropdown from 'Top flights' to 'Price'.

    A no-op when the URL already pins the cheapest sort via ``tfu``.
    """
    from src.google_parse import CHEAPEST_SORT_PARAM

    if f"tfu={CHEAPEST_SORT_PARAM}" in page.url:
        return False
    try:
        sort_button = page.locator(
            "button:has-text('Sorted by top flights'), [aria-label*='Sorted by top flights']"
        )
        if await sort_button.count() > 0 and await sort_button.first.is_visible():
            await sort_button.first.click(timeout=1_500)
            await page.wait_for_timeout(1_000)
            price_option = page.locator(
                "[role='menuitem']:has-text('Price'), [role='option']:has-text('Price'), span:text-is('Price')"
            )
            if await price_option.count() > 0:
                await price_option.first.click(timeout=1_500)
                await page.wait_for_timeout(2_500)
                return True
    except Exception:
        pass
    return False


async def read_cheapest_tab_text(page: Page) -> str:
    """Text of the Cheapest tab header, which carries the headline fare."""
    try:
        tab = page.locator(
            "[role='tab']:has-text('Cheapest'), button:has-text('Cheapest'), [aria-label*='Cheapest']"
        ).first
        if await tab.count() > 0:
            return await tab.inner_text() or ""
    except Exception:
        pass
    return ""


async def _card_texts(page: Page, selector: str) -> list[str]:
    """Raw innerText of every result card, preferring one round-trip JS eval."""
    try:
        return await page.evaluate(
            "(selector) => Array.from(document.querySelectorAll(selector)).map(e => e.innerText || '')",
            selector,
        )
    except Exception:
        try:
            return await page.locator(selector).all_inner_texts()
        except Exception:
            return []


async def extract_cards(
    page: Page,
    *,
    curr: str = "EUR",
    selector: str = DEFAULT_CARD_SELECTOR,
    min_length: int = 25,
    key_length: int = 80,
    limit: int = 30,
    scroll_steps: int = 1,
    parser: Callable[[str, str], dict[str, Any]] = parse_card_details,
) -> list[dict[str, Any]]:
    """Parse the visible flight cards, de-duplicated and priced.

    Reads the already-rendered Top and Other departing flights. It deliberately
    never clicks 'view more flights', which collapses the Top Departing Flights
    section and loses the cheapest results.
    """
    for _ in range(scroll_steps):
        try:
            await page.evaluate("window.scrollBy(0, 400)")
            await page.wait_for_timeout(400)
        except Exception:
            break

    cards: list[dict[str, Any]] = []
    seen: set[str] = set()
    for text in await _card_texts(page, selector):
        compact = " ".join(text.split())
        if len(compact) < min_length:
            continue
        lowered = compact.lower()
        if "stop" not in lowered and "nonstop" not in lowered:
            continue
        card = parser(compact, curr)
        if not card["observed_prices"]:
            continue
        key = compact[:key_length].lower()
        if key in seen:
            continue
        seen.add(key)
        cards.append(card)
    return cards[:limit]


async def wait_for_hydration(page: Page, attempts: int, interval_ms: int, ready: Callable[[str], bool]) -> str:
    """Poll the page while Google streams results in, stopping early when blocked."""
    text = ""
    for _ in range(attempts):
        await page.wait_for_timeout(interval_ms)
        text = await page_text(page)
        if is_blocked(text) or ready(text):
            break
    return text
