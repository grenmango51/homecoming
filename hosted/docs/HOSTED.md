# Flight Finder — hosted, owner-only

The website handles sign-in, the search queue and results. All searching runs on the
owner's Ubuntu server through `worker/hosted_worker.py`, which only makes **outbound**
HTTPS calls. No inbound port, tunnel, SSH or shell is exposed.

## Access model

- Owner = verified account whose email equals `app_owner_config.owner_email`.
  Checked by `public.is_flight_owner()` (email confirmed, not anonymous).
  The value is **deployment specific**: this deployment uses the original owner's address,
  set in `drizzle/migrations/0000_migration.sql`. Before deploying a personal fork, change it to
  your own verified address *before* anyone signs in — either edit that seed line in a fresh
  project, or (existing project) run once as a database admin:
  `update public.app_owner_config set owner_email = 'you@example.com' where id;`
  Never leave it pointing at someone else's address, and never make "first sign-up" the owner.
- Session and job polling call `flight_owner_refresh()` (owner-checked; anon/non-owner → `forbidden`),
  which reaps jobs whose worker lease expired or that passed the 35-minute limit, so a lost worker
  never leaves a search "running" forever.
- `flight_submit` itself re-validates the exact seven-key search and computes the date pairs in
  PostgreSQL (`flight_validate_search`). Supplied `_pairs` must equal the computed list or be null;
  anything else → `invalid_search`. Calling the RPC directly is therefore as strict as the website.
- Every owner server function calls `is_flight_owner` and returns **403** otherwise; no session → **401**
  (`requireSupabaseAuth`). RLS on `flight_jobs`, `flight_fares`, `flight_worker_status` allows SELECT
  only to the owner for their own rows. No client INSERT/UPDATE/DELETE grants exist; writes go through
  `flight_submit` / `flight_cancel` (owner-checked SECURITY DEFINER) and worker RPCs (service_role only).
- Worker endpoints require `Authorization: Bearer $WORKER_TOKEN` (timing-safe compare, ≥32 chars).
  If the secret is unset every worker call returns **401** (fails closed).

## Required secrets

| Where | Name | Value |
|---|---|---|
| Project Settings → Secrets | `WORKER_TOKEN` | one long random string (e.g. `openssl rand -hex 48`) |
| Ubuntu `/etc/flight-finder/worker.env` (0600 root:flightfinder) | `FLIGHT_WORKER_TOKEN` | the **same** value |
| Ubuntu worker.env | `FLIGHT_API_BASE` | `https://<published-domain>/api/public/worker` |
| Ubuntu worker.env | `FLIGHT_SERVICE_DIR` | `/var/lib/flight-finder` |

## Owner API (server functions, browser → backend, bearer = owner session)

| Function | Input | Output |
|---|---|---|
| `getFlightSession` | – | `{owner, job: JobSnapshot\|null, worker: {online,last_seen_at,state}}` (latest job restores on refresh) |
| `submitFlightSearch` | `{search, idempotency_key: uuid}` — `search` is exactly the 7-key object below | `{error, job, worker}`; same key ⇒ same job (replay-safe) |
| `getFlightJob` | `{id: uuid}` | `{job, worker}` |
| `cancelFlightJob` | `{id: uuid}` | `{job, worker}` — queued ⇒ cancelled immediately; running ⇒ flag for the worker |

`search` = `{"origin":"HEL","destination":"HAN","departure":"2026-11-05","return_date":"2026-12-09","trip_type":"roundtrip","flex_days":3,"min_stay_nights":21}`
(rules in `src/lib/flight-query.ts`; worker re-checks with the original `src.local_search.validate_search`).

Limits: one queued-or-running search at a time globally, 10 submissions/hour, query ≤ 8192 bytes, ≤ 49 date pairs,
30 min runtime (+5 min cleanup, then reaped), queued jobs expire after 1 h.

`JobSnapshot` = `{id, status: queued|running|complete|partial|failed|cancelled, message, query, elapsed_seconds,
fares: [web_export fare rows], providers: {google_flights|skyscanner: {verified, expected, statuses}}}`.
`verified` is counted from stored fares, never trusted from the worker.

## Worker API (`POST https://<domain>/api/public/worker/<action>`, JSON, Bearer WORKER_TOKEN, body ≤ 64 KB)

| Action | Body | Response |
|---|---|---|
| `heartbeat` | `{state:"idle", version}` | `{ok:true}` — sent every 30 s even when idle; site shows “offline” after 90 s |
| `claim` | `{}` | `{job:null}` or `{job:{id, lease_token, search, pairs, started_at, lease_seconds:120, runtime_budget_seconds:1800}}` — atomic (advisory lock + `SKIP LOCKED`), never hands out a running job |
| `progress` | `{job_id, lease_token, fares:[...], providers:{p:{statuses:{...}}}}` | `{cancel: bool, accepted: n}`; renews lease; **409** on wrong/expired lease |
| `finish` | `{job_id, lease_token, outcome: done\|failed\|timeout\|cancelled}` | `{ok:true}`; **409** if replayed/stale |

Fare rows must be the `src/web_export.py` contract. The backend accepts a row only if provider ∈ {google_flights, skyscanner},
status `complete`, market FI, 1 passenger, economy, route equals the job, date pair is in the job, price is a positive number,
and `observed_at` is between job start and now+60 s. The provider link is **rebuilt server-side**; worker URLs are ignored.
An expired lease marks the job `failed`/`partial` — it is never re-queued, so a restart cannot run a job twice
(the worker also refuses a job whose directory already exists).

### Worker contract notes (unchanged request/response shapes)
- `claim` → `{job: null}` or `{job: {id, lease_token, search, pairs, started_at, lease_seconds, runtime_budget_seconds}}`.
  The worker rejects the whole claim before touching disk unless `id`/`lease_token` are canonical
  lowercase UUIDs, `started_at` is a timezone-aware timestamp, `search` has exactly the seven keys
  (one-way ⇒ `return_date: null`), and `pairs` equals what the original `validate_search` computes.
- `progress` / `finish` return **409** when the lease token is wrong, the lease has expired, or the job
  is past its 35-minute limit; the worker then stops its own process group and does not retry.
- `FLIGHT_API_BASE` must be a plain `https://host[:port]/path` (no user/password, query or fragment).
  The worker never follows redirects, so the bearer secret cannot be forwarded to another host.
- Cancel/timeout/stale kill this job's own process group (`start_new_session`, pgid = child pid),
  including scraper children left after the parent exits. Stopping the service relies on systemd
  `KillMode=control-group`. No global process matching.

## Ubuntu install (run by whoever has server access)

```sh
sudo useradd --system --home /var/lib/flight-finder --shell /usr/sbin/nologin flightfinder
sudo mkdir -p /opt/flight-finder /etc/flight-finder
sudo git clone https://github.com/grenmango51/homecoming /opt/flight-finder/homecoming
sudo cp worker/hosted_worker.py /opt/flight-finder/homecoming/        # from this repo
sudo cp worker/test_hosted_worker.py /opt/flight-finder/homecoming/tests/
sudo python3 -m venv /opt/flight-finder/venv
sudo /opt/flight-finder/venv/bin/pip install -r /opt/flight-finder/homecoming/requirements.txt
sudo PLAYWRIGHT_BROWSERS_PATH=/opt/flight-finder/browsers /opt/flight-finder/venv/bin/python -m patchright install --with-deps chromium
sudo install -m 600 -o root -g flightfinder /dev/null /etc/flight-finder/worker.env   # then edit, add the 3 vars + PLAYWRIGHT_BROWSERS_PATH=/opt/flight-finder/browsers
sudo cp worker/flight-finder-worker.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now flight-finder-worker
journalctl -u flight-finder-worker -f
cd /opt/flight-finder/homecoming && /opt/flight-finder/venv/bin/python -m unittest tests.test_hosted_worker
```

If `ProtectHome`/`PrivateDevices` prevents Chromium from starting, relax only that line and note why.

## Tests

- `bunx vitest run src/test/flight-query.test.ts` — malicious/invalid input, pair counts, verified-only fare acceptance.
- `python -m unittest tests.test_hosted_worker` — fixed argv/no shell, extra keys/bool numbers, cancel kills own group, no double run, stale lease, unverified rows excluded.
- `python -m unittest tests.test_hosted_worker` also covers traversal/malformed claim IDs (nothing written, nothing spawned), look-alike/credentialed API URLs, redirect token leaks, expired lease, and orphaned child cleanup.
- `supabase/tests/submit_strict.sql` — direct-RPC submit with unknown keys, booleans/strings as numbers, invalid/past/far dates, one-way with return, forged pairs, oversized payload; computed 49/7 pairs; owner refresh reaps a lost worker; anon/non-owner refresh rejected; finish/progress reject expired lease and time budget. Uses a rolled-back owner test double.
- `supabase/tests/queue.sql` — non-owner submit/cancel rejected, single claim, wrong lease rejected, cancel flag, replayed finish rejected, lease expiry reaped not re-queued (runs in a rolled-back block).

