"""Configuration loader and schema for Flight Finder.

General execution settings live in ``config/config.toml``; route and date
settings live in a per-trip file under ``config/`` (e.g. ``HEL_HAN.toml``).
"""

from __future__ import annotations

import datetime as dt
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.common import build_search_pairs

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
DEFAULT_CONFIG_PATH = CONFIG_DIR / "config.toml"
DEFAULT_TRIP_FILE = "HEL_HAN.toml"

ONE_WAY_TRIP_TYPES = {"one-way", "oneway"}


@dataclass
class TripConfig:
    origin: str = "HEL"
    dest: str = "HAN"
    min_stay_nights: int = 21
    trip_type: str = "round-trip"  # "round-trip" or "one-way"
    date_mode: str = "range"  # "range", "window", or "exact"
    depart_from: str | None = "2026-12-09"
    depart_to: str | None = "2026-12-13"
    return_from: str | None = "2027-01-05"
    return_to: str | None = "2027-01-09"
    window_start: str | None = "2026-12-09"
    window_end: str | None = "2027-01-09"
    exact_pairs: list[list[str]] = field(default_factory=list)

    def get_search_pairs(self) -> list[tuple[dt.date, dt.date | None]]:
        """Date pairs to scan. One-way trips return ``(departure, None)`` pairs."""
        if self.trip_type in ONE_WAY_TRIP_TYPES:
            if not (self.depart_from and self.depart_to):
                return []
            first = dt.date.fromisoformat(self.depart_from)
            last = dt.date.fromisoformat(self.depart_to)
            return [(first + dt.timedelta(days=offset), None) for offset in range((last - first).days + 1)]

        if self.date_mode == "exact" and self.exact_pairs:
            return build_search_pairs(exact_pairs=self.exact_pairs, min_stay_nights=self.min_stay_nights)

        if self.date_mode == "window":
            return build_search_pairs(
                window_start=self.window_start,
                window_end=self.window_end,
                min_stay_nights=self.min_stay_nights,
            )

        return build_search_pairs(
            depart_from=self.depart_from,
            depart_to=self.depart_to,
            return_from=self.return_from,
            return_to=self.return_to,
            min_stay_nights=self.min_stay_nights,
        )


@dataclass
class ExecutionConfig:
    strategy: str = "parallel"  # "parallel" or "sequential"
    skip_existing: bool = True
    auto_compare: bool = True
    browser_executable: str | None = None


@dataclass
class ScraperConfig:
    """Settings common to both scrapers."""

    enabled: bool = True
    delay_seconds: int = 3
    timeout_seconds: int = 30
    profile_dir: str = ".browser-profile"
    results_dir: str = "flight_results"

    def resolved_profile_dir(self, root: Path = PROJECT_ROOT) -> Path:
        return (root / self.profile_dir).resolve()

    def resolved_results_dir(self, root: Path = PROJECT_ROOT) -> Path:
        return (root / self.results_dir).resolve()


@dataclass
class GoogleFlightsConfig(ScraperConfig):
    gl: str = "FI"  # Point of sale country code


@dataclass
class SkyscannerConfig(ScraperConfig):
    delay_seconds: int = 4
    timeout_seconds: int = 45
    poll_wait_seconds: int = 30
    challenge_timeout_seconds: int = 90
    profile_dir: str = ".skyscanner-profile"
    results_dir: str = "flight_results_skyscanner"


@dataclass
class ComparisonConfig:
    output_dir: str = "flight_results"

    def resolved_output_dir(self, root: Path = PROJECT_ROOT) -> Path:
        return (root / self.output_dir).resolve()


@dataclass
class AppConfig:
    trip: TripConfig = field(default_factory=TripConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    google_flights: GoogleFlightsConfig = field(default_factory=GoogleFlightsConfig)
    skyscanner: SkyscannerConfig = field(default_factory=SkyscannerConfig)
    comparison: ComparisonConfig = field(default_factory=ComparisonConfig)
    trip_file: str | None = None


def resolve_trip_file(raw_path: str | Path, base_dir: Path) -> Path | None:
    """Find a trip file given a bare name, or a path relative to the config dir or project root."""
    candidate = Path(raw_path)
    for path in (candidate, base_dir / candidate, CONFIG_DIR / candidate, PROJECT_ROOT / candidate):
        if path.is_file():
            return path.resolve()
    return None


def _read_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _load_trip_table(path: Path) -> dict[str, Any]:
    """Read a trip file, accepting either a ``[trip]`` table or a bare top-level table."""
    data = _read_toml(path)
    return data.get("trip", data)


def _resolve_trip(
    data: dict[str, Any], config_path: Path, trip_path: Path | str | None
) -> tuple[dict[str, Any], str | None]:
    """Resolve trip settings in precedence order.

    1. ``trip_path`` passed explicitly
    2. ``trip_file`` named in config.toml
    3. an inline ``[trip]`` table in config.toml
    4. the default trip file in ``config/``
    5. built-in :class:`TripConfig` defaults
    """
    reference = trip_path or data.get("trip_file")
    if reference:
        resolved = resolve_trip_file(reference, config_path.parent)
        if resolved:
            return _load_trip_table(resolved), resolved.name
        if not trip_path and "trip" in data:
            return data["trip"], None
        return {}, None

    if "trip" in data:
        return data["trip"], None

    default = resolve_trip_file(DEFAULT_TRIP_FILE, config_path.parent)
    if default:
        return _load_trip_table(default), default.name
    return {}, None


def load_config(
    config_path: Path | str | None = None,
    trip_path: Path | str | None = None,
) -> AppConfig:
    """Load general settings from config.toml and route settings from the trip file."""
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    data = _read_toml(path) if path.is_file() else {}

    trip_data, trip_filename = _resolve_trip(data, path, trip_path)
    exec_data = data.get("execution", {})
    gf_data = data.get("google_flights", {})
    ss_data = data.get("skyscanner", {})
    comp_data = data.get("comparison", {})

    browser_executable = exec_data.get("browser_executable") or None
    if isinstance(browser_executable, str):
        browser_executable = browser_executable.strip() or None

    return AppConfig(
        trip=TripConfig(
            trip_type=str(trip_data.get("trip_type", "round-trip")),
            origin=trip_data.get("origin", "HEL"),
            dest=trip_data.get("dest", "HAN"),
            min_stay_nights=int(trip_data.get("min_stay_nights", 21)),
            date_mode=str(trip_data.get("date_mode", "range")),
            depart_from=trip_data.get("depart_from") or None,
            depart_to=trip_data.get("depart_to") or None,
            return_from=trip_data.get("return_from") or None,
            return_to=trip_data.get("return_to") or None,
            window_start=trip_data.get("window_start") or None,
            window_end=trip_data.get("window_end") or None,
            exact_pairs=trip_data.get("exact_pairs", []),
        ),
        execution=ExecutionConfig(
            strategy=str(exec_data.get("strategy", "parallel")),
            skip_existing=bool(exec_data.get("skip_existing", True)),
            auto_compare=bool(exec_data.get("auto_compare", True)),
            browser_executable=browser_executable,
        ),
        google_flights=GoogleFlightsConfig(
            enabled=bool(gf_data.get("enabled", True)),
            gl=str(gf_data.get("gl", "FI")).strip().upper(),
            delay_seconds=int(gf_data.get("delay_seconds", 3)),
            timeout_seconds=int(gf_data.get("timeout_seconds", 30)),
            profile_dir=str(gf_data.get("profile_dir", ".browser-profile")),
            results_dir=str(gf_data.get("results_dir", "flight_results")),
        ),
        skyscanner=SkyscannerConfig(
            enabled=bool(ss_data.get("enabled", True)),
            delay_seconds=int(ss_data.get("delay_seconds", 4)),
            timeout_seconds=int(ss_data.get("timeout_seconds", 45)),
            poll_wait_seconds=int(ss_data.get("poll_wait_seconds", 30)),
            challenge_timeout_seconds=int(ss_data.get("challenge_timeout_seconds", 90)),
            profile_dir=str(ss_data.get("profile_dir", ".skyscanner-profile")),
            results_dir=str(ss_data.get("results_dir", "flight_results_skyscanner")),
        ),
        comparison=ComparisonConfig(output_dir=str(comp_data.get("output_dir", "flight_results"))),
        trip_file=trip_filename,
    )
