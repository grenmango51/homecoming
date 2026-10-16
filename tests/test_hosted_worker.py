"""Browser-free tests for hosted_worker.py. Copy to tests/ in the homecoming repo:
    python -m unittest tests.test_hosted_worker
"""

import datetime as dt
import email.message
import io
import json
import os
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import urllib.response
from pathlib import Path
from unittest import mock

import hosted_worker as hw

TODAY = hw.utc_today()
DEP = (TODAY + dt.timedelta(days=40)).isoformat()
RET = (TODAY + dt.timedelta(days=70)).isoformat()
SEARCH = {"origin": "HEL", "destination": "HAN", "departure": DEP, "return_date": RET,
          "trip_type": "roundtrip", "flex_days": 0, "min_stay_nights": 21}


class FakeApi:
    def __init__(self, cancel=False, stale=False):
        self.calls, self.cancel, self.stale = [], cancel, stale

    def post(self, action, body):
        self.calls.append((action, body))
        if self.stale and action == "progress":
            raise hw.StaleLease()
        return {"cancel": self.cancel} if action == "progress" else {}


def job(**overrides):
    return {"id": "11111111-1111-1111-1111-111111111111", "lease_token": "22222222-2222-2222-2222-222222222222",
            "search": dict(SEARCH), "pairs": [[DEP, RET]], "started_at": "2000-01-01T00:00:00+00:00"} | overrides


class FakeProc:
    pid = 999999
    def __init__(self, *a, **k):
        self.args, self.kwargs, self.returncode = a, k, None
    def poll(self):
        return self.returncode
    def wait(self, timeout=None):
        return 0


class WorkerTests(unittest.TestCase):
    def test_api_requires_https_and_long_token(self):
        with self.assertRaises(SystemExit):
            hw.Api("http://evil.example", "x" * 40)
        with self.assertRaises(SystemExit):
            hw.Api("https://ok.example", "short")

    def test_rejects_extra_keys_bool_numbers_and_pair_mismatch(self):
        for bad in (job(search=SEARCH | {"cmd": "rm -rf /"}), job(search=SEARCH | {"flex_days": True}),
                    job(pairs=[[DEP, RET], [DEP, None]]), job(search=SEARCH | {"origin": "hel"})):
            with self.assertRaises((ValueError, KeyError)):
                hw.check_job(bad)

    def test_fixed_argv_no_shell_new_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            created = []
            def popen(*a, **k):
                p = FakeProc(*a, **k)
                p.returncode = 0
                created.append(p)
                return p
            api = FakeApi()
            with mock.patch.object(hw, "stop_group"):
                self.assertEqual(hw.run_job(api, job(), Path(tmp), popen=popen), "done")
            argv, kw = created[0].args[0], created[0].kwargs
            self.assertEqual(argv[2:], [str(hw.ROOT / "run_all.py"), "--config", argv[4], "--trip", argv[4], "--no-skip-existing"])
            self.assertIs(kw["shell"], False)
            self.assertTrue(kw["start_new_session"])
            self.assertEqual(api.calls[-1][0], "finish")

    def test_cancel_kills_only_own_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(hw, "stop_group") as stop:
                out = hw.run_job(FakeApi(cancel=True), job(), Path(tmp), popen=FakeProc)
            self.assertEqual(out, "cancelled")
            self.assertIsInstance(stop.call_args.args[0], FakeProc)

    def test_never_runs_same_job_twice(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "jobs" / job()["id"]).mkdir(parents=True)
            popen = mock.Mock()
            self.assertEqual(hw.run_job(FakeApi(), job(), Path(tmp), popen=popen), "failed")
            popen.assert_not_called()

    def test_stale_lease_stops_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(hw, "stop_group") as stop:
                self.assertEqual(hw.run_job(FakeApi(stale=True), job(), Path(tmp), popen=FakeProc), "stale")
            stop.assert_called()

    def test_snapshot_excludes_unverified_and_old(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "google").mkdir()
            (d / "skyscanner").mkdir()
            row = {"origin": "HEL", "destination": "HAN", "departure_date": DEP, "return_date": RET,
                   "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(), "status": "observed",
                   "lowest_observed_price_eur": 500, "completion_version": 3, "completion_evidence": "dom_settled",
                   "gl": "FI"}  # no google_completion_check => must be dropped
            (d / "google" / f"{DEP}_{RET}.json").write_text(json.dumps(row))
            query = hw.validate_search(SEARCH, TODAY)
            fares, providers = hw.snapshot(d, query, "2000-01-01T00:00:00+00:00")
            self.assertEqual(fares, [])
            self.assertEqual(providers["google_flights"]["statuses"], {"observed": 1})


    # --- claim validation happens before any filesystem write -------------------------
    def test_traversal_and_malformed_claims_never_touch_disk(self):
        bad_claims = [
            job(id="../../etc/cron.d/x"), job(id="11111111-1111-1111-1111-111111111111/../../x"),
            job(id="11111111111111111111111111111111"), job(id="11111111-1111-1111-1111-11111111111A"),
            job(id=None), job(lease_token="../x"), job(started_at="2000-01-01T00:00:00"),  # naive
            job(started_at="not a date"), job(started_at=12),
            job(search=SEARCH | {"trip_type": "oneway"}),  # one-way with a return date
            job(search=SEARCH | {"departure": DEP + " ; rm"}), job(pairs="[]"), job(pairs=[]),
            job(pairs=[[DEP, RET, "x"]]), job(pairs=[[DEP, 5]]), job(extra="--config=/etc/passwd"),
            {"id": "11111111-1111-1111-1111-111111111111"}, [], "job",
        ]
        for claim in bad_claims:
            with tempfile.TemporaryDirectory() as tmp:
                popen = mock.Mock()
                self.assertEqual(hw.run_job(FakeApi(), claim, Path(tmp), popen=popen), "failed", claim)
                popen.assert_not_called()
                self.assertEqual(list(Path(tmp).rglob("*")), [], claim)

    def test_valid_oneway_claim_accepted(self):
        one = SEARCH | {"trip_type": "oneway", "return_date": None}
        q = hw.check_job(job(search=one, pairs=[[DEP, None]]))
        self.assertEqual(q["return_date"], None)

    def test_expired_lease_stops_and_reports_stale(self):
        class ExpiredApi(FakeApi):
            def post(self, action, body):
                self.calls.append((action, body))
                if action in ("progress", "finish"):
                    raise hw.StaleLease()
                return {}
        with tempfile.TemporaryDirectory() as tmp:
            api = ExpiredApi()
            with mock.patch.object(hw, "stop_group") as stop:
                self.assertEqual(hw.run_job(api, job(), Path(tmp), popen=FakeProc), "stale")
            stop.assert_called()
            self.assertNotIn("finish", [a for a, _ in api.calls])

    # --- API base and bearer secret ----------------------------------------------------
    def test_api_base_must_be_exact_https(self):
        for base in ("http://127.0.0.1:8080/api", "http://127.0.0.1.evil.example/api", "https://user:pw@good.example/api",
                     "https://good.example@evil.example/api", "https://good.example/api?x=1", "https://good.example/api#f",
                     "https:///api", "ftp://good.example", "https://good.example:99999/api", " https://good.example/api",
                     "javascript:alert(1)", ""):
            with self.assertRaises(SystemExit, msg=base):
                hw.Api(base, "x" * 40)
        self.assertEqual(hw.Api("https://good.example/api/public/worker/", "x" * 40).base, "https://good.example/api/public/worker")

    def test_redirects_are_never_followed_with_token(self):
        seen = []
        class Handler(urllib.request.BaseHandler):
            handler_order = 100  # run before the real HTTPS handler; no network
            def https_open(self, req):
                seen.append((req.full_url, req.get_header("Authorization")))
                hdrs = email.message.Message()
                if "evil" in req.full_url:
                    return urllib.response.addinfourl(io.BytesIO(b"{}"), hdrs, req.full_url, 200)
                hdrs["Location"] = "https://evil.example/steal"
                res = urllib.response.addinfourl(io.BytesIO(b""), hdrs, req.full_url, 302)
                res.msg = "Found"
                return res
        opener = urllib.request.build_opener(hw._NoRedirect(), Handler())
        api = hw.Api("https://good.example/api", "s" * 40, opener=opener.open)
        with self.assertRaises(urllib.error.HTTPError):
            api.post("heartbeat", {})
        self.assertEqual([u for u, _ in seen], ["https://good.example/api/heartbeat"])
        self.assertFalse(any("evil" in u for u, _ in seen))

    def test_default_opener_refuses_redirects(self):
        api = hw.Api("https://good.example/api", "s" * 40)
        handler = hw._NoRedirect()
        req = urllib.request.Request("https://good.example/api/claim", headers={"Authorization": "Bearer " + "s" * 40})
        self.assertIsNone(handler.redirect_request(req, None, 302, "Found", {}, "https://evil.example/"))
        self.assertIsNotNone(api.opener)

    def test_unknown_action_rejected(self):
        api = hw.Api("https://good.example/api", "s" * 40, opener=mock.Mock())
        with self.assertRaises(ValueError):
            api.post("../admin", {})

    # --- process-group cleanup ---------------------------------------------------------
    @unittest.skipUnless(os.name == "posix", "Linux process-group integration")
    def test_stop_group_kills_orphaned_children_after_parent_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            pidfile = Path(tmp) / "child.pid"
            proc = subprocess.Popen(["/bin/sh", "-c", f"sleep 120 & echo $! > {pidfile}; exit 0"], start_new_session=True)
            proc.wait(timeout=5)
            for _ in range(50):
                if pidfile.exists() and pidfile.read_text().strip():
                    break
                time.sleep(0.05)
            child = int(pidfile.read_text())
            os.kill(child, 0)  # child still alive although parent exited
            hw.stop_group(proc, grace=3)
            time.sleep(0.2)
            alive = True
            try:
                os.kill(child, 0)
                with open(f"/proc/{child}/stat") as fh:
                    alive = fh.read().split()[2] != "Z"
            except (ProcessLookupError, FileNotFoundError):
                alive = False
            self.assertFalse(alive)

    @unittest.skipUnless(os.name == "posix", "Linux process-group integration")
    def test_stop_group_leaves_other_processes_alone(self):
        other = subprocess.Popen(["sleep", "120"], start_new_session=True)
        try:
            mine = subprocess.Popen(["sleep", "120"], start_new_session=True)
            hw.stop_group(mine, grace=3)
            self.assertIsNotNone(mine.poll())
            self.assertIsNone(other.poll())
        finally:
            other.kill()
            other.wait()


if __name__ == "__main__":
    unittest.main()
