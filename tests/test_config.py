"""TOML configuration loading, trip-file resolution and date-pair generation."""

import datetime as dt
import tempfile
import unittest
from pathlib import Path

from src.config import AppConfig, TripConfig, load_config, resolve_trip_file


def write_toml(content: str) -> Path:
    with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False, encoding="utf-8") as handle:
        handle.write(content)
        return Path(handle.name)


class DefaultConfigTests(unittest.TestCase):
    def test_shipped_config_loads(self) -> None:
        cfg = load_config()
        self.assertIsInstance(cfg, AppConfig)
        self.assertEqual(len(cfg.trip.origin), 3)
        self.assertGreater(cfg.trip.min_stay_nights, 0)
        self.assertIn(cfg.trip.date_mode, ["range", "window", "exact"])
        self.assertIn(cfg.execution.strategy, ["parallel", "sequential"])
        self.assertIsInstance(cfg.execution.skip_existing, bool)
        self.assertIsInstance(cfg.execution.auto_compare, bool)

    def test_google_and_skyscanner_use_separate_profiles_and_result_dirs(self) -> None:
        cfg = load_config()
        self.assertNotEqual(cfg.google_flights.resolved_profile_dir(), cfg.skyscanner.resolved_profile_dir())
        self.assertNotEqual(cfg.google_flights.resolved_results_dir(), cfg.skyscanner.resolved_results_dir())

    def test_point_of_sale_is_normalised(self) -> None:
        cfg = load_config(write_toml('[google_flights]\ngl = " se "\n'))
        self.assertEqual(cfg.google_flights.gl, "SE")


class TripFileTests(unittest.TestCase):
    def test_default_trip_file_is_hel_han(self) -> None:
        cfg = load_config()
        self.assertEqual(cfg.trip.origin, "HEL")
        self.assertEqual(cfg.trip.dest, "HAN")
        self.assertEqual(cfg.trip.min_stay_nights, 21)
        self.assertEqual(cfg.trip_file, "HEL_HAN.toml")

    def test_trip_file_can_be_selected_by_bare_name(self) -> None:
        cfg = load_config(trip_path="PHL_HAN.toml")
        self.assertEqual(cfg.trip.origin, "PHL")
        self.assertEqual(cfg.trip.dest, "HAN")
        self.assertEqual(cfg.trip.min_stay_nights, 18)
        self.assertEqual(cfg.trip_file, "PHL_HAN.toml")

    def test_config_toml_can_name_the_trip_file(self) -> None:
        path = write_toml('trip_file = "PHL_HAN.toml"\n\n[execution]\nstrategy = "parallel"\n')
        try:
            cfg = load_config(path)
            self.assertEqual(cfg.trip.origin, "PHL")
            self.assertEqual(cfg.trip.min_stay_nights, 18)
        finally:
            path.unlink()

    def test_inline_trip_table_is_honoured(self) -> None:
        path = write_toml('[trip]\norigin = "OUL"\ndest = "BKK"\nmin_stay_nights = 14\n')
        try:
            cfg = load_config(path)
            self.assertEqual(cfg.trip.origin, "OUL")
            self.assertEqual(cfg.trip.dest, "BKK")
            self.assertIsNone(cfg.trip_file)
        finally:
            path.unlink()

    def test_unknown_trip_file_resolves_to_none(self) -> None:
        self.assertIsNone(resolve_trip_file("NOPE.toml", Path(".")))

    def test_every_shipped_trip_config_generates_pairs(self) -> None:
        for name in ("HEL_HAN.toml", "PHL_HAN.toml", "SIN_HAN.toml", "HEL_BRU.toml", "AMS_HEL.toml"):
            with self.subTest(trip=name):
                cfg = load_config(trip_path=name)
                self.assertGreater(len(cfg.trip.get_search_pairs()), 0, f"{name} produced no pairs")


class CustomConfigTests(unittest.TestCase):
    def test_all_sections_round_trip(self) -> None:
        path = write_toml(
            """
[trip]
origin = "OUL"
dest = "BKK"
min_stay_nights = 14
date_mode = "range"
depart_from = "2026-11-01"
depart_to = "2026-11-03"
return_from = "2026-11-20"
return_to = "2026-11-22"

[execution]
strategy = "sequential"
skip_existing = false
auto_compare = false

[google_flights]
enabled = false
delay_seconds = 5

[skyscanner]
enabled = true
delay_seconds = 6
poll_wait_seconds = 45
"""
        )
        try:
            cfg = load_config(path)
            self.assertEqual(cfg.trip.origin, "OUL")
            self.assertEqual(cfg.trip.min_stay_nights, 14)
            self.assertEqual(cfg.execution.strategy, "sequential")
            self.assertFalse(cfg.execution.skip_existing)
            self.assertFalse(cfg.execution.auto_compare)
            self.assertFalse(cfg.google_flights.enabled)
            self.assertEqual(cfg.google_flights.delay_seconds, 5)
            self.assertTrue(cfg.skyscanner.enabled)
            self.assertEqual(cfg.skyscanner.delay_seconds, 6)
            self.assertEqual(cfg.skyscanner.poll_wait_seconds, 45)
            self.assertEqual(len(cfg.trip.get_search_pairs()), 9)  # 3 departures x 3 returns
        finally:
            path.unlink()

    def test_blank_browser_executable_means_auto_detect(self) -> None:
        path = write_toml('[execution]\nbrowser_executable = "   "\n')
        try:
            self.assertIsNone(load_config(path).execution.browser_executable)
        finally:
            path.unlink()

    def test_missing_config_file_falls_back_to_defaults(self) -> None:
        cfg = load_config(Path("/nonexistent/config.toml"))
        self.assertEqual(cfg.trip.origin, "HEL")


class SearchPairTests(unittest.TestCase):
    def test_range_mode(self) -> None:
        trip = TripConfig(date_mode="range")
        self.assertEqual(len(trip.get_search_pairs()), 25)

    def test_window_mode(self) -> None:
        trip = TripConfig(date_mode="window")
        self.assertEqual(len(trip.get_search_pairs()), 66)

    def test_exact_mode_uses_only_the_listed_pairs(self) -> None:
        trip = TripConfig(
            date_mode="exact",
            exact_pairs=[["2026-12-09", "2027-01-06"], ["2026-12-09", "2027-01-07"]],
        )
        pairs = trip.get_search_pairs()
        self.assertEqual(len(pairs), 2)
        self.assertEqual(pairs[0], (dt.date(2026, 12, 9), dt.date(2027, 1, 6)))

    def test_exact_mode_without_pairs_falls_back_to_range(self) -> None:
        self.assertEqual(len(TripConfig(date_mode="exact", exact_pairs=[]).get_search_pairs()), 25)

    def test_one_way_trips_have_no_return_leg(self) -> None:
        trip = TripConfig(
            trip_type="one-way",
            depart_from="2026-12-15",
            depart_to="2026-12-19",
            min_stay_nights=0,
        )
        pairs = trip.get_search_pairs()
        self.assertEqual(len(pairs), 5)
        self.assertTrue(all(return_date is None for _, return_date in pairs))

    def test_one_way_without_departure_dates_yields_nothing(self) -> None:
        self.assertEqual(TripConfig(trip_type="one-way", depart_from=None).get_search_pairs(), [])


if __name__ == "__main__":
    unittest.main()
