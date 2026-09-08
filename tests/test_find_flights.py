import json
import tempfile
import unittest
from pathlib import Path

from src import google_flights as flights


class FlightReportTests(unittest.TestCase):
    def test_default_window_has_25_eligible_pairs(self) -> None:
        pairs = [
            (departure, return_date)
            for departure in flights.date_range("2026-12-09", "2026-12-13")
            for return_date in flights.date_range("2027-01-05", "2027-01-09")
            if (return_date - departure).days >= 21
        ]
        self.assertEqual(len(pairs), 25)

    def test_build_search_pairs_window_mode_generates_66_pairs(self) -> None:
        pairs = flights.build_search_pairs(window_start="2026-12-09", window_end="2027-01-09", min_stay_nights=21)
        self.assertEqual(len(pairs), 66)
        self.assertEqual(pairs[0], (flights.dt.date(2026, 12, 9), flights.dt.date(2026, 12, 30)))
        self.assertEqual(pairs[-1], (flights.dt.date(2026, 12, 19), flights.dt.date(2027, 1, 9)))
        self.assertIn((flights.dt.date(2026, 12, 9), flights.dt.date(2027, 1, 9)), pairs)
        for dep, ret in pairs:
            self.assertGreaterEqual((ret - dep).days, 21)
            self.assertGreaterEqual(dep, flights.dt.date(2026, 12, 9))
            self.assertLessEqual(ret, flights.dt.date(2027, 1, 9))

    def test_build_search_pairs_range_mode_generates_25_pairs(self) -> None:
        pairs = flights.build_search_pairs(
            depart_from="2026-12-09",
            depart_to="2026-12-13",
            return_from="2027-01-05",
            return_to="2027-01-09",
            min_stay_nights=21,
        )
        self.assertEqual(len(pairs), 25)

    def test_parses_google_euro_text_in_both_accessible_formats(self) -> None:
        self.assertEqual(flights.money_values("Cheapest from €800. From 1,004 euros round trip total."), [800.0, 1004.0])

    def test_direct_query_url_contains_the_requested_dates(self) -> None:
        url = flights.flight_search_url("HEL", "HAN", flights.dt.date(2026, 12, 10), flights.dt.date(2027, 1, 8))
        encoded = url.split("tfs=", 1)[1].split("&", 1)[0]
        import base64
        raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        self.assertIn(b"2026-12-10", raw)
        self.assertIn(b"2027-01-08", raw)

    def test_tfs_template_is_valid_and_untruncated(self) -> None:
        import base64
        raw = base64.urlsafe_b64decode(flights.TFS_TEMPLATE + "=" * (-len(flights.TFS_TEMPLATE) % 4))
        self.assertIn(b"2026-12-09", raw)
        self.assertIn(b"2027-01-09", raw)
        self.assertIn(b"/m/03khn", raw)
        self.assertIn(b"/m/0fnff", raw)
        self.assertEqual(raw.count(b"2026-12-09"), 1)
        self.assertEqual(raw.count(b"2027-01-09"), 1)

    def test_daily_report_includes_reference_check_and_csv_rows(self) -> None:
        observation = {
            "departure_date": "2026-12-09",
            "return_date": "2027-01-09",
            "stay_nights": 31,
            "status": "observed",
            "lowest_observed_price_eur": 800.0,
            "protection_label": "not_flagged_by_google",
            "fetched_at": "2026-09-07T00:00:00+00:00",
        }
        with tempfile.TemporaryDirectory() as directory:
            json_path, csv_path = flights.write_daily_report(Path(directory), [observation], 800.0)
            report = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertTrue(report["reference_matches_expected_price"])
            self.assertEqual(len(csv_path.read_text(encoding="utf-8").splitlines()), 2)

    def test_duration_minutes_parses_various_formats(self) -> None:
        self.assertEqual(flights.duration_minutes("16 hr 15 min"), 975)
        self.assertEqual(flights.duration_minutes("19 hr"), 1140)
        self.assertEqual(flights.duration_minutes("2h 30m"), 150)
        self.assertEqual(flights.duration_minutes("15h"), 900)
        self.assertIsNone(flights.duration_minutes("no duration"))

    def test_cheapest_banner_price_parsing(self) -> None:
        self.assertEqual(flights.cheapest_banner_price("Cheapest from €1,064"), 1064.0)
        self.assertEqual(flights.cheapest_banner_price("Cheapest from €800"), 800.0)
        self.assertEqual(flights.cheapest_banner_price("cheapest eur 950"), 950.0)
        self.assertIsNone(flights.cheapest_banner_price("No flights available"))

    def test_page_status_classification(self) -> None:
        self.assertEqual(flights.page_status("6 results returned. Top departing flights"), "observed")
        self.assertEqual(flights.page_status("Cheapest from €1,064"), "observed")
        self.assertEqual(flights.page_status("Please verify you are human captcha"), "blocked")
        self.assertEqual(flights.page_status("Oops, something went wrong"), "incomplete")
        self.assertEqual(flights.page_status("random empty content"), "incomplete")

    def test_make_observation_uses_candidates_and_banner_without_footer_bleed(self) -> None:
        obs = flights.make_observation(
            origin="HEL",
            destination="HAN",
            departure=flights.dt.date(2026, 12, 9),
            return_date=flights.dt.date(2027, 1, 9),
            page_text="6 results returned. Top departing flights\nCheapest from €800\nPrivacy Terms €350",
            candidates=[
                "Finnair, Qatar Airways 16 hr 15 min HEL–HAN 1 stop €850 round trip separate tickets booked together",
                "Turkish Airlines 19 hr HEL–HAN 1 stop €1,004 round trip",
            ],
        )
        self.assertEqual(obs["lowest_observed_price_eur"], 800.0)
        self.assertEqual(obs["status"], "observed")
        self.assertEqual(obs["stay_nights"], 31)
        self.assertEqual(obs["protection_label"], "separate_tickets")
        self.assertEqual(len(obs["candidate_cards"]), 2)
        self.assertEqual(obs["candidate_cards"][0]["observed_duration_minutes"], 975)


if __name__ == "__main__":
    unittest.main()
