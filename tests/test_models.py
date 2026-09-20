"""Query identity, state flags, price validation and attempt result mismatch detection."""

import math
import unittest
from datetime import date

from src.models import AttemptResult, PriceObservation, SearchQuery, SearchState


def _query(**overrides) -> SearchQuery:
    defaults = {
        "provider": "google",
        "origin": "HEL",
        "destination": "HAN",
        "departure": date(2026, 12, 9),
        "return_date": date(2027, 1, 5),
    }
    defaults.update(overrides)
    return SearchQuery(**defaults)


def _price(amount: float = 782.0, currency: str = "EUR", source: str = "itinerary_card") -> PriceObservation:
    return PriceObservation(
        amount=amount, currency=currency, source=source, timestamp="2026-09-20T00:00:00+00:00"
    )


def _result(**overrides) -> AttemptResult:
    defaults = {
        "state": SearchState.COMPLETE,
        "query": _query(),
        "attempt_id": "test-1",
        "started_at": "2026-09-20T00:00:00+00:00",
        "finished_at": "2026-09-20T00:01:00+00:00",
        "headline_price": None,
        "lowest_itinerary_price": None,
        "accepted_price": None,
        "all_prices": [],
        "completion_evidence": None,
        "notes": [],
    }
    defaults.update(overrides)
    return AttemptResult(**defaults)


class QueryFingerprintTests(unittest.TestCase):
    def test_fingerprint_is_deterministic(self) -> None:
        q = _query()
        self.assertEqual(q.fingerprint(), q.fingerprint())

    def test_fingerprint_changes_with_origin(self) -> None:
        self.assertNotEqual(_query(origin="HEL").fingerprint(), _query(origin="OUL").fingerprint())

    def test_fingerprint_changes_with_destination(self) -> None:
        self.assertNotEqual(_query(destination="HAN").fingerprint(), _query(destination="BKK").fingerprint())

    def test_fingerprint_changes_with_departure(self) -> None:
        self.assertNotEqual(
            _query(departure=date(2026, 12, 9)).fingerprint(),
            _query(departure=date(2026, 12, 10)).fingerprint(),
        )

    def test_fingerprint_changes_with_market(self) -> None:
        self.assertNotEqual(_query(market="FI").fingerprint(), _query(market="SE").fingerprint())

    def test_fingerprint_changes_with_provider(self) -> None:
        self.assertNotEqual(_query(provider="google").fingerprint(), _query(provider="skyscanner").fingerprint())


class QueryMatchTests(unittest.TestCase):
    def test_matches_same_search_different_provider(self) -> None:
        q1 = _query(provider="google")
        q2 = _query(provider="skyscanner")
        self.assertTrue(q1.matches(q2))

    def test_does_not_match_different_route(self) -> None:
        q1 = _query(destination="HAN")
        q2 = _query(destination="BKK")
        self.assertFalse(q1.matches(q2))


class StateTests(unittest.TestCase):
    def test_terminal_states(self) -> None:
        for state in (SearchState.COMPLETE, SearchState.NO_RESULTS, SearchState.BLOCKED,
                      SearchState.TIMEOUT, SearchState.ERROR, SearchState.QUERY_MISMATCH,
                      SearchState.COMPLETION_UNVERIFIED):
            with self.subTest(state=state):
                self.assertTrue(state.is_terminal)

    def test_non_terminal_states(self) -> None:
        for state in (SearchState.NAVIGATING, SearchState.VALIDATING_QUERY, SearchState.SEARCHING,
                      SearchState.PROVIDER_COMPLETE, SearchState.RENDERING_FINAL_RESULTS):
            with self.subTest(state=state):
                self.assertFalse(state.is_terminal)

    def test_success_states(self) -> None:
        self.assertTrue(SearchState.COMPLETE.is_success)
        self.assertTrue(SearchState.NO_RESULTS.is_success)
        self.assertFalse(SearchState.BLOCKED.is_success)
        self.assertFalse(SearchState.ERROR.is_success)

    def test_can_accept_fare(self) -> None:
        self.assertTrue(SearchState.COMPLETE.can_accept_fare)
        self.assertFalse(SearchState.NO_RESULTS.can_accept_fare)
        self.assertFalse(SearchState.COMPLETION_UNVERIFIED.can_accept_fare)


class PriceValidationTests(unittest.TestCase):
    def test_accepts_normal_fares(self) -> None:
        self.assertTrue(PriceObservation.validate(782.0, "EUR"))
        self.assertTrue(PriceObservation.validate(19.0, "EUR"))
        self.assertTrue(PriceObservation.validate(1234.50, "EUR"))

    def test_rejects_invalid_fares(self) -> None:
        self.assertFalse(PriceObservation.validate(-50.0, "EUR"))
        self.assertFalse(PriceObservation.validate(0.0, "EUR"))
        self.assertFalse(PriceObservation.validate(math.nan, "EUR"))
        self.assertFalse(PriceObservation.validate(1_000_001.0, "EUR"))


class AttemptResultTests(unittest.TestCase):
    def test_price_mismatch_detected(self) -> None:
        result = _result(
            headline_price=_price(845.0),
            lowest_itinerary_price=_price(941.0),
        )
        self.assertTrue(result.price_mismatch)

    def test_no_mismatch_when_prices_agree(self) -> None:
        result = _result(
            headline_price=_price(845.0),
            lowest_itinerary_price=_price(845.0),
        )
        self.assertFalse(result.price_mismatch)

    def test_no_mismatch_when_prices_missing(self) -> None:
        result = _result(headline_price=None, lowest_itinerary_price=None)
        self.assertFalse(result.price_mismatch)

    def test_accepted_price_exists_only_when_complete(self) -> None:
        """AttemptResult is a plain dataclass; callers set accepted_price only when state is COMPLETE."""
        error_result = _result(state=SearchState.ERROR, accepted_price=None)
        self.assertIsNone(error_result.accepted_price)


if __name__ == "__main__":
    unittest.main()
