#!/usr/bin/env python3
"""Unified Orchestrator for Flight Finder.

Reads config/config.toml, coordinates parallel (or sequential) execution of
Google Flights and Skyscanner fare scrapers, and automatically triggers
cross-platform price comparison.
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
from pathlib import Path

from src.common import configure_stdio
from src.config import PROJECT_ROOT, AppConfig, load_config
from src.reporting import today_stamp

ROOT = PROJECT_ROOT

# Windows venv layout; on other platforms the active interpreter is used.
VENV_PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"

SCRAPERS = (
    ("Google Flights", "src.google_flights", "google_flights"),
    ("Skyscanner", "src.skyscanner", "skyscanner"),
)


async def stream_output(stream: asyncio.StreamReader, log_file: Path) -> None:
    """Stream process stdout/stderr in real-time to console and log file."""
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("a", encoding="utf-8", errors="replace") as f:
        while True:
            line_bytes = await stream.readline()
            if not line_bytes:
                break
            line = line_bytes.decode("utf-8", errors="replace")
            f.write(line)
            f.flush()
            sys.stdout.write(line)
            sys.stdout.flush()


async def run_scraper(
    name: str,
    module: str,
    log_path: Path,
    config_path: Path | None = None,
    trip_path: Path | str | None = None,
    skip_existing: bool | None = None,
) -> int:
    """Launch a scraper module as an asynchronous subprocess with live output logging."""
    py_exec = str(VENV_PYTHON) if VENV_PYTHON.is_file() else sys.executable
    cmd = [py_exec, "-m", module]
    if config_path:
        cmd.extend(["--config", str(config_path)])
    if trip_path:
        cmd.extend(["--trip", str(trip_path)])
    if skip_existing is not None:
        cmd.append("--skip-existing" if skip_existing else "--no-skip-existing")

    print(f"[{name}] Starting process...")
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(ROOT),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    if proc.stdout:
        await stream_output(proc.stdout, log_path)

    exit_code = await proc.wait()
    status_msg = "COMPLETED" if exit_code == 0 else f"EXITED with code {exit_code}"
    print(f"[{name}] {status_msg}")
    return exit_code


async def orchestrate(
    config_path: Path | None = None,
    trip_path: Path | str | None = None,
    skip_existing: bool | None = None,
) -> int:
    """Run the enabled scrapers, then the cross-platform comparison."""
    cfg: AppConfig = load_config(config_path, trip_path)
    if skip_existing is not None:
        cfg.execution.skip_existing = skip_existing

    today = today_stamp()
    print("=" * 65)
    print(f" FLIGHT FINDER UNIFIED RUNNER ({today})")
    print("=" * 65)
    print(f" Route:            {cfg.trip.origin} -> {cfg.trip.dest}")
    if cfg.trip_file:
        print(f" Trip Profile:     {cfg.trip_file}")
    print(f" Date Mode:        {cfg.trip.date_mode.upper()}")
    if cfg.trip.date_mode == "range":
        print(f" Departures:       {cfg.trip.depart_from} to {cfg.trip.depart_to}")
        print(f" Returns:          {cfg.trip.return_from} to {cfg.trip.return_to}")
    elif cfg.trip.date_mode == "exact":
        print(f" Exact Pairs:      {len(cfg.trip.exact_pairs)}")
    else:
        print(f" Window:           {cfg.trip.window_start} to {cfg.trip.window_end}")
    print(f" Min Stay:         {cfg.trip.min_stay_nights} nights")
    print(f" Strategy:         {cfg.execution.strategy.upper()}")
    print(f" Skip Existing:    {cfg.execution.skip_existing}")
    print(f" Google Flights:   {'ENABLED (' + ', '.join(cfg.google_flights.gl_list) + ')' if cfg.google_flights.enabled else 'DISABLED'}")
    print(f" Skyscanner:       {'ENABLED' if cfg.skyscanner.enabled else 'DISABLED'}")
    print("=" * 65 + "\n")

    active_trip = trip_path or cfg.trip_file
    enabled = [
        (name, module, getattr(cfg, attr).resolved_results_dir() / "logs" / f"run_{attr}_{today}.log")
        for name, module, attr in SCRAPERS
        if getattr(cfg, attr).enabled
    ]

    def launch(name: str, module: str, log_path: Path):
        return run_scraper(name, module, log_path, config_path, active_trip, cfg.execution.skip_existing)

    exit_codes: dict[str, int] = {}
    if cfg.execution.strategy == "parallel":
        results = await asyncio.gather(*(launch(*scraper) for scraper in enabled))
        exit_codes = {name: code for (name, _, _), code in zip(enabled, results, strict=True)}
    else:
        for scraper in enabled:
            exit_codes[scraper[0]] = await launch(*scraper)

    print("\n" + "=" * 65)
    print(" SCRAPING SUMMARY")
    print("=" * 65)
    for name, code in exit_codes.items():
        print(f" - {name.ljust(18)}: {'SUCCESS' if code == 0 else f'WARNING / EXIT {code}'}")
    print("=" * 65 + "\n")

    if exit_codes and all(code != 0 for code in exit_codes.values()):
        print("[Comparison] Skipping price comparison because all scrapers failed.")
        return 1

    if cfg.execution.auto_compare:
        print("[Comparison] Running cross-platform price comparison...")
        from src.compare import main as compare_main

        comp_args: list[str] = []
        if config_path:
            comp_args.extend(["--config", str(config_path)])
        if active_trip:
            comp_args.extend(["--trip", str(active_trip)])
        try:
            compare_main(comp_args)
        except SystemExit as exc:
            if exc.code not in (0, None):
                print(f"[Comparison] Warning: Comparison exited with code {exc.code}")

    return 0


def ensure_venv() -> None:
    """Re-exec under the project venv when started with a different interpreter."""
    if not VENV_PYTHON.is_file():
        return
    try:
        if Path(sys.executable).resolve() == VENV_PYTHON.resolve():
            return
        result = subprocess.run([str(VENV_PYTHON)] + sys.argv, cwd=str(ROOT))
    except OSError:
        return
    sys.exit(result.returncode)


def main() -> None:
    ensure_venv()
    configure_stdio()

    parser = argparse.ArgumentParser(description="Unified parallel orchestrator for flight scrapers.")
    parser.add_argument("--config", type=Path, help="Path to custom config.toml")
    parser.add_argument("--trip", type=str, help="Path or name of trip config file (e.g. HEL_HAN.toml or PHL_HAN.toml)")
    parser.add_argument("--skip-existing", action=argparse.BooleanOptionalAction, default=None, help="Override skip_existing")
    args = parser.parse_args()

    exit_code = asyncio.run(orchestrate(args.config, args.trip, args.skip_existing))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
