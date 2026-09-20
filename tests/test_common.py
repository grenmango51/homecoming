"""Date generation and shared price/duration parsing."""

import datetime as dt
import unittest

from src.common import build_search_pairs, date_range, duration_minutes, parse_numeric_price


class DateRangeTests(unittest.TestCase):
    def test_inclusive_range(self) -> None:
        self.assertEqual(len(date_range("2026-12-09", "2026-12-13")), 5)

    def test_single_day(self) -> None:
        self.assertEqual(date_range("2026-12-09", "2026-12-09"), [dt.date(2026, 12, 9)])

    def test_reversed_range_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            date_range("2026-12-13", "2026-12-09")


class SearchPairTests(unittest.TestCase):
    def test_range_mode_is_the_eligible_cartesian_product(self) -> None:
        pairs = build_search_pairs(
            depart_from="2026-12-09",
            depart_to="2026-12-13",
            return_from="2027-01-05",
            return_to="2027-01-09",
            min_stay_nights=21,
        )
        self.assertEqual(len(pairs), 25)
        for departure, return_date in pairs:
            self.assertGreaterEqual((return_date - departure).days, 21)

    def test_window_mode_spans_the_whole_holiday_window(self) -> None:
        pairs = build_search_pairs(window_start="2026-12-09", window_end="2027-01-09", min_stay_nights=21)
        self.assertEqual(len(pairs), 66)
        self.assertEqual(pairs[0], (dt.date(2026, 12, 9), dt.date(2026, 12, 30)))
        self.assertEqual(pairs[-1], (dt.date(2026, 12, 19), dt.date(2027, 1, 9)))
        for departure, return_date in pairs:
            self.assertGreaterEqual((return_date - departure).days, 21)
            self.assertGreaterEqual(departure, dt.date(2026, 12, 9))
            self.assertLessEqual(return_date, dt.date(2027, 1, 9))

    def test_exact_mode_keeps_only_pairs_meeting_min_stay(self) -> None:
        pairs = build_search_pairs(
            exact_pairs=[["2026-12-09", "2027-01-05"], ["2026-12-09", "2026-12-15"]],
            min_stay_nights=21,
        )
        self.assertEqual(pairs, [(dt.date(2026, 12, 9), dt.date(2027, 1, 5))])

    def test_reversed_window_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            build_search_pairs(window_start="2027-01-09", window_end="2026-12-09")

    def test_no_date_source_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            build_search_pairs(depart_from="2026-12-09")


class PriceParsingTests(unittest.TestCase):
    def test_european_and_anglo_separators(self) -> None:
        self.assertEqual(parse_numeric_price("782"), 782.0)
        self.assertEqual(parse_numeric_price("782 €"), 782.0)
        self.assertEqual(parse_numeric_price("1,001"), 1001.0)
        self.assertEqual(parse_numeric_price("1.001 €"), 1001.0)
        self.assertEqual(parse_numeric_price("€ 1,234.50"), 1234.50)

    def test_rejects_non_numeric_and_implausible_values(self) -> None:
        self.assertIsNone(parse_numeric_price("invalid"))
        self.assertIsNone(parse_numeric_price(""))
        self.assertIsNone(parse_numeric_price("0"))
        self.assertIsNone(parse_numeric_price("200000"))

    def test_accepts_budget_and_premium_fares(self) -> None:
        """Fares below €50 and above €25000 are valid after removing arbitrary floors."""
        self.assertEqual(parse_numeric_price("19"), 19.0)
        self.assertEqual(parse_numeric_price("49"), 49.0)
        self.assertEqual(parse_numeric_price("30000"), 30000.0)


class DurationTests(unittest.TestCase):
    def test_english_formats(self) -> None:
        self.assertEqual(duration_minutes("16 hr 15 min"), 975)
        self.assertEqual(duration_minutes("19 hr"), 1140)
        self.assertEqual(duration_minutes("2h 30m"), 150)
        self.assertEqual(duration_minutes("15h"), 900)

    def test_finnish_formats(self) -> None:
        self.assertEqual(duration_minutes("17 t 35 min"), 1055)
        self.assertEqual(duration_minutes("17 tuntia 35 minuuttia"), 1055)

    def test_missing_duration(self) -> None:
        self.assertIsNone(duration_minutes("no duration"))


if __name__ == "__main__":
    unittest.main()
