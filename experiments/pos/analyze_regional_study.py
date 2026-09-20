#!/usr/bin/env python3
"""Automated Statistical & Mechanistic Analyzer for Regional Dynamic Pricing Study.

Ingests all JSON observation files from flight_results_study/, performs cross-region
and cross-airline arbitrage calculations, assesses Point of Origin vs Point of Sale effects,
and generates structured matrices and markdown report sections.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import pandas as pd
from tabulate import tabulate

from src.config import PROJECT_ROOT

STUDY_RESULTS_DIR = PROJECT_ROOT / "flight_results_study"

# Estimated September 2026 live Forex rates against EUR (1 EUR = X Foreign Currency)
FX_RATES_PER_EUR = {
    "EUR": 1.0,
    "USD": 1.085,
    "GBP": 0.852,
    "TRY": 52.3,
    "VND": 30120.0,
    "QAR": 3.95,
    "AED": 3.98,
    "SEK": 11.40,
}


def load_study_data(results_dir: Path = STUDY_RESULTS_DIR) -> list[dict[str, Any]]:
    records = []
    if not results_dir.exists():
        return records
    for file in results_dir.glob("*.json"):
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
            if data.get("status") == "observed" and data.get("lowest_price") is not None:
                data["_filepath"] = str(file)
                records.append(data)
        except Exception:
            continue
    return records


def analyze_outbound_pos_arbitrage(records: list[dict[str, Any]]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Analyze HEL -> HAN flights across regions holding currency in EUR."""
    outbound = [
        r for r in records
        if r.get("origin") == "HEL"
        and r.get("destination") == "HAN"
        and r.get("currency") == "EUR"
    ]
    if not outbound:
        return pd.DataFrame(), pd.DataFrame()

    # Build row per observation
    rows = []
    for r in outbound:
        dep = r["departure_date"]
        ret = r["return_date"]
        gl = r["gl"]
        price = r["lowest_price"]
        # Top airline and duration
        cards = r.get("cards", [])
        top_carrier = "Unknown"
        duration_m = None
        stops = None
        layovers = []
        if cards:
            c0 = cards[0]
            top_carrier = ", ".join(c0.get("carriers", [])) or "Unknown"
            duration_m = c0.get("duration_minutes")
            stops = c0.get("stops")
            layovers = c0.get("layovers", [])

        rows.append({
            "departure": dep,
            "return": ret,
            "date_pair": f"{dep} -> {ret}",
            "gl": gl,
            "price_eur": price,
            "carrier": top_carrier,
            "duration_m": duration_m,
            "stops": stops,
            "layovers": "/".join(layovers),
            "reported_loc": r.get("reported_location", "Unknown"),
        })

    df = pd.DataFrame(rows)

    # Pivot table: Date Pair x Region Price
    pivot_prices = df.pivot_table(
        index="date_pair",
        columns="gl",
        values="price_eur",
        aggfunc="min"
    )

    # Summary analysis per date pair
    summary_rows = []
    for date_pair, group in df.groupby("date_pair"):
        prices_by_gl = dict(zip(group["gl"], group["price_eur"]))
        fi_price = prices_by_gl.get("FI") or prices_by_gl.get("NONE")
        if not prices_by_gl:
            continue

        min_gl = min(prices_by_gl, key=prices_by_gl.get)
        min_price = prices_by_gl[min_gl]
        max_gl = max(prices_by_gl, key=prices_by_gl.get)
        max_price = prices_by_gl[max_gl]

        delta = (fi_price - min_price) if fi_price is not None else 0.0
        pct = (delta / fi_price * 100) if fi_price and fi_price > 0 else 0.0

        # Find best carrier for cheapest fare
        best_row = group[group["gl"] == min_gl].iloc[0]

        summary_rows.append({
            "Date Pair": date_pair,
            "Finland Baseline (€)": fi_price,
            "Cheapest POS": min_gl,
            "Cheapest Fare (€)": min_price,
            "Cheapest Carrier": best_row["carrier"],
            "Layover": best_row["layovers"],
            "Max POS": max_gl,
            "Highest Fare (€)": max_price,
            "Max Spread (€)": max_price - min_price,
            "Savings vs FI (€)": delta,
            "Savings (%)": round(pct, 1),
        })

    summary_df = pd.DataFrame(summary_rows)
    return pivot_prices, summary_df


def analyze_airline_variance(records: list[dict[str, Any]]) -> pd.DataFrame:
    """Analyze price variance per airline across regions for identical date pairs."""
    card_rows = []
    for r in records:
        if r.get("origin") != "HEL" or r.get("destination") != "HAN" or r.get("currency") != "EUR":
            continue
        gl = r["gl"]
        dep = r["departure_date"]
        ret = r["return_date"]
        date_pair = f"{dep} -> {ret}"
        for card in r.get("cards", []):
            p = card.get("lowest_price")
            carriers = card.get("carriers", [])
            dur = card.get("duration_minutes")
            stops = card.get("stops")
            layovers = "/".join(card.get("layovers", []))
            if not p or not carriers:
                continue
            for carrier in carriers:
                card_rows.append({
                    "date_pair": date_pair,
                    "gl": gl,
                    "airline": carrier,
                    "price_eur": p,
                    "duration_m": dur,
                    "stops": stops,
                    "layovers": layovers,
                })

    if not card_rows:
        return pd.DataFrame()

    cdf = pd.DataFrame(card_rows)

    # For each airline, calculate min, max, avg, and spread across regions
    airline_stats = []
    for airline, group in cdf.groupby("airline"):
        if len(group) < 5:
            continue
        min_p = group["price_eur"].min()
        max_p = group["price_eur"].max()
        mean_p = group["price_eur"].mean()
        spread = max_p - min_p
        spread_pct = (spread / min_p * 100) if min_p > 0 else 0.0

        # Find best POS for this airline
        pos_means = group.groupby("gl")["price_eur"].mean()
        best_pos = pos_means.idxmin()
        worst_pos = pos_means.idxmax()

        airline_stats.append({
            "Airline": airline,
            "Observations": len(group),
            "Min Fare (€)": min_p,
            "Max Fare (€)": max_p,
            "Mean Fare (€)": round(mean_p, 1),
            "Price Spread (€)": round(spread, 1),
            "Spread (%)": round(spread_pct, 1),
            "Cheapest POS": best_pos,
            "Most Expensive POS": worst_pos,
        })

    res_df = pd.DataFrame(airline_stats)
    if not res_df.empty:
        res_df = res_df.sort_values(by="Price Spread (€)", ascending=False)
    return res_df


def analyze_directionality_control(records: list[dict[str, Any]]) -> pd.DataFrame:
    """Compare Outbound (HEL -> HAN) vs Reverse (HAN -> HEL) across regions."""
    outbound = {
        (r["departure_date"], r["return_date"], r["gl"]): r["lowest_price"]
        for r in records
        if r.get("origin") == "HEL" and r.get("destination") == "HAN" and r.get("currency") == "EUR"
    }
    reverse = {
        (r["departure_date"], r["return_date"], r["gl"]): r["lowest_price"]
        for r in records
        if r.get("origin") == "HAN" and r.get("destination") == "HEL" and r.get("currency") == "EUR"
    }

    common_keys = set(outbound.keys()).intersection(set(reverse.keys()))
    if not common_keys:
        return pd.DataFrame()

    rows = []
    for dep, ret, gl in sorted(common_keys):
        out_p = outbound[(dep, ret, gl)]
        rev_p = reverse[(dep, ret, gl)]
        diff = out_p - rev_p
        pct = (diff / out_p * 100) if out_p else 0.0
        rows.append({
            "Departure": dep,
            "Return": ret,
            "Region (gl)": gl,
            "HEL -> HAN Fare (€)": out_p,
            "HAN -> HEL Fare (€)": rev_p,
            "Difference (€)": round(diff, 1),
            "Origin Discount (%)": round(pct, 1),
        })

    return pd.DataFrame(rows)


def analyze_currency_arbitrage(records: list[dict[str, Any]]) -> pd.DataFrame:
    """Analyze currency quotation vs EUR equivalent with live forex conversion."""
    curr_records = [
        r for r in records
        if r.get("category") == "currency_arbitrage" or r.get("currency") != "EUR"
    ]
    if not curr_records:
        return pd.DataFrame()

    # Find matching EUR baseline for 2026-12-09 -> 2027-01-05
    eur_baseline = {
        r["gl"]: r["lowest_price"]
        for r in records
        if r.get("departure_date") == "2026-12-09"
        and r.get("return_date") == "2027-01-05"
        and r.get("currency") == "EUR"
        and r.get("origin") == "HEL"
    }

    rows = []
    for r in curr_records:
        gl = r["gl"]
        curr = r["currency"]
        native_price = r["lowest_price"]
        rate = FX_RATES_PER_EUR.get(curr, 1.0)
        converted_eur = native_price / rate if rate > 0 else native_price
        google_eur = eur_baseline.get(gl)

        fx_spread_eur = (converted_eur - google_eur) if google_eur else 0.0
        fx_spread_pct = (fx_spread_eur / google_eur * 100) if google_eur else 0.0

        rows.append({
            "Region": gl,
            "Currency": curr,
            "Native Fare": f"{native_price:,.0f} {curr}",
            "FX Rate / EUR": rate,
            "Converted to EUR (€)": round(converted_eur, 1),
            "Google EUR Quote (€)": google_eur,
            "FX Spread vs Google (€)": round(fx_spread_eur, 1),
            "Variance (%)": round(fx_spread_pct, 1),
        })

    return pd.DataFrame(rows)


def run_full_analysis():
    records = load_study_data()
    print(f"Loaded {len(records)} valid observed records from {STUDY_RESULTS_DIR}.")
    if not records:
        print("No records found yet.")
        return

    pivot_prices, summary_df = analyze_outbound_pos_arbitrage(records)
    airline_df = analyze_airline_variance(records)
    dir_df = analyze_directionality_control(records)
    curr_df = analyze_currency_arbitrage(records)

    print("\n" + "=" * 90)
    print("1. OUTBOUND POS ARBITRAGE SUMMARY (HEL -> HAN)")
    print("=" * 90)
    if not summary_df.empty:
        print(tabulate(summary_df, headers="keys", tablefmt="github", showindex=False))

    print("\n" + "=" * 90)
    print("2. REGIONAL PRICE MATRIX (Date Pair x Region [EUR])")
    print("=" * 90)
    if not pivot_prices.empty:
        print(tabulate(pivot_prices.reset_index(), headers="keys", tablefmt="github", showindex=False))

    print("\n" + "=" * 90)
    print("3. AIRLINE PRICE SPREAD & BEST POS")
    print("=" * 90)
    if not airline_df.empty:
        print(tabulate(airline_df, headers="keys", tablefmt="github", showindex=False))

    print("\n" + "=" * 90)
    print("4. DIRECTIONALITY & POINT OF ORIGIN (POO) EFFECT")
    print("=" * 90)
    if not dir_df.empty:
        print(tabulate(dir_df, headers="keys", tablefmt="github", showindex=False))

    print("\n" + "=" * 90)
    print("5. CURRENCY CONVERSION & IATA FOREX DIVERGENCE")
    print("=" * 90)
    if not curr_df.empty:
        print(tabulate(curr_df, headers="keys", tablefmt="github", showindex=False))


if __name__ == "__main__":
    run_full_analysis()
