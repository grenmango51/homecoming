import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from src import google_flights
from src import skyscanner


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
        self.assertEqual(skyscanner.parse_numeric_price("1.001 €"), 1001.0)
        self.assertEqual(skyscanner.parse_numeric_price("€ 1,234.50"), 1234.50)
        self.assertEqual(skyscanner.parse_numeric_price("invalid"), None)

    def test_cheapest_tab_price(self) -> None:
        text1 = "Halvin alk. 782 € Paras alk. 850 € Nopein alk. 1,100 €"
        self.assertEqual(skyscanner.cheapest_tab_price(text1), 782.0)

        text2 = "Cheapest from €795 Best €850 Fastest €1,200"
        self.assertEqual(skyscanner.cheapest_tab_price(text2), 795.0)

        text3 = "No tabs visible here"
        self.assertIsNone(skyscanner.cheapest_tab_price(text3))

    def test_duration_minutes(self) -> None:
        self.assertEqual(skyscanner.duration_minutes("17 t 35 min"), 1055)
        self.assertEqual(skyscanner.duration_minutes("17 tuntia 35 minuuttia"), 1055)
        self.assertEqual(skyscanner.duration_minutes("15h 20m"), 920)
        self.assertEqual(skyscanner.duration_minutes("14 hr 35 min"), 875)

    def test_page_status(self) -> None:
        self.assertEqual(skyscanner.page_status("https://skyscanner.net/flights", "12 results returned"), "observed")
        self.assertEqual(skyscanner.page_status("https://skyscanner.net/flights", "Halvin alk. 782 €"), "observed")
        self.assertEqual(skyscanner.page_status("https://skyscanner.net/flights", "Please verify you are human"), "user_action_required")
        self.assertEqual(skyscanner.page_status("https://skyscanner.net/flights/captcha", "anything"), "user_action_required")
        self.assertEqual(skyscanner.page_status("https://skyscanner.net/flights", "Oops, something went wrong"), "incomplete")

    def test_challenge_detection(self) -> None:
        self.assertTrue(skyscanner.is_challenge_page("https://skyscanner.net/sttc/px/captcha", "anything"))
        self.assertTrue(skyscanner.is_challenge_page("https://skyscanner.net", "oletko oikea henkilö vai robotti"))
        self.assertTrue(skyscanner.is_challenge_page("https://skyscanner.net", "Press & Hold"))
        self.assertFalse(skyscanner.is_challenge_page("https://skyscanner.net/flights", "Cheapest from €782"))

    def test_itinerary_json_parsing(self) -> None:
        sample_itin = {
            "price": {"raw": 782.0, "formatted": "782 €"},
            "legs": [
                {
                    "departure": "2026-12-09T17:00:00",
                    "arrival": "2026-12-10T14:35:00",
                    "durationInMinutes": 1055,
                    "stopCount": 1,
                    "carriers": {"marketing": [{"name": "Qatar Airways"}]},
                }
            ],
            "pricingOptions": [{"agentName": "Trip.com", "price": {"raw": 782.0}}],
        }
        parsed = skyscanner.parse_itinerary_json(sample_itin)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["price_eur"], 782.0)
        self.assertEqual(parsed["legs"][0]["duration_minutes"], 1055)
        self.assertEqual(parsed["legs"][0]["carriers"], ["Qatar Airways"])
        self.assertEqual(parsed["sample_deals"][0]["seller"], "Trip.com")

    def test_daily_report_tracking(self) -> None:
        obs = {
            "departure_date": "2026-12-09",
            "return_date": "2027-01-09",
            "stay_nights": 31,
            "status": "observed",
            "lowest_observed_price_eur": 782.0,
            "itinerary_count": 10,
            "fetched_at": "2026-09-07T00:00:00+00:00",
        }
        with tempfile.TemporaryDirectory() as td:
            j_path, c_path = skyscanner.write_daily_report(Path(td), [obs])
            data = json.loads(j_path.read_text(encoding="utf-8"))
            self.assertEqual(data["total_pairs_scanned"], 1)
            self.assertEqual(data["observed_pairs"], 1)
            self.assertEqual(len(data["observations"]), 1)
            self.assertEqual(len(c_path.read_text(encoding="utf-8").splitlines()), 2)


if __name__ == "__main__":
    unittest.main()
