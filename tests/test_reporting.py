"""Observation persistence, daily reports and POS summary rows."""

import csv
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from src.reporting import (
    DAILY_REPORT_FIELDS,
    SKYSCANNER_REPORT_FIELDS,
    checkpoint_filename,
    error_observation,
    load_checkpoint,
    load_summary_records,
    pair_filename,
    read_json,
    save_observation,
    summary_record,
    write_daily_report,
    write_summary_reports,
)

OBSERVATION = {
    "departure_date": "2026-12-13",
    "return_date": "2027-01-09",
    "stay_nights": 27,
    "status": "observed",
    "lowest_observed_price_eur": 800.0,
    "protection_label": "not_flagged_by_google",
    "fetched_at": "2026-09-07T00:00:00+00:00",
}


class FilenameTests(unittest.TestCase):
    def test_pair_filename(self) -> None:
        self.assertEqual(pair_filename("2026-12-09", "2027-01-05"), "2026-12-09_2027-01-05.json")

    def test_checkpoint_filename_round_trip(self) -> None:
        self.assertEqual(
            checkpoint_filename("HEL", "HAN", dt.date(2026, 12, 9), dt.date(2027, 1, 5), "FI"),
            "HEL_HAN_2026-12-09_2027-01-05_gl-FI.json",
        )

    def test_checkpoint_filename_one_way(self) -> None:
        self.assertEqual(
            checkpoint_filename("HEL", "BRU", dt.date(2026, 12, 15), None, "FI"),
            "HEL_BRU_2026-12-15_oneway_gl-FI.json",
        )


class DailyReportTests(unittest.TestCase):
    def test_google_report_has_one_data_row_per_observation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            json_path, csv_path = write_daily_report(
                Path(directory), [OBSERVATION], source="Google Flights UI", stem="daily_fare_report"
            )
            report = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(report["total_pairs_scanned"], 1)
            self.assertEqual(report["observed_pairs"], 1)
            self.assertEqual(len(csv_path.read_text(encoding="utf-8").splitlines()), 2)

    def test_rows_are_sorted_by_date_pair(self) -> None:
        later = dict(OBSERVATION, departure_date="2026-12-20")
        earlier = dict(OBSERVATION, departure_date="2026-12-01")
        with tempfile.TemporaryDirectory() as directory:
            json_path, _ = write_daily_report(
                Path(directory), [later, earlier], source="Google Flights UI", stem="daily_fare_report"
            )
            rows = json.loads(json_path.read_text(encoding="utf-8"))["observations"]
            self.assertEqual([r["departure_date"] for r in rows], ["2026-12-01", "2026-12-20"])

    def test_skyscanner_itinerary_count_falls_back_to_the_card_count(self) -> None:
        observation = dict(OBSERVATION, candidate_cards=[{}, {}, {}])
        observation.pop("protection_label")
        with tempfile.TemporaryDirectory() as directory:
            _, csv_path = write_daily_report(
                Path(directory),
                [observation],
                source="Skyscanner UI",
                stem="daily_fare_report_skyscanner",
                fieldnames=SKYSCANNER_REPORT_FIELDS,
                fallbacks={
                    "itinerary_count": lambda item: item.get(
                        "itinerary_count", len(item.get("candidate_cards", []))
                    )
                },
            )
            row = next(csv.DictReader(csv_path.read_text(encoding="utf-8").splitlines()))
            self.assertEqual(row["itinerary_count"], "3")

    def test_daily_fields_cover_what_the_comparison_reads_back(self) -> None:
        for field in ("departure_date", "return_date", "status", "lowest_observed_price_eur"):
            self.assertIn(field, DAILY_REPORT_FIELDS)


class CheckpointTests(unittest.TestCase):
    def test_save_and_reload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = save_observation(Path(directory), OBSERVATION)
            self.assertEqual(path.name, "2026-12-13_2027-01-09.json")
            self.assertEqual(load_checkpoint(path), OBSERVATION)

    def test_missing_and_corrupt_files_read_as_absent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(load_checkpoint(Path(directory) / "nope.json"))
            corrupt = Path(directory) / "bad.json"
            corrupt.write_text("{not json", encoding="utf-8")
            self.assertIsNone(load_checkpoint(corrupt))
            self.assertEqual(read_json(corrupt, default=[]), [])

    def test_summary_records_resume_from_disk(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows = [summary_record(
                {"status": "observed", "lowest_price_eur": 900.0, "cards": [], "fetched_at": "x"},
                origin="HEL", destination="HAN",
                departure=dt.date(2026, 12, 9), return_date=dt.date(2027, 1, 5),
                gl="FI", region_name="Finland",
            )]
            write_summary_reports(root, rows, "summary_report")
            self.assertEqual(load_summary_records(root, "summary_report"), rows)

    def test_summary_records_default_to_empty(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(load_summary_records(Path(directory), "summary_report"), [])


class SummaryRecordTests(unittest.TestCase):
    def test_flattens_the_cheapest_card(self) -> None:
        observation = {
            "status": "observed",
            "lowest_price_eur": 782.0,
            "fetched_at": "2026-09-07T00:00:00+00:00",
            "cards": [{"carriers": ["Finnair", "Qatar Airways"], "duration_minutes": 975, "stops": 1, "layovers": ["DOH"]}],
        }
        row = summary_record(
            observation,
            origin="HEL", destination="HAN",
            departure=dt.date(2026, 12, 9), return_date=dt.date(2027, 1, 5),
            gl="FI", region_name="Finland",
        )
        self.assertEqual(row["carrier"], "Finnair, Qatar Airways")
        self.assertEqual(row["layovers"], "DOH")
        self.assertEqual(row["stay_nights"], 27)
        self.assertEqual(row["lowest_price_eur"], 782.0)

    def test_one_way_rows_record_no_return_and_no_stay(self) -> None:
        row = summary_record(
            {"status": "observed", "lowest_price_eur": 210.0, "cards": []},
            origin="HEL", destination="BRU",
            departure=dt.date(2026, 12, 15), return_date=None,
            gl="FI", region_name="Finland",
        )
        self.assertEqual(row["return_date"], "oneway")
        self.assertEqual(row["stay_nights"], 0)
        self.assertEqual(row["carrier"], "Unknown")


class ErrorObservationTests(unittest.TestCase):
    def test_records_the_exception_and_stays_retryable(self) -> None:
        observation = error_observation(
            source="Google Flights UI",
            origin="HEL", destination="HAN",
            departure=dt.date(2026, 12, 9), return_date=dt.date(2027, 1, 5),
            error=TimeoutError("page never settled"),
            note="needs a retry",
        )
        self.assertEqual(observation["status"], "error")
        self.assertIsNone(observation["lowest_observed_price_eur"])
        self.assertIn("TimeoutError", observation["error"])
        self.assertEqual(observation["stay_nights"], 27)

    def test_extra_fields_are_merged(self) -> None:
        observation = error_observation(
            source="Skyscanner UI",
            origin="HEL", destination="HAN",
            departure=dt.date(2026, 12, 9), return_date=dt.date(2027, 1, 5),
            error=ValueError("boom"), note="retry", extra={"itinerary_count": 0},
        )
        self.assertEqual(observation["itinerary_count"], 0)


if __name__ == "__main__":
    unittest.main()
