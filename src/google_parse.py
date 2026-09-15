"""Pure parsing helpers for Google Flights result pages.

Deliberately free of any Playwright import so that fare-parsing logic stays
unit-testable without a browser installed. The async page-driving counterparts
live in :mod:`src.browser`.
"""

from __future__ import annotations

import base64
import datetime as dt
import re
from typing import Any

from src.common import EUROS_RE, PRICE_RE, duration_minutes

GOOGLE_FLIGHTS = "https://www.google.com/travel/flights"

# Sort-by-price ("Cheapest") selector understood by the Google Flights front end.
CHEAPEST_SORT_PARAM = "EgoIABAAGAAgAigB"

# A captured protobuf TFS payload for HEL->HAN on 2026-12-09 / 2027-01-05. The
# departure and return dates are byte-substituted to retarget it; the embedded
# origin (/m/03khn = Helsinki) means it is only valid for that one route.
HEL_HAN_TFS_TEMPLATE = (
    "CBwQAhojEgoyMDI2LTEyLTA5agwIAhIIL20vMDNraG5yBwgBEgNIQU4aIxIKMjAyNy0wMS0wNWoHCAESA0hBTnIMCAIS"
    "CC9tLzAza2huQAFIAXABggELCP___________wGYAQE"
)
_TEMPLATE_DEPARTURE = b"2026-12-09"
_TEMPLATE_RETURN = b"2027-01-05"

CHEAPEST_BANNER_RE = re.compile(r"[Cc]heapest[\s\S]{0,100}?(?:€\s*|eur\s*)([0-9][0-9.,\s]*)", re.IGNORECASE)
MULTI_CURRENCY_RE = re.compile(
    r"(?:[€$£₺₫]|EUR|USD|GBP|TRY|VND|QAR|AED|SEK)\s*([0-9][0-9.,\s]*)", re.IGNORECASE
)
AIRPORT_RE = re.compile(r"\b([A-Z]{3})\b")

# Minimum plausible fare, per parser. The EUR-only parser used by the daily
# HEL->HAN scan can assume long-haul pricing; the multi-currency parser used by
# the POS studies must also accept short-haul and weak-currency quotes.
MIN_EUR_FARE = 50.0
MIN_MULTI_CURRENCY_FARE = 15.0

KEY_AIRLINES = [
    "Qatar Airways", "Emirates", "Turkish Airlines", "Finnair", "Etihad",
    "Vietnam Airlines", "Air France", "KLM", "Lufthansa", "THAI", "Iberia",
    "Condor", "Singapore Airlines", "British Airways", "China Southern", "Air China",
]

KNOWN_AIRPORTS = {
    "HEL", "HAN", "DOH", "DXB", "IST", "WAW", "AUH", "MUC", "BER", "ZRH",
    "BKK", "CPH", "AMS", "CDG", "LHR", "FRA", "SIN", "VIE", "ARN", "OSL",
}

# Endpoint codes are the route itself, not a layover.
_ROUTE_ENDPOINTS = {"HEL", "HAN"}


# --------------------------------------------------------------------------- #
# URL builders
# --------------------------------------------------------------------------- #

def build_structured_flight_url(dep: dt.date, ret: dt.date, gl: str = "FI") -> str:
    """Encode a direct protobuf TFS URL pinned to cheapest sort. HEL->HAN only."""
    pad = "=" * (-len(HEL_HAN_TFS_TEMPLATE) % 4)
    raw = base64.urlsafe_b64decode(HEL_HAN_TFS_TEMPLATE + pad)
    raw = raw.replace(_TEMPLATE_DEPARTURE, dep.isoformat().encode())
    raw = raw.replace(_TEMPLATE_RETURN, ret.isoformat().encode())
    tfs = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return (
        f"{GOOGLE_FLIGHTS}/search?tfs={tfs}&tfu={CHEAPEST_SORT_PARAM}"
        f"&hl=en&gl={gl}&curr=EUR"
    )


def _is_template_route(origin: str, dest: str) -> bool:
    return origin == "HEL" and dest == "HAN"


def flight_search_url(
    origin: str, destination: str, departure: dt.date, return_date: dt.date, gl: str = "FI"
) -> str:
    """Daily-scan URL: protobuf template for HEL->HAN, free-text query otherwise."""
    orig, dest = origin.strip().upper(), destination.strip().upper()
    if _is_template_route(orig, dest):
        return build_structured_flight_url(departure, return_date, gl=gl)
    return (
        f"{GOOGLE_FLIGHTS}?q=Flights%20to%20{dest}%20from%20{orig}"
        f"%20on%20{departure.isoformat()}%20through%20{return_date.isoformat()}"
        f"&hl=en&gl={gl}&curr=EUR"
    )


def build_route_url(
    origin: str,
    dest: str,
    dep: dt.date,
    ret: dt.date | None,
    gl: str = "FI",
    curr: str = "EUR",
) -> str:
    """POS-scan URL supporting one-way trips and forcing the cheapest sort tab."""
    orig, dst = origin.strip().upper(), dest.strip().upper()
    if ret is None:
        return (
            f"{GOOGLE_FLIGHTS}?q=Flights%20to%20{dst}%20from%20{orig}"
            f"%20on%20{dep.isoformat()}%20one%20way"
            f"&tfu={CHEAPEST_SORT_PARAM}&hl=en&gl={gl}&curr={curr}"
        )
    if _is_template_route(orig, dst):
        return build_structured_flight_url(dep, ret, gl=gl)
    return (
        f"{GOOGLE_FLIGHTS}?q=Flights%20to%20{dst}%20from%20{orig}"
        f"%20on%20{dep.isoformat()}%20through%20{ret.isoformat()}"
        f"&tfu={CHEAPEST_SORT_PARAM}&hl=en&gl={gl}&curr={curr}"
    )


def build_regional_url(
    origin: str,
    destination: str,
    departure: dt.date,
    return_date: dt.date,
    gl: str | None = None,
    curr: str = "EUR",
) -> str:
    """Regional-study URL. ``gl=None`` or ``"NONE"`` leaves the POS unset (natural IP)."""
    orig, dest = origin.strip().upper(), destination.strip().upper()
    url = (
        f"{GOOGLE_FLIGHTS}?q=Flights%20to%20{dest}%20from%20{orig}"
        f"%20on%20{departure.isoformat()}%20through%20{return_date.isoformat()}"
        f"&hl=en&curr={curr}"
    )
    if gl and gl != "NONE":
        url += f"&gl={gl}"
    return url


# --------------------------------------------------------------------------- #
# Price extraction
# --------------------------------------------------------------------------- #

def _normalize_grouping(raw: str) -> str:
    """Collapse thousands/decimal separators in a bare numeric string."""
    raw = raw.strip().replace(" ", "").replace(" ", "")
    if raw.count(",") == 1 and "." not in raw:
        return raw.replace(",", "") if len(raw.split(",")[1]) == 3 else raw.replace(",", ".")
    if raw.count(".") == 1 and "," not in raw:
        return raw.replace(".", "") if len(raw.split(".")[1]) == 3 else raw
    if "," in raw and "." in raw:
        if raw.rfind(",") > raw.rfind("."):
            return raw.replace(".", "").replace(",", ".")
        return raw.replace(",", "")
    return raw.replace(",", "").replace(".", "")


def money_values(text: str) -> list[float]:
    """Euro fares in a card or page. Used by the daily HEL->HAN scan."""
    values: list[float] = []
    for raw in PRICE_RE.findall(text) + EUROS_RE.findall(text):
        normalized = raw.replace(" ", "").replace(" ", "")
        normalized = (
            normalized.replace(",", "")
            if normalized.count(",") <= 1
            else normalized.replace(".", "").replace(",", ".")
        )
        try:
            value = float(normalized)
        except ValueError:
            continue
        if value >= MIN_EUR_FARE:
            values.append(value)
    return values


def extract_money_values(text: str, curr: str = "EUR") -> list[float]:
    """Fares in any of the currencies the POS studies request, with a EUR fallback."""
    values: list[float] = []
    for match in MULTI_CURRENCY_RE.finditer(text):
        try:
            value = float(_normalize_grouping(match.group(1)))
        except ValueError:
            continue
        if value >= MIN_MULTI_CURRENCY_FARE:
            values.append(value)
    if not values and curr == "EUR":
        for raw in PRICE_RE.findall(text) + EUROS_RE.findall(text):
            try:
                value = float(raw.replace(" ", "").replace(" ", "").replace(",", ""))
            except ValueError:
                continue
            if value >= MIN_MULTI_CURRENCY_FARE:
                values.append(value)
    return values


def cheapest_banner_price(text: str) -> float | None:
    """Fare quoted by the 'Cheapest' banner / tab, if Google rendered one."""
    match = CHEAPEST_BANNER_RE.search(text)
    if not match:
        return None
    raw = match.group(1).replace(" ", "").replace(" ", "")
    normalized = raw.replace(",", "") if raw.count(",") <= 1 else raw.replace(".", "").replace(",", ".")
    try:
        value = float(normalized)
    except ValueError:
        return None
    return value if value >= MIN_EUR_FARE else None


def tab_price(tab_text: str) -> float | None:
    """Euro figure shown on a result tab header, e.g. 'Cheapest €782'."""
    match = re.search(r"€\s*([\d,]+)", tab_text)
    return float(match.group(1).replace(",", "")) if match else None


# --------------------------------------------------------------------------- #
# Card & page classification
# --------------------------------------------------------------------------- #

def protection_label(text: str) -> str:
    """Whether Google warned that the itinerary is separate tickets booked together."""
    return "separate_tickets" if "separate tickets booked together" in text.lower() else "not_flagged_by_google"


def stop_count(text: str) -> int:
    """Number of stops advertised on a card; 0 when nonstop or unstated."""
    lowered = text.lower()
    for count in (1, 2, 3):
        if f"{count} stop" in lowered:
            return count
    return 0


def _card_from_prices(raw_text: str, prices: list[float], curr: str) -> dict[str, Any]:
    lowered = raw_text.lower()
    layovers = list(
        dict.fromkeys(
            code
            for code in AIRPORT_RE.findall(raw_text)
            if code in KNOWN_AIRPORTS and code not in _ROUTE_ENDPOINTS
        )
    )
    return {
        "text": raw_text,
        "carriers": [carrier for carrier in KEY_AIRLINES if carrier.lower() in lowered],
        "stops": stop_count(raw_text),
        "layovers": layovers,
        "duration_minutes": duration_minutes(raw_text),
        "observed_prices": prices,
        "lowest_price": min(prices) if prices else None,
        "currency": curr,
        "protection_label": protection_label(raw_text),
    }


def parse_card_details(raw_text: str, curr: str = "EUR") -> dict[str, Any]:
    """Parse a flight card using the multi-currency price parser (POS studies)."""
    return _card_from_prices(raw_text, extract_money_values(raw_text, curr=curr), curr)


def parse_eur_card_details(raw_text: str, curr: str = "EUR") -> dict[str, Any]:
    """Parse a flight card using the euro-only price parser (daily scan)."""
    card = _card_from_prices(raw_text, money_values(raw_text), curr)
    card["observed_prices_eur"] = card["observed_prices"]
    return card


def page_status(text: str) -> str:
    """Classify a rendered Google Flights page: observed / blocked / incomplete."""
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


def is_blocked(text: str) -> bool:
    """True when the page is an anti-automation interstitial rather than results."""
    lowered = text.lower()
    return any(marker in lowered for marker in ("unusual traffic", "captcha", "verify you are human"))


def reported_location(page_text: str) -> str:
    """Country Google reports in the page footer, used to confirm the POS took effect."""
    match = re.search(r"Location\s*([^\n\r]+)", page_text)
    return match.group(1).strip() if match else "Unknown"


def reported_currency(page_text: str) -> str:
    """Currency code Google reports in the page footer."""
    match = re.search(r"Currency\s*([A-Z]{3})", page_text)
    return match.group(1).strip() if match else "Unknown"


def make_observation(
    *,
    origin: str,
    destination: str,
    departure: dt.date,
    return_date: dt.date,
    page_text: str,
    candidates: list[dict[str, Any]] | list[str],
) -> dict[str, Any]:
    """Build the persisted observation for one date pair.

    The lowest fare is the cheaper of the parsed cards and the 'Cheapest' banner.
    A page that parsed prices but lacked the usual result markers is promoted
    from ``incomplete`` to ``observed``.
    """
    status = page_status(page_text)
    candidate_prices: list[float] = []
    cards: list[dict[str, Any]] = []

    for item in candidates:
        if isinstance(item, dict):
            prices = item.get("observed_prices", item.get("observed_prices_eur", []))
            cards.append(item)
        else:
            prices = money_values(item)
            cards.append(
                {
                    "text": item,
                    "observed_prices_eur": prices,
                    "observed_duration_minutes": duration_minutes(item),
                    "protection_label": protection_label(item),
                }
            )
        candidate_prices.extend(prices)

    # cheapest_banner_price already rejects anything below MIN_EUR_FARE.
    banner = cheapest_banner_price(page_text)
    if candidate_prices:
        lowest_price: float | None = min(candidate_prices)
        if banner is not None:
            lowest_price = min(lowest_price, banner)
    elif banner is not None:
        lowest_price = banner
    elif status == "observed":
        page_prices = money_values(page_text)
        lowest_price = min(page_prices) if page_prices else None
    else:
        lowest_price = None

    if (candidate_prices or banner is not None) and status == "incomplete":
        status = "observed"

    card_text = "\n".join(card.get("text", "") for card in cards)
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
        "protection_label": protection_label(card_text or page_text),
        "seller_confirmation_required": True,
        "candidate_cards": cards,
        "notes": [
            "Read from the rendered Google Flights UI; fares and availability are volatile.",
            "No booking or checkout action was performed.",
            "Baggage through-checking and missed-connection protection require seller confirmation.",
        ],
    }
