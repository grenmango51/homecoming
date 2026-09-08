#!/usr/bin/env python3
"""Unified Orchestrator for Flight Finder.

Reads config/config.toml, coordinates parallel (or sequential) execution of
Google Flights and Skyscanner fare scrapers, and automatically triggers
cross-platform price comparison.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import os
import sys
from pathlib import Path

from src.config import load_config, AppConfig, PROJECT_ROOT

ROOT = PROJECT_ROOT


async def stream_output(stream: asyncio.StreamReader, log_file: Path, prefix: str = "") -> None:
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


async def run_scraper(name: str, module: str, log_path: Path, config_path: Path | None = None) -> int:
    """Launch a scraper module as an asynchronous subprocess with live output logging."""
    cmd = [sys.executable, "-m", module]
    if config_path:
        cmd.extend(["--config", str(config_path)])

    print(f"[{name}] Starting process...")
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(ROOT),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    if proc.stdout:
        await stream_output(proc.stdout, log_path, prefix=name)

    exit_code = await proc.wait()
    status_msg = "COMPLETED" if exit_code == 0 else f"EXITED with code {exit_code}"
    print(f"[{name}] {status_msg}")
    return exit_code


async def orchestrate(config_path: Path | None = None) -> int:
    cfg: AppConfig = load_config(config_path)

    today = dt.datetime.now().astimezone().strftime("%Y-%m-%d")
    print("=" * 65)
    print(f" FLIGHT FINDER UNIFIED RUNNER ({today})")
    print("=" * 65)
    print(f" Route:            {cfg.trip.origin} -> {cfg.trip.dest}")
    print(f" Date Mode:        {cfg.trip.date_mode.upper()}")
    if cfg.trip.date_mode == "range":
        print(f" Departures:       {cfg.trip.depart_from} to {cfg.trip.depart_to}")
        print(f" Returns:          {cfg.trip.return_from} to {cfg.trip.return_to}")
    else:
        print(f" Window:           {cfg.trip.window_start} to {cfg.trip.window_end}")
    print(f" Min Stay:         {cfg.trip.min_stay_nights} nights")
    print(f" Strategy:         {cfg.execution.strategy.upper()}")
    print(f" Skip Existing:    {cfg.execution.skip_existing}")
    print(f" Google Flights:   {'ENABLED' if cfg.google_flights.enabled else 'DISABLED'}")
    print(f" Skyscanner:       {'ENABLED' if cfg.skyscanner.enabled else 'DISABLED'}")
    print("=" * 65 + "\n")

    tasks = []
    google_log = cfg.google_flights.resolved_results_dir() / "logs" / f"run_google_{today}.log"
    skyscanner_log = cfg.skyscanner.resolved_results_dir() / "logs" / f"run_skyscanner_{today}.log"

    exit_codes: dict[str, int] = {}

    if cfg.execution.strategy == "parallel":
        coros = []
        if cfg.google_flights.enabled:
            coros.append(("Google Flights", run_scraper("Google Flights", "src.google_flights", google_log, config_path)))
        if cfg.skyscanner.enabled:
            coros.append(("Skyscanner", run_scraper("Skyscanner", "src.skyscanner", skyscanner_log, config_path)))

        results = await asyncio.gather(*(c[1] for c in coros), return_exceptions=False)
        for (name, _), code in zip(coros, results):
            exit_codes[name] = code
    else:
        # Sequential execution
        if cfg.google_flights.enabled:
            exit_codes["Google Flights"] = await run_scraper("Google Flights", "src.google_flights", google_log, config_path)
        if cfg.skyscanner.enabled:
            exit_codes["Skyscanner"] = await run_scraper("Skyscanner", "src.skyscanner", skyscanner_log, config_path)

    print("\n" + "=" * 65)
    print(" SCRAPING SUMMARY")
    print("=" * 65)
    for name, code in exit_codes.items():
        state = "SUCCESS" if code == 0 else f"WARNING / EXIT {code}"
        print(f" - {name.ljust(18)}: {state}")
    print("=" * 65 + "\n")

    # Run comparison if requested
    if cfg.execution.auto_compare:
        print("[Comparison] Running cross-platform price comparison...")
        from src.compare import main as compare_main
        try:
            compare_main()
        except SystemExit as exc:
            if exc.code not in (0, None):
                print(f"[Comparison] Warning: Comparison exited with code {exc.code}")

    # Return non-zero if all scrapers failed
    if any(code == 0 for code in exit_codes.values()):
        return 0
    return 1 if exit_codes else 0


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace", line_buffering=True)

    parser = argparse.ArgumentParser(description="Unified parallel orchestrator for flight scrapers.")
    parser.add_argument("--config", type=Path, help="Path to custom config.toml")
    args = parser.parse_args()

    exit_code = asyncio.run(orchestrate(args.config))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
