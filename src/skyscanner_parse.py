"""Pure parsing helpers for Skyscanner result pages.

Free of any Playwright import so fare parsing stays unit-testable without a
browser installed; the async page driving lives in :mod:`src.skyscanner`.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

from src.common import (
    COMPLETION_VERSION,
    PRICE_RE,
    PRICE_SUFFIX_RE,
    duration_minutes,
    is_valid_eur_fare,
    parse_numeric_price,
)

SKYSCANNER_BASE = "https://www.skyscanner.fi"
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
        f"&preferdirects=false&outboundaltsenabled=false&inboundaltsenabled=false"
        f"&sortby=cheapest&currency=EUR"
    )


ANCILLARY_TERMS = (
    "baggage", "bag", "bags", "seat", "seats", "carry-on", "carryon",
    "laukku", "istuin", "matkatavara", "maksu", "fee", "fees",
)


def money_values(text: str) -> list[float]:
    """Find valid flight price figures in euro format, excluding ancillary fees."""
    values: list[float] = []
    for pattern in (PRICE_RE, PRICE_SUFFIX_RE):
        for match in pattern.finditer(text):
            start, end = match.span()
            preceding = text[max(0, start - 25):start].lower()
            trailing = text[end:min(len(text), end + 20)].lower()
            if any(
                re.search(rf"\b{re.escape(w)}\b", preceding) or re.search(rf"\b{re.escape(w)}\b", trailing)
                for w in ANCILLARY_TERMS
            ):
                continue
            raw = match.group(1) if match.groups() else match.group(0)
            num = parse_numeric_price(raw)
            if num is not None and is_valid_eur_fare(num):
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
            "press and hold",
            "verify you are human",
            "unusual traffic",
            "oletko oikea henkilö vai robotti",
            "person or a robot",
            "are you a person",
            "paina ja pidä",
            "paina ja pidä painettuna",
            "vahvista että olet ihminen",
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


def select_authoritative_fare(
    final_payload: dict[str, Any] | None,
    intermediate_payloads: list[dict[str, Any]] | None = None,
    dom_cards: list[str] | None = None,
    tab_price: float | None = None,
) -> float | None:
    """Select the authoritative lowest fare from the completed search state.

    The final authoritative payload replaces any intermediate results to prevent
    preserving obsolete or retracted lower prices.
    """
    if final_payload:
        results = extract_from_xhr_payloads([final_payload])
        if results:
            prices = [r["price_eur"] for r in results if is_valid_eur_fare(r.get("price_eur"))]
            if prices:
                return min(prices)
    if tab_price is not None and is_valid_eur_fare(tab_price):
        return tab_price
    if dom_cards:
        dom_prices: list[float] = []
        for card in dom_cards:
            for p in money_values(card):
                if is_valid_eur_fare(p):
                    dom_prices.append(p)
        if dom_prices:
            return min(dom_prices)
    return None


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
    status: str | None = None,
    completion_evidence: str | None = None,
    authoritative_fare_eur: float | None = None,
) -> dict[str, Any]:
    """Construct structured, persistent observation dictionary with completion evidence."""
    if status is None:
        status = page_status(page_url, page_text)

    if status == "observed":
        if authoritative_fare_eur is not None and is_valid_eur_fare(authoritative_fare_eur):
            lowest_price = authoritative_fare_eur
        elif xhr_candidates:
            xhr_prices = [c["price_eur"] for c in xhr_candidates if is_valid_eur_fare(c.get("price_eur"))]
            lowest_price = min(xhr_prices) if xhr_prices else None
        else:
            tab_p = cheapest_tab_price(page_text)
            if tab_p is not None and is_valid_eur_fare(tab_p):
                lowest_price = tab_p
            elif dom_candidates:
                dom_prices = [p for b in dom_candidates for p in money_values(b) if is_valid_eur_fare(p)]
                lowest_price = min(dom_prices) if dom_prices else None
            else:
                lowest_price = None

        if lowest_price is None:
            status = "incomplete"
            completion_evidence = None
    else:
        lowest_price = None
        completion_evidence = None

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
        "completion_version": COMPLETION_VERSION,
        "completion_evidence": completion_evidence,
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
