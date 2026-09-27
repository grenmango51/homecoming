# Browser Profile Cleanup Plan

Execution plan for reducing the browser-profile clutter in the project root.
Written for an agent to execute phase by phase. Each phase ends with a check;
do not start the next phase until the check passes.

## Background (state as of 2026-09-27)

- The project root holds 45 profile folders (`.browser-profile*`, `.skyscanner-profile*`), about 2.5 GB in total.
- The daily run (`run_all.py`) uses only two of them, set in `config/config.toml`:
  - `[google_flights] profile_dir = ".browser-profile-daily"` (about 147 MB)
  - `[skyscanner] profile_dir = ".skyscanner-profile-stealth"` (about 675 MB)
- Profiles are not recreated per run. `src/google_flights.py` (`run()`, around line 238) and `src/skyscanner.py` (`run()`, around line 530) call `mkdir(exist_ok=True)` and reuse the folder.
- The other folders came from:
  - ad-hoc test runs using `--profile-dir` or the `GOOGLE_FLIGHTS_PROFILE_DIR`/`SKYSCANNER_PROFILE_DIR` environment variables (`.browser-profile-test-*`, `-debug`, `-verify`, `-oneway-*`, `-temp-check`, `-capture`, `-loc-test`, `-hel-han`, `test2`)
  - `experiments/pos/multi_route_parallel_orchestrator.py`, which creates `.browser-profile-{ORIGIN}_{DEST}` per route
  - `experiments/pos/all_pos_scanner.py` and `regional_study_scanner.py`, which use `.browser-profile`
  - old config defaults (`.browser-profile`, `.skyscanner-profile`)
- Most of a profile's size is disposable cache. In `.skyscanner-profile-stealth`, `Default/Cache` is 498 MB and `Default/Code Cache` is 148 MB. Cookies (`Default/Network`), `Local Storage` and `IndexedDB` together are under 1 MB.
- The Skyscanner bot-check clearance lives in cookies and site storage. It must survive between runs (see `docs/REBUILD_PLAN.md` section 4 and README "Persistent Profiles"). Google Flights consent is pre-seeded on every launch (`GOOGLE_CONSENT_COOKIES` in `src/browser/session.py`), and the market is passed in the URL. So Google Flights probably does not need a saved profile.
- `var/` is already in `.gitignore`, and `docs/REBUILD_PLAN.md` already proposes `var/profiles/<provider>/`.

## Goals

1. Keep at most one profile folder per provider, stored under `var/profiles/` instead of the project root.
2. Stop profiles from growing without limit.
3. Let Google Flights run without a saved profile, once a test run shows this works.
4. Remove the unused folders, but only after the user approves.

## Guardrails

- Never delete a profile folder without explicit user approval of the exact list. No wildcard `Remove-Item -Recurse` over `.browser-profile*`.
- Move or change a profile only while no Chrome process is using it (see the check in Phase 0).
- Keep the `GOOGLE_FLIGHTS_PROFILE_DIR`/`SKYSCANNER_PROFILE_DIR` environment variables and the `--profile-dir` CLI flags working.
- The two providers must keep separate profiles. `tests/test_config.py::test_google_and_skyscanner_use_separate_profiles_and_result_dirs` must keep passing.
- Tests must stay browser-free (see `AGENTS.md`). Put pure logic in `src/config.py` or `src/common.py`. Put anything that launches Chromium in `src/browser/`.
- Skyscanner stays on a persistent profile. Do not add cookie-clearing or reset logic.
- Out of scope: the `flight_results_*` folders and `scratch_*.json` files in the root. Trip configs and tests reference them (for example `test_modular_hel_ams_config`), so moving them needs its own plan.

## Phase 0: Baseline

1. Run `git status --short` and record the output. Don't touch untracked user files (`scratch_*.json` and similar).
2. Run the checks from `AGENTS.md` and record the result:
   ```
   .\.venv\Scripts\python.exe -m unittest discover -s tests -t .
   .\.venv\Scripts\python.exe -m ruff check src/ tests/
   ```
3. Record the size of each profile folder. Reuse this PowerShell command:
   ```powershell
   Get-ChildItem -Force -Directory -Filter '.*profile*' | ForEach-Object {
     $s = (Get-ChildItem $_.FullName -Recurse -Force -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum
     [pscustomobject]@{ Name = $_.Name; MB = [math]::Round($s / 1MB, 1) }
   } | Sort-Object MB -Descending
   ```
4. Profile-in-use check (use it again before every move or delete):
   ```powershell
   Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" |
     Where-Object { $_.CommandLine -match 'profile' } | Select-Object ProcessId, CommandLine
   ```
   An empty result means no scraper browser is open.

**Check:** tests and ruff pass, or pre-existing failures are recorded, and the baseline sizes are saved in the phase notes.

## Phase 1: One launch function per provider

`src/skyscanner.py` builds its browser context inline (around line 550), duplicating `launch_skyscanner_context` in `src/browser/skyscanner.py`. The arguments are identical, apart from the `try/except` around `add_cookies`. Later phases change launch options, so there must be exactly one place to change them per provider.

1. In `src/skyscanner.py` `run()`, replace the inline `playwright.chromium.launch_persistent_context(...)` and `add_cookies` call with:
   ```python
   context = await launch_skyscanner_context(
       playwright, profile_dir, executable_path=browser_executable
   )
   ```
   Import it from `src.browser.skyscanner`. That module is already imported there around lines 34 and 40. Drop the `SKYSCANNER_COOKIES` import if it is no longer used.
2. Google Flights already goes through `browser.launch_google_context`. No change is needed.

**Check:** tests and ruff pass. `rg "launch_persistent_context" src` shows only `src/browser/session.py` and `src/browser/skyscanner.py`.

## Phase 2: Move profiles under `var/profiles/`

1. `src/config.py`:
   - Add `PROFILES_DIR = Path("var") / "profiles"` near `PROJECT_ROOT`.
   - Change the defaults to `ScraperConfig.profile_dir = "var/profiles/google"` and `SkyscannerConfig.profile_dir = "var/profiles/skyscanner"`.
   - Change the fallback strings in `load_config()` to the same two values (the `gf_data.get("profile_dir", ...)` and `ss_data.get("profile_dir", ...)` calls, around lines 262 and 272).
   - `resolved_profile_dir()` stays the same. It already resolves relative to `PROJECT_ROOT`.
2. `config/config.toml`: set `profile_dir = "var/profiles/google"` and `profile_dir = "var/profiles/skyscanner"`, and update the comment above each.
3. The scrapers already call `profile_dir.mkdir(parents=True, exist_ok=True)`, so `var/profiles/` is created on first use.
4. One-time migration. Keep the existing saved state; don't start fresh:
   - Run the in-use check from Phase 0. Stop if anything is running.
   - `New-Item -ItemType Directory -Force var\profiles`
   - `Move-Item -LiteralPath .browser-profile-daily -Destination var\profiles\google`
   - `Move-Item -LiteralPath .skyscanner-profile-stealth -Destination var\profiles\skyscanner`
   - This is a rename on the same drive. Chrome's encrypted cookies are tied to the Windows user account and the `Local State` file inside the folder, not to the folder path, so they keep working after the move. To undo, move the folders back.
5. Tests in `tests/test_config.py`:
   - Assert that the shipped config resolves both profiles under `PROJECT_ROOT / "var" / "profiles"`.
   - Assert that the built-in defaults, loaded from a missing config file, are `var/profiles/google` and `var/profiles/skyscanner`.
   - Keep the existing separate-profiles test.

**Check:** tests and ruff pass. Run `.\.venv\Scripts\python.exe run_all.py --trip HEL_HAN.toml --no-skip-existing`. Both scrapers must report observations at about the same rate as the last run in `flight_results/logs/`. Skyscanner must not show more bot-check pages than before. If Skyscanner gets blocked where it wasn't before, move the folder back, restore the old `profile_dir`, and report to the user.

## Phase 3: Limit profile growth

Chrome's HTTP and code cache is what makes profiles large. It holds no login or bot-check state.

1. In both launch functions (`launch_google_context` in `src/browser/session.py` and `launch_skyscanner_context` in `src/browser/skyscanner.py`), add `"--disk-cache-size=52428800"` (50 MB) to `args`. Define it once as a module constant in `src/browser/session.py` and import it in `skyscanner.py`.
2. Add a pure helper to `src/common.py`, which must stay browser-free:
   ```python
   PRUNABLE_PROFILE_SUBDIRS = (
       "Default/Cache", "Default/Code Cache", "Default/GPUCache",
       "GrShaderCache", "ShaderCache",
   )

   def prunable_cache_dirs(profile_dir: Path) -> list[Path]:
       """Cache folders inside a Chromium profile that hold no session or consent state."""
       return [profile_dir / sub for sub in PRUNABLE_PROFILE_SUBDIRS if (profile_dir / sub).is_dir()]
   ```
   Never include `Default/Network` (cookies), `Local Storage`, `IndexedDB`, `Session Storage`, `Service Worker`, `Preferences` or `Local State`.
3. Add `prune_cache: bool = True` to `ScraperConfig`, parse it in `load_config()` for both sections, and document it in `config/config.toml`.
4. In each scraper's `run()`, after the `async with async_playwright()` block exits (the browser is closed), and only if `prune_cache` is on, delete each path from `prunable_cache_dirs(profile_dir)` using `shutil.rmtree(path, ignore_errors=True)`. Print one line with the freed size.
5. Tests in `tests/test_common.py`, using a temporary directory:
   - `prunable_cache_dirs` returns only the listed subfolders that exist.
   - It never returns `Default/Network` or `Default/Local Storage`, even when they exist.
   - Config parsing of `prune_cache` works for true, false and missing.

**Check:** tests and ruff pass. After one daily run, `var/profiles/skyscanner` is well under 100 MB and Skyscanner success is unchanged from Phase 2.

## Phase 4: Optional temporary profile for Google Flights

Try this only after Phases 1 to 3 are stable. It removes the Google profile folder completely.

1. Add `profile_mode: str = "persistent"` to `GoogleFlightsConfig`. The allowed values are `"persistent"` and `"ephemeral"`. Parse it in `load_config()`. In `validate_config()`, add an issue for any other value.
2. In `src/google_flights.py` `run()`, when the mode is `"ephemeral"`, pass `""` as the profile directory to `launch_google_context` and skip `profile_dir.mkdir` and cache pruning. With an empty user-data directory, Playwright and Patchright create a temporary profile and delete it when the browser closes. The consent cookies are still added by `launch_google_context`.
3. Print which mode is active at startup, next to the existing "Using Patchright" line.
4. Tests: parsing and validation of `profile_mode`, with the default `"persistent"`.
5. Test run. Keep `"persistent"` in `config/config.toml` while testing and pass the setting through a temporary config copy:
   - Run Google Flights alone twice with `--no-skip-existing` on `HEL_HAN.toml`: once persistent and once ephemeral.
   - Compare the status counts (observed, blocked, empty, timed out) and the cheapest price per date pair in the two daily reports.

**Check:** the ephemeral run's observed count is within 1 pair of the persistent run, with no new consent or bot-check pages. If so, report the result to the user and ask before changing the default in `config/config.toml`. If not, leave `"persistent"` as the default and record what went wrong in this file.

Do not add an ephemeral mode for Skyscanner.

## Phase 5: Experiment scripts

1. `experiments/pos/multi_route_parallel_orchestrator.py` (around line 156): change `ROOT / f".browser-profile-{route_tag}"` to `ROOT / "var" / "profiles" / f"google-{route_tag}"`. Parallel routes still need separate folders.
2. `experiments/pos/all_pos_scanner.py` and `experiments/pos/regional_study_scanner.py`: change `DEFAULT_PROFILE_DIR = ROOT / ".browser-profile"` to `ROOT / "var" / "profiles" / "google-study"`. Don't share the daily Google profile, because a study run at the same time as the daily run would fight over the profile lock.
3. Don't change any other experiment behaviour.

**Check:** `.\.venv\Scripts\python.exe -m ruff check experiments/pos/` shows no new findings on the changed lines, and `.\.venv\Scripts\python.exe -m py_compile` succeeds on the three changed files. `experiments/` has no `__init__.py` and the scripts import Patchright, so use a compile check instead of an import check.

## Phase 6: Remove unused folders (user approval required)

1. Run the in-use check from Phase 0.
2. Build the candidate list: every root-level `.browser-profile*` and `.skyscanner-profile*` folder still present after Phase 2. Show the user each folder name, its size, its last-modified date and the total.
3. Note that the per-route folders (`.browser-profile-AMS_HEL`, `-HEL_BRU`, `-PHL_HAN`, `-SIN_HAN`) and `.browser-profile` hold experiment state. Deleting them only means the next experiment run starts with a fresh profile.
4. Ask the user to approve the list. Delete only the approved folders, one at a time by exact name, with `Remove-Item -LiteralPath <name> -Recurse -Force`.
5. Keep the old patterns in `.gitignore` (`.browser-profile/`, `.browser-profile*/`, `.skyscanner-profile*/`) so any leftovers stay ignored.

**Check:** only `var/profiles/` holds profiles. `run_all.py --trip HEL_HAN.toml` still works.

## Phase 7: Documentation

- `README.md` around line 110: replace `.browser-profile` and `.skyscanner-profile` with `var/profiles/google` and `var/profiles/skyscanner`. Mention cache pruning and, if adopted, the Google `profile_mode`.
- `DAILY_RUN_GUIDE.md` lines 17-19: same path update.
- `config/config.toml`: comments for `profile_dir`, `prune_cache` and `profile_mode`.
- `docs/REBUILD_PLAN.md`: no change needed. `var/profiles/<provider>/` already matches its layout.
- At the end of this file, add a short "Outcome" section with the before and after sizes, and whether Phase 4 was adopted.

## Final validation

```
.\.venv\Scripts\python.exe -m unittest discover -s tests -t .
.\.venv\Scripts\python.exe -m ruff check src/ tests/
.\.venv\Scripts\python.exe run_all.py --trip HEL_HAN.toml
```

Report which checks passed, which failed and which were skipped. Include the profile sizes before and after, and any phase that was rolled back.

## Outcome (2026-09-27)

- **Before:** 45 profile folders in the project root, about 2.5 GB.
- **After:** 1 profile folder: `.skyscanner-profile-stealth` (677 MB before its
  first post-change run; pruning shrinks it from there). Google Flights keeps
  no profile at all. 43 unused folders (1.76 GB) were deleted with user
  approval.
- **Phase 2 partially rolled back.** Moving the Skyscanner profile to
  `var/profiles/skyscanner` coincided with a spike in anti-bot challenges
  (11 vs 2-4 on the previous two runs) and one manually solved Press & Hold.
  Per this plan's fallback, the profile was moved back to
  `.skyscanner-profile-stealth` and its `profile_dir` restored. The Google
  Flights move to `var/profiles/google` was kept (50/50 observed, baseline
  runtime).
- **Phase 3 adopted.** Both launchers cap the disk cache at 50 MB and both
  scrapers prune disposable cache folders after each run
  (`prune_cache = true` by default). First pruning freed 46.4 MB from the
  Google profile.
- **Phase 4 adopted.** Ephemeral vs persistent A/B on HEL_HAN: both 50/50
  observed, no consent walls or bot-check pages, prices within normal
  day-to-day volatility, same runtime (~425s vs ~430s). The user approved
  making `profile_mode = "ephemeral"` the default; `var/profiles/google` was
  deleted. The Google move to `var/profiles/` from Phase 2 is therefore moot
  but harmless: it remains the `profile_dir` used if `persistent` is ever
  re-enabled.
- **Phase 5 done.** Experiment scripts now write profiles under
  `var/profiles/` (`google-{route}`, `google-study`).
- **Phase 6 done.** 43 root-level profile folders deleted by exact name after
  user approval. `.gitignore` patterns kept.
- **Note for future runs:** TOML files must be written without a BOM;
  PowerShell 5.1 `Set-Content -Encoding utf8` adds one and `tomllib` rejects
  it.
