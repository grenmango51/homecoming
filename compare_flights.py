#!/usr/bin/env python3
"""Cross-platform flight fare comparison tool: Google Flights vs. Skyscanner.

Compares lowest observed fares between Google Flights and Skyscanner for identical
departure and return date pairs. Identifies price arbitrage, best deals, and calculates
savings between providers.

Outputs:
- Rich formatted terminal comparison table
- CSV comparison report: flight_results/flight_comparison_report_YYYY-MM-DD.csv
- JSON comparison report: flight_results/flight_comparison_report_YYYY-MM-DD.json
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DEFAULT_GOOGLE_DIR = ROOT / "flight_results"
DEFAULT_SKYSCANNER_DIR = ROOT / "flight_results_skyscanner"
DEFAULT_OUTPUT_DIR = ROOT / "flight_results"


def load_daily_csv(report_path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    """Load records from a daily fare report CSV keyed by (departure_date, return_date)."""
    records: dict[tuple[str, str], dict[str, Any]] = {}
    if not report_path.is_file():
        return records

    with report_path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            dep = row.get("departure_date", "").strip()
            ret = row.get("return_date", "").strip()
            if not dep or not ret:
                continue
            raw_price = row.get("lowest_observed_price_eur", "")
            try:
                price = float(raw_price) if raw_price else None
            except ValueError:
                price = None
            try:
                stay = int(row.get("stay_nights", 0))
            except ValueError:
                stay = 0
            records[(dep, ret)] = {
                "departure_date": dep,
                "return_date": ret,
                "stay_nights": stay,
                "status": row.get("status", "unknown"),
                "price_eur": price,
                "fetched_at": row.get("fetched_at"),
            }
    return records


def load_pair_jsons(directory: Path) -> dict[tuple[str, str], dict[str, Any]]:
    """Load individual pair JSON files (e.g. YYYY-MM-DD_YYYY-MM-DD.json) from a directory."""
    records: dict[tuple[str, str], dict[str, Any]] = {}
    if not directory.is_dir():
        return records

    for p in directory.glob("????-??-??_????-??-??.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            dep = data.get("departure_date")
            ret = data.get("return_date")
            if dep and ret:
                records[(dep, ret)] = {
                    "departure_date": dep,
                    "return_date": ret,
                    "stay_nights": data.get("stay_nights", 0),
                    "status": data.get("status", "unknown"),
                    "price_eur": data.get("lowest_observed_price_eur"),
                    "fetched_at": data.get("fetched_at"),
                    "candidate_cards": data.get("candidate_cards", []),
                }
        except Exception:
            continue
    return records


def find_latest_report(directory: Path, prefix: str) -> Path | None:
    """Find the most recent daily report CSV by filename timestamp."""
    if not directory.is_dir():
        return None
    candidates = sorted(directory.glob(f"{prefix}_*.csv"), reverse=True)
    return candidates[0] if candidates else None


def compare_records(
    google_data: dict[tuple[str, str], dict[str, Any]],
    skyscanner_data: dict[tuple[str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge and compare records from both sources."""
    all_keys = sorted(set(google_data.keys()) | set(skyscanner_data.keys()))
    comparisons: list[dict[str, Any]] = []

    for dep, ret in all_keys:
        g = google_data.get((dep, ret), {})
        s = skyscanner_data.get((dep, ret), {})

        g_price = g.get("price_eur")
        s_price = s.get("price_eur")
        stay = g.get("stay_nights") or s.get("stay_nights") or 0

        cheaper_source: str | None = None
        price_diff: float | None = None
        savings_eur: float | None = None
        savings_pct: float | None = None
        cheapest_price: float | None = None

        if g_price is not None and s_price is not None:
            price_diff = round(g_price - s_price, 2)  # positive = Skyscanner is cheaper
            if g_price < s_price:
                cheaper_source = "Google Flights"
                cheapest_price = g_price
                savings_eur = round(s_price - g_price, 2)
                savings_pct = round((savings_eur / s_price) * 100, 1) if s_price > 0 else 0.0
            elif s_price < g_price:
                cheaper_source = "Skyscanner"
                cheapest_price = s_price
                savings_eur = round(g_price - s_price, 2)
                savings_pct = round((savings_eur / g_price) * 100, 1) if g_price > 0 else 0.0
            else:
                cheaper_source = "Tie"
                cheapest_price = g_price
                savings_eur = 0.0
                savings_pct = 0.0
        elif g_price is not None:
            cheaper_source = "Google Flights (Only)"
            cheapest_price = g_price
        elif s_price is not None:
            cheaper_source = "Skyscanner (Only)"
            cheapest_price = s_price

        comparisons.append(
            {
                "departure_date": dep,
                "return_date": ret,
                "stay_nights": stay,
                "google_price_eur": g_price,
                "skyscanner_price_eur": s_price,
                "cheapest_price_eur": cheapest_price,
                "winner": cheaper_source,
                "skyscanner_savings_eur": price_diff,  # > 0 means Skyscanner is cheaper
                "savings_eur": savings_eur,
                "savings_pct": savings_pct,
                "google_status": g.get("status", "not_scanned"),
                "skyscanner_status": s.get("status", "not_scanned"),
            }
        )
    return comparisons


def render_table(comparisons: list[dict[str, Any]]) -> str:
    """Format comparisons into an aligned terminal table."""
    headers = [
        "Departure",
        "Return",
        "Nights",
        "Google (€)",
        "Skyscanner (€)",
        "Cheapest (€)",
        "Winner",
        "Diff (€)",
    ]
    widths = [10, 10, 6, 11, 15, 13, 16, 9]

    sep_line = "+-" + "-+-".join("-" * w for w in widths) + "-+"
    header_line = "| " + " | ".join(h.ljust(w) for h, w in zip(headers, widths)) + " |"

    lines = [sep_line, header_line, sep_line]

    for row in comparisons:
        dep = row["departure_date"]
        ret = row["return_date"]
        nights = str(row["stay_nights"])
        g_str = f"€{row['google_price_eur']:.0f}" if row["google_price_eur"] is not None else "-"
        s_str = f"€{row['skyscanner_price_eur']:.0f}" if row["skyscanner_price_eur"] is not None else "-"
        c_str = f"€{row['cheapest_price_eur']:.0f}" if row["cheapest_price_eur"] is not None else "-"
        winner = row["winner"] or "-"
        
        diff = row["skyscanner_savings_eur"]
        if diff is not None:
            if diff > 0:
                diff_str = f"-€{diff:.0f} (SS)"
            elif diff < 0:
                diff_str = f"+€{-diff:.0f} (GF)"
            else:
                diff_str = "€0"
        else:
            diff_str = "-"

        # Highlight reference pair
        is_ref = (dep == "2026-12-09" and ret == "2027-01-09")
        marker = " *" if is_ref else "  "

        row_str = "| " + " | ".join([
            dep.ljust(widths[0]),
            ret.ljust(widths[1]),
            nights.rjust(widths[2]),
            g_str.rjust(widths[3]),
            s_str.rjust(widths[4]),
            c_str.rjust(widths[5]),
            (winner + marker).ljust(widths[6]),
            diff_str.rjust(widths[7]),
        ]) + " |"
        lines.append(row_str)

    lines.append(sep_line)
    return "\n".join(lines)


def write_comparison_reports(
    output_dir: Path,
    comparisons: list[dict[str, Any]],
    stamp: str | None = None,
) -> tuple[Path, Path]:
    """Write comparison CSV and JSON artifacts."""
    output_dir.mkdir(parents=True, exist_ok=True)
    if stamp is None:
        stamp = dt.datetime.now().astimezone().strftime("%Y-%m-%d")

    csv_path = output_dir / f"flight_comparison_report_{stamp}.csv"
    json_path = output_dir / f"flight_comparison_report_{stamp}.json"

    # Compute summary metrics
    matched = [c for c in comparisons if c["google_price_eur"] is not None and c["skyscanner_price_eur"] is not None]
    skyscanner_wins = [c for c in matched if c["winner"] == "Skyscanner"]
    google_wins = [c for c in matched if c["winner"] == "Google Flights"]
    ties = [c for c in matched if c["winner"] == "Tie"]

    max_skyscanner_saving = max((c["savings_eur"] for c in skyscanner_wins), default=0.0)
    max_google_saving = max((c["savings_eur"] for c in google_wins), default=0.0)

    # Absolute lowest overall flight across both platforms
    all_prices = [c["cheapest_price_eur"] for c in comparisons if c["cheapest_price_eur"] is not None]
    overall_lowest = min(all_prices) if all_prices else None
    overall_cheapest_pairs = [c for c in comparisons if c["cheapest_price_eur"] == overall_lowest]

    summary = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "total_pairs": len(comparisons),
        "both_providers_observed": len(matched),
        "skyscanner_cheaper_count": len(skyscanner_wins),
        "google_cheaper_count": len(google_wins),
        "tie_count": len(ties),
        "max_skyscanner_saving_eur": max_skyscanner_saving,
        "max_google_saving_eur": max_google_saving,
        "overall_lowest_fare_eur": overall_lowest,
        "overall_lowest_fare_pairs": [
            {
                "departure_date": c["departure_date"],
                "return_date": c["return_date"],
                "winner": c["winner"],
                "price_eur": c["cheapest_price_eur"],
            }
            for c in overall_cheapest_pairs
        ],
        "reference_pair_2026_12_09_2027_01_09": next(
            (c for c in comparisons if c["departure_date"] == "2026-12-09" and c["return_date"] == "2027-01-09"),
            None,
        ),
        "comparisons": comparisons,
    }

    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=(
                "departure_date",
                "return_date",
                "stay_nights",
                "google_price_eur",
                "skyscanner_price_eur",
                "cheapest_price_eur",
                "winner",
                "skyscanner_savings_eur",
                "savings_eur",
                "savings_pct",
                "google_status",
                "skyscanner_status",
            ),
        )
        writer.writeheader()
        writer.writerows(comparisons)

    return csv_path, json_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare Google Flights and Skyscanner fares.")
    parser.add_argument("--google-dir", default=str(DEFAULT_GOOGLE_DIR), help="Path to Google Flights results directory")
    parser.add_argument("--skyscanner-dir", default=str(DEFAULT_SKYSCANNER_DIR), help="Path to Skyscanner results directory")
    parser.add_argument("--google-report", help="Path to specific Google Flights daily CSV report")
    parser.add_argument("--skyscanner-report", help="Path to specific Skyscanner daily CSV report")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Output directory for comparison reports")
    return parser.parse_args()


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace", line_buffering=True)

    args = parse_args()
    google_dir = Path(args.google_dir).resolve()
    skyscanner_dir = Path(args.skyscanner_dir).resolve()
    output_dir = Path(args.output_dir).resolve()

    # Load Google Flights data (first individual JSONs, fallback/overlay daily CSV)
    google_records = load_pair_jsons(google_dir)
    google_csv = Path(args.google_report) if args.google_report else find_latest_report(google_dir, "daily_fare_report")
    if google_csv and google_csv.is_file():
        print(f"Reading Google Flights CSV report: {google_csv.name}")
        csv_records = load_daily_csv(google_csv)
        for k, v in csv_records.items():
            if k not in google_records or google_records[k]["price_eur"] is None:
                google_records[k] = v

    # Load Skyscanner data (first individual JSONs, fallback/overlay daily CSV)
    skyscanner_records = load_pair_jsons(skyscanner_dir)
    skyscanner_csv = Path(args.skyscanner_report) if args.skyscanner_report else find_latest_report(skyscanner_dir, "daily_fare_report_skyscanner")
    if skyscanner_csv and skyscanner_csv.is_file():
        print(f"Reading Skyscanner CSV report: {skyscanner_csv.name}")
        csv_records = load_daily_csv(skyscanner_csv)
        for k, v in csv_records.items():
            if k not in skyscanner_records or skyscanner_records[k]["price_eur"] is None:
                skyscanner_records[k] = v

    print(f"Loaded {len(google_records)} Google Flights records, {len(skyscanner_records)} Skyscanner records.")

    comparisons = compare_records(google_records, skyscanner_records)
    if not comparisons:
        print("No flight records found to compare.", file=sys.stderr)
        sys.exit(1)

    print("\n" + render_table(comparisons))

    csv_out, json_out = write_comparison_reports(output_dir, comparisons)
    print(f"\nSaved comparison reports:")
    print(f"  CSV:  {csv_out.name}")
    print(f"  JSON: {json_out.name}")

    # Summary highlights
    ref = next((c for c in comparisons if c["departure_date"] == "2026-12-09" and c["return_date"] == "2027-01-09"), None)
    if ref:
        print("\n" + "=" * 60)
        print("REFERENCE PAIR HIGHLIGHT (2026-12-09 -> 2027-01-09):")
        print(f"  Google Flights: €{ref['google_price_eur'] if ref['google_price_eur'] is not None else 'N/A'}")
        print(f"  Skyscanner:     €{ref['skyscanner_price_eur'] if ref['skyscanner_price_eur'] is not None else 'N/A'}")
        if ref['google_price_eur'] and ref['skyscanner_price_eur']:
            if ref['winner'] == "Skyscanner":
                print(f"  WINNER: Skyscanner by €{ref['savings_eur']:.0f} ({ref['savings_pct']}%) cheaper!")
            elif ref['winner'] == "Google Flights":
                print(f"  WINNER: Google Flights by €{ref['savings_eur']:.0f} ({ref['savings_pct']}%) cheaper!")
            else:
                print("  WINNER: Tie!")
        print("=" * 60)


if __name__ == "__main__":
    main()

