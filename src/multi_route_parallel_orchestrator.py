#!/usr/bin/env python3
"""Multi-Route Parallel Orchestrator for Google Flights POS Arbitrage.

Executes Point-of-Sale arbitrage scans across multiple routes in parallel:
1. PHL -> HAN (Round-Trip): Transpacific/Transatlantic Corridor
2. HEL -> BRU (One-Way): Nordic to Western Europe Outbound
3. AMS -> HEL (One-Way): Western Europe to Nordic Return
Follow-up:
4. SIN -> HAN (Round-Trip): Intra-Asian Regional Benchmark

Each route runs in its own dedicated, isolated browser context to prevent
profile lock contention, using the vetted active dynamic POS markets list.
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

if str(Path(__file__).resolve().parent.parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pandas as pd
from tabulate import tabulate

try:
    from playwright.async_api import BrowserContext, Page, async_playwright
except ImportError as error:
    raise SystemExit("Playwright is required. Run pip install playwright") from error

from src.all_pos_scanner import (
    build_route_url,
    handle_consent_if_needed,
    click_reload_if_present,
    extract_clean_candidate_cards,
)
from src.common import resolve_browser_executable
from src.config import load_config, PROJECT_ROOT

ROOT = PROJECT_ROOT
ACTIVE_MARKETS_FILE = ROOT / "flight_results_all_pos" / "active_market_gl_codes.json"
ALL_AVAILABLE_REGIONS = ROOT / "all_available_regions_mapped.json"


def load_target_markets() -> list[dict[str, str]]:
    """Load vetted active markets, falling back to all regions if filter hasn't run yet."""
    if ACTIVE_MARKETS_FILE.exists():
        try:
            data = json.loads(ACTIVE_MARKETS_FILE.read_text(encoding="utf-8"))
            if data:
                print(f"[Orchestrator] Loaded {len(data)} vetted active dynamic markets from {ACTIVE_MARKETS_FILE.name}")
                return data
        except Exception:
            pass
    if ALL_AVAILABLE_REGIONS.exists():
        data = json.loads(ALL_AVAILABLE_REGIONS.read_text(encoding="utf-8"))
        print(f"[Orchestrator] Loaded {len(data)} markets from {ALL_AVAILABLE_REGIONS.name}")
        return data
    return [{"gl": "US", "name": "United States"}, {"gl": "FI", "name": "Finland"}, {"gl": "VN", "name": "Vietnam"}]


def get_checkpoint_filename(origin: str, dest: str, dep: dt.date, ret: dt.date | None, gl: str) -> str:
    ret_str = ret.isoformat() if ret else "oneway"
    return f"{origin}_{dest}_{dep.isoformat()}_{ret_str}_gl-{gl}.json"


def update_route_summary_reports(
    results_dir: Path,
    summary_data: list[dict[str, Any]],
) -> tuple[Path, Path]:
    results_dir.mkdir(parents=True, exist_ok=True)
    json_path = results_dir / "summary_report.json"
    csv_path = results_dir / "summary_report.csv"

    json_path.write_text(json.dumps(summary_data, ensure_ascii=False, indent=2), encoding="utf-8")

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


async def scan_single_target_query(
    page: Page,
    *,
    origin: str,
    destination: str,
    departure: dt.date,
    return_date: dt.date | None,
    gl: str,
    curr: str = "EUR",
    timeout_ms: int = 35000,
) -> dict[str, Any]:
    url = build_route_url(origin, destination, departure, return_date, gl=gl, curr=curr)
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    except Exception:
        await page.goto(url, timeout=timeout_ms)

    await page.wait_for_timeout(1000)
    await handle_consent_if_needed(page, url)

    # Reload if glitched
    page_text = await page.locator("body").inner_text()
    if "something went wrong" in page_text.lower():
        reloaded = await click_reload_if_present(page)
        if reloaded:
            await page.wait_for_timeout(2500)

    # Wait for Google Flights async streaming
    for _ in range(10):
        await page.wait_for_timeout(800)
        body_text = await page.locator("body").inner_text()
        if "unusual traffic" in body_text.lower() or "captcha" in body_text.lower():
            break
        if not re.search(r"loading results|fetching results", body_text, re.IGNORECASE):
            break

    candidates = await extract_clean_candidate_cards(page, curr=curr)

    prices = []
    for c in candidates:
        if c.get("lowest_price") is not None:
            prices.append(c["lowest_price"])
        prices.extend(c.get("observed_prices", []))

    tab_price = None
    try:
        cheapest_tab = page.locator("[role='tab']:has-text('Cheapest')").first
        if await cheapest_tab.count() > 0:
            tab_txt = await cheapest_tab.inner_text()
            m = re.search(r"€\s*([\d,]+)", tab_txt)
            if m:
                tab_price = float(m.group(1).replace(",", ""))
    except Exception:
        pass

    lowest_price = min(prices) if prices else None
    if tab_price is not None:
        if lowest_price is None or tab_price < lowest_price:
            lowest_price = tab_price
    top_card = candidates[0] if candidates else {}

    return {
        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source": "Multi-Route POS Scanner",
        "origin": origin,
        "destination": destination,
        "departure_date": departure.isoformat(),
        "return_date": return_date.isoformat() if return_date else "oneway",
        "stay_nights": (return_date - departure).days if return_date else 0,
        "gl": gl,
        "currency": curr,
        "status": "observed" if lowest_price else "incomplete",
        "lowest_price_eur": lowest_price,
        "num_candidates": len(candidates),
        "cards": candidates,
        "page_url": page.url,
    }


async def scan_route(
    trip_config_file: str,
    regions: list[dict[str, str]],
    delay_seconds: float = 0.0,
    headless: bool = False,
    max_retries: int = 2,
) -> Path:
    cfg = load_config(trip_path=trip_config_file)
    origin = cfg.trip.origin
    dest = cfg.trip.dest
    pairs = cfg.trip.get_search_pairs()

    route_tag = f"{origin}_{dest}"
    results_dir = ROOT / f"flight_results_{route_tag}"
    profile_dir = ROOT / f".browser-profile-{route_tag}"
    results_dir.mkdir(parents=True, exist_ok=True)
    profile_dir.mkdir(parents=True, exist_ok=True)

    print(f"[{route_tag}] Starting scan: {len(pairs)} pairs × {len(regions)} regions ({len(pairs)*len(regions)} queries)")

    summary_records: list[dict[str, Any]] = []
    summary_json = results_dir / "summary_report.json"
    if summary_json.exists():
        try:
            summary_records = json.loads(summary_json.read_text(encoding="utf-8"))
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
            viewport={"width": 1400, "height": 950},
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
        total_tasks = len(pairs) * len(regions)

        for pair_idx, (dep, ret) in enumerate(pairs, start=1):
            ret_str = ret.isoformat() if ret else "oneway"
            pair_label = f"{dep.isoformat()} -> {ret_str}"
            print(f"\n[{route_tag}] [{pair_idx}/{len(pairs)}] >>> Scanning: {pair_label} <<<", flush=True)

            for reg in regions:
                task_num += 1
                gl = reg["gl"]
                reg_name = reg.get("name", gl)
                key = (origin, dest, dep.isoformat(), ret_str, gl)

                fpath = results_dir / get_checkpoint_filename(origin, dest, dep, ret, gl)
                if fpath.exists():
                    try:
                        cached = json.loads(fpath.read_text(encoding="utf-8"))
                        if cached.get("status") == "observed" and cached.get("lowest_price_eur"):
                            p_eur = cached.get("lowest_price_eur")
                            if key not in seen_keys:
                                top_c = cached.get("cards", [{}])[0]
                                summary_records.append({
                                    "origin": origin,
                                    "destination": dest,
                                    "departure_date": dep.isoformat(),
                                    "return_date": ret_str,
                                    "stay_nights": (ret - dep).days if ret else 0,
                                    "gl": gl,
                                    "region_name": reg_name,
                                    "status": "observed",
                                    "lowest_price_eur": p_eur,
                                    "carrier": ", ".join(top_c.get("carriers", [])) or "Unknown",
                                    "duration_minutes": top_c.get("duration_minutes"),
                                    "stops": top_c.get("stops"),
                                    "layovers": "/".join(top_c.get("layovers", [])),
                                    "fetched_at": cached.get("fetched_at"),
                                })
                                seen_keys.add(key)
                            continue
                    except Exception:
                        pass

                # Live query
                obs = None
                for attempt in range(1, max_retries + 1):
                    try:
                        obs = await scan_single_target_query(
                            page,
                            origin=origin,
                            destination=dest,
                            departure=dep,
                            return_date=ret,
                            gl=gl,
                        )
                        if obs.get("status") == "observed":
                            break
                    except Exception as err:
                        if attempt == max_retries:
                            obs = {
                                "status": "error",
                                "origin": origin,
                                "destination": dest,
                                "departure_date": dep.isoformat(),
                                "return_date": ret_str,
                                "stay_nights": (ret - dep).days if ret else 0,
                                "gl": gl,
                                "lowest_price_eur": None,
                                "error": str(err),
                            }
                        await asyncio.sleep(1.0)

                # Persist JSON checkpoint
                fpath.write_text(json.dumps(obs, ensure_ascii=False, indent=2), encoding="utf-8")

                top_c = obs.get("cards", [{}])[0] if obs.get("cards") else {}
                p_eur = obs.get("lowest_price_eur")
                summary_records.append({
                    "origin": origin,
                    "destination": dest,
                    "departure_date": dep.isoformat(),
                    "return_date": ret_str,
                    "stay_nights": (ret - dep).days if ret else 0,
                    "gl": gl,
                    "region_name": reg_name,
                    "status": obs.get("status"),
                    "lowest_price_eur": p_eur,
                    "carrier": ", ".join(top_c.get("carriers", [])) or "Unknown",
                    "duration_minutes": top_c.get("duration_minutes"),
                    "stops": top_c.get("stops"),
                    "layovers": "/".join(top_c.get("layovers", [])),
                    "fetched_at": obs.get("fetched_at"),
                })
                seen_keys.add(key)

                p_str = f"€{p_eur:.0f}" if p_eur else "N/A"
                print(f"  [{route_tag}] [{task_num}/{total_tasks}] {gl} ({reg_name[:12]}): {p_str}", flush=True)

                if delay_seconds > 0:
                    await asyncio.sleep(delay_seconds)

            update_route_summary_reports(results_dir, summary_records)

        await context.close()

    update_route_summary_reports(results_dir, summary_records)
    print(f"[{route_tag}] Completed successfully. Results in {results_dir}")
    return results_dir


def analyze_route_results(results_dir: Path, domestic_gl: str) -> None:
    """Generate markdown arbitrage report for a specific route."""
    summary_csv = results_dir / "summary_report.csv"
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
    for dp, group in df.groupby("date_pair"):
        min_row = group.loc[group["lowest_price_eur"].idxmin()]
        max_row = group.loc[group["lowest_price_eur"].idxmax()]
        dom_row = group[group["gl"] == domestic_gl]
        dom_p = dom_row["lowest_price_eur"].iloc[0] if not dom_row.empty else None

        savings = (dom_p - min_row["lowest_price_eur"]) if dom_p else 0.0
        date_summaries.append({
            "Date Pair": dp,
            "Cheapest POS": f"{min_row['gl']} ({min_row['region_name']})",
            "Cheapest (€)": f"€{min_row['lowest_price_eur']:.0f}",
            "Carriers": min_row["carrier"],
            f"Domestic {domestic_gl} (€)": f"€{dom_p:.0f}" if dom_p else "N/A",
            "Savings vs Dom (€)": f"€{savings:.0f}" if savings > 0 else "€0",
            "Spread (€)": f"€{max_row['lowest_price_eur'] - min_row['lowest_price_eur']:.0f}",
        })

    pos_ranking = []
    for gl, group in df.groupby("gl"):
        pos_ranking.append({
            "gl": gl,
            "Market": group["region_name"].iloc[0] if "region_name" in group else gl,
            "Obs": len(group),
            "Mean (€)": round(group["lowest_price_eur"].mean(), 1),
            "Median (€)": round(group["lowest_price_eur"].median(), 1),
            "Min (€)": round(group["lowest_price_eur"].min(), 0),
        })

    rank_df = pd.DataFrame(pos_ranking).sort_values(by="Mean (€)")

    report_lines = [
        f"# Point of Sale Arbitrage Report: {route_name}",
        f"**Observations**: {len(df)} | **Markets**: {df['gl'].nunique()} | **Date Pairs**: {df['date_pair'].nunique()}",
        f"**Domestic Benchmark POS**: {domestic_gl}\n",
        "## 1. Cheapest Market per Date Pair",
        tabulate(pd.DataFrame(date_summaries), headers="keys", tablefmt="github", showindex=False),
        "\n## 2. Top 15 Cheapest Markets Overall",
        tabulate(rank_df.head(15), headers="keys", tablefmt="github", showindex=False),
    ]
    report_file = results_dir / "ARBITRAGE_REPORT.md"
    report_file.write_text("\n".join(report_lines), encoding="utf-8")
    print(f"Generated route report: {report_file}")


async def main():
    parser = argparse.ArgumentParser(description="Multi-Route Parallel POS Scanner")
    parser.add_argument("--delay", type=float, default=0.0)
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()

    # Load vetted active markets
    regions = load_target_markets()

    print("=" * 80)
    print("LAUNCHING 4-ROUTE PARALLEL REGIONAL ARBITRAGE STUDY")
    print("1. Philadelphia (PHL) -> Hanoi (HAN) [Round-Trip: 8 date pairs]")
    print("2. Helsinki (HEL) -> Brussels (BRU) [One-Way: 15-19 Dec 2026]")
    print("3. Amsterdam (AMS) -> Helsinki (HEL) [One-Way: 1-5 Jan 2027]")
    print("4. Singapore (SIN) -> Hanoi (HAN) [Round-Trip: 8 date pairs]")
    print("=" * 80)

    # Launch all 4 routes concurrently in parallel!
    results = await asyncio.gather(
        scan_route("config/PHL_HAN.toml", regions, delay_seconds=args.delay, headless=args.headless),
        scan_route("config/HEL_BRU.toml", regions, delay_seconds=args.delay, headless=args.headless),
        scan_route("config/AMS_HEL.toml", regions, delay_seconds=args.delay, headless=args.headless),
        scan_route("config/SIN_HAN.toml", regions, delay_seconds=args.delay, headless=args.headless),
        return_exceptions=True,
    )

    for r in results:
        if isinstance(r, Exception):
            print(f"[ERROR in route scan]: {r}")

    # Analyze completed parallel routes
    analyze_route_results(ROOT / "flight_results_PHL_HAN", domestic_gl="US")
    analyze_route_results(ROOT / "flight_results_HEL_BRU", domestic_gl="FI")
    analyze_route_results(ROOT / "flight_results_AMS_HEL", domestic_gl="NL")
    analyze_route_results(ROOT / "flight_results_SIN_HAN", domestic_gl="SG")

    print("\n================================================================================")
    print("ALL 4 REGIONAL ARBITRAGE RESEARCH STUDIES COMPLETED!")
    print("================================================================================")


if __name__ == "__main__":
    asyncio.run(main())
