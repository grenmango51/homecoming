#!/usr/bin/env python3
"""Serve Flight Finder on loopback and run fresh, bounded local scraper jobs."""

from __future__ import annotations

import argparse
import datetime as dt
import hmac
import json
import os
import secrets
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen

from src.local_search import job_config, validate_search
from src.web_export import export_fares

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
DATA = ROOT / "var" / "local-ui"
TERMINAL_STATES = {"complete", "partial", "failed", "cancelled"}


def stop_process(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    else:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)


class SearchJobs:
    def __init__(self, directory: Path = DATA, *, budget: int = 1800):
        self.directory = directory
        self.budget = budget
        self.lock = threading.RLock()
        self.jobs: dict[str, dict] = {}
        self.process: subprocess.Popen | None = None
        self.active: str | None = None

    def start(self, raw: dict) -> dict:
        query = validate_search(raw)
        with self.lock:
            if self.active:
                raise RuntimeError("A search is already running. Stop it before starting another.")
            job_id = secrets.token_hex(12)
            directory = self.directory / "jobs" / job_id
            directory.mkdir(parents=True)
            config = directory / "search.toml"
            config.write_text(job_config(query, directory, self.directory / "profiles", budget=self.budget), encoding="utf-8")
            self.jobs[job_id] = {"id": job_id, "status": "running", "query": query,
                                 "started_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                                 "directory": directory, "message": "Opening both provider browsers…"}
            self.active = job_id
            # Spawn under the lock so Cancel cannot arrive before the process exists.
            try:
                log = (directory / "search.log").open("w", encoding="utf-8")
                try:
                    options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
                    self.process = subprocess.Popen(
                        [sys.executable, "-u", str(ROOT / "run_all.py"), "--config", str(config),
                         "--trip", str(config), "--no-skip-existing"],
                        cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                        env=os.environ | {"FLIGHT_FINDER_HUMAN_CHECKS_ONLY": "1"}, **options,
                    )
                finally:
                    log.close()
            except OSError:
                self.jobs[job_id]["status"] = "failed"
                self.jobs[job_id]["message"] = "Could not start the scanners. Check your Python/browser installation."
                self.active = None
                raise
            threading.Thread(target=self._watch, args=(job_id, self.process), daemon=True).start()
            return self.snapshot(job_id)

    def _watch(self, job_id: str, proc: subprocess.Popen) -> None:
        timed_out = False
        try:
            proc.wait(timeout=self.budget + 120)
        except subprocess.TimeoutExpired:
            timed_out = True
            stop_process(proc)
            proc.wait(timeout=15)
        finally:
            with self.lock:
                job = self.jobs[job_id]
                if job["status"] != "cancelled":
                    summary = self.snapshot(job_id)
                    expected = len(job["query"]["pairs"]) * 2
                    count = len(summary["fares"])
                    job["status"] = "complete" if count == expected else "partial" if count else "failed"
                    job["message"] = (
                        "Search finished." if count == expected else
                        "Time limit reached; completed prices are shown." if timed_out else
                        "Some prices could not be verified. Check provider status below."
                    )
                    job["finished_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
                if self.active == job_id:
                    self.active = None
                    self.process = None

    def snapshot(self, job_id: str) -> dict:
        with self.lock:
            job = self.jobs[job_id]
            directory = job["directory"]
            payload = export_fares([(directory / "google", "google_flights"),
                                    (directory / "skyscanner", "skyscanner")])
            allowed = {tuple(pair) for pair in job["query"]["pairs"]}
            fares = [fare for fare in payload["fares"]
                     if (fare["departure"], fare["return_date"]) in allowed
                     and fare["origin"] == job["query"]["origin"]
                     and fare["destination"] == job["query"]["destination"]
                     and fare["observed_at"] >= job["started_at"]]
            providers = {}
            for folder, provider in (("google", "google_flights"), ("skyscanner", "skyscanner")):
                counts: dict[str, int] = {}
                for path in (directory / folder).glob("20??-??-??_*.json"):
                    try:
                        row = json.loads(path.read_text(encoding="utf-8"))
                        status = str(row.get("status", "unknown"))
                        counts[status] = counts.get(status, 0) + 1
                    except (OSError, ValueError, AttributeError):
                        continue
                providers[provider] = {"verified": sum(fare["provider"] == provider for fare in fares),
                                       "expected": len(allowed), "statuses": counts}
            return {key: value for key, value in job.items() if key != "directory"} | {
                "fares": fares, "providers": providers, "mode": "live",
                "elapsed_seconds": round(
                    (dt.datetime.fromisoformat(job["finished_at"]).timestamp() if job.get("finished_at") else time.time())
                    - dt.datetime.fromisoformat(job["started_at"]).timestamp()
                ),
            }

    def cancel(self, job_id: str) -> dict:
        with self.lock:
            job = self.jobs[job_id]
            if job["status"] not in TERMINAL_STATES:
                job["status"] = "cancelled"
                job["message"] = "Search stopped. Completed prices are kept locally."
                job["finished_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
                if self.process:
                    stop_process(self.process)
                    self.process.wait(timeout=15)
                if self.active == job_id:
                    self.active = None
                    self.process = None
            return self.snapshot(job_id)

    def close(self) -> None:
        with self.lock:
            if self.active:
                self.cancel(self.active)


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int, jobs: SearchJobs):
        self.jobs = jobs
        self.token = secrets.token_urlsafe(32)
        super().__init__(("127.0.0.1", port), Handler)

    @property
    def origins(self) -> set[str]:
        port = self.server_address[1]
        return {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}


class Handler(BaseHTTPRequestHandler):
    server: LocalServer

    def log_message(self, fmt: str, *args) -> None:
        # Request headers/body and tokens are never logged.
        print(f"[local-ui] {fmt % args}")

    def reply(self, code: int, payload, content_type: str = "application/json") -> None:
        data = json.dumps(payload).encode() if content_type == "application/json" else payload
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(data)

    def authorized(self, *, write: bool = False, asset: bool = False) -> bool:
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        valid = f"http://{host}" in self.server.origins
        if not asset:
            valid = valid and (origin is None or origin in self.server.origins)
            valid = valid and self.headers.get("Sec-Fetch-Site", "same-origin") in {"same-origin", "none"}
        if write:
            valid = valid and origin in self.server.origins
            valid = valid and hmac.compare_digest(self.headers.get("X-Flight-Token", ""), self.server.token)
            valid = valid and self.headers.get("Content-Type", "").split(";")[0] == "application/json"
        if not valid:
            self.reply(403, {"error": "Only this local Flight Finder page may access the app."})
        return valid

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        # Following a link from another site may open the public UI assets.
        # Session data and job endpoints still reject cross-site reads/writes.
        if not self.authorized(asset=not path.startswith("/api/")):
            return
        if path == "/api/session":
            with self.server.jobs.lock:
                latest = next(reversed(self.server.jobs.jobs), None)
                job = self.server.jobs.snapshot(latest) if latest else None
            self.reply(200, {"app": "flight-finder-local", "token": self.server.token, "job": job})
        elif path.startswith("/api/jobs/"):
            try:
                self.reply(200, self.server.jobs.snapshot(path.removeprefix("/api/jobs/")))
            except KeyError:
                self.reply(404, {"error": "Search not found. The local app may have restarted."})
        else:
            # Explicit asset allowlist: never serve profiles, results, logs or repository files.
            assets = {"/": ("index.html", "text/html; charset=utf-8"),
                      "/index.html": ("index.html", "text/html; charset=utf-8"),
                      "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                      "/styles.css": ("styles.css", "text/css; charset=utf-8"),
                      "/favicon.svg": ("favicon.svg", "image/svg+xml")}
            if path not in assets:
                self.reply(404, {"error": "Not found"})
                return
            filename, content_type = assets[path]
            self.reply(200, (WEB / filename).read_bytes(), content_type)

    def do_POST(self) -> None:
        if not self.authorized(write=True):
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 8192:
                self.reply(413, {"error": "Search request too large or empty."})
                return
            raw = json.loads(self.rfile.read(length))
            path = urlsplit(self.path).path
            if path == "/api/jobs":
                self.reply(202, self.server.jobs.start(raw))
            elif path.startswith("/api/jobs/") and path.endswith("/cancel"):
                self.reply(200, self.server.jobs.cancel(path.split("/")[3]))
            else:
                self.reply(404, {"error": "Not found"})
        except (ValueError, TypeError):
            self.reply(400, {"error": "Invalid search. Use valid airports, future dates and a supported date range."})
        except RuntimeError as error:
            self.reply(409, {"error": str(error)})
        except KeyError:
            self.reply(404, {"error": "Search not found."})
        except OSError:
            self.reply(500, {"error": "Could not start the scanners. Check your local installation."})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=4173)
    parser.add_argument("--open", action="store_true", help="Open the local search page in your browser")
    args = parser.parse_args()
    jobs = SearchJobs()
    try:
        server = LocalServer(args.port, jobs)
    except OSError:
        if args.open:
            url = f"http://127.0.0.1:{args.port}"
            try:
                with urlopen(f"{url}/api/session", timeout=2) as response:
                    existing = json.load(response)
                if existing.get("app") == "flight-finder-local":
                    webbrowser.open(url)
                    print(f"Flight Finder is already running: {url}")
                    return
            except (OSError, ValueError):
                pass
        raise SystemExit(f"Port {args.port} is occupied. Close the other app or choose --port 4174.") from None
    url = f"http://127.0.0.1:{server.server_address[1]}"
    print(f"Flight Finder: {url}\nKeep this terminal open. Ctrl+C stops the app and its search.", flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        jobs.close()
        server.server_close()


if __name__ == "__main__":
    main()
