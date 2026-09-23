"""Skyscanner browser automation, anti-bot challenge handling, and DOM extraction.

Provides persistent stealth browser launching (Patchright / Playwright),
session resetting, anti-bot verification waiting, and DOM card extraction.
"""

from __future__ import annotations

import random
import sys
import time
from pathlib import Path

try:
    from patchright.async_api import BrowserContext, Page, Playwright
except ImportError:
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


async def reset_session(
    page: Page,
    home_url: str = "https://www.skyscanner.fi/",
    *,
    scrub_cookies: bool = False,
) -> None:
    """Warm up on homepage and dismiss consent to keep the anti-bot session healthy.

    When scrub_cookies=True, wipes accumulated blocked/flagged tokens, clears
    localStorage, re-injects baseline Finnish locale/market cookies, and warms up.
    """
    try:
        if scrub_cookies:
            print("[Skyscanner] Scrubbing cookies and resetting session on homepage...", flush=True)
            await page.context.clear_cookies()
            try:
                await page.evaluate("() => { try { localStorage.clear(); sessionStorage.clear(); } catch(e) {} }")
            except Exception:
                pass
        await page.context.add_cookies(SKYSCANNER_COOKIES)
        await page.goto(home_url, wait_until="domcontentloaded", timeout=25000)
        try:
            await page.mouse.move(120, 240)
            await page.wait_for_timeout(200)
            await page.mouse.move(420, 360, steps=6)
        except Exception:
            pass
        for btn_name in ("Reject all", "Hylkää kaikki", "Decline all", "Accept all", "Hyväksy kaikki"):
            btn = page.get_by_role("button", name=btn_name, exact=False)
            if await btn.count() > 0 and await btn.first.is_visible():
                await btn.first.click(timeout=2000)
                break
        await page.wait_for_timeout(2000)
    except Exception as e:
        print(f"[Skyscanner] reset_session error: {e}", file=sys.stderr, flush=True)


async def find_press_and_hold_target(page: Page) -> tuple[float, float] | None:
    """Find the coordinates of a PerimeterX press-and-hold button or container."""
    selectors = [
        "[aria-label*='Press and Hold' i]",
        "[aria-label*='Press & Hold' i]",
        "[aria-label*='Paina ja pidä' i]",
        "div[role='button'][aria-label*='hold' i]",
        "#px-captcha",
        "#px-captcha-wrapper",
        "div[class*='px-captcha']",
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel)
            if await loc.count() > 0 and await loc.first.is_visible():
                box = await loc.first.bounding_box()
                if box and box["width"] > 15 and box["height"] > 15:
                    return (box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        except Exception:
            continue

    for frame in page.frames:
        if frame == page.main_frame:
            continue
        for sel in [
            "[aria-label*='Press and Hold' i]",
            "[aria-label*='Press & Hold' i]",
            "[aria-label*='Paina ja pidä' i]",
            "#px-captcha",
            "div[role='button']",
            "button",
        ]:
            try:
                loc = frame.locator(sel)
                if await loc.count() > 0 and await loc.first.is_visible():
                    box = await loc.first.bounding_box()
                    if box and box["width"] > 15 and box["height"] > 15:
                        return (box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            except Exception:
                continue

    try:
        iframe_loc = page.locator(
            "iframe[title*='PerimeterX' i], iframe[src*='perimeterx' i], iframe[src*='px' i], iframe[id*='px-captcha' i]"
        )
        if await iframe_loc.count() > 0 and await iframe_loc.first.is_visible():
            box = await iframe_loc.first.bounding_box()
            if box and box["width"] > 20 and box["height"] > 20:
                return (box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    except Exception:
        pass

    return None


async def try_solve_press_and_hold(page: Page, hold_seconds: float = 11.5) -> bool:
    """Attempt automated solving of PerimeterX 'Press & Hold' challenge."""
    try:
        body_text = await page.locator("body").inner_text()
    except Exception:
        body_text = ""
    if not is_challenge_page(page.url, body_text):
        return True

    print("[Skyscanner] Anti-bot challenge detected. Searching for Press & Hold target...", flush=True)
    coords = None
    for _ in range(4):
        coords = await find_press_and_hold_target(page)
        if coords:
            break
        await page.wait_for_timeout(1000)

    if not coords:
        print("[Skyscanner] No Press & Hold target element found.", flush=True)
        return False

    cx, cy = coords
    print(
        f"[Skyscanner] Press & Hold target found at ({cx:.1f}, {cy:.1f}). Holding for {hold_seconds:.1f}s...",
        flush=True,
    )

    try:
        await page.mouse.move(cx, cy, steps=8)
        await page.wait_for_timeout(300)
        await page.mouse.down()

        start_hold = time.monotonic()
        while time.monotonic() - start_hold < hold_seconds:
            await page.wait_for_timeout(1000)
            jitter_x = cx + random.uniform(-1.0, 1.0)
            jitter_y = cy + random.uniform(-1.0, 1.0)
            await page.mouse.move(jitter_x, jitter_y)

        await page.mouse.up()
        print(f"[Skyscanner] Released hold after {hold_seconds:.1f}s. Waiting for verification to register...", flush=True)
        await page.wait_for_timeout(3500)

        try:
            new_text = await page.locator("body").inner_text()
            if not is_challenge_page(page.url, new_text):
                print("[Skyscanner] Challenge passed via automated Press & Hold!", flush=True)
                return True
        except Exception:
            pass
    except Exception as exc:
        print(f"[Skyscanner] Error during automated press & hold: {exc}", file=sys.stderr, flush=True)
        try:
            await page.mouse.up()
        except Exception:
            pass

    return False


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
