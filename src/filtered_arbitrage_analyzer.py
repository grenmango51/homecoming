#!/usr/bin/env python3
"""Statistical mode filter and arbitrage analyzer for the all-POS scan.

Implements the mode-exclusion filter:

1. Compute the modal fare per date pair across every scanned market. The mode
   approximates the generic GDS tariff, i.e. the default unlocalized price.
2. Drop any market whose fare was never below that mode -- it has no localized
   pricing to offer and only costs bandwidth to scan.
3. For the remaining active markets, report mean, median, minimum, discount
   frequency and win counts.
4. Export the filtered dataset and the reusable active-market list.
"""

from __future__ import annotations

import json

import pandas as pd
from tabulate import tabulate

from src.analysis import count_discounts, date_pair_modes, global_min_wins, load_pos_observations
from src.config import PROJECT_ROOT

ALL_POS_DIR = PROJECT_ROOT / "flight_results_all_pos"
FILTERED_CSV = ALL_POS_DIR / "filtered_flight_dataset.csv"
ACTIVE_MARKETS_JSON = ALL_POS_DIR / "active_market_gl_codes.json"
ACTIVE_SUMMARY_CSV = ALL_POS_DIR / "filtered_active_arbitrage_markets.csv"
ACTIVE_SUMMARY_JSON = ALL_POS_DIR / "filtered_active_arbitrage_markets.json"
REPORT_MD = ALL_POS_DIR / "MODE_FILTERED_ARBITRAGE_STUDY.md"

# Home markets for the studied corridors. These are always kept even when they
# never beat the mode, because the study needs their baseline to compare against.
MANDATORY_DOMESTIC_GLS = {"FI", "VN", "US", "BE", "NL", "SG"}

ACTIVE_COLUMNS = [
    "gl", "region_name", "mean_fare_eur", "median_fare_eur",
    "min_fare_eur", "discount_rate_pct", "global_min_wins",
]
MIN_COLUMNS = ["gl", "region_name", "min_fare_eur", "mean_fare_eur", "median_fare_eur", "global_min_wins"]


def partition_markets(
    df: pd.DataFrame, modes: dict[str, dict[str, object]]
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split markets into dynamically-priced (active) and fallback-only (filtered)."""
    active: list[dict[str, object]] = []
    filtered: list[dict[str, object]] = []

    for gl, group in df.groupby("gl"):
        region_name = group["region_name"].iloc[0]
        prices = group["lowest_price_eur"]
        discounts = count_discounts(group, modes)

        if discounts == 0 and gl not in MANDATORY_DOMESTIC_GLS:
            at_mode = sum(
                1
                for _, row in group.iterrows()
                if (m := modes.get(row["date_pair"])) and row["lowest_price_eur"] == m["mode_price"]
            )
            filtered.append({
                "gl": gl,
                "region_name": region_name,
                "total_queries": len(group),
                "times_at_mode": at_mode,
                "times_above_mode": len(group) - at_mode,
                "mean_fare": round(prices.mean(), 1),
                "reason": "Always >= Mode (Fallback / Inflated GDS Tariff)",
            })
        else:
            active.append({
                "gl": gl,
                "region_name": region_name,
                "total_queries": len(group),
                "discount_queries": discounts,
                "discount_rate_pct": round(discounts / len(group) * 100, 1),
                "mean_fare_eur": round(prices.mean(), 1),
                "median_fare_eur": round(prices.median(), 1),
                "min_fare_eur": round(prices.min(), 0),
                "max_fare_eur": round(prices.max(), 0),
                "spread_eur": round(prices.max() - prices.min(), 0),
            })

    return pd.DataFrame(active), pd.DataFrame(filtered)


def run_mode_filtered_analysis() -> None:
    df = load_pos_observations(ALL_POS_DIR)
    if df.empty:
        print(f"No flight records found in {ALL_POS_DIR}.")
        return

    market_count = df["gl"].nunique()
    print("=" * 90)
    print(f"LOADED {len(df)} FLIGHT OBSERVATIONS ACROSS {df['date_pair'].nunique()} DATE PAIRS & {market_count} MARKETS")
    print("=" * 90)

    modes = date_pair_modes(df)
    active_df, filtered_df = partition_markets(df, modes)
    if active_df.empty:
        print("No market ever priced below its date-pair mode; nothing to rank.")
        return

    active_df["global_min_wins"] = active_df["gl"].map(global_min_wins(df)).fillna(0).astype(int)
    active_df = active_df.sort_values(by="mean_fare_eur")

    df[df["gl"].isin(set(active_df["gl"]))].to_csv(FILTERED_CSV, index=False, encoding="utf-8")
    active_df.to_csv(ACTIVE_SUMMARY_CSV, index=False, encoding="utf-8")
    active_df.to_json(ACTIVE_SUMMARY_JSON, orient="records", indent=2, force_ascii=False)

    active_markets = [{"gl": row["gl"], "name": row["region_name"]} for _, row in active_df.iterrows()]
    ACTIVE_MARKETS_JSON.write_text(json.dumps(active_markets, indent=2, ensure_ascii=False), encoding="utf-8")

    filtered_pct = len(filtered_df) / market_count * 100
    active_pct = len(active_df) / market_count * 100

    print("\nStatistical Mode Exclusion Results:")
    print(f"- Total Sovereign Markets Scanned: {market_count}")
    print(f"- Filtered Out (Always >= Mode): {len(filtered_df)} markets ({filtered_pct:.1f}%)")
    print(f"- Remaining Active Dynamic Arbitrage Markets: {len(active_df)} markets ({active_pct:.1f}%)")
    print(f"- Active Market Codes saved to: {ACTIVE_MARKETS_JSON} ({len(active_markets)} markets)")

    by_mean = active_df.sort_values(by="mean_fare_eur").head(20)
    by_median = active_df.sort_values(by=["median_fare_eur", "mean_fare_eur"]).head(20)
    by_min = active_df.sort_values(by=["min_fare_eur", "mean_fare_eur"]).head(20)

    for title, table, columns in (
        ("TOP 20 ACTIVE MARKETS BY MEAN FARE:", by_mean, ACTIVE_COLUMNS),
        ("TOP 20 ACTIVE MARKETS BY MEDIAN FARE:", by_median, ACTIVE_COLUMNS),
        ("TOP 20 ACTIVE MARKETS BY OVERALL MINIMUM FARE:", by_min, MIN_COLUMNS),
    ):
        print("\n" + "=" * 90)
        print(title)
        print("=" * 90)
        print(tabulate(table[columns], headers="keys", tablefmt="github", showindex=False))

    mode_table = pd.DataFrame([
        {
            "Date Pair": date_pair,
            "Mode Fare (€)": f"€{info['mode_price']:.0f}",
            "Mode Frequency": f"{info['mode_frequency']}/{info['total_markets']} ({info['pct_mode']}%)",
            "Min Fare (€)": f"€{info['min_price']:.0f}",
            "Max Fare (€)": f"€{info['max_price']:.0f}",
            "Max Spread (€)": f"€{info['max_price'] - info['min_price']:.0f}",
        }
        for date_pair, info in modes.items()
    ])

    report = "\n".join([
        "# Google Flights Arbitrage: Statistical Mode Filter & Active Market Study",
        f"**Route**: {df['origin'].iloc[0]} -> {df['destination'].iloc[0]}",
        "**Methodology**: Statistical Mode-Exclusion Filter (excluding non-dynamic fallback markets)",
        f"**Total Observations**: {len(df)} queries across {df['date_pair'].nunique()} date pairs",
        "",
        "## 1. Executive Summary & Filter Mechanics",
        "- **Hypothesis**: the statistical mode across sovereign regions represents the unlocalized "
        "default tariff (GDS fallback fare), assigned where a market has no localized interline "
        "agreements or dynamic inventory.",
        f"- **Filtered Out**: **{len(filtered_df)} countries** ({filtered_pct:.1f}%) whose price was "
        "**always greater than or equal to the mode** across every date pair scanned.",
        f"- **Vetted Active Markets**: **{len(active_df)} countries** ({active_pct:.1f}%) that offered "
        "localized pricing below the generic benchmark.",
        f"- **Reusable Asset**: the vetted country list is preserved in `{ACTIVE_MARKETS_JSON.name}` "
        "for all future route studies.",
        "",
        "## 2. Date Pair Benchmark Modes",
        tabulate(mode_table, headers="keys", tablefmt="github", showindex=False),
        "",
        "## 3. Active Markets Ranked by Mean Fare (Cheapest Overall Average)",
        tabulate(by_mean[ACTIVE_COLUMNS], headers="keys", tablefmt="github", showindex=False),
        "",
        "## 4. Active Markets Ranked by Median Fare (Most Consistent Pricing)",
        tabulate(by_median[ACTIVE_COLUMNS], headers="keys", tablefmt="github", showindex=False),
        "",
        "## 5. Active Markets Ranked by Overall Minimum Fare (Absolute Peak Bargains)",
        tabulate(by_min[MIN_COLUMNS], headers="keys", tablefmt="github", showindex=False),
        "",
        "## 6. List of Filtered-Out Markets (Generic GDS Tariffs)",
        "These markets never produced a price below the mode and can be omitted from future "
        "scrapers to save bandwidth and compute:\n",
        tabulate(
            filtered_df.head(30)[["gl", "region_name", "total_queries", "mean_fare", "reason"]]
            if not filtered_df.empty
            else pd.DataFrame(columns=["gl", "region_name", "total_queries", "mean_fare", "reason"]),
            headers="keys",
            tablefmt="github",
            showindex=False,
        ),
        f"\n*(And {max(0, len(filtered_df) - 30)} more fallback markets...)*",
    ])

    REPORT_MD.write_text(report, encoding="utf-8")
    print(f"\nGenerated comprehensive report at: {REPORT_MD}")


if __name__ == "__main__":
    run_mode_filtered_analysis()
