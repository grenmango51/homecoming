import json
import tempfile
import unittest
from pathlib import Path

from src import compare as cf


class FlightComparisonTests(unittest.TestCase):
    def test_compare_records_skyscanner_cheaper(self) -> None:
        google = {
            ("2026-12-09", "2027-01-09"): {
                "departure_date": "2026-12-09",
                "return_date": "2027-01-09",
                "stay_nights": 31,
                "status": "observed",
                "price_eur": 800.0,
            }
        }
        skyscanner = {
            ("2026-12-09", "2027-01-09"): {
                "departure_date": "2026-12-09",
                "return_date": "2027-01-09",
                "stay_nights": 31,
                "status": "observed",
                "price_eur": 782.0,
            }
        }
        comps = cf.compare_records(google, skyscanner)
        self.assertEqual(len(comps), 1)
        c = comps[0]
        self.assertEqual(c["winner"], "Skyscanner")
        self.assertEqual(c["cheapest_price_eur"], 782.0)
        self.assertEqual(c["savings_eur"], 18.0)
        self.assertEqual(c["skyscanner_savings_eur"], 18.0)
        self.assertEqual(c["savings_pct"], 2.2)

    def test_compare_records_google_cheaper(self) -> None:
        google = {
            ("2026-12-10", "2027-01-05"): {
                "departure_date": "2026-12-10",
                "return_date": "2027-01-05",
                "stay_nights": 26,
                "status": "observed",
                "price_eur": 851.0,
            }
        }
        skyscanner = {
            ("2026-12-10", "2027-01-05"): {
                "departure_date": "2026-12-10",
                "return_date": "2027-01-05",
                "stay_nights": 26,
                "status": "observed",
                "price_eur": 890.0,
            }
        }
        comps = cf.compare_records(google, skyscanner)
        c = comps[0]
        self.assertEqual(c["winner"], "Google Flights")
        self.assertEqual(c["cheapest_price_eur"], 851.0)
        self.assertEqual(c["savings_eur"], 39.0)
        self.assertEqual(c["skyscanner_savings_eur"], -39.0)
        self.assertEqual(c["savings_pct"], 4.4)

    def test_compare_records_tie(self) -> None:
        google = {
            ("2026-12-11", "2027-01-06"): {
                "departure_date": "2026-12-11",
                "return_date": "2027-01-06",
                "stay_nights": 26,
                "status": "observed",
                "price_eur": 920.0,
            }
        }
        skyscanner = {
            ("2026-12-11", "2027-01-06"): {
                "departure_date": "2026-12-11",
                "return_date": "2027-01-06",
                "stay_nights": 26,
                "status": "observed",
                "price_eur": 920.0,
            }
        }
        comps = cf.compare_records(google, skyscanner)
        c = comps[0]
        self.assertEqual(c["winner"], "Tie")
        self.assertEqual(c["cheapest_price_eur"], 920.0)
        self.assertEqual(c["savings_eur"], 0.0)
        self.assertEqual(c["skyscanner_savings_eur"], 0.0)

    def test_compare_records_unpaired(self) -> None:
        google = {
            ("2026-12-09", "2027-01-09"): {
                "departure_date": "2026-12-09",
                "return_date": "2027-01-09",
                "stay_nights": 31,
                "status": "observed",
                "price_eur": 800.0,
            }
        }
        skyscanner = {}
        comps = cf.compare_records(google, skyscanner)
        self.assertEqual(len(comps), 1)
        self.assertEqual(comps[0]["winner"], "Google Flights (Only)")
        self.assertEqual(comps[0]["cheapest_price_eur"], 800.0)
        self.assertIsNone(comps[0]["savings_eur"])

    def test_render_table(self) -> None:
        comps = [
            {
                "departure_date": "2026-12-09",
                "return_date": "2027-01-09",
                "stay_nights": 31,
                "google_price_eur": 800.0,
                "skyscanner_price_eur": 782.0,
                "cheapest_price_eur": 782.0,
                "winner": "Skyscanner",
                "skyscanner_savings_eur": 18.0,
            }
        ]
        table = cf.render_table(comps)
        self.assertIn("2026-12-09", table)
        self.assertIn("2027-01-09", table)
        self.assertIn("€800", table)
        self.assertIn("€782", table)
        self.assertIn("Skyscanner", table)
        self.assertIn("-€18 (SS)", table)

    def test_write_comparison_reports(self) -> None:
        comps = [
            {
                "departure_date": "2026-12-09",
                "return_date": "2027-01-09",
                "stay_nights": 31,
                "google_price_eur": 800.0,
                "skyscanner_price_eur": 782.0,
                "cheapest_price_eur": 782.0,
                "winner": "Skyscanner",
                "skyscanner_savings_eur": 18.0,
                "savings_eur": 18.0,
                "savings_pct": 2.2,
                "google_status": "observed",
                "skyscanner_status": "observed",
            }
        ]
        with tempfile.TemporaryDirectory() as td:
            csv_path, json_path = cf.write_comparison_reports(Path(td), comps, stamp="2026-09-08")
            self.assertTrue(csv_path.is_file())
            self.assertTrue(json_path.is_file())

            data = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(data["total_pairs"], 1)
            self.assertEqual(data["skyscanner_cheaper_count"], 1)
            self.assertEqual(data["overall_lowest_fare_eur"], 782.0)
            self.assertEqual(data["max_skyscanner_saving_eur"], 18.0)

    def test_compare_records_with_google_se_winner(self) -> None:
        google_fi = {
            ("2026-12-09", "2027-01-09"): {
                "departure_date": "2026-12-09",
                "return_date": "2027-01-09",
                "stay_nights": 31,
                "status": "observed",
                "price_eur": 800.0,
            }
        }
        google_se = {
            ("2026-12-09", "2027-01-09"): {
                "departure_date": "2026-12-09",
                "return_date": "2027-01-09",
                "stay_nights": 31,
                "status": "observed",
                "price_eur": 775.0,
            }
        }
        skyscanner = {
            ("2026-12-09", "2027-01-09"): {
                "departure_date": "2026-12-09",
                "return_date": "2027-01-09",
                "stay_nights": 31,
                "status": "observed",
                "price_eur": 785.0,
            }
        }
        comps = cf.compare_records(google_fi, skyscanner, google_se_data=google_se)
        self.assertEqual(len(comps), 1)
        c = comps[0]
        self.assertEqual(c["winner"], "Google Flights (SE)")
        self.assertEqual(c["cheapest_price_eur"], 775.0)
        self.assertEqual(c["savings_eur"], 10.0)  # 785 - 775
        self.assertEqual(c["google_se_price_eur"], 775.0)

        # Verify 3-way table rendering
        table = cf.render_table(comps)
        self.assertIn("Google FI (€)", table)
        self.assertIn("Google SE (€)", table)
        self.assertIn("€775", table)
        self.assertIn("Google Flights (SE)", table)
        self.assertIn("-€10 (SE)", table)

    def test_write_comparison_reports_with_se(self) -> None:
        comps = [
            {
                "departure_date": "2026-12-09",
                "return_date": "2027-01-09",
                "stay_nights": 31,
                "google_price_eur": 800.0,
                "google_se_price_eur": 775.0,
                "skyscanner_price_eur": 785.0,
                "cheapest_price_eur": 775.0,
                "winner": "Google Flights (SE)",
                "skyscanner_savings_eur": 15.0,
                "savings_eur": 10.0,
                "savings_pct": 1.3,
                "google_status": "observed",
                "google_se_status": "observed",
                "skyscanner_status": "observed",
            }
        ]
        with tempfile.TemporaryDirectory() as td:
            csv_path, json_path = cf.write_comparison_reports(Path(td), comps, stamp="2026-09-14")
            self.assertTrue(csv_path.is_file())
            csv_text = csv_path.read_text(encoding="utf-8")
            self.assertIn("google_se_price_eur", csv_text)
            self.assertIn("775.0", csv_text)

            data = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(data["google_se_cheaper_count"], 1)
            self.assertEqual(data["max_google_se_saving_eur"], 10.0)


if __name__ == "__main__":
    unittest.main()

