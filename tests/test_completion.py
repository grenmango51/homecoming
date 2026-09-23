"""Completion state reducer and event transition tests."""

import datetime
import unittest

from src.completion import CompletionReducer, SearchEvent
from src.models import SearchQuery, SearchState


def _make_query() -> SearchQuery:
    return SearchQuery(
        provider="google",
        origin="HEL",
        destination="HAN",
        departure=datetime.date(2026, 12, 9),
        return_date=datetime.date(2027, 1, 5),
    )


def _event(event_type: str, attempt_id: str = "att-1") -> SearchEvent:
    return SearchEvent(
        event_type=event_type,
        attempt_id=attempt_id,
        timestamp="2026-09-20T10:00:00Z",
    )


class CompletionReducerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.query = _make_query()
        self.reducer = CompletionReducer(self.query, attempt_id="att-1")

    def test_normal_completion_flow(self) -> None:
        self.assertEqual(self.reducer.state, SearchState.NAVIGATING)

        self.reducer.transition(_event("navigation_started"))
        self.assertEqual(self.reducer.state, SearchState.VALIDATING_QUERY)

        self.reducer.transition(_event("query_validated"))
        self.assertEqual(self.reducer.state, SearchState.SEARCHING)

        self.reducer.transition(_event("provider_complete"))
        self.assertEqual(self.reducer.state, SearchState.PROVIDER_COMPLETE)

        self.reducer.transition(_event("rendering_started"))
        self.assertEqual(self.reducer.state, SearchState.RENDERING_FINAL_RESULTS)

        self.reducer.transition(_event("rendering_done"))
        self.assertEqual(self.reducer.state, SearchState.COMPLETE)
        self.assertTrue(self.reducer.state.is_terminal)
        self.assertTrue(self.reducer.state.is_success)
        self.assertTrue(self.reducer.state.can_accept_fare)

    def test_provider_complete_direct_to_complete_on_rendering_done(self) -> None:
        self.reducer.transition(_event("navigation_started"))
        self.reducer.transition(_event("query_validated"))
        self.reducer.transition(_event("provider_complete"))
        self.reducer.transition(_event("rendering_done"))
        self.assertEqual(self.reducer.state, SearchState.COMPLETE)

    def test_rendering_done_before_provider_complete_is_unverified(self) -> None:
        self.reducer.transition(_event("navigation_started"))
        self.reducer.transition(_event("query_validated"))
        self.reducer.transition(_event("rendering_done"))
        self.assertEqual(self.reducer.state, SearchState.COMPLETION_UNVERIFIED)
        self.assertTrue(self.reducer.state.is_terminal)
        self.assertFalse(self.reducer.state.can_accept_fare)

    def test_blocked_during_search(self) -> None:
        self.reducer.transition(_event("navigation_started"))
        self.reducer.transition(_event("query_validated"))
        self.reducer.transition(_event("blocked"))
        self.assertEqual(self.reducer.state, SearchState.BLOCKED)
        self.assertTrue(self.reducer.state.is_terminal)
        self.assertFalse(self.reducer.state.is_success)

    def test_challenge_detected_during_search(self) -> None:
        self.reducer.transition(_event("navigation_started"))
        self.reducer.transition(_event("query_validated"))
        self.reducer.transition(_event("challenge_detected"))
        self.assertEqual(self.reducer.state, SearchState.BLOCKED)

    def test_timeout_during_search(self) -> None:
        self.reducer.transition(_event("navigation_started"))
        self.reducer.transition(_event("query_validated"))
        self.reducer.transition(_event("timeout"))
        self.assertEqual(self.reducer.state, SearchState.TIMEOUT)
        self.assertTrue(self.reducer.state.is_terminal)

    def test_no_results_is_terminal_success_without_fare(self) -> None:
        self.reducer.transition(_event("navigation_started"))
        self.reducer.transition(_event("query_validated"))
        self.reducer.transition(_event("no_results"))
        self.assertEqual(self.reducer.state, SearchState.NO_RESULTS)
        self.assertTrue(self.reducer.state.is_terminal)
        self.assertTrue(self.reducer.state.is_success)
        self.assertFalse(self.reducer.state.can_accept_fare)

    def test_query_mismatch_during_validation(self) -> None:
        self.reducer.transition(_event("navigation_started"))
        self.reducer.transition(_event("query_mismatch"))
        self.assertEqual(self.reducer.state, SearchState.QUERY_MISMATCH)
        self.assertTrue(self.reducer.state.is_terminal)
        self.assertFalse(self.reducer.state.is_success)

    def test_error_from_any_non_terminal_state(self) -> None:
        self.reducer.transition(_event("navigation_started"))
        self.reducer.transition(_event("error"))
        self.assertEqual(self.reducer.state, SearchState.ERROR)
        self.assertTrue(self.reducer.state.is_terminal)

    def test_ignores_stale_attempt_events(self) -> None:
        self.reducer.transition(_event("navigation_started", attempt_id="att-1"))
        self.assertEqual(self.reducer.state, SearchState.VALIDATING_QUERY)
        # Event from old or mismatched attempt ID is ignored
        self.reducer.transition(_event("query_validated", attempt_id="old-att"))
        self.assertEqual(self.reducer.state, SearchState.VALIDATING_QUERY)

    def test_ignores_late_response_events(self) -> None:
        self.reducer.transition(_event("navigation_started"))
        self.reducer.transition(_event("late_response"))
        self.assertEqual(self.reducer.state, SearchState.VALIDATING_QUERY)

    def test_terminal_states_reject_further_transitions(self) -> None:
        self.reducer.transition(_event("navigation_started"))
        self.reducer.transition(_event("query_validated"))
        self.reducer.transition(_event("timeout"))
        self.assertEqual(self.reducer.state, SearchState.TIMEOUT)

        # Subsequent events must not alter terminal state
        self.reducer.transition(_event("provider_complete"))
        self.assertEqual(self.reducer.state, SearchState.TIMEOUT)
        self.reducer.transition(_event("rendering_done"))
        self.assertEqual(self.reducer.state, SearchState.TIMEOUT)

    def test_reset_reinitializes_to_navigating_with_new_attempt(self) -> None:
        self.reducer.transition(_event("navigation_started"))
        self.reducer.transition(_event("query_validated"))
        self.reducer.transition(_event("timeout"))

        self.reducer.reset("att-2")
        self.assertEqual(self.reducer.state, SearchState.NAVIGATING)
        self.assertEqual(self.reducer.attempt_id, "att-2")
        self.assertEqual(self.reducer.history, [])

        # Events with old att-1 ignored
        self.reducer.transition(_event("navigation_started", attempt_id="att-1"))
        self.assertEqual(self.reducer.state, SearchState.NAVIGATING)

        # Events with new att-2 accepted
        self.reducer.transition(_event("navigation_started", attempt_id="att-2"))
        self.assertEqual(self.reducer.state, SearchState.VALIDATING_QUERY)

    def test_history_records_transitions(self) -> None:
        self.reducer.transition(_event("navigation_started"))
        self.reducer.transition(_event("query_validated"))
        self.reducer.transition(_event("provider_complete"))

        expected = [
            ("navigation_started", SearchState.NAVIGATING, SearchState.VALIDATING_QUERY),
            ("query_validated", SearchState.VALIDATING_QUERY, SearchState.SEARCHING),
            ("provider_complete", SearchState.SEARCHING, SearchState.PROVIDER_COMPLETE),
        ]
        self.assertEqual(self.reducer.history, expected)

    def test_late_cheaper_fare_rejected_in_all_non_complete_states(self) -> None:
        """Only SearchState.COMPLETE can accept and publish a fare.

        All non-complete states (unverified, timeout, blocked, error, in-progress)
        must reject fares to prevent publishing premature or ghost low prices.
        """
        for state in SearchState:
            if state == SearchState.COMPLETE:
                self.assertTrue(state.can_accept_fare)
            else:
                self.assertFalse(
                    state.can_accept_fare,
                    f"State {state} unexpectedly permits accepting fares"
                )

    def test_late_response_during_search_is_safely_ignored(self) -> None:
        self.reducer.transition(_event("navigation_started"))
        self.reducer.transition(_event("query_validated"))
        self.assertEqual(self.reducer.state, SearchState.SEARCHING)

        # Late response arriving during search must not advance state
        self.reducer.transition(_event("late_response"))
        self.assertEqual(self.reducer.state, SearchState.SEARCHING)
        self.assertFalse(self.reducer.state.can_accept_fare)


if __name__ == "__main__":
    unittest.main()

