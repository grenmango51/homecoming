"""
Pure state machine for search completion.
"""

from dataclasses import dataclass, field
from typing import Any

from .models import SearchQuery, SearchState


@dataclass
class SearchEvent:
    event_type: str
    attempt_id: str
    timestamp: str
    data: dict[str, Any] = field(default_factory=dict)


class CompletionReducer:
    def __init__(self, query: SearchQuery, attempt_id: str):
        self.query = query
        self.attempt_id = attempt_id
        self._state = SearchState.NAVIGATING
        self.history: list[tuple[str, SearchState, SearchState]] = []

    @property
    def state(self) -> SearchState:
        return self._state

    def transition(self, event: SearchEvent) -> SearchState:
        if event.attempt_id != self.attempt_id:
            return self._state

        if self._state.is_terminal:
            return self._state

        if event.event_type == 'late_response':
            return self._state

        current = self._state
        next_state = current

        # State machine implementation
        if event.event_type == 'error':
            next_state = SearchState.ERROR
        elif current == SearchState.NAVIGATING:
            if event.event_type == 'navigation_started':
                next_state = SearchState.VALIDATING_QUERY
        elif current == SearchState.VALIDATING_QUERY:
            if event.event_type == 'query_validated':
                next_state = SearchState.SEARCHING
            elif event.event_type == 'query_mismatch':
                next_state = SearchState.QUERY_MISMATCH
        elif current == SearchState.SEARCHING:
            if event.event_type == 'provider_complete':
                next_state = SearchState.PROVIDER_COMPLETE
            elif event.event_type in ('blocked', 'challenge_detected'):
                next_state = SearchState.BLOCKED
            elif event.event_type == 'no_results':
                next_state = SearchState.NO_RESULTS
            elif event.event_type == 'timeout':
                next_state = SearchState.TIMEOUT
            elif event.event_type == 'rendering_done':
                next_state = SearchState.COMPLETION_UNVERIFIED
        elif current == SearchState.PROVIDER_COMPLETE:
            if event.event_type == 'rendering_started':
                next_state = SearchState.RENDERING_FINAL_RESULTS
            elif event.event_type == 'rendering_done':
                next_state = SearchState.COMPLETE
            elif event.event_type == 'no_results':
                next_state = SearchState.NO_RESULTS
        elif current == SearchState.RENDERING_FINAL_RESULTS:
            if event.event_type == 'rendering_done':
                next_state = SearchState.COMPLETE
            elif event.event_type == 'no_results':
                next_state = SearchState.NO_RESULTS

        if current != next_state:
            self.history.append((event.event_type, current, next_state))
            self._state = next_state

        return self._state

    def reset(self, new_attempt_id: str):
        self.attempt_id = new_attempt_id
        self._state = SearchState.NAVIGATING
        self.history = []
