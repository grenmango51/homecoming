#!/usr/bin/env python3
"""Human-supervised Skyscanner fare scanner for Helsinki (HEL) to Hanoi (HAN).

This program loads the rendered Skyscanner page in a visible, persistent browser context.
It captures internal search API responses and extracts rendered flight fare cards.
It never follows booking affiliate links or makes purchase actions.
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

from src.common import (
    PRICE_RE,
    PRICE_SUFFIX_RE,
    date_range,
    build_search_pairs,
    resolve_browser_executable,
    parse_numeric_price,
    duration_minutes,
)
from src.config import load_config, PROJECT_ROOT

ROOT = PROJECT_ROOT
DEFAULT_PROFILE_DIR = ROOT / ".skyscanner-profile"
DEFAULT_RESULTS_DIR = ROOT / "flight_results_skyscanner"
SKYSCANNER_BASE = "https://www.skyscanner.net"

CHEAPEST_TAB_RE = re.compile(
    r"(?:halvin|cheapest)\s*(?:alk\.|alkaen|from)?\s*(?:€\s*|EUR\s*)?([0-9][0-9.,\s]*)\s*(?:€|eur)?",
    re.IGNORECASE,
)
DURATION_EN_RE = re.compile(r"\b(\d{1,2})\s*(?:h|hr|hours?)\s*(?:(\d{1,2})\s*(?:m|min|minutes?))?\b", re.IGNORECASE)
DURATION_FI_RE = re.compile(r"\b(\d{1,2})\s*(?:t|tuntia)\s*(?:(\d{1,2})\s*(?:min|minuuttia))?\b", re.IGNORECASE)


def cheapest_tab_price(text: str) -> float | None:
    """Extract price specifically labeled as the cheapest / halvin option."""
    match = CHEAPEST_TAB_RE.search(text)
    if not match:
        return None
    return parse_numeric_price(match.group(1))


def to_yymmdd(date_obj: dt.date) -> str:
    """Format date to Skyscanner YYMMDD string (e.g. 2026-12-09 -> '261209')."""
    return date_obj.strftime("%y%m%d")


def flight_search_url(
    origin: str,
    destination: str,
    departure: dt.date,
    return_date: dt.date,
    *,
    base_url: str = SKYSCANNER_BASE,
) -> str:
    """Build canonical Skyscanner round-trip flight search URL."""
    dep_str = to_yymmdd(departure)
    ret_str = to_yymmdd(return_date)
    orig = origin.strip().lower()
    dest = destination.strip().lower()
    return (
        f"{base_url}/transport/flights/{orig}/{dest}/{dep_str}/{ret_str}/"
        f"?adultsv2=1&cabinclass=economy&childrenv2=&ref=home&rtn=1"
        f"&outboundaltsenabled=false&inboundaltsenabled=false&preferdirects=false"
    )


def money_values(text: str) -> list[float]:
    """Find all potential flight price figures in euro format."""
    values: list[float] = []
    for match in PRICE_RE.findall(text) + PRICE_SUFFIX_RE.findall(text):
        num = parse_numeric_price(match)
        if num is not None:
            values.append(num)
    return values


def is_challenge_page(url: str, text: str) -> bool:
    """Determine if current view is an anti-bot challenge / verification screen."""
    lowered_url = url.lower()
    lowered_text = text.lower()
    if any(marker in lowered_url for marker in ("captcha", "/sttc/px/", "perimeterx")):
        return True
    if any(
        phrase in lowered_text
        for phrase in (
            "robotti",
            "press & hold",
            "verify you are human",
            "unusual traffic",
            "oletko oikea henkilö vai robotti",
        )
    ):
        return True
    return False


def page_status(url: str, text: str) -> str:
    """Classify Skyscanner page status."""
    if is_challenge_page(url, text):
        return "user_action_required"
    lowered = text.lower()
    if "sign in" in lowered and len(text) < 800:
        return "user_action_required"
    if "oops, something went wrong" in lowered or ("mitään ei löytynyt" in lowered and "tulosta" not in lowered):
        return "incomplete"
    if any(marker in lowered for marker in ("results", "tulosta", "halvin", "cheapest", "paras", "nopein", "suora", "direct", "stops")):
        return "observed"
    return "incomplete"


def parse_itinerary_json(itinerary: dict[str, Any]) -> dict[str, Any] | None:
    """Extract clean structured flight card information from Skyscanner XHR JSON item."""
    try:
        price_raw = itinerary.get("price", {}).get("raw")
        formatted = itinerary.get("price", {}).get("formatted", "")
        price_eur = float(price_raw) if price_raw is not None else None
        if price_eur is None and formatted:
            prices = money_values(formatted)
            if prices:
                price_eur = prices[0]

        legs = itinerary.get("legs", [])
        parsed_legs = []
        for leg in legs:
            marketing_carriers = [c.get("name") for c in leg.get("carriers", {}).get("marketing", []) if c.get("name")]
            operating_carriers = [c.get("name") for c in leg.get("carriers", {}).get("operating", []) if c.get("name")]
            parsed_legs.append(
                {
                    "departure": leg.get("departure"),
                    "arrival": leg.get("arrival"),
                    "duration_minutes": leg.get("durationInMinutes") or leg.get("duration"),
                    "stop_count": leg.get("stopCount", 0),
                    "carriers": marketing_carriers or operating_carriers,
                    "operating_carriers": operating_carriers,
                }
            )

        deal_options = itinerary.get("pricingOptions", [])
        deals = []
        for opt in deal_options[:5]:
            agent = opt.get("agentName")
            if not agent and opt.get("agents"):
                agent = opt["agents"][0].get("name")
            opt_price = opt.get("price", {}).get("raw")
            if agent:
                deals.append({"seller": agent, "price_eur": opt_price})

        return {
            "price_eur": price_eur,
            "legs": parsed_legs,
            "deals_count": len(deal_options),
            "sample_deals": deals,
        }
    except Exception:
        return None


def extract_from_xhr_payloads(payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract all valid flight itineraries from captured Skyscanner XHR payloads."""
    results: list[dict[str, Any]] = []
    for data in payloads:
        raw_itineraries = None
        if isinstance(data, dict):
            if "itineraries" in data and isinstance(data["itineraries"], dict):
                raw_itineraries = data["itineraries"].get("results")
            elif "content" in data and isinstance(data["content"], dict):
                results_dict = data["content"].get("results", {})
                if isinstance(results_dict, dict) and "itineraries" in results_dict:
                    raw_itins = results_dict["itineraries"]
                    raw_itineraries = list(raw_itins.values()) if isinstance(raw_itins, dict) else raw_itins

        if isinstance(raw_itineraries, list):
            for item in raw_itineraries:
                if isinstance(item, dict):
                    parsed = parse_itinerary_json(item)
                    if parsed and parsed.get("price_eur") is not None:
                        results.append(parsed)

    results.sort(key=lambda x: x["price_eur"] if x["price_eur"] is not None else float("inf"))
    return results


async def extract_dom_candidate_cards(page: Page) -> list[str]:
    """Fallback extraction of visible flight cards in the rendered DOM."""
    locators = page.locator(
        "[data-testid='flight-card'], [data-testid='itinerary-card'], [role='listitem'], div[class*='FlightCard']"
    )
    count = await locators.count()
    cards: list[str] = []
    seen: set[str] = set()
    for i in range(min(count, 40)):
        try:
            txt = await locators.nth(i).inner_text()
            compact = " ".join(txt.split())
            if len(compact) < 20:
                continue
            if money_values(compact):
                key = compact[:100].lower()
                if key not in seen:
                    seen.add(key)
                    cards.append(compact[:3000])
        except Exception:
            continue
    return cards


def make_observation(
    *,
    origin: str,
    destination: str,
    departure: dt.date,
    return_date: dt.date,
    page_url: str,
    page_text: str,
    xhr_candidates: list[dict[str, Any]],
    dom_candidates: list[str],
) -> dict[str, Any]:
    """Construct structured, persistent observation dictionary."""
    status = page_status(page_url, page_text)

    observed_prices: list[float] = []
    tab_price = cheapest_tab_price(page_text)
    if tab_price is not None:
        observed_prices.append(tab_price)

    for c in xhr_candidates:
        p = c.get("price_eur")
        if p is not None:
            observed_prices.append(float(p))

    for block in dom_candidates:
        observed_prices.extend(money_values(block))

    if not observed_prices and status == "observed":
        observed_prices.extend(money_values(page_text))

    lowest_price: float | None = min(observed_prices) if observed_prices else None

    return {
        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source": "Skyscanner UI",
        "origin": origin,
        "destination": destination,
        "departure_date": departure.isoformat(),
        "return_date": return_date.isoformat(),
        "stay_nights": (return_date - departure).days,
        "status": status,
        "lowest_observed_price_eur": lowest_price,
        "protection_label": "not_flagged_by_skyscanner",
        "seller_confirmation_required": True,
        "itinerary_count": len(xhr_candidates) or len(dom_candidates),
        "candidate_cards": xhr_candidates[:20] if xhr_candidates else [
            {
                "raw_text": block,
                "observed_prices_eur": money_values(block),
                "duration_minutes": duration_minutes(block),
            }
            for block in dom_candidates[:20]
        ],
        "notes": [
            "Read from the rendered Skyscanner page & unified-search API; fares are volatile.",
            "No booking, checkout, or affiliate handoff was executed.",
            "Always verify baggage allowance and transfer connection terms with chosen seller.",
        ],
    }


def failed_observation(
    *, origin: str, destination: str, departure: dt.date, return_date: dt.date, error: Exception
) -> dict[str, Any]:
    """Record a failure observation so the matrix run continues gracefully."""
    return {
        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source": "Skyscanner UI",
        "origin": origin,
        "destination": destination,
        "departure_date": departure.isoformat(),
        "return_date": return_date.isoformat(),
        "stay_nights": (return_date - departure).days,
        "status": "error",
        "lowest_observed_price_eur": None,
        "protection_label": "unknown",
        "seller_confirmation_required": True,
        "itinerary_count": 0,
        "candidate_cards": [],
        "error": f"{type(error).__name__}: {error}",
        "notes": ["The query failed; this pair should be retried."],
    }


def save_observation(results_dir: Path, observation: dict[str, Any]) -> Path:
    results_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{observation['departure_date']}_{observation['return_date']}.json"
    path = results_dir / filename
    path.write_text(json.dumps(observation, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_daily_report(
    results_dir: Path,
    observations: list[dict[str, Any]],
    reference_price: float | None = 782.0,
) -> tuple[Path, Path]:
    """Write standardized daily summary CSV and JSON reports."""
    results_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().astimezone().strftime("%Y-%m-%d")
    rows = sorted(observations, key=lambda item: (item["departure_date"], item["return_date"]))
    reference = next(
        (item for item in rows if item["departure_date"] == "2026-12-09" and item["return_date"] == "2027-01-09"),
        None,
    )

    report = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source": "Skyscanner UI",
        "reference_price_eur": reference_price,
        "reference_pair": reference,
        "reference_matches_expected_price": (
            reference is not None and reference.get("lowest_observed_price_eur") == reference_price
            if reference_price is not None else None
        ),
        "total_pairs_scanned": len(rows),
        "observed_pairs": sum(1 for r in rows if r.get("status") == "observed"),
        "observations": rows,
    }
    json_path = results_dir / f"daily_fare_report_skyscanner_{stamp}.json"
    csv_path = results_dir / f"daily_fare_report_skyscanner_{stamp}.csv"

    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=(
                "departure_date",
                "return_date",
                "stay_nights",
                "status",
                "lowest_observed_price_eur",
                "itinerary_count",
                "fetched_at",
            ),
        )
        writer.writeheader()
        writer.writerows(
            {
                "departure_date": item.get("departure_date"),
                "return_date": item.get("return_date"),
                "stay_nights": item.get("stay_nights"),
                "status": item.get("status"),
                "lowest_observed_price_eur": item.get("lowest_observed_price_eur"),
                "itinerary_count": item.get("itinerary_count", len(item.get("candidate_cards", []))),
                "fetched_at": item.get("fetched_at"),
            }
            for item in rows
        )
    return json_path, csv_path


async def scan_pair(
    page: Page,
    *,
    origin: str,
    destination: str,
    departure: dt.date,
    return_date: dt.date,
    timeout_ms: int,
    challenge_timeout_seconds: int = 90,
) -> dict[str, Any]:
    """Navigate to Skyscanner, handle consent/challenges gracefully, and extract flight fares."""
    captured_payloads: list[dict[str, Any]] = []

    async def handle_response(response: Response) -> None:
        try:
            url_lowered = response.url.lower()
            if "web-unified-search" in url_lowered or "graphql" in url_lowered:
                if response.status == 200 and "application/json" in response.headers.get("content-type", ""):
                    body = await response.json()
                    captured_payloads.append(body)
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

        # Wait for flight results to settle
        try:
            await page.wait_for_function(
                """() => {
                    const text = document.body?.innerText || '';
                    if (/results|tulosta|halvin|cheapest|direct|stops/i.test(text)) {
                        return true;
                    }
                    return false;
                }""",
                timeout=timeout_ms,
            )
        except Exception:
            pass

        # Brief stabilization delay
        await page.wait_for_timeout(3500)
        page_text = await page.locator("body").inner_text()

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
    if args.depart_from:
        pairs = build_search_pairs(
            depart_from=args.depart_from,
            depart_to=args.depart_to,
            return_from=args.return_from,
            return_to=args.return_to,
            min_stay_nights=args.min_stay_nights,
        )
    else:
        pairs = build_search_pairs(
            window_start=args.window_start,
            window_end=args.window_end,
            min_stay_nights=args.min_stay_nights,
        )

    if not pairs:
        raise ValueError("No date pairs meet the minimum-stay requirement.")

    profile_dir = Path(os.environ.get("SKYSCANNER_PROFILE_DIR", args.profile_dir)).resolve()
    results_dir = Path(args.results_dir).resolve()
    browser_executable = resolve_browser_executable(args.browser_executable)

    profile_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)

    print(f"[Skyscanner] Scanning {len(pairs)} exact date pairs in a visible browser.")
    print(f"[Skyscanner] Results: {results_dir}")
    if browser_executable:
        print(f"[Skyscanner] Using installed browser: {browser_executable}")

    today_stamp = dt.datetime.now().astimezone().strftime("%Y-%m-%d")
    report_json: Path = results_dir / f"daily_fare_report_skyscanner_{today_stamp}.json"
    report_csv: Path = results_dir / f"daily_fare_report_skyscanner_{today_stamp}.csv"
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
            {"name": "ssculture", "value": "locale:::en-GB&market:::FI&currency:::EUR", "domain": ".skyscanner.net", "path": "/"},
            {"name": "ssculture", "value": "locale:::en-GB&market:::FI&currency:::EUR", "domain": ".skyscanner.fi", "path": "/"},
        ])
        page = context.pages[0] if context.pages else await context.new_page()

        try:
            for index, (departure, return_date) in enumerate(pairs, start=1):
                pair_file = results_dir / f"{departure}_{return_date}.json"
                if args.skip_existing and pair_file.exists():
                    try:
                        cached = json.loads(pair_file.read_text(encoding="utf-8"))
                        if cached.get("status") == "observed" and cached.get("fetched_at", "").startswith(today_stamp):
                            print(
                                f"[Skyscanner] [{index}/{len(pairs)}] {departure} -> {return_date} "
                                f"(cached today: €{cached.get('lowest_observed_price_eur')})"
                            )
                            observations.append(cached)
                            report_json, report_csv = write_daily_report(results_dir, observations, args.reference_price)
                            continue
                    except Exception:
                        pass

                print(f"[Skyscanner] [{index}/{len(pairs)}] {departure} -> {return_date}")
                try:
                    observation = await scan_pair(
                        page,
                        origin=args.origin,
                        destination=args.dest,
                        departure=departure,
                        return_date=return_date,
                        timeout_ms=args.timeout_seconds * 1000,
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

                saved = save_observation(results_dir, observation)
                observations.append(observation)
                report_json, report_csv = write_daily_report(results_dir, observations, args.reference_price)
                print(f"  {observation['status']}; lowest observed: €{observation['lowest_observed_price_eur']}; saved {saved.name}")

                if observation["status"] in {"blocked", "user_action_required"}:
                    print(
                        "[Skyscanner] Stopping: browser requires human verification. Please resolve and re-run.",
                        file=sys.stderr,
                    )
                    return 2

                if index < len(pairs):
                    await page.wait_for_timeout(args.delay_seconds * 1000)

        finally:
            await context.close()

    if args.reference_price is not None and args.reference_price > 0:
        reference = next(
            (item for item in observations if item["departure_date"] == "2026-12-09" and item["return_date"] == "2027-01-09"),
            None,
        )
        if reference is not None:
            if reference.get("lowest_observed_price_eur") == args.reference_price:
                print(f"\n[Skyscanner] SUCCESS: Verified reference pair 2026-12-09 -> 2027-01-09 matches expected cheapest €{args.reference_price:.0f}!")
            else:
                print(
                    f"\n[Skyscanner] Reference alert: expected €{args.reference_price:.0f}, observed €{reference.get('lowest_observed_price_eur')}.",
                    file=sys.stderr,
                )

    print(f"\n[Skyscanner] Daily Skyscanner report written: {report_json.name}, {report_csv.name}")
    return 0


def parser() -> argparse.ArgumentParser:
    cfg = load_config()
    res = argparse.ArgumentParser(description="Human-supervised Skyscanner matrix scanner.")
    res.add_argument("--config", help="Optional path to config.toml")
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
    res.add_argument("--challenge-timeout-seconds", type=int, default=cfg.skyscanner.challenge_timeout_seconds, help="Wait time for human challenge solver")
    res.add_argument("--skip-existing", action=argparse.BooleanOptionalAction, default=cfg.execution.skip_existing, help="Skip queries already observed today")
    res.add_argument("--profile-dir", default=str(cfg.skyscanner.resolved_profile_dir()))
    res.add_argument("--results-dir", default=str(cfg.skyscanner.resolved_results_dir()))
    res.add_argument("--browser-executable", default=cfg.execution.browser_executable, help="Custom path to Chrome/Edge executable")
    res.add_argument("--reference-price", type=float, default=cfg.skyscanner.reference_price, help="Expected € price for reference pair")
    return res


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace", line_buffering=True)
    args = parser().parse_args()
    try:
        raise SystemExit(asyncio.run(run(args)))
    except (ValueError, OSError) as error:
        raise SystemExit(f"Error: {error}") from error


if __name__ == "__main__":
    main()
