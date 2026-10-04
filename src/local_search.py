"""Browser-free contracts for bounded searches initiated by the localhost UI."""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path


def validate_search(raw: dict, today: dt.date | None = None) -> dict:
    """Accept only route/date parameters; callers cannot select programs or paths."""
    today = today or dt.date.today()
    if not isinstance(raw, dict):
        raise ValueError("Search must be a JSON object.")
    origin, destination = raw.get("origin"), raw.get("destination")
    if not all(isinstance(code, str) and re.fullmatch(r"[A-Z]{3}", code) for code in (origin, destination)):
        raise ValueError("Enter three-letter airport codes.")
    if origin == destination:
        raise ValueError("Choose different airports.")
    trip_type = raw.get("trip_type")
    if trip_type not in {"roundtrip", "oneway"}:
        raise ValueError("Choose round trip or one way.")
    radius, minimum = raw.get("flex_days"), raw.get("min_stay_nights")
    if type(radius) is not int or radius not in {0, 1, 3}:
        raise ValueError("Flexible dates must be 0, 1 or 3 days.")
    if type(minimum) is not int or not 1 <= minimum <= 365:
        raise ValueError("Minimum stay must be between 1 and 365 nights.")
    try:
        dep = dt.date.fromisoformat(raw["departure"])
        ret = dt.date.fromisoformat(raw["return_date"]) if trip_type == "roundtrip" else None
    except (KeyError, ValueError, TypeError):
        raise ValueError("Choose valid travel dates.") from None
    if dep < today or dep > today + dt.timedelta(days=365):
        raise ValueError("Departure must be within the next year.")
    if ret and (ret <= dep or (ret - dep).days < minimum or ret > today + dt.timedelta(days=365)):
        raise ValueError("Return must meet your minimum stay and be within the next year.")
    pairs = []
    for offset in range(-radius, radius + 1):
        departure = dep + dt.timedelta(days=offset)
        if departure < today or departure > today + dt.timedelta(days=365):
            continue
        if ret is None:
            pairs.append([departure.isoformat(), None])
            continue
        for return_offset in range(-radius, radius + 1):
            returning = ret + dt.timedelta(days=return_offset)
            if (returning - departure).days >= minimum and returning <= today + dt.timedelta(days=365):
                pairs.append([departure.isoformat(), returning.isoformat()])
    return {
        "origin": origin, "destination": destination, "departure": dep.isoformat(),
        "return_date": ret.isoformat() if ret else None, "trip_type": trip_type,
        "flex_days": radius, "min_stay_nights": minimum, "pairs": pairs,
    }


def job_config(query: dict, job_dir: Path, profile_dir: Path, *, budget: int = 1800) -> str:
    """Generate a private config independent of the daily scan configuration."""
    # JSON string escaping is compatible with TOML basic strings, including Windows paths.
    quote = json.dumps
    trip = [
        "[trip]", f"origin = {quote(query['origin'])}", f"dest = {quote(query['destination'])}",
        f"trip_type = {quote('one-way' if query['trip_type'] == 'oneway' else 'round-trip')}",
        'date_mode = "exact"', f"min_stay_nights = {query['min_stay_nights']}",
    ]
    if query["trip_type"] == "oneway":
        trip += [f"depart_from = {quote(query['pairs'][0][0])}", f"depart_to = {quote(query['pairs'][-1][0])}"]
    else:
        trip.append(f"exact_pairs = {quote(query['pairs'])}")
    sections = {
        "execution": {"strategy": "parallel", "skip_existing": False,
                      "auto_compare": query["trip_type"] == "roundtrip", "runtime_budget_seconds": budget},
        "google_flights": {"enabled": True, "gl": ["FI"], "profile_mode": "ephemeral",
                           "profile_dir": str(profile_dir / "google"), "results_dir": str(job_dir / "google"),
                           "delay_seconds": 3, "timeout_seconds": 30},
        "skyscanner": {"enabled": True, "profile_dir": str(profile_dir / "skyscanner"),
                       "results_dir": str(job_dir / "skyscanner"), "delay_seconds": 2,
                       "timeout_seconds": 45, "poll_wait_seconds": 30,
                       "attended": True, "challenge_timeout_seconds": 90},
        "comparison": {"output_dir": str(job_dir / "comparison")},
    }
    lines = trip
    for name, values in sections.items():
        lines.extend(["", f"[{name}]"])
        lines.extend(f"{key} = {quote(value)}" for key, value in values.items())
    return "\n".join(lines) + "\n"
