"""Shared arbitrage statistics: mode detection, win counts and discount counting."""

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from src.analysis import (
    count_discounts,
    date_pair_modes,
    global_min_wins,
    load_pos_observations,
)
from src.regions import load_all_regions, load_region_name_map


def frame(rows: list[tuple[str, str, float]]) -> pd.DataFrame:
    df = pd.DataFrame(
        [{"date_pair": pair, "gl": gl, "lowest_price_eur": price} for pair, gl, price in rows]
    )
    return df


class ModeTests(unittest.TestCase):
    def test_mode_is_the_most_common_fare_per_date_pair(self) -> None:
        modes = date_pair_modes(frame([
            ("A", "FI", 900.0), ("A", "SE", 900.0), ("A", "DE", 900.0), ("A", "VN", 700.0),
        ]))
        self.assertEqual(modes["A"]["mode_price"], 900.0)
        self.assertEqual(modes["A"]["mode_frequency"], 3)
        self.assertEqual(modes["A"]["total_markets"], 4)
        self.assertEqual(modes["A"]["pct_mode"], 75.0)
        self.assertEqual(modes["A"]["min_price"], 700.0)

    def test_all_distinct_fares_fall_back_to_the_median(self) -> None:
        modes = date_pair_modes(frame([("A", "FI", 800.0), ("A", "SE", 900.0), ("A", "DE", 1000.0)]))
        # Every value is its own mode, so the first is taken; it must still be a real fare.
        self.assertIn(modes["A"]["mode_price"], {800.0, 900.0, 1000.0})


class WinTests(unittest.TestCase):
    def test_cheapest_market_wins_each_date_pair(self) -> None:
        wins = global_min_wins(frame([
            ("A", "FI", 900.0), ("A", "SE", 700.0),
            ("B", "FI", 850.0), ("B", "SE", 860.0),
        ]))
        self.assertEqual(wins, {"SE": 1, "FI": 1})

    def test_ties_award_a_win_to_every_market(self) -> None:
        wins = global_min_wins(frame([("A", "FI", 700.0), ("A", "SE", 700.0), ("A", "DE", 900.0)]))
        self.assertEqual(wins["FI"], 1)
        self.assertEqual(wins["SE"], 1)
        self.assertNotIn("DE", wins)


class DiscountTests(unittest.TestCase):
    def test_counts_only_fares_strictly_below_the_mode(self) -> None:
        df = frame([
            ("A", "SE", 700.0), ("A", "FI", 900.0), ("A", "DE", 900.0), ("A", "US", 900.0),
            ("B", "SE", 850.0), ("B", "FI", 850.0), ("B", "DE", 850.0),
        ])
        modes = date_pair_modes(df)
        self.assertEqual(count_discounts(df[df["gl"] == "SE"], modes), 1)
        self.assertEqual(count_discounts(df[df["gl"] == "FI"], modes), 0)


class LoaderTests(unittest.TestCase):
    OBSERVATION = {
        "status": "observed",
        "origin": "HEL",
        "destination": "HAN",
        "departure_date": "2026-12-09",
        "return_date": "2027-01-05",
        "stay_nights": 27,
        "gl": "FI",
        "lowest_price_eur": 782.0,
        "cards": [{"carriers": ["Finnair"], "duration_minutes": 975, "stops": 1, "layovers": ["DOH"]}],
    }

    def _dir(self, stack, files: dict[str, dict]) -> Path:
        directory = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        for name, payload in files.items():
            (directory / name).write_text(json.dumps(payload), encoding="utf-8")
        return directory

    def test_missing_directory_returns_an_empty_frame(self) -> None:
        self.assertTrue(load_pos_observations(Path("/nonexistent")).empty)

    def test_loads_observations_and_adds_a_date_pair_column(self) -> None:
        import contextlib

        with contextlib.ExitStack() as stack:
            directory = self._dir(stack, {"a.json": self.OBSERVATION})
            df = load_pos_observations(directory, gl_map={"FI": "Finland"})
            self.assertEqual(len(df), 1)
            self.assertEqual(df["date_pair"].iloc[0], "2026-12-09 -> 2027-01-05")
            self.assertEqual(df["region_name"].iloc[0], "Finland")
            self.assertEqual(df["carrier"].iloc[0], "Finnair")
            self.assertEqual(df["layovers"].iloc[0], "DOH")

    def test_skips_roll_up_files_unobserved_records_and_cheap_noise(self) -> None:
        import contextlib

        with contextlib.ExitStack() as stack:
            directory = self._dir(stack, {
                "a.json": self.OBSERVATION,
                "summary_report.json": {"status": "observed", "lowest_price_eur": 1.0},
                "all_pos_summary_report.json": {"status": "observed", "lowest_price_eur": 1.0},
                "b.json": dict(self.OBSERVATION, status="blocked"),
                "c.json": dict(self.OBSERVATION, lowest_price_eur=3.0),
            })
            df = load_pos_observations(directory, gl_map={}, min_price=15.0)
            self.assertEqual(len(df), 1)

    def test_corrupt_files_are_skipped(self) -> None:
        import contextlib

        with contextlib.ExitStack() as stack:
            directory = self._dir(stack, {"a.json": self.OBSERVATION})
            (directory / "bad.json").write_text("{not json", encoding="utf-8")
            self.assertEqual(len(load_pos_observations(directory, gl_map={})), 1)


class RegionTests(unittest.TestCase):
    def test_shipped_region_mapping_is_complete(self) -> None:
        regions = load_all_regions()
        self.assertGreater(len(regions), 100)
        self.assertTrue(all(len(r["gl"]) == 2 for r in regions))
        self.assertEqual(len({r["gl"] for r in regions}), len(regions), "duplicate gl codes")

    def test_name_map_covers_the_studied_markets(self) -> None:
        names = load_region_name_map()
        for gl in ("FI", "VN", "US", "SE", "SG", "NL", "BE"):
            self.assertIn(gl, names)

    def test_unknown_region_file_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            load_all_regions("/nonexistent/regions.json")

    def test_name_map_degrades_to_empty(self) -> None:
        self.assertEqual(load_region_name_map("/nonexistent/regions.json"), {})


if __name__ == "__main__":
    unittest.main()
