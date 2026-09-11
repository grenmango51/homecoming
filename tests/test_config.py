import tempfile
import unittest
from pathlib import Path

from src.config import load_config, DEFAULT_CONFIG_PATH, AppConfig


class ConfigTests(unittest.TestCase):
    def test_load_default_config(self) -> None:
        cfg = load_config()
        self.assertIsInstance(cfg, AppConfig)
        self.assertEqual(len(cfg.trip.origin), 3)
        self.assertEqual(cfg.trip.dest, "HAN")
        self.assertGreater(cfg.trip.min_stay_nights, 0)
        self.assertIn(cfg.trip.date_mode, ["range", "window"])
        self.assertIn(cfg.execution.strategy, ["parallel", "sequential"])
        self.assertIsInstance(cfg.execution.skip_existing, bool)
        self.assertIsInstance(cfg.execution.auto_compare, bool)

    def test_range_mode_generates_pairs(self) -> None:
        cfg = load_config()
        pairs = cfg.trip.get_search_pairs()
        self.assertGreater(len(pairs), 0)

    def test_window_mode_generates_pairs(self) -> None:
        cfg = load_config()
        cfg.trip.date_mode = "window"
        pairs = cfg.trip.get_search_pairs()
        self.assertGreater(len(pairs), 0)

    def test_load_custom_toml_file(self) -> None:
        custom_content = """
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
reference_price = 950.0

[skyscanner]
enabled = true
delay_seconds = 6
reference_price = 920.0
"""
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False, encoding="utf-8") as f:
            f.write(custom_content)
            temp_path = Path(f.name)

        try:
            cfg = load_config(temp_path)
            self.assertEqual(cfg.trip.origin, "OUL")
            self.assertEqual(cfg.trip.dest, "BKK")
            self.assertEqual(cfg.trip.min_stay_nights, 14)
            self.assertEqual(cfg.execution.strategy, "sequential")
            self.assertFalse(cfg.execution.skip_existing)
            self.assertFalse(cfg.execution.auto_compare)
            self.assertFalse(cfg.google_flights.enabled)
            self.assertEqual(cfg.google_flights.delay_seconds, 5)
            self.assertEqual(cfg.google_flights.reference_price, 950.0)
            self.assertTrue(cfg.skyscanner.enabled)
            self.assertEqual(cfg.skyscanner.delay_seconds, 6)
            self.assertEqual(cfg.skyscanner.reference_price, 920.0)
            pairs = cfg.trip.get_search_pairs()
            self.assertEqual(len(pairs), 9)  # 3 departures x 3 returns
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def test_disabled_reference_price_returns_none(self) -> None:
        custom_content = """
[google_flights]
reference_price = 0

[skyscanner]
reference_price = 0.0
"""
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False, encoding="utf-8") as f:
            f.write(custom_content)
            temp_path = Path(f.name)

        try:
            cfg = load_config(temp_path)
            self.assertIsNone(cfg.google_flights.reference_price)
            self.assertIsNone(cfg.skyscanner.reference_price)
        finally:
            if temp_path.exists():
                temp_path.unlink()


    def test_modular_hel_han_config(self) -> None:
        cfg = load_config()
        self.assertEqual(cfg.trip.origin, "HEL")
        self.assertEqual(cfg.trip.dest, "HAN")
        self.assertEqual(cfg.trip.min_stay_nights, 21)
        self.assertEqual(cfg.trip.depart_from, "2026-12-09")
        self.assertEqual(cfg.trip.depart_to, "2026-12-13")
        self.assertEqual(cfg.trip.return_from, "2027-01-05")
        self.assertEqual(cfg.trip.return_to, "2027-01-09")
        self.assertEqual(cfg.trip.window_start, "2026-12-09")
        self.assertEqual(cfg.trip.window_end, "2027-01-09")
        self.assertEqual(cfg.trip_file, "HEL_HAN.toml")

    def test_modular_phl_han_config(self) -> None:
        cfg = load_config(trip_path="PHL_HAN.toml")
        self.assertEqual(cfg.trip.origin, "PHL")
        self.assertEqual(cfg.trip.dest, "HAN")
        self.assertEqual(cfg.trip.min_stay_nights, 18)
        self.assertEqual(cfg.trip.depart_from, "2026-12-13")
        self.assertEqual(cfg.trip.depart_to, "2026-12-16")
        self.assertEqual(cfg.trip.return_from, "2027-01-09")
        self.assertEqual(cfg.trip.return_to, "2027-01-10")
        self.assertEqual(cfg.trip_file, "PHL_HAN.toml")

    def test_modular_trip_file_in_custom_config(self) -> None:
        custom_content = """
trip_file = "PHL_HAN.toml"

[execution]
strategy = "parallel"
"""
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False, encoding="utf-8") as f:
            f.write(custom_content)
            temp_path = Path(f.name)

        try:
            cfg = load_config(temp_path)
            self.assertEqual(cfg.trip.origin, "PHL")
            self.assertEqual(cfg.trip.dest, "HAN")
            self.assertEqual(cfg.trip.min_stay_nights, 18)
        finally:
            if temp_path.exists():
                temp_path.unlink()


if __name__ == "__main__":
    unittest.main()
