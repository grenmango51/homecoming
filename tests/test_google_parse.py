"""Google Flights URL construction and page/card parsing."""

import datetime as dt
import unittest

from src.google_parse import (
    build_regional_url,
    build_route_url,
    build_structured_flight_url,
    cheapest_banner_price,
    extract_money_values,
    flight_search_url,
    is_blocked,
    make_observation,
    money_values,
    page_status,
    parse_card_details,
    parse_eur_card_details,
    protection_label,
    reported_currency,
    reported_location,
    stop_count,
    tab_price,
)

DEPARTURE = dt.date(2026, 12, 9)
RETURN = dt.date(2027, 1, 5)


class UrlTests(unittest.TestCase):
    def test_hel_han_uses_the_protobuf_template_with_substituted_dates(self) -> None:
        url = build_structured_flight_url(dt.date(2026, 12, 10), dt.date(2027, 1, 7))
        self.assertIn("/travel/flights/search?tfs=", url)
        self.assertIn("curr=EUR", url)
        # The template is retargeted by byte-substituting the encoded dates.
        self.assertNotEqual(url, build_structured_flight_url(DEPARTURE, RETURN))

    def test_daily_url_falls_back_to_free_text_for_other_routes(self) -> None:
        url = flight_search_url("PHL", "HAN", dt.date(2026, 12, 10), dt.date(2027, 1, 8))
        self.assertIn("PHL", url)
        self.assertIn("HAN", url)
        self.assertIn("2026-12-10", url)
        self.assertIn("2027-01-08", url)

    def test_route_codes_are_normalised_to_uppercase(self) -> None:
        self.assertEqual(
            flight_search_url("hel", "han", DEPARTURE, RETURN),
            flight_search_url("HEL", "HAN", DEPARTURE, RETURN),
        )

    def test_pos_url_supports_one_way(self) -> None:
        url = build_route_url("HEL", "BRU", dt.date(2026, 12, 15), None)
        self.assertIn("one%20way", url)
        self.assertNotIn("through", url)

    def test_pos_url_pins_the_cheapest_sort(self) -> None:
        self.assertIn("tfu=", build_route_url("PHL", "HAN", DEPARTURE, RETURN))

    def test_regional_url_omits_gl_for_the_natural_ip_baseline(self) -> None:
        self.assertNotIn("gl=", build_regional_url("HEL", "HAN", DEPARTURE, RETURN, gl="NONE"))
        self.assertNotIn("gl=", build_regional_url("HEL", "HAN", DEPARTURE, RETURN, gl=None))
        self.assertIn("gl=VN", build_regional_url("HEL", "HAN", DEPARTURE, RETURN, gl="VN", curr="VND"))


class PriceTests(unittest.TestCase):
    def test_euro_values_in_both_accessible_formats(self) -> None:
        self.assertEqual(
            money_values("Cheapest from €800. From 1,004 euros round trip total."), [800.0, 1004.0]
        )

    def test_euro_parser_ignores_sub_fare_amounts(self) -> None:
        self.assertEqual(money_values("baggage €35 seat €12"), [])

    def test_multi_currency_parser_accepts_short_haul_and_weak_currencies(self) -> None:
        self.assertEqual(extract_money_values("$1,650 one stop"), [1650.0])
        self.assertEqual(extract_money_values("£23 nonstop"), [23.0])
        self.assertEqual(extract_money_values("₫30.120.000", curr="VND"), [30120000.0])

    def test_cheapest_banner(self) -> None:
        self.assertEqual(cheapest_banner_price("Cheapest from €1,064"), 1064.0)
        self.assertEqual(cheapest_banner_price("cheapest eur 950"), 950.0)
        self.assertIsNone(cheapest_banner_price("No flights available"))

    def test_tab_header_price(self) -> None:
        self.assertEqual(tab_price("Cheapest €1,064"), 1064.0)
        self.assertIsNone(tab_price("Cheapest Fetching results"))


class CardTests(unittest.TestCase):
    def test_stop_count(self) -> None:
        self.assertEqual(stop_count("Nonstop 13 hr"), 0)
        self.assertEqual(stop_count("1 stop 17 hr"), 1)
        self.assertEqual(stop_count("3 stops 30 hr"), 3)

    def test_protection_label(self) -> None:
        self.assertEqual(protection_label("Separate tickets booked together"), "separate_tickets")
        self.assertEqual(protection_label("Nonstop"), "not_flagged_by_google")

    def test_card_captures_carriers_layovers_and_duration(self) -> None:
        card = parse_eur_card_details(
            "1 stop 17 hr 35 min HEL DOH HAN Qatar Airways €1,234.50 round trip"
        )
        self.assertEqual(card["carriers"], ["Qatar Airways"])
        self.assertEqual(card["layovers"], ["DOH"])
        self.assertEqual(card["stops"], 1)
        self.assertEqual(card["duration_minutes"], 1055)
        self.assertEqual(card["lowest_price"], 1234.50)

    def test_route_endpoints_are_not_reported_as_layovers(self) -> None:
        card = parse_eur_card_details("Nonstop 13 hr HEL HAN Finnair €782")
        self.assertEqual(card["layovers"], [])

    def test_eur_and_multi_currency_parsers_differ_on_the_fare_floor(self) -> None:
        text = "1 stop 5 hr £23 cheap hop"
        self.assertEqual(parse_eur_card_details(text)["observed_prices"], [])
        self.assertEqual(parse_card_details(text)["observed_prices"], [23.0])

    def test_only_the_eur_card_carries_the_legacy_eur_alias(self) -> None:
        text = "Nonstop 13 hr HEL HAN Finnair €782"
        self.assertIn("observed_prices_eur", parse_eur_card_details(text))
        self.assertNotIn("observed_prices_eur", parse_card_details(text))


class PageStatusTests(unittest.TestCase):
    def test_classification(self) -> None:
        self.assertEqual(page_status("6 results returned. Top departing flights"), "observed")
        self.assertEqual(page_status("Cheapest from €1,064"), "observed")
        self.assertEqual(page_status("Please verify you are human captcha"), "blocked")
        self.assertEqual(page_status("Oops, something went wrong"), "incomplete")
        self.assertEqual(page_status("random empty content"), "incomplete")

    def test_is_blocked(self) -> None:
        self.assertTrue(is_blocked("Our systems have detected unusual traffic"))
        self.assertFalse(is_blocked("6 results returned"))

    def test_footer_readback(self) -> None:
        text = "Location\n  Finland\nCurrency\nEUR\n"
        self.assertEqual(reported_location(text), "Finland")
        self.assertEqual(reported_currency(text), "EUR")
        self.assertEqual(reported_location("no footer"), "Unknown")


class ObservationTests(unittest.TestCase):
    def test_banner_beats_card_prices_and_footer_noise_is_ignored(self) -> None:
        obs = make_observation(
            origin="HEL",
            destination="HAN",
            departure=DEPARTURE,
            return_date=dt.date(2027, 1, 9),
            page_text="6 results returned. Top departing flights\nCheapest from €800\nPrivacy Terms €350",
            candidates=[
                "Finnair, Qatar Airways 16 hr 15 min HEL–HAN 1 stop €850 round trip separate tickets booked together",
                "Turkish Airlines 19 hr HEL–HAN 1 stop €1,004 round trip",
            ],
        )
        self.assertEqual(obs["lowest_observed_price_eur"], 800.0)
        self.assertEqual(obs["status"], "observed")
        self.assertEqual(obs["stay_nights"], 31)
        self.assertEqual(obs["protection_label"], "separate_tickets")
        self.assertEqual(len(obs["candidate_cards"]), 2)
        self.assertEqual(obs["candidate_cards"][0]["observed_duration_minutes"], 975)

    def test_parsed_prices_promote_an_otherwise_incomplete_page(self) -> None:
        obs = make_observation(
            origin="HEL", destination="HAN", departure=DEPARTURE, return_date=RETURN,
            page_text="unrecognised layout",
            candidates=[parse_eur_card_details("1 stop 12 hr HEL DOH HAN €900")],
        )
        self.assertEqual(obs["status"], "observed")
        self.assertEqual(obs["lowest_observed_price_eur"], 900.0)

    def test_blocked_page_reports_no_price(self) -> None:
        obs = make_observation(
            origin="HEL", destination="HAN", departure=DEPARTURE, return_date=RETURN,
            page_text="Please verify you are human", candidates=[],
        )
        self.assertEqual(obs["status"], "blocked")
        self.assertIsNone(obs["lowest_observed_price_eur"])


if __name__ == "__main__":
    unittest.main()
