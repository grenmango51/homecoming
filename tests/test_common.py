"""Date generation and shared price/duration parsing."""

import datetime as dt
import unittest

from src.common import (
    COMPLETION_VERSION,
    build_search_pairs,
    circuit_breaker_tripped,
    date_range,
    deferred_observation,
    duration_minutes,
    is_valid_completion_cache,
    is_valid_eur_fare,
    parse_numeric_price,
)


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


class FareValidationTests(unittest.TestCase):
    def test_valid_positive_finite_fares(self) -> None:
        self.assertTrue(is_valid_eur_fare(19.0))
        self.assertTrue(is_valid_eur_fare(782.0))
        self.assertTrue(is_valid_eur_fare(100_000.0))
        self.assertTrue(is_valid_eur_fare(100))

    def test_invalid_fares(self) -> None:
        self.assertFalse(is_valid_eur_fare(None))
        self.assertFalse(is_valid_eur_fare(0))
        self.assertFalse(is_valid_eur_fare(0.0))
        self.assertFalse(is_valid_eur_fare(-10.0))
        self.assertFalse(is_valid_eur_fare(float("inf")))
        self.assertFalse(is_valid_eur_fare(float("nan")))
        self.assertFalse(is_valid_eur_fare(True))
        self.assertFalse(is_valid_eur_fare(False))
        self.assertFalse(is_valid_eur_fare("782.0"))
        self.assertFalse(is_valid_eur_fare(100_001.0))


class CircuitBreakerTests(unittest.TestCase):
    def test_circuit_breaker_trips_at_threshold(self) -> None:
        self.assertFalse(circuit_breaker_tripped(0))
        self.assertFalse(circuit_breaker_tripped(1))
        self.assertTrue(circuit_breaker_tripped(2))
        self.assertTrue(circuit_breaker_tripped(5))
        self.assertFalse(circuit_breaker_tripped(2, threshold=3))
        self.assertTrue(circuit_breaker_tripped(3, threshold=3))

    def test_deferred_observation_structure(self) -> None:
        dep = dt.date(2026, 12, 9)
        ret = dt.date(2027, 1, 5)
        obs = deferred_observation(
            source="Google Flights UI",
            origin="HEL",
            destination="HAN",
            departure=dep,
            return_date=ret,
            reason="circuit_breaker_tripped",
        )
        self.assertEqual(obs["status"], "deferred")
        self.assertIsNone(obs["lowest_observed_price_eur"])
        self.assertEqual(obs["completion_version"], COMPLETION_VERSION)
        self.assertIsNone(obs["completion_evidence"])
        self.assertEqual(obs["stay_nights"], 27)
        self.assertIn("circuit_breaker_tripped", obs["notes"][0])


class StrictCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.valid_record = {
            "status": "observed",
            "fetched_at": "2026-09-21T10:00:00+00:00",
            "origin": "HEL",
            "destination": "HAN",
            "departure_date": "2026-12-09",
            "return_date": "2027-01-05",
            "completion_version": COMPLETION_VERSION,
            "completion_evidence": "xhr_complete+dom_settled",
            "lowest_observed_price_eur": 782.0,
            "gl": "FI",
        }

    def test_valid_cache_accepted(self) -> None:
        self.assertTrue(
            is_valid_completion_cache(
                self.valid_record,
                stamp="2026-09-21",
                origin="HEL",
                dest="HAN",
                departure_date=dt.date(2026, 12, 9),
                return_date=dt.date(2027, 1, 5),
                extra_match={"gl": "FI"},
            )
        )

    def test_rejects_non_dict(self) -> None:
        self.assertFalse(is_valid_completion_cache(None, stamp="2026-09-21", origin="HEL", dest="HAN"))

    def test_rejects_non_observed_status(self) -> None:
        rec = dict(self.valid_record, status="deferred")
        self.assertFalse(is_valid_completion_cache(rec, stamp="2026-09-21", origin="HEL", dest="HAN"))

    def test_rejects_stale_stamp(self) -> None:
        self.assertFalse(is_valid_completion_cache(self.valid_record, stamp="2026-09-20", origin="HEL", dest="HAN"))

    def test_rejects_route_mismatch(self) -> None:
        self.assertFalse(is_valid_completion_cache(self.valid_record, stamp="2026-09-21", origin="OUL", dest="HAN"))
        self.assertFalse(is_valid_completion_cache(self.valid_record, stamp="2026-09-21", origin="HEL", dest="BKK"))

    def test_rejects_date_mismatch(self) -> None:
        self.assertFalse(
            is_valid_completion_cache(
                self.valid_record,
                stamp="2026-09-21",
                origin="HEL",
                dest="HAN",
                departure_date=dt.date(2026, 12, 10),
            )
        )

    def test_rejects_older_completion_version(self) -> None:
        rec = dict(self.valid_record, completion_version=COMPLETION_VERSION - 1)
        self.assertFalse(is_valid_completion_cache(rec, stamp="2026-09-21", origin="HEL", dest="HAN"))

    def test_rejects_missing_completion_evidence(self) -> None:
        rec = dict(self.valid_record, completion_evidence=None)
        self.assertFalse(is_valid_completion_cache(rec, stamp="2026-09-21", origin="HEL", dest="HAN"))
        rec2 = dict(self.valid_record, completion_evidence="   ")
        self.assertFalse(is_valid_completion_cache(rec2, stamp="2026-09-21", origin="HEL", dest="HAN"))

    def test_rejects_invalid_published_fare(self) -> None:
        for bad_fare in (None, 0, -10.0, float("nan"), "free", True):
            rec = dict(self.valid_record, lowest_observed_price_eur=bad_fare)
            self.assertFalse(is_valid_completion_cache(rec, stamp="2026-09-21", origin="HEL", dest="HAN"))

    def test_strict_extra_match_fails_on_missing_or_mismatched_keys(self) -> None:
        # Missing key in cached record must fail
        rec_without_gl = dict(self.valid_record)
        rec_without_gl.pop("gl")
        self.assertFalse(
            is_valid_completion_cache(
                rec_without_gl,
                stamp="2026-09-21",
                origin="HEL",
                dest="HAN",
                extra_match={"gl": "FI"},
            )
        )
        # Mismatched value must fail
        self.assertFalse(
            is_valid_completion_cache(
                self.valid_record,
                stamp="2026-09-21",
                origin="HEL",
                dest="HAN",
                extra_match={"gl": "SE"},
            )
        )


if __name__ == "__main__":
    unittest.main()
