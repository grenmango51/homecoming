#!/usr/bin/env python3
"""Multi-Region Google Flights Scanner for Empirical Regional Arbitrage Study.

Scans flight fares across 10 strategic geographic markets (FI, VN, DE, FR, GB, TR, QA, AE, US, SE),
evaluates Point of Sale (POS) and Point of Origin (POO) price dynamics, tests currency impacts,
and saves incremental structured JSON records to flight_results_study/.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="backslashreplace", line_buffering=True)

try:
    from playwright.async_api import BrowserContext, Page, async_playwright
except ImportError as error:
    raise SystemExit("Playwright is required. Run: .\\.venv\\Scripts\\python.exe -m pip install playwright") from error

from src.common import (
    PRICE_RE,
    EUROS_RE,
    duration_minutes,
    resolve_browser_executable,
)
from src.config import PROJECT_ROOT

ROOT = PROJECT_ROOT
DEFAULT_PROFILE_DIR = ROOT / ".browser-profile"
STUDY_RESULTS_DIR = ROOT / "flight_results_study"
GOOGLE_FLIGHTS = "https://www.google.com/travel/flights"

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

KEY_AIRLINES = [
    "Qatar Airways", "Emirates", "Turkish Airlines", "Finnair", "Etihad",
    "Vietnam Airlines", "Air France", "KLM", "Lufthansa", "THAI", "Iberia",
    "Condor", "Singapore Airlines", "British Airways", "China Southern", "Air China"
]

AIRPORT_RE = re.compile(r"\b([A-Z]{3})\b")
KNOWN_AIRPORTS = {
    "HEL", "HAN", "DOH", "DXB", "IST", "WAW", "AUH", "MUC", "BER", "ZRH",
    "BKK", "CPH", "AMS", "CDG", "LHR", "FRA", "SIN", "VIE", "ARN", "OSL"
}


def build_regional_url(origin: str, destination: str, departure: dt.date, return_date: dt.date, gl: str | None = None, curr: str = "EUR") -> str:
    orig = origin.strip().upper()
    dest = destination.strip().upper()
    url = f"{GOOGLE_FLIGHTS}?q=Flights%20to%20{dest}%20from%20{orig}%20on%20{departure.isoformat()}%20through%20{return_date.isoformat()}&hl=en&curr={curr}"
    if gl and gl != "NONE":
        url += f"&gl={gl}"
    return url


def extract_money_values(text: str, curr: str = "EUR") -> list[float]:
    values: list[float] = []
    # Match symbols: €, $, £, ₺, ₫, or standard currency prefixes
    pattern = re.compile(r"(?:[€$£₺₫]|EUR|USD|GBP|TRY|VND|QAR|AED|SEK)\s*([0-9][0-9.,\s]*)", re.IGNORECASE)
    for match in pattern.finditer(text):
        raw = match.group(1).strip().replace(" ", "").replace("\u00a0", "")
        # Remove commas / dots appropriately
        if raw.count(",") == 1 and raw.count(".") == 0:
            if len(raw.split(",")[1]) == 3:
                normalized = raw.replace(",", "")
            else:
                normalized = raw.replace(",", ".")
        elif raw.count(".") == 1 and raw.count(",") == 0:
            if len(raw.split(".")[1]) == 3:
                normalized = raw.replace(".", "")
            else:
                normalized = raw
        elif "," in raw and "." in raw:
            if raw.rfind(",") > raw.rfind("."):
                normalized = raw.replace(".", "").replace(",", ".")
            else:
                normalized = raw.replace(",", "")
        else:
            normalized = raw.replace(",", "").replace(".", "")
        try:
            val = float(normalized)
            if val >= 15.0:
                values.append(val)
        except ValueError:
            continue
    # Fallback to general price regex
    if not values and curr == "EUR":
        for raw in PRICE_RE.findall(text) + EUROS_RE.findall(text):
            normalized = raw.replace(" ", "").replace("\u00a0", "").replace(",", "")
            try:
                val = float(normalized)
                if val >= 15.0:
                    values.append(val)
            except ValueError:
                continue
    return values


def parse_card_details(raw_text: str, curr: str = "EUR") -> dict[str, Any]:
    # Stops
    stops = 0
    lower = raw_text.lower()
    if "nonstop" in lower:
        stops = 0
    elif "1 stop" in lower:
        stops = 1
    elif "2 stops" in lower:
        stops = 2
    elif "3 stops" in lower:
        stops = 3

    # Layovers
    found_airports = [
        code for code in AIRPORT_RE.findall(raw_text)
        if code in KNOWN_AIRPORTS and code not in {"HEL", "HAN"}
    ]
    layovers = list(dict.fromkeys(found_airports))

    # Airlines
    carriers = [carrier for carrier in KEY_AIRLINES if carrier.lower() in lower]

    # Prices
    prices = extract_money_values(raw_text, curr=curr)

    # Duration
    dur_min = duration_minutes(raw_text)

    # Protection
    protection = "separate_tickets" if "separate tickets booked together" in lower else "not_flagged_by_google"

    return {
        "text": raw_text,
        "carriers": carriers,
        "stops": stops,
        "layovers": layovers,
        "duration_minutes": dur_min,
        "observed_prices": prices,
        "lowest_price": min(prices) if prices else None,
        "currency": curr,
        "protection_label": protection,
    }


async def wait_for_flight_results(page: Page, timeout_ms: int = 20000) -> str:
    try:
        await page.wait_for_function(
            """() => {
                const text = document.body?.innerText || '';
                if (/unusual traffic|captcha|verify you are human/i.test(text)) {
                    return true;
                }
                const hasResults = /\\b\\d+\\s+results returned\\b|departing flights|cheapest|no flights/i.test(text);
                const isLoading = /loading results|fetching results/i.test(text);
                return hasResults && !isLoading;
            }""",
            timeout=timeout_ms,
        )
    except Exception:
        pass
    await page.wait_for_timeout(2000)
    return await page.locator("body").inner_text()


async def click_reload_if_present(page: Page) -> bool:
    try:
        reload_btn = page.get_by_role("button", name="Reload", exact=True)
        if await reload_btn.count() > 0 and await reload_btn.first.is_visible():
            await reload_btn.click(timeout=2000)
            await page.wait_for_timeout(3000)
            return True
    except Exception:
        pass
    return False


async def switch_cheapest(page: Page) -> bool:
    try:
        tab_locators = [
            page.locator("[role='tab']:has-text('Cheapest')"),
            page.locator("button:has-text('Cheapest')"),
            page.locator("[aria-label*='Cheapest']"),
            page.locator("div[role='tab']:has-text('Cheapest')"),
        ]
        for loc in tab_locators:
            if await loc.count() > 0 and await loc.first.is_visible():
                is_selected = await loc.first.get_attribute("aria-selected")
                if is_selected != "true":
                    await loc.first.click(timeout=2000)
                    await page.wait_for_timeout(2500)
                    return True
                return True
    except Exception:
        pass
    return False


async def extract_clean_candidate_cards(page: Page, curr: str = "EUR") -> list[dict[str, Any]]:
    # Evaluate flight card elements directly from Top and Other departing flights
    # Crucially DO NOT click "view more flights", which collapses Top Departing Flights
    try:
        await page.evaluate("window.scrollBy(0, 400)")
        await page.wait_for_timeout(400)
    except Exception:
        pass

    raw_texts: list[str] = []
    try:
        raw_texts = await page.evaluate(
            """() => {
                const els = document.querySelectorAll("ul.Rk10dc > li, li[role='listitem'], [role='listitem'], li.pIav2d");
                return Array.from(els).map(e => e.innerText || '');
            }"""
        )
    except Exception:
        try:
            locators = page.locator("ul.Rk10dc > li, li[role='listitem'], [role='listitem'], li.pIav2d")
            raw_texts = await locators.all_inner_texts()
        except Exception:
            raw_texts = []

    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for txt in raw_texts:
        compact = " ".join(txt.split())
        if len(compact) < 25:
            continue
        lowered = compact.lower()
        if ("stop" in lowered or "nonstop" in lowered):
            card = parse_card_details(compact, curr=curr)
            if not card["observed_prices"]:
                continue
            key = compact[:100].lower()
            if key not in seen:
                seen.add(key)
                candidates.append(card)
    return candidates[:30]


extract_candidate_cards = extract_clean_candidate_cards


async def scan_single_target(
    page: Page,
    *,
    origin: str,
    destination: str,
    departure: dt.date,
    return_date: dt.date,
    gl: str,
    curr: str = "EUR",
    timeout_ms: int = 25000,
) -> dict[str, Any]:
    url = build_regional_url(origin, destination, departure, return_date, gl=gl, curr=curr)
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    except Exception:
        await page.goto(url, timeout=timeout_ms)

    # Check consent
    try:
        reject_btn = page.get_by_role("button", name="Reject all", exact=True)
        if await reject_btn.is_visible(timeout=2000):
            await reject_btn.click()
            await page.wait_for_timeout(1000)
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    except Exception:
        pass

    page_text = await wait_for_flight_results(page, timeout_ms=timeout_ms)

    # If incomplete or reload needed
    if "something went wrong" in page_text.lower() or "no results returned" in page_text.lower():
        reloaded = await click_reload_if_present(page)
        if reloaded:
            page_text = await wait_for_flight_results(page, timeout_ms=timeout_ms)

    await switch_cheapest(page)
    try:
        page_text = await page.locator("body").inner_text()
    except Exception:
        pass

    cards = await extract_candidate_cards(page, curr=curr)
    
    # Extract footer location & currency reported by Google
    footer_location = "Unknown"
    footer_currency = "Unknown"
    loc_match = re.search(r"Location\s*([^\n\r]+)", page_text)
    if loc_match:
        footer_location = loc_match.group(1).strip()
    curr_match = re.search(r"Currency\s*([A-Z]{3})", page_text)
    if curr_match:
        footer_currency = curr_match.group(1).strip()

    lowest_price = None
    all_card_prices = [c["lowest_price"] for c in cards if c["lowest_price"] is not None]
    if all_card_prices:
        lowest_price = min(all_card_prices)

    status = "observed" if cards else ("incomplete" if "loading" in page_text.lower() else "no_results")
    if any(m in page_text.lower() for m in ("unusual traffic", "captcha", "verify you are human")):
        status = "blocked"

    return {
        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source": "Google Flights Regional Scanner",
        "origin": origin,
        "destination": destination,
        "departure_date": departure.isoformat(),
        "return_date": return_date.isoformat(),
        "stay_nights": (return_date - departure).days,
        "gl": gl,
        "currency": curr,
        "status": status,
        "lowest_price": lowest_price,
        "reported_location": footer_location,
        "reported_currency": footer_currency,
        "num_candidates": len(cards),
        "cards": cards,
        "page_url": page.url,
    }


def get_checkpoint_filename(origin: str, dest: str, dep: dt.date, ret: dt.date, gl: str, curr: str) -> str:
    return f"{origin}_{dest}_{dep.isoformat()}_{ret.isoformat()}_gl-{gl}_curr-{curr}.json"


async def main_scanner(
    headless: bool = True,
    delay_seconds: float = 2.5,
    max_retries: int = 2
) -> None:
    results_dir = STUDY_RESULTS_DIR
    profile_dir = DEFAULT_PROFILE_DIR
    results_dir.mkdir(parents=True, exist_ok=True)
    profile_dir.mkdir(parents=True, exist_ok=True)

    # 10 Key date pairs across December 2026 - January 2027
    date_pairs = [
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

    # Reverse route date pairs (control)
    reverse_date_pairs = [
        (dt.date(2026, 12, 9), dt.date(2027, 1, 5)),
        (dt.date(2026, 12, 10), dt.date(2027, 1, 6)),
        (dt.date(2026, 12, 11), dt.date(2027, 1, 6)),
        (dt.date(2026, 12, 12), dt.date(2027, 1, 6)),
    ]

    # Currency test pairs (fixed date pair, native currency vs EUR)
    currency_tests = [
        {"origin": "HEL", "dest": "HAN", "dep": dt.date(2026, 12, 9), "ret": dt.date(2027, 1, 5), "gl": "VN", "curr": "VND"},
        {"origin": "HEL", "dest": "HAN", "dep": dt.date(2026, 12, 9), "ret": dt.date(2027, 1, 5), "gl": "TR", "curr": "TRY"},
        {"origin": "HEL", "dest": "HAN", "dep": dt.date(2026, 12, 9), "ret": dt.date(2027, 1, 5), "gl": "US", "curr": "USD"},
        {"origin": "HEL", "dest": "HAN", "dep": dt.date(2026, 12, 9), "ret": dt.date(2027, 1, 5), "gl": "GB", "curr": "GBP"},
        {"origin": "HEL", "dest": "HAN", "dep": dt.date(2026, 12, 9), "ret": dt.date(2027, 1, 5), "gl": "SE", "curr": "SEK"},
        {"origin": "HEL", "dest": "HAN", "dep": dt.date(2026, 12, 9), "ret": dt.date(2027, 1, 5), "gl": "QA", "curr": "QAR"},
        {"origin": "HEL", "dest": "HAN", "dep": dt.date(2026, 12, 9), "ret": dt.date(2027, 1, 5), "gl": "AE", "curr": "AED"},
    ]

    # Build full execution matrix
    tasks = []
    # 1. HEL -> HAN across gl=NONE (baseline) + 10 regions x 10 date pairs
    for dep, ret in date_pairs:
        tasks.append({
            "origin": "HEL",
            "dest": "HAN",
            "dep": dep,
            "ret": ret,
            "gl": "NONE",
            "curr": "EUR",
            "category": "baseline_no_gl",
            "region_name": "Natural IP Baseline",
        })
        for reg in TARGET_REGIONS:
            tasks.append({
                "origin": "HEL",
                "dest": "HAN",
                "dep": dep,
                "ret": ret,
                "gl": reg["code"],
                "curr": "EUR",
                "category": "outbound_pos",
                "region_name": reg["name"],
            })

    # 2. HAN -> HEL (reverse route) across 10 regions x 4 date pairs
    for dep, ret in reverse_date_pairs:
        for reg in TARGET_REGIONS:
            tasks.append({
                "origin": "HAN",
                "dest": "HEL",
                "dep": dep,
                "ret": ret,
                "gl": reg["code"],
                "curr": "EUR",
                "category": "reverse_control",
                "region_name": reg["name"],
            })

    # 3. Currency tests
    for c_test in currency_tests:
        tasks.append({
            "origin": c_test["origin"],
            "dest": c_test["dest"],
            "dep": c_test["dep"],
            "ret": c_test["ret"],
            "gl": c_test["gl"],
            "curr": c_test["curr"],
            "category": "currency_arbitrage",
            "region_name": c_test["gl"],
        })

    print("=" * 80)
    print("STARTING AUTONOMOUS OVERNIGHT REGIONAL DYNAMIC PRICING STUDY")
    print(f"Total planned queries: {len(tasks)}")
    print(f"Destination folder: {results_dir}")
    print(f"Throttling delay: {delay_seconds}s | Headless: {headless}")
    print("=" * 80)

    browser_executable = resolve_browser_executable()
    async with async_playwright() as playwright:
        context: BrowserContext = await playwright.chromium.launch_persistent_context(
            str(profile_dir),
            headless=headless,
            executable_path=browser_executable,
            locale="en-US",
            viewport={"width": 1440, "height": 1000},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else await context.new_page()

        completed_count = 0
        skipped_count = 0
        error_count = 0

        for idx, task in enumerate(tasks, start=1):
            fname = get_checkpoint_filename(task["origin"], task["dest"], task["dep"], task["ret"], task["gl"], task["curr"])
            filepath = results_dir / fname

            # Skip existing valid results
            if filepath.exists():
                try:
                    existing = json.loads(filepath.read_text(encoding="utf-8"))
                    if existing.get("status") == "observed" and existing.get("cards"):
                        skipped_count += 1
                        print(f"[{idx}/{len(tasks)}] [CACHED] {task['origin']}->{task['dest']} | POS={task['gl']} ({task['curr']}) | {task['dep']}->{task['ret']} => {existing.get('lowest_price')} {task['curr']}")
                        continue
                except Exception:
                    pass

            print(f"[{idx}/{len(tasks)}] [SCANNING] {task['origin']}->{task['dest']} | POS={task['gl']} ({task['curr']}) | {task['dep']}->{task['ret']}...")

            # Run with retry
            obs = None
            for attempt in range(1, max_retries + 1):
                try:
                    obs = await scan_single_target(
                        page,
                        origin=task["origin"],
                        destination=task["dest"],
                        departure=task["dep"],
                        return_date=task["ret"],
                        gl=task["gl"],
                        curr=task["curr"],
                    )
                    obs["category"] = task["category"]
                    obs["region_name"] = task["region_name"]
                    if obs["status"] == "observed" and obs["cards"]:
                        break
                    elif attempt < max_retries:
                        print(f"  Attempt {attempt} returned {obs['status']}. Retrying in 2s...")
                        await asyncio.sleep(2.0)
                except Exception as ex:
                    print(f"  Attempt {attempt} failed: {ex}")
                    if attempt < max_retries:
                        await asyncio.sleep(2.0)

            if obs is None:
                obs = {
                    "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "source": "Google Flights Regional Scanner",
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
            elif obs["status"] == "observed":
                completed_count += 1
            else:
                error_count += 1

            # Save immediately
            filepath.write_text(json.dumps(obs, ensure_ascii=False, indent=2), encoding="utf-8")
            lowest_str = f"{obs.get('lowest_price')} {task['curr']}" if obs.get('lowest_price') else "N/A"
            print(f"  => Status: {obs.get('status')} | Lowest: {lowest_str} | Cards: {len(obs.get('cards', []))} | Saved: {fname}")

            # Polite throttling delay
            if idx < len(tasks):
                await asyncio.sleep(delay_seconds)

        await context.close()

    print("\n" + "=" * 80)
    print("REGIONAL STUDY SCAN COMPLETE!")
    print(f"Total: {len(tasks)} | New Observed: {completed_count} | Cached: {skipped_count} | Incomplete/Error: {error_count}")
    print(f"Raw data saved to: {results_dir}")
    print("=" * 80)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Multi-Region Flight Scanner")
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--delay", type=float, default=2.5)
    args = parser.parse_args()
    asyncio.run(main_scanner(headless=args.headless, delay_seconds=args.delay))
