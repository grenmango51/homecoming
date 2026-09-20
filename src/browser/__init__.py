"""Browser package providing Playwright and Patchright automation.

Contains session management, consent handling, and provider-specific page
driving for Google Flights and Skyscanner.
"""

from src.browser.google import (
    _RESULTS_READY_JS,
    DEFAULT_CARD_SELECTOR,
    LISTITEM_CARD_SELECTOR,
    click_reload_if_present,
    extract_cards,
    read_cheapest_tab_text,
    sort_by_price,
    switch_to_cheapest_tab,
    wait_for_hydration,
    wait_for_results,
)
from src.browser.session import (
    CONSENT_BUTTON_NAMES,
    GOOGLE_CONSENT_COOKIES,
    dismiss_consent,
    launch_google_context,
    page_text,
)
from src.browser.skyscanner import (
    SKYSCANNER_COOKIES,
    extract_dom_candidate_cards,
    launch_skyscanner_context,
    reset_session,
    wait_for_challenge_resolution,
)

__all__ = [
    "CONSENT_BUTTON_NAMES",
    "DEFAULT_CARD_SELECTOR",
    "GOOGLE_CONSENT_COOKIES",
    "LISTITEM_CARD_SELECTOR",
    "SKYSCANNER_COOKIES",
    "_RESULTS_READY_JS",
    "click_reload_if_present",
    "dismiss_consent",
    "extract_cards",
    "extract_dom_candidate_cards",
    "launch_google_context",
    "launch_skyscanner_context",
    "page_text",
    "read_cheapest_tab_text",
    "reset_session",
    "sort_by_price",
    "switch_to_cheapest_tab",
    "wait_for_challenge_resolution",
    "wait_for_hydration",
    "wait_for_results",
]
