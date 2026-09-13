"""Configuration loader and schema validator for Flight Finder.

Loads settings from config/config.toml using Python's built-in tomllib.
"""

from __future__ import annotations

import datetime as dt
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.common import build_search_pairs

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "config.toml"


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
        """Generate search date pairs based on the configured date_mode and trip_type."""
        if getattr(self, "trip_type", "round-trip") in {"one-way", "oneway"}:
            if self.depart_from and self.depart_to:
                first = dt.date.fromisoformat(self.depart_from)
                last = dt.date.fromisoformat(self.depart_to)
                return [(first + dt.timedelta(days=i), None) for i in range((last - first).days + 1)]
            return []

        if self.date_mode == "exact" and self.exact_pairs:
            return build_search_pairs(
                exact_pairs=self.exact_pairs,
                min_stay_nights=self.min_stay_nights,
            )

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


def parse_optional_price(val: Any, default: float | None = None) -> float | None:
    """Parse reference price; returns None if 0, empty, false, or omitted to disable checks."""
    if val is None or val == "" or val is False:
        return None
    try:
        p = float(val)
        return p if p > 0 else None
    except (ValueError, TypeError):
        return None


@dataclass
class GoogleFlightsConfig:
    enabled: bool = True
    gl: str = "FI"
    delay_seconds: int = 3
    timeout_seconds: int = 30
    reference_price: float | None = None
    profile_dir: str = ".browser-profile"
    results_dir: str = "flight_results"

    def resolved_profile_dir(self, root: Path = PROJECT_ROOT) -> Path:
        return (root / self.profile_dir).resolve()

    def resolved_results_dir(self, root: Path = PROJECT_ROOT) -> Path:
        return (root / self.results_dir).resolve()


@dataclass
class SkyscannerConfig:
    enabled: bool = True
    delay_seconds: int = 4
    timeout_seconds: int = 45
    poll_wait_seconds: int = 30
    challenge_timeout_seconds: int = 90
    reference_price: float | None = None
    profile_dir: str = ".skyscanner-profile"
    results_dir: str = "flight_results_skyscanner"

    def resolved_profile_dir(self, root: Path = PROJECT_ROOT) -> Path:
        return (root / self.profile_dir).resolve()

    def resolved_results_dir(self, root: Path = PROJECT_ROOT) -> Path:
        return (root / self.results_dir).resolve()


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
    """Resolve a trip configuration file from absolute or relative paths."""
    p = Path(raw_path)
    if p.is_file():
        return p.resolve()
    candidate = (base_dir / p).resolve()
    if candidate.is_file():
        return candidate
    candidate = (PROJECT_ROOT / "config" / p).resolve()
    if candidate.is_file():
        return candidate
    candidate = (PROJECT_ROOT / p).resolve()
    if candidate.is_file():
        return candidate
    return None


def load_config(
    config_path: Path | str | None = None,
    trip_path: Path | str | None = None,
) -> AppConfig:
    """Load and parse TOML configuration from file and modular trip file."""
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    data: dict[str, Any] = {}
    if path.is_file():
        with path.open("rb") as f:
            data = tomllib.load(f)

    # Resolve trip configuration:
    # 1. Explicit trip_path passed to load_config
    # 2. trip_file specified in config.toml
    # 3. Inline [trip] table in config.toml
    # 4. Default "HEL_HAN.toml" in config/ directory
    # 5. Built-in TripConfig defaults
    trip_data: dict[str, Any] = {}
    resolved_trip_filename: str | None = None

    candidate_trip_ref = trip_path or data.get("trip_file")
    if candidate_trip_ref:
        trip_file_path = resolve_trip_file(candidate_trip_ref, path.parent)
        if trip_file_path and trip_file_path.is_file():
            resolved_trip_filename = trip_file_path.name
            with trip_file_path.open("rb") as f:
                t_data = tomllib.load(f)
                trip_data = t_data.get("trip", t_data)
        elif not trip_path and "trip" in data:
            trip_data = data.get("trip", {})
    elif "trip" in data:
        trip_data = data.get("trip", {})
    else:
        def_trip = resolve_trip_file("HEL_HAN.toml", path.parent)
        if def_trip and def_trip.is_file():
            resolved_trip_filename = def_trip.name
            with def_trip.open("rb") as f:
                t_data = tomllib.load(f)
                trip_data = t_data.get("trip", t_data)

    exec_data = data.get("execution", {})
    gf_data = data.get("google_flights", {})
    ss_data = data.get("skyscanner", {})
    comp_data = data.get("comparison", {})

    browser_exec = exec_data.get("browser_executable", "")
    browser_exec = browser_exec.strip() if isinstance(browser_exec, str) else None
    if not browser_exec:
        browser_exec = None

    gf_ref = parse_optional_price(gf_data.get("reference_price"))
    ss_ref = parse_optional_price(ss_data.get("reference_price"))

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
            browser_executable=browser_exec,
        ),
        google_flights=GoogleFlightsConfig(
            enabled=bool(gf_data.get("enabled", True)),
            gl=str(gf_data.get("gl", "SE")).strip().upper(),
            delay_seconds=int(gf_data.get("delay_seconds", 3)),
            timeout_seconds=int(gf_data.get("timeout_seconds", 30)),
            reference_price=gf_ref,
            profile_dir=str(gf_data.get("profile_dir", ".browser-profile")),
            results_dir=str(gf_data.get("results_dir", "flight_results")),
        ),
        skyscanner=SkyscannerConfig(
            enabled=bool(ss_data.get("enabled", True)),
            delay_seconds=int(ss_data.get("delay_seconds", 4)),
            timeout_seconds=int(ss_data.get("timeout_seconds", 45)),
            poll_wait_seconds=int(ss_data.get("poll_wait_seconds", 30)),
            challenge_timeout_seconds=int(ss_data.get("challenge_timeout_seconds", 90)),
            reference_price=ss_ref,
            profile_dir=str(ss_data.get("profile_dir", ".skyscanner-profile")),
            results_dir=str(ss_data.get("results_dir", "flight_results_skyscanner")),
        ),
        comparison=ComparisonConfig(
            output_dir=str(comp_data.get("output_dir", "flight_results")),
        ),
        trip_file=resolved_trip_filename,
    )
