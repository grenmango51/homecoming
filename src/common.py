"""Shared utilities for Google Flights and Skyscanner scrapers.

Includes date math, search pair generation, browser executable discovery,
and common string/price/duration parsers.
"""

from __future__ import annotations

import datetime as dt
import os
import re
import sys
from pathlib import Path

# Currency & duration regular expressions
PRICE_RE = re.compile(r"(?:€\s*|EUR\s*)([0-9][0-9.,\s]*)", re.IGNORECASE)
PRICE_SUFFIX_RE = re.compile(r"([0-9][0-9.,\s]*)\s*(?:€|euros?|eur\b)", re.IGNORECASE)
EUROS_RE = re.compile(r"\b([0-9][0-9.,\s]*)\s+euros?\b", re.IGNORECASE)

DURATION_EN_RE = re.compile(r"\b(\d{1,2})\s*(?:h|hr|hours?)\s*(?:(\d{1,2})\s*(?:m|min|minutes?))?\b", re.IGNORECASE)
DURATION_FI_RE = re.compile(r"\b(\d{1,2})\s*(?:t|tuntia)\s*(?:(\d{1,2})\s*(?:min|minuuttia))?\b", re.IGNORECASE)

def configure_stdio() -> None:
    """Force UTF-8 line-buffered output so fare symbols survive a Windows console."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace", line_buffering=True)


def date_range(start: str, end: str) -> list[dt.date]:
    """Generate an inclusive list of dates between ISO format start and end."""
    first, last = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    if last < first:
        raise ValueError(f"End date {end} is before start date {start}.")
    return [first + dt.timedelta(days=offset) for offset in range((last - first).days + 1)]

def build_search_pairs(
    *,
    exact_pairs: list[list[str]] | list[tuple[str, str]] | None = None,
    window_start: str | None = None,
    window_end: str | None = None,
    depart_from: str | None = None,
    depart_to: str | None = None,
    return_from: str | None = None,
    return_to: str | None = None,
    min_stay_nights: int = 21,
) -> list[tuple[dt.date, dt.date]]:
    """Generate date pairs for explicit pairs, a continuous trip window, or ranges."""
    if exact_pairs:
        pairs: list[tuple[dt.date, dt.date]] = []
        for pair in exact_pairs:
            dep = dt.date.fromisoformat(pair[0])
            ret = dt.date.fromisoformat(pair[1])
            if (ret - dep).days >= min_stay_nights:
                pairs.append((dep, ret))
        return pairs

    if window_start and window_end:
        start_d = dt.date.fromisoformat(window_start)
        end_d = dt.date.fromisoformat(window_end)
        if end_d < start_d:
            raise ValueError(f"Window end {window_end} is before start date {window_start}.")
        pairs = []
        cur_dep = start_d
        while cur_dep <= end_d:
            cur_ret = cur_dep + dt.timedelta(days=min_stay_nights)
            while cur_ret <= end_d:
                pairs.append((cur_dep, cur_ret))
                cur_ret += dt.timedelta(days=1)
            cur_dep += dt.timedelta(days=1)
        return pairs

    if not (depart_from and depart_to and return_from and return_to):
        raise ValueError("Must specify either exact_pairs, window_start/window_end, or full depart/return ranges.")

    departures = date_range(depart_from, depart_to)
    returns = date_range(return_from, return_to)
    return [
        (departure, return_date)
        for departure in departures
        for return_date in returns
        if (return_date - departure).days >= min_stay_nights
    ]

def resolve_browser_executable(requested: str | None = None) -> str | None:
    """Prefer an installed stable browser when Playwright Chromium is unusable.

    A dedicated profile is still used, so everyday user profiles are never opened or modified.
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

def parse_numeric_price(raw: str) -> float | None:
    """Clean and convert price string to float, handling separators and symbols."""
    s = re.sub(r"[^\d.,]", "", raw.strip().replace("\u00a0", ""))
    if not s:
        return None
    if re.match(r"^\d{1,2}[,.]\d{3}$", s):
        s = s.replace(",", "").replace(".", "")
    elif "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        parts = s.split(",")
        if len(parts) == 2 and len(parts[1]) == 3:
            s = "".join(parts)
        else:
            s = s.replace(",", ".")
    try:
        val = float(s)
        return val if 0.01 <= val <= 100_000.0 else None
    except ValueError:
        return None

def duration_minutes(value: str) -> int | None:
    """Parse duration strings like '16 hr 15 min', '2h 30m', or '17 t 35 min' into minutes."""
    match = DURATION_EN_RE.search(value) or DURATION_FI_RE.search(value)
    if not match:
        return None
    return int(match.group(1)) * 60 + int(match.group(2) or 0)
