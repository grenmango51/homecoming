"""Public exports must preserve completion policy and omit private/raw fields."""

import unittest

from src.web_export import public_fare


class PublicFareTests(unittest.TestCase):
    def setUp(self):
        self.row = {
            'origin': 'HEL', 'destination': 'HAN', 'departure_date': '2026-12-09',
            'return_date': '2027-01-09', 'fetched_at': '2026-10-04T06:00:00+00:00',
            'status': 'observed', 'lowest_observed_price_eur': 863,
            'completion_version': 3, 'completion_evidence': 'dom_settled',
            'candidate_cards': [{'cookies': 'private'}], 'notes': ['private path'], 'gl': 'FI',
        }

    def test_verified_fare_is_exported_with_allowlist(self):
        fare = public_fare(self.row, 'skyscanner')
        self.assertEqual(fare['price_eur'], 863)
        self.assertEqual(fare['status'], 'complete')
        self.assertNotIn('candidate_cards', fare)
        self.assertNotIn('notes', fare)
        self.assertIn('/hel/han/261209/270109/', fare['search_url'])

    def test_google_prices_require_the_current_loading_check(self):
        self.assertIsNone(public_fare(self.row, 'google_flights'))
        checked = {**self.row, 'google_completion_check': 1}
        self.assertIsNotNone(public_fare(checked, 'google_flights'))
        self.assertIsNone(public_fare({**checked, 'google_completion_check': 0}, 'google_flights'))

    def test_rejects_unverified_or_wrong_search(self):
        for key, value in [
            ('status', 'timeout'), ('completion_version', 2),
            ('completion_evidence', ''), ('lowest_observed_price_eur', float('nan')),
            ('gl', 'SE'), ('passengers', 2), ('cabin', 'business'),
            ('return_date', '2026-12-08'), ('origin', '../'),
            ('fetched_at', '2026-10-04T06:00:00'),
        ]:
            with self.subTest(key=key, value=value):
                self.assertIsNone(public_fare({**self.row, key: value}, 'skyscanner'))


if __name__ == '__main__':
    unittest.main()
