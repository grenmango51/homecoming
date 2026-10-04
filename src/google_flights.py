#!/usr/bin/env python3
"""Human-supervised Google Flights fare scanner.

Reads the rendered Google Flights page in a dedicated browser. It
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
import time
from pathlib import Path
from typing import Any

try:
    from patchright.async_api import Page, async_playwright
    _USING_PATCHRIGHT = True
except ImportError:
    try:
        from playwright.async_api import Page, async_playwright
        _USING_PATCHRIGHT = False
    except ImportError as error:
        raise SystemExit(
            "Patchright or Playwright is required. Run: .\\.venv\\Scripts\\python.exe -m pip install patchright "
            "and then: .\\.venv\\Scripts\\patchright.exe install chromium"
        ) from error

from src import browser, reporting
from src.common import (
    build_search_pairs,
    circuit_breaker_tripped,
    configure_stdio,
    deferred_observation,
    is_valid_completion_cache,
    prune_profile_cache,
    resolve_browser_executable,
)
from src.config import PROJECT_ROOT, load_config
from src.google_parse import (
    GOOGLE_COMPLETION_CHECK,
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


async def extract_candidate_cards(
    page: Page, curr: str = "EUR", *, scroll_steps: int = 3, texts: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Parse the visible flight cards with the euro-only price parser."""
    return await browser.extract_cards(
        page,
        curr=curr,
        selector=browser.LISTITEM_CARD_SELECTOR,
        min_length=20,
        key_length=80,
        limit=50,
        scroll_steps=scroll_steps,
        parser=parse_eur_card_details,
        texts=texts,
    )


async def scan_pair(
    page: Page,
    *,
    origin: str,
    destination: str,
    departure: dt.date,
    return_date: dt.date | None,
    timeout_ms: int,
    gl: str = "FI",
) -> dict[str, Any]:
    """Load one date pair and return its observation."""
    query_url = flight_search_url(origin, destination, departure, return_date, gl=gl)
    try:
        await page.goto(query_url, wait_until="domcontentloaded", timeout=timeout_ms)
    except Exception:
        try:
            await page.goto(query_url, timeout=timeout_ms)
        except Exception:
            pass

    # First-run consent screen for the dedicated profile. Choose the
    # privacy-preserving option and let Google remember it there.
    if await browser.dismiss_consent(page, timeout_ms=min(timeout_ms, 3_000)):
        try:
            await page.goto(query_url, wait_until="domcontentloaded", timeout=timeout_ms)
        except Exception:
            pass

    try:
        initial_snapshot = await browser.wait_for_result_snapshot(page, timeout_ms)
        text = initial_snapshot["text"]
    except TimeoutError:
        page_t = await browser.page_text(page)
        observation = make_observation(
            origin=origin,
            destination=destination,
            departure=departure,
            return_date=return_date,
            page_text=page_t,
            candidates=[],
            status="timeout",
        )
        observation["gl"] = gl
        observation["page_url"] = page.url
        observation["visible_page_text"] = page_t[:2000]
        return observation

    # Google sometimes renders an error card instead of results; its own Reload
    # button recovers faster than a fresh navigation.
    lowered = text.lower()
    if "something went wrong" in lowered or "no results returned" in lowered or page_status(text) == "incomplete":
        if await browser.click_reload_if_present(page):
            try:
                initial_snapshot = await browser.wait_for_result_snapshot(page, timeout_ms)
                text = initial_snapshot["text"]
            except TimeoutError:
                pass

    candidates = await extract_candidate_cards(page)

    # Scrolling can cause additional rendering. Require a fresh quiet interval
    # afterward, then read cards without triggering another scroll/load cycle.
    final_status = None
    try:
        snapshot = await browser.wait_for_result_snapshot(page, timeout_ms)
        candidates = await extract_candidate_cards(page, scroll_steps=0, texts=snapshot["card_texts"])
    except TimeoutError:
        final_status = "completion_unverified"
        candidates = []
        snapshot = await browser.google_result_snapshot(page)

    # Parse the exact snapshot that passed the gate, including the tab headline.
    # Separate DOM reads could otherwise capture a new, unfinished price update.
    text = snapshot["text"] or text
    tab_text = "\n".join(snapshot["tab_texts"])
    if tab_text:
        text = f"{tab_text}\n{text}"

    observation = make_observation(
        origin=origin,
        destination=destination,
        departure=departure,
        return_date=return_date,
        page_text=text,
        candidates=candidates,
        status=final_status,
    )
    observation["gl"] = gl
    if observation["status"] == "observed":
        observation["google_completion_check"] = GOOGLE_COMPLETION_CHECK
        observation["loading_checks"] = {
            "stable_ms": snapshot["stable_ms"],
            "loading_seen": initial_snapshot["loading_seen"] or snapshot["loading_seen"],
            "visible_progressbars": snapshot["visible_progressbars"],
            "loading_messages": snapshot["loading_messages"], "visible_skeletons": snapshot["visible_skeletons"],
        }
    if observation["status"] != "observed":
        observation["page_url"] = page.url
        observation["visible_page_text"] = text[:2000]
    return observation


def failed_observation(
    *, origin: str, destination: str, departure: dt.date, return_date: dt.date | None, error: Exception
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


def resolve_pairs(args: argparse.Namespace, cfg: Any) -> list[tuple[dt.date, dt.date | None]]:
    """Pick date pairs from explicit CLI ranges, else from the trip config."""
    if cfg.trip.trip_type in {"one-way", "oneway"}:
        return cfg.trip.get_search_pairs()
    if args.depart_from and args.depart_to:
        return build_search_pairs(
            depart_from=args.depart_from,
            depart_to=args.depart_to,
            return_from=args.return_from,
            return_to=args.return_to,
            min_stay_nights=args.min_stay_nights,
        )
    return cfg.trip.get_search_pairs()


def cached_observation(
    pair_file: Path,
    *,
    stamp: str,
    origin: str,
    dest: str,
    departure: dt.date,
    return_date: dt.date | None,
    gl: str,
) -> dict[str, Any] | None:
    """Return today's saved observation for this pair when it is verified complete and valid."""
    cached = reporting.load_checkpoint(pair_file)
    if not cached:
        return None
    if is_valid_completion_cache(
        cached,
        stamp=stamp,
        origin=origin,
        dest=dest,
        departure_date=departure,
        return_date=return_date,
        extra_match={"gl": gl, "google_completion_check": GOOGLE_COMPLETION_CHECK},
    ):
        return cached
    return None


async def run(args: argparse.Namespace) -> int:
    cfg = load_config(args.config, args.trip)
    pairs = resolve_pairs(args, cfg)
    if not pairs:
        raise ValueError("No date pairs meet the minimum-stay requirement.")

    if args.gl:
        if "," in args.gl:
            gl_list = [x.strip().upper() for x in args.gl.split(",") if x.strip()]
        else:
            gl_list = [args.gl.strip().upper()]
    else:
        gl_list = cfg.google_flights.gl_list

    profile_mode = cfg.google_flights.profile_mode
    profile_dir = Path(os.environ.get("GOOGLE_FLIGHTS_PROFILE_DIR", args.profile_dir)).resolve()
    base_results_dir = Path(args.results_dir).resolve()
    browser_executable = resolve_browser_executable(args.browser_executable)
    if profile_mode == "persistent":
        profile_dir.mkdir(parents=True, exist_ok=True)
    base_results_dir.mkdir(parents=True, exist_ok=True)
    headless = getattr(args, "headless", cfg.google_flights.headless)

    print(
        f"[Google Flights] Scanning {len(pairs)} date pairs "
        f"({'background' if headless else 'visible'} browser) "
        f"(Point of Sale: {', '.join(gl_list)}). Results: {base_results_dir}"
    )
    if _USING_PATCHRIGHT:
        print("[Google Flights] Using Patchright stealth Chromium engine.")
    else:
        print("[Google Flights] Using Playwright Chromium engine (Patchright not available).")
    if profile_mode == "ephemeral":
        print("[Google Flights] Profile mode: ephemeral (temporary profile, deleted when the browser closes).")
    else:
        print(f"[Google Flights] Profile mode: persistent ({profile_dir})")
    if not headless:
        print("[Google Flights] If Google shows consent, sign-in, or a challenge, handle it in the opened browser.")
    if browser_executable:
        print(f"[Google Flights] Using installed browser: {browser_executable}")

    stamp = reporting.today_stamp()
    written_reports: list[tuple[str, Path, Path]] = []
    start_time = time.monotonic()
    budget_seconds = getattr(args, "runtime_budget_seconds", cfg.execution.runtime_budget_seconds)
    deadline = start_time + budget_seconds
    consecutive_blocks = 0
    all_observations: list[dict[str, Any]] = []
    run_interrupted = False

    async with async_playwright() as playwright:
        context = await browser.launch_google_context(
            playwright,
            "" if profile_mode == "ephemeral" else profile_dir,
            headless=headless,
            executable_path=browser_executable,
        )
        page = context.pages[0] if context.pages else await context.new_page()
        try:
            for m_idx, current_gl in enumerate(gl_list, start=1):
                if len(gl_list) > 1:
                    cur_results_dir = cfg.google_flights.resolved_results_dir_for_gl(current_gl)
                else:
                    cur_results_dir = base_results_dir
                cur_results_dir.mkdir(parents=True, exist_ok=True)

                print(
                    f"\n[Google Flights] [{m_idx}/{len(gl_list)}] Scanning {len(pairs)} pairs "
                    f"for POS: gl={current_gl} (Results: {cur_results_dir.name})"
                )
                cur_report_json = cur_results_dir / f"{REPORT_STEM}_{stamp}.json"
                cur_report_csv = cur_results_dir / f"{REPORT_STEM}_{stamp}.csv"
                observations: list[dict[str, Any]] = []

                for index, (departure, return_date) in enumerate(pairs, start=1):
                    progress = f"[{index}/{len(pairs)}] {departure} -> {return_date}"
                    pair_file = cur_results_dir / reporting.pair_filename(departure, return_date)

                    # Budget check
                    if time.monotonic() >= deadline:
                        print(
                            f"[Google Flights] Runtime budget of {budget_seconds}s exhausted. Deferring remaining pairs...",
                            file=sys.stderr,
                        )
                        run_interrupted = True
                        for rem_dep, rem_ret in pairs[index - 1:]:
                            d_obs = deferred_observation(
                                source=SOURCE,
                                origin=args.origin,
                                destination=args.dest,
                                departure=rem_dep,
                                return_date=rem_ret,
                                reason="runtime_budget_exhausted",
                            )
                            d_obs["gl"] = current_gl
                            save_observation(cur_results_dir, d_obs)
                            observations.append(d_obs)
                            all_observations.append(d_obs)
                        cur_report_json, cur_report_csv = write_daily_report(cur_results_dir, observations)
                        written_reports.append((current_gl, cur_report_json, cur_report_csv))
                        break

                    if args.skip_existing:
                        cached = cached_observation(
                            pair_file,
                            stamp=stamp,
                            origin=args.origin,
                            dest=args.dest,
                            departure=departure,
                            return_date=return_date,
                            gl=current_gl,
                        )
                        if cached:
                            print(
                                f"[Google Flights] {progress} "
                                f"(cached today: €{cached.get('lowest_observed_price_eur')})"
                            )
                            observations.append(cached)
                            all_observations.append(cached)
                            cur_report_json, cur_report_csv = write_daily_report(cur_results_dir, observations)
                            consecutive_blocks = 0
                            continue

                    print(f"[Google Flights] {progress}")
                    query_start = time.monotonic()
                    rem_time = max(5.0, deadline - time.monotonic())
                    effective_timeout_ms = min(args.timeout_seconds * 1000, int(rem_time * 1000))
                    try:
                        observation = await scan_pair(
                            page,
                            origin=args.origin,
                            destination=args.dest,
                            departure=departure,
                            return_date=return_date,
                            timeout_ms=effective_timeout_ms,
                            gl=current_gl,
                        )
                    except Exception as error:
                        observation = failed_observation(
                            origin=args.origin,
                            destination=args.dest,
                            departure=departure,
                            return_date=return_date,
                            error=error,
                        )
                        observation["gl"] = current_gl
                        observation["page_url"] = page.url
                        observation["visible_page_text"] = (await browser.page_text(page))[:2000]

                    query_elapsed = round(time.monotonic() - query_start, 2)
                    observation["query_elapsed_seconds"] = query_elapsed
                    saved = save_observation(cur_results_dir, observation)
                    observations.append(observation)
                    all_observations.append(observation)
                    cur_report_json, cur_report_csv = write_daily_report(cur_results_dir, observations)
                    print(
                        f"  {observation['status']}; lowest observed: "
                        f"{observation['lowest_observed_price_eur']}; elapsed: {query_elapsed}s; saved {saved.name}"
                    )

                    if observation["status"] in NEEDS_HUMAN:
                        consecutive_blocks += 1
                        if not getattr(args, "attended", False):
                            if circuit_breaker_tripped(consecutive_blocks, threshold=2):
                                print(
                                    f"\n[Google Flights] Circuit breaker tripped after {consecutive_blocks} consecutive challenges. "
                                    "Deferring remaining pairs...",
                                    file=sys.stderr,
                                    flush=True,
                                )
                                run_interrupted = True
                                for rem_dep, rem_ret in pairs[index:]:
                                    d_obs = deferred_observation(
                                        source=SOURCE,
                                        origin=args.origin,
                                        destination=args.dest,
                                        departure=rem_dep,
                                        return_date=rem_ret,
                                        reason="circuit_breaker_tripped",
                                    )
                                    d_obs["gl"] = current_gl
                                    save_observation(cur_results_dir, d_obs)
                                    observations.append(d_obs)
                                    all_observations.append(d_obs)
                                cur_report_json, cur_report_csv = write_daily_report(cur_results_dir, observations)
                                written_reports.append((current_gl, cur_report_json, cur_report_csv))
                                return 2
                        else:
                            print(
                                "[Google Flights] Attended mode: browser needs human action.",
                                file=sys.stderr,
                            )
                    else:
                        consecutive_blocks = 0

                    if index < len(pairs):
                        await page.wait_for_timeout(args.delay_seconds * 1000)

                written_reports.append((current_gl, cur_report_json, cur_report_csv))
                if run_interrupted:
                    break
                if m_idx < len(gl_list):
                    await page.wait_for_timeout(args.delay_seconds * 1000)
        finally:
            await context.close()

    for gl_code, r_json, r_csv in written_reports:
        print(f"[Google Flights] Daily report ({gl_code}) written: {r_json.name}, {r_csv.name}")

    if cfg.google_flights.prune_cache and profile_mode == "persistent":
        freed_mb = prune_profile_cache(profile_dir)
        if freed_mb > 0:
            print(f"[Google Flights] Pruned {freed_mb:.1f} MB of browser cache from the profile.")

    total_scanned = len(pairs) * len(gl_list)
    total_observed = sum(1 for obs in all_observations if obs.get("status") == "observed")
    total_deferred = sum(1 for obs in all_observations if obs.get("status") == "deferred")
    total_failed = len(all_observations) - total_observed - total_deferred
    total_elapsed = round(time.monotonic() - start_time, 2)
    print(
        f"\n[Google Flights] Run summary: elapsed={total_elapsed}s; "
        f"coverage: observed={total_observed}/{total_scanned}, deferred={total_deferred}, failed={total_failed}"
    )

    if run_interrupted or total_observed < total_scanned:
        return 2 if consecutive_blocks >= 2 else 1
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

    res.add_argument("--gl", default=None, help="Point of sale region code(s) (e.g. 'FI', 'SE', or 'FI,SE')")
    res.add_argument("--min-stay-nights", type=int, default=cfg.trip.min_stay_nights, help="Minimum stay duration in nights")
    res.add_argument("--delay-seconds", type=int, default=cfg.google_flights.delay_seconds, help="Delay between pages in seconds")
    res.add_argument("--timeout-seconds", type=int, default=cfg.google_flights.timeout_seconds)
    res.add_argument(
        "--runtime-budget-seconds",
        type=int,
        default=cfg.execution.runtime_budget_seconds,
        help="Maximum per-scraper runtime budget in seconds (default 1800)",
    )
    res.add_argument(
        "--attended",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Wait for human intervention on anti-bot challenges",
    )
    res.add_argument(
        "--skip-existing",
        action=argparse.BooleanOptionalAction,
        default=cfg.execution.skip_existing,
        help="Skip querying pairs already observed today",
    )
    res.add_argument("--profile-dir", default=str(cfg.google_flights.resolved_profile_dir()))
    res.add_argument("--results-dir", default=str(cfg.google_flights.resolved_results_dir()))
    res.add_argument("--browser-executable", default=cfg.execution.browser_executable, help="Path to Chrome/Edge")
    res.add_argument("--headless", action=argparse.BooleanOptionalAction,
                     default=cfg.google_flights.headless, help="Run without a browser window")
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
