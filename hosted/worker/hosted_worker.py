#!/usr/bin/env python3
"""Outbound-only adapter: claims owner searches from the hosted Flight Finder and
runs them with the existing, unchanged scrapers (run_all.py).

Install into the root of the homecoming repository (next to local_app.py).
It opens NO listening port. All traffic is outbound HTTPS to FLIGHT_API_BASE.

Environment (systemd EnvironmentFile, mode 0600):
  FLIGHT_API_BASE      e.g. https://<project>.lovable.app/api/public/worker
  FLIGHT_WORKER_TOKEN  long random secret, identical to the backend WORKER_TOKEN
  FLIGHT_SERVICE_DIR   dedicated data dir (default /var/lib/flight-finder)
"""

from __future__ import annotations

import datetime as dt
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.local_search import job_config, validate_search  # noqa: E402  (existing, unchanged)
from src.web_export import export_fares  # noqa: E402  (existing, unchanged)

VERSION = "hosted-worker/1"
BUDGET = 1800
IDLE_POLL = 15
HEARTBEAT_EVERY = 30
PROGRESS_EVERY = 10
ALLOWED_KEYS = {"origin", "destination", "departure", "return_date", "trip_type", "flex_days", "min_stay_nights"}
CLAIM_KEYS = {"id", "lease_token", "search", "pairs", "started_at", "lease_seconds", "runtime_budget_seconds"}
MAX_PAIRS = 49


class StaleLease(Exception):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow redirects: the bearer secret must only ever go to FLIGHT_API_BASE."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


def check_api_base(base: str) -> str:
    """Exact parsed HTTPS URL; no credentials, query, fragment, or look-alike prefixes."""
    try:
        url = urllib.parse.urlsplit(base)
        port = url.port
    except ValueError:
        raise SystemExit("FLIGHT_API_BASE must be an https URL") from None
    if (url.scheme != "https" or not url.hostname or url.username is not None or url.password is not None
            or "@" in url.netloc or url.query or url.fragment or "?" in base or "#" in base
            or any(c.isspace() for c in base) or (port is not None and not 0 < port < 65536)):
        raise SystemExit("FLIGHT_API_BASE must be an https URL")
    return base.rstrip("/")


class Api:
    def __init__(self, base: str, token: str, opener=None):
        self.base = check_api_base(base)
        if len(token) < 32 or any(c.isspace() for c in token):
            raise SystemExit("FLIGHT_WORKER_TOKEN missing or too short")
        self.token = token
        self.opener = opener or urllib.request.build_opener(_NoRedirect()).open

    def post(self, action: str, body: dict) -> dict:
        if action not in {"heartbeat", "claim", "progress", "finish"}:
            raise ValueError("unknown action")
        req = urllib.request.Request(
            f"{self.base}/{action}", data=json.dumps(body).encode(), method="POST",
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
        )
        try:
            with self.opener(req, timeout=30) as res:
                if res.geturl().split("?")[0] != f"{self.base}/{action}":
                    raise RuntimeError("unexpected redirect")
                return json.loads(res.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            if exc.code == 409:
                raise StaleLease() from None
            raise


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:  # not ours: never touch it
        return False


def stop_group(proc: subprocess.Popen, grace: float = 10.0) -> None:
    """Terminate this job's own process group (start_new_session => pgid == proc.pid),
    even when the parent already exited but scraper children remain. No global matching."""
    pgid = proc.pid
    if proc.poll() is None or _group_alive(pgid):
        for sig, wait in ((signal.SIGTERM, grace), (signal.SIGKILL, 5.0)):
            try:
                os.killpg(pgid, sig)
            except (ProcessLookupError, PermissionError):
                break
            end = time.monotonic() + wait
            while time.monotonic() < end:
                proc.poll()  # reap the leader so it does not linger as a zombie
                if not _group_alive(pgid):
                    break
                time.sleep(0.1)
            else:
                continue
            break
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def utc_today() -> dt.date:
    return dt.datetime.now(dt.timezone.utc).date()


def canonical_uuid(value) -> str:
    if not isinstance(value, str):
        raise ValueError("bad uuid")
    parsed = uuid.UUID(value)
    if str(parsed) != value:
        raise ValueError("non-canonical uuid")
    return value


def check_job(job: dict) -> dict:
    """Validate the whole claim before any filesystem write or spawn.
    Re-validates with the original Python rules; backend pairs must match exactly."""
    if not isinstance(job, dict) or not {"id", "lease_token", "search", "pairs", "started_at"} <= set(job) or not set(job) <= CLAIM_KEYS:
        raise ValueError("unexpected claim fields")
    canonical_uuid(job["id"])
    canonical_uuid(job["lease_token"])
    started = job["started_at"]
    if not isinstance(started, str):
        raise ValueError("bad started_at")
    start = dt.datetime.fromisoformat(started.replace("Z", "+00:00"))
    if start.tzinfo is None or start > dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=5):
        raise ValueError("bad started_at")
    search = job["search"]
    if not isinstance(search, dict) or set(search) != ALLOWED_KEYS:
        raise ValueError("unexpected search fields")
    for key in ("flex_days", "min_stay_nights"):
        if type(search[key]) is not int:
            raise ValueError("numbers must be integers")
    for key in ("origin", "destination", "departure", "trip_type"):
        if type(search[key]) is not str:
            raise ValueError("bad field type")
    if search["trip_type"] == "oneway" and search["return_date"] is not None:
        raise ValueError("one-way search must have no return date")
    if search["trip_type"] == "roundtrip" and type(search["return_date"]) is not str:
        raise ValueError("bad return date")
    for key in ("departure", "return_date"):
        if search[key] is not None and (len(search[key]) != 10 or dt.date.fromisoformat(search[key]).isoformat() != search[key]):
            raise ValueError("bad date")
    pairs = job["pairs"]
    if not isinstance(pairs, list) or not 1 <= len(pairs) <= MAX_PAIRS:
        raise ValueError("bad pairs")
    for pair in pairs:
        if not isinstance(pair, list) or len(pair) != 2 or type(pair[0]) is not str or not (pair[1] is None or type(pair[1]) is str):
            raise ValueError("bad pair")
    query = validate_search(search, utc_today())
    if [list(p) for p in query["pairs"]] != [list(p) for p in pairs]:
        raise ValueError("date pairs mismatch")
    return query


def snapshot(directory: Path, query: dict, started_at: str) -> tuple[list, dict]:
    """Completion-verified fares from this job only (same rules as local_app.SearchJobs.snapshot)."""
    payload = export_fares([(directory / "google", "google_flights"), (directory / "skyscanner", "skyscanner")])
    allowed = {tuple(p) for p in query["pairs"]}
    start = dt.datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    fares = [f for f in payload["fares"]
             if (f["departure"], f["return_date"]) in allowed
             and f["origin"] == query["origin"] and f["destination"] == query["destination"]
             and dt.datetime.fromisoformat(f["observed_at"]) >= start]
    providers = {}
    for folder, provider in (("google", "google_flights"), ("skyscanner", "skyscanner")):
        counts: dict[str, int] = {}
        for path in (directory / folder).glob("20??-??-??_*.json"):
            try:
                status = str(json.loads(path.read_text(encoding="utf-8")).get("status", "unknown"))
            except (OSError, ValueError, AttributeError):
                continue
            counts[status] = counts.get(status, 0) + 1
        providers[provider] = {"statuses": counts}
    return fares, providers


def run_job(api: Api, job: dict, service: Path, popen=subprocess.Popen) -> str:
    try:
        query = check_job(job)  # nothing touches disk or spawns before this passes
    except (ValueError, KeyError, TypeError):
        if isinstance(job, dict):
            try:
                api.post("finish", {"job_id": canonical_uuid(job.get("id")), "lease_token": canonical_uuid(job.get("lease_token")), "outcome": "failed"})
            except (ValueError, TypeError, StaleLease):
                pass
        return "failed"
    job_id, lease = job["id"], job["lease_token"]
    jobs_dir = (service / "jobs").resolve()
    directory = jobs_dir / job_id
    if directory.resolve().parent != jobs_dir:
        return "failed"
    try:
        directory.mkdir(parents=True)  # exists => already executed once; never run twice
    except FileExistsError:
        api.post("finish", {"job_id": job_id, "lease_token": lease, "outcome": "failed"})
        return "failed"
    config = directory / "search.toml"
    config.write_text(job_config(query, directory, service / "profiles", budget=BUDGET), encoding="utf-8")
    with (directory / "search.log").open("w", encoding="utf-8") as log:
        proc = popen(
            [sys.executable, "-u", str(ROOT / "run_all.py"), "--config", str(config), "--trip", str(config), "--no-skip-existing"],
            cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, start_new_session=True, shell=False,
        )
    deadline = time.monotonic() + BUDGET + 120
    outcome = "done"
    try:
        while True:
            finished = proc.poll() is not None
            fares, providers = snapshot(directory, query, job["started_at"])
            cancel = api.post("progress", {"job_id": job_id, "lease_token": lease, "fares": fares, "providers": providers}).get("cancel")
            if cancel:
                stop_group(proc)
                outcome = "cancelled"
                break
            if finished:
                outcome = "done" if proc.returncode == 0 else "failed"
                break
            if time.monotonic() > deadline:
                stop_group(proc)
                outcome = "timeout"
                break
            time.sleep(PROGRESS_EVERY)
        if outcome != "cancelled":
            fares, providers = snapshot(directory, query, job["started_at"])
            api.post("progress", {"job_id": job_id, "lease_token": lease, "fares": fares, "providers": providers})
        api.post("finish", {"job_id": job_id, "lease_token": lease, "outcome": outcome})
    except StaleLease:
        stop_group(proc)  # backend expired or reassigned nothing: stop our own job only
        return "stale"
    finally:
        stop_group(proc)
    return outcome


def main() -> None:
    api = Api(os.environ.get("FLIGHT_API_BASE", ""), os.environ.get("FLIGHT_WORKER_TOKEN", ""))
    service = Path(os.environ.get("FLIGHT_SERVICE_DIR", "/var/lib/flight-finder"))
    (service / "jobs").mkdir(parents=True, exist_ok=True)
    last_beat = 0.0
    while True:
        try:
            if time.monotonic() - last_beat > HEARTBEAT_EVERY:
                api.post("heartbeat", {"state": "idle", "version": VERSION})
                last_beat = time.monotonic()
            job = api.post("claim", {}).get("job")
            if job:
                print(f"[worker] job {run_job(api, job, service)}", flush=True)
                last_beat = 0.0
                continue
        except Exception as exc:  # network blips: back off, never crash the service
            print(f"[worker] {type(exc).__name__}", flush=True)
        time.sleep(IDLE_POLL)


if __name__ == "__main__":
    main()

