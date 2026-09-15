#!/usr/bin/env python3
"""Cross-corridor master analyzer for the Google Flights POS study.

Aggregates every completed route scan into one comparison matrix plus a detailed
section per corridor: statistical mode behaviour, domestic benchmark versus the
global minimum, and market rankings by mean, median, minimum and win count.

Writes ``MASTER_ARBITRAGE_STUDY.md`` at the project root.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
from tabulate import tabulate

from src.analysis import count_discounts, date_pair_modes, global_min_wins, load_pos_observations
from src.common import configure_stdio
from src.config import PROJECT_ROOT
from src.regions import load_region_name_map
from src.reporting import today_stamp

OUTPUT_FILE = PROJECT_ROOT / "MASTER_ARBITRAGE_STUDY.md"

# Fares below this are parsing noise (fees, ancillaries) rather than itineraries.
MIN_PLAUSIBLE_FARE = 15.0

ROUTES = [
    {
        "id": "HEL_HAN",
        "title": "Helsinki (HEL) -> Hanoi (HAN)",
        "type": "Round-Trip Long-Haul",
        "dir": PROJECT_ROOT / "flight_results_all_pos",
        "domestic_gl": "FI",
        "domestic_name": "Finland",
    },
    {
        "id": "PHL_HAN",
        "title": "Philadelphia (PHL) -> Hanoi (HAN)",
        "type": "Round-Trip Transpacific / Intercontinental",
        "dir": PROJECT_ROOT / "flight_results_PHL_HAN",
        "domestic_gl": "US",
        "domestic_name": "United States",
    },
    {
        "id": "HEL_BRU",
        "title": "Helsinki (HEL) -> Brussels (BRU)",
        "type": "One-Way Intra-Europe Outbound",
        "dir": PROJECT_ROOT / "flight_results_HEL_BRU",
        "domestic_gl": "FI",
        "domestic_name": "Finland",
    },
    {
        "id": "AMS_HEL",
        "title": "Amsterdam (AMS) -> Helsinki (HEL)",
        "type": "One-Way Intra-Europe Return",
        "dir": PROJECT_ROOT / "flight_results_AMS_HEL",
        "domestic_gl": "NL",
        "domestic_name": "Netherlands",
    },
    {
        "id": "SIN_HAN",
        "title": "Singapore (SIN) -> Hanoi (HAN)",
        "type": "Round-Trip Intra-Asia Regional",
        "dir": PROJECT_ROOT / "flight_results_SIN_HAN",
        "domestic_gl": "SG",
        "domestic_name": "Singapore",
    },
]

# Hand-written interpretation of the September 2026 run. The figures quoted here
# are fixed prose, NOT recomputed from the data above -- re-verify them against
# the generated tables before citing them after a fresh scan.
STATIC_COMMENTARY = """## Appendix: Global Distribution System (GDS) & Reseller Pricing Mechanisms

> **Note**: this section is static commentary from the September 2026 study run.
> The numbers below are not recomputed by this script; check them against the
> tables above after any new scan.

### A. The "Unlocalized Mode Tariff" Rule (Fallback Fare)
Across all long-haul corridors, between **51% and 76%** of sovereign country codes returned
the exact same price (the statistical mode).
- Countries such as Afghanistan (AF), Belize (BZ), Burundi (BI) and Bhutan (BT) lack localized
  ticketing agreements or regional carrier representation.
- Absent localized currency filings or point-of-sale discounts, the GDS serves the published
  **IATA / GDS baseline tariff**.
- Filtering out these non-dynamic fallback markets cut scraping overhead by **30-40%** with no
  loss in arbitrage discovery.

### B. Nordic Currency Basket Arbitrage (SEK / NOK Advantage)
On Europe-to-Asia routes (`HEL -> HAN`, `HEL -> BRU`), Scandinavian markets -- **Sweden (`SE`)**,
**Norway (`NO`)** and **Denmark (`DK`)** -- consistently captured the lowest fares.
- Sweden took 12 global minimum wins on HEL -> HAN, producing fares up to **EUR 370 cheaper**
  than generic GDS tariffs and **EUR 11-15 cheaper** than the Finnish domestic baseline.
- **Mechanism**: dynamic currency conversion buffers plus localized bilateral distribution
  agreements with SAS and partner alliances create price drops when converted back to EUR.

### C. Southeast Asian & UK Point-of-Sale Advantages on US Transpacific Routes
On Philadelphia to Hanoi (`PHL -> HAN`):
- Booking via the domestic US market (`US`) averaged **EUR 1,651.8**.
- Ticketing through the **United Kingdom (`GB`, 1,623.4)**, **Philippines (`PH`, 1,624.1)**,
  **Thailand (`TH`, 1,624.4)**, **Kuwait (`KW`, 1,624.6)** or **Singapore (`SG`, 1,627.8)**
  saved **EUR 28-35 per ticket**.
- **Mechanism**: carriers publish lower base fare inventory (booking buckets) in Asian
  origin/destination markets to stimulate local demand, while charging a premium to US-based
  POS users with higher willingness to pay.

### D. Intra-European Route Parity vs Single-Carrier Markups
On short-haul intra-EU routes (`HEL -> BRU`, `AMS -> HEL`):
- POS arbitrage is tighter than on long-haul international flights, due to EU price transparency
  rules (Regulation EC No 1008/2008).
- Differences still emerge between non-stop flag carriers (Finnair, Brussels Airlines) and
  connecting carriers (KLM, SAS), where a foreign POS ticketed via Sweden or Norway bypasses
  domestic direct-flight surcharges.
"""


MARKET_COLUMNS = ["gl", "region_name", "mean_eur", "median_eur", "min_eur", "spread_eur", "discount_rate_pct", "global_min_wins"]
MARKET_HEADERS = ["GL", "Market", "Mean (€)", "Median (€)", "Min (€)", "Spread (€)", "Discount Rate (%)", "Min Wins"]


def analyze_corridor(df: pd.DataFrame, route: dict[str, Any]) -> dict[str, Any]:
    """Summarise one corridor: modes, per-market aggregates, domestic vs global minimum."""
    if df.empty:
        return {}

    modes = date_pair_modes(df)
    wins = global_min_wins(df)

    market_df = pd.DataFrame([
        {
            "gl": gl,
            "region_name": group["region_name"].iloc[0],
            "queries": len(group),
            "mean_eur": round(group["lowest_price_eur"].mean(), 1),
            "median_eur": round(group["lowest_price_eur"].median(), 1),
            "min_eur": round(group["lowest_price_eur"].min(), 0),
            "max_eur": round(group["lowest_price_eur"].max(), 0),
            "spread_eur": round(group["lowest_price_eur"].max() - group["lowest_price_eur"].min(), 0),
            "discount_queries": count_discounts(group, modes),
            "discount_rate_pct": round(count_discounts(group, modes) / len(group) * 100, 1),
            "global_min_wins": wins.get(gl, 0),
        }
        for gl, group in df.groupby("gl")
    ])

    domestic = market_df[market_df["gl"] == route["domestic_gl"]]
    domestic_mean = domestic["mean_eur"].iloc[0] if not domestic.empty else None
    domestic_median = domestic["median_eur"].iloc[0] if not domestic.empty else None
    domestic_min = domestic["min_eur"].iloc[0] if not domestic.empty else None

    absolute_min = market_df["min_eur"].min()
    top_mean = market_df.sort_values(by="mean_eur").iloc[0]
    top_median = market_df.sort_values(by=["median_eur", "mean_eur"]).iloc[0]

    max_savings = (domestic_min - absolute_min) if domestic_min is not None else 0.0
    mean_savings = (domestic_mean - top_mean["mean_eur"]) if domestic_mean is not None else 0.0

    return {
        "route": route,
        "total_observations": len(df),
        "unique_markets": df["gl"].nunique(),
        "date_pairs_count": df["date_pair"].nunique(),
        "modes": modes,
        "market_df": market_df,
        "domestic_mean": domestic_mean,
        "domestic_median": domestic_median,
        "domestic_min": domestic_min,
        "abs_min": absolute_min,
        "abs_min_markets": market_df[market_df["min_eur"] == absolute_min]["gl"].tolist(),
        "top_mean": top_mean.to_dict(),
        "top_median": top_median.to_dict(),
        "max_savings_eur": max_savings,
        "max_savings_pct": round(max_savings / domestic_min * 100, 1) if domestic_min else 0.0,
        "mean_savings_eur": mean_savings,
        "mean_savings_pct": round(mean_savings / domestic_mean * 100, 1) if domestic_mean else 0.0,
    }


def _money(value: float | None, decimals: int = 0) -> str:
    return f"€{value:.{decimals}f}" if value is not None else "N/A"


def _corridor_section(result: dict[str, Any]) -> list[str]:
    route = result["route"]
    lines = [
        f"## {route['title']} ({route['type']})",
        f"- **Total Scanned**: {result['total_observations']} queries across "
        f"{result['unique_markets']} markets and {result['date_pairs_count']} date periods.",
        f"- **Domestic Benchmark ({route['domestic_name']} - `{route['domestic_gl']}`)**: "
        f"Mean {_money(result['domestic_mean'], 1)} | Median {_money(result['domestic_median'], 1)} "
        f"| Min {_money(result['domestic_min'])}",
        f"- **Cheapest Point of Sale by Mean**: **{result['top_mean']['region_name']} "
        f"(`{result['top_mean']['gl']}`)** at **{_money(result['top_mean']['mean_eur'], 1)}** "
        f"(Savings: {_money(result['mean_savings_eur'], 1)} / {result['mean_savings_pct']}%)",
        f"- **Cheapest Point of Sale by Median**: **{result['top_median']['region_name']} "
        f"(`{result['top_median']['gl']}`)** at **{_money(result['top_median']['median_eur'], 1)}**",
        f"- **Absolute Global Lowest Fare**: **{_money(result['abs_min'])}** via "
        f"{', '.join(result['abs_min_markets'])} (Max Spread vs Domestic: {_money(result['max_savings_eur'])})\n",
        "### Top 15 Cheapest Points of Sale (Ranked by Mean Fare):",
        tabulate(
            result["market_df"].sort_values(by="mean_eur").head(15)[MARKET_COLUMNS],
            headers=MARKET_HEADERS,
            tablefmt="github",
            showindex=False,
        ),
        "",
        "### Statistical Mode & Fare Spread Distribution per Date:",
        tabulate(
            pd.DataFrame([
                {
                    "Date Period": date_pair,
                    "Mode Fare (€)": _money(info["mode_price"]),
                    "Mode Frequency": f"{info['mode_frequency']}/{info['total_markets']} ({info['pct_mode']}%)",
                    "Lowest Fare (€)": _money(info["min_price"]),
                    "Highest Fare (€)": _money(info["max_price"]),
                    "Max Spread (€)": _money(info["max_price"] - info["min_price"]),
                }
                for date_pair, info in result["modes"].items()
            ]),
            headers="keys",
            tablefmt="github",
            showindex=False,
        ),
        "\n---\n",
    ]
    return lines


def generate_master_arbitrage_report() -> str:
    configure_stdio()
    gl_map = load_region_name_map()
    results: list[dict[str, Any]] = []

    for route in ROUTES:
        print(f"Loading data for {route['title']}...")
        origin, dest = route["id"].split("_")
        df = load_pos_observations(
            route["dir"],
            gl_map=gl_map,
            min_price=MIN_PLAUSIBLE_FARE,
            default_origin=origin,
            default_dest=dest,
        )
        result = analyze_corridor(df, route)
        if result:
            results.append(result)
            print(f"  Loaded {result['total_observations']} observations across {result['unique_markets']} markets.")
        else:
            print(f"  No observed data available yet for {route['id']}.")

    if not results:
        return "No completed route observations found."

    matrix = pd.DataFrame([
        {
            "Corridor": r["route"]["title"],
            "Corridor Type": r["route"]["type"],
            "Obs / Dates": f"{r['total_observations']} / {r['date_pairs_count']}",
            "Domestic Baseline": (
                f"{r['route']['domestic_gl']}: {_money(r['domestic_mean'], 1)} "
                f"(min {_money(r['domestic_min'])})"
            ),
            "Cheapest POS (Mean)": (
                f"{r['top_mean']['gl']} ({r['top_mean']['region_name']}) "
                f"({_money(r['top_mean']['mean_eur'], 1)})"
            ),
            "Cheapest POS (Absolute Min)": f"{', '.join(r['abs_min_markets'][:3])} ({_money(r['abs_min'])})",
            "Mean Savings vs Dom": (
                f"{_money(r['mean_savings_eur'], 1)} ({r['mean_savings_pct']}%)"
                if r["mean_savings_eur"] > 0
                else "Baseline is lowest"
            ),
            "Max Single-Ticket Spread": f"{_money(r['max_savings_eur'])} ({r['max_savings_pct']}%)",
        }
        for r in results
    ])

    total_observations = sum(r["total_observations"] for r in results)
    lines = [
        "# Comprehensive Google Flights Point of Sale (POS) Regional Arbitrage Study",
        "## Multi-Corridor Empirical Findings & GDS Dynamic Pricing Analysis\n",
        f"**Date Generated**: {today_stamp()}",
        f"**Total Flight Queries Evaluated**: {total_observations:,} observations "
        f"across {len(results)} international corridors\n",
        "## 1. Master Cross-Corridor Arbitrage Matrix",
        "Baseline domestic pricing against the globally cheapest point of sale, per route:\n",
        tabulate(matrix, headers="keys", tablefmt="github", showindex=False),
        "\n---\n",
    ]
    for result in results:
        lines.extend(_corridor_section(result))
    lines.append(STATIC_COMMENTARY)

    report = "\n".join(lines)
    OUTPUT_FILE.write_text(report, encoding="utf-8")
    print(f"\n[Master Analyzer] Master report successfully written to {OUTPUT_FILE}")
    return report


if __name__ == "__main__":
    generate_master_arbitrage_report()
