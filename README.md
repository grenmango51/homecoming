# Flight finder
# Flight Finder: Google Flights & Skyscanner Scraper & Price Comparison

Human-supervised Google Flights matrix scanner for Helsinki (HEL) to Hanoi
(HAN) returns. It reads the rendered Google Flights UI in a visible Chromium
window and saves timestamped observations locally. It does not click booking or
checkout controls.
Automated fare tracking and price comparison tool for Helsinki (HEL) to Hanoi (HAN) round trips.
It scrapes both **Google Flights** and **Skyscanner** across identical date pairs, extracts the lowest available fares (prioritizing the true **Cheapest** flight option, not merely the algorithmic "Best"), and computes cross-platform price arbitrage.

## Run
---

## Key Features

1. **Dual Platform Scrapers**:
   - **Google Flights** (`find_flights.py`): Scrapes Google Flights UI, parses structured flight cards, verifies €800 baseline.
   - **Skyscanner** (`find_flights_skyscanner.py`): Scrapes Skyscanner UI via Patchright, parses internal unified-search XHR payloads and rendered DOM cards, verifies €782 baseline.
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
   - Reference pair: **2026-12-09 -> 2027-01-09** (31 nights).
   - Google Flights: **€800.0**
   - Skyscanner Cheapest: **€782.0**
   - **Arbitrage**: Skyscanner saves **€18.0 (2.2%)** on this itinerary!

---

## Quick Start (PowerShell)

### 1. Prerequisites & Dependencies

```powershell
# Default: 25 exact pairs — 9–13 Dec 2026 × 5–9 Jan 2027
.\run_daily_report.ps1
# In virtual environment:
.\.venv\Scripts\python.exe -m pip install playwright patchright
.\.venv\Scripts\patchright.exe install chromium
```

# Install Chromium once if needed
.\.venv\Scripts\python.exe -m pip install playwright
.\.venv\Scripts\playwright.exe install chromium
### 2. Run Skyscanner Daily Scan

```powershell
# Scans default 25 date pairs (or reference pair with custom flags)
.\run_daily_skyscanner.ps1

# Or scan single reference pair directly:
.\.venv\Scripts\python.exe find_flights_skyscanner.py --depart-from 2026-12-09 --depart-to 2026-12-09 --return-from 2027-01-09 --return-to 2027-01-09
```

On the first run, handle Google consent, sign-in, or challenges directly in the
visible browser. The dedicated profile is `.browser-profile/` by default; set
`GOOGLE_FLIGHTS_PROFILE_DIR` to put it elsewhere. Results go to
`flight_results/`, which is deliberately ignored by Git.
### 3. Run Google Flights Daily Scan

On Windows the scanner automatically uses installed Chrome or Edge with that
dedicated profile. This avoids a broken Playwright-managed Chromium install. To
choose a browser explicitly, pass `--browser-executable 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'`.
```powershell
.\run_daily.ps1
```

Each run refreshes `daily_fare_report_YYYY-MM-DD.csv` and `.json` with one row
per exact date pair, plus separate timestamped observations. The report records
whether the 9 Dec–9 Jan reference price matches €800. A mismatch is an alert,
not a substitute fare: prices are volatile and the tool never fabricates a
match.
### 4. Run Cross-Platform Price Comparison

The scanner stops rather than attempting to bypass a CAPTCHA or sign-in flow.
Every result requires seller confirmation for baggage handling and missed-
connection protection, regardless of Google's label.
```powershell
.\run_compare.ps1
```

## Options
---

## Command Line Options

### `find_flights_skyscanner.py`

| Flag | Default | Description |
| --- | --- | --- |
| `--depart-from` / `--depart-to` | `2026-12-09` / `2026-12-13` | Inclusive departure range |
| `--return-from` / `--return-to` | `2027-01-05` / `2027-01-09` | Inclusive return range |
| `--min-stay-nights` | `21` | Minimum time between departure and return dates |
| `--delay-seconds` | `8` | Respectful pause between result pages |
| `--timeout-seconds` | `30` | Maximum wait for each rendered page |
| `--profile-dir` | `.browser-profile/` | Dedicated persistent Chromium profile |
| `--results-dir` | `flight_results/` | Local timestamped JSON observations |
| `--browser-executable` | Installed Chrome/Edge | Browser executable override |
| `--reference-price` | `800` | Expected price for 9 Dec–9 Jan, used as a report alert |
| `--window-start` / `--window-end` | None | Full trip window (e.g. `2026-12-09` to `2027-01-09`) |
| `--min-stay-nights` | `21` | Minimum stay nights |
| `--delay-seconds` | `4` | Politeness pause between searches |
| `--timeout-seconds` | `35` | Page load timeout |
| `--profile-dir` | `.skyscanner-profile/` | Dedicated persistent Chromium profile |
| `--results-dir` | `flight_results_skyscanner/` | Output directory for JSON observations & daily CSV |
| `--reference-price` | `782.0` | Expected price for 9 Dec–9 Jan reference pair |
| `--skip-existing` | `False` | Skip pairs already observed today |

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

Run all automated unit tests:

```powershell
.\.venv\Scripts\python.exe -m unittest test_find_flights.py test_skyscanner.py test_compare_flights.py -v
```
All 27 test cases validate date generation, URL construction, European price formats, regex parsing, anti-bot classification, and arbitrage logic.
