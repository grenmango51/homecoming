# Flight Finder: Google Flights & Skyscanner Scraper & Price Comparison

An automated fare tracking and price comparison tool for round-trip flights. It scrapes both **Google Flights** and **Skyscanner** across identical date pairs, extracts the lowest available fares (prioritizing the true **Cheapest** flight option, not merely algorithmic "Best"), and computes cross-platform price arbitrage.

---

## Key Features

1. **Dual Platform Scrapers**:
   - **Google Flights** (`find_flights.py`): Scrapes Google Flights UI, parses structured flight cards, verifies price baselines.
   - **Skyscanner** (`find_flights_skyscanner.py`): Scrapes Skyscanner UI via Patchright, parses internal unified-search XHR payloads and rendered DOM cards.
2. **100% Identical Date Ranges**: Both scrapers share identical date generation logic (supporting either range mode or full holiday window mode with configurable `min_stay_nights`).
3. **Anti-Bot Bypass Architecture for Skyscanner**:
   - Uses `patchright` to strip CDP automation leaks (`navigator.webdriver`, `Runtime.enable`) at build time.
   - Pre-injects `ssculture` locale cookies (`locale:::en-GB&market:::FI&currency:::EUR`) on `.skyscanner.net` and `.skyscanner.fi`.
   - Initiates requests via `.net` canonical URLs which execute server-side signed session handoffs to `.fi`, bypassing PerimeterX challenges.
   - Human challenge fallback mechanism with configurable timeout if manual verification is ever required.
4. **Cross-Platform Fare Arbitrage** (`compare_flights.py`):
   - Aligns Google Flights and Skyscanner observations for every matching date pair.
   - Computes price differential $\Delta = P_{\text{Google}} - P_{\text{Skyscanner}}$, identifying the cheaper provider and exact savings (€ and %).
   - Generates an aligned terminal summary table and saves timestamped CSV and JSON comparison reports.
5. **Authentically Scraped Reference Benchmark**:
   - Reference pair: **2026-12-09 -> 2027-01-09** (31 nights HEL–HAN).
   - Google Flights: **€800.0**
   - Skyscanner Cheapest: **€782.0**
   - **Arbitrage**: Skyscanner saves **€18.0 (2.2%)** on this itinerary!

---

## Prerequisites

Before getting started, make sure you have:

1. **Python 3.10+** installed on your system (ensure Python is added to your system `PATH`).
2. **Google Chrome** or **Microsoft Edge** installed (on Windows, the scrapers automatically detect your installed browser to bypass bot detection).
3. **PowerShell** (Windows) or standard bash terminal (macOS/Linux).
4. **Git** installed to clone and manage the repository.

---

## Quick Start & Setup Guide

### 1. Clone the Repository
```powershell
git clone <repository-url>
cd "Google flights"
```

### 2. Create and Activate Virtual Environment
```powershell
# Create virtual environment
python -m venv .venv

# Activate virtual environment (PowerShell)
.\.venv\Scripts\Activate.ps1
```

> **Note**: If PowerShell displays an execution policy error when activating, run:
> `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`

### 3. Install Dependencies
```powershell
# Install required Python packages
pip install -r requirements.txt
pip install playwright patchright

# Install the patched Chromium browser binary
.\.venv\Scripts\patchright.exe install chromium
```

---

## First-Time Run & Anti-Bot Verification

Both scrapers operate in **visible (headful) browser mode**. Headless automation is detected by Google Flights and Skyscanner anti-bot heuristics, which suppresses flight results.

* **Dedicated Profiles**: 
  - Google Flights uses `.browser-profile/`
  - Skyscanner uses `.skyscanner-profile/`
  Your everyday personal browser profile is never touched.
* **First-Run Human Verification**:
  On the very first run, a browser window will open. If prompted with a **Cookie Consent banner ("Accept All")**, a Google sign-in prompt, or a Skyscanner Cloudflare/PerimeterX challenge ("Press & Hold"), simply resolve it manually in the open window.
* The session and cookies will be preserved in the persistent profile folder, allowing subsequent scans to run automatically.

---

## How to Customize for Your Own Flights

### A. Skyscanner (`find_flights_skyscanner.py`) — *Custom Routes Ready*
Skyscanner can be run for **any airport codes and dates** directly using command-line arguments:

```powershell
# Scan a single date pair for any route:
.\.venv\Scripts\python.exe find_flights_skyscanner.py --origin LHR --dest JFK --depart-from 2026-11-01 --depart-to 2026-11-01 --return-from 2026-11-15 --return-to 2026-11-15 --reference-price 0

# Scan a date matrix (e.g. 3 departure dates x 3 return dates):
.\.venv\Scripts\python.exe find_flights_skyscanner.py --origin CDG --dest NRT --depart-from 2026-10-10 --depart-to 2026-10-12 --return-from 2026-10-25 --return-to 2026-10-27 --reference-price 0
```

> **Currency / Locale Note**: By default, Skyscanner cookies request the European market in EUR (`market:::FI&currency:::EUR`). To track prices in USD (`USD`), GBP (`GBP`), etc., edit the cookie strings in `find_flights_skyscanner.py`.

### B. Google Flights (`find_flights.py`) — *Route Note*
- **Current Route**: Google Flights uses a base64-encoded protobuf query string (`TFS_TEMPLATE`) that is calibrated for **Helsinki (HEL) to Hanoi (HAN)**.
- **Custom Dates for HEL $\rightarrow$ HAN**: You can change the dates freely:
  ```powershell
  .\.venv\Scripts\python.exe find_flights.py --depart-from 2026-12-09 --depart-to 2026-12-11 --return-from 2027-01-05 --return-to 2027-01-07
  ```
- **Searching a Different Route on Google Flights**:
  If you want to use Google Flights for a different route (e.g., LHR to JFK):
  1. Open [Google Flights](https://www.google.com/travel/flights) in your browser and perform a search for your route.
  2. Copy the `tfs=...` query parameter from the resulting URL.
  3. Update `TFS_TEMPLATE` in `find_flights.py` with your route's query string.

---

## Centralized Configuration (`config/config.toml`)

All search dates, airport codes, stay durations, polite pauses, timeouts, reference benchmarks, and execution settings are maintained in a single editable file:

📂 [`config/config.toml`](file:///d:/Hoai%20Anh/Aalto/Hobbies/Google%20flights/config/config.toml)

```toml
[trip]
origin = "HEL"
dest = "HAN"
min_stay_nights = 21

# "range" (Cartesian product) or "window" (full holiday span)
date_mode = "range"

depart_from = "2026-12-09"
depart_to = "2026-12-13"
return_from = "2027-01-05"
return_to = "2027-01-09"

[execution]
strategy = "parallel"      # "parallel" (both run at the same time) or "sequential"
skip_existing = true       # Skip queries already recorded today
auto_compare = true        # Automatically print comparison table after scans
```

---

## Running Scans & Daily Automation

### 1. Unified Parallel Execution (Recommended)
Run **both** Google Flights and Skyscanner concurrently, followed by the automatic price comparison table:

```powershell
# PowerShell one-click launcher
.\run_all.ps1

# Or directly via Python:
.\.venv\Scripts\python.exe run_all.py
```

### 2. Standalone Platform Scans (Optional)
If you wish to run a specific scraper in isolation:

```powershell
# Run Skyscanner only
.\run_daily_skyscanner.ps1

# Run Google Flights only
.\run_daily.ps1

# Run Cross-Platform Price Comparison only
.\run_compare.ps1
```

---

## Viewing and Analyzing the Results

All scraped data is stored locally in the project directories:

| Output File | Location | Description |
| --- | --- | --- |
| **Google Flights Daily CSV** | `flight_results/daily_fare_report_YYYY-MM-DD.csv` | Summary table of Google Flights prices per date pair |
| **Skyscanner Daily CSV** | `flight_results_skyscanner/daily_fare_report_skyscanner_YYYY-MM-DD.csv` | Summary table of Skyscanner cheapest prices |
| **Comparison Report CSV** | `flight_results/comparison_report_YYYY-MM-DD.csv` | Side-by-side comparison with savings and provider arbitrage |
| **Raw JSON Observations** | `flight_results/` & `flight_results_skyscanner/` | Full structured flight details, timestamps, and card texts |

* **Spreadsheets**: Open any generated `.csv` file directly in **Microsoft Excel** or **Google Sheets**. Sort the `lowest_observed_price_eur` column ascending to instantly see the cheapest flight options.
* **Terminal Summary**: After every run, a Top 10 cheapest flights table is displayed directly in your console.

---

## Command Line Options

### `find_flights_skyscanner.py`

| Flag | Default | Description |
| --- | --- | --- |
| `--origin` | `HEL` | Origin airport IATA code |
| `--dest` | `HAN` | Destination airport IATA code |
| `--depart-from` / `--depart-to` | `2026-12-09` / `2026-12-13` | Inclusive departure date range |
| `--return-from` / `--return-to` | `2027-01-05` / `2027-01-09` | Inclusive return date range |
| `--window-start` / `--window-end` | None | Full holiday trip window (e.g. `2026-12-09` to `2027-01-09`) |
| `--min-stay-nights` | `21` | Minimum stay nights between departure and return |
| `--delay-seconds` | `4` | Politeness pause between consecutive searches |
| `--timeout-seconds` | `35` | Maximum page load timeout |
| `--challenge-timeout-seconds` | `90` | Time allotted for manual challenge/CAPTCHA resolution |
| `--skip-existing` | `False` | Skip date pairs already observed today |
| `--profile-dir` | `.skyscanner-profile/` | Dedicated persistent Chromium profile directory |
| `--results-dir` | `flight_results_skyscanner/` | Output directory for JSON observations & daily CSV |
| `--browser-executable` | Auto-detect | Path to custom Chrome or Edge executable |
| `--reference-price` | `782.0` | Expected reference price for 9 Dec 2026–9 Jan 2027 benchmark |

### `find_flights.py`

| Flag | Default | Description |
| --- | --- | --- |
| `--origin` | `HEL` | Origin airport IATA code (HEL supported by template) |
| `--dest` | `HAN` | Destination airport IATA code (HAN supported by template) |
| `--window-start` / `--window-end` | `2026-12-09` / `2027-01-09` | Full holiday window earliest departure & latest return |
| `--depart-from` / `--depart-to` | None | Optional explicit departure date range |
| `--return-from` / `--return-to` | None | Optional explicit return date range |
| `--min-stay-nights` | `21` | Minimum stay duration in nights |
| `--delay-seconds` | `3` | Pause between pages in seconds |
| `--timeout-seconds` | `30` | Maximum page wait timeout |
| `--skip-existing` | `False` | Skip date pairs already observed today |
| `--profile-dir` | `.browser-profile/` | Dedicated persistent Chromium profile directory |
| `--results-dir` | `flight_results/` | Output directory for JSON observations & daily CSV |
| `--browser-executable` | Auto-detect | Path to custom Chrome or Edge executable |
| `--reference-price` | `800.0` | Expected reference price for 9 Dec 2026–9 Jan 2027 benchmark |

### `compare_flights.py`

| Flag | Default | Description |
| --- | --- | --- |
| `--google-dir` | `flight_results/` | Directory containing Google Flights results |
| `--skyscanner-dir` | `flight_results_skyscanner/` | Directory containing Skyscanner results |
| `--output-dir` | `flight_results/` | Directory for comparison CSV and JSON outputs |
| `--google-report` | Latest in `flight_results/` | Explicit path to Google Flights daily CSV |
| `--skyscanner-report` | Latest in `flight_results_skyscanner/` | Explicit path to Skyscanner daily CSV |

---

## Test Suite

Run the full automated unit test suite:

```powershell
.\.venv\Scripts\python.exe -m unittest discover tests -v
```

All 31 test cases validate TOML configuration parsing, date generation (both range and window modes), URL construction, European price formats, regex parsing, anti-bot classification, and cross-platform arbitrage calculation.
