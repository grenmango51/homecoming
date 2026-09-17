#!/usr/bin/env python3
"""Cross-platform flight fare comparison tool: Google Flights vs. Skyscanner.

Compares lowest observed fares between Google Flights and Skyscanner for identical
departure and return date pairs. Identifies price arbitrage, best deals, and calculates
savings between providers.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

from src.common import configure_stdio
from src.config import PROJECT_ROOT, load_config

ROOT = PROJECT_ROOT
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


def load_pair_jsons(
    directory: Path,
    origin: str | None = None,
    dest: str | None = None,
) -> dict[tuple[str, str], dict[str, Any]]:
    """Load individual pair JSON files from a directory, optionally filtered by route."""
    records: dict[tuple[str, str], dict[str, Any]] = {}
    if not directory.is_dir():
        return records

    for p in directory.glob("????-??-??_????-??-??.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if origin and data.get("origin") and data.get("origin").upper() != origin.upper():
                continue
            if dest and data.get("destination") and data.get("destination").upper() != dest.upper():
                continue
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
    google_se_data: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Merge and compare records from Google Flights (FI), optional Google Flights (SE), and Skyscanner."""
    has_se = bool(google_se_data)
    all_keys = set(google_data.keys()) | set(skyscanner_data.keys())
    if google_se_data:
        all_keys |= set(google_se_data.keys())
    sorted_keys = sorted(all_keys)
    comparisons: list[dict[str, Any]] = []

    for dep, ret in sorted_keys:
        g = google_data.get((dep, ret), {})
        s = skyscanner_data.get((dep, ret), {})
        g_se = (google_se_data or {}).get((dep, ret), {})

        g_price = g.get("price_eur")
        s_price = s.get("price_eur")
        g_se_price = g_se.get("price_eur") if has_se else None
        stay = g.get("stay_nights") or s.get("stay_nights") or g_se.get("stay_nights") or 0

        cheaper_source: str | None = None
        price_diff: float | None = None
        savings_eur: float | None = None
        savings_pct: float | None = None
        cheapest_price: float | None = None

        if has_se:
            candidates: list[tuple[str, float]] = []
            if g_price is not None:
                candidates.append(("Google Flights (FI)", g_price))
            if g_se_price is not None:
                candidates.append(("Google Flights (SE)", g_se_price))
            if s_price is not None:
                candidates.append(("Skyscanner", s_price))

            if g_price is not None and s_price is not None:
                price_diff = round(g_price - s_price, 2)

            if len(candidates) >= 2:
                candidates.sort(key=lambda x: x[1])
                cheapest_price = candidates[0][1]
                min_candidates = [c for c in candidates if c[1] == cheapest_price]
                if len(min_candidates) > 1:
                    cheaper_source = "Tie"
                    savings_eur = 0.0
                    savings_pct = 0.0
                else:
                    cheaper_source = min_candidates[0][0]
                    runner_up_price = candidates[1][1]
                    savings_eur = round(runner_up_price - cheapest_price, 2)
                    savings_pct = round((savings_eur / runner_up_price) * 100, 1) if runner_up_price > 0 else 0.0
            elif len(candidates) == 1:
                cheaper_source = f"{candidates[0][0]} (Only)"
                cheapest_price = candidates[0][1]
        else:
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
                "google_se_price_eur": g_se_price,
                "skyscanner_price_eur": s_price,
                "cheapest_price_eur": cheapest_price,
                "winner": cheaper_source,
                "skyscanner_savings_eur": price_diff,
                "savings_eur": savings_eur,
                "savings_pct": savings_pct,
                "google_status": g.get("status", "not_scanned"),
                "google_se_status": g_se.get("status", "not_scanned") if has_se else None,
                "skyscanner_status": s.get("status", "not_scanned"),
            }
        )
    return comparisons


def render_table(comparisons: list[dict[str, Any]]) -> str:
    """Format comparisons into an aligned terminal table."""
    has_se = any(row.get("google_se_price_eur") is not None for row in comparisons)
    if has_se:
        headers = [
            "Departure",
            "Return",
            "Nights",
            "Google FI (€)",
            "Google SE (€)",
            "Skyscanner (€)",
            "Cheapest (€)",
            "Winner",
            "Diff (€)",
        ]
        widths = [10, 10, 6, 13, 13, 15, 13, 20, 10]
    else:
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
    header_line = "| " + " | ".join(h.ljust(w) for h, w in zip(headers, widths, strict=True)) + " |"

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
        if has_se:
            g_se_str = f"€{row['google_se_price_eur']:.0f}" if row.get("google_se_price_eur") is not None else "-"
            if winner.startswith("Google Flights (SE)") and row.get("savings_eur"):
                diff_str = f"-€{row['savings_eur']:.0f} (SE)"
            elif winner.startswith("Google Flights (FI)") and row.get("savings_eur"):
                diff_str = f"-€{row['savings_eur']:.0f} (FI)"
            elif winner == "Skyscanner" and row.get("savings_eur"):
                diff_str = f"-€{row['savings_eur']:.0f} (SS)"
            elif winner == "Tie":
                diff_str = "€0"
            elif diff is not None:
                diff_str = f"-€{diff:.0f} (SS)" if diff > 0 else (f"+€{-diff:.0f} (GF)" if diff < 0 else "€0")
            else:
                diff_str = "-"

            row_str = "| " + " | ".join([
                dep.ljust(widths[0]),
                ret.ljust(widths[1]),
                nights.rjust(widths[2]),
                g_str.rjust(widths[3]),
                g_se_str.rjust(widths[4]),
                s_str.rjust(widths[5]),
                c_str.rjust(widths[6]),
                winner.ljust(widths[7]),
                diff_str.rjust(widths[8]),
            ]) + " |"
        else:
            if diff is not None:
                if diff > 0:
                    diff_str = f"-€{diff:.0f} (SS)"
                elif diff < 0:
                    diff_str = f"+€{-diff:.0f} (GF)"
                else:
                    diff_str = "€0"
            else:
                diff_str = "-"

            row_str = "| " + " | ".join([
                dep.ljust(widths[0]),
                ret.ljust(widths[1]),
                nights.rjust(widths[2]),
                g_str.rjust(widths[3]),
                s_str.rjust(widths[4]),
                c_str.rjust(widths[5]),
                winner.ljust(widths[6]),
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

    has_se = any(c.get("google_se_price_eur") is not None for c in comparisons)

    matched = [c for c in comparisons if c["google_price_eur"] is not None and c["skyscanner_price_eur"] is not None]
    skyscanner_wins = [c for c in comparisons if c["winner"] == "Skyscanner"]
    google_fi_wins = [c for c in comparisons if c["winner"] in ("Google Flights", "Google Flights (FI)")]
    google_se_wins = [c for c in comparisons if c["winner"] == "Google Flights (SE)"]
    ties = [c for c in comparisons if c["winner"] == "Tie"]

    max_skyscanner_saving = max((c["savings_eur"] for c in skyscanner_wins), default=0.0)
    max_google_fi_saving = max((c["savings_eur"] for c in google_fi_wins), default=0.0)
    max_google_se_saving = max((c["savings_eur"] for c in google_se_wins), default=0.0)

    all_prices = [c["cheapest_price_eur"] for c in comparisons if c["cheapest_price_eur"] is not None]
    overall_lowest = min(all_prices) if all_prices else None
    overall_cheapest_pairs = [c for c in comparisons if c["cheapest_price_eur"] == overall_lowest]

    summary = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "total_pairs": len(comparisons),
        "both_providers_observed": len(matched),
        "skyscanner_cheaper_count": len(skyscanner_wins),
        "google_cheaper_count": len(google_fi_wins),
        "google_se_cheaper_count": len(google_se_wins),
        "tie_count": len(ties),
        "max_skyscanner_saving_eur": max_skyscanner_saving,
        "max_google_saving_eur": max_google_fi_saving,
        "max_google_se_saving_eur": max_google_se_saving,
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
        "comparisons": comparisons,
    }

    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    if has_se:
        fieldnames = (
            "departure_date",
            "return_date",
            "stay_nights",
            "google_price_eur",
            "google_se_price_eur",
            "skyscanner_price_eur",
            "cheapest_price_eur",
            "winner",
            "skyscanner_savings_eur",
            "savings_eur",
            "savings_pct",
            "google_status",
            "google_se_status",
            "skyscanner_status",
        )
    else:
        fieldnames = (
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
        )

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(comparisons)

    return csv_path, json_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    pre_p = argparse.ArgumentParser(add_help=False)
    pre_p.add_argument("--config", help="Optional path to config.toml")
    pre_p.add_argument("--trip", help="Optional path or name of trip config file")
    pre_args, _ = pre_p.parse_known_args(argv)

    cfg = load_config(pre_args.config, pre_args.trip)
    parser = argparse.ArgumentParser(description="Compare Google Flights and Skyscanner fares.")
    parser.add_argument("--config", default=pre_args.config, help="Optional path to config.toml")
    parser.add_argument("--trip", default=pre_args.trip, help="Optional path or name of trip config file")
    parser.add_argument("--google-dir", default=str(cfg.google_flights.resolved_results_dir()), help="Path to Google Flights results")
    parser.add_argument("--google-se-dir", default=str(cfg.google_flights.resolved_results_dir_for_gl("SE")), help="Path to Google Flights SE results")
    parser.add_argument("--skyscanner-dir", default=str(cfg.skyscanner.resolved_results_dir()), help="Path to Skyscanner results")
    parser.add_argument("--google-report", help="Path to specific Google Flights daily CSV report")
    parser.add_argument("--google-se-report", help="Path to specific Google Flights SE daily CSV report")
    parser.add_argument("--skyscanner-report", help="Path to specific Skyscanner daily CSV report")
    parser.add_argument("--origin", default=cfg.trip.origin, help="Filter by origin airport code")
    parser.add_argument("--dest", default=cfg.trip.dest, help="Filter by destination airport code")
    parser.add_argument("--output-dir", default=str(cfg.comparison.resolved_output_dir()), help="Output directory for comparison reports")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    configure_stdio()

    args = parse_args(argv)
    google_dir = Path(args.google_dir).resolve()
    skyscanner_dir = Path(args.skyscanner_dir).resolve()
    output_dir = Path(args.output_dir).resolve()

    google_records = load_pair_jsons(google_dir, origin=args.origin, dest=args.dest)
    if args.google_report:
        google_csv = Path(args.google_report)
        if google_csv.is_file():
            print(f"Reading Google Flights CSV report: {google_csv.name}")
            csv_records = load_daily_csv(google_csv)
            for k, v in csv_records.items():
                if k not in google_records or google_records[k]["price_eur"] is None:
                    google_records[k] = v

    google_se_dir = Path(args.google_se_dir).resolve() if args.google_se_dir else None
    google_se_records: dict[tuple[str, str], dict[str, Any]] = {}
    if google_se_dir and google_se_dir.is_dir():
        google_se_records = load_pair_jsons(google_se_dir, origin=args.origin, dest=args.dest)
        if args.google_se_report:
            se_csv = Path(args.google_se_report)
            if se_csv.is_file():
                print(f"Reading Google Flights SE CSV report: {se_csv.name}")
                csv_records = load_daily_csv(se_csv)
                for k, v in csv_records.items():
                    if k not in google_se_records or google_se_records[k]["price_eur"] is None:
                        google_se_records[k] = v
        else:
            latest_se = find_latest_report(google_se_dir, "daily_fare_report")
            if latest_se and latest_se.is_file():
                csv_records = load_daily_csv(latest_se)
                for k, v in csv_records.items():
                    if k not in google_se_records or google_se_records[k]["price_eur"] is None:
                        google_se_records[k] = v

    skyscanner_records = load_pair_jsons(skyscanner_dir, origin=args.origin, dest=args.dest)
    if args.skyscanner_report:
        skyscanner_csv = Path(args.skyscanner_report)
        if skyscanner_csv.is_file():
            print(f"Reading Skyscanner CSV report: {skyscanner_csv.name}")
            csv_records = load_daily_csv(skyscanner_csv)
            for k, v in csv_records.items():
                if k not in skyscanner_records or skyscanner_records[k]["price_eur"] is None:
                    skyscanner_records[k] = v

    if google_se_records:
        print(f"Loaded {len(google_records)} Google Flights (FI) records, {len(google_se_records)} Google Flights (SE) records, {len(skyscanner_records)} Skyscanner records.")
    else:
        print(f"Loaded {len(google_records)} Google Flights records, {len(skyscanner_records)} Skyscanner records.")

    comparisons = compare_records(
        google_records,
        skyscanner_records,
        google_se_data=google_se_records if google_se_records else None,
    )
    if not comparisons:
        print("No flight records found to compare.", file=sys.stderr)
        sys.exit(1)

    print("\n" + render_table(comparisons))

    csv_out, json_out = write_comparison_reports(output_dir, comparisons)
    print("\nSaved comparison reports:")
    print(f"  CSV:  {csv_out.name}")
    print(f"  JSON: {json_out.name}")

    valid_fares = [c for c in comparisons if c["cheapest_price_eur"] is not None]
    if valid_fares:
        best = min(valid_fares, key=lambda c: c["cheapest_price_eur"])
        print("\n" + "=" * 60)
        print(f"BEST FARE FOUND ({best['departure_date']} -> {best['return_date']}, {best['stay_nights']} nights):")
        print(f"  Cheapest Platform: {best['winner']}")
        print(f"  Lowest Price:      €{best['cheapest_price_eur']:.0f}")
        if best.get("savings_eur"):
            print(f"  Savings:           €{best['savings_eur']:.0f} ({best['savings_pct']}%) vs alternative")
        print("=" * 60)


if __name__ == "__main__":
    main()
