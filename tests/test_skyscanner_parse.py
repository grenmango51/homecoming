"""Skyscanner URL construction, challenge detection and XHR payload parsing."""

import datetime as dt
import unittest

from src.skyscanner_parse import (
    cheapest_tab_price,
    extract_from_xhr_payloads,
    flight_search_url,
    is_challenge_page,
    make_observation,
    money_values,
    page_status,
    to_yymmdd,
)


class UrlTests(unittest.TestCase):
    def test_yymmdd_formatting(self) -> None:
        self.assertEqual(to_yymmdd(dt.date(2026, 12, 9)), "261209")
        self.assertEqual(to_yymmdd(dt.date(2027, 1, 9)), "270109")

    def test_round_trip_url(self) -> None:
        url = flight_search_url("HEL", "HAN", dt.date(2026, 12, 9), dt.date(2027, 1, 9))
        self.assertTrue(url.startswith("https://www.skyscanner.fi/transport/flights/hel/han/261209/270109/"))
        self.assertIn("adultsv2=1", url)
        self.assertIn("cabinclass=economy", url)


class PriceTests(unittest.TestCase):
    def test_money_values_handles_prefix_and_suffix_notation(self) -> None:
        self.assertEqual(money_values("€782"), [782.0])
        self.assertEqual(money_values("782 €"), [782.0])

    def test_cheapest_tab_in_english_and_finnish(self) -> None:
        self.assertEqual(cheapest_tab_price("Halvin alk. 782 € Paras alk. 850 €"), 782.0)
        self.assertEqual(cheapest_tab_price("Cheapest from €795 Best €850"), 795.0)
        self.assertIsNone(cheapest_tab_price("No tabs visible here"))


class PageStatusTests(unittest.TestCase):
    def test_classification(self) -> None:
        self.assertEqual(page_status("https://skyscanner.net/flights", "12 results returned"), "observed")
        self.assertEqual(page_status("https://skyscanner.net/flights", "Halvin alk. 782 €"), "observed")
        self.assertEqual(page_status("https://skyscanner.net/flights", "Please verify you are human"), "user_action_required")
        self.assertEqual(page_status("https://skyscanner.net/flights/captcha", "anything"), "user_action_required")
        self.assertEqual(page_status("https://skyscanner.net/flights", "Oops, something went wrong"), "incomplete")

    def test_challenge_detection(self) -> None:
        self.assertTrue(is_challenge_page("https://skyscanner.net/sttc/px/captcha", "anything"))
        self.assertTrue(is_challenge_page("https://skyscanner.net", "oletko oikea henkilö vai robotti"))
        self.assertTrue(is_challenge_page("https://skyscanner.net", "Press & Hold"))
        self.assertTrue(is_challenge_page("https://skyscanner.net", "Are you a person or a robot?"))
        self.assertFalse(is_challenge_page("https://skyscanner.net/flights", "Cheapest from €782"))


SAMPLE_ITINERARY = {
    "price": {"raw": 782.0, "formatted": "782 €"},
    "legs": [
        {
            "departure": "2026-12-09T17:00:00",
            "arrival": "2026-12-10T14:35:00",
            "durationInMinutes": 1055,
            "stopCount": 1,
            "carriers": {"marketing": [{"name": "Qatar Airways"}]},
        }
    ],
    "pricingOptions": [{"agentName": "Trip.com", "price": {"raw": 782.0}}],
}


class ItineraryTests(unittest.TestCase):
    def test_parses_legs_carriers_and_sellers(self) -> None:
        from src.skyscanner_parse import parse_itinerary_json

        parsed = parse_itinerary_json(SAMPLE_ITINERARY)
        self.assertEqual(parsed["price_eur"], 782.0)
        self.assertEqual(parsed["legs"][0]["duration_minutes"], 1055)
        self.assertEqual(parsed["legs"][0]["carriers"], ["Qatar Airways"])
        self.assertEqual(parsed["sample_deals"][0]["seller"], "Trip.com")

    def test_a_cheaper_pricing_option_wins(self) -> None:
        from src.skyscanner_parse import parse_itinerary_json

        itinerary = dict(SAMPLE_ITINERARY)
        itinerary["pricingOptions"] = [{"agentName": "Kiwi", "price": {"raw": 700.0}}]
        self.assertEqual(parse_itinerary_json(itinerary)["price_eur"], 700.0)

    def test_xhr_payloads_are_sorted_cheapest_first(self) -> None:
        cheap = {"price": {"raw": 600.0}, "legs": [], "pricingOptions": []}
        payload = {"itineraries": {"results": [SAMPLE_ITINERARY, cheap]}}
        results = extract_from_xhr_payloads([payload])
        self.assertEqual([r["price_eur"] for r in results], [600.0, 782.0])

    def test_nested_content_results_shape_is_understood(self) -> None:
        payload = {"content": {"results": {"itineraries": {"a": SAMPLE_ITINERARY}}}}
        self.assertEqual(len(extract_from_xhr_payloads([payload])), 1)


class ObservationTests(unittest.TestCase):
    def test_lowest_price_spans_tab_xhr_and_dom_sources(self) -> None:
        obs = make_observation(
            origin="HEL",
            destination="HAN",
            departure=dt.date(2026, 12, 9),
            return_date=dt.date(2027, 1, 9),
            page_url="https://www.skyscanner.net/transport/flights/hel/han/261209/270109/",
            page_text="Cheapest from €795",
            xhr_candidates=[{"price_eur": 782.0}],
            dom_candidates=["1 stop 17 hr €820"],
        )
        self.assertEqual(obs["lowest_observed_price_eur"], 782.0)
        self.assertEqual(obs["status"], "observed")
        self.assertEqual(obs["stay_nights"], 31)

    def test_budget_fares_below_200_are_preserved(self) -> None:
        obs = make_observation(
            origin="HEL",
            destination="AMS",
            departure=dt.date(2026, 12, 9),
            return_date=dt.date(2027, 1, 9),
            page_url="https://www.skyscanner.fi/transport/flights/hel/ams/261209/270109/",
            page_text="Halvin 45 €",
            xhr_candidates=[{"price_eur": 45.0}],
            dom_candidates=["Suora 2 hr 30 min 45 €"],
        )
        self.assertEqual(obs["lowest_observed_price_eur"], 45.0)
        self.assertEqual(obs["status"], "observed")


if __name__ == "__main__":
    unittest.main()
