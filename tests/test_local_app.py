"""Test the local execution boundary without importing browser automation."""

import datetime as dt
import http.client
import json
import tempfile
import threading
import tomllib
import unittest
from pathlib import Path
from unittest.mock import Mock

from local_app import LocalServer, SearchJobs
from src.local_search import job_config, validate_search
from src.web_export import public_fare

TODAY = dt.date(2026, 10, 4)
QUERY = {"origin": "HEL", "destination": "HAN", "departure": "2026-12-09",
         "return_date": "2027-01-09", "trip_type": "roundtrip", "flex_days": 3,
         "min_stay_nights": 21}


class SearchContractTests(unittest.TestCase):
    def test_grid_and_oneway_are_bounded(self):
        self.assertEqual(len(validate_search(QUERY, TODAY)["pairs"]), 49)
        exact = validate_search(QUERY | {"flex_days": 0}, TODAY)
        self.assertEqual(exact["pairs"], [["2026-12-09", "2027-01-09"]])
        one = validate_search(QUERY | {"trip_type": "oneway", "return_date": None}, TODAY)
        self.assertEqual(len(one["pairs"]), 7)
        self.assertTrue(all(pair[1] is None for pair in one["pairs"]))

    def test_rejects_injection_large_scans_and_invalid_dates(self):
        for key, value in [("origin", "HEL;cmd"), ("destination", "HEL"),
                           ("flex_days", 100), ("flex_days", True), ("trip_type", "shell"),
                           ("departure", "2026-02-30"), ("departure", "2020-01-01"),
                           ("return_date", "2026-12-10"), ("min_stay_nights", 0)]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_search(QUERY | {key: value}, TODAY)

    def test_generated_config_keeps_daily_settings_separate(self):
        query = validate_search(QUERY, TODAY)
        cfg = tomllib.loads(job_config(query, Path('private/jobs/abc'), Path('private/profiles')))
        self.assertFalse(cfg["execution"]["skip_existing"])
        self.assertEqual(cfg["execution"]["strategy"], "parallel")
        self.assertEqual(cfg["trip"]["exact_pairs"], query["pairs"])
        self.assertEqual(cfg["google_flights"]["gl"], ["FI"])
        self.assertIn("private", cfg["skyscanner"]["results_dir"])
        self.assertTrue(cfg["google_flights"]["headless"])
        self.assertTrue(cfg["skyscanner"]["headless"])
        self.assertFalse(cfg["skyscanner"]["attended"])
        self.assertEqual(cfg["skyscanner"]["challenge_timeout_seconds"], 0)

    def test_oneway_exports_do_not_invent_return_date(self):
        row = {"origin": "HEL", "destination": "HAN", "departure_date": "2026-12-09",
               "return_date": None, "fetched_at": "2026-10-04T06:00:00+00:00",
               "status": "observed", "lowest_observed_price_eur": 420,
               "completion_version": 3, "completion_evidence": "dom_settled", "gl": "FI",
               "google_completion_check": 1}
        for provider in ("skyscanner", "google_flights"):
            fare = public_fare(row, provider)
            self.assertIsNone(fare["return_date"])
            self.assertEqual(fare["price_eur"], 420)
            self.assertNotIn("270109", fare["search_url"])

    def test_snapshot_excludes_old_and_unrequested_fares(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "google").mkdir()
            row = {"origin": "HEL", "destination": "HAN", "departure_date": "2026-12-09",
                   "return_date": "2027-01-09", "fetched_at": "2026-10-04T06:00:00+00:00",
                   "status": "observed", "lowest_observed_price_eur": 420,
                   "completion_version": 3, "completion_evidence": "dom_settled", "gl": "FI",
                   "google_completion_check": 1}
            path = directory / "google/2026-12-09_2027-01-09.json"
            path.write_text(json.dumps(row))
            jobs = SearchJobs(directory)
            jobs.jobs["test"] = {"id": "test", "status": "running", "directory": directory,
                                 "query": validate_search(QUERY | {"flex_days": 0}, TODAY),
                                 "started_at": "2026-10-04T07:00:00+00:00"}
            self.assertEqual(jobs.snapshot("test")["fares"], [])
            path.write_text(json.dumps(row | {"fetched_at": "2026-10-04T08:00:00+00:00"}))
            self.assertEqual(len(jobs.snapshot("test")["fares"]), 1)
            path.write_text(json.dumps(row | {"fetched_at": "2026-10-04T08:00:00+00:00", "destination": "BKK"}))
            self.assertEqual(jobs.snapshot("test")["fares"], [])


class LocalHttpTests(unittest.TestCase):
    def setUp(self):
        self.jobs = Mock()
        self.jobs.lock = threading.RLock()
        self.jobs.jobs = {}
        self.jobs.start.return_value = {"id": "test", "status": "running"}
        self.server = LocalServer(0, self.jobs)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, method, path, headers=None, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        data = response.read()
        conn.close()
        return response.status, data

    def test_cross_origin_and_wrong_host_cannot_trigger_scripts(self):
        headers = {"Origin": "https://attacker.example", "Content-Type": "application/json",
                   "X-Flight-Token": self.server.token}
        self.assertEqual(self.request("POST", "/api/jobs", headers, "{}")[0], 403)
        self.assertEqual(self.request("GET", "/api/session", {"Host": "attacker.example"})[0], 403)
        headers["Origin"] = self.origin
        headers["X-Flight-Token"] = "wrong"
        self.assertEqual(self.request("POST", "/api/jobs", headers, "{}")[0], 403)
        self.jobs.start.assert_not_called()

    def test_cross_site_navigation_can_open_ui_but_cannot_read_session(self):
        headers = {"Sec-Fetch-Site": "cross-site", "Origin": "https://example.com"}
        self.assertEqual(self.request("GET", "/", headers)[0], 200)
        self.assertEqual(self.request("GET", "/api/session", headers)[0], 403)

    def test_same_origin_search_requires_session_token(self):
        code, body = self.request("GET", "/api/session")
        self.assertEqual(code, 200)
        token = json.loads(body)["token"]
        headers = {"Origin": self.origin, "Content-Type": "application/json", "X-Flight-Token": token}
        self.assertEqual(self.request("POST", "/api/jobs", headers, json.dumps(QUERY))[0], 202)
        self.jobs.start.assert_called_once_with(QUERY)

    def test_static_server_never_exposes_repository_or_profiles(self):
        for path in ("/../config/config.toml", "/var/local-ui/profiles/cookies", "/data/fares.json", "/.git/config"):
            self.assertEqual(self.request("GET", path)[0], 404)


if __name__ == "__main__":
    unittest.main()
