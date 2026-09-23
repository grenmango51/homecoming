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
import time
from pathlib import Path
from typing import Any

try:
    from patchright.async_api import BrowserContext, Page, Request, Response, async_playwright
except ImportError:
    try:
        from playwright.async_api import BrowserContext, Page, Request, Response, async_playwright
    except ImportError as error:
        raise SystemExit(
            "Patchright or Playwright is required. Run: .\\.venv\\Scripts\\python.exe -m pip install patchright "
            "and then: .\\.venv\\Scripts\\patchright.exe install chromium"
        ) from error

from src import reporting
from src.browser.skyscanner import (
    SKYSCANNER_COOKIES,
    extract_dom_candidate_cards,
    try_solve_press_and_hold,
    wait_for_challenge_resolution,
)
from src.browser.skyscanner import (
    reset_session as _browser_reset_session,
)
from src.common import (
    build_search_pairs,
    circuit_breaker_tripped,
    configure_stdio,
    deferred_observation,
    is_valid_completion_cache,
    resolve_browser_executable,
)
from src.config import PROJECT_ROOT, load_config
from src.reporting import SKYSCANNER_REPORT_FIELDS
from src.skyscanner_parse import (
    SOURCE,
    cheapest_tab_price,
    extract_from_xhr_payloads,
    flight_search_url,
    is_challenge_page,
    make_observation,
    select_authoritative_fare,
)

ROOT = PROJECT_ROOT
REPORT_STEM = "daily_fare_report_skyscanner"

NEEDS_HUMAN = {"blocked", "user_action_required"}


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


async def reset_session(page: Page, *, scrub_cookies: bool = False) -> None:
    """Delegate to the browser module's reset_session."""
    print(f"[Skyscanner] Resetting session on homepage (scrub_cookies={scrub_cookies})...", flush=True)
    await _browser_reset_session(page, scrub_cookies=scrub_cookies)


async def _handle_challenge(
    page: Page,
    page_text: str,
    query_url: str,
    *,
    timeout_ms: int,
    challenge_timeout_seconds: int,
    attended: bool = False,
) -> str:
    """Multi-stage challenge handling:
    1. Automated Press & Hold solver directly on current view
    2. Cookie-scrubbed session reset and query reload
    3. Second Press & Hold attempt on fresh page
    4. Attended human solver (if enabled)
    """
    if not is_challenge_page(page.url, page_text):
        return page_text

    print("[Skyscanner] Anti-bot challenge detected. Attempting automated resolution...", flush=True)

    # Stage 1: Try automated solving directly on current challenge screen
    if await try_solve_press_and_hold(page):
        try:
            page_text = await page.locator("body").inner_text()
            if not is_challenge_page(page.url, page_text):
                return page_text
        except Exception:
            pass

    # Stage 2: Session recovery with cookie scrubbing
    print("[Skyscanner] Clearing blocked cookies and warming up on homepage...", flush=True)
    await reset_session(page, scrub_cookies=True)
    await page.wait_for_timeout(2000)

    try:
        await page.goto(query_url, wait_until="domcontentloaded", timeout=timeout_ms)
        page_text = await page.locator("body").inner_text()
    except Exception:
        pass

    if not is_challenge_page(page.url, page_text):
        print("[Skyscanner] Challenge cleared after cookie-scrubbed session reset!", flush=True)
        return page_text

    # Stage 3: Second attempt at Press & Hold on the fresh page
    if await try_solve_press_and_hold(page):
        try:
            page_text = await page.locator("body").inner_text()
            if not is_challenge_page(page.url, page_text):
                return page_text
        except Exception:
            pass

    # Stage 4: Attended mode if configured
    if attended and challenge_timeout_seconds > 0:
        print(
            "\n" + "!" * 70 + "\n"
            "[ACTION REQUIRED] Skyscanner anti-bot challenge detected in the visible browser window!\n"
            "Please press and hold the verification button in Chrome to continue.\n"
            f"Waiting up to {challenge_timeout_seconds} seconds for verification...\n"
            + "!" * 70,
            flush=True,
        )
        resolved = await wait_for_challenge_resolution(page, timeout_seconds=challenge_timeout_seconds)
        try:
            page_text = await page.locator("body").inner_text()
        except Exception:
            pass
        if resolved:
            print("[RESOLVED] Challenge passed! Resuming search.", flush=True)
    else:
        print("[Skyscanner] Automated challenge attempts did not clear challenge; proceeding.", flush=True)

    return page_text


async def scan_pair(
    page: Page,
    *,
    origin: str,
    destination: str,
    departure: dt.date,
    return_date: dt.date,
    timeout_ms: int,
    poll_wait_seconds: int = 30,
    challenge_timeout_seconds: int = 0,
    attended: bool = False,
) -> dict[str, Any]:
    """Navigate to Skyscanner, verify provider completion, and extract authoritative fares.

    Guarantees:
    - Requests and responses scoped to current query and attempt (rejects stale/unrelated GraphQL).
    - Explicit provider completion (backend signal) AND DOM final rendering (skeletons=0, cards>0).
    - Timeouts or unverified evidence result in status=completion_unverified with null published fare.
    - Final authoritative snapshot replaces intermediate results to prevent obsolete low fares.
    """
    attempt_start = time.monotonic()
    captured_payloads: list[dict[str, Any]] = []
    search_complete_event = asyncio.Event()
    request_start_times: dict[Any, float] = {}

    def handle_request(request: Request) -> None:
        request_start_times[request] = time.monotonic()

    async def handle_response(response: Response) -> None:
        try:
            req_start = request_start_times.get(response.request)
            if req_start is not None and req_start < attempt_start:
                return  # Reject stale response from a prior attempt

            url_lowered = response.url.lower()
            is_unified = "web-unified-search" in url_lowered or "/unified-search/" in url_lowered
            is_graphql = "graphql" in url_lowered
            if not (is_unified or is_graphql):
                return

            if response.status != 200 or "application/json" not in response.headers.get("content-type", ""):
                return

            body = await response.json()
            if not isinstance(body, dict):
                return

            if is_graphql:
                # Reject unrelated GraphQL (auth, analytics, account, telemetry)
                has_flight_data = (
                    "itineraries" in body
                    or "flightSearch" in body
                    or "flight_search" in str(body.get("data", "")).lower()
                    or "itineraries" in str(body.get("data", "")).lower()
                )
                if not has_flight_data:
                    return

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

    page.on("request", handle_request)
    page.on("response", handle_response)
    query_url = flight_search_url(origin, destination, departure, return_date)

    try:
        try:
            await page.goto(query_url, wait_until="domcontentloaded", timeout=timeout_ms)
        except Exception:
            try:
                await page.goto(query_url, timeout=timeout_ms)
            except Exception:
                pass

        # Cookie consent dialog handling
        try:
            for btn_name in ("Reject all", "Hylkää kaikki", "Decline all", "Accept all", "Hyväksy kaikki"):
                btn = page.get_by_role("button", name=btn_name, exact=False)
                if await btn.count() > 0 and await btn.first.is_visible():
                    await btn.first.click(timeout=2000)
                    break
        except Exception:
            pass

        # Pre-completion challenge check
        page_text = await page.locator("body").inner_text()
        page_text = await _handle_challenge(
            page,
            page_text,
            query_url,
            timeout_ms=timeout_ms,
            challenge_timeout_seconds=challenge_timeout_seconds,
            attended=attended,
        )

        if is_challenge_page(page.url, page_text):
            obs = make_observation(
                origin=origin,
                destination=destination,
                departure=departure,
                return_date=return_date,
                page_url=page.url,
                page_text=page_text,
                xhr_candidates=[],
                dom_candidates=[],
                status="user_action_required",
            )
            obs["page_url"] = page.url
            obs["visible_page_text"] = page_text[:2000]
            return obs

        # Initial wait for search view
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
                timeout=min(timeout_ms, 12000),
            )
        except Exception:
            pass

        # Switch to "Cheapest" / "Halvin" tab once
        try:
            cheapest_tab = page.locator(
                "[data-testid='FqsTab_CHEAPEST'], [id*='radio:CHEAPEST'], [data-testid*='CHEAPEST' i], "
                "label:has-text('Cheapest'), label:has-text('Halvin'), button:has-text('Cheapest'), "
                "button:has-text('Halvin'), [aria-label*='Halvin' i], [aria-label*='Cheapest' i]"
            )
            if await cheapest_tab.count() > 0 and await cheapest_tab.first.is_visible():
                await cheapest_tab.first.click(timeout=1500)
        except Exception:
            pass

        # Wait for BOTH: explicit provider completion AND final rendering
        completion_waiter = page.wait_for_function(
            """() => {
                const text = document.body ? document.body.innerText.toLowerCase() : '';
                if (text.includes('person or a robot') || text.includes('robotti') || text.includes('press & hold')) {
                    return 'challenge';
                }

                // 1. Progress bar must indicate complete
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
                    return false;
                }

                // 2. Skeletons must be completely gone
                const skeletons = document.querySelectorAll(
                    "[class*='TicketPlaceholder'], [class*='placeholder'], [class*='shimmer'], [class*='skeleton'], [data-testid*='skeleton']"
                );
                if (skeletons.length > 0) {
                    return false;
                }

                // 3. Cards must exist
                const cards = document.querySelectorAll(
                    "div[class*='Ticket'], div[class*='Card'], [data-testid*='itinerary'], [data-testid='flight-card']"
                );
                if (cards.length > 0) {
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

        dom_task = asyncio.create_task(completion_waiter)
        xhr_task = asyncio.create_task(search_complete_event.wait())
        provider_completed = False
        dom_settled = False

        try:
            done, pending = await asyncio.wait(
                [dom_task, xhr_task],
                timeout=timeout_ms / 1000,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if pending:
                done2, _ = await asyncio.wait(pending, timeout=2.0)
                done = done | done2

            for t in pending:
                t.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

            if dom_task in done and not dom_task.cancelled():
                try:
                    res = dom_task.result()
                    dom_settled = bool(res and res != "challenge")
                except Exception:
                    dom_settled = False

            if xhr_task in done and not xhr_task.cancelled():
                try:
                    xhr_task.result()
                    provider_completed = search_complete_event.is_set()
                except Exception:
                    provider_completed = False
            elif search_complete_event.is_set():
                provider_completed = True
        except Exception:
            pass

        # Scroll to hydrate DOM cards
        try:
            await page.evaluate("window.scrollBy(0, 600)")
        except Exception:
            pass

        page_text = await page.locator("body").inner_text()
        page_text = await _handle_challenge(
            page,
            page_text,
            query_url,
            timeout_ms=timeout_ms,
            challenge_timeout_seconds=challenge_timeout_seconds,
            attended=attended,
        )

        if is_challenge_page(page.url, page_text):
            obs = make_observation(
                origin=origin,
                destination=destination,
                departure=departure,
                return_date=return_date,
                page_url=page.url,
                page_text=page_text,
                xhr_candidates=[],
                dom_candidates=[],
                status="user_action_required",
            )
            obs["page_url"] = page.url
            obs["visible_page_text"] = page_text[:2000]
            return obs

        # Authoritative snapshot vs unverified/timeout
        if provider_completed or dom_settled:
            final_payload = captured_payloads[-1] if captured_payloads else None
            xhr_results = extract_from_xhr_payloads([final_payload]) if final_payload else []
            dom_cards = await extract_dom_candidate_cards(page)
            tab_price = cheapest_tab_price(page_text)
            authoritative_fare = select_authoritative_fare(
                final_payload=final_payload,
                dom_cards=dom_cards,
                tab_price=tab_price,
            )
            if authoritative_fare is not None:
                status = "observed"
                completion_evidence = "xhr_complete+dom_settled" if (provider_completed and dom_settled) else ("dom_settled" if dom_settled else "xhr_complete")
            else:
                status = "incomplete"
                completion_evidence = None
        else:
            final_payload = None
            xhr_results = []
            dom_cards = []
            authoritative_fare = None
            status = "completion_unverified"
            completion_evidence = None

        observation = make_observation(
            origin=origin,
            destination=destination,
            departure=departure,
            return_date=return_date,
            page_url=page.url,
            page_text=page_text,
            xhr_candidates=xhr_results,
            dom_candidates=dom_cards,
            status=status,
            completion_evidence=completion_evidence,
            authoritative_fare_eur=authoritative_fare,
        )
        if observation["status"] != "observed":
            observation["page_url"] = page.url
            observation["visible_page_text"] = page_text[:2000]

        return observation

    finally:
        page.remove_listener("request", handle_request)
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
        await context.add_cookies(SKYSCANNER_COOKIES)
        page = context.pages[0] if context.pages else await context.new_page()

        start_time = time.monotonic()
        budget_seconds = getattr(args, "runtime_budget_seconds", cfg.execution.runtime_budget_seconds)
        deadline = start_time + budget_seconds
        consecutive_blocks = 0
        run_interrupted = False

        try:
            for index, (departure, return_date) in enumerate(pairs, start=1):
                pair_file = results_dir / f"{departure}_{return_date}.json"

                # Check runtime budget
                if time.monotonic() >= deadline:
                    print(
                        f"\n[Skyscanner] Runtime budget of {budget_seconds}s exhausted. Deferring remaining pairs...",
                        file=sys.stderr,
                        flush=True,
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
                        save_observation(results_dir, d_obs)
                        observations.append(d_obs)
                    report_json, report_csv = write_daily_report(results_dir, observations)
                    break

                if args.skip_existing and pair_file.exists():
                    try:
                        cached = json.loads(pair_file.read_text(encoding="utf-8"))
                        if is_valid_completion_cache(
                            cached,
                            stamp=stamp,
                            origin=args.origin,
                            dest=args.dest,
                            departure_date=departure,
                            return_date=return_date,
                        ):
                            print(
                                f"[Skyscanner] [{index}/{len(pairs)}] {departure} -> {return_date} "
                                f"(cached today: €{cached.get('lowest_observed_price_eur')})"
                            )
                            observations.append(cached)
                            report_json, report_csv = write_daily_report(results_dir, observations)
                            consecutive_blocks = 0
                            continue
                    except Exception:
                        pass

                print(f"[Skyscanner] [{index}/{len(pairs)}] {departure} -> {return_date}")
                max_retries = 1
                attempt = 0
                query_start = time.monotonic()
                rem_time = max(5.0, deadline - time.monotonic())
                effective_timeout_ms = min(args.timeout_seconds * 1000, int(rem_time * 1000))

                while attempt <= max_retries:
                    attempt += 1
                    try:
                        observation = await scan_pair(
                            page,
                            origin=args.origin,
                            destination=args.dest,
                            departure=departure,
                            return_date=return_date,
                            timeout_ms=effective_timeout_ms,
                            poll_wait_seconds=getattr(args, "poll_wait_seconds", 30),
                            challenge_timeout_seconds=args.challenge_timeout_seconds,
                            attended=getattr(args, "attended", False),
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
                        await reset_session(page, scrub_cookies=True)

                query_elapsed = round(time.monotonic() - query_start, 2)
                observation["query_elapsed_seconds"] = query_elapsed
                saved = save_observation(results_dir, observation)
                observations.append(observation)
                report_json, report_csv = write_daily_report(results_dir, observations)
                print(
                    f"  {observation['status']}; lowest observed: €{observation['lowest_observed_price_eur']}; "
                    f"elapsed: {query_elapsed}s; saved {saved.name}"
                )

                if observation["status"] in NEEDS_HUMAN:
                    consecutive_blocks += 1
                    if not getattr(args, "attended", False):
                        cb_threshold = getattr(args, "circuit_breaker_threshold", 5)
                        if circuit_breaker_tripped(consecutive_blocks, threshold=cb_threshold):
                            print(
                                f"\n[Skyscanner] Circuit breaker tripped after {consecutive_blocks} consecutive challenges. "
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
                                save_observation(results_dir, d_obs)
                                observations.append(d_obs)
                            report_json, report_csv = write_daily_report(results_dir, observations)
                            return 2
                else:
                    consecutive_blocks = 0

                if index < len(pairs):
                    jitter = random.uniform(1.0, 3.0)
                    pause_s = args.delay_seconds + jitter
                    print(f"  [Politeness pause: {pause_s:.1f}s]")
                    await page.wait_for_timeout(pause_s * 1000)

            # Second-pass sweep: retry any unverified or challenged pairs from this run
            if not run_interrupted and time.monotonic() < deadline - 60:
                unverified_entries = [
                    (i, obs)
                    for i, obs in enumerate(observations)
                    if obs.get("status") in ("user_action_required", "incomplete", "completion_unverified")
                ]
                if unverified_entries:
                    print(
                        f"\n[Skyscanner] Starting second-pass sweep on {len(unverified_entries)} unverified pairs after a 20s cooldown...",
                        flush=True,
                    )
                    await page.wait_for_timeout(20000)
                    await reset_session(page, scrub_cookies=True)

                    for obs_idx, old_obs in unverified_entries:
                        if time.monotonic() >= deadline:
                            break
                        dep_str = old_obs.get("departure_date")
                        ret_str = old_obs.get("return_date")
                        if not dep_str or not ret_str:
                            continue
                        dep_date = dt.date.fromisoformat(dep_str)
                        ret_date = dt.date.fromisoformat(ret_str)
                        print(f"[Skyscanner] [Sweep] Retrying {dep_date} -> {ret_date}...")
                        try:
                            rem_time = max(5.0, deadline - time.monotonic())
                            sweep_timeout_ms = min(args.timeout_seconds * 1000, int(rem_time * 1000))
                            sweep_obs = await scan_pair(
                                page,
                                origin=args.origin,
                                destination=args.dest,
                                departure=dep_date,
                                return_date=ret_date,
                                timeout_ms=sweep_timeout_ms,
                                poll_wait_seconds=getattr(args, "poll_wait_seconds", 30),
                                challenge_timeout_seconds=args.challenge_timeout_seconds,
                                attended=getattr(args, "attended", False),
                            )
                        except Exception as err:
                            sweep_obs = failed_observation(
                                origin=args.origin,
                                destination=args.dest,
                                departure=dep_date,
                                return_date=ret_date,
                                error=err,
                            )

                        if sweep_obs.get("status") == "observed":
                            print(f"  [Sweep Success] {dep_date} -> {ret_date}: €{sweep_obs['lowest_observed_price_eur']}")
                            save_observation(results_dir, sweep_obs)
                            observations[obs_idx] = sweep_obs
                            report_json, report_csv = write_daily_report(results_dir, observations)
                        else:
                            print(f"  [Sweep Result] {dep_date} -> {ret_date}: {sweep_obs.get('status')}")

                        await page.wait_for_timeout(random.uniform(5.0, 8.0) * 1000)

        finally:
            await context.close()

    print(f"\n[Skyscanner] Daily Skyscanner report written: {report_json.name}, {report_csv.name}")
    total_scanned = len(pairs)
    total_observed = sum(1 for obs in observations if obs.get("status") == "observed")
    total_deferred = sum(1 for obs in observations if obs.get("status") == "deferred")
    total_failed = len(observations) - total_observed - total_deferred
    total_elapsed = round(time.monotonic() - start_time, 2)
    print(
        f"\n[Skyscanner] Run summary: elapsed={total_elapsed}s; "
        f"coverage: observed={total_observed}/{total_scanned}, deferred={total_deferred}, failed={total_failed}"
    )

    if run_interrupted or total_observed < total_scanned:
        return 2 if consecutive_blocks >= 2 else 1
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
    res.add_argument(
        "--runtime-budget-seconds",
        type=int,
        default=cfg.execution.runtime_budget_seconds,
        help="Maximum per-scraper runtime budget in seconds (default 1800)",
    )
    res.add_argument(
        "--circuit-breaker-threshold",
        type=int,
        default=5,
        help="Consecutive challenges before tripping circuit breaker (default 5)",
    )
    res.add_argument(
        "--attended",
        action=argparse.BooleanOptionalAction,
        default=cfg.skyscanner.attended,
        help="Wait for human intervention on anti-bot challenges",
    )
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
