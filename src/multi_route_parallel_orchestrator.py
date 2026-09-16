#!/usr/bin/env python3
"""Multi-route parallel orchestrator for Google Flights POS arbitrage.

Runs a point-of-sale scan for several routes concurrently, each in its own
isolated browser context so the persistent profiles never contend for a lock,
then writes a per-route arbitrage report.

Routes come from the trip configs listed in ``ROUTES``; the market list is the
vetted "active" set produced by :mod:`src.filtered_arbitrage_analyzer`, falling
back to every known region.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import re
import sys
from pathlib import Path
from typing import Any

import pandas as pd
from tabulate import tabulate

try:
    from playwright.async_api import Page, async_playwright
except ImportError as error:
    raise SystemExit(
        "Playwright is required. Run: python -m pip install -r requirements.txt"
    ) from error

from src import browser, reporting
from src.all_pos_scanner import ACTIVE_MARKETS_FILE, extract_candidate_cards
from src.common import resolve_browser_executable
from src.config import PROJECT_ROOT, load_config
from src.google_parse import build_route_url
from src.google_parse import tab_price as parse_tab_price
from src.regions import load_all_regions

ROOT = PROJECT_ROOT
SOURCE = "Multi-Route POS Scanner"
SUMMARY_STEM = "summary_report"

# Poll while Google streams results in.
HYDRATION_ATTEMPTS = 10
HYDRATION_INTERVAL_MS = 800
LOADING_RE = re.compile(r"loading results|fetching results", re.IGNORECASE)

# Each route: trip config plus the market that counts as "booking at home".
ROUTES = [
    {"trip": "PHL_HAN.toml", "domestic_gl": "US", "label": "Philadelphia (PHL) -> Hanoi (HAN) [Round-Trip]"},
    {"trip": "HEL_BRU.toml", "domestic_gl": "FI", "label": "Helsinki (HEL) -> Brussels (BRU) [One-Way]"},
    {"trip": "AMS_HEL.toml", "domestic_gl": "NL", "label": "Amsterdam (AMS) -> Helsinki (HEL) [One-Way]"},
    {"trip": "SIN_HAN.toml", "domestic_gl": "SG", "label": "Singapore (SIN) -> Hanoi (HAN) [Round-Trip]"},
]

FALLBACK_MARKETS = [
    {"gl": "US", "name": "United States"},
    {"gl": "FI", "name": "Finland"},
    {"gl": "VN", "name": "Vietnam"},
]


def load_target_markets() -> list[dict[str, str]]:
    """Vetted active markets, or every known region if the filter has not run yet."""
    vetted = reporting.read_json(ACTIVE_MARKETS_FILE, default=None)
    if vetted:
        print(f"[Orchestrator] Loaded {len(vetted)} vetted active dynamic markets from {ACTIVE_MARKETS_FILE.name}")
        return vetted
    try:
        regions = load_all_regions()
    except FileNotFoundError:
        return FALLBACK_MARKETS
    print(f"[Orchestrator] Loaded {len(regions)} markets from the full region mapping")
    return regions


async def scan_single_target_query(
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

    await page.wait_for_timeout(1_000)
    await browser.dismiss_consent(page, target_url=url)

    page_text = await browser.page_text(page)
    if "something went wrong" in page_text.lower():
        if await browser.click_reload_if_present(page):
            await page.wait_for_timeout(2_500)

    await browser.wait_for_hydration(
        page,
        attempts=HYDRATION_ATTEMPTS,
        interval_ms=HYDRATION_INTERVAL_MS,
        ready=lambda text: not LOADING_RE.search(text),
    )

    cards = await extract_candidate_cards(page, curr=curr)
    prices: list[float] = []
    for card in cards:
        if card.get("lowest_price") is not None:
            prices.append(card["lowest_price"])
        prices.extend(card.get("observed_prices", []))

    tab_price = parse_tab_price(await browser.read_cheapest_tab_text(page))
    lowest_price = min(prices) if prices else None
    if tab_price is not None and (lowest_price is None or tab_price < lowest_price):
        lowest_price = tab_price

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
        "status": "observed" if lowest_price else "incomplete",
        "lowest_price_eur": lowest_price,
        "num_candidates": len(cards),
        "cards": cards,
        "page_url": page.url,
    }


async def scan_route(
    trip_config_file: str,
    regions: list[dict[str, str]],
    delay_seconds: float = 0.0,
    headless: bool = False,
    max_retries: int = 2,
) -> Path:
    """Scan every (date pair x market) combination for one route."""
    cfg = load_config(trip_path=trip_config_file)
    origin, dest = cfg.trip.origin, cfg.trip.dest
    pairs = cfg.trip.get_search_pairs()

    route_tag = f"{origin}_{dest}"
    results_dir = ROOT / f"flight_results_{route_tag}"
    profile_dir = ROOT / f".browser-profile-{route_tag}"
    results_dir.mkdir(parents=True, exist_ok=True)
    profile_dir.mkdir(parents=True, exist_ok=True)

    total_tasks = len(pairs) * len(regions)
    print(f"[{route_tag}] Starting scan: {len(pairs)} pairs x {len(regions)} regions ({total_tasks} queries)")

    summary_records = reporting.load_summary_records(results_dir, SUMMARY_STEM)
    seen_keys = {
        (r["origin"], r["destination"], r["departure_date"], r["return_date"], r["gl"])
        for r in summary_records
        if r.get("status") == "observed"
    }

    browser_executable = resolve_browser_executable()
    task_num = 0

    async with async_playwright() as playwright:
        context = await browser.launch_google_context(
            playwright,
            profile_dir,
            headless=headless,
            executable_path=browser_executable,
            viewport={"width": 1400, "height": 950},
        )
        page = context.pages[0] if context.pages else await context.new_page()

        try:
            for pair_index, (dep, ret) in enumerate(pairs, start=1):
                ret_str = ret.isoformat() if ret else "oneway"
                print(f"\n[{route_tag}] [{pair_index}/{len(pairs)}] >>> Scanning: {dep.isoformat()} -> {ret_str} <<<", flush=True)

                for region in regions:
                    task_num += 1
                    gl = region["gl"]
                    region_name = region.get("name", gl)
                    key = (origin, dest, dep.isoformat(), ret_str, gl)
                    checkpoint = results_dir / reporting.checkpoint_filename(origin, dest, dep, ret, gl)

                    cached = reporting.load_checkpoint(checkpoint)
                    if cached and cached.get("status") == "observed" and cached.get("lowest_price_eur"):
                        if key not in seen_keys:
                            summary_records.append(
                                reporting.summary_record(
                                    cached,
                                    origin=origin, destination=dest, departure=dep, return_date=ret,
                                    gl=gl, region_name=region_name, status="observed",
                                    price_eur=cached["lowest_price_eur"],
                                )
                            )
                            seen_keys.add(key)
                        continue

                    observation = None
                    for attempt in range(1, max_retries + 1):
                        try:
                            observation = await scan_single_target_query(
                                page,
                                origin=origin, destination=dest, departure=dep,
                                return_date=ret, gl=gl,
                            )
                            if observation.get("status") == "observed":
                                break
                        except Exception as error:
                            print(f"    [{route_tag}] attempt {attempt} failed: {error}", file=sys.stderr)
                        if attempt < max_retries:
                            await asyncio.sleep(1.0)

                    if observation is None:
                        observation = {
                            "fetched_at": reporting.utc_now(),
                            "source": SOURCE,
                            "status": "error",
                            "origin": origin,
                            "destination": dest,
                            "departure_date": dep.isoformat(),
                            "return_date": ret_str,
                            "stay_nights": (ret - dep).days if ret else 0,
                            "gl": gl,
                            "lowest_price_eur": None,
                            "cards": [],
                        }

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
                    print(
                        f"  [{route_tag}] [{task_num}/{total_tasks}] {gl} ({region_name[:12]}): "
                        f"{f'€{price:.0f}' if price else 'N/A'}",
                        flush=True,
                    )
                    if delay_seconds > 0:
                        await asyncio.sleep(delay_seconds)

                reporting.write_summary_reports(results_dir, summary_records, SUMMARY_STEM)
        finally:
            await context.close()

    reporting.write_summary_reports(results_dir, summary_records, SUMMARY_STEM)
    print(f"[{route_tag}] Completed successfully. Results in {results_dir}")
    return results_dir


def analyze_route_results(results_dir: Path, domestic_gl: str) -> None:
    """Write a markdown arbitrage report for one route's summary table."""
    summary_csv = results_dir / f"{SUMMARY_STEM}.csv"
    if not summary_csv.exists():
        return
    df = pd.read_csv(summary_csv)
    if df.empty or "lowest_price_eur" not in df.columns:
        return
    df = df[df["status"] == "observed"].copy()
    if df.empty:
        return

    df["date_pair"] = df["departure_date"] + " -> " + df["return_date"].astype(str)
    route_name = f"{df['origin'].iloc[0]} -> {df['destination'].iloc[0]}"

    date_summaries = []
    for date_pair, group in df.groupby("date_pair"):
        cheapest = group.loc[group["lowest_price_eur"].idxmin()]
        priciest = group.loc[group["lowest_price_eur"].idxmax()]
        domestic = group[group["gl"] == domestic_gl]
        domestic_price = domestic["lowest_price_eur"].iloc[0] if not domestic.empty else None
        savings = (domestic_price - cheapest["lowest_price_eur"]) if domestic_price else 0.0

        date_summaries.append({
            "Date Pair": date_pair,
            "Cheapest POS": f"{cheapest['gl']} ({cheapest['region_name']})",
            "Cheapest (€)": f"€{cheapest['lowest_price_eur']:.0f}",
            "Carriers": cheapest["carrier"],
            f"Domestic {domestic_gl} (€)": f"€{domestic_price:.0f}" if domestic_price else "N/A",
            "Savings vs Dom (€)": f"€{savings:.0f}" if savings > 0 else "€0",
            "Spread (€)": f"€{priciest['lowest_price_eur'] - cheapest['lowest_price_eur']:.0f}",
        })

    ranking = pd.DataFrame(
        [
            {
                "gl": gl,
                "Market": group["region_name"].iloc[0],
                "Obs": len(group),
                "Mean (€)": round(group["lowest_price_eur"].mean(), 1),
                "Median (€)": round(group["lowest_price_eur"].median(), 1),
                "Min (€)": round(group["lowest_price_eur"].min(), 0),
            }
            for gl, group in df.groupby("gl")
        ]
    ).sort_values(by="Mean (€)")

    report = "\n".join([
        f"# Point of Sale Arbitrage Report: {route_name}",
        f"**Observations**: {len(df)} | **Markets**: {df['gl'].nunique()} | **Date Pairs**: {df['date_pair'].nunique()}",
        f"**Domestic Benchmark POS**: {domestic_gl}\n",
        "## 1. Cheapest Market per Date Pair",
        tabulate(pd.DataFrame(date_summaries), headers="keys", tablefmt="github", showindex=False),
        "\n## 2. Top 15 Cheapest Markets Overall",
        tabulate(ranking.head(15), headers="keys", tablefmt="github", showindex=False),
    ])
    report_file = results_dir / "ARBITRAGE_REPORT.md"
    report_file.write_text(report, encoding="utf-8")
    print(f"Generated route report: {report_file}")


async def orchestrate(delay_seconds: float, headless: bool) -> None:
    regions = load_target_markets()

    print("=" * 80)
    print(f"LAUNCHING {len(ROUTES)}-ROUTE PARALLEL REGIONAL ARBITRAGE STUDY")
    for index, route in enumerate(ROUTES, start=1):
        print(f"{index}. {route['label']}")
    print("=" * 80)

    results = await asyncio.gather(
        *(
            scan_route(route["trip"], regions, delay_seconds=delay_seconds, headless=headless)
            for route in ROUTES
        ),
        return_exceptions=True,
    )

    for route, result in zip(ROUTES, results, strict=True):
        if isinstance(result, Exception):
            print(f"[ERROR in {route['trip']}]: {result}", file=sys.stderr)
        else:
            analyze_route_results(result, domestic_gl=route["domestic_gl"])

    print("\n" + "=" * 80)
    print("ALL REGIONAL ARBITRAGE RESEARCH STUDIES COMPLETED!")
    print("=" * 80)


def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-Route Parallel POS Scanner")
    parser.add_argument("--delay", type=float, default=0.0)
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()
    asyncio.run(orchestrate(args.delay, args.headless))


if __name__ == "__main__":
    main()
