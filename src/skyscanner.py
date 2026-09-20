#!/usr/bin/env python3
"""Human-supervised Skyscanner fare scanner for Helsinki (HEL) to Hanoi (HAN).

This program loads the rendered Skyscanner page in a visible, persistent browser context.
It captures internal search API responses and extracts rendered flight fare cards.
It never follows booking affiliate links or makes purchase actions.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import random
import sys
from pathlib import Path
from typing import Any

try:
    from patchright.async_api import BrowserContext, Page, Response, async_playwright
except ImportError:
    try:
        from playwright.async_api import BrowserContext, Page, Response, async_playwright
    except ImportError as error:
        raise SystemExit(
            "Patchright or Playwright is required. Run: .\\.venv\\Scripts\\python.exe -m pip install patchright "
            "and then: .\\.venv\\Scripts\\patchright.exe install chromium"
        ) from error

from src import reporting
from src.common import build_search_pairs, configure_stdio, resolve_browser_executable
from src.config import PROJECT_ROOT, load_config
from src.reporting import SKYSCANNER_REPORT_FIELDS
from src.skyscanner_parse import (
    SOURCE,
    extract_from_xhr_payloads,
    flight_search_url,
    is_challenge_page,
    make_observation,
    money_values,
)

ROOT = PROJECT_ROOT
REPORT_STEM = "daily_fare_report_skyscanner"

NEEDS_HUMAN = {"blocked", "user_action_required"}

async def extract_dom_candidate_cards(page: Page) -> list[str]:
    """Fallback extraction of visible flight cards in the rendered DOM."""
    locators = page.locator(
        "div[class*='Ticket'], div[class*='Card'], [aria-label*='Flight option'], [aria-label*='Lentovaihtoehto'], [data-testid='flight-card'], [data-testid='itinerary-card'], [data-testid*='itinerary']"
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
            prices = [p for p in money_values(compact) if p > 0]
            if prices:
                key = compact[:100].lower()
                if key not in seen:
                    seen.add(key)
                    cards.append(compact[:3000])
        except Exception:
            continue
    return cards


def failed_observation(
    *, origin: str, destination: str, departure: dt.date, return_date: dt.date, error: Exception
) -> dict[str, Any]:
    """Record a failure observation so the matrix run continues gracefully."""
    return reporting.error_observation(
        source=SOURCE,
        origin=origin,
        destination=destination,
        departure=departure,
        return_date=return_date,
        error=error,
        note="The query failed; this pair should be retried.",
        extra={"itinerary_count": 0},
    )


def save_observation(results_dir: Path, observation: dict[str, Any]) -> Path:
    return reporting.save_observation(results_dir, observation)


def write_daily_report(
    results_dir: Path, observations: list[dict[str, Any]]
) -> tuple[Path, Path]:
    """Write the day's Skyscanner JSON and CSV roll-up."""
    return reporting.write_daily_report(
        results_dir,
        observations,
        source=SOURCE,
        stem=REPORT_STEM,
        fieldnames=SKYSCANNER_REPORT_FIELDS,
        fallbacks={
            "itinerary_count": lambda item: item.get(
                "itinerary_count", len(item.get("candidate_cards", []))
            )
        },
    )


async def reset_session(page: Page) -> None:
    """Clear cookies/tokens and warm up on homepage to reset anti-bot challenges."""
    try:
        await page.context.clear_cookies()
        await page.context.add_cookies([
            {"name": "ssculture", "value": "locale:::fi-FI&market:::FI&currency:::EUR", "domain": ".skyscanner.net", "path": "/"},
            {"name": "ssculture", "value": "locale:::fi-FI&market:::FI&currency:::EUR", "domain": ".skyscanner.fi", "path": "/"},
        ])
        print("[Skyscanner] Resetting session on homepage...", flush=True)
        await page.goto("https://www.skyscanner.fi/", wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_timeout(1000)
        for btn_name in ("Reject all", "Hylkää kaikki", "Decline all", "Accept all", "Hyväksy kaikki"):
            btn = page.get_by_role("button", name=btn_name, exact=False)
            if await btn.count() > 0 and await btn.first.is_visible():
                await btn.first.click(timeout=2000)
                await page.wait_for_timeout(500)
                break
    except Exception:
        pass


async def scan_pair(
    page: Page,
    *,
    origin: str,
    destination: str,
    departure: dt.date,
    return_date: dt.date,
    timeout_ms: int,
    poll_wait_seconds: int = 30,
    challenge_timeout_seconds: int = 25,
) -> dict[str, Any]:
    """Navigate to Skyscanner, handle consent/challenges gracefully, and extract flight fares."""
    captured_payloads: list[dict[str, Any]] = []
    search_complete_event = asyncio.Event()

    async def handle_response(response: Response) -> None:
        try:
            url_lowered = response.url.lower()
            if "web-unified-search" in url_lowered or "graphql" in url_lowered:
                if response.status == 200 and "application/json" in response.headers.get("content-type", ""):
                    body = await response.json()
                    captured_payloads.append(body)
                    # Detect backend scan completion signal in XHR payload
                    status = body.get("status")
                    context_obj = body.get("context") or body.get("itineraries", {}).get("context", {})
                    ctx_status = context_obj.get("status") if isinstance(context_obj, dict) else None
                    query_status = body.get("query_status") or body.get("searchStatus")
                    if any(
                        str(s).upper() in ("COMPLETE", "COMPLETED", "FINISHED")
                        for s in (status, ctx_status, query_status)
                        if s
                    ):
                        search_complete_event.set()
        except Exception:
            pass

    page.on("response", handle_response)
    query_url = flight_search_url(origin, destination, departure, return_date)

    try:
        try:
            await page.goto(query_url, wait_until="domcontentloaded", timeout=timeout_ms)
        except Exception:
            await page.goto(query_url, timeout=timeout_ms)

        # Cookie consent dialog handling
        try:
            for btn_name in ("Reject all", "Hylkää kaikki", "Decline all", "Accept all", "Hyväksy kaikki"):
                btn = page.get_by_role("button", name=btn_name, exact=False)
                if await btn.count() > 0 and await btn.first.is_visible():
                    await btn.first.click(timeout=2000)
                    await page.wait_for_timeout(500)
                    break
        except Exception:
            pass

        # Check for challenge / bot detection
        page_text = await page.locator("body").inner_text()
        if is_challenge_page(page.url, page_text):
            print("[Skyscanner] Anti-bot challenge detected. Attempting automated session reset...", flush=True)
            await reset_session(page)
            try:
                await page.goto(query_url, wait_until="domcontentloaded", timeout=timeout_ms)
                await page.wait_for_timeout(2000)
                page_text = await page.locator("body").inner_text()
            except Exception:
                pass

        if is_challenge_page(page.url, page_text):
            print(
                "\n" + "!" * 70 + "\n"
                "[ACTION REQUIRED] Skyscanner anti-bot challenge detected in the visible browser window!\n"
                "Please press and hold the verification button in Chrome to continue.\n"
                f"Waiting up to {challenge_timeout_seconds} seconds for verification...\n"
                + "!" * 70,
                flush=True,
            )
            elapsed = 0
            while elapsed < challenge_timeout_seconds:
                await page.wait_for_timeout(2000)
                elapsed += 2
                page_text = await page.locator("body").inner_text()
                if not is_challenge_page(page.url, page_text):
                    print(f"\n[RESOLVED] Challenge passed after {elapsed}s! Resuming search.", flush=True)
                    break
            else:
                print("\n[TIMEOUT] Challenge was not resolved within allotted time.", file=sys.stderr, flush=True)

        # Wait for initial search view or challenge to appear
        try:
            await page.wait_for_function(
                """() => {
                    const text = document.body ? document.body.innerText.toLowerCase() : '';
                    if (text.includes('person or a robot') || text.includes('robotti') || text.includes('press & hold')) {
                        return true;
                    }
                    const pb = document.querySelector("[role='progressbar'], [class*='ProgressBar'], [class*='BpkProgress']");
                    const cards = document.querySelector("div[class*='Ticket'], div[class*='Card'], [data-testid*='itinerary'], [data-testid='flight-card']");
                    const skeletons = document.querySelector("[class*='TicketPlaceholder'], [class*='placeholder'], [class*='shimmer'], [class*='skeleton']");
                    const tabs = document.querySelector("[data-testid='FqsTab_CHEAPEST'], [id*='radio:CHEAPEST'], [data-testid*='CHEAPEST' i]");
                    return !!(pb || cards || skeletons || tabs || /results|tulosta|halvin|cheapest|direct|stops/i.test(text));
                }""",
                timeout=min(timeout_ms, 15000),
            )
        except Exception:
            pass

        # Switch to "Cheapest" / "Halvin" tab early so the view prioritizes the lowest fares
        try:
            cheapest_tab = page.locator("[data-testid='FqsTab_CHEAPEST'], [id*='radio:CHEAPEST'], [data-testid*='CHEAPEST' i], label:has-text('Cheapest'), label:has-text('Halvin'), button:has-text('Cheapest'), button:has-text('Halvin'), [aria-label*='Halvin' i], [aria-label*='Cheapest' i]")
            if await cheapest_tab.count() > 0 and await cheapest_tab.first.is_visible():
                await cheapest_tab.first.click(timeout=1500)
        except Exception:
            pass

        # Dynamic event-driven wait for the EXACT sign that all prices are scanned.
        # Skyscanner visual completion signs:
        # A) Progress bar reaches 100% (aria-valuenow == 100 or aria-valuenow == aria-valuemax, or Checked X of X)
        # B) Progress bar disappears / hides after scan
        # C) All shimmer/skeleton placeholders have vanished (count === 0)
        # D) Flight cards or confirmed result state is rendered in the DOM
        # E) Conductor / unified-search XHR reports completion status
        # Scrapes the very millisecond the completion sign appears with ZERO second-guessing.
        try:
            completion_waiter = page.wait_for_function(
                """() => {
                    const text = document.body ? document.body.innerText.toLowerCase() : '';
                    if (text.includes('person or a robot') || text.includes('robotti') || text.includes('press & hold')) {
                        return 'challenge';
                    }

                    // 1. Check progress bar state
                    const pb = document.querySelector("[role='progressbar'], [class*='ProgressBar'], [class*='progress-bar'], [class*='BpkProgress']");
                    const isPbVisible = pb && (pb.offsetParent !== null || window.getComputedStyle(pb).display !== 'none');

                    if (isPbVisible) {
                        const val = pb.getAttribute('aria-valuenow');
                        const max = pb.getAttribute('aria-valuemax') || '100';
                        if (val && max && Number(val) >= Number(max)) {
                            return 'progress_100';
                        }
                        const label = pb.getAttribute('aria-label') || '';
                        const match = label.match(/(\\d+)\\s*(?:of|\\/)\\s*(\\d+)/i);
                        if (match && Number(match[1]) >= Number(match[2])) {
                            return 'progress_all_providers';
                        }
                        // Progress bar is actively scanning
                        return false;
                    }

                    // 2. When progress bar is no longer visible, verify results settled
                    const cards = document.querySelectorAll(
                        "div[class*='Ticket'], div[class*='Card'], [data-testid*='itinerary'], [data-testid='flight-card']"
                    );
                    const skeletons = document.querySelectorAll(
                        "[class*='TicketPlaceholder'], [class*='placeholder'], [class*='shimmer'], [class*='skeleton'], [data-testid*='skeleton']"
                    );

                    // Visual sign: cards exist and zero skeletons remain
                    if (cards.length > 0 && skeletons.length === 0) {
                        return 'results_settled';
                    }

                    // Empty results confirmed
                    if (text.includes('ei tuloksia') || text.includes('no results found') || text.includes('no flights found') || text.includes('mitään ei löytynyt')) {
                        return 'no_results';
                    }

                    return false;
                }""",
                timeout=timeout_ms,
            )

            done, pending = await asyncio.wait(
                [
                    asyncio.create_task(completion_waiter),
                    asyncio.create_task(search_complete_event.wait()),
                ],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()

            # If XHR finished first, ensure DOM skeletons have settled
            try:
                await page.wait_for_function(
                    """() => {
                        const skeletons = document.querySelectorAll("[class*='TicketPlaceholder'], [class*='placeholder'], [class*='shimmer'], [class*='skeleton'], [data-testid*='skeleton']");
                        return skeletons.length === 0;
                    }""",
                    timeout=3000,
                )
            except Exception:
                pass

        except Exception:
            pass

        # Re-ensure "Cheapest" tab is active after all results loaded
        try:
            cheapest_tab = page.locator("[data-testid='FqsTab_CHEAPEST'], [id*='radio:CHEAPEST'], [data-testid*='CHEAPEST' i], label:has-text('Cheapest'), label:has-text('Halvin'), button:has-text('Cheapest'), button:has-text('Halvin'), [aria-label*='Halvin' i], [aria-label*='Cheapest' i]")
            if await cheapest_tab.count() > 0 and await cheapest_tab.first.is_visible():
                await cheapest_tab.first.click(timeout=1500)
                await page.wait_for_timeout(500)
        except Exception:
            pass

        # Scroll down slightly to trigger hydration of DOM cards
        try:
            await page.evaluate("window.scrollBy(0, 600)")
            await page.wait_for_timeout(500)
        except Exception:
            pass

        page_text = await page.locator("body").inner_text()

        # Check for challenge / bot detection after polling as well
        if is_challenge_page(page.url, page_text):
            print("[Skyscanner] Anti-bot challenge detected after poll. Attempting automated session reset...", flush=True)
            await reset_session(page)
            try:
                await page.goto(query_url, wait_until="domcontentloaded", timeout=timeout_ms)
                await page.wait_for_timeout(3000)
                page_text = await page.locator("body").inner_text()
            except Exception:
                pass

        if is_challenge_page(page.url, page_text):
            print(
                "\n" + "!" * 70 + "\n"
                "[ACTION REQUIRED] Skyscanner anti-bot challenge detected in the visible browser window!\n"
                "Please press and hold the verification button in Chrome to continue.\n"
                f"Waiting up to {challenge_timeout_seconds} seconds for verification...\n"
                + "!" * 70,
                flush=True,
            )
            elapsed = 0
            while elapsed < challenge_timeout_seconds:
                await page.wait_for_timeout(2000)
                elapsed += 2
                page_text = await page.locator("body").inner_text()
                if not is_challenge_page(page.url, page_text):
                    print(f"\n[RESOLVED] Challenge passed after {elapsed}s! Re-fetching results for {origin}->{destination}...", flush=True)
                    try:
                        await page.goto(query_url, wait_until="domcontentloaded", timeout=timeout_ms)
                        await page.wait_for_timeout(4000)
                    except Exception:
                        pass
                    page_text = await page.locator("body").inner_text()
                    break
            else:
                print("\n[TIMEOUT] Challenge was not resolved within allotted time.", file=sys.stderr, flush=True)

        # Parse captured data
        xhr_results = extract_from_xhr_payloads(captured_payloads)
        dom_cards = await extract_dom_candidate_cards(page)

        observation = make_observation(
            origin=origin,
            destination=destination,
            departure=departure,
            return_date=return_date,
            page_url=page.url,
            page_text=page_text,
            xhr_candidates=xhr_results,
            dom_candidates=dom_cards,
        )
        if observation["status"] != "observed":
            observation["page_url"] = page.url
            observation["visible_page_text"] = page_text[:2000]

        return observation

    finally:
        page.remove_listener("response", handle_response)


async def run(args: argparse.Namespace) -> int:
    cfg = load_config(args.config, args.trip)
    if args.depart_from and args.depart_to:
        pairs = build_search_pairs(
            depart_from=args.depart_from,
            depart_to=args.depart_to,
            return_from=args.return_from,
            return_to=args.return_to,
            min_stay_nights=args.min_stay_nights,
        )
    else:
        pairs = [p for p in cfg.trip.get_search_pairs() if p[1] is not None]

    if not pairs:
        raise ValueError("No date pairs meet the minimum-stay requirement.")

    profile_dir = Path(os.environ.get("SKYSCANNER_PROFILE_DIR", args.profile_dir)).resolve()
    results_dir = Path(args.results_dir).resolve()
    browser_executable = resolve_browser_executable(args.browser_executable) if args.browser_executable else None

    profile_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)

    print(f"[Skyscanner] Scanning {len(pairs)} exact date pairs.")
    print(f"[Skyscanner] Results: {results_dir}")
    if browser_executable:
        print(f"[Skyscanner] Using custom browser executable: {browser_executable}")
    else:
        print("[Skyscanner] Using Patchright stealth Chromium engine.")

    stamp = reporting.today_stamp()
    report_json: Path = results_dir / f"{REPORT_STEM}_{stamp}.json"
    report_csv: Path = results_dir / f"{REPORT_STEM}_{stamp}.csv"
    observations: list[dict[str, Any]] = []

    async with async_playwright() as playwright:
        context: BrowserContext = await playwright.chromium.launch_persistent_context(
            str(profile_dir),
            headless=False,
            executable_path=browser_executable,
            locale="en-GB",
            viewport={"width": 1440, "height": 1000},
            ignore_default_args=["--enable-automation"],
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-infobars",
            ],
        )
        await context.add_cookies([
            {"name": "ssculture", "value": "locale:::fi-FI&market:::FI&currency:::EUR", "domain": ".skyscanner.net", "path": "/"},
            {"name": "ssculture", "value": "locale:::fi-FI&market:::FI&currency:::EUR", "domain": ".skyscanner.fi", "path": "/"},
        ])
        page = context.pages[0] if context.pages else await context.new_page()

        # Clean session warm-up: clear any stale perimeterX cookies and warm up on homepage
        await reset_session(page)

        try:
            for index, (departure, return_date) in enumerate(pairs, start=1):
                pair_file = results_dir / f"{departure}_{return_date}.json"
                if args.skip_existing and pair_file.exists():
                    try:
                        cached = json.loads(pair_file.read_text(encoding="utf-8"))
                        if (
                            cached.get("status") == "observed"
                            and str(cached.get("fetched_at", "")).startswith(stamp)
                            and cached.get("origin") == args.origin
                            and cached.get("destination") == args.dest
                        ):
                            print(
                                f"[Skyscanner] [{index}/{len(pairs)}] {departure} -> {return_date} "
                                f"(cached today: €{cached.get('lowest_observed_price_eur')})"
                            )
                            observations.append(cached)
                            report_json, report_csv = write_daily_report(results_dir, observations)
                            continue
                    except Exception:
                        pass

                print(f"[Skyscanner] [{index}/{len(pairs)}] {departure} -> {return_date}")
                max_retries = 2
                attempt = 0
                while attempt <= max_retries:
                    attempt += 1
                    try:
                        observation = await scan_pair(
                            page,
                            origin=args.origin,
                            destination=args.dest,
                            departure=departure,
                            return_date=return_date,
                            timeout_ms=args.timeout_seconds * 1000,
                            poll_wait_seconds=getattr(args, "poll_wait_seconds", 30),
                            challenge_timeout_seconds=args.challenge_timeout_seconds,
                        )
                    except Exception as error:
                        observation = failed_observation(
                            origin=args.origin,
                            destination=args.dest,
                            departure=departure,
                            return_date=return_date,
                            error=error,
                        )

                    if observation["status"] not in NEEDS_HUMAN:
                        break

                    if attempt <= max_retries:
                        cooloff = 15 + attempt * 5
                        print(
                            f"[Skyscanner] Pair {departure} -> {return_date} flagged with {observation['status']}. "
                            f"Cooling off for {cooloff}s and retrying attempt {attempt + 1}/{max_retries + 1}...",
                            file=sys.stderr,
                            flush=True,
                        )
                        await page.wait_for_timeout(cooloff * 1000)
                        await reset_session(page)

                saved = save_observation(results_dir, observation)
                observations.append(observation)
                report_json, report_csv = write_daily_report(results_dir, observations)
                print(f"  {observation['status']}; lowest observed: €{observation['lowest_observed_price_eur']}; saved {saved.name}")

                if index < len(pairs):
                    jitter = random.uniform(1.0, 4.0)
                    pause_s = args.delay_seconds + jitter
                    print(f"  [Politeness pause: {pause_s:.1f}s]")
                    await page.wait_for_timeout(pause_s * 1000)

            # Final sweep pass: retry any remaining flagged pairs once more
            flagged = [obs for obs in observations if obs.get("status") in NEEDS_HUMAN]
            if flagged:
                print(f"\n[Skyscanner] Starting final retry sweep for {len(flagged)} flagged pair(s)...", flush=True)
                for sw_idx, fl in enumerate(flagged, 1):
                    dep = dt.date.fromisoformat(fl["departure_date"])
                    ret = dt.date.fromisoformat(fl["return_date"])
                    print(f"[Skyscanner] [Sweep {sw_idx}/{len(flagged)}] Retrying {dep} -> {ret}...")
                    try:
                        await reset_session(page)
                        sw_obs = await scan_pair(
                            page,
                            origin=args.origin,
                            destination=args.dest,
                            departure=dep,
                            return_date=ret,
                            timeout_ms=args.timeout_seconds * 1000,
                            poll_wait_seconds=getattr(args, "poll_wait_seconds", 30),
                            challenge_timeout_seconds=args.challenge_timeout_seconds,
                        )
                        if sw_obs["status"] not in NEEDS_HUMAN:
                            save_observation(results_dir, sw_obs)
                            for i, obs_item in enumerate(observations):
                                if obs_item.get("departure_date") == fl["departure_date"] and obs_item.get("return_date") == fl["return_date"]:
                                    observations[i] = sw_obs
                            report_json, report_csv = write_daily_report(results_dir, observations)
                            print(f"  [Sweep Recovered!] {sw_obs['status']}; lowest observed: €{sw_obs['lowest_observed_price_eur']}")
                    except Exception as sw_err:
                        print(f"  [Sweep Failed]: {sw_err}", file=sys.stderr)
                    await page.wait_for_timeout(args.delay_seconds * 1000)

        finally:
            await context.close()

    print(f"\n[Skyscanner] Daily Skyscanner report written: {report_json.name}, {report_csv.name}")
    failed_count = sum(1 for obs in observations if obs.get("status") != "observed")
    if observations and failed_count == len(observations):
        return 2
    if failed_count > 0:
        return 1
    return 0


def parser(argv: list[str] | None = None) -> argparse.ArgumentParser:
    pre_p = argparse.ArgumentParser(add_help=False)
    pre_p.add_argument("--config", help="Optional path to config.toml")
    pre_p.add_argument("--trip", help="Optional path or name of trip config file")
    pre_args, _ = pre_p.parse_known_args(argv)

    cfg = load_config(pre_args.config, pre_args.trip)
    res = argparse.ArgumentParser(description="Human-supervised Skyscanner matrix scanner.")
    res.add_argument("--config", default=pre_args.config, help="Optional path to config.toml")
    res.add_argument("--trip", default=pre_args.trip, help="Optional path or name of trip config file")
    res.add_argument("--origin", default=cfg.trip.origin)
    res.add_argument("--dest", default=cfg.trip.dest)

    if cfg.trip.date_mode == "range":
        res.add_argument("--depart-from", default=cfg.trip.depart_from, help="Start of departure range")
        res.add_argument("--depart-to", default=cfg.trip.depart_to, help="End of departure range")
        res.add_argument("--return-from", default=cfg.trip.return_from, help="Start of return range")
        res.add_argument("--return-to", default=cfg.trip.return_to, help="End of return range")
        res.add_argument("--window-start", help="Trip window earliest departure date")
        res.add_argument("--window-end", help="Trip window latest return date")
    else:
        res.add_argument("--window-start", default=cfg.trip.window_start, help="Earliest departure date")
        res.add_argument("--window-end", default=cfg.trip.window_end, help="Latest return date")
        res.add_argument("--depart-from", help="Optional explicit start of departure range")
        res.add_argument("--depart-to", help="Optional explicit end of departure range")
        res.add_argument("--return-from", help="Optional explicit start of return range")
        res.add_argument("--return-to", help="Optional explicit end of return range")

    res.add_argument("--min-stay-nights", type=int, default=cfg.trip.min_stay_nights)
    res.add_argument("--delay-seconds", type=int, default=cfg.skyscanner.delay_seconds, help="Delay between searches in seconds")
    res.add_argument("--timeout-seconds", type=int, default=cfg.skyscanner.timeout_seconds, help="Page load timeout in seconds")
    res.add_argument("--poll-wait-seconds", type=int, default=cfg.skyscanner.poll_wait_seconds, help="Wait time in seconds for Skyscanner provider polling")
    res.add_argument("--challenge-timeout-seconds", type=int, default=cfg.skyscanner.challenge_timeout_seconds, help="Wait time for human challenge solver")
    res.add_argument("--skip-existing", action=argparse.BooleanOptionalAction, default=cfg.execution.skip_existing, help="Skip queries already observed today")
    res.add_argument("--profile-dir", default=str(cfg.skyscanner.resolved_profile_dir()))
    res.add_argument("--results-dir", default=str(cfg.skyscanner.resolved_results_dir()))
    res.add_argument("--browser-executable", default=cfg.execution.browser_executable, help="Custom path to Chrome/Edge executable")
    return res


def main() -> None:
    configure_stdio()
    args = parser().parse_args()
    try:
        raise SystemExit(asyncio.run(run(args)))
    except (ValueError, OSError) as error:
        raise SystemExit(f"Error: {error}") from error


if __name__ == "__main__":
    main()
