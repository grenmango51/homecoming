#!/usr/bin/env python3
"""Analyzer for Exhaustive 186-Region Google Flights POS Scan.

Processes all JSON and CSV records in flight_results_all_pos/,
ranks all 186 Points of Sale, identifies the absolute global minimum fare,
and outputs formatted Markdown comparison tables.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any
import pandas as pd
from tabulate import tabulate

from src.config import PROJECT_ROOT

ALL_POS_DIR = PROJECT_ROOT / "flight_results_all_pos"
SUMMARY_CSV = ALL_POS_DIR / "all_pos_summary_report.csv"


def load_pos_data() -> pd.DataFrame:
    if SUMMARY_CSV.exists():
        try:
            df = pd.read_csv(SUMMARY_CSV)
            if not df.empty and "lowest_price_eur" in df.columns:
                return df[df["status"] == "observed"].copy()
        except Exception:
            pass

    # Fallback to loading all JSONs
    records = []
    if not ALL_POS_DIR.exists():
        return pd.DataFrame()
    for f in ALL_POS_DIR.glob("*.json"):
        if f.name == "all_pos_summary_report.json":
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            if d.get("status") == "observed" and d.get("lowest_price_eur"):
                top_c = d.get("cards", [{}])[0] if d.get("cards") else {}
                records.append({
                    "origin": d["origin"],
                    "destination": d["destination"],
                    "departure_date": d["departure_date"],
                    "return_date": d["return_date"],
                    "stay_nights": d["stay_nights"],
                    "gl": d["gl"],
                    "status": d["status"],
                    "lowest_price_eur": d["lowest_price_eur"],
                    "carrier": ", ".join(top_c.get("carriers", [])) or "Unknown",
                    "duration_minutes": top_c.get("duration_minutes"),
                    "stops": top_c.get("stops"),
                    "layovers": "/".join(top_c.get("layovers", [])),
                    "fetched_at": d.get("fetched_at"),
                })
        except Exception:
            continue
    return pd.DataFrame(records)


def run_all_pos_analysis():
    df = load_pos_data()
    print(f"Loaded {len(df)} observed POS flight records from {ALL_POS_DIR}.")
    if df.empty:
        print("No observed records found yet.")
        return

    df["date_pair"] = df["departure_date"] + " -> " + df["return_date"]

    # 1. Overall Global Minimums
    abs_min = df["lowest_price_eur"].min()
    abs_max = df["lowest_price_eur"].max()
    print("\n" + "=" * 90)
    print(f"GLOBAL ARBITRAGE EXTREMES: Min = €{abs_min} | Max = €{abs_max} | Global Spread = €{abs_max - abs_min}")
    print("=" * 90)

    # 2. Cheapest POS for Each Date Pair
    date_summaries = []
    for date_pair, group in df.groupby("date_pair"):
        min_row = group.loc[group["lowest_price_eur"].idxmin()]
        max_row = group.loc[group["lowest_price_eur"].idxmax()]
        fi_rows = group[group["gl"] == "FI"]
        fi_price = fi_rows["lowest_price_eur"].iloc[0] if not fi_rows.empty else None

        delta = (fi_price - min_row["lowest_price_eur"]) if fi_price is not None else 0.0

        date_summaries.append({
            "Date Pair": date_pair,
            "Cheapest POS": f"{min_row['gl']} ({min_row.get('region_name', min_row['gl'])})",
            "Cheapest Fare (€)": min_row["lowest_price_eur"],
            "Cheapest Carrier": min_row["carrier"],
            "Finland (€)": fi_price,
            "Highest POS": f"{max_row['gl']}",
            "Highest Fare (€)": max_row["lowest_price_eur"],
            "Spread (€)": max_row["lowest_price_eur"] - min_row["lowest_price_eur"],
            "Savings vs FI (€)": delta,
            "Savings (%)": round((delta / fi_price * 100), 1) if fi_price else 0.0,
            "POS Sample Count": len(group),
        })

    sum_df = pd.DataFrame(date_summaries)
    if not sum_df.empty:
        print("\n--- CHEAPEST POINT OF SALE PER DATE PAIR ---")
        print(tabulate(sum_df, headers="keys", tablefmt="github", showindex=False))

    # 3. Global Ranking of Regions across all date pairs (Mean Price)
    pos_ranking = []
    for gl, group in df.groupby("gl"):
        reg_name = group.get("region_name", gl).iloc[0] if "region_name" in group else gl
        mean_p = group["lowest_price_eur"].mean()
        min_p = group["lowest_price_eur"].min()
        max_p = group["lowest_price_eur"].max()
        pos_ranking.append({
            "gl": gl,
            "Region": reg_name,
            "Obs Count": len(group),
            "Mean Fare (€)": round(mean_p, 1),
            "Min Fare (€)": min_p,
            "Max Fare (€)": max_p,
            "Spread (€)": max_p - min_p,
        })

    rank_df = pd.DataFrame(pos_ranking).sort_values(by="Mean Fare (€)", ascending=True)
    print("\n--- TOP 20 CHEAPEST GLOBAL POINTS OF SALE ---")
    print(tabulate(rank_df.head(20), headers="keys", tablefmt="github", showindex=False))

    print("\n--- TOP 10 MOST EXPENSIVE GLOBAL POINTS OF SALE ---")
    print(tabulate(rank_df.tail(10), headers="keys", tablefmt="github", showindex=False))


if __name__ == "__main__":
    run_all_pos_analysis()
