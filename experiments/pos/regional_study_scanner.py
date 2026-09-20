#!/usr/bin/env python3
"""Multi-region Google Flights scanner for the empirical regional arbitrage study.

Scans fares across 10 strategic markets (FI, VN, DE, FR, GB, TR, QA, AE, US, SE),
evaluates point-of-sale (POS) and point-of-origin (POO) price dynamics, tests
currency impacts, and writes one JSON record per query to ``flight_results_study/``.

The query matrix below is fixed on purpose: it is the study's protocol, so it
must stay reproducible run to run rather than follow the active trip config.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
from typing import Any

try:
    from playwright.async_api import Page, async_playwright
except ImportError as error:
    raise SystemExit(
        "Playwright is required. Run: python -m pip install -r requirements.txt"
    ) from error

from src import browser, reporting
from src.common import configure_stdio, resolve_browser_executable
from src.config import PROJECT_ROOT
from src.google_parse import (
    build_regional_url,
    is_blocked,
    parse_card_details,
    reported_currency,
    reported_location,
)

ROOT = PROJECT_ROOT
DEFAULT_PROFILE_DIR = ROOT / ".browser-profile"
STUDY_RESULTS_DIR = ROOT / "flight_results_study"
SOURCE = "Google Flights Regional Scanner"

TARGET_REGIONS = [
    {"code": "FI", "name": "Finland", "currency": "EUR", "role": "Baseline / Origin"},
    {"code": "VN", "name": "Vietnam", "currency": "EUR", "alt_currency": "VND", "role": "Destination Market"},
    {"code": "DE", "name": "Germany", "currency": "EUR", "role": "EU Aviation Mega-Hub"},
    {"code": "FR", "name": "France", "currency": "EUR", "role": "SkyTeam / Air France Hub"},
    {"code": "GB", "name": "United Kingdom", "currency": "EUR", "alt_currency": "GBP", "role": "Non-EU Western Europe"},
    {"code": "TR", "name": "Turkey", "currency": "EUR", "alt_currency": "TRY", "role": "Turkish Airlines Hub / Volatile FX"},
    {"code": "QA", "name": "Qatar", "currency": "EUR", "alt_currency": "QAR", "role": "Qatar Airways Hub"},
    {"code": "AE", "name": "UAE", "currency": "EUR", "alt_currency": "AED", "role": "Emirates / Etihad Hub"},
    {"code": "US", "name": "United States", "currency": "EUR", "alt_currency": "USD", "role": "Global Benchmark"},
    {"code": "SE", "name": "Sweden", "currency": "EUR", "alt_currency": "SEK", "role": "Nordic Peer Control"},
]

# 10 key date pairs across December 2026 - January 2027.
DATE_PAIRS = [
    (dt.date(2026, 12, 9), dt.date(2027, 1, 5)),
    (dt.date(2026, 12, 9), dt.date(2027, 1, 6)),
    (dt.date(2026, 12, 9), dt.date(2027, 1, 7)),
    (dt.date(2026, 12, 10), dt.date(2027, 1, 5)),
    (dt.date(2026, 12, 10), dt.date(2027, 1, 6)),
    (dt.date(2026, 12, 10), dt.date(2027, 1, 7)),
    (dt.date(2026, 12, 11), dt.date(2027, 1, 5)),
    (dt.date(2026, 12, 11), dt.date(2027, 1, 6)),
    (dt.date(2026, 12, 11), dt.date(2027, 1, 8)),
    (dt.date(2026, 12, 12), dt.date(2027, 1, 6)),
]

# Reverse-route control: does the point of origin move the price independently?
REVERSE_DATE_PAIRS = [
    (dt.date(2026, 12, 9), dt.date(2027, 1, 5)),
    (dt.date(2026, 12, 10), dt.date(2027, 1, 6)),
    (dt.date(2026, 12, 11), dt.date(2027, 1, 6)),
    (dt.date(2026, 12, 12), dt.date(2027, 1, 6)),
]

# Currency control: one fixed date pair quoted in each market's native currency.
CURRENCY_TEST_DEPARTURE = dt.date(2026, 12, 9)
CURRENCY_TEST_RETURN = dt.date(2027, 1, 5)
CURRENCY_TESTS = [("VN", "VND"), ("TR", "TRY"), ("US", "USD"), ("GB", "GBP"), ("SE", "SEK"), ("QA", "QAR"), ("AE", "AED")]


def build_task_matrix() -> list[dict[str, Any]]:
    """Expand the study protocol into the flat list of queries to run."""
    tasks: list[dict[str, Any]] = []

    # 1. HEL -> HAN: natural-IP baseline plus each target POS, per date pair.
    for dep, ret in DATE_PAIRS:
        tasks.append({
            "origin": "HEL", "dest": "HAN", "dep": dep, "ret": ret,
            "gl": "NONE", "curr": "EUR",
            "category": "baseline_no_gl", "region_name": "Natural IP Baseline",
        })
        for region in TARGET_REGIONS:
            tasks.append({
                "origin": "HEL", "dest": "HAN", "dep": dep, "ret": ret,
                "gl": region["code"], "curr": "EUR",
                "category": "outbound_pos", "region_name": region["name"],
            })

    # 2. HAN -> HEL reverse control.
    for dep, ret in REVERSE_DATE_PAIRS:
        for region in TARGET_REGIONS:
            tasks.append({
                "origin": "HAN", "dest": "HEL", "dep": dep, "ret": ret,
                "gl": region["code"], "curr": "EUR",
                "category": "reverse_control", "region_name": region["name"],
            })

    # 3. Native-currency quotes for the fixed reference pair.
    for gl, curr in CURRENCY_TESTS:
        tasks.append({
            "origin": "HEL", "dest": "HAN",
            "dep": CURRENCY_TEST_DEPARTURE, "ret": CURRENCY_TEST_RETURN,
            "gl": gl, "curr": curr,
            "category": "currency_arbitrage", "region_name": gl,
        })

    return tasks


async def extract_candidate_cards(page: Page, curr: str = "EUR") -> list[dict[str, Any]]:
    """Parse visible cards with the multi-currency price parser."""
    return await browser.extract_cards(
        page,
        curr=curr,
        selector=browser.LISTITEM_CARD_SELECTOR,
        min_length=25,
        key_length=100,
        limit=30,
        scroll_steps=1,
        parser=parse_card_details,
    )


async def scan_single_target(
    page: Page,
    *,
    origin: str,
    destination: str,
    departure: dt.date,
    return_date: dt.date,
    gl: str,
    curr: str = "EUR",
    timeout_ms: int = 25_000,
) -> dict[str, Any]:
    """Run one region/currency query and return its observation."""
    url = build_regional_url(origin, destination, departure, return_date, gl=gl, curr=curr)
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    except Exception:
        await page.goto(url, timeout=timeout_ms)

    if await browser.dismiss_consent(page, timeout_ms=2_000):
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

    page_text = await browser.wait_for_results(page, timeout_ms, settle_ms=2_000)

    lowered = page_text.lower()
    if "something went wrong" in lowered or "no results returned" in lowered:
        if await browser.click_reload_if_present(page):
            page_text = await browser.wait_for_results(page, timeout_ms, settle_ms=2_000)

    await browser.switch_to_cheapest_tab(page)
    page_text = await browser.page_text(page) or page_text

    cards = await extract_candidate_cards(page, curr=curr)
    card_prices = [card["lowest_price"] for card in cards if card["lowest_price"] is not None]

    if is_blocked(page_text):
        status = "blocked"
    elif cards:
        status = "observed"
    elif "loading" in page_text.lower():
        status = "incomplete"
    else:
        status = "no_results"

    return {
        "fetched_at": reporting.utc_now(),
        "source": SOURCE,
        "origin": origin,
        "destination": destination,
        "departure_date": departure.isoformat(),
        "return_date": return_date.isoformat(),
        "stay_nights": (return_date - departure).days,
        "gl": gl,
        "currency": curr,
        "status": status,
        "lowest_price": min(card_prices) if card_prices else None,
        "reported_location": reported_location(page_text),
        "reported_currency": reported_currency(page_text),
        "num_candidates": len(cards),
        "cards": cards,
        "page_url": page.url,
    }


def get_checkpoint_filename(origin: str, dest: str, dep: dt.date, ret: dt.date, gl: str, curr: str) -> str:
    """Checkpoint name; currency is part of the key because it is a study variable."""
    return f"{origin}_{dest}_{dep.isoformat()}_{ret.isoformat()}_gl-{gl}_curr-{curr}.json"


async def main_scanner(headless: bool = True, delay_seconds: float = 2.5, max_retries: int = 2) -> None:
    results_dir = STUDY_RESULTS_DIR
    results_dir.mkdir(parents=True, exist_ok=True)
    DEFAULT_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    tasks = build_task_matrix()
    print("=" * 80)
    print("STARTING AUTONOMOUS OVERNIGHT REGIONAL DYNAMIC PRICING STUDY")
    print(f"Total planned queries: {len(tasks)}")
    print(f"Destination folder: {results_dir}")
    print(f"Throttling delay: {delay_seconds}s | Headless: {headless}")
    print("=" * 80)

    browser_executable = resolve_browser_executable()
    completed_count = skipped_count = error_count = 0

    async with async_playwright() as playwright:
        context = await browser.launch_google_context(
            playwright, DEFAULT_PROFILE_DIR, headless=headless, executable_path=browser_executable
        )
        page = context.pages[0] if context.pages else await context.new_page()

        try:
            for index, task in enumerate(tasks, start=1):
                label = (
                    f"{task['origin']}->{task['dest']} | POS={task['gl']} ({task['curr']}) "
                    f"| {task['dep']}->{task['ret']}"
                )
                filename = get_checkpoint_filename(
                    task["origin"], task["dest"], task["dep"], task["ret"], task["gl"], task["curr"]
                )
                filepath = results_dir / filename

                existing = reporting.load_checkpoint(filepath)
                if existing and existing.get("status") == "observed" and existing.get("cards"):
                    skipped_count += 1
                    print(f"[{index}/{len(tasks)}] [CACHED] {label} => {existing.get('lowest_price')} {task['curr']}")
                    continue

                print(f"[{index}/{len(tasks)}] [SCANNING] {label}...")
                observation = None
                for attempt in range(1, max_retries + 1):
                    try:
                        observation = await scan_single_target(
                            page,
                            origin=task["origin"],
                            destination=task["dest"],
                            departure=task["dep"],
                            return_date=task["ret"],
                            gl=task["gl"],
                            curr=task["curr"],
                        )
                        observation["category"] = task["category"]
                        observation["region_name"] = task["region_name"]
                        if observation["status"] == "observed" and observation["cards"]:
                            break
                        if attempt < max_retries:
                            print(f"  Attempt {attempt} returned {observation['status']}. Retrying in 2s...")
                            await asyncio.sleep(2.0)
                    except Exception as error:
                        print(f"  Attempt {attempt} failed: {error}")
                        if attempt < max_retries:
                            await asyncio.sleep(2.0)

                if observation is None:
                    observation = {
                        "fetched_at": reporting.utc_now(),
                        "source": SOURCE,
                        "origin": task["origin"],
                        "destination": task["dest"],
                        "departure_date": task["dep"].isoformat(),
                        "return_date": task["ret"].isoformat(),
                        "gl": task["gl"],
                        "currency": task["curr"],
                        "status": "error",
                        "lowest_price": None,
                        "cards": [],
                        "category": task["category"],
                    }
                    error_count += 1
                elif observation["status"] == "observed":
                    completed_count += 1
                else:
                    error_count += 1

                reporting.write_json(filepath, observation)
                lowest = observation.get("lowest_price")
                lowest_str = f"{lowest} {task['curr']}" if lowest else "N/A"
                print(
                    f"  => Status: {observation.get('status')} | Lowest: {lowest_str} "
                    f"| Cards: {len(observation.get('cards', []))} | Saved: {filename}"
                )

                if index < len(tasks):
                    await asyncio.sleep(delay_seconds)
        finally:
            await context.close()

    print("\n" + "=" * 80)
    print("REGIONAL STUDY SCAN COMPLETE!")
    print(f"Total: {len(tasks)} | New Observed: {completed_count} | Cached: {skipped_count} | Incomplete/Error: {error_count}")
    print(f"Raw data saved to: {results_dir}")
    print("=" * 80)


def main() -> None:
    configure_stdio()
    parser = argparse.ArgumentParser(description="Multi-Region Flight Scanner")
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--delay", type=float, default=2.5)
    args = parser.parse_args()
    asyncio.run(main_scanner(headless=args.headless, delay_seconds=args.delay))


if __name__ == "__main__":
    main()
