# Google Flights browser-automation handoff

## Goal

Find round-trip Helsinki (HEL) to Hanoi (HAN) fares for one adult in economy.

- Departure and return dates must fall between 9 December 2026 and 9 January 2027.
- Minimum stay: 21 nights.
- Budget target: €800 total, including mandatory taxes and fees.
- Prefer a protected, one-ticket itinerary; also show cheaper separate-ticket options when Google explicitly labels them that way.
- Maximum duration: 30 hours for each direction.
- Long city layovers are allowed; do not reject them merely for being 10–24 hours.

## Live evidence confirmed in the in-app browser

Google Flights was opened with the exact selected itinerary. The live rendered page showed:

| Item | Confirmed value |
| --- | --- |
| Total | €800 for one adult, economy, including required taxes and fees |
| Outbound | 9 Dec 2026: HEL → CDG → HAN; AY 1571 then VN 18; 17h 35m; 3h 20m in Paris |
| Return | 9 Jan 2027: HAN → AMS → HEL; VN 83 then AY 1306; 20h 55m; 5h in Amsterdam |
| €800 sellers | Mytrip, Flightnetwork, Gotogate |
| Other observed seller | Booking.com: €828 |

Google's results page explicitly displayed `Separate tickets booked together` for some *other* itineraries, but **not** for this €800 itinerary. This is useful evidence, but not a guarantee of baggage through-checking or missed-connection protection. That must be confirmed in the chosen seller's terms before purchasing.

No purchase was started or completed.

## Why the current Python approach is not trustworthy

`find_flights.py` uses the unofficial `flights` / `fli` package. It does not reproduce the live Google Flights UI:

1. It returns cached data before trying a fresh request.
2. It expands only a limited number of outbound candidates for a round trip, rather than evaluating every possible pairing.
3. It works from a simplified internal request format that the package itself documents as unable to represent self-transfer filtering correctly.
4. Google can show dynamic reseller/combined offers in the browser that are absent from that API response.
5. The current script's round-trip serialization needs repair: the library returns tuples for multi-leg results, while the current `serialize_result()` assumes a single result object.
6. The €800 record embedded in the script was copied from the screenshot and was incorrectly classified as a self-transfer. It is a reference fixture, not live data.

Treat the current CLI as an experiment, not a fare finder, until it is replaced or substantially redesigned.

## Recommended implementation: dedicated persistent browser profile

Use Playwright with a **dedicated local Chromium profile**, launched visibly. The profile preserves the Google Flights session between runs without saving credentials in project files.

Suggested layout (all ignored by Git):

```text
.browser-profile/       # browser cookies/session; never commit
.flight_cache/          # timestamped extracted results; never treat as live
flight_results/         # optional redacted JSON/CSV exports
```

Do not use the normal personal Chrome profile. A dedicated profile makes it easy to inspect, clear, or delete only this tool's browser state.

### One-time setup next session

1. Add Playwright to the project environment and install its Chromium browser.
2. Create/launch a persistent context whose profile directory comes from an environment variable, for example `GOOGLE_FLIGHTS_PROFILE_DIR`.
3. Launch headful (`headless=False`) on the first run.
4. The user signs in to Google themselves if desired. Never place passwords, cookies, or an exported profile in source code, logs, Markdown, or Git.
5. Add `.browser-profile/`, `.flight_cache/`, and result files containing booking URLs to `.gitignore`.

The tool should pause rather than try to defeat CAPTCHA, bot checks, or sign-in flows. Google Flights' terms and rate limits should be respected; use small date windows and a human-supervised browser.

## MVP design

### Input

Use a small JSON or CLI configuration with:

```json
{
  "origin": "HEL",
  "destination": "HAN",
  "departure_from": "2026-12-09",
  "departure_to": "2026-12-19",
  "return_from": "2026-12-30",
  "return_to": "2027-01-09",
  "minimum_stay_nights": 21,
  "max_price_eur": 800,
  "max_direction_minutes": 1800
}
```

The narrow departure range above is deliberate: departures later than 19 Dec cannot meet a 21-night minimum and return by 9 Jan.

### Browser workflow

1. Navigate visibly to Google Flights.
2. Set one passenger, economy, HEL, HAN, the selected date pair, and EUR.
3. Read the rendered accessible DOM after results load. Google currently exposes result summaries such as `From 800 euros round trip total` and may expose `Separate tickets booked together` as visible text.
4. Extract candidate price, total duration, stops, carriers, layovers, and the literal Google label about separate tickets.
5. Select the best candidate only to read its booking-options page; extract seller names and prices. Do not click `Continue` or start a purchase.
6. Store a timestamped result with `source: "Google Flights UI"` and mark the protection status as:
   - `separate_tickets` only if Google visibly says `Separate tickets booked together`;
   - `not_flagged_by_google` if no such label is present;
   - `seller_confirmation_required` for baggage and missed-connection protection in every case.

### Result shape

```json
{
  "fetched_at": "ISO-8601 timestamp",
  "source": "Google Flights UI",
  "total_price_eur": 800,
  "protection_label": "not_flagged_by_google",
  "seller_confirmation_required": true,
  "outbound": { "duration_minutes": 1055, "layovers": [{"airport": "CDG", "minutes": 200}] },
  "return": { "duration_minutes": 1255, "layovers": [{"airport": "AMS", "minutes": 300}] },
  "offers": [
    {"seller": "Mytrip", "price_eur": 800},
    {"seller": "Flightnetwork", "price_eur": 800},
    {"seller": "Gotogate", "price_eur": 800}
  ]
}
```

## Date-search strategy

Do not launch a 121-combination brute-force grid in one unattended run. Flight pages can rate-limit, and each result is volatile.

Build in stages:

1. Exact date pair: validate the browser scraper against 9 Dec / 9 Jan and the €800 deal above.
2. Small batch: 2–4 departure dates and 2–4 return dates, enforcing 21 nights before navigation.
3. Larger scan: only with visible progress, throttling, durable timestamped cache, and a manual stop control.

For each successful run, distinguish live result time from cached result time in the output. Never carry a previous fare forward as if it were bookable now.

## Acceptance checks for the rewrite

- The exact 9 Dec / 9 Jan page produces an observed €800 result with at least one €800 seller while it remains live.
- A result explicitly labelled `Separate tickets booked together` is shown in the separate-ticket section.
- The €800 itinerary is not automatically classified as separate-ticket merely because it has multiple airlines.
- No result over €800, under 21 nights, or over 30 hours in either direction is shown as eligible.
- The program never clicks a purchase/checkout control.
- Credentials, cookies, browser profiles, and unredacted ephemeral booking tokens are never committed.

## First work items next time

1. Replace the `fli`-based fetch path instead of extending it.
2. Add Playwright and a visible persistent-context launcher.
3. Add a single-pair DOM extractor and save its output as JSON.
4. Validate it against the live €800 page before implementing date ranges.
5. Add a small, throttled date-batch runner and clear output categories.
