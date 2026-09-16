# Daily Automation Guide

How to run the fare tracker every day on Windows. See [README.md](README.md) for
setup, configuration and the point-of-sale study tools.

---

## 1. How it works

- **Configuration.** Execution settings live in `config/config.toml`; the route
  and its dates live in a trip file such as `config/HEL_HAN.toml`. Switch routes
  by changing `trip_file`, or per run with `--trip`.
- **Parallel scanning.** `run_all.ps1` launches Google Flights and Skyscanner at
  the same time, scans identical date pairs, and prints a side-by-side
  comparison table.
- **Visible browser required.** Both sites suppress results for headless
  automation, so the scrapers open a real window using their own persistent
  profile directories (`.browser-profile-daily`, `.skyscanner-profile`). Your
  everyday browser profile is never touched.
- **Resuming.** With `skip_existing = true`, a scan interrupted midway picks up
  where it left off: today's already-observed pairs are read from disk and only
  the remaining dates are queried.
- **Reports are incremental.** The daily CSV and JSON are rewritten after every
  query, so they are safe to open at any point during a run.

---

## 2. Running on demand

```powershell
# Both platforms, then the comparison (recommended)
powershell -ExecutionPolicy Bypass -File .\run_all.ps1

# A different route for this run only
powershell -ExecutionPolicy Bypass -File .\run_all.ps1 --trip PHL_HAN.toml

# One platform at a time
powershell -ExecutionPolicy Bypass -File .\run_daily.ps1              # Google Flights
powershell -ExecutionPolicy Bypass -File .\run_daily_skyscanner.ps1   # Skyscanner
powershell -ExecutionPolicy Bypass -File .\run_compare.ps1            # comparison only
```

Logs are written to `flight_results\logs\` and `flight_results_skyscanner\logs\`.

---

## 3. Scheduling a daily run (Windows Task Scheduler)

Run these from the project directory; `$PWD` supplies the paths, so nothing is
hard-coded.

```powershell
$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-ExecutionPolicy Bypass -File `"$PWD\run_all.ps1`"" `
    -WorkingDirectory "$PWD"
$trigger  = New-ScheduledTaskTrigger -Daily -At 9:00AM
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "FlightFinderDailyScan" -Action $action -Trigger $trigger `
    -Settings $settings -Description "Daily flight fare scan"
```

To remove it later:

```powershell
Unregister-ScheduledTask -TaskName "FlightFinderDailyScan" -Confirm:$false
```

> The scrapers need a **visible desktop session**, so schedule the task to run
> only when you are logged in. "Run whether user is logged on or not" starts the
> browser in a session with no display and the scan returns no results.

### Graphical alternative

1. `Win + R`, run `taskschd.msc`.
2. **Create Basic Task** -> name it, choose **Daily** and a time.
3. **Action**: *Start a program*.
   - **Program/script**: `powershell.exe`
   - **Add arguments**: `-ExecutionPolicy Bypass -File "<project path>\run_all.ps1"`
   - **Start in**: `<project path>`
4. On the task's **General** tab, leave *Run only when user is logged on* selected.

---

## 4. Reading the output

Everything lands in `flight_results\` (Google Flights and the comparison) and
`flight_results_skyscanner\` (Skyscanner):

- **Daily CSV** — `daily_fare_report_<date>.csv`
  Columns: `departure_date`, `return_date`, `stay_nights`, `status`,
  `lowest_observed_price_eur`, `protection_label`, `fetched_at`.
  Sort by `lowest_observed_price_eur` ascending for the best deals.
- **Daily JSON** — `daily_fare_report_<date>.json`
  The same rows plus every parsed flight card.
- **Comparison** — `flight_comparison_report_<date>.csv` / `.json`
  Google vs Skyscanner per date pair, with the winner and the saving.
- **Per-pair observation** — `<departure>_<return>.json`
  Full diagnostic detail for one date pair, including the page URL and visible
  text when a scan did not complete.
- **Logs** — `logs\run_<platform>_<date>.log`

### Statuses

| Status | Meaning |
| --- | --- |
| `observed` | Results parsed successfully |
| `incomplete` | Page loaded but no fares could be read; retry |
| `blocked` / `user_action_required` | Anti-bot challenge or sign-in; resolve it in the open window and re-run |
| `error` | The query raised; the pair is recorded and should be retried |
