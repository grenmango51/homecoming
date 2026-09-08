import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

import find_flights as google_flights
import find_flights_skyscanner as skyscanner


class SkyscannerScannerTests(unittest.TestCase):
    def test_search_pairs_match_google_flights_exactly(self) -> None:
        """Verify Skyscanner builds the exact same 100% identical date pairs as Google Flights."""
        # Range mode
        gf_range = google_flights.build_search_pairs(
            depart_from="2026-12-09",
            depart_to="2026-12-13",
            return_from="2027-01-05",
            return_to="2027-01-09",
            min_stay_nights=21,
        )
        ss_range = skyscanner.build_search_pairs(
            depart_from="2026-12-09",
            depart_to="2026-12-13",
            return_from="2027-01-05",
            return_to="2027-01-09",
            min_stay_nights=21,
        )
        self.assertEqual(gf_range, ss_range)
        self.assertEqual(len(ss_range), 25)

        # Window mode
        gf_window = google_flights.build_search_pairs(
            window_start="2026-12-09",
            window_end="2027-01-09",
            min_stay_nights=21,
        )
        ss_window = skyscanner.build_search_pairs(
            window_start="2026-12-09",
            window_end="2027-01-09",
            min_stay_nights=21,
        )
        self.assertEqual(gf_window, ss_window)
        self.assertEqual(len(ss_window), 66)

    def test_to_yymmdd_formatting(self) -> None:
        self.assertEqual(skyscanner.to_yymmdd(dt.date(2026, 12, 9)), "261209")
        self.assertEqual(skyscanner.to_yymmdd(dt.date(2027, 1, 9)), "270109")

    def test_flight_search_url(self) -> None:
        url = skyscanner.flight_search_url("HEL", "HAN", dt.date(2026, 12, 9), dt.date(2027, 1, 9))
        expected_part = "https://www.skyscanner.net/transport/flights/hel/han/261209/270109/"
        self.assertTrue(url.startswith(expected_part))
        self.assertIn("adultsv2=1", url)
        self.assertIn("cabinclass=economy", url)

    def test_parse_numeric_price(self) -> None:
        self.assertEqual(skyscanner.parse_numeric_price("782"), 782.0)
        self.assertEqual(skyscanner.parse_numeric_price("782 €"), 782.0)
        self.assertEqual(skyscanner.parse_numeric_price("1,001"), 1001.0)
        self.assertEqual(skyscanner.parse_numeric_price("1 001 €"), 1001.0)
        self.assertEqual(skyscanner.parse_numeric_price("1.336 €"), 1336.0)
        self.assertEqual(skyscanner.parse_numeric_price("1 087"), 1087.0)
        # Below bounds
        self.assertIsNone(skyscanner.parse_numeric_price("15"))
        # Non-numeric
        self.assertIsNone(skyscanner.parse_numeric_price("abc"))

    def test_cheapest_tab_price(self) -> None:
        # Finnish
        self.assertEqual(skyscanner.cheapest_tab_price("Halvin 782 €"), 782.0)
        self.assertEqual(skyscanner.cheapest_tab_price("Halvin alk. 782 €"), 782.0)
        self.assertEqual(skyscanner.cheapest_tab_price("Halvin alkaen 782 €"), 782.0)
        # English
        self.assertEqual(skyscanner.cheapest_tab_price("Cheapest 782 €"), 782.0)
        self.assertEqual(skyscanner.cheapest_tab_price("Cheapest from €782"), 782.0)
        self.assertEqual(skyscanner.cheapest_tab_price("Cheapest €1,001"), 1001.0)
        # None
        self.assertIsNone(skyscanner.cheapest_tab_price("Paras 950 €"))

    def test_duration_minutes(self) -> None:
        self.assertEqual(skyscanner.duration_minutes("17 t 35 min"), 1055)
        self.assertEqual(skyscanner.duration_minutes("17 tuntia 35 minuuttia"), 1055)
        self.assertEqual(skyscanner.duration_minutes("15h 20m"), 920)
        self.assertEqual(skyscanner.duration_minutes("14 hr 35 min"), 875)

    def test_challenge_detection(self) -> None:
        self.assertTrue(skyscanner.is_challenge_page("https://www.skyscanner.fi/sttc/px/captcha", ""))
        self.assertTrue(skyscanner.is_challenge_page("https://www.skyscanner.fi", "Oletko oikea henkilö vai robotti?"))
        self.assertTrue(skyscanner.is_challenge_page("https://www.skyscanner.fi", "Press & Hold to confirm you are human"))
        self.assertFalse(skyscanner.is_challenge_page("https://www.skyscanner.fi/transport/flights/hel/han", "15 tulosta löytyi"))

    def test_page_status(self) -> None:
        self.assertEqual(skyscanner.page_status("https://skyscanner.fi", "15 tulosta löytyi Halvin 782 €"), "observed")
        self.assertEqual(skyscanner.page_status("https://skyscanner.fi", "Cheapest flights results"), "observed")
        self.assertEqual(skyscanner.page_status("https://skyscanner.fi", "Oletko oikea henkilö vai robotti"), "user_action_required")

    def test_itinerary_json_parsing(self) -> None:
        sample_itin = {
            "price": {"raw": 782.0, "formatted": "782 €"},
            "legs": [
                {
                    "departure": "2026-12-09T07:40:00",
                    "arrival": "2026-12-10T06:15:00",
                    "durationInMinutes": 1055,
                    "stopCount": 1,
                    "carriers": {"marketing": [{"name": "Finnair"}]},
                },
                {
                    "departure": "2027-01-09T06:30:00",
                    "arrival": "2027-01-09T22:25:00",
                    "durationInMinutes": 1255,
                    "stopCount": 1,
                    "carriers": {"marketing": [{"name": "Vietnam Airlines"}]},
                },
            ],
            "pricingOptions": [{"agentName": "Ebookers", "price": {"raw": 782.0}}],
        }
        parsed = skyscanner.parse_itinerary_json(sample_itin)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["price_eur"], 782.0)
        self.assertEqual(len(parsed["legs"]), 2)
        self.assertEqual(parsed["legs"][0]["duration_minutes"], 1055)
        self.assertEqual(parsed["sample_deals"][0]["seller"], "Ebookers")

    def test_daily_report_tracking(self) -> None:
        obs = {
            "departure_date": "2026-12-09",
            "return_date": "2027-01-09",
            "stay_nights": 31,
            "status": "observed",
            "lowest_observed_price_eur": 782.0,
            "protection_label": "not_flagged_by_skyscanner",
            "fetched_at": "2026-09-08T00:00:00+00:00",
            "candidate_cards": [],
        }
        with tempfile.TemporaryDirectory() as td:
            j_path, c_path = skyscanner.write_daily_report(Path(td), [obs], reference_price=782.0)
            data = json.loads(j_path.read_text(encoding="utf-8"))
            self.assertTrue(data["reference_matches_expected_price"])
            self.assertEqual(data["reference_price_eur"], 782.0)
            self.assertEqual(len(c_path.read_text(encoding="utf-8").splitlines()), 2)


if __name__ == "__main__":
    unittest.main()

