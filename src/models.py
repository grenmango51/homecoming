"""
Core data types for the rebuilt flight scraper.
This module is browser-free and contains no Playwright/Patchright imports.
"""

import datetime
import hashlib
import math
from dataclasses import dataclass
from enum import Enum, auto


@dataclass
class SearchQuery:
    provider: str
    origin: str
    destination: str
    departure: datetime.date
    return_date: datetime.date | None = None
    trip_type: str = 'round-trip'
    passengers: int = 1
    cabin: str = 'economy'
    currency: str = 'EUR'
    market: str = 'FI'

    def fingerprint(self) -> str:
        """Returns a deterministic string hash of all fields for storage identity."""
        components = [
            str(self.provider),
            str(self.origin),
            str(self.destination),
            self.departure.isoformat(),
            self.return_date.isoformat() if self.return_date else "",
            str(self.trip_type),
            str(self.passengers),
            str(self.cabin),
            str(self.currency),
            str(self.market),
        ]
        hasher = hashlib.sha256()
        hasher.update("|".join(components).encode('utf-8'))
        return hasher.hexdigest()

    def matches(self, other: 'SearchQuery') -> bool:
        """Compares search identity (excluding provider) for cross-provider comparison."""
        return (
            self.origin == other.origin and
            self.destination == other.destination and
            self.departure == other.departure and
            self.return_date == other.return_date and
            self.trip_type == other.trip_type and
            self.passengers == other.passengers and
            self.cabin == other.cabin and
            self.currency == other.currency and
            self.market == other.market
        )


class SearchState(Enum):
    NAVIGATING = auto()
    VALIDATING_QUERY = auto()
    SEARCHING = auto()
    PROVIDER_COMPLETE = auto()
    RENDERING_FINAL_RESULTS = auto()
    COMPLETE = auto()
    NO_RESULTS = auto()
    BLOCKED = auto()
    TIMEOUT = auto()
    ERROR = auto()
    QUERY_MISMATCH = auto()
    COMPLETION_UNVERIFIED = auto()

    @property
    def is_terminal(self) -> bool:
        return self in {
            SearchState.COMPLETE, SearchState.NO_RESULTS, SearchState.BLOCKED,
            SearchState.TIMEOUT, SearchState.ERROR, SearchState.QUERY_MISMATCH,
            SearchState.COMPLETION_UNVERIFIED,
        }

    @property
    def is_success(self) -> bool:
        return self in {SearchState.COMPLETE, SearchState.NO_RESULTS}

    @property
    def can_accept_fare(self) -> bool:
        return self == SearchState.COMPLETE


@dataclass
class PriceObservation:
    amount: float
    currency: str
    source: str
    timestamp: str
    evidence_ref: str | None = None

    @staticmethod
    def validate(amount: float, currency: str) -> bool:
        """Rejects non-positive amounts, NaN, and amounts > 1_000_000."""
        if math.isnan(amount) or amount <= 0 or amount > 1_000_000:
            return False
        return True


@dataclass
class AttemptResult:
    state: SearchState
    query: SearchQuery
    attempt_id: str
    started_at: str
    finished_at: str
    headline_price: PriceObservation | None
    lowest_itinerary_price: PriceObservation | None
    accepted_price: PriceObservation | None
    all_prices: list[PriceObservation]
    completion_evidence: str | None
    notes: list[str]

    @property
    def price_mismatch(self) -> bool:
        """Returns True when headline and lowest itinerary differ."""
        if self.headline_price and self.lowest_itinerary_price:
            return self.headline_price.amount != self.lowest_itinerary_price.amount
        return False
