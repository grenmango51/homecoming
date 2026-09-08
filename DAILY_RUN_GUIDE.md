# Daily Automation & Execution Guide

This guide explains how to run the Google Flights fare tracker daily on Windows to monitor HEL → HAN prices across all eligible dates (trips 21+ nights between Dec 9, 2026 and Jan 9, 2027).

---

## 1. How It Works (Important Context)

- **Central Configuration (`config/config.toml`)**:
  All parameters (dates, routes, delay times, timeouts, reference benchmarks, and parallel execution mode) are maintained in a single editable file: `config/config.toml`.
- **Parallel Multi-Platform Scanning**:
  Running `run_all.ps1` launches Google Flights and Skyscanner in parallel, scans matching date pairs, and automatically generates a side-by-side price comparison table.
- **Headful / Visible Browser Required**:
  Google Flights and Skyscanner employ anti-bot heuristics that detect headless automation (`navigator.webdriver`). In headless mode, search results are suppressed.
  Therefore, this tool uses visible browser windows with isolated persistent profile folders (`.browser-profile` and `.skyscanner-profile`).
- **Resuming with `skip_existing = true`**:
  If a daily scan is ever stopped or interrupted midway through, re-running immediately resumes from where it left off, reading today's already observed pairs from disk and scanning only the remaining dates.
- **Reference Price Check**:
  Every daily report verifies the baseline reference pair (Dec 9, 2026 to Jan 9, 2027) as an automated integrity check.

---

## 2. Running On-Demand (Manual Execution)

### Option A: Unified Parallel Runner (Recommended)
Open PowerShell in this directory and run:
```powershell
powershell -ExecutionPolicy Bypass -File .\run_all.ps1
```
This runs both scrapers concurrently, saves logs to `flight_results/logs/` and `flight_results_skyscanner/logs/`, and prints the cross-platform comparison arbitrage table.

### Option B: Standalone Platform Scanners
- **Google Flights only**: `powershell -ExecutionPolicy Bypass -File .\run_daily.ps1`
- **Skyscanner only**: `powershell -ExecutionPolicy Bypass -File .\run_daily_skyscanner.ps1`
- **Price Comparison only**: `powershell -ExecutionPolicy Bypass -File .\run_compare.ps1`

---

## 3. Setting Up Automated Daily Scheduling (Windows Task Scheduler)

You can have Windows automatically launch the scan every day at a time of your choice (e.g. 9:00 AM).

### Method A: One-Line PowerShell Setup (Recommended)
Open PowerShell (as Administrator if you want it to run with elevated privileges) and run:

```powershell
$action = New-ScheduledTaskAction -Execute "d:\Hoai Anh\Aalto\Hobbies\Google flights\run_daily.bat" -WorkingDirectory "d:\Hoai Anh\Aalto\Hobbies\Google flights"
$trigger = New-ScheduledTaskTrigger -Daily -At 9:00AM
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "GoogleFlightsDailyScan" -Action $action -Trigger $trigger -Settings $settings -Description "Daily Google Flights matrix scan for HEL to HAN"
```

To remove or disable the scheduled task later:
```powershell
Unregister-ScheduledTask -TaskName "GoogleFlightsDailyScan" -Confirm:$false
```

### Method B: Graphical Interface (Task Scheduler GUI)
1. Press `Win + R`, type `taskschd.msc`, and press Enter.
2. In the right pane, click **Create Basic Task...**.
3. **Name**: `Google Flights Daily Scan` -> Click **Next**.
4. **Trigger**: Select **Daily** -> Click **Next**.
5. **Time**: Choose your preferred time (e.g. `09:00:00 AM`), Recur every `1` days -> Click **Next**.
6. **Action**: Select **Start a program** -> Click **Next**.
7. **Program/script**: Click Browse and select:
   `d:\Hoai Anh\Aalto\Hobbies\Google flights\run_daily.bat`
   - **Start in (optional)**: Enter `d:\Hoai Anh\Aalto\Hobbies\Google flights`
8. Click **Finish**.

---

## 4. Reading and Analyzing the Output

All scan results are saved automatically inside `flight_results/`:
- **Daily CSV**: `flight_results/daily_fare_report_YYYY-MM-DD.csv`
  - Can be opened directly in Microsoft Excel, Google Sheets, or any spreadsheet tool.
  - Columns: `departure_date`, `return_date`, `stay_nights`, `status`, `lowest_observed_price_eur`, `protection_label`, `fetched_at`.
  - Sort by `lowest_observed_price_eur` (Ascending) to immediately see the best flight deals.
- **Daily JSON**: `flight_results/daily_fare_report_YYYY-MM-DD.json`
  - Contains complete metadata, reference check pass/fail, and full flight-card text for every candidate flight.
- **Individual Pair Observations**: `flight_results/YYYY-MM-DD_YYYY-MM-DD.json`
  - Deep-dive diagnostic data for each specific route/date pair.
- **Logs**: `flight_results/logs/run_YYYY-MM-DD.log`
