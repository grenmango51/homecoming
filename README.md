# Flight finder

Human-supervised Google Flights matrix scanner for Helsinki (HEL) to Hanoi
(HAN) returns. It reads the rendered Google Flights UI in a visible Chromium
window and saves timestamped observations locally. It does not click booking or
checkout controls.

## Run

```powershell
# Default: 25 exact pairs — 9–13 Dec 2026 × 5–9 Jan 2027
.\run_daily_report.ps1

# Install Chromium once if needed
.\.venv\Scripts\python.exe -m pip install playwright
.\.venv\Scripts\playwright.exe install chromium
```

On the first run, handle Google consent, sign-in, or challenges directly in the
visible browser. The dedicated profile is `.browser-profile/` by default; set
`GOOGLE_FLIGHTS_PROFILE_DIR` to put it elsewhere. Results go to
`flight_results/`, which is deliberately ignored by Git.

On Windows the scanner automatically uses installed Chrome or Edge with that
dedicated profile. This avoids a broken Playwright-managed Chromium install. To
choose a browser explicitly, pass `--browser-executable 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'`.

Each run refreshes `daily_fare_report_YYYY-MM-DD.csv` and `.json` with one row
per exact date pair, plus separate timestamped observations. The report records
whether the 9 Dec–9 Jan reference price matches €800. A mismatch is an alert,
not a substitute fare: prices are volatile and the tool never fabricates a
match.

The scanner stops rather than attempting to bypass a CAPTCHA or sign-in flow.
Every result requires seller confirmation for baggage handling and missed-
connection protection, regardless of Google's label.

## Options

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
