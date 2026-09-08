# Daily Automation & Execution Guide

This guide explains how to run the Google Flights fare tracker daily on Windows to monitor HEL → HAN prices across all eligible dates (trips 21+ nights between Dec 9, 2026 and Jan 9, 2027).

---

## 1. How It Works (Important Context)

- **Headful / Visible Browser Required**:
  Google Flights employs advanced anti-bot heuristics that detect headless automation (`navigator.webdriver`). In headless mode, Google Flights suppresses flight results.
  Therefore, this tool uses a visible Chrome window with `--disable-blink-features=AutomationControlled` using a dedicated private profile folder (`.browser-profile`).
- **Resuming with `--skip-existing`**:
  If a daily scan is ever stopped or interrupted midway through its 66 pairs, re-running with `--skip-existing` immediately resumes from where it left off, reading today's already observed pairs from disk and scanning only the remaining dates.
- **Reference Price Check**:
  Every daily report verifies the €800 target reference pair (Dec 9, 2026 to Jan 9, 2027) as an automated integrity check.

---

## 2. Running On-Demand (Manual Execution)

### Option A: One-Click Batch File (Easiest)
Simply double-click:
```
d:\Hoai Anh\Aalto\Hobbies\Google flights\run_daily.bat
```
This activates the virtual environment, executes the 66-pair matrix with `--skip-existing`, and leaves the console open so you can view the summary.

### Option B: PowerShell Runner
Open PowerShell in this directory:
```powershell
powershell -ExecutionPolicy Bypass -File .\run_daily.ps1
```
This logs output to `flight_results/logs/run_YYYY-MM-DD.log` and prints a neat sorted table of the Top 10 cheapest flights found today.

### Option C: Direct Python CLI
```powershell
.\.venv\Scripts\python.exe find_flights.py --skip-existing --delay-seconds 3
```

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
