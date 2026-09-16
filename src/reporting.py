"""Shared persistence for fare observations and their roll-up reports.

Every scanner writes the same three artifacts: one JSON checkpoint per query, a
daily CSV+JSON report, and (for the POS studies) a running summary table. The
writers live here so the schemas stay in sync across scanners.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

DAILY_REPORT_FIELDS = (
    "departure_date",
    "return_date",
    "stay_nights",
    "status",
    "lowest_observed_price_eur",
    "protection_label",
    "fetched_at",
)

SKYSCANNER_REPORT_FIELDS = (
    "departure_date",
    "return_date",
    "stay_nights",
    "status",
    "lowest_observed_price_eur",
    "itinerary_count",
    "fetched_at",
)

SUMMARY_FIELDS = (
    "origin",
    "destination",
    "departure_date",
    "return_date",
    "stay_nights",
    "gl",
    "region_name",
    "status",
    "lowest_price_eur",
    "carrier",
    "duration_minutes",
    "stops",
    "layovers",
    "fetched_at",
)


def today_stamp() -> str:
    """Local-time date stamp used to name daily reports and detect fresh cache."""
    return dt.datetime.now().astimezone().strftime("%Y-%m-%d")


def utc_now() -> str:
    """UTC timestamp recorded on every observation."""
    return dt.datetime.now(dt.timezone.utc).isoformat()


def write_json(path: Path, payload: Any) -> Path:
    """Write indented UTF-8 JSON, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_csv(path: Path, fieldnames: Sequence[str], rows: Iterable[dict[str, Any]]) -> Path:
    """Write a CSV restricted to ``fieldnames``, ignoring any extra keys."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fieldnames), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return path


def pair_filename(departure: Any, return_date: Any) -> str:
    """Checkpoint name for a single date pair, e.g. ``2026-12-09_2027-01-05.json``."""
    return f"{departure}_{return_date}.json"


def checkpoint_filename(origin: str, dest: str, dep: dt.date, ret: dt.date | None, gl: str) -> str:
    """Checkpoint name for one route/date/POS query; one-way trips use ``oneway``."""
    ret_str = ret.isoformat() if ret else "oneway"
    return f"{origin}_{dest}_{dep.isoformat()}_{ret_str}_gl-{gl}.json"


def save_observation(results_dir: Path, observation: dict[str, Any]) -> Path:
    """Persist one observation keyed by its date pair."""
    return write_json(
        results_dir / pair_filename(observation["departure_date"], observation["return_date"]),
        observation,
    )


def write_daily_report(
    results_dir: Path,
    observations: list[dict[str, Any]],
    *,
    source: str,
    stem: str,
    fieldnames: Sequence[str] = DAILY_REPORT_FIELDS,
    fallbacks: dict[str, Callable[[dict[str, Any]], Any]] | None = None,
) -> tuple[Path, Path]:
    """Write the day's JSON and CSV roll-up, sorted by date pair.

    Rewritten after every query so an interrupted run still leaves a readable
    report on disk.
    """
    results_dir.mkdir(parents=True, exist_ok=True)
    stamp = today_stamp()
    rows = sorted(observations, key=lambda item: (item["departure_date"], item["return_date"]))
    fallbacks = fallbacks or {}

    json_path = write_json(
        results_dir / f"{stem}_{stamp}.json",
        {
            "generated_at": utc_now(),
            "source": source,
            "total_pairs_scanned": len(rows),
            "observed_pairs": sum(1 for row in rows if row.get("status") == "observed"),
            "observations": rows,
        },
    )
    csv_path = write_csv(
        results_dir / f"{stem}_{stamp}.csv",
        fieldnames,
        (
            {
                field: fallbacks[field](item) if field in fallbacks else item.get(field)
                for field in fieldnames
            }
            for item in rows
        ),
    )
    return json_path, csv_path


def write_summary_reports(
    results_dir: Path, summary_data: list[dict[str, Any]], stem: str = "summary_report"
) -> tuple[Path, Path]:
    """Write the running POS summary table as JSON and CSV."""
    results_dir.mkdir(parents=True, exist_ok=True)
    json_path = write_json(results_dir / f"{stem}.json", summary_data)
    csv_path = results_dir / f"{stem}.csv"
    if summary_data:
        write_csv(csv_path, SUMMARY_FIELDS, summary_data)
    return json_path, csv_path


def summary_record(
    observation: dict[str, Any],
    *,
    origin: str,
    destination: str,
    departure: dt.date,
    return_date: dt.date | None,
    gl: str,
    region_name: str,
    status: str | None = None,
    price_eur: float | None = None,
) -> dict[str, Any]:
    """Flatten an observation into one summary-table row, using its cheapest card."""
    cards = observation.get("cards") or []
    top_card = cards[0] if cards else {}
    layovers = top_card.get("layovers")
    return {
        "origin": origin,
        "destination": destination,
        "departure_date": departure.isoformat(),
        "return_date": return_date.isoformat() if return_date else "oneway",
        "stay_nights": (return_date - departure).days if return_date else 0,
        "gl": gl,
        "region_name": region_name,
        "status": status if status is not None else observation.get("status"),
        "lowest_price_eur": price_eur if price_eur is not None else observation.get("lowest_price_eur"),
        "carrier": ", ".join(top_card.get("carriers", [])) or "Unknown",
        "duration_minutes": top_card.get("duration_minutes"),
        "stops": top_card.get("stops"),
        "layovers": "/".join(layovers) if isinstance(layovers, list) else "",
        "fetched_at": observation.get("fetched_at"),
    }


def error_observation(
    *,
    source: str,
    origin: str,
    destination: str,
    departure: dt.date,
    return_date: dt.date,
    error: Exception,
    note: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record a failed query so one bad pair never loses the rest of the matrix."""
    observation = {
        "fetched_at": utc_now(),
        "source": source,
        "origin": origin,
        "destination": destination,
        "departure_date": departure.isoformat(),
        "return_date": return_date.isoformat(),
        "stay_nights": (return_date - departure).days,
        "status": "error",
        "lowest_observed_price_eur": None,
        "protection_label": "unknown",
        "seller_confirmation_required": True,
        "candidate_cards": [],
        "error": f"{type(error).__name__}: {error}",
        "notes": [note],
    }
    observation.update(extra or {})
    return observation


def read_json(path: Path, default: Any = None) -> Any:
    """Read JSON from ``path``, returning ``default`` if it is missing or unreadable."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def load_checkpoint(path: Path) -> dict[str, Any] | None:
    """Read a saved observation, returning None if it is missing or unreadable."""
    data = read_json(path)
    return data if isinstance(data, dict) else None


def load_summary_records(results_dir: Path, stem: str) -> list[dict[str, Any]]:
    """Resume an interrupted scan from its previously written summary table."""
    data = read_json(results_dir / f"{stem}.json", default=[])
    return data if isinstance(data, list) else []
