"""Browser session, context lifecycle, and common page helpers.

Handles Playwright and Patchright persistent contexts, profile configuration,
cookie consent banners, and raw page text retrieval.
"""

from __future__ import annotations

from pathlib import Path

from playwright.async_api import BrowserContext, Page, Playwright

# Pre-seeded so the dedicated profile never sees an interstitial consent wall.
GOOGLE_CONSENT_COOKIES = [
    {"name": "SOCS", "value": "CAESEwgDEgk1ODEzNzI3NDQaAmVuIAEaBgiAo_mwBg", "domain": ".google.com", "path": "/"},
    {"name": "CONSENT", "value": "PENDING+999", "domain": ".google.com", "path": "/"},
]

CONSENT_BUTTON_NAMES = ("Reject all", "Accept all")


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
    """Dismiss the first-run cookie banner, preferring the privacy-preserving choice."""
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
