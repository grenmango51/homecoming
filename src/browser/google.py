"""Google Flights browser automation and DOM interaction helpers.

Drives the rendered Google Flights interface: result readiness waiting,
cheapest-tab switching, sort order enforcement, and card extraction.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

try:
    from patchright.async_api import Page
except ImportError:
    from playwright.async_api import Page

from src.browser.session import page_text
from src.completion import GoogleCompletionGate
from src.google_parse import is_blocked, parse_card_details

DEFAULT_CARD_SELECTOR = "ul.Rk10dc > li, li.pIav2d"
LISTITEM_CARD_SELECTOR = "ul.Rk10dc > li, li[role='listitem'], [role='listitem'], li.pIav2d"

_RESULT_STATE_JS = """() => {
    const text = document.body?.innerText || '';
    const visible = element => {
        const rect = element.getBoundingClientRect();
        if (!element.getClientRects().length || rect.width <= 0 || rect.height <= 0) return false;
        for (let node = element; node instanceof Element; node = node.parentElement) {
            const style = getComputedStyle(node);
            if (style.display === 'none' || style.visibility === 'hidden' || style.visibility === 'collapse' || Number(style.opacity) === 0) return false;
        }
        // Google uses aria-hidden=true on visibly animated decorative indicators.
        return true;
    };
    const shown = selector => [...document.querySelectorAll(selector)].filter(visible);
    const progress = shown("[role='progressbar'], [aria-busy='true'], .m6QErb.D5KUk");
    const skeletons = shown("[class*='placeholder'], [class*='shimmer'], [class*='skeleton']");
    const loadingMessages = shown(".HoPSkc, .OeyRC, [role='status']")
        .filter(e => /searching|fetching results|loading (?:flights|results)/i.test(e.innerText || ''));
    const cards = shown("ul.Rk10dc > li, li.pIav2d, [role='listitem']");
    const tabs = shown("[role='tab'], [aria-label*='Cheapest']")
        .filter(e => /cheapest/i.test(e.innerText || e.getAttribute('aria-label') || ''));
    return {
        text,
        blocked: /unusual traffic|captcha|verify you are human/i.test(text),
        retryable_error: /oops, something went wrong|no results returned/i.test(text),
        loading: progress.length > 0 || skeletons.length > 0 || loadingMessages.length > 0,
        has_results: /\\b\\d+\\s+results returned\\b|departing flights|cheapest|no flights/i.test(text),
        has_cards: cards.length > 0,
        card_texts: cards.map(e => e.innerText || ''),
        tab_texts: tabs.map(e => e.innerText || ''),
        signature: JSON.stringify([tabs.map(e => e.innerText || ''), cards.map(e => e.innerText || '')]),
        visible_progressbars: progress.length,
        loading_messages: loadingMessages.length,
        visible_skeletons: skeletons.length,
    };
}"""

_RESULTS_READY_JS = "() => { const s = (" + _RESULT_STATE_JS + ")(); return s.blocked || (!s.loading && s.has_results && s.has_cards); }"


async def google_result_snapshot(page: Page) -> dict:
    """Read the loading indicators and result signature together."""
    return await page.evaluate(_RESULT_STATE_JS)


async def wait_for_results(page: Page, timeout_ms: int, settle_ms: int = 3_000) -> str:
    snapshot = await wait_for_result_snapshot(page, timeout_ms, settle_ms)
    return snapshot["text"]


async def wait_for_result_snapshot(page: Page, timeout_ms: int, settle_ms: int = 3_000) -> dict:
    """Wait for every loading indicator to clear and prices/cards to stay stable."""
    gate = GoogleCompletionGate(settle_seconds=max(0, settle_ms) / 1000)
    deadline = time.monotonic() + timeout_ms / 1000
    loading_seen = False
    while time.monotonic() < deadline:
        snapshot = await google_result_snapshot(page)
        loading_seen = loading_seen or snapshot["loading"]
        if gate.ready(snapshot, time.monotonic()):
            return snapshot | {"loading_seen": loading_seen, "stable_ms": settle_ms}
        await page.wait_for_timeout(min(250, max(1, int((deadline - time.monotonic()) * 1000))))
    raise TimeoutError(f"Google Flights results did not finish loading and stabilize within {timeout_ms}ms")


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
    """Switch the sort dropdown from 'Top flights' to 'Price' using bounded readiness.

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
    texts: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Parse the visible flight cards, de-duplicated and priced."""
    for _ in range(scroll_steps):
        try:
            await page.evaluate("window.scrollBy(0, 400)")
            await page.wait_for_timeout(400)
        except Exception:
            break

    cards: list[dict[str, Any]] = []
    seen: set[str] = set()
    for text in texts if texts is not None else await _card_texts(page, selector):
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
