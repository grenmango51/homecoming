# Flight scraper rebuild: implementation handoff

## Objective and scope

Rebuild the existing project around one reliable workflow: open an exact flight date pair in Google Flights and Skyscanner, verify the displayed search, wait for evidence that the provider has finished its search, and only then accept the displayed fare. Compare equivalent searches and preserve enough evidence to explain every price.

This is an implementation brief for the next agent. The planning session inspected code, tests, sync-conflict variants, and saved logs; it did not run a new live scan or alter application code. Current live selectors, response schemas, and completion signals remain unverified. Do not substitute guesses for that discovery work.

Start with HEL–HAN, 2026-12-09 to 2027-01-07, one adult, economy, round trip, EUR, Finland market. Keep exact/range/window date generation, but every generated pair must become its own exact search. First prove one pair per provider, then several pairs, then the configured full scan. Preserve configured market variants as separate queries; defer broad point-of-sale studies.

The supplied screenshot shows a selected Cheapest tab advertising €845 while the visible cards start at €941 and the list still says “Sorted by top flights.” This demonstrates why tab selection, list sorting, headline fares, and itinerary prices must be distinguished. It does not establish search completion or guarantee that €845 is available now.

## Findings from the repository

1. **Google readiness is too permissive.** `src/browser.py::_RESULTS_READY_JS` accepts words such as “cheapest” and absence of two loading phrases. `wait_for_results()` swallows errors and adds a fixed 2.5-second wait. `src/google_flights.py::scan_pair()` does not call the existing `switch_to_cheapest_tab()` helper; sorting trusts a URL parameter in some cases. `src/google_parse.py::make_observation()` can promote incomplete results to observed merely because prices exist.
2. **Skyscanner's completion result is not enforced.** `src/skyscanner.py::scan_pair()` races a DOM waiter against an event but never inspects the completed task's result or exception. A timeout or a returned `challenge` can therefore lead into extraction. A generic card with no skeletons also qualifies without proof a search finished. After challenge recovery, navigation can proceed to extraction without running the full completion gate again.
3. **Response data is not scoped tightly enough.** The Skyscanner listener accepts broad `graphql`/`web-unified-search` responses without validating search identity. Captured payloads are accumulated and minimized across time rather than reconciled into a final search snapshot. Retries within an attempt retain the collection. There is no saved completion evidence.
4. **Cheap fares can be discarded or unrelated prices accepted.** Google has a €50 floor; Skyscanner uses €200 in both parsing and DOM extraction. Both can fall back to money found anywhere in page text. Skyscanner labels numeric response prices EUR without proving their currency. Headline, card, and network prices are collapsed into a single minimum.
5. **Storage and comparisons need stronger identity.** Daily checkpoint filenames contain only the dates. Some cache readers check route/market, but files can still overwrite another query. Comparisons join on dates and accept non-null prices without requiring completed status. UTC timestamp prefixes are compared with a local-day string for cache freshness.
6. **Operational success is misleading.** Skyscanner returns zero after writing reports even if searches failed. `run_all.py` can return zero after partial provider failure or a caught comparison failure. `poll_wait_seconds` is passed through but unused inside `scan_pair()`. Repeated resets, nested retries, and a final retry sweep complicate recovery.
7. **Cleanup must preserve divergent work.** There are 22 source/config/documentation sync-conflict files outside generated results. Some contain substantial differences; one wrapper is identical. They are untracked, as are root analyzer copies and two old test files. `live-captions/` is an unrelated untracked project. Do not blindly delete or commit these.
8. **Useful foundations already exist.** Pure parsing, date helpers, TOML configuration, report writers, and the two-provider runner are reusable. The root `find_flights*.py` and `compare_flights.py` files are already thin compatibility wrappers; they are not significant duplicated implementations.

Verification on 2026-09-20:

- `python -m unittest discover -s tests -t .`, using `.venv/Scripts/python.exe`: 124 tests, 10 errors, one failure. The failures occur in `tests/test_find_flights.py` and `tests/test_skyscanner.py`, which import old helper locations; the URL assertion also disagrees with the current URL builder.
- Explicitly running the seven current test modules (`analysis`, `common`, `compare_flights`, `config`, `google_parse`, `reporting`, `skyscanner_parse`): 104 tests pass.
- `.venv/Scripts/python.exe -m ruff check .`: unavailable because Ruff is not installed. No Ruff executable was found on PATH. Lint cleanliness is unknown.
- The saved Skyscanner report for 2026-09-20 labels 21 of 25 pairs observed. Logs show repeated verification timeouts and session resets. These labels do not prove completion under the stricter proposed contract.

## Required design

### 1. Discover the real completion contract before implementing it

Build a small opt-in diagnostic mode using the existing browser setup. Capture timestamped state transitions, result-region DOM snapshots, screenshots, and relevant browser-generated response metadata/payloads for a single exact search. Register listeners before triggering the search. Keep credentials, cookies, tokens, and personal information out of committed fixtures.

Inspect both the interface and network progression from initial search through partial results to final results. Identify the actual provider-specific completion indicator and show which search it belongs to. Test delayed results and navigation from one date pair to another. Record selectors, response fields, sample evidence, and limitations in `docs/provider-signals.md`.

For Skyscanner, investigate its provider-progress indicator and terminal search response. The partner API documents incremental results and `RESULT_STATUS_COMPLETE`, but that is **not evidence that the consumer website uses the same schema**. The current code only recognizes COMPLETE/COMPLETED/FINISHED; determine the actual website values from captures.

For Google, investigate observable loading transitions, result updates, and relevant response lifecycle. Do not assume Google has an “all airlines checked” signal, or that it uses Cloudflare. If no reliable terminal signal can be demonstrated, expose `completion_unverified`; a settled-looking page must not silently become complete. Document that limitation before claiming the strict requirement is met.

Neither a fixed sleep, `networkidle`, a price appearing, nor a stable price for several seconds establishes complete provider coverage. Stability can be an extra rendering check after a validated terminal signal. An observed loading-to-hidden transition is only sufficient if discovery demonstrates its meaning for that provider; absence of a loading indicator alone is insufficient.

### 2. Use an explicit search state machine

Use small typed dataclasses/enums, not a general plugin framework:

`navigating -> validating_query -> searching -> provider_complete -> rendering_final_results -> complete | no_results`

Other outcomes: `blocked`, `timeout`, `error`, `query_mismatch`, `completion_unverified`. A challenge may enter an explicitly bounded user-action wait. Recovery must restart query validation and the completion gate with a fresh attempt identity.

The pure state reducer consumes provider events; browser code only gathers events and performs actions. Completion must be scoped to the current query, attempt/navigation generation, and search/session ID where available. Ignore late responses from old searches. Use one monotonic attempt deadline, separate navigation/challenge budgets, and bounded retries. Retrieve task results and exceptions, cancel and await pending tasks, drain tracked response handlers, and remove listeners on every exit.

Only `complete` can produce an accepted fare; `no_results` is a successful terminal search with no fare. Incomplete or blocked observations may retain diagnostic prices separately but cannot participate in cheapest-price rankings or successful-cache reuse.

### 3. Read the exact UI and retain price provenance

- Verify origin, destination, departure/return dates including year, trip type, travelers, cabin, filters, displayed currency, and market. A URL alone is not proof. Use direct URLs when reliable and UI date selection when necessary; remove dependence on a HEL–HAN-only captured protobuf as the universal path.
- Activate Cheapest and assert its active state. If the list must be sorted, assert the rendered sort state too. Recheck completion/render readiness after actions that trigger a new search.
- Extract prices only from verified result elements. Store `headline_price`, `lowest_itinerary_price`, and `accepted_price` separately, with `price_source`, currency, amount basis, timestamp, and linked evidence. Use Decimal or integer minor units.
- A completed, query-verified Cheapest headline is a valid UI headline observation even if no matching visible itinerary is found. Label it `headline_only`; do not invent itinerary details. Attempt bounded expansion/scrolling to locate a matching itinerary and record mismatches explicitly. Preserve the cheaper headline rather than replacing it silently with the first card.
- If return-flight selection is necessary to establish a full itinerary total, implement that read-only selection and record the pricing stage. Do not call a departure suggestion a fully verified booking total.
- Remove arbitrary fare floors and whole-page minimum-price fallbacks. Reject invalid amounts using context, currency, and schema validation. Preserve self-transfer flags for the specific itinerary rather than assigning a page-wide flag to every result.
- Reconcile network deltas by itinerary/offer identity, including updates and removals, if discovery proves the response stream supports this. If semantics cannot be established, use the final rendered UI as the price source and network events only as corroborating completion evidence. Never take the cheapest amount across all historical payloads.

### 4. Make browser access predictable

Treat anti-bot access and result completeness as separate problems. Identify the actual challenge from evidence; PerimeterX-like markers exist in the current Skyscanner code, but their current live behavior needs verification. Google/Cloudflare attribution is unconfirmed.

Use a dedicated persistent visible profile per provider, with a profile lock and no simultaneous owners. Preserve successful verification and consent state. Remove automatic cookie-clearing/reset loops as the default recovery mechanism. Implement clear challenge detection, bounded supervised resolution, conservative pacing, and a provider-level stop after repeated blocks. Do not claim Patchright, browser flags, cookie changes, or retries guarantee a bypass. Initially preserve the existing browser backend behind one explicit configuration point; evaluate any replacement against captured successful runs instead of adding more stealth layers.

The unattended-access requirement remains a feasibility gate: demonstrate it in a controlled smoke run or report it as unresolved. If access stays blocked, record `blocked` and leave the other provider functional. An authorized API can be evaluated as a separately labeled alternative, but must not silently replace the requested UI-derived prices.

### 5. Share orchestration, persistence, and reporting

Keep provider-specific selectors and signal decoders separate. Share the query model, state reducer, retry policy, cache rules, artifact writing, and report generation. Run at most one active search per provider initially; run the two providers concurrently with isolated profiles, as requested by the repository instructions.

Define a query fingerprint from route, dates, trip type, passengers, cabin, filters, market, currency, and provider for storage. Cross-provider comparison uses equivalent search fields excluding provider; label market differences explicitly. Include schema version and run/attempt IDs. Preserve configured FI/SE values without hard-coded country-specific comparison branches.

Write observations atomically. Save one immutable attempt artifact, then update a small successful-cache index. Use explicit UTC timestamps and a defined freshness policy; keep `--no-skip-existing` for live validation. Old `observed` records without completion evidence are historical/unverified, never auto-promoted into the new cache.

Persist observations as work progresses; generate final reports from them and rebuild reports on resume. Avoid rewriting an ever-growing daily CSV/JSON after every pair. Report completed, empty, blocked, timed-out, unverified, and cached counts. Partial data must remain visible without declaring a false winner. Define and propagate exit codes for complete success, partial failure, and fatal failure, including report-generation errors.

## Proposed folder layout

```text
config/
  config.toml
  trips/*.toml
src/
  common.py, config.py, models.py, completion.py
  google_parse.py, skyscanner_parse.py
  reporting.py, analysis.py, runner.py, cli.py
  browser/
    __init__.py
    session.py
    google.py
    skyscanner.py
tests/
  test_*.py                 # browser-free default discovery
  fixtures/google/
  fixtures/skyscanner/
integration_tests/         # separately invoked browser/live tests
scripts/                   # PowerShell launchers and diagnostics
docs/                      # setup, completion evidence, migration notes
experiments/pos/            # isolated legacy regional studies
data/reference/            # region mappings needed by experiments
var/                       # ignored runtime output
  profiles/<provider>/
  runs/<run-id>/<provider>/<query-id>/
  cache/
run_all.py                 # preserve established command
find_flights.py            # temporary compatibility wrapper
find_flights_skyscanner.py # temporary compatibility wrapper
compare_flights.py         # temporary compatibility wrapper
pyproject.toml
requirements.txt
README.md
agents.md
```

Keep the existing pure-module names to avoid a gratuitous import migration. Convert `src/browser.py` into a package whose `__init__.py` temporarily preserves needed exports; all Playwright/Patchright imports and page driving belong there. This preserves the intent of the current agent instruction; update its path wording as part of the migration. Do not place browser imports in parser or state-machine modules.

Move trip files with a compatibility lookup for existing `--trip HEL_HAN.toml` and explicit legacy paths. Update package discovery in `pyproject.toml` so `src.browser` is included, root/path resolution, launchers, documentation, and fixture paths together. Migrate profiles only with browsers closed, preserving old profiles and configured overrides. Do not move/delete the unrelated `live-captions/` project without explicit user direction; scope tooling to the flight project in the meantime.

## Keep, simplify, and retire

- **Keep and strengthen:** date generation and numeric/duration helpers in `common.py`; TOML loader/dataclasses in `config.py`; provider parsing helpers and their tests; report serialization and useful comparison calculations. Rewrite their acceptance rules where this brief conflicts with old tests.
- **Keep the runner concept:** one entry point, isolated providers, streaming logs, automatic comparison. Consolidate shared CLI/config/retry/report loops without forcing both providers to share their UI logic.
- **Replace:** permissive completion heuristics, broad swallowed exceptions, unused polling configuration, duplicate challenge handlers, repeated Cheapest selectors, retry sweeps, whole-page price scraping, and date-only cache identity.
- **Quarantine research:** `all_pos_scanner.py`, `regional_study_scanner.py`, `multi_route_parallel_orchestrator.py`, `multi_route_master_analyzer.py`, `filtered_arbitrage_analyzer.py`, root and `src/` analyzer variants, and region datasets. Compare root/src copies before choosing a canonical version. Keep experiments out of default imports and scans; make pandas/tabulate optional research dependencies if the core no longer needs them.
- **Resolve sync conflicts deliberately:** snapshot tracked and untracked work; compare each conflict against its canonical file; recover unique useful changes; record a disposition manifest. Remove exact duplicates and reconciled files only after preservation. Do not merely hide conflict files in `.gitignore` while treating the cleanup as done.
- **Consolidate tests:** merge unique assertions from the two obsolete test modules into the corresponding pure-module suites; remove duplicate coverage and obsolete imports. Do not make parsers depend on browser modules just to satisfy old tests.
- **Consolidate docs/scripts:** README for setup and the canonical command; focused docs for signals, operations, and migration. Keep compatibility launchers until known scheduled-task references have been checked and updated.

## Implementation order and acceptance gates

1. **Preserve and baseline.** Record git status, back up untracked/conflicting work, classify variants, establish focused lint scope and a development Ruff dependency, and restore browser-free full test discovery. No bulk deletion or `git clean`.
2. **Prove one exact search.** Add diagnostic capture; establish each provider's actual completion and query-validation contract. Save sanitized fixtures. If a provider lacks a trustworthy signal or stays blocked, report that gate as unresolved rather than disguising it with delays.
3. **Implement pure contracts and regression tests.** Add query identity, completion state transitions, observation schema, and accepted-price rules before rewriting browser flows.
4. **Refactor provider drivers and runner.** Move browser code into the package, enforce the completion gate, consolidate lifecycle/retry handling, implement exact UI verification and price provenance. Preserve current entry commands throughout.
5. **Migrate storage and comparisons.** Add atomic writes, complete-only cache reuse, explicit legacy handling, currency/query matching, and truthful exit/report statuses.
6. **Complete folder cleanup.** Move experiments, configs, scripts, and reference data; resolve conflict files; update packaging, docs, and agent instructions; preserve runtime data and unrelated work.
7. **Validate incrementally.** Run one pair per provider with cache bypass, then three pairs including a short-haul low-fare case, then the configured parallel daily scan. Review saved evidence against the displayed final UI. Do not use screenshot prices as immutable expectations for live tests.

Required regression scenarios:

- Expensive partial result followed by a cheaper final result; price extraction cannot succeed early.
- Cards before progress starts; progress hidden before ever appearing; stable partial price; terminal network event before final DOM render; no-results terminal state.
- Timeout, challenge, unresolved verification, recovery navigation, malformed response, unrelated GraphQL response, late previous-query event, response-handler cleanup, and cancellation.
- Cheapest headline €845 versus visible cards €941: preserve both and the mismatch. Acceptance still depends on completion evidence.
- Valid fares below €50 and €200, multiple currencies, ancillary prices, localized number formats, self-transfer provenance, and wrong route/date/year/cabin.
- Same dates on different routes/markets/currencies; stale or incomplete cache; old schema; midnight freshness boundaries; interrupted writes; partial provider failure and comparison errors.
- Parser/config/state/report imports and default unit tests work without Playwright/Patchright installed. Browser integration tests remain separate and opt-in. Unsupported trip modes fail validation rather than silently running a different search.

Final checks: `python -m unittest discover -s tests -t .` and `ruff check .` within the documented flight-project scope, plus separately reported live smoke checks. If a live provider remains blocked or unverified, the final implementation report must say so. A tidy repository alone does not meet the completion requirement.

## Primary references

- [Playwright Page API](https://playwright.dev/python/docs/api/class-page): distinguishes navigation milestones and discourages using network-idle as readiness proof.
- [Playwright network events](https://playwright.dev/python/docs/network): browser response observation mechanics.
- [Skyscanner create-and-poll](https://developers.skyscanner.net/docs/getting-started/create-and-poll) and [API FAQ](https://developers.skyscanner.net/docs/faqs): incremental results and the partner API completion status. Use as background, not as a substitute for inspecting consumer-site evidence.
