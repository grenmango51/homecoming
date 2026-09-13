#!/usr/bin/env python3
"""Human-supervised Google Flights fare scanner.

This program reads the rendered Google Flights page in a visible, persistent
browser. It never follows a booking link or clicks a purchase/checkout control.
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
    from playwright.async_api import BrowserContext, Page, async_playwright
except ImportError as error:
    raise SystemExit(
        "Playwright is required. Run: .\\.venv\\Scripts\\python.exe -m pip install playwright "
        "and then: .\\.venv\\Scripts\\playwright.exe install chromium"
    ) from error

from src.common import (
    PRICE_RE,
    EUROS_RE,
    date_range,
    build_search_pairs,
    resolve_browser_executable,
    duration_minutes,
)
from src.config import load_config, PROJECT_ROOT

import base64

ROOT = PROJECT_ROOT
DEFAULT_PROFILE_DIR = ROOT / ".browser-profile-daily"
DEFAULT_RESULTS_DIR = ROOT / "flight_results"
GOOGLE_FLIGHTS = "https://www.google.com/travel/flights"

CHEAPEST_BANNER_RE = re.compile(
    r"[Cc]heapest[\s\S]{0,100}?(?:€\s*|eur\s*)([0-9][0-9.,\s]*)", re.IGNORECASE
)

USER_TFS_TEMPLATE = "CBwQAhojEgoyMDI2LTEyLTA5agwIAhIIL20vMDNraG5yBwgBEgNIQU4aIxIKMjAyNy0wMS0wNWoHCAESA0hBTnIMCAISCC9tLzAza2huQAFIAXABggELCP___________wGYAQE"


def build_structured_flight_url(dep: dt.date, ret: dt.date, gl: str = "FI") -> str:
    """Encode direct protobuf TFS URL that locks Google Flights to cheapest sort and Finnish POS."""
    dep_bytes = dep.isoformat().encode()
    ret_bytes = ret.isoformat().encode()
    pad = "=" * (-len(USER_TFS_TEMPLATE) % 4)
    raw = base64.urlsafe_b64decode(USER_TFS_TEMPLATE + pad)
    raw_new = raw.replace(b"2026-12-09", dep_bytes).replace(b"2027-01-05", ret_bytes)
    tfs = base64.urlsafe_b64encode(raw_new).decode().rstrip("=")
    return f"https://www.google.com/travel/flights/search?tfs={tfs}&tfu=EgoIABAAGAAgAigB&hl=en&gl={gl}&curr=EUR"


def date_accessible_name(value: dt.date) -> str:
    """Match the English accessible name exposed by Google Flights' date picker."""
    return f"{value.strftime('%A, %B')} {value.day}, {value.year}"


def flight_search_url(origin: str, destination: str, departure: dt.date, return_date: dt.date, gl: str = "FI") -> str:
    """Build a direct Google Flights result URL for any route and date pair."""
    orig = origin.strip().upper()
    dest = destination.strip().upper()
    if orig == "HEL" and dest == "HAN":
        return build_structured_flight_url(departure, return_date, gl=gl)
    return f"{GOOGLE_FLIGHTS}?q=Flights%20to%20{dest}%20from%20{orig}%20on%20{departure.isoformat()}%20through%20{return_date.isoformat()}&hl=en&gl={gl}&curr=EUR"


def money_values(text: str) -> list[float]:
    values: list[float] = []
    for raw in PRICE_RE.findall(text) + EUROS_RE.findall(text):
        normalized = raw.replace(" ", "").replace("\u00a0", "")
        normalized = normalized.replace(",", "") if normalized.count(",") <= 1 else normalized.replace(".", "").replace(",", ".")
        try:
            val = float(normalized)
            if val >= 50.0:
                values.append(val)
        except ValueError:
            continue
    return values


def cheapest_banner_price(text: str) -> float | None:
    match = CHEAPEST_BANNER_RE.search(text)
    if not match:
        return None
    raw = match.group(1).replace(" ", "").replace("\u00a0", "")
    normalized = raw.replace(",", "") if raw.count(",") <= 1 else raw.replace(".", "").replace(",", ".")
    try:
        val = float(normalized)
        return val if val >= 50.0 else None
    except ValueError:
        return None


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


def protection_label(text: str) -> str:
    if "separate tickets booked together" in text.lower():
        return "separate_tickets"
    return "not_flagged_by_google"


def parse_card_details(raw_text: str, curr: str = "EUR") -> dict[str, Any]:
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

    found_airports = [
        code for code in AIRPORT_RE.findall(raw_text)
        if code in KNOWN_AIRPORTS and code not in {"HEL", "HAN"}
    ]
    layovers = list(dict.fromkeys(found_airports))
    carriers = [carrier for carrier in KEY_AIRLINES if carrier.lower() in lower]
    prices = money_values(raw_text)
    dur_min = duration_minutes(raw_text)
    protection = protection_label(raw_text)

    return {
        "text": raw_text,
        "carriers": carriers,
        "stops": stops,
        "layovers": layovers,
        "duration_minutes": dur_min,
        "observed_prices": prices,
        "observed_prices_eur": prices,
        "lowest_price": min(prices) if prices else None,
        "currency": curr,
        "protection_label": protection,
    }


async def extract_clean_candidate_cards(page: Page, curr: str = "EUR") -> list[dict[str, Any]]:
    """Return parsed flight cards directly without clicking 'more flights', which collapses Top Departing Flights."""
    try:
        await page.evaluate("window.scrollBy(0, 400)")
        await page.wait_for_timeout(400)
        await page.evaluate("window.scrollBy(0, 400)")
        await page.wait_for_timeout(400)
    except Exception:
        pass

    raw_texts: list[str] = []
    try:
        raw_texts = await page.evaluate(
            """() => {
                const els = document.querySelectorAll("ul.Rk10dc > li, li.pIav2d");
                return Array.from(els).map(e => e.innerText || '');
            }"""
        )
    except Exception:
        try:
            locators = page.locator("ul.Rk10dc > li, li.pIav2d")
            raw_texts = await locators.all_inner_texts()
        except Exception:
            raw_texts = []

    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for txt in raw_texts:
        compact = " ".join(txt.split())
        if len(compact) < 20:
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
    return candidates[:40]


async def visible_candidate_blocks(page: Page) -> list[str]:
    """Compatibility wrapper returning candidate raw text blocks."""
    cards = await extract_clean_candidate_cards(page)
    return [c["text"] for c in cards]


async def wait_for_results(page: Page, timeout_ms: int) -> str:
    """Wait for rendered content while preserving a page for manual intervention."""
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
    # Brief stabilization pause for flight cards and cheapest banner to finish rendering
    await page.wait_for_timeout(2500)
    return await page.locator("body").inner_text()


def page_status(text: str) -> str:
    lowered = text.lower()
    if any(marker in lowered for marker in ("unusual traffic", "captcha", "verify you are human")):
        return "blocked"
    if "sign in" in lowered and len(text) < 1000:
        return "user_action_required"
    if "oops, something went wrong" in lowered:
        return "incomplete"
    if not re.search(r"\b\d+ results returned\b|departing flights|no flights|cheapest", lowered):
        return "incomplete"
    return "observed"


def make_observation(
    *,
    origin: str,
    destination: str,
    departure: dt.date,
    return_date: dt.date,
    page_text: str,
    candidates: list[dict[str, Any]] | list[str],
) -> dict[str, Any]:
    status = page_status(page_text)
    candidate_prices: list[float] = []
    card_dicts: list[dict[str, Any]] = []

    for item in candidates:
        if isinstance(item, dict):
            prices = item.get("observed_prices", item.get("observed_prices_eur", []))
            candidate_prices.extend(prices)
            card_dicts.append(item)
        else:
            prices = money_values(item)
            candidate_prices.extend(prices)
            card_dicts.append({
                "text": item,
                "observed_prices_eur": prices,
                "observed_duration_minutes": duration_minutes(item),
                "protection_label": protection_label(item),
            })

    banner = cheapest_banner_price(page_text)
    if candidate_prices:
        lowest_card_price = min(candidate_prices)
        if banner is not None and banner >= 50.0:
            lowest_price: float | None = min(lowest_card_price, banner)
        else:
            lowest_price = lowest_card_price
    elif banner is not None and banner >= 50.0:
        lowest_price = banner
    elif status == "observed":
        all_text_prices = money_values(page_text)
        lowest_price = min(all_text_prices) if all_text_prices else None
    else:
        lowest_price = None

    if (candidate_prices or (banner is not None and banner >= 50.0)) and status == "incomplete":
        status = "observed"

    all_card_text = "\n".join(c.get("text", "") for c in card_dicts)
    return {
        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source": "Google Flights UI",
        "origin": origin,
        "destination": destination,
        "departure_date": departure.isoformat(),
        "return_date": return_date.isoformat(),
        "stay_nights": (return_date - departure).days,
        "status": status,
        "lowest_observed_price_eur": lowest_price,
        "protection_label": protection_label(all_card_text or page_text),
        "seller_confirmation_required": True,
        "candidate_cards": card_dicts,
        "notes": [
            "Read from the rendered Google Flights UI; fares and availability are volatile.",
            "No booking or checkout action was performed.",
            "Baggage through-checking and missed-connection protection require seller confirmation.",
        ],
    }


async def switch_to_cheapest_tab(page: Page) -> bool:
    """Ensure Google Flights is sorted by Price / Cheapest tab if not already active."""
    # If the URL already specifies tfu=EgoIABAAGAAgAigB, cheapest sort is active natively
    if "tfu=EgoIABAAGAAgAigB" in page.url:
        return False

    # Check if dropdown says 'Sorted by top flights'
    try:
        sort_btn = page.locator("button:has-text('Sorted by top flights'), [aria-label*='Sorted by top flights']")
        if await sort_btn.count() > 0 and await sort_btn.first.is_visible():
            await sort_btn.first.click(timeout=1500)
            await page.wait_for_timeout(1000)
            price_opt = page.locator("[role='menuitem']:has-text('Price'), [role='option']:has-text('Price'), span:text-is('Price')")
            if await price_opt.count() > 0:
                await price_opt.first.click(timeout=1500)
                await page.wait_for_timeout(2500)
                return True
    except Exception:
        pass
    return False


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
    query_url = flight_search_url(origin, destination, departure, return_date, gl=gl)
    try:
        await page.goto(query_url, wait_until="domcontentloaded", timeout=timeout_ms)
    except Exception:
        await page.goto(query_url, timeout=timeout_ms)
    # This is a first-run cookie-consent screen for the dedicated profile.
    # Choose the privacy-preserving option and let Google remember it there.
    try:
        reject_btn = page.get_by_role("button", name="Reject all", exact=True)
        if await reject_btn.is_visible(timeout=min(timeout_ms, 3_000)):
            await reject_btn.click()
            await page.wait_for_timeout(500)
            await page.goto(query_url, wait_until="domcontentloaded", timeout=timeout_ms)
    except Exception:
        pass
    page_text = await wait_for_results(page, timeout_ms)

    # If results glitched or showed 'something went wrong' / 'no results', click Reload
    if "something went wrong" in page_text.lower() or "no results returned" in page_text.lower() or page_status(page_text) == "incomplete":
        try:
            reload_btn = page.get_by_role("button", name="Reload", exact=True)
            if await reload_btn.count() > 0 and await reload_btn.first.is_visible():
                await reload_btn.click(timeout=2000)
                await page.wait_for_timeout(3500)
                page_text = await wait_for_results(page, timeout_ms)
        except Exception:
            pass

    # Ensure results are sorted by Price / Cheapest tab (if not already handled by tfu param)
    switched = await switch_to_cheapest_tab(page)
    if switched:
        await page.wait_for_timeout(2000)

    # Extract clean candidate cards directly without clicking 'more flights'
    candidates = await extract_clean_candidate_cards(page)

    # Refresh full body text after cards render
    try:
        page_text = await page.locator("body").inner_text()
        cheapest_tab = page.locator("[role='tab']:has-text('Cheapest'), button:has-text('Cheapest'), [aria-label*='Cheapest']").first
        if await cheapest_tab.count() > 0:
            tab_txt = await cheapest_tab.inner_text()
            if tab_txt:
                page_text = tab_txt + "\n" + page_text
    except Exception:
        pass

    observation = make_observation(
        origin=origin,
        destination=destination,
        departure=departure,
        return_date=return_date,
        page_text=page_text,
        candidates=candidates,
    )
    observation["gl"] = gl
    if observation["status"] != "observed":
        observation["page_url"] = page.url
        observation["visible_page_text"] = page_text[:2000]
    return observation


def failed_observation(
    *, origin: str, destination: str, departure: dt.date, return_date: dt.date, error: Exception
) -> dict[str, Any]:
    """Persist a recoverable error instead of losing an entire daily matrix."""
    return {
        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source": "Google Flights UI",
        "origin": origin,
        "destination": destination,
        "departure_date": departure.isoformat(),
        "return_date": return_date.isoformat(),
        "stay_nights": (return_date - departure).days,
        "status": "error",
        "lowest_observed_price_eur": None,
        "protection_label": "unknown",
        "seller_confirmation_required": True,
        "candidate_cards": [],
        "error": f"{type(error).__name__}: {error}",
        "notes": ["The page could not be read; this pair needs a retry."],
    }


def save_observation(results_dir: Path, observation: dict[str, Any]) -> Path:
    results_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{observation['departure_date']}_{observation['return_date']}.json"
    path = results_dir / filename
    path.write_text(json.dumps(observation, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_daily_report(results_dir: Path, observations: list[dict[str, Any]], reference_price: float | None = None) -> tuple[Path, Path]:
    """Write compact report files that are safe to open after each daily run."""
    results_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().astimezone().strftime("%Y-%m-%d")
    rows = sorted(observations, key=lambda item: (item["departure_date"], item["return_date"]))
    report = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source": "Google Flights UI",
        "total_pairs_scanned": len(rows),
        "observed_pairs": sum(1 for r in rows if r.get("status") == "observed"),
        "observations": rows,
    }
    json_path = results_dir / f"daily_fare_report_{stamp}.json"
    csv_path = results_dir / f"daily_fare_report_{stamp}.csv"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=("departure_date", "return_date", "stay_nights", "status", "lowest_observed_price_eur", "protection_label", "fetched_at"),
        )
        writer.writeheader()
        writer.writerows({key: item.get(key) for key in writer.fieldnames} for item in rows)
    return json_path, csv_path


async def run(args: argparse.Namespace) -> int:
    cfg = load_config(args.config, args.trip)
    if cfg.trip.date_mode == "exact" and cfg.trip.exact_pairs and not (args.depart_from and args.depart_to):
        pairs = [p for p in cfg.trip.get_search_pairs() if p[1] is not None]
    elif args.depart_from:
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

    gl = getattr(args, "gl", None) or cfg.google_flights.gl or "FI"

    profile_dir = Path(os.environ.get("GOOGLE_FLIGHTS_PROFILE_DIR", args.profile_dir)).resolve()
    results_dir = Path(args.results_dir).resolve()
    browser_executable = resolve_browser_executable(args.browser_executable)
    profile_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)
    print(f"[Google Flights] Scanning {len(pairs)} exact date pairs in a visible browser (Point of Sale: gl={gl}). Results: {results_dir}")
    print("[Google Flights] If Google shows consent, sign-in, or a challenge, handle it in the opened browser.")
    if browser_executable:
        print(f"[Google Flights] Using installed browser: {browser_executable}")

    today_stamp = dt.datetime.now().astimezone().strftime("%Y-%m-%d")
    report_json: Path = results_dir / f"daily_fare_report_{today_stamp}.json"
    report_csv: Path = results_dir / f"daily_fare_report_{today_stamp}.csv"

    async with async_playwright() as playwright:
        context: BrowserContext = await playwright.chromium.launch_persistent_context(
            str(profile_dir),
            headless=False,
            executable_path=browser_executable,
            locale="en-US",
            viewport={"width": 1440, "height": 1000},
            args=["--disable-blink-features=AutomationControlled"],
        )
        await context.add_cookies([
            {"name": "SOCS", "value": "CAESEwgDEgk1ODEzNzI3NDQaAmVuIAEaBgiAo_mwBg", "domain": ".google.com", "path": "/"},
            {"name": "CONSENT", "value": "PENDING+999", "domain": ".google.com", "path": "/"},
        ])
        page = context.pages[0] if context.pages else await context.new_page()
        observations: list[dict[str, Any]] = []
        try:
            for index, (departure, return_date) in enumerate(pairs, start=1):
                pair_file = results_dir / f"{departure}_{return_date}.json"
                if args.skip_existing and pair_file.exists():
                    try:
                        cached = json.loads(pair_file.read_text(encoding="utf-8"))
                        if (
                            cached.get("status") == "observed"
                            and cached.get("fetched_at", "").startswith(today_stamp)
                            and cached.get("origin") == args.origin
                            and cached.get("destination") == args.dest
                            and cached.get("gl", gl) == gl
                        ):
                            print(f"[Google Flights] [{index}/{len(pairs)}] {departure} -> {return_date} (cached today: €{cached.get('lowest_observed_price_eur')})")
                            observations.append(cached)
                            report_json, report_csv = write_daily_report(results_dir, observations, args.reference_price)
                            continue
                    except Exception:
                        pass

                print(f"[Google Flights] [{index}/{len(pairs)}] {departure} -> {return_date}")
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
                    try:
                        observation["page_url"] = page.url
                        observation["visible_page_text"] = (await page.locator("body").inner_text())[:2000]
                    except Exception:
                        pass
                saved = save_observation(results_dir, observation)
                observations.append(observation)
                report_json, report_csv = write_daily_report(results_dir, observations, args.reference_price)
                print(f"  {observation['status']}; lowest observed: {observation['lowest_observed_price_eur']}; saved {saved.name}")
                if observation["status"] in {"blocked", "user_action_required"}:
                    print("[Google Flights] Stopping: browser needs human action. Re-run after resolving it.", file=sys.stderr)
                    return 2
                if index < len(pairs):
                    await page.wait_for_timeout(args.delay_seconds * 1000)
        finally:
            await context.close()

    print(f"[Google Flights] Daily report written: {report_json.name}, {report_csv.name}")
    return 0


def parser(argv: list[str] | None = None) -> argparse.ArgumentParser:
    pre_p = argparse.ArgumentParser(add_help=False)
    pre_p.add_argument("--config", help="Optional path to config.toml")
    pre_p.add_argument("--trip", help="Optional path or name of trip config file")
    pre_args, _ = pre_p.parse_known_args(argv)

    cfg = load_config(pre_args.config, pre_args.trip)
    res = argparse.ArgumentParser(description="Human-supervised Google Flights matrix scanner (no purchase actions).")
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
        res.add_argument("--window-start", default=cfg.trip.window_start, help="Earliest allowed departure date")
        res.add_argument("--window-end", default=cfg.trip.window_end, help="Latest allowed return date")
        res.add_argument("--depart-from", help="Optional explicit start of departure range")
        res.add_argument("--depart-to", help="Optional explicit end of departure range")
        res.add_argument("--return-from", help="Optional explicit start of return range")
        res.add_argument("--return-to", help="Optional explicit end of return range")

    res.add_argument("--gl", default=cfg.google_flights.gl, help="Point of sale region code (e.g. SE, FI, DE)")
    res.add_argument("--min-stay-nights", type=int, default=cfg.trip.min_stay_nights, help="Minimum stay duration in nights")
    res.add_argument("--delay-seconds", type=int, default=cfg.google_flights.delay_seconds, help="Delay between pages in seconds")
    res.add_argument("--timeout-seconds", type=int, default=cfg.google_flights.timeout_seconds)
    res.add_argument("--skip-existing", action=argparse.BooleanOptionalAction, default=cfg.execution.skip_existing, help="Skip querying pairs already observed today")
    res.add_argument("--profile-dir", default=str(cfg.google_flights.resolved_profile_dir()))
    res.add_argument("--results-dir", default=str(cfg.google_flights.resolved_results_dir()))
    res.add_argument("--browser-executable", default=cfg.execution.browser_executable, help="Path to Chrome/Edge")
    res.add_argument("--reference-price", type=float, default=cfg.google_flights.reference_price, help="Expected € price for reference pair")
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
