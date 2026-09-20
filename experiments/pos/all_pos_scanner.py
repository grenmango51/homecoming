#!/usr/bin/env python3
"""Exhaustive global point-of-sale (POS) scanner for Google Flights.

Scans every Google Flights POS region for the date pairs of the active trip
config, saves one JSON record per query, and keeps a live global arbitrage
summary (CSV and JSON) up to date as it goes.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import sys
from pathlib import Path
from typing import Any

try:
    from playwright.async_api import Page, async_playwright
except ImportError as error:
    raise SystemExit(
        "Playwright is required. Run: python -m pip install -r requirements.txt"
    ) from error

from src import browser, reporting
from src.common import configure_stdio, resolve_browser_executable
from src.config import PROJECT_ROOT, load_config
from src.google_parse import (
    build_route_url,
    is_blocked,
    parse_card_details,
    reported_location,
)
from src.google_parse import (
    tab_price as parse_tab_price,
)
from src.regions import load_all_regions

ROOT = PROJECT_ROOT
DEFAULT_PROFILE_DIR = ROOT / ".browser-profile"
ALL_POS_RESULTS_DIR = ROOT / "flight_results_all_pos"
ACTIVE_MARKETS_FILE = ALL_POS_RESULTS_DIR / "active_market_gl_codes.json"
SUMMARY_STEM = "all_pos_summary_report"
SOURCE = "Google Flights All-POS Scanner"

# Google streams results in after first paint; poll until the Cheapest tab
# resolves to a fare and cards exist, or the budget runs out.
HYDRATION_ATTEMPTS = 12
HYDRATION_INTERVAL_MS = 1_000

# How often the running summary is flushed to disk mid-pair.
SUMMARY_FLUSH_EVERY = 10


async def extract_candidate_cards(page: Page, curr: str = "EUR") -> list[dict[str, Any]]:
    """Parse the visible flight cards with the multi-currency price parser."""
    return await browser.extract_cards(
        page,
        curr=curr,
        selector=browser.DEFAULT_CARD_SELECTOR,
        min_length=25,
        key_length=80,
        limit=30,
        scroll_steps=0,
        parser=parse_card_details,
    )


async def _cheapest_tab_price(page: Page) -> float | None:
    """Fare shown on the Cheapest tab header, or None while it is still loading."""
    return parse_tab_price(await browser.read_cheapest_tab_text(page))


async def scan_single_target(
    page: Page,
    *,
    origin: str,
    destination: str,
    departure: dt.date,
    return_date: dt.date | None,
    gl: str,
    curr: str = "EUR",
    timeout_ms: int = 35_000,
) -> dict[str, Any]:
    """Run one route/date/POS query and return its observation."""
    url = build_route_url(origin, destination, departure, return_date, gl=gl, curr=curr)
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    except Exception:
        await page.goto(url, timeout=timeout_ms)

    await page.wait_for_timeout(1_500)
    if await browser.dismiss_consent(page, target_url=url):
        await page.wait_for_timeout(1_000)

    page_text = await browser.page_text(page)
    if "something went wrong" in page_text.lower():
        if await browser.click_reload_if_present(page):
            await page.wait_for_timeout(3_000)

    # Wait for Google's async result streaming to settle.
    for _ in range(HYDRATION_ATTEMPTS):
        await page.wait_for_timeout(HYDRATION_INTERVAL_MS)
        page_text = await browser.page_text(page)
        if is_blocked(page_text):
            break
        tab_text = await browser.read_cheapest_tab_text(page)
        card_count = await page.locator("ul.Rk10dc > li, li.pIav2d, [role='listitem']").count()
        if "fetching results" not in tab_text.lower() and card_count > 0 and "€" in tab_text:
            break

    cards = await extract_candidate_cards(page, curr=curr)
    page_text = await browser.page_text(page)
    tab_price = await _cheapest_tab_price(page)

    card_prices = [card["lowest_price"] for card in cards if card.get("lowest_price") is not None]
    lowest_price = min(card_prices) if card_prices else None
    if tab_price is not None and (lowest_price is None or tab_price < lowest_price):
        lowest_price = tab_price

    if is_blocked(page_text):
        status = "blocked"
    elif cards or tab_price:
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
        "return_date": return_date.isoformat() if return_date else "oneway",
        "stay_nights": (return_date - departure).days if return_date else 0,
        "gl": gl,
        "currency": curr,
        "status": status,
        "lowest_price_eur": lowest_price,
        "cheapest_tab_price": tab_price,
        "reported_location": reported_location(page_text),
        "num_candidates": len(cards),
        "cards": cards,
        "page_url": page.url,
    }


def _empty_observation(
    origin: str, destination: str, departure: dt.date, return_date: dt.date | None, gl: str
) -> dict[str, Any]:
    """Placeholder recorded when every attempt for a query raised."""
    return {
        "fetched_at": reporting.utc_now(),
        "source": SOURCE,
        "origin": origin,
        "destination": destination,
        "departure_date": departure.isoformat(),
        "return_date": return_date.isoformat() if return_date else "oneway",
        "stay_nights": (return_date - departure).days if return_date else 0,
        "gl": gl,
        "currency": "EUR",
        "status": "error",
        "lowest_price_eur": None,
        "cards": [],
    }


def resolve_results_dir(origin: str, dest: str, custom_regions: bool) -> Path:
    """The canonical HEL->HAN study keeps its own directory; other routes get one each."""
    if origin == "HEL" and dest == "HAN" and not custom_regions:
        return ALL_POS_RESULTS_DIR
    return ROOT / f"flight_results_{origin}_{dest}"


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
    origin, dest = cfg.trip.origin, cfg.trip.dest

    # get_search_pairs honours every date_mode (exact / range / window) and
    # one-way trips; deriving pairs by hand here previously ignored exact mode.
    pairs = cfg.trip.get_search_pairs()
    if max_pairs:
        pairs = pairs[:max_pairs]
    if not pairs:
        raise SystemExit(f"No date pairs generated for {config_file}; check its trip settings.")

    regions = load_all_regions(regions_file)
    results_dir = resolve_results_dir(origin, dest, custom_regions=regions_file is not None)
    results_dir.mkdir(parents=True, exist_ok=True)
    DEFAULT_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    total_tasks = len(pairs) * len(regions)
    print("=" * 80)
    print("STARTING EXHAUSTIVE ALL-POS GOOGLE FLIGHTS REGIONAL SCAN")
    print(f"Route: {origin} -> {dest}")
    print(f"Date pairs: {len(pairs)} ({cfg.trip.date_mode} mode, {cfg.trip.trip_type})")
    print(f"Available Regions: {len(regions)} sovereign markets / gl codes")
    print(f"Total Queries: {total_tasks}")
    print(f"Results Directory: {results_dir}")
    print(f"Throttling Delay: {delay_seconds}s | Headless: {headless}")
    print("=" * 80)

    summary_records = reporting.load_summary_records(results_dir, SUMMARY_STEM)

    seen_keys = {
        (r["origin"], r["destination"], r["departure_date"], r["return_date"], r["gl"])
        for r in summary_records
        if r.get("status") == "observed"
    }

    browser_executable = resolve_browser_executable()
    task_num = observed_count = cached_count = error_count = 0

    async with async_playwright() as playwright:
        context = await browser.launch_google_context(
            playwright, DEFAULT_PROFILE_DIR, headless=headless, executable_path=browser_executable
        )
        page = context.pages[0] if context.pages else await context.new_page()

        try:
            for pair_index, (dep, ret) in enumerate(pairs, start=1):
                ret_str = ret.isoformat() if ret else "oneway"
                stay_label = f"{(ret - dep).days} nights" if ret else "one-way"
                print(
                    f"\n[{pair_index}/{len(pairs)}] >>> Scanning Date Pair: "
                    f"{dep.isoformat()} -> {ret_str} (Stay: {stay_label}) <<<",
                    flush=True,
                )

                pair_prices: list[tuple[float, str, str]] = []

                for region in regions:
                    task_num += 1
                    gl, region_name = region["gl"], region["name"]
                    key = (origin, dest, dep.isoformat(), ret_str, gl)
                    checkpoint = results_dir / reporting.checkpoint_filename(origin, dest, dep, ret, gl)

                    cached = reporting.load_checkpoint(checkpoint)
                    if cached and cached.get("status") == "observed" and cached.get("lowest_price_eur"):
                        cached_count += 1
                        price = cached["lowest_price_eur"]
                        pair_prices.append((price, gl, region_name))
                        print(f"  [{task_num}/{total_tasks}] [CACHED] {gl} ({region_name[:15]}): €{price}")
                        if key not in seen_keys:
                            summary_records.append(
                                reporting.summary_record(
                                    cached,
                                    origin=origin, destination=dest, departure=dep, return_date=ret,
                                    gl=gl, region_name=region_name, status="observed", price_eur=price,
                                )
                            )
                            seen_keys.add(key)
                        continue

                    observation = None
                    for attempt in range(1, max_retries + 1):
                        try:
                            observation = await scan_single_target(
                                page,
                                origin=origin, destination=dest, departure=dep,
                                return_date=ret, gl=gl, curr="EUR",
                            )
                            if observation["status"] == "observed" and observation["cards"]:
                                break
                        except Exception as error:
                            print(f"    attempt {attempt} failed: {error}", file=sys.stderr)
                        if attempt < max_retries:
                            await asyncio.sleep(1.5)

                    if observation is None:
                        observation = _empty_observation(origin, dest, dep, ret, gl)
                        error_count += 1
                    elif observation["status"] == "observed":
                        observed_count += 1
                    else:
                        error_count += 1

                    reporting.write_json(checkpoint, observation)
                    summary_records.append(
                        reporting.summary_record(
                            observation,
                            origin=origin, destination=dest, departure=dep, return_date=ret,
                            gl=gl, region_name=region_name,
                        )
                    )
                    seen_keys.add(key)

                    price = observation.get("lowest_price_eur")
                    if price:
                        pair_prices.append((price, gl, region_name))
                    print(
                        f"  [{task_num}/{total_tasks}] [LIVE] {gl} ({region_name[:15]}): "
                        f"{f'€{price}' if price else 'N/A'} ({len(observation.get('cards', []))} cards)",
                        flush=True,
                    )

                    if task_num % SUMMARY_FLUSH_EVERY == 0:
                        reporting.write_summary_reports(results_dir, summary_records, SUMMARY_STEM)
                    await asyncio.sleep(delay_seconds)

                if pair_prices:
                    cheapest = min(pair_prices, key=lambda item: item[0])
                    priciest = max(pair_prices, key=lambda item: item[0])
                    print(
                        f"  >>> Pair Summary: Lowest = €{cheapest[0]} ({cheapest[1]}: {cheapest[2]}) "
                        f"| Highest = €{priciest[0]} ({priciest[1]}) "
                        f"| Spread = €{priciest[0] - cheapest[0]} <<<",
                        flush=True,
                    )

                reporting.write_summary_reports(results_dir, summary_records, SUMMARY_STEM)
        finally:
            await context.close()

    final_json, final_csv = reporting.write_summary_reports(results_dir, summary_records, SUMMARY_STEM)
    print("\n" + "=" * 80, flush=True)
    print(f"ALL-POS GLOBAL SCAN COMPLETED FOR {origin} -> {dest}!", flush=True)
    print(
        f"Total Processed: {task_num} | Observed: {observed_count} "
        f"| Cached: {cached_count} | Error/Empty: {error_count}",
        flush=True,
    )
    print(f"Final Summary Reports saved to:\n  {final_json}\n  {final_csv}", flush=True)
    print("=" * 80, flush=True)

    if results_dir == ALL_POS_RESULTS_DIR:
        print("\n>>> EXECUTING STATISTICAL MODE-EXCLUSION FILTER & ACTIVE ARBITRAGE ANALYSIS <<<", flush=True)
        try:
            from src.filtered_arbitrage_analyzer import run_mode_filtered_analysis

            run_mode_filtered_analysis()
        except Exception as error:
            print(f"Error during mode filter analysis: {error}", flush=True)

    if auto_chain_trip:
        print("\n" + "=" * 80, flush=True)
        print(f">>> AUTO-CHAINING INTO TEST RUN: {auto_chain_trip} <<<", flush=True)
        print(f"Using Vetted Active Markets: {ACTIVE_MARKETS_FILE}", flush=True)
        print("=" * 80 + "\n", flush=True)
        await run_all_pos_scanner(
            config_file=auto_chain_trip,
            headless=headless,
            delay_seconds=delay_seconds,
            max_retries=max_retries,
            regions_file=ACTIVE_MARKETS_FILE if ACTIVE_MARKETS_FILE.exists() else None,
            auto_chain_trip=None,
        )


def main() -> None:
    configure_stdio()
    parser = argparse.ArgumentParser(description="All-POS Google Flights Scanner")
    parser.add_argument("--config", default="HEL_HAN.toml", help="Trip configuration filename in config/")
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--delay", type=float, default=0.0, help="Delay between queries in seconds")
    parser.add_argument("--max-pairs", type=int, default=None, help="Optional limit on number of date pairs")
    parser.add_argument("--regions-file", default=None, help="Optional path to custom regions JSON file")
    parser.add_argument("--auto-chain-trip", default=None, help="Trip config to run after this scan completes")
    args = parser.parse_args()
    asyncio.run(
        run_all_pos_scanner(
            config_file=args.config,
            headless=args.headless,
            delay_seconds=args.delay,
            max_pairs=args.max_pairs,
            regions_file=args.regions_file,
            auto_chain_trip=args.auto_chain_trip,
        )
    )


if __name__ == "__main__":
    main()
