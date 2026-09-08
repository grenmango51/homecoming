#!/usr/bin/env python3
"""Human-supervised Google Flights fare scanner.

This program reads the rendered Google Flights page in a visible, persistent
browser. It never follows a booking link or clicks a purchase/checkout control.
Google Flights changes frequently, so every saved result is a timestamped
observation rather than a promise that a fare is still available.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
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


ROOT = Path(__file__).resolve().parent
DEFAULT_PROFILE_DIR = ROOT / ".browser-profile"
DEFAULT_RESULTS_DIR = ROOT / "flight_results"
GOOGLE_FLIGHTS = "https://www.google.com/travel/flights"
# Captured from the normal Google Flights result page for HEL–HAN round trip,
# one adult, economy. The dates are replaced below before every direct search.
TFS_TEMPLATE = (
    "CBwQAhooEgoyMDI2LTEyLTA5agwIAhIIL20vMDNraG5yDAgDEggvbS8wZm5mZhoo"
    "EgoyMDI3LTAxLTA5agwIAxIIL20vMGZuZmYrDAgCEggvbS8wM2tobkABSAFwAYIBCwj"
    "EgoyMDI3LTAxLTA5agwIAxIIL20vMGZuZmZyDAgCEggvbS8wM2tobkABSAFwAYIBCwj"
    "___________8BmAEB"
)
PRICE_RE = re.compile(r"(?:€\s*|EUR\s*)([0-9][0-9.,\s]*)", re.IGNORECASE)
EUROS_RE = re.compile(r"\b([0-9][0-9.,\s]*)\s+euros?\b", re.IGNORECASE)
DURATION_RE = re.compile(r"\b(\d{1,2})h(?:\s*(\d{1,2})m)?\b", re.IGNORECASE)
DURATION_RE = re.compile(r"\b(\d{1,2})\s*(?:h|hr|hours?)\s*(?:(\d{1,2})\s*(?:m|min|minutes?))?\b", re.IGNORECASE)
CHEAPEST_BANNER_RE = re.compile(r"cheapest\s+(?:from\s+)?(?:€\s*|eur\s*)([0-9][0-9.,\s]*)", re.IGNORECASE)


def date_range(start: str, end: str) -> list[dt.date]:
    first, last = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    if last < first:
        raise ValueError(f"End date {end} is before start date {start}.")
    return [first + dt.timedelta(days=offset) for offset in range((last - first).days + 1)]


def resolve_browser_executable(requested: str | None) -> str | None:
    """Prefer an installed stable browser when Playwright Chromium is unusable.

    A dedicated profile is still used, so the user's everyday Chrome profile is
    never opened or modified. Returning ``None`` lets Playwright use its own
    Chromium on hosts where that browser works.
    """
    if requested:
        candidate = Path(requested)
        if not candidate.is_file():
            raise ValueError(f"Browser executable does not exist: {candidate}")
        return str(candidate)
    if os.name != "nt":
        return None
    for candidate in (
        Path(os.environ.get("PROGRAMFILES", r"C:\\Program Files")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\\Program Files (x86)")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(os.environ.get("PROGRAMFILES", r"C:\\Program Files")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\\Program Files (x86)")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
    ):
        if candidate.is_file():
            return str(candidate)
    return None


def date_accessible_name(value: dt.date) -> str:
    """Match the English accessible name exposed by Google Flights' date picker."""
    return f"{value.strftime('%A, %B')} {value.day}, {value.year}"


def flight_search_url(origin: str, destination: str, departure: dt.date, return_date: dt.date) -> str:
    """Build a direct Google Flights result URL from its own captured state.

    The route fields in this project are intentionally fixed to HEL–HAN. Dates
    are literal bytes in Google's query state and are the only fields varied.
    This avoids the generic Explore redirect that occurs when re-submitting the
    prefilled origin on the current Google Flights landing page.
    """
    if (origin, destination) != ("HEL", "HAN"):
        raise ValueError("The direct URL template currently supports HEL to HAN only.")
    raw = base64.urlsafe_b64decode(TFS_TEMPLATE + "=" * (-len(TFS_TEMPLATE) % 4))
    raw = raw.replace(b"2026-12-09", departure.isoformat().encode(), 1)
    raw = raw.replace(b"2027-01-09", return_date.isoformat().encode(), 1)
    encoded = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return f"{GOOGLE_FLIGHTS}/search?tfs={encoded}&tfu=EgYIABAAGAA&hl=en&curr=EUR"
    return f"{GOOGLE_FLIGHTS}/search?tfs={encoded}&tfu=EgYIACACKAEiAA&hl=en&curr=EUR"


def money_values(text: str) -> list[float]:
    values: list[float] = []
    for raw in PRICE_RE.findall(text) + EUROS_RE.findall(text):
        normalized = raw.replace(" ", "").replace("\u00a0", "")
        normalized = normalized.replace(",", "") if normalized.count(",") <= 1 else normalized.replace(".", "").replace(",", ".")
        try:
            values.append(float(normalized))
        except ValueError:
            continue
    return values


def duration_minutes(value: str) -> int | None:
    match = DURATION_RE.search(value)
    if not match:
        return None
    return int(match.group(1)) * 60 + int(match.group(2) or 0)
    hours = int(match.group(1))
    minutes = int(match.group(2) or 0)
    return hours * 60 + minutes


def cheapest_banner_price(text: str) -> float | None:
    match = CHEAPEST_BANNER_RE.search(text)
    if not match:
        return None
    raw = match.group(1).replace(" ", "").replace("\u00a0", "")
    normalized = raw.replace(",", "") if raw.count(",") <= 1 else raw.replace(".", "").replace(",", ".")
    try:
        return float(normalized)
    except ValueError:
        return None


def protection_label(text: str) -> str:
    if "separate tickets booked together" in text.lower():
        return "separate_tickets"
    return "not_flagged_by_google"


async def visible_candidate_blocks(page: Page) -> list[str]:
    """Return flight-card text, without relying on generated Google CSS classes."""
    locators = page.locator("li[role='listitem'], [role='listitem'], li.pIav2d")
    count = await locators.count()
    candidates: list[str] = []
    seen: set[str] = set()
    for i in range(count):
        txt = await locators.nth(i).inner_text()
        compact = " ".join(txt.split())
        if len(compact) < 25:
            continue
        lowered = compact.lower()
        if ("round trip" in lowered or "one way" in lowered) and ("stop" in lowered or "nonstop" in lowered):
            prices = money_values(compact)
            if not prices:
                continue
            key = compact[:120].lower()
            if key not in seen:
                seen.add(key)
                candidates.append(compact[:4000])
    return candidates[:40]


async def wait_for_results(page: Page, timeout_ms: int) -> str:
    """Wait for rendered content while preserving a page for manual intervention."""
    try:
        await page.wait_for_function(
            """() => {
                const text = document.body?.innerText || '';
                if (/unusual traffic|captcha|verify you are human/i.test(text)) {
                    return true;
                }
                const hasResults = /\\b\\d+\\s+results returned\\b|top departing flights|cheapest from|no flights/i.test(text);
                const isLoading = /loading results/i.test(text);
                return hasResults && !isLoading;
            }""",
            timeout=timeout_ms,
        )
    except Exception:
        pass
    # Brief stabilization pause for flight cards to finish rendering
    await page.wait_for_timeout(1000)
    return await page.locator("body").inner_text()


def page_status(text: str) -> str:
    lowered = text.lower()
    if any(marker in lowered for marker in ("unusual traffic", "captcha", "verify you are human")):
        return "blocked"
    if "sign in" in lowered and len(text) < 1000:
        return "user_action_required"
    if "oops, something went wrong" in lowered:
        return "incomplete"
    if not re.search(r"\b\d+ results returned\b|top departing flights|no flights|cheapest from", lowered):
        return "incomplete"
    return "observed"


def make_observation(
    *, origin: str, destination: str, departure: dt.date, return_date: dt.date, page_text: str, candidates: list[str]
) -> dict[str, Any]:
    status = page_status(page_text)
    candidate_prices: list[float] = []
    for block in candidates:
        candidate_prices.extend(money_values(block))

    banner = cheapest_banner_price(page_text)
    if candidate_prices and banner is not None:
        lowest_price: float | None = min(min(candidate_prices), banner)
    elif candidate_prices:
        lowest_price = min(candidate_prices)
    elif banner is not None:
        lowest_price = banner
    elif status == "observed":
        all_prices = money_values(page_text)
        lowest_price = min(all_prices) if all_prices else None
    else:
        lowest_price = None

    all_card_text = "\n".join(candidates)
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
        "candidate_cards": [
            {
                "text": block,
                "observed_prices_eur": money_values(block),
                "observed_duration_minutes": duration_minutes(block),
                "protection_label": protection_label(block),
            }
            for block in candidates
        ],
        "notes": [
            "Read from the rendered Google Flights UI; fares and availability are volatile.",
            "No booking or checkout action was performed.",
            "Baggage through-checking and missed-connection protection require seller confirmation.",
        ],
    }


def save_observation(results_dir: Path, observation: dict[str, Any]) -> Path:
    results_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{observation['departure_date']}_{observation['return_date']}.json"
    path = results_dir / filename
    path.write_text(json.dumps(observation, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_daily_report(results_dir: Path, observations: list[dict[str, Any]], reference_price: float | None) -> tuple[Path, Path]:
    """Write compact report files that are safe to open after each daily run."""
    results_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().astimezone().strftime("%Y-%m-%d")
    rows = sorted(observations, key=lambda item: (item["departure_date"], item["return_date"]))
    reference = next(
        (item for item in rows if item["departure_date"] == "2026-12-09" and item["return_date"] == "2027-01-09"),
        None,
    )
    report = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source": "Google Flights UI",
        "reference_price_eur": reference_price,
        "reference_pair": reference,
        "reference_matches_expected_price": (
            reference is not None and reference.get("lowest_observed_price_eur") == reference_price
            if reference_price is not None else None
        ),
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


async def scan_pair(
    page: Page,
    *,
    origin: str,
    destination: str,
    departure: dt.date,
    return_date: dt.date,
    timeout_ms: int,
) -> dict[str, Any]:
    query_url = flight_search_url(origin, destination, departure, return_date)
    await page.goto(query_url, wait_until="domcontentloaded")
    # This is a first-run cookie-consent screen for the dedicated profile.
    # Choose the privacy-preserving option and let Google remember it there.
    try:
        reject_btn = page.get_by_role("button", name="Reject all", exact=True)
        if await reject_btn.is_visible(timeout=min(timeout_ms, 3_000)):
            await reject_btn.click()
            await page.wait_for_timeout(500)
            await page.goto(query_url, wait_until="domcontentloaded")
    except Exception:
        pass
    page_text = await wait_for_results(page, timeout_ms)

    # If results are still loading or showed a temporary glitch, give a brief retry
    if page_status(page_text) == "incomplete":
        try:
            reload_btn = page.get_by_role("button", name="Reload", exact=True)
            if await reload_btn.count() > 0 and await reload_btn.first.is_visible():
                await reload_btn.first.click(timeout=2000)
            else:
                await page.wait_for_timeout(3000)
        except Exception:
            await page.wait_for_timeout(2000)
        page_text = await wait_for_results(page, timeout_ms)

    # Click "View more flights" if present to discover all options
    try:
        more_btn = page.get_by_text("View more flights", exact=False)
        if await more_btn.count() > 0 and await more_btn.first.is_visible():
            await more_btn.first.click(timeout=2000)
            await page.wait_for_timeout(1500)
            page_text = await page.locator("body").inner_text()
    except Exception:
        pass

    candidates = await visible_candidate_blocks(page)
    observation = make_observation(
        origin=origin,
        destination=destination,
        departure=departure,
        return_date=return_date,
        page_text=page_text,
        candidates=candidates,
    )
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


async def run(args: argparse.Namespace) -> int:
    departures = date_range(args.depart_from, args.depart_to)
    returns = date_range(args.return_from, args.return_to)
    pairs = [(departure, return_date) for departure in departures for return_date in returns if (return_date - departure).days >= args.min_stay_nights]
    if not pairs:
        raise ValueError("No date pairs meet the minimum-stay requirement.")

    profile_dir = Path(os.environ.get("GOOGLE_FLIGHTS_PROFILE_DIR", args.profile_dir)).resolve()
    results_dir = Path(args.results_dir).resolve()
    browser_executable = resolve_browser_executable(args.browser_executable)
    profile_dir.mkdir(parents=True, exist_ok=True)
    print(f"Scanning {len(pairs)} exact date pairs in a visible browser. Results: {results_dir}")
    print("If Google shows consent, sign-in, or a challenge, handle it yourself in the opened browser.")
    if browser_executable:
        print(f"Using installed browser: {browser_executable}")

    async with async_playwright() as playwright:
        context: BrowserContext = await playwright.chromium.launch_persistent_context(
            str(profile_dir),
            headless=False,
            executable_path=browser_executable,
            locale="en-IE",
            viewport={"width": 1440, "height": 1000},
        )
        page = context.pages[0] if context.pages else await context.new_page()
        observations: list[dict[str, Any]] = []
        try:
            for index, (departure, return_date) in enumerate(pairs, start=1):
                print(f"[{index}/{len(pairs)}] {departure} -> {return_date}")
                try:
                    observation = await scan_pair(
                        page,
                        origin=args.origin,
                        destination=args.dest,
                        departure=departure,
                        return_date=return_date,
                        timeout_ms=args.timeout_seconds * 1000,
                    )
                except Exception as error:
                    observation = failed_observation(
                        origin=args.origin,
                        destination=args.dest,
                        departure=departure,
                        return_date=return_date,
                        error=error,
                    )
                    # Capture only the rendered diagnostics needed to resolve
                    # an automation failure, never cookies or page source.
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
                    print("Stopping: browser needs human action. Re-run after resolving it.", file=sys.stderr)
                    return 2
                if index < len(pairs):
                    await page.wait_for_timeout(args.delay_seconds * 1000)
        finally:
            await context.close()
    reference = next(
        (item for item in observations if item["departure_date"] == "2026-12-09" and item["return_date"] == "2027-01-09"),
        None,
    )
    if args.reference_price is not None:
        if reference is None:
            print("Reference pair 2026-12-09/2027-01-09 was not included in this run.", file=sys.stderr)
        elif reference["lowest_observed_price_eur"] != args.reference_price:
            print(
                f"Reference mismatch: expected €{args.reference_price:.0f}, observed €{reference['lowest_observed_price_eur']}.",
                file=sys.stderr,
            )
    print(f"Daily report written: {report_json.name}, {report_csv.name}")
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Human-supervised Google Flights matrix scanner (no purchase actions).")
    result.add_argument("--origin", default="HEL")
    result.add_argument("--dest", default="HAN")
    result.add_argument("--depart-from", default="2026-12-09")
    result.add_argument("--depart-to", default="2026-12-13")
    result.add_argument("--return-from", default="2027-01-05")
    result.add_argument("--return-to", default="2027-01-09")
    result.add_argument("--min-stay-nights", type=int, default=21)
    result.add_argument("--delay-seconds", type=int, default=8, help="Delay between pages; keep this modest and human-supervised.")
    result.add_argument("--timeout-seconds", type=int, default=30)
    result.add_argument("--profile-dir", default=str(DEFAULT_PROFILE_DIR))
    result.add_argument("--results-dir", default=str(DEFAULT_RESULTS_DIR))
    result.add_argument("--browser-executable", help="Optional path to Chrome/Edge. Defaults to installed Chrome or Edge on Windows.")
    result.add_argument("--reference-price", type=float, default=800.0, help="Expected € price for 9 Dec 2026–9 Jan 2027; recorded as a pass/fail check.")
    return result


def main() -> None:
    # Windows consoles often default to a legacy code page; results can contain
    # symbols such as € and must never abort a scan merely while logging.
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
