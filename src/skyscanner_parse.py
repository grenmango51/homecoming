"""Pure parsing helpers for Skyscanner result pages.

Free of any Playwright import so fare parsing stays unit-testable without a
browser installed; the async page driving lives in :mod:`src.skyscanner`.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

from src.common import PRICE_RE, PRICE_SUFFIX_RE, duration_minutes, parse_numeric_price

SKYSCANNER_BASE = "https://www.skyscanner.net"
SOURCE = "Skyscanner UI"

CHEAPEST_TAB_RE = re.compile(
    r"(?:halvin|cheapest)\s*(?:alk\.|alkaen|from)?\s*(?:€\s*|EUR\s*)?([0-9][0-9.,\s]*)\s*(?:€|eur)?",
    re.IGNORECASE,
)

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
        f"&sortby=cheapest"
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
            "person or a robot",
            "are you a person",
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
        for opt in deal_options:
            agent = opt.get("agentName")
            if not agent and opt.get("agents"):
                agent = opt["agents"][0].get("name")
            opt_price = opt.get("price", {}).get("raw")
            if opt_price is not None:
                try:
                    val = float(opt_price)
                    if price_eur is None or val < price_eur:
                        price_eur = val
                except (ValueError, TypeError):
                    pass
            if agent and len(deals) < 5:
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
    if tab_price is not None and tab_price >= 200.0:
        observed_prices.append(tab_price)

    for c in xhr_candidates:
        p = c.get("price_eur")
        if p is not None and float(p) >= 200.0:
            observed_prices.append(float(p))

    for block in dom_candidates:
        for p in money_values(block):
            if p >= 200.0:
                observed_prices.append(p)

    if not observed_prices and status == "observed":
        for p in money_values(page_text):
            if p >= 200.0:
                observed_prices.append(p)

    lowest_price: float | None = min(observed_prices) if observed_prices else None
    if lowest_price is None and status == "observed":
        status = "incomplete"

    return {
        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source": SOURCE,
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
