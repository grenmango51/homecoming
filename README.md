# Flight Finder

Flight Finder automatically tracks, scans, and compares flight prices across **Google Flights** and **Skyscanner** to find the true lowest fares (including budget airlines and self-transfer options, not just algorithmic "Best" recommendations).

---

## Quick Start

### 1. Installation

Requires **Python 3.11+** and **Google Chrome** or **Microsoft Edge**.

```powershell
# Create and activate a virtual environment
python -m venv .venv
.\.venv\Scripts\activate.ps1     # On Windows (PowerShell)
# source .venv/bin/activate      # On macOS / Linux

# Install dependencies and browser support
pip install -r requirements.txt
python -m patchright install chromium
```

### 2. Run the Search

To run both Google Flights and Skyscanner and generate a side-by-side price comparison table:

```powershell
python run_all.py
```
*(Or run `.\run_all.ps1` on Windows)*

A visible browser window will open to fetch live prices. Once finished, a summary table is printed to your terminal, and detailed reports (`.csv` and `.json`) are saved in the `flight_results/` folder.

---

## Configuring Your Flights

All flight search settings are configured through simple trip files located in the `config/trips/` folder (such as `config/trips/HEL_HAN.toml`, with `config/HEL_HAN.toml` supported for backwards compatibility).

To select which trip file to use, set `trip_file` in [`config/config.toml`](config/config.toml):

```toml
# config/config.toml
trip_file = "HEL_HAN.toml"
```

### Flight Settings (`config/trips/<YOUR_TRIP>.toml`)

Open your route's configuration file (e.g., `config/trips/HEL_HAN.toml`) to customize your travel details:

```toml
[trip]
# Departure and arrival airport codes (IATA 3-letter uppercase)
origin = "HEL"
dest = "HAN"

# Type of trip: "round-trip" or "one-way"
trip_type = "round-trip"

# Minimum stay duration at destination in nights (for round-trip)
min_stay_nights = 21

# Date strategy: choose "exact", "range", or "window"
date_mode = "exact"

# ------------------------------------------------------------------------------
# Option 1: "exact" date pairs (active when date_mode = "exact")
# Specify the exact departure and return dates you want to check:
# ------------------------------------------------------------------------------
exact_pairs = [
    ["2026-12-09", "2027-01-05"],
    ["2026-12-09", "2027-01-07"],
    ["2026-12-10", "2027-01-06"],
    ["2026-12-10", "2027-01-09"],
]

# ------------------------------------------------------------------------------
# Option 2: Date "range" (active when date_mode = "range")
# Searches all combinations between these departure and return dates
# that satisfy your min_stay_nights:
# ------------------------------------------------------------------------------
depart_from = "2026-12-09"
depart_to   = "2026-12-13"
return_from = "2027-01-05"
return_to   = "2027-01-09"

# ------------------------------------------------------------------------------
# Option 3: General "window" (active when date_mode = "window")
# Searches all possible departure and return dates inside this holiday window:
# ------------------------------------------------------------------------------
window_start = "2026-12-09"
window_end   = "2027-01-09"
```

### Adding a New Route

To track a new route:
1. Create a new file in `config/trips/` named after your route (for example, `config/trips/JFK_CDG.toml` for New York to Paris).
2. Set your `origin`, `dest`, and dates inside that file.
3. In `config/config.toml`, change `trip_file = "JFK_CDG.toml"`.
4. Run `python run_all.py`.


---

## What to Expect While Running

1. **Visible Browser Window**: The search runs in a visible browser window to ensure airlines and booking providers return accurate live pricing without blocking.
2. **Persistent Profiles & First-Run Consent**: Persistent browser profiles (`.browser-profile` and `.skyscanner-profile`) maintain cookies, storage, and consent choices across runs. No automatic cookie wiping is performed. If cookie consent or a verification prompt appears on a first run, resolve it once interactively; preferences persist for future automated runs.
3. **Resuming Searches**: Re-running automatically skips date pairs already completed today using strict cache verification (`COMPLETION_VERSION = 3`). Incomplete, timed-out, or deferred entries are cleanly re-queried.

---

## Robustness, Completion & Performance Features

- **Authoritative Completion Verification**: Lowest fares are published **only** after searches fully complete. On Skyscanner, intermediate in-flight prices are superseded by the final authoritative payload (status `COMPLETE`) rather than taking transient or retracted lower prices. On Google Flights, DOM settling (`_wait_for_settled`) ensures flight cards are rendered before parsing.
- **Fail-Closed on Unverified Results**: If rendering times out, provider responses fail to arrive, or a page is blocked, the query fails closed: no false or ghost low fares are published (`lowest_observed_price_eur: null`), and the exact status (`timeout`, `user_action_required`, `deferred`) is logged.
- **Unattended Default & Challenge Handling**: In automated runs (`attended = false`, default `challenge_timeout_seconds = 0`), anti-bot challenges trigger an immediate fail-closed exit or pair deferral rather than stalling. Pass `--attended` when running interactively to wait for manual captcha solving.
- **Circuit Breaker**: Detects repeated blocking signals (default threshold: 2 consecutive blocked attempts) and aborts further queries immediately, recording remaining pairs as deferred. This prevents IP reputation degradation and wasted browser cycles.
- **Runtime Budget**: The scan operates under an enforceable runtime budget (`--runtime-budget-seconds`, default 1800s / 30 min). When the deadline is reached, in-flight work halts cleanly, saving all completed observations and deferring remainder.
- **Provenance-Aware Fare Parsing**: Removes arbitrary fare floors (e.g. €50 minimums that discarded valid budget routes) while actively filtering out ancillary fees (baggage, seat selection, extra legroom) using contextual term detection.

---

## Live Acceptance & Verification Procedure

When testing or validating scraper performance on live routes:

1. **Run Full Batches**:
   - Google Flights: verify completion over 50 queries (e.g. 5 departures × 10 returns, or full range).
   - Skyscanner: verify completion over 25 queries.
2. **Delayed Same-Search Final Price Recheck**:
   - Periodically cross-check scraped lowest fares against a manual browser search for the exact same date pair after results settle to verify the final displayed fare matches the logged authoritative fare.
3. **Verify Timing & Coverage Summary**:
   - Inspect the final log output for total runtime, per-query average elapsed time, and coverage metrics (e.g. `25/25 completed (100.0%)`, `0 deferred`, `0 timed out`).
   - *Important*: A quick blocked exit (e.g. encountering a captcha on query 1 and exiting in 15 seconds) is **not** a successful speedup. Real efficiency gains come from bounded DOM settling, asynchronous XHR capture, and removing redundant fixed sleep delays while preserving 100% authoritative completion.

---

## Results & Reports

After each run, results are saved in the `flight_results/` directory:
- **`flight_comparison_report_<DATE>.csv`**: Side-by-side price comparison table showing the cheapest platform and potential savings for each date pair.
- **`daily_fare_report_<DATE>.csv`**: Full log of lowest observed fares for Google Flights.
- **`flight_results_skyscanner/daily_fare_report_skyscanner_<DATE>.csv`**: Full log of lowest observed fares for Skyscanner.
