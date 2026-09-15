# Flight Finder

Fare tracking and price comparison for round-trip and one-way flights. It reads
**Google Flights** and **Skyscanner** over identical date pairs, extracts the
lowest available fare (the true *Cheapest* option, not the algorithmic "Best"),
and computes cross-platform arbitrage.

A second set of tools studies **point-of-sale (POS) arbitrage**: the same
itinerary priced from every country Google Flights supports as a market.

---

## Layout

```
run_all.py                  Unified runner: both scrapers in parallel, then the comparison
find_flights.py             Thin wrapper for src/google_flights.py
find_flights_skyscanner.py  Thin wrapper for src/skyscanner.py
compare_flights.py          Thin wrapper for src/compare.py
config/
  config.toml               Execution, browser and per-platform settings
  <ROUTE>.toml              One trip profile per route (HEL_HAN, PHL_HAN, ...)
src/
  common.py                 Date maths, price/duration parsing, browser discovery
  config.py                 TOML loading and the config schema
  regions.py                Google Flights POS region list
  reporting.py              Observation checkpoints, daily reports, summary tables
  browser.py                Shared Playwright page driving
  google_parse.py           Google Flights URL building and page/card parsing (no browser needed)
  skyscanner_parse.py       Skyscanner URL building and payload parsing (no browser needed)
  google_flights.py         Daily Google Flights scanner
  skyscanner.py             Daily Skyscanner scanner
  compare.py                Cross-platform fare comparison
  all_pos_scanner.py        One route x every POS region
  regional_study_scanner.py Fixed 10-market study incl. reverse-route and currency controls
  multi_route_parallel_orchestrator.py  Several routes x POS regions, concurrently
  analysis.py               Shared arbitrage statistics (mode, win counts, discounts)
  filtered_arbitrage_analyzer.py        Mode-exclusion filter -> vetted active markets
  analyze_all_pos.py        POS ranking for a single route
  analyze_regional_study.py POS / POO / currency analysis for the regional study
  multi_route_master_analyzer.py        Cross-corridor master report
tests/                      Unit tests (no browser required)
```

---

## Prerequisites

1. **Python 3.11 or newer** — the config loader uses the standard-library `tomllib`.
2. **Google Chrome** or **Microsoft Edge** installed. On Windows the scrapers
   detect an installed browser automatically, which is less likely to be flagged
   as automation than the bundled Chromium.
3. PowerShell (Windows) or any POSIX shell.

---

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # PowerShell
# source .venv/bin/activate           # macOS / Linux

pip install -r requirements.txt
python -m patchright install chromium  # or: python -m playwright install chromium
```

> If PowerShell blocks activation, run
> `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` first.

### First run

Both scrapers run in a **visible (headful) browser**. Headless automation is
detected by Google Flights and Skyscanner, which suppresses results.

Each scraper uses its own persistent profile directory, so your everyday browser
profile is never opened or modified:

| Scraper | Profile directory |
| --- | --- |
| Google Flights (daily) | `.browser-profile-daily/` (set by `config/config.toml`) |
| Google Flights (POS studies) | `.browser-profile/` |
| Skyscanner | `.skyscanner-profile/` |

On the first run a browser window opens. Resolve any cookie-consent banner,
sign-in prompt or Skyscanner "Press & Hold" challenge by hand; the session is
kept in the profile directory so later runs are unattended.

---

## Configuration

General settings live in [`config/config.toml`](config/config.toml); the active
route and its dates live in a separate trip file under `config/`:

```toml
# config/config.toml
trip_file = "HEL_HAN.toml"

[execution]
strategy = "parallel"       # "parallel" or "sequential"
skip_existing = true        # resume today's scan instead of re-querying
auto_compare = true         # run the comparison once scraping finishes
browser_executable = ""     # leave empty to auto-detect Chrome/Edge

[google_flights]
enabled = true
gl = "FI"                   # point of sale country code
delay_seconds = 3
timeout_seconds = 30
profile_dir = ".browser-profile-daily"
results_dir = "flight_results"

[skyscanner]
enabled = true
delay_seconds = 10
timeout_seconds = 45
poll_wait_seconds = 30      # let slower OTAs finish reporting
challenge_timeout_seconds = 120
profile_dir = ".skyscanner-profile"
results_dir = "flight_results_skyscanner"

[comparison]
output_dir = "flight_results"
```

A trip file sets the route and how date pairs are generated:

```toml
# config/HEL_HAN.toml
[trip]
origin = "HEL"
dest = "HAN"
trip_type = "round-trip"    # or "one-way"
min_stay_nights = 21
date_mode = "exact"         # "exact" | "range" | "window"

# date_mode = "exact": scan exactly these pairs
exact_pairs = [["2026-12-09", "2027-01-05"], ["2026-12-10", "2027-01-06"]]

# date_mode = "range": every departure x every return meeting min_stay_nights
depart_from = "2026-12-09"
depart_to   = "2026-12-13"
return_from = "2027-01-05"
return_to   = "2027-01-09"

# date_mode = "window": every valid pair inside one continuous window
window_start = "2026-12-09"
window_end   = "2027-01-09"
```

Shipped trip profiles: `HEL_HAN`, `PHL_HAN`, `SIN_HAN` (round-trip) and
`HEL_BRU`, `AMS_HEL` (one-way).

---

## Daily fare tracking

```powershell
# Both scrapers in parallel, then the comparison table (recommended)
.\run_all.ps1
python run_all.py
python run_all.py --trip PHL_HAN.toml

# One platform at a time
.\run_daily.ps1              # Google Flights
.\run_daily_skyscanner.ps1   # Skyscanner
.\run_compare.ps1            # comparison only

# Or directly, for any route and dates
python -m src.google_flights --origin PHL --dest HAN \
    --depart-from 2026-12-13 --depart-to 2026-12-16 \
    --return-from 2027-01-09 --return-to 2027-01-10
python -m src.skyscanner --origin LHR --dest JFK \
    --depart-from 2026-11-01 --depart-to 2026-11-01 \
    --return-from 2026-11-15 --return-to 2026-11-15
```

Every flag defaults to the value in the active config, so the commands above
only need the settings you want to override.

### Output

| File | Description |
| --- | --- |
| `flight_results/<departure>_<return>.json` | Full observation for one date pair |
| `flight_results/daily_fare_report_<date>.csv` / `.json` | Google Flights roll-up for the day |
| `flight_results_skyscanner/daily_fare_report_skyscanner_<date>.csv` / `.json` | Skyscanner roll-up |
| `flight_results/flight_comparison_report_<date>.csv` / `.json` | Side-by-side comparison with savings |
| `flight_results*/logs/run_*_<date>.log` | Per-run logs |

Reports are rewritten after **every** query, so an interrupted run still leaves
a readable report. With `skip_existing = true`, re-running resumes from today's
saved observations instead of re-querying them.

---

## Point-of-sale arbitrage study

The same itinerary is often priced differently depending on which country
Google Flights believes you are buying from. These tools measure that.

```powershell
# One route across every POS region, honouring the active trip config
python -m src.all_pos_scanner --config HEL_HAN.toml

# Drop markets that never beat the generic tariff -> active_market_gl_codes.json
python -m src.filtered_arbitrage_analyzer

# Several routes concurrently, using the vetted market list
python -m src.multi_route_parallel_orchestrator

# Fixed 10-market study with reverse-route and native-currency controls
python -m src.regional_study_scanner

# Reports
python -m src.analyze_all_pos              # POS ranking for one route
python -m src.analyze_regional_study       # POS / POO / currency effects
python -m src.multi_route_master_analyzer  # writes MASTER_ARBITRAGE_STUDY.md
```

**Mode-exclusion filter.** Most country codes return the identical modal fare —
the unlocalized GDS fallback tariff. `filtered_arbitrage_analyzer` finds that
mode per date pair, drops every market that never priced below it, and writes
the survivors to `flight_results_all_pos/active_market_gl_codes.json`. Later
scans read that list and skip the rest, which is where most of the scan-time
saving comes from.

Study output lands in `flight_results_all_pos/`, `flight_results_<ORIGIN>_<DEST>/`
and `flight_results_study/`. All of it is gitignored.

---

## Tests

```powershell
python -m unittest discover -s tests -t .
```

99 tests cover date generation, TOML configuration, URL construction, European
and multi-currency price formats, card and page classification, anti-bot
detection, report writing and the arbitrage statistics. They import only the
pure parsing modules, so **the suite runs without Playwright or a browser
installed**.

Lint with `ruff check .` (configured in `pyproject.toml`).

---

## Scope and limitations

- Fares are read from rendered pages and are volatile; always confirm on the
  seller's own site before booking. Nothing here follows a booking link or
  clicks a purchase control.
- Baggage through-checking and missed-connection protection are **not**
  guaranteed for itineraries Google labels *separate tickets booked together*;
  observations carry a `protection_label` for this.
- The Google Flights protobuf URL template is pinned to HEL->HAN. Other routes
  fall back to a free-text query URL, which is slightly less precise.
- `src/analyze_regional_study.py` converts native-currency fares using **static**
  FX rates from September 2026. Refresh them before trusting that table.
- `MASTER_ARBITRAGE_STUDY.md` ends with a hand-written commentary section whose
  figures are fixed prose, not recomputed. Re-verify it against the generated
  tables after a new scan.
