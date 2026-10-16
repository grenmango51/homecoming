# Flight Finder handoff — 4 October 2026

The user explicitly asked the current agent to stop and give another agent a continuation prompt. Do not restart Lovable builder prompts: the account ran out of builder credits. Continue directly in code. No work should run flight searches on the laptop for the hosted site.

## Goal and authorization

Preserve the existing Turkish Airlines-inspired UI, two-month date picker, flexible date grid, airport inputs and search controls. Host the owner-only web app publicly, while Google Flights and Skyscanner searches run on the user's separate Ubuntu server. The user selected “Only me, with a login or passcode.” The web form must submit a fixed validated flight-query format, never arbitrary scripts, shell commands, URLs, file paths or executable names. Reuse the existing original scrapers, including completion verification and Skyscanner's existing automatic challenge handler. Never present intermediate or blocked results as verified fares. User authorized server setup, GitHub source and hosting.

The user initially wanted Vercel, then switched to Lovable. Two old Vercel copies are paused and should stay paused. Builder credits are exhausted; existing Lovable hosting/backend and GitHub sync can still be used. Do not consume more builder credits or silently choose a paid upgrade.

## Local workspace and preservation

- Windows workspace: `D:\Hoai Anh\Aalto\Hobbies\Google flights`.
- Main public scraper repository: https://github.com/grenmango51/homecoming, branch `main`, HEAD `5062628`.
- Full recovered Lovable source is **uncommitted** under `hosted/` (107 nonbinary files, environment credentials and Lovable binding file omitted; public owner email sanitized to `owner@example.com`).
- Root `hosted_worker.py` and `tests/test_hosted_worker.py` are **uncommitted** recovered worker + cleaned tests. These must be preserved and eventually committed separately from unrelated work.
- Deployment-connected GitHub repo: https://github.com/grenmango51/flight-finder-pro (private). It was connected successfully through Lovable's existing GitHub integration. Local clone: `var/hosted-connected`, branch `main`, latest observed commit `bff1a61 Add project README`. Read its `AGENTS.md` before editing. Push normal commits to main to sync code back to Lovable, without sending builder messages. No force push.
- No new recovery changes have been committed or pushed yet. Do not assume the server or deployment-connected clone already contains root worker or the latest local test/refactor.

Unrelated existing changes: `config/config.toml`, `run_all.py`, `src/google_flights.py`, `src/skyscanner.py`, `docs/SPEED_TUNING_PLAN.md`, scratch JSON files, scheduled-task helper scripts, `web/.gitignore`. Do not stage, revert or overwrite these. In particular, run_all.py has an unrelated comparison fallback and scraper files have graceful shutdown/performance edits.

Before committing, AGENTS.md requires `.venv\Scripts\python.exe -m unittest discover -s tests -t .` and `.venv\Scripts\python.exe -m ruff check src/ tests/`. Keep test imports browser-free; page driving belongs in `src/browser/`.

## Hosting/backend state

- Lovable project: Flight Finder Pro, ID `48f1fcaf-2cb3-403f-b519-d8f9f40ff700`.
- Editor: https://lovable.dev/projects/48f1fcaf-2cb3-403f-b519-d8f9f40ff700
- Preview: https://id-preview--48f1fcaf-2cb3-403f-b519-d8f9f40ff700.lovable.app
- Production is **not published**. Cloud PostgreSQL/Supabase is provisioned. Owner sign-in is working and database owner checks use the existing verified owner email, not first-user signup. Preserve that deployed allowlist; sanitized public source contains placeholders.
- The user signed in and submitted job `fd56aa61-2eb2-46b0-9800-e5925ad2508c`, departure 2026-12-09, return 2027-01-09, HEL→HAN, ±3, min21. It was queued at 15:45:07 UTC on 4 October and has no fares. It may now be expired by the one-hour queued-job reaper. Inspect before taking action; don't casually cancel/replace the user's job.
- **WORKER_TOKEN is not configured. No worker is connected. No live Ubuntu→cloud→UI search has succeeded.** Worker API currently fails closed with 401.
- Lovable Secrets UI: `?view=more&subview=cloud&section=secrets` on the project editor. Add `WORKER_TOKEN`, random >=32 characters. Corresponding Ubuntu env name is `FLIGHT_WORKER_TOKEN`. Keep the same secret on both sides, never commit or print it, never ask user to paste it into chat. Browser policy requires user handoff before entering/submitting a new authentication credential through UI. Prepare a concrete secrets form and an Ubuntu hidden-entry prompt, then ask the user to complete credential entry. No official MCP secret-setting tool was available.
- Preview may require platform authentication that prevents unauthenticated server polling, even with worker bearer token. Publish the reviewed owner-gated app to obtain a public production endpoint, then configure `FLIGHT_API_BASE=https://<production-host>/api/public/worker`. Do not send the bearer token across redirects; worker explicitly refuses redirects.

## Architecture already recovered and reviewed

- `hosted/src/lib/flight-query.ts`: exact seven query keys, uppercase IATA, canonical dates UTC today..+365, trip type roundtrip/oneway, flex radius0/1/3, min stay1..365, at most49 pairs, no booleans-as-integers or extra fields. Fares verified against route/pairs/timestamp/market/economy/one adult. Provider URLs rebuilt by backend.
- `hosted/src/lib/flight.functions.ts`: authenticated owner-only session/submit/get/cancel and owner refresh/reaper. One active search, 10 submissions/hour.
- `hosted/src/routes/api/public/worker/$action.ts`: bearer token, only heartbeat/claim/progress/finish, server-only Supabase admin credentials. Ubuntu receives only worker token, never Supabase service-role key.
- `hosted/supabase/migrations/*0002*`: SQL repeats strict input validation even for direct RPC calls, owner-only refresh, expiration, lease checks and reaping. RLS + restricted service-only RPCs.
- `hosted_worker.py`: outbound-only HTTPS polling; strict canonical UUID/claim/query/pair validation before disk/process access; fixed argv, shell=False; isolated job directory; original run_all.py + fixed generated TOML; completion-verified fare export; heartbeat, cancellation, 30min budget; own process-group termination including orphan children. No listening port. Never grant arbitrary command or server browsing capabilities through the app.
- `hosted/worker/` contains the originally recovered worker copy/tests/root service example. Root worker/test are currently authoritative; reconcile duplicate copies before shipping.
- Previous already-pushed commits `306b6d4` hide browser windows and strictly wait for Google's actual loading signals; `5062628` preserves Skyscanner's existing automatic handler. Skyscanner can still block a datacenter/home IP. Do not promise bypass success without testing. No successful live recheck of restored handler yet.

## Frozen form and latest unfinished local change

Original symptom: owner preview stayed “Connecting…” / disabled “Searching…” and controls didn't respond. Lovable's last completed revision `0eddd778777b58173a93b536677f8d0fff78bca0` changed FlightApp to write static trusted markup once in an effect, using a ref for changing server functions. React no longer replaces the DOM/event listeners on server hook rerenders. Reloaded preview showed proper worker-offline/waiting state and fare grid, but real calendar/swap controls were not yet fully tested through the browser.

Immediately before stopping, we extracted that same component to `hosted/src/flight/FlightApp.tsx` and imported it from `hosted/src/routes/index.tsx`, to allow a meaningful real-form regression test. Added `hosted/src/flight/FlightApp.test.tsx` that simulates state-updating server hooks, real job polling and real form event handlers.

**Latest test result: 32 passing, 1 failing.** The failed test successfully kept airport edits, calendar DOM and handlers across polling, then expected body `data-trip-type` to become `oneway` after clicking one-way. It remained `roundtrip`. Investigate whether this is an actual styling sync bug or test-event mismatch. Legacy `bindEvents` trip-type listener calls `syncDateFields()` but not `syncOptionControls()`, where the body dataset is set. Do not claim all tests pass. Unmount/polling-cleanup regression passes. The stop-search assertions in the first test have not run because it failed earlier.

Prior to that new test/refactor: original 31 web tests passed, full Python suite 217 passed with two POSIX process tests skipped on Windows, and Ruff passed for src/tests/rootworker. npm dependencies installed in `hosted/node_modules`; npm lockfile newly generated. Node local24.13.1. A production build was started after the latest test run but **interrupted at the user's stop request**, so production build is unverified. Exec session45899 terminated. No background build from that session should remain.

## Ubuntu server already prepared

- Host: `user-System-Product-Name`; Linux user `user`, uid1000.
- Saved SSH address `192.168.10.41:51250` becomes reachable when Aalto VPN disconnected, but tested laptop keys are rejected. Do not repeatedly try SSH/passwords or weaken SSH/firewall rules. No working Tailscale address was identified.
- Working access was the existing authenticated **T3 web terminal**, https://app.t3.codes, server shows Connected. User terminal tab was https://app.t3.codes/draft/702c62b2-a6b7-4f11-a900-248b16bf75af. No T3 agent prompt was sent; only terminal used. Browser control via CUA. Xterm outputs need screenshot, not AX text. Use paste, because typing dropped characters once.
- User was told they can reconnect their Aalto VPN after this terminal access worked.
- `sudo -n` unavailable. Do not escalate through lxd or other privileged backdoors. Dedicated restricted root-installed service requires user's normal sudo entry; alternatively test a properly isolated user service if supported. Existing user lingering is enabled.
- Dedicated source checkout: `/home/user/.local/share/flight-finder/source`, clean clone of public homecoming at5062628. Other user projects untouched.
- Python3.12.15 installed using official uv to `/home/user/.local/share/uv/python/`; uv in `~/.local/bin`. Existing system Python3.10 is insufficient for tomllib.
- Venv: `/home/user/.local/share/flight-finder/venv`, requirements installed, Playwright/Patchright Chromium installed and **actual headless browser launch succeeded**.
- Browsers: `/home/user/.local/share/flight-finder/browsers`; set PLAYWRIGHT_BROWSERS_PATH accordingly.
- Official Node22 Linux archive downloaded and SHA256 verified. Its installed folder is recorded in `/home/user/.local/share/flight-finder/node-dir.txt`. Add `<contents>/bin` to PATH; system Node12 fails optional loader tests.
- Server's existing201 Python tests passed with Node22 PATH. New16 worker tests have not been transferred/run yet; two POSIX process tests must run here.
- No worker service, worker secret/env or live search installed/started yet.

Suggested deployment: dedicated account/root unit is preferred isolation, or user unit with ProtectHome=tmpfs, restrictive read-only binds for source/venv/uv Python/browser runtime, write bind only service data, NoNewPrivileges, PrivateTmp, ProtectSystem=strict, own cgroup kill and mode600 env. Verify kernel/systemd support; don't silently relax home isolation. Worker should not see SSH keys or unrelated projects. The app never needs inbound access to Ubuntu or a public shell/T3 tunnel.

## Remaining acceptance steps

1. Read workspace and connected repo instructions; inspect preserved work rather than regenerating it.
2. Resolve failing regression, test actual signed-in date picker, airport swap, trip type, dropdowns and cancel across polling. Keep UI unchanged except actual functional fixes. Run web tests and production build.
3. Write reusable public README/deployment instructions, reconcile worker copies, sanitize public configuration. Run required Python/Ruff checks. Commit only this task's files to homecoming; copy reviewed deployment edits into var/hosted-connected and push main normally to sync Lovable.
4. Transfer committed worker to the isolated Ubuntu checkout; run all217 Python tests including POSIX process termination tests and confirm headless operation.
5. Complete user credential handoff, configure token both sides, publish reviewed owner-gated web app, install suitably restricted Ubuntu worker service. No builder prompt/credits required for these steps.
6. Verify signed-out/nonowner access is denied, unauthenticated worker requests are401, invalid query input fails at every boundary. No fake prices, remote shell endpoints or secrets in client/source.
7. Run an actual authorized fresh flight search via hosted form, observe server worker claim and completion-verified prices arrive in UI. Confirm cancellation kills only its own scraper processes and restart/reconnect preserves saved progress. Report actual provider block/timeout honestly.
8. Give the user the working production URL, GitHub code link and concise tested results. If credentials or sudo entry block deployment, show exactly the user action needed; don't claim the hosted search works.

At handoff, recovery/setup is substantial but **the hosted live-search goal is not complete**.
