# Speed and Parallelism Plan

Execution plan for cutting the daily 25-pair run (`run_all.py --trip HEL_HAN.toml`) from about 14 minutes to 3–5 minutes. It works by removing idle time, running several browser pages per provider at once, and measuring how fast each site can be queried before it starts blocking.

Written for an agent to execute phase by phase. Each phase ends with a check; do not start the next phase until the check passes.

**Prerequisite:** complete Phases 1–3 of `docs/BROWSER_PROFILE_PLAN.md` first. Phase 1 there gives each provider a single launch function, which this plan changes. This plan must not create extra profile folders: all parallel pages of a provider share one browser and one profile.

## Where the time goes today

Measured from `flight_results/logs/run_google_flights_2026-09-26.log` and `flight_results_skyscanner/logs/run_skyscanner_2026-09-2{5,6}.log`:

| Provider | Queries | Work per query | Idle per query | Run time |
|---|---|---|---|---|
| Google Flights | 50 (25 pairs × FI, SE markets) | ~5.3 s, of which 3.0 s is a fixed sleep (`settle_ms=3_000` in `wait_for_results`, `src/browser/google.py`) | 3 s `delay_seconds` | 418 s (7.0 min) |
| Skyscanner | 25 | ~17 s (page load + waiting for all providers) | 12 s `delay_seconds` + 1–3 s jitter | 808–827 s (13.5–14 min) |

- `run_all.py` already runs the two providers in parallel, so the total run time is Skyscanner's (~14 min).
- Skyscanner's first 2–3 pairs of every run hit the bot check and cost an extra 60–80 s. Stage 2 of `_handle_challenge` (`src/skyscanner.py`) calls `reset_session(scrub_cookies=True)`, and `reset_session` (`src/browser/skyscanner.py`) calls `context.clear_cookies()`. That deletes the check's clearance cookie, so the next run starts without it. This is a hypothesis; Phase 2 tests it.
- About 42% of Skyscanner's run and 36% of Google's run is intentional idle time.
- Both scrapers are strictly sequential: one page, one query at a time (`for index, (departure, return_date) in enumerate(pairs)` in each `run()`).
- Machine: 16 logical CPUs, 15.7 GB RAM, ~4.7 GB free with normal apps open. A Chromium page on these sites uses roughly 250–500 MB, so start with a budget of about 6 pages in total across both providers.

## Target arithmetic

Rough estimate: run time ≈ `queries × (work + delay) / workers` + ~20 s browser start-up.

| Provider | Setting | Estimate |
|---|---|---|
| Google | 1 worker, settle removed (~2.5 s work), 1 s delay | 50 × 3.5 = 175 s |
| Google | 2 workers (one per market) | ~90 s |
| Skyscanner | 1 worker, 4 s delay | 25 × 21 = 525 s |
| Skyscanner | 2 workers, 6 s delay | ~290 s (≈5 min) |
| Skyscanner | 3 workers, 6 s delay | ~195 s |
| Skyscanner | 4 workers, 6 s delay | ~145 s (≈3 min) |

Google can very likely finish in under 3 minutes. Skyscanner sets the total. 5 minutes needs about 2–3 Skyscanner workers. 3 minutes needs about 4 workers and almost no bot checks. That is roughly 5× today's request rate, and it may not be reachable without blocks. The speed test in Phase 4 decides; don't assume either target.

## Guardrails

- **Stop at the first hard block.** Never retry through blocks, and never add proxies, IP rotation, fingerprint spoofing or new evasion code. Speed comes only from less idle time, parallel pages and measured pacing.
- **Google blocks are IP-wide.** A Google "unusual traffic" page can also affect the user's normal Google use on this network for a while. If it appears during a test, stop all Google testing for the day.
- **Pacing:** run the speed test at most once per provider per day. Leave at least 60 minutes after a normal daily run before starting a test.
- **Defaults:** `workers = 1` keeps today's behaviour, and stays the default until Phase 5 adopts a tested value.
- **Profiles:** one browser and one profile per provider. Parallel work uses extra pages in the same browser context, never extra profile folders.
- **Tests stay browser-free** (`AGENTS.md`). New pure logic goes in a new `src/pacing.py`. Add it to the browser-free list in `AGENTS.md`. Code that drives pages stays in `src/google_flights.py`, `src/skyscanner.py` and `src/browser/`.
- **Keep what already works:** `skip_existing` resume, per-pair JSON files, the daily report format, the runtime budget, the circuit breaker, the deferred observations and exit codes.
- Validate every phase with:
  ```
  .\.venv\Scripts\python.exe -m unittest discover -s tests -t .
  .\.venv\Scripts\python.exe -m ruff check src/ tests/
  ```

## Phase 0: Baseline

1. Record `git status --short`, test results and ruff results.
2. From the latest two daily logs, record for each provider: run time, observed count, p50 and p90 of `query_elapsed_seconds`, and the number of "Anti-bot challenge detected" lines. The Phase 4 speed test compares against these numbers.

**Check:** the baseline numbers are written at the bottom of this file under "Outcome".

## Phase 1: Time each step of a query

You can't tune what you can't see. Record where each query's time goes.

1. Create `src/pacing.py` (pure, browser-free) with:
   - `class StepTimer`: `start(name)` / `stop(name)`, or a context-manager method `step(name)`, using `time.monotonic()`, plus `as_dict() -> dict[str, float]` (seconds, rounded to 2 decimals).
   - `summarize_run(observations: list[dict], wall_seconds: float) -> dict`: returns count, observed, blocked (`blocked` or `user_action_required`), challenges (sum of `challenge_count`), p50 and p90 of `query_elapsed_seconds`, queries per minute, and wall seconds.
   - `project_run_seconds(queries: int, workers: int, per_query_s: float, delay_s: float, startup_s: float = 20.0) -> float`: the estimate from the table above.
2. `src/google_flights.py` `scan_pair`: time the steps `navigate`, `consent`, `wait_results`, `reload_retry`, `extract` and `cheapest_tab`. Store them as `observation["timings"]`.
3. `src/skyscanner.py` `scan_pair`: time `navigate`, `consent`, `challenge`, `initial_wait`, `completion_wait` and `extract`. Count challenges in `_handle_challenge`: return the count and seconds alongside the page text, or pass in a small mutable stats dict. Store them as `observation["challenge_count"]` and `observation["challenge_seconds"]`.
4. At the end of both `run()` functions, print `summarize_run(...)` on one line next to the existing "Run summary" line.
5. Tests, in a new `tests/test_pacing.py`: `StepTimer` using a fake clock (patch `time.monotonic`), `summarize_run` on hand-made observations, and `project_run_seconds` against the table values.
6. `DAILY_REPORT_FIELDS` and `SKYSCANNER_REPORT_FIELDS` stay unchanged. The new fields only go in the per-pair JSON files.

**Check:** tests and ruff pass. One normal run (`run_all.py --trip HEL_HAN.toml --no-skip-existing`) produces `timings` in the pair files, and its total time is within 5% of the baseline.

## Phase 2: Single-worker speed-ups (no extra pages)

Each change has its own config setting, so the Phase 4 speed test can compare old and new behaviour.

1. **Google: replace the fixed 3 s wait.** In `src/browser/google.py` `wait_for_results`, keep the existing `_RESULTS_READY_JS` wait. Replace `page.wait_for_timeout(settle_ms)` with a stability check: read the number of result cards and the text of the Cheapest tab (the same source `read_cheapest_tab_text` uses) every 400 ms. Return when two readings in a row match, or after `settle_ms` at most. Add `[google_flights] settle_mode = "fixed" | "stable"`, defaulting to `"fixed"` until Phase 4 confirms it.
2. **Allow fractional delays.** In `src/config.py` and both `parser()` functions, change `delay_seconds` from `int` to `float`. Update `validate_config`: Skyscanner's 2 s minimum stays, and Google delays below 0.5 s produce a warning.
3. **Skyscanner: test keeping the clearance cookie.** Add `[skyscanner] challenge_scrub_cookies = true` (today's behaviour). When it is false, Stage 2 of `_handle_challenge` and the retry and sweep paths in `run()` call `reset_session(page, scrub_cookies=False)`. That still warms up on the homepage but keeps the cookies. Expected effect, still to be confirmed: fewer challenges at the start of the next run. This also agrees with `docs/REBUILD_PLAN.md` section 4, which says to stop clearing cookies as the default recovery.
4. **Background-page throttling.** In both launch functions, add `--disable-background-timer-throttling`, `--disable-renderer-backgrounding` and `--disable-backgrounding-occluded-windows` to `args`. Chromium otherwise slows pages that aren't in front, which would make the parallel pages in Phase 3 look slower than they really are.
5. **Tests:** config parsing for `settle_mode`, float `delay_seconds` and `challenge_scrub_cookies`, and the new `validate_config` messages.

**Check:** tests and ruff pass. Run Google once with `settle_mode = "stable"` and `delay_seconds = 1`, using a temporary config copy, not the shipped `config.toml`. It must observe 50/50. Its prices must match a run made just before it with today's settings: the median difference must be at most €5, and at least 80% of pairs must be within €10. Keep the faster settings only if they pass.

## Phase 3: Parallel workers per provider

Several pages in one browser context per provider, fed from a shared job queue.

### Config (`src/config.py`, `config/config.toml`)

Add these fields to `ScraperConfig` (both providers) and parse them in `load_config()`:

- `workers: int = 1` — the number of pages that run at the same time.
- `start_interval_seconds: float = 0.0` — the minimum gap between the start of any two queries of this provider, across all its workers. This caps the provider's total request rate, whatever the worker count. `delay_seconds` stays as the per-worker pause after each query.
- `max_workers_total: int = 6`, in `[execution]` — a memory guard. `validate_config` warns if `google_flights.workers + skyscanner.workers` is larger (only providers that are enabled count).

### Pure logic in `src/pacing.py` (unit-tested)

- `Job` dataclass with `gl: str | None`, `departure: date`, `return_date: date | None`. Add `build_jobs(pairs, gl_list) -> list[Job]` that interleaves markets (FI pair 1, SE pair 1, FI pair 2, …), so each market gets results early and a block affects both markets evenly.
- `StartGate`: an asyncio-based limiter that enforces `start_interval_seconds` across workers (`async def wait()`). Accept an injectable clock and sleep function so tests don't sleep for real.
- `SharedBreaker`: wraps `circuit_breaker_tripped` with a shared consecutive-block counter and a `tripped` flag that all workers check before taking a job.
- `AdaptivePacer`: after a challenge or block, multiply the interval by 1.5 and pause one worker. After 10 successful queries in a row, step back towards the configured values, never going faster than them. Pure state methods: `on_success()`, `on_challenge()`, `current_interval`, `active_workers`.

### Google Flights (`src/google_flights.py` `run()`)

1. Launch one context as today, then open `workers` pages (`context.pages[0]`, then `await context.new_page()`).
2. Build the jobs with `build_jobs(pairs, gl_list)`. Resolve cached jobs first (`cached_observation`) and put only the remaining jobs on an `asyncio.Queue`.
3. Each worker loops: check the deadline and the breaker, `await gate.wait()`, then call the existing `scan_pair(page, …, gl=job.gl)` without changes. Store the result, then `await page.wait_for_timeout(delay)`.
4. Start the workers `start_interval_seconds` apart (or 1 s if that is 0) so they don't all load at once.
5. **One writer.** Keep observations in a dict keyed by `(gl, departure, return_date)`. After each result, take an `asyncio.Lock`, call `save_observation` in that market's results folder (`resolved_results_dir_for_gl`), and rewrite that market's daily report. `write_daily_report` already sorts rows by date, so finishing out of order is fine.
6. If the deadline passes or the breaker trips: stop taking jobs, let in-flight queries finish, then save the remaining jobs as `deferred_observation(...)` with the same `reason` values as today. Keep the exit codes as they are.
7. With `workers = 1` and `start_interval_seconds = 0`, the behaviour and console output must match today's, apart from the added timing fields.

### Skyscanner (`src/skyscanner.py` `run()`)

Same pool structure, plus challenge coordination, because all pages share one set of cookies:

1. **Challenge lock.** Share an `asyncio.Lock` and a "challenge in progress" `asyncio.Event` across workers. When `_handle_challenge` detects a challenge, the worker takes the lock and sets the event. The other workers then wait before their next navigation, but finish their current query. The worker calls `page.bring_to_front()` before `try_solve_press_and_hold` (press-and-hold needs real input on a visible page), solves, then clears the event.
2. **Clearing cookies while others run.** If `challenge_scrub_cookies` is true, clear cookies only after the other workers are idle, because `context.clear_cookies()` affects every page. Make `workers > 1` with `challenge_scrub_cookies = true` a `validate_config` warning, and recommend `false` for parallel runs.
3. **Retries.** The inner `while attempt <= max_retries` loop's cool-off (`15 + attempt * 5` s) becomes a delayed re-queue: the job goes back on the queue with a not-before time. This keeps a worker from sitting idle.
4. **Second-pass sweep.** Keep it after the pool drains, as today, but run it on a single worker with today's 5–8 s pauses.
5. Keep today's circuit-breaker threshold (5), shared across workers through `SharedBreaker`.

### Tests (`tests/test_pacing.py`, `tests/test_config.py`)

- `build_jobs` ordering and counts (25 pairs × 2 markets = 50 jobs, interleaved; one-way pairs keep `return_date=None`).
- `StartGate` spacing with a fake clock: 3 workers and a 5 s interval give query starts at 0, 5 and 10 s.
- `SharedBreaker` trips at the threshold, and a success resets the count.
- `AdaptivePacer` backs off, then recovers, and never goes below the configured minimum.
- Config: `workers`, `start_interval_seconds` and `max_workers_total` parse with their defaults, and the new `validate_config` warnings appear.

**Check:** tests and ruff pass. With `workers = 1` for both providers, one daily run gives the same observed counts as the baseline (±1) and the same console format. Then run once with Google at `workers = 2` and Skyscanner at `workers = 1`. It must observe 50/50 for Google with no blocks.

## Phase 4: Speed test (find the fastest safe setting)

A separate command that steps up speed until a provider shows signs of blocking, then reports the last safe setting.

### Command

New browser-driving module `src/speed_probe.py`, run as `python -m src.speed_probe`:

```
.\.venv\Scripts\python.exe -m src.speed_probe --provider google --trip HEL_HAN.toml
.\.venv\Scripts\python.exe -m src.speed_probe --provider skyscanner --trip HEL_HAN.toml
```

- It reuses the worker pool from Phase 3. Move the pool out of `run()` into a function, for example `run_jobs(context, jobs, *, workers, start_interval, delay, …) -> list[dict]` in each scraper module, so the probe and the daily run share it.
- Results go to `var/tuning/<provider>/<YYYY-MM-DD_HHMM>/`. It never writes to the daily result folders and always runs with `skip_existing` off.
- Options: `--levels` (a comma-separated subset), `--queries-per-level`, `--rest-seconds`, `--dry-run` (print the levels and estimated times without opening a browser).

### Speed levels

Each level is `(workers, delay_seconds, start_interval_seconds)`. Every level runs the same fixed set of queries: the first N jobs from `build_jobs`, so markets are mixed.

Google, 12 queries per level, 60 s rest between levels:

| Level | Workers | Delay | Interval | Notes |
|---|---|---|---|---|
| G0 | 1 | 3.0 | 0 | today's settings, `settle_mode = "fixed"` (baseline) |
| G1 | 1 | 1.0 | 0 | `settle_mode = "stable"` from here on |
| G2 | 2 | 1.0 | 1.0 | |
| G3 | 3 | 1.0 | 1.0 | |
| G4 | 4 | 0.5 | 0.5 | |
| G5 | 6 | 0.5 | 0.5 | only if total free RAM stays above 1.5 GB |

Skyscanner, 10 queries per level, 120 s rest between levels, `challenge_scrub_cookies = false` from S1:

| Level | Workers | Delay | Interval | Notes |
|---|---|---|---|---|
| S0 | 1 | 12 | 0 | today's settings (baseline) |
| S1 | 1 | 6 | 0 | |
| S2 | 2 | 8 | 4 | |
| S3 | 2 | 5 | 3 | |
| S4 | 3 | 6 | 3 | |
| S5 | 4 | 6 | 2 | |

### Pass criteria (per level, computed in `src/pacing.py`, unit-tested)

A level passes only if all of these hold:

1. No hard blocks: no final `blocked` or `user_action_required` status.
2. Challenges: at most 1 per 10 queries, and every one clears automatically. For Google, any "unusual traffic" page fails the level.
3. At least 90% of queries observed.
4. Prices match the provider's level-0 baseline: the median difference is at most €5, and at least 80% of queries are within €10. This catches a parallel setting that returns early, incomplete results.
5. p90 query time is at most 2× the baseline p90. A rising p90 is an early sign of throttling.
6. Free system memory stays above 1.5 GB. Sample it every 5 s with `psutil`. psutil is not installed today: add it to `requirements.txt` as optional and skip this check with a warning when it is missing.

Add `evaluate_level(level_obs, baseline_obs, resource_samples) -> LevelResult` (pass or fail plus reasons) and `recommend(levels) -> Level | None` (the last level that passed before the first failure) to `src/pacing.py`, with tests for each failure reason.

### Stop rules

- On the first failing level, stop that provider's test immediately. Don't run faster levels and don't retry.
- If the failure was a block or challenge (criteria 1–2), wait at least 24 hours before testing that provider again.
- A failure caused only by prices, time or memory (criteria 3–6) may be rerun once on another day, to rule out noise.
- `--provider all` runs Google and Skyscanner at the same time, level by level, to test total memory use. Run it only after each provider has passed on its own.

### Output

- `summary.json` holding, for each level, the settings, `summarize_run` output, `LevelResult`, and projected full-run time (`project_run_seconds` for 50 Google or 25 Skyscanner queries).
- A console table with one row per level: `level | workers | delay | interval | wall s | q/min | p50 | p90 | observed | challenges | blocks | price match | projected full run | PASS/FAIL (reason)`.
- A final line such as `Recommended: S3 (2 workers, 5 s delay, 3 s interval) → projected 25-pair run 4.6 min`.

### Tests

`tests/test_pacing.py` covers `evaluate_level`, `recommend` and the level tables. The probe module itself is browser-driven and not unit-tested. Its `--dry-run` path must work without opening a browser, and a smoke test of the arguments can call a pure `plan_levels(args)` helper.

**Check:** tests and ruff pass. `--dry-run` prints both level tables with estimated times. One real Google test completes and writes `summary.json`. Report the table to the user before running the Skyscanner test.

## Phase 5: Adopt the tested settings

1. For each provider, take the recommended level. For daily use, pick **one level below** the recommendation: a test with 10–12 queries is shorter than a full run, and block thresholds change with time of day.
2. Confirm with two normal daily runs (`run_all.py --trip HEL_HAN.toml --no-skip-existing`) on different days. Each must observe everything, with no blocks, and the total time must match the projection within 25%.
3. Only then write the values into `config/config.toml` (`workers`, `delay_seconds`, `start_interval_seconds`, `settle_mode`, `challenge_scrub_cookies`). Enable `AdaptivePacer` in the daily run so that a bad day slows down gradually instead of tripping the circuit breaker.
4. Report the before and after times to the user, and ask before changing the defaults if either confirmation run hit a block.

**Check:** two confirmation runs pass, and the new values and results are recorded under "Outcome".

## Phase 6: Documentation

- `README.md` and `DAILY_RUN_GUIDE.md`: describe `workers`, `start_interval_seconds` and `settle_mode`, how to run the speed test, and the block warnings in Guardrails.
- `config/config.toml`: comments for each new setting, stating the default and the tested value.
- `AGENTS.md`: add `src/pacing.py` to the browser-free modules list.
- At the end of this file, fill in "Outcome": the baseline, each level result, the adopted settings and the run times before and after.

## Final validation

```
.\.venv\Scripts\python.exe -m unittest discover -s tests -t .
.\.venv\Scripts\python.exe -m ruff check src/ tests/
.\.venv\Scripts\python.exe -m src.speed_probe --provider google --dry-run
.\.venv\Scripts\python.exe run_all.py --trip HEL_HAN.toml --no-skip-existing
```

Report which checks passed, which failed and which were skipped. Include the measured total run time against the 3- and 5-minute targets, and the level each provider stopped at, with the reason.

## Outcome

_To be filled in by the executing agent._
