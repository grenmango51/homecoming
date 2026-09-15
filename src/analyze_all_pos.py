#!/usr/bin/env python3
"""Analyzer for the exhaustive all-POS Google Flights scan.

Ranks every point of sale in ``flight_results_all_pos/``, finds the absolute
global minimum fare, and prints Markdown comparison tables.
"""

from __future__ import annotations

import pandas as pd
from tabulate import tabulate

from src.analysis import load_pos_observations
from src.config import PROJECT_ROOT

ALL_POS_DIR = PROJECT_ROOT / "flight_results_all_pos"

# Market treated as "booking from home" for the HEL -> HAN corridor.
DOMESTIC_GL = "FI"


def summarize_date_pairs(df: pd.DataFrame) -> pd.DataFrame:
    """Cheapest and dearest market per date pair, against the domestic baseline."""
    rows = []
    for date_pair, group in df.groupby("date_pair"):
        cheapest = group.loc[group["lowest_price_eur"].idxmin()]
        priciest = group.loc[group["lowest_price_eur"].idxmax()]
        domestic = group[group["gl"] == DOMESTIC_GL]
        domestic_price = domestic["lowest_price_eur"].iloc[0] if not domestic.empty else None
        savings = (domestic_price - cheapest["lowest_price_eur"]) if domestic_price is not None else 0.0

        rows.append({
            "Date Pair": date_pair,
            "Cheapest POS": f"{cheapest['gl']} ({cheapest['region_name']})",
            "Cheapest Fare (€)": cheapest["lowest_price_eur"],
            "Cheapest Carrier": cheapest["carrier"],
            f"{DOMESTIC_GL} Baseline (€)": domestic_price,
            "Highest POS": priciest["gl"],
            "Highest Fare (€)": priciest["lowest_price_eur"],
            "Spread (€)": priciest["lowest_price_eur"] - cheapest["lowest_price_eur"],
            f"Savings vs {DOMESTIC_GL} (€)": savings,
            "Savings (%)": round(savings / domestic_price * 100, 1) if domestic_price else 0.0,
            "POS Sample Count": len(group),
        })
    return pd.DataFrame(rows)


def rank_markets(df: pd.DataFrame) -> pd.DataFrame:
    """Every market ranked by mean fare, cheapest first."""
    return pd.DataFrame([
        {
            "gl": gl,
            "Region": group["region_name"].iloc[0],
            "Obs Count": len(group),
            "Mean Fare (€)": round(group["lowest_price_eur"].mean(), 1),
            "Min Fare (€)": group["lowest_price_eur"].min(),
            "Max Fare (€)": group["lowest_price_eur"].max(),
            "Spread (€)": group["lowest_price_eur"].max() - group["lowest_price_eur"].min(),
        }
        for gl, group in df.groupby("gl")
    ]).sort_values(by="Mean Fare (€)")


def run_all_pos_analysis() -> None:
    df = load_pos_observations(ALL_POS_DIR)
    print(f"Loaded {len(df)} observed POS flight records from {ALL_POS_DIR}.")
    if df.empty:
        print("No observed records found yet.")
        return

    lowest = df["lowest_price_eur"].min()
    highest = df["lowest_price_eur"].max()
    print("\n" + "=" * 90)
    print(f"GLOBAL ARBITRAGE EXTREMES: Min = €{lowest} | Max = €{highest} | Global Spread = €{highest - lowest}")
    print("=" * 90)

    date_summary = summarize_date_pairs(df)
    if not date_summary.empty:
        print("\n--- CHEAPEST POINT OF SALE PER DATE PAIR ---")
        print(tabulate(date_summary, headers="keys", tablefmt="github", showindex=False))

    ranking = rank_markets(df)
    print("\n--- TOP 20 CHEAPEST GLOBAL POINTS OF SALE ---")
    print(tabulate(ranking.head(20), headers="keys", tablefmt="github", showindex=False))

    print("\n--- TOP 10 MOST EXPENSIVE GLOBAL POINTS OF SALE ---")
    print(tabulate(ranking.tail(10), headers="keys", tablefmt="github", showindex=False))


if __name__ == "__main__":
    run_all_pos_analysis()
