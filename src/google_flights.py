#!/usr/bin/env python3
"""Human-supervised Google Flights fare scanner.

Reads the rendered Google Flights page in a visible, persistent browser. It
never follows a booking link or clicks a purchase/checkout control.

Fare parsing lives in :mod:`src.google_parse` and page driving in
:mod:`src.browser`; this module is the daily single-route scan on top of them.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import os
import sys
from pathlib import Path
from typing import Any

try:
    from playwright.async_api import Page, async_playwright
except ImportError as error:
    raise SystemExit(
        "Playwright is required. Run: python -m pip install -r requirements.txt "
        "and then: python -m playwright install chromium"
    ) from error

from src import browser, reporting
from src.common import build_search_pairs, configure_stdio, resolve_browser_executable
from src.config import PROJECT_ROOT, load_config
from src.google_parse import (
    flight_search_url,
    make_observation,
    page_status,
    parse_eur_card_details,
)
from src.reporting import DAILY_REPORT_FIELDS

ROOT = PROJECT_ROOT
SOURCE = "Google Flights UI"
REPORT_STEM = "daily_fare_report"

NEEDS_HUMAN = {"blocked", "user_action_required"}


async def extract_candidate_cards(page: Page, curr: str = "EUR") -> list[dict[str, Any]]:
    """Parse the visible flight cards with the euro-only price parser."""
    return await browser.extract_cards(
        page,
        curr=curr,
        selector=browser.DEFAULT_CARD_SELECTOR,
        min_length=20,
        key_length=80,
        limit=40,
        scroll_steps=2,
        parser=parse_eur_card_details,
    )


async def scan_pair(
    page: Page,
    *,
    origin: str,
    destination: str,
    departure: dt.date,
    return_date: dt.date,
    timeout_ms: int,
    gl: str = "FI",
) -> dict[str, Any]:
    """Load one date pair and return its observation."""
    query_url = flight_search_url(origin, destination, departure, return_date, gl=gl)
    try:
        await page.goto(query_url, wait_until="domcontentloaded", timeout=timeout_ms)
    except Exception:
        await page.goto(query_url, timeout=timeout_ms)

    # First-run consent screen for the dedicated profile. Choose the
    # privacy-preserving option and let Google remember it there.
    if await browser.dismiss_consent(page, timeout_ms=min(timeout_ms, 3_000)):
        await page.goto(query_url, wait_until="domcontentloaded", timeout=timeout_ms)

    text = await browser.wait_for_results(page, timeout_ms)

    # Google sometimes renders an error card instead of results; its own Reload
    # button recovers faster than a fresh navigation.
    lowered = text.lower()
    if "something went wrong" in lowered or "no results returned" in lowered or page_status(text) == "incomplete":
        if await browser.click_reload_if_present(page, settle_ms=3_500):
            text = await browser.wait_for_results(page, timeout_ms)

    if await browser.sort_by_price(page):
        await page.wait_for_timeout(2_000)

    candidates = await extract_candidate_cards(page)

    # Re-read the body once cards have rendered, and prepend the Cheapest tab
    # header so its headline fare is visible to the banner parser.
    text = await browser.page_text(page) or text
    tab_text = await browser.read_cheapest_tab_text(page)
    if tab_text:
        text = f"{tab_text}\n{text}"

    observation = make_observation(
        origin=origin,
        destination=destination,
        departure=departure,
        return_date=return_date,
        page_text=text,
        candidates=candidates,
    )
    observation["gl"] = gl
    if observation["status"] != "observed":
        observation["page_url"] = page.url
        observation["visible_page_text"] = text[:2000]
    return observation


def failed_observation(
    *, origin: str, destination: str, departure: dt.date, return_date: dt.date, error: Exception
) -> dict[str, Any]:
    """Persist a recoverable error instead of losing an entire daily matrix."""
    return reporting.error_observation(
        source=SOURCE,
        origin=origin,
        destination=destination,
        departure=departure,
        return_date=return_date,
        error=error,
        note="The page could not be read; this pair needs a retry.",
    )


def save_observation(results_dir: Path, observation: dict[str, Any]) -> Path:
    return reporting.save_observation(results_dir, observation)


def write_daily_report(results_dir: Path, observations: list[dict[str, Any]]) -> tuple[Path, Path]:
    """Write the day's Google Flights JSON and CSV roll-up."""
    return reporting.write_daily_report(
        results_dir,
        observations,
        source=SOURCE,
        stem=REPORT_STEM,
        fieldnames=DAILY_REPORT_FIELDS,
    )


def resolve_pairs(args: argparse.Namespace, cfg: Any) -> list[tuple[dt.date, dt.date]]:
    """Pick date pairs from explicit CLI ranges, else from the trip config."""
    if args.depart_from and args.depart_to:
        return build_search_pairs(
            depart_from=args.depart_from,
            depart_to=args.depart_to,
            return_from=args.return_from,
            return_to=args.return_to,
            min_stay_nights=args.min_stay_nights,
        )
    if cfg.trip.date_mode == "exact" and cfg.trip.exact_pairs:
        return [pair for pair in cfg.trip.get_search_pairs() if pair[1] is not None]
    return build_search_pairs(
        window_start=args.window_start,
        window_end=args.window_end,
        min_stay_nights=args.min_stay_nights,
    )


def cached_observation(
    pair_file: Path, *, stamp: str, origin: str, dest: str, gl: str
) -> dict[str, Any] | None:
    """Return today's saved observation for this pair when it is still valid."""
    cached = reporting.load_checkpoint(pair_file)
    if not cached:
        return None
    matches = (
        cached.get("status") == "observed"
        and str(cached.get("fetched_at", "")).startswith(stamp)
        and cached.get("origin") == origin
        and cached.get("destination") == dest
        and cached.get("gl", gl) == gl
    )
    return cached if matches else None


async def run(args: argparse.Namespace) -> int:
    cfg = load_config(args.config, args.trip)
    pairs = resolve_pairs(args, cfg)
    if not pairs:
        raise ValueError("No date pairs meet the minimum-stay requirement.")

    gl = args.gl or cfg.google_flights.gl or "FI"
    profile_dir = Path(os.environ.get("GOOGLE_FLIGHTS_PROFILE_DIR", args.profile_dir)).resolve()
    results_dir = Path(args.results_dir).resolve()
    browser_executable = resolve_browser_executable(args.browser_executable)
    profile_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)

    print(
        f"[Google Flights] Scanning {len(pairs)} date pairs in a visible browser "
        f"(Point of Sale: gl={gl}). Results: {results_dir}"
    )
    print("[Google Flights] If Google shows consent, sign-in, or a challenge, handle it in the opened browser.")
    if browser_executable:
        print(f"[Google Flights] Using installed browser: {browser_executable}")

    stamp = reporting.today_stamp()
    report_json = results_dir / f"{REPORT_STEM}_{stamp}.json"
    report_csv = results_dir / f"{REPORT_STEM}_{stamp}.csv"
    observations: list[dict[str, Any]] = []

    async with async_playwright() as playwright:
        context = await browser.launch_google_context(
            playwright, profile_dir, headless=False, executable_path=browser_executable
        )
        page = context.pages[0] if context.pages else await context.new_page()
        try:
            for index, (departure, return_date) in enumerate(pairs, start=1):
                progress = f"[{index}/{len(pairs)}] {departure} -> {return_date}"
                pair_file = results_dir / reporting.pair_filename(departure, return_date)

                if args.skip_existing:
                    cached = cached_observation(
                        pair_file, stamp=stamp, origin=args.origin, dest=args.dest, gl=gl
                    )
                    if cached:
                        print(
                            f"[Google Flights] {progress} "
                            f"(cached today: €{cached.get('lowest_observed_price_eur')})"
                        )
                        observations.append(cached)
                        report_json, report_csv = write_daily_report(results_dir, observations)
                        continue

                print(f"[Google Flights] {progress}")
                try:
                    observation = await scan_pair(
                        page,
                        origin=args.origin,
                        destination=args.dest,
                        departure=departure,
                        return_date=return_date,
                        timeout_ms=args.timeout_seconds * 1000,
                        gl=gl,
                    )
                except Exception as error:
                    observation = failed_observation(
                        origin=args.origin,
                        destination=args.dest,
                        departure=departure,
                        return_date=return_date,
                        error=error,
                    )
                    observation["page_url"] = page.url
                    observation["visible_page_text"] = (await browser.page_text(page))[:2000]

                saved = save_observation(results_dir, observation)
                observations.append(observation)
                report_json, report_csv = write_daily_report(results_dir, observations)
                print(
                    f"  {observation['status']}; lowest observed: "
                    f"{observation['lowest_observed_price_eur']}; saved {saved.name}"
                )

                if observation["status"] in NEEDS_HUMAN:
                    print(
                        "[Google Flights] Stopping: browser needs human action. Re-run after resolving it.",
                        file=sys.stderr,
                    )
                    return 2
                if index < len(pairs):
                    await page.wait_for_timeout(args.delay_seconds * 1000)
        finally:
            await context.close()

    print(f"[Google Flights] Daily report written: {report_json.name}, {report_csv.name}")
    return 0


def parser(argv: list[str] | None = None) -> argparse.ArgumentParser:
    """Build the CLI, defaulting every flag from the resolved configuration."""
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config", help="Optional path to config.toml")
    pre.add_argument("--trip", help="Optional path or name of trip config file")
    pre_args, _ = pre.parse_known_args(argv)

    cfg = load_config(pre_args.config, pre_args.trip)
    res = argparse.ArgumentParser(
        description="Human-supervised Google Flights matrix scanner (no purchase actions)."
    )
    res.add_argument("--config", default=pre_args.config, help="Optional path to config.toml")
    res.add_argument("--trip", default=pre_args.trip, help="Optional path or name of trip config file")
    res.add_argument("--origin", default=cfg.trip.origin)
    res.add_argument("--dest", default=cfg.trip.dest)

    ranged = cfg.trip.date_mode == "range"
    res.add_argument("--depart-from", default=cfg.trip.depart_from if ranged else None, help="Start of departure range")
    res.add_argument("--depart-to", default=cfg.trip.depart_to if ranged else None, help="End of departure range")
    res.add_argument("--return-from", default=cfg.trip.return_from if ranged else None, help="Start of return range")
    res.add_argument("--return-to", default=cfg.trip.return_to if ranged else None, help="End of return range")
    res.add_argument("--window-start", default=None if ranged else cfg.trip.window_start, help="Earliest allowed departure date")
    res.add_argument("--window-end", default=None if ranged else cfg.trip.window_end, help="Latest allowed return date")

    res.add_argument("--gl", default=cfg.google_flights.gl, help="Point of sale region code (e.g. SE, FI, DE)")
    res.add_argument("--min-stay-nights", type=int, default=cfg.trip.min_stay_nights, help="Minimum stay duration in nights")
    res.add_argument("--delay-seconds", type=int, default=cfg.google_flights.delay_seconds, help="Delay between pages in seconds")
    res.add_argument("--timeout-seconds", type=int, default=cfg.google_flights.timeout_seconds)
    res.add_argument(
        "--skip-existing",
        action=argparse.BooleanOptionalAction,
        default=cfg.execution.skip_existing,
        help="Skip querying pairs already observed today",
    )
    res.add_argument("--profile-dir", default=str(cfg.google_flights.resolved_profile_dir()))
    res.add_argument("--results-dir", default=str(cfg.google_flights.resolved_results_dir()))
    res.add_argument("--browser-executable", default=cfg.execution.browser_executable, help="Path to Chrome/Edge")
    return res


def main(argv: list[str] | None = None) -> None:
    configure_stdio()
    args = parser(argv).parse_args(argv)
    try:
        raise SystemExit(asyncio.run(run(args)))
    except (ValueError, OSError) as error:
        raise SystemExit(f"Error: {error}") from error


if __name__ == "__main__":
    main()
