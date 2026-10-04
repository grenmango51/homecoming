"""Allowlisted, browser-free export of completion-verified public fare data."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path

from .common import is_valid_completion_cache
from .google_parse import GOOGLE_COMPLETION_CHECK, build_route_url
from .reporting import utc_now, write_json
from .skyscanner_parse import flight_search_url


def public_fare(row: dict, provider: str) -> dict | None:
    """Omit cookies, raw payloads, local paths and unverified/other-market prices."""
    try:
        origin, dest = row['origin'], row['destination']
        if not all(isinstance(v, str) and re.fullmatch(r'[A-Z]{3}', v) for v in (origin, dest)):
            return None
        dep = dt.date.fromisoformat(row['departure_date'])
        ret = dt.date.fromisoformat(row['return_date']) if row.get('return_date') else None
        fetched = dt.datetime.fromisoformat(row['fetched_at'])
        if fetched.tzinfo is None or (ret and ret <= dep):
            return None
        if row.get('gl', 'FI') != 'FI' or row.get('passengers', 1) != 1:
            return None
        if row.get('cabin', 'economy') != 'economy' or row.get('currency', 'EUR') != 'EUR':
            return None
        if not is_valid_completion_cache(
            row, stamp=row['fetched_at'][:10], origin=origin, dest=dest,
            departure_date=dep, return_date=ret,
        ):
            return None
        if provider == 'google_flights':
            if row.get('google_completion_check') != GOOGLE_COMPLETION_CHECK:
                return None
            url = build_route_url(origin, dest, dep, ret)
        elif provider == 'skyscanner':
            url = flight_search_url(origin, dest, dep, ret)
        else:
            return None
        return {
            'origin': origin, 'destination': dest, 'departure': dep.isoformat(),
            'return_date': ret.isoformat() if ret else None, 'provider': provider,
            'price_eur': row['lowest_observed_price_eur'],
            'observed_at': fetched.isoformat(), 'status': 'complete', 'search_url': url,
            'market': 'FI', 'passengers': 1, 'cabin': 'economy',
        }
    except (KeyError, TypeError, ValueError):
        return None


def export_fares(sources: list[tuple[Path, str]]) -> dict:
    latest = {}
    for directory, provider in sources:
        for path in directory.glob('20??-??-??_*.json'):
            try:
                row = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            if not isinstance(row, dict):
                continue
            fare = public_fare(row, provider)
            if fare is None:
                continue
            key = (fare['origin'], fare['destination'], fare['departure'], fare['return_date'], provider)
            if key not in latest or fare['observed_at'] > latest[key]['observed_at']:
                latest[key] = fare
    return {'generated_at': utc_now(), 'mode': 'archive', 'fares': list(latest.values())}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('web/data/fares.json'))
    args = parser.parse_args()
    payload = export_fares([
        (Path('flight_results'), 'google_flights'),
        (Path('flight_results_skyscanner'), 'skyscanner'),
    ])
    write_json(args.output, payload)
    print(f"Exported {len(payload['fares'])} verified fares to {args.output}")


if __name__ == '__main__':
    main()
