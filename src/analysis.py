"""Shared loading and statistics for the point-of-sale arbitrage analyzers.

All three analyzers read the same on-disk shape -- one JSON observation per
(route, date pair, market) -- and compute the same two primitives on it: the
per-date statistical mode, and which market won the global minimum.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from src.regions import load_region_name_map
from src.reporting import read_json

# Roll-ups and derived exports written alongside the observations; never data.
REPORT_FILENAMES = {
    "summary_report.json",
    "all_pos_summary_report.json",
    "active_market_gl_codes.json",
    "filtered_active_arbitrage_markets.json",
}


def load_pos_observations(
    directory: Path,
    *,
    gl_map: dict[str, str] | None = None,
    min_price: float = 0.0,
    default_origin: str = "",
    default_dest: str = "",
) -> pd.DataFrame:
    """Load every observed fare in ``directory`` into a tidy DataFrame.

    Returns an empty frame when the directory is missing or holds no observed
    records. A ``date_pair`` column is added for grouping.
    """
    if not directory.exists():
        return pd.DataFrame()

    gl_map = gl_map if gl_map is not None else load_region_name_map()
    records: list[dict[str, Any]] = []

    for path in directory.glob("*.json"):
        if path.name in REPORT_FILENAMES:
            continue
        data = read_json(path)
        if not isinstance(data, dict) or data.get("status") != "observed":
            continue

        price = data.get("lowest_price_eur")
        try:
            price = float(price) if price is not None else None
        except (TypeError, ValueError):
            continue
        if price is None or price < min_price:
            continue

        cards = data.get("cards") or []
        top_card = cards[0] if cards else {}
        layovers = top_card.get("layovers")
        gl = data.get("gl")

        records.append({
            "origin": data.get("origin", default_origin),
            "destination": data.get("destination", default_dest),
            "departure_date": data.get("departure_date"),
            "return_date": str(data.get("return_date")),
            "stay_nights": data.get("stay_nights", 0),
            "gl": gl,
            "region_name": gl_map.get(gl, data.get("region_name", gl)),
            "status": "observed",
            "lowest_price_eur": price,
            "carrier": ", ".join(top_card.get("carriers", [])) or "Unknown",
            "duration_minutes": top_card.get("duration_minutes"),
            "stops": top_card.get("stops"),
            "layovers": "/".join(layovers) if isinstance(layovers, list) else "",
            "search_url": data.get("search_url"),
            "fetched_at": data.get("fetched_at"),
        })

    df = pd.DataFrame(records)
    if not df.empty:
        df["date_pair"] = df["departure_date"] + " -> " + df["return_date"]
    return df


def date_pair_modes(df: pd.DataFrame) -> dict[str, dict[str, Any]]:
    """Per date pair, the modal fare across markets and how dominant it is.

    The mode approximates the unlocalized GDS fallback tariff: the price a
    market returns when it has no localized inventory of its own.
    """
    modes: dict[str, dict[str, Any]] = {}
    for date_pair, group in df.groupby("date_pair"):
        prices = group["lowest_price_eur"]
        mode_series = prices.mode()
        mode_value = mode_series.iloc[0] if not mode_series.empty else prices.median()
        mode_frequency = int((prices == mode_value).sum())
        modes[date_pair] = {
            "mode_price": mode_value,
            "mode_frequency": mode_frequency,
            "total_markets": len(group),
            "pct_mode": round(mode_frequency / len(group) * 100, 1),
            "min_price": prices.min(),
            "max_price": prices.max(),
        }
    return modes


def global_min_wins(df: pd.DataFrame) -> dict[str, int]:
    """How often each market was (tied for) cheapest on a date pair."""
    wins: dict[str, int] = {}
    for _, group in df.groupby("date_pair"):
        lowest = group["lowest_price_eur"].min()
        for gl in group[group["lowest_price_eur"] == lowest]["gl"]:
            wins[gl] = wins.get(gl, 0) + 1
    return wins


def count_discounts(group: pd.DataFrame, modes: dict[str, dict[str, Any]]) -> int:
    """Number of observations in ``group`` priced below their date pair's mode."""
    return sum(
        1
        for _, row in group.iterrows()
        if (mode := modes.get(row["date_pair"])) and row["lowest_price_eur"] < mode["mode_price"]
    )
