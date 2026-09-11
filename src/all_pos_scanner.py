#!/usr/bin/env python3
"""Exhaustive Global Point of Sale (POS) Scanner for Google Flights.

Scans all 186 official Google Flights geographic regions for the date pairs
configured in config/HEL_HAN.toml, saves individual JSON records, and maintains
live updated global arbitrage summaries (CSV and JSON).
"""

from __future__ import annotations

import argparse
import asyncio
import csv
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
    build_search_pairs,
    resolve_browser_executable,
    duration_minutes,
)
from src.config import PROJECT_ROOT, load_config
from src.regional_study_scanner import (
    build_regional_url,
    wait_for_flight_results,
    click_reload_if_present,
    switch_cheapest,
    parse_card_details,
)

import base64

ROOT = PROJECT_ROOT
DEFAULT_PROFILE_DIR = ROOT / ".browser-profile"
ALL_POS_RESULTS_DIR = ROOT / "flight_results_all_pos"
REGIONS_MAPPING_FILE = ROOT / "all_available_regions_mapped.json"

USER_TFS_TEMPLATE = "CBwQAhojEgoyMDI2LTEyLTA5agwIAhIIL20vMDNraG5yBwgBEgNIQU4aIxIKMjAyNy0wMS0wNWoHCAESA0hBTnIMCAISCC9tLzAza2huQAFIAXABggELCP___________wGYAQE"

def build_structured_flight_url(dep: dt.date, ret: dt.date, gl: str = "FI") -> str:
    dep_bytes = dep.isoformat().encode()
    ret_bytes = ret.isoformat().encode()
    pad = '=' * (-len(USER_TFS_TEMPLATE) % 4)
    raw = base64.urlsafe_b64decode(USER_TFS_TEMPLATE + pad)
    raw_new = raw.replace(b'2026-12-09', dep_bytes).replace(b'2027-01-05', ret_bytes)
    tfs = base64.urlsafe_b64encode(raw_new).decode().rstrip('=')
    return f"https://www.google.com/travel/flights/search?tfs={tfs}&tfu=EgoIABAAGAAgAigB&hl=en&gl={gl}&curr=EUR"


def build_route_url(origin: str, dest: str, dep: dt.date, ret: dt.date | None, gl: str = "FI", curr: str = "EUR") -> str:
    orig = origin.strip().upper()
    dst = dest.strip().upper()
    if ret is None:
        return f"https://www.google.com/travel/flights?q=Flights%20to%20{dst}%20from%20{orig}%20on%20{dep.isoformat()}%20one%20way&tfu=EgoIABAAGAAgAigB&hl=en&gl={gl}&curr={curr}"
    if orig == "HEL" and dst == "HAN":
        return build_structured_flight_url(dep, ret, gl=gl)
    return f"https://www.google.com/travel/flights?q=Flights%20to%20{dst}%20from%20{orig}%20on%20{dep.isoformat()}%20through%20{ret.isoformat()}&tfu=EgoIABAAGAAgAigB&hl=en&gl={gl}&curr={curr}"


def load_all_regions(regions_file: Path | None = None) -> list[dict[str, str]]:
    target_file = regions_file or REGIONS_MAPPING_FILE
    if not target_file.exists():
        raise FileNotFoundError(f"Missing {target_file}")
    data = json.loads(target_file.read_text(encoding="utf-8"))
    return [{"gl": item["gl"], "name": item.get("name", item["gl"])} for item in data if "gl" in item]


def get_pos_checkpoint_filename(origin: str, dest: str, dep: dt.date, ret: dt.date | None, gl: str) -> str:
    ret_str = ret.isoformat() if ret else "oneway"
    return f"{origin}_{dest}_{dep.isoformat()}_{ret_str}_gl-{gl}.json"


def update_summary_reports(
    results_dir: Path,
    summary_data: list[dict[str, Any]]
) -> tuple[Path, Path]:
    results_dir.mkdir(parents=True, exist_ok=True)
    json_path = results_dir / "all_pos_summary_report.json"
    csv_path = results_dir / "all_pos_summary_report.csv"

    # Write JSON
    json_path.write_text(json.dumps(summary_data, ensure_ascii=False, indent=2), encoding="utf-8")

    # Write CSV
    if summary_data:
        fields = [
            "origin", "destination", "departure_date", "return_date", "stay_nights",
            "gl", "region_name", "status", "lowest_price_eur", "carrier",
            "duration_minutes", "stops", "layovers", "fetched_at"
        ]
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(summary_data)

    return json_path, csv_path


async def handle_consent_if_needed(page: Page, target_url: str) -> None:
    if "consent.google.com" in page.url or await page.locator("button:has-text('Reject all'), button:has-text('Accept all')").is_visible():
        try:
            btn = page.locator("button:has-text('Reject all'), button:has-text('Accept all')").first
            if await btn.count() > 0 and await btn.is_visible():
                await btn.click()
                try:
                    await page.wait_for_url(lambda u: "travel/flights" in u, timeout=10000)
                except Exception:
                    await page.goto(target_url, wait_until="domcontentloaded")
        except Exception:
            pass
    await page.wait_for_timeout(1000)


async def extract_clean_candidate_cards(page: Page, curr: str = "EUR") -> list[dict[str, Any]]:
    # Evaluate flight card elements directly from Top and Other departing flights
    # Crucially DO NOT click "view more flights", which collapses Top Departing Flights
    raw_texts = await page.evaluate(
        """() => {
            const els = document.querySelectorAll("ul.Rk10dc > li, li.pIav2d");
            return Array.from(els).map(e => e.innerText || '');
        }"""
    )
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
            key = compact[:80].lower()
            if key not in seen:
                seen.add(key)
                candidates.append(card)
    return candidates[:30]


async def scan_single_target(
    page: Page,
    *,
    origin: str,
    destination: str,
    departure: dt.date,
    return_date: dt.date,
    gl: str,
    curr: str = "EUR",
    timeout_ms: int = 35000,
) -> dict[str, Any]:
    url = build_route_url(origin, destination, departure, return_date, gl=gl, curr=curr)
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    except Exception:
        await page.goto(url, timeout=timeout_ms)

    await page.wait_for_timeout(1500)
    await handle_consent_if_needed(page, url)

    # Check reload if needed
    page_text = await page.locator("body").inner_text()
    if "something went wrong" in page_text.lower():
        reloaded = await click_reload_if_present(page)
        if reloaded:
            await page.wait_for_timeout(3000)

    # Wait for Google Flights async streaming to complete
    tab_price = None
    for _ in range(12):
        await page.wait_for_timeout(1000)
        body_text = await page.locator("body").inner_text()
        if "unusual traffic" in body_text.lower() or "captcha" in body_text.lower():
            break

        cheapest_tab = page.locator("[role='tab']:has-text('Cheapest')")
        tab_txt = await cheapest_tab.first.inner_text() if await cheapest_tab.count() > 0 else ""
        cards_count = await page.locator("ul.Rk10dc > li, li.pIav2d, [role='listitem']").count()

        # If Cheapest tab has resolved and cards are present, stop waiting
        if "fetching results" not in tab_txt.lower() and cards_count > 0 and "€" in tab_txt:
            break

    cards = await extract_clean_candidate_cards(page, curr=curr)
    page_text = await page.locator("body").inner_text()

    # Footer location
    footer_location = "Unknown"
    loc_match = re.search(r"Location\s*([^\n\r]+)", page_text)
    if loc_match:
        footer_location = loc_match.group(1).strip()

    # Check cheapest tab header text
    try:
        cheapest_tab = page.locator("[role='tab']:has-text('Cheapest')").first
        if await cheapest_tab.count() > 0:
            tab_txt = await cheapest_tab.inner_text()
            m = re.search(r"€\s*([\d,]+)", tab_txt)
            if m:
                tab_price = float(m.group(1).replace(",", ""))
    except Exception:
        pass

    lowest_price = None
    all_prices = [c["lowest_price"] for c in cards if c.get("lowest_price") is not None]
    if all_prices:
        lowest_price = min(all_prices)
    if tab_price is not None:
        if lowest_price is None or tab_price < lowest_price:
            lowest_price = tab_price

    status = "observed" if (cards or tab_price) else ("incomplete" if "loading" in page_text.lower() else "no_results")
    if any(m in page_text.lower() for m in ("unusual traffic", "captcha", "verify you are human")):
        status = "blocked"

    return {
        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source": "Google Flights All-POS Scanner",
        "origin": origin,
        "destination": destination,
        "departure_date": departure.isoformat(),
        "return_date": return_date.isoformat(),
        "stay_nights": (return_date - departure).days,
        "gl": gl,
        "currency": curr,
        "status": status,
        "lowest_price_eur": lowest_price,
        "cheapest_tab_price": tab_price,
        "reported_location": footer_location,
        "num_candidates": len(cards),
        "cards": cards,
        "page_url": page.url,
    }


async def run_all_pos_scanner(
    config_file: str = "HEL_HAN.toml",
    headless: bool = True,
    delay_seconds: float = 0.0,
    max_retries: int = 2,
    max_pairs: int | None = None,
    regions_file: str | Path | None = None,
    auto_chain_trip: str | None = None,
) -> None:
    cfg = load_config(trip_path=config_file)
    origin = cfg.trip.origin
    dest = cfg.trip.dest

    if cfg.trip.date_mode == "range":
        pairs = build_search_pairs(
            depart_from=cfg.trip.depart_from,
            depart_to=cfg.trip.depart_to,
            return_from=cfg.trip.return_from,
            return_to=cfg.trip.return_to,
            min_stay_nights=cfg.trip.min_stay_nights,
        )
    else:
        pairs = build_search_pairs(
            window_start=cfg.trip.window_start,
            window_end=cfg.trip.window_end,
            min_stay_nights=cfg.trip.min_stay_nights,
        )

    if max_pairs:
        pairs = pairs[:max_pairs]

    target_reg_path = Path(regions_file) if regions_file else None
    regions = load_all_regions(target_reg_path)

    if origin == "HEL" and dest == "HAN" and regions_file is None:
        results_dir = ALL_POS_RESULTS_DIR
    else:
        results_dir = ROOT / f"flight_results_{origin}_{dest}"

    profile_dir = DEFAULT_PROFILE_DIR
    results_dir.mkdir(parents=True, exist_ok=True)
    profile_dir.mkdir(parents=True, exist_ok=True)

    total_tasks = len(pairs) * len(regions)
    print("=" * 80)
    print("STARTING EXHAUSTIVE ALL-POS GOOGLE FLIGHTS REGIONAL SCAN")
    print(f"Route: {origin} -> {dest}")
    print(f"Date pairs: {len(pairs)} pairs (from {cfg.trip.depart_from} to {cfg.trip.return_to})")
    print(f"Available Regions: {len(regions)} sovereign markets / gl codes")
    print(f"Total Queries: {total_tasks}")
    print(f"Results Directory: {results_dir}")
    print(f"Throttling Delay: {delay_seconds}s | Headless: {headless}")
    print("=" * 80)

    # Load existing summaries if present
    summary_records: list[dict[str, Any]] = []
    summary_json_path = results_dir / "all_pos_summary_report.json"
    if summary_json_path.exists():
        try:
            summary_records = json.loads(summary_json_path.read_text(encoding="utf-8"))
        except Exception:
            summary_records = []

    seen_keys = {
        (r["origin"], r["destination"], r["departure_date"], r["return_date"], r["gl"])
        for r in summary_records if r.get("status") == "observed"
    }

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
        try:
            await context.add_cookies([
                {"name": "SOCS", "value": "CAESEwgDEgk1ODEzNzI3NDQaAmVuIAEaBgiAo_mwBg", "domain": ".google.com", "path": "/"},
                {"name": "CONSENT", "value": "PENDING+999", "domain": ".google.com", "path": "/"},
            ])
        except Exception:
            pass
        page = context.pages[0] if context.pages else await context.new_page()

        task_num = 0
        observed_count = 0
        cached_count = 0
        error_count = 0

        # Scan ordered by Date Pair, then by Region
        for pair_idx, (dep, ret) in enumerate(pairs, start=1):
            ret_str = ret.isoformat() if ret else "oneway"
            stay_nights = (ret - dep).days if ret else 0
            pair_str = f"{dep.isoformat()} -> {ret_str}"
            stay_label = f"{stay_nights} nights" if ret else "one-way"
            print(f"\n[{pair_idx}/{len(pairs)}] >>> Scanning Date Pair: {pair_str} (Stay: {stay_label}) <<<", flush=True)

            pair_observed_prices = []

            for reg_idx, reg in enumerate(regions, start=1):
                task_num += 1
                gl = reg["gl"]
                reg_name = reg["name"]
                key = (origin, dest, dep.isoformat(), ret_str, gl)

                fname = get_pos_checkpoint_filename(origin, dest, dep, ret, gl)
                fpath = results_dir / fname

                # Check cached file
                if fpath.exists():
                    try:
                        cached_obs = json.loads(fpath.read_text(encoding="utf-8"))
                        if cached_obs.get("status") == "observed" and cached_obs.get("lowest_price_eur"):
                            cached_count += 1
                            price_eur = cached_obs.get("lowest_price_eur")
                            pair_observed_prices.append((price_eur, gl, reg_name))
                            print(f"  [{task_num}/{total_tasks}] [CACHED] {gl} ({reg_name[:15]}): €{price_eur}")
                            if key not in seen_keys:
                                top_c = cached_obs.get("cards", [{}])[0]
                                summary_records.append({
                                    "origin": origin,
                                    "destination": dest,
                                    "departure_date": dep.isoformat(),
                                    "return_date": ret_str,
                                    "stay_nights": stay_nights,
                                    "gl": gl,
                                    "region_name": reg_name,
                                    "status": "observed",
                                    "lowest_price_eur": price_eur,
                                    "carrier": ", ".join(top_c.get("carriers", [])) or "Unknown",
                                    "duration_minutes": top_c.get("duration_minutes"),
                                    "stops": top_c.get("stops"),
                                    "layovers": "/".join(top_c.get("layovers", [])),
                                    "fetched_at": cached_obs.get("fetched_at"),
                                })
                                seen_keys.add(key)
                            continue
                    except Exception:
                        pass

                # Live query
                obs = None
                for attempt in range(1, max_retries + 1):
                    try:
                        obs = await scan_single_target(
                            page,
                            origin=origin,
                            destination=dest,
                            departure=dep,
                            return_date=ret,
                            gl=gl,
                            curr="EUR",
                        )
                        if obs["status"] == "observed" and obs["cards"]:
                            break
                        elif attempt < max_retries:
                            await asyncio.sleep(1.5)
                    except Exception as err:
                        if attempt < max_retries:
                            await asyncio.sleep(1.5)

                if obs is None:
                    obs = {
                        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                        "source": "Google Flights All-POS Scanner",
                        "origin": origin,
                        "destination": dest,
                        "departure_date": dep.isoformat(),
                        "return_date": ret.isoformat(),
                        "stay_nights": (ret - dep).days,
                        "gl": gl,
                        "currency": "EUR",
                        "status": "error",
                        "lowest_price_eur": None,
                        "cards": [],
                    }
                    error_count += 1
                elif obs["status"] == "observed":
                    observed_count += 1
                else:
                    error_count += 1

                # Save raw JSON
                fpath.write_text(json.dumps(obs, ensure_ascii=False, indent=2), encoding="utf-8")

                # Record in summary
                price_eur = obs.get("lowest_price_eur")
                top_c = obs.get("cards", [{}])[0] if obs.get("cards") else {}
                summary_records.append({
                    "origin": origin,
                    "destination": dest,
                    "departure_date": dep.isoformat(),
                    "return_date": ret.isoformat(),
                    "stay_nights": (ret - dep).days,
                    "gl": gl,
                    "region_name": reg_name,
                    "status": obs.get("status"),
                    "lowest_price_eur": price_eur,
                    "carrier": ", ".join(top_c.get("carriers", [])) or "Unknown",
                    "duration_minutes": top_c.get("duration_minutes"),
                    "stops": top_c.get("stops"),
                    "layovers": "/".join(top_c.get("layovers", [])),
                    "fetched_at": obs.get("fetched_at"),
                })
                seen_keys.add(key)

                if price_eur:
                    pair_observed_prices.append((price_eur, gl, reg_name))
                price_str = f"€{price_eur}" if price_eur else "N/A"
                print(f"  [{task_num}/{total_tasks}] [LIVE] {gl} ({reg_name[:15]}): {price_str} ({len(obs.get('cards', []))} cards)", flush=True)

                # Periodic report update every 10 queries
                if task_num % 10 == 0:
                    update_summary_reports(results_dir, summary_records)

                # Polite throttling delay
                await asyncio.sleep(delay_seconds)

            # End of pair summary
            if pair_observed_prices:
                cheapest_in_pair = min(pair_observed_prices, key=lambda x: x[0])
                highest_in_pair = max(pair_observed_prices, key=lambda x: x[0])
                spread = highest_in_pair[0] - cheapest_in_pair[0]
                print(f"  >>> Pair Summary: Lowest = €{cheapest_in_pair[0]} ({cheapest_in_pair[1]}: {cheapest_in_pair[2]}) | Highest = €{highest_in_pair[0]} ({highest_in_pair[1]}) | Spread = €{spread} <<<", flush=True)

            # Update report after each date pair
            update_summary_reports(results_dir, summary_records)

        await context.close()

    final_json, final_csv = update_summary_reports(results_dir, summary_records)
    print("\n" + "=" * 80, flush=True)
    print(f"ALL-POS GLOBAL SCAN COMPLETED FOR {origin} -> {dest}!", flush=True)
    print(f"Total Processed: {task_num} | Observed: {observed_count} | Cached: {cached_count} | Error/Empty: {error_count}", flush=True)
    print(f"Final Summary Reports saved to:\n  {final_json}\n  {final_csv}", flush=True)
    print("=" * 80, flush=True)

    # Automatically run statistical mode filter & report if this was HEL -> HAN
    if origin == "HEL" and dest == "HAN" and regions_file is None:
        print("\n>>> EXECUTING STATISTICAL MODE-EXCLUSION FILTER & ACTIVE ARBITRAGE ANALYSIS <<<", flush=True)
        try:
            from src.filtered_arbitrage_analyzer import run_mode_filtered_analysis
            run_mode_filtered_analysis()
        except Exception as ex:
            print(f"Error during mode filter analysis: {ex}", flush=True)

    # Auto-chain into follow-up trip (e.g. PHL_HAN.toml) with vetted active markets
    if auto_chain_trip:
        vetted_regions_file = ALL_POS_RESULTS_DIR / "active_market_gl_codes.json"
        print(f"\n" + "=" * 80, flush=True)
        print(f">>> AUTO-CHAINING INTO TEST RUN: {auto_chain_trip} <<<", flush=True)
        print(f"Using Vetted Active Markets: {vetted_regions_file}", flush=True)
        print("=" * 80 + "\n", flush=True)
        await run_all_pos_scanner(
            config_file=auto_chain_trip,
            headless=headless,
            delay_seconds=delay_seconds,
            max_retries=max_retries,
            regions_file=vetted_regions_file if vetted_regions_file.exists() else None,
            auto_chain_trip=None,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="All-POS Google Flights Scanner")
    parser.add_argument("--config", default="HEL_HAN.toml", help="Trip configuration filename in config/")
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--delay", type=float, default=0.0, help="Delay between queries (0.0 uses natural Google Flights hydration)")
    parser.add_argument("--max-pairs", type=int, default=None, help="Optional limit on number of date pairs")
    parser.add_argument("--regions-file", default=None, help="Optional path to custom regions JSON file")
    parser.add_argument("--auto-chain-trip", default=None, help="Optional trip config to automatically run after this scan completes")
    args = parser.parse_args()
    asyncio.run(run_all_pos_scanner(
        config_file=args.config,
        headless=args.headless,
        delay_seconds=args.delay,
        max_pairs=args.max_pairs,
        regions_file=args.regions_file,
        auto_chain_trip=args.auto_chain_trip,
    ))
