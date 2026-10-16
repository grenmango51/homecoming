import { describe, expect, it } from "vitest";
import { acceptFare, validateSearch } from "@/lib/flight-query";

const today = new Date(Date.UTC(2026, 9, 4));
const base = { origin: "HEL", destination: "HAN", departure: "2026-11-05", return_date: "2026-12-09", trip_type: "roundtrip", flex_days: 3, min_stay_nights: 21 };

describe("validateSearch", () => {
  it("accepts the canonical request and builds up to 49 pairs", () => {
    const { pairs } = validateSearch(base, today);
    expect(pairs.length).toBe(49);
    expect(validateSearch({ ...base, flex_days: 1 }, today).pairs.length).toBe(9);
    expect(validateSearch({ ...base, flex_days: 0 }, today).pairs).toEqual([["2026-11-05", "2026-12-09"]]);
  });
  it("one-way gives up to 7 departures with null return", () => {
    const { pairs } = validateSearch({ ...base, trip_type: "oneway", return_date: null }, today);
    expect(pairs.length).toBe(7);
    expect(pairs.every(([, r]) => r === null)).toBe(true);
  });
  it("excludes past dates and short stays", () => {
    const { pairs } = validateSearch({ ...base, departure: "2026-10-05", return_date: "2026-10-27", flex_days: 3 }, today);
    expect(pairs.every(([d]) => d >= "2026-10-04")).toBe(true);
    expect(pairs.every(([d, r]) => (Date.parse(r!) - Date.parse(d)) / 864e5 >= 21)).toBe(true);
  });
  const bad: Array<[string, unknown]> = [
    ["extra key", { ...base, cmd: "rm -rf /" }],
    ["missing key", { origin: "HEL" }],
    ["array", [base]],
    ["lowercase", { ...base, origin: "hel" }],
    ["same airport", { ...base, destination: "HEL" }],
    ["bool flex", { ...base, flex_days: true }],
    ["flex 2", { ...base, flex_days: 2 }],
    ["string stay", { ...base, min_stay_nights: "21" }],
    ["float stay", { ...base, min_stay_nights: 21.5 }],
    ["stay 0", { ...base, min_stay_nights: 0 }],
    ["bad date", { ...base, departure: "2026-02-30" }],
    ["past", { ...base, departure: "2026-10-01" }],
    ["too far", { ...base, departure: "2027-12-01", return_date: "2027-12-30" }],
    ["shell in code", { ...base, origin: "$(id)" }],
    ["url", { ...base, destination: "https://evil" }],
    ["oneway with return", { ...base, trip_type: "oneway" }],
    ["huge", { ...base, origin: "A".repeat(9000) }],
  ];
  it.each(bad)("rejects %s", (_n, input) => expect(() => validateSearch(input, today)).toThrow());
});

describe("acceptFare", () => {
  const { query, pairs } = validateSearch({ ...base, flex_days: 0 }, today);
  const job = { query, pairs, started_at: "2026-10-04T08:00:00Z" };
  const fare = { provider: "google_flights", origin: "HEL", destination: "HAN", departure: "2026-11-05", return_date: "2026-12-09",
    price_eur: 612, observed_at: "2026-10-04T08:05:00+00:00", status: "complete", search_url: "https://evil.example/", market: "FI", passengers: 1, cabin: "economy" };
  const now = new Date("2026-10-04T09:00:00Z");
  it("accepts verified rows and rebuilds the provider link", () => {
    const out = acceptFare(fare, job, now)!;
    expect(out.search_url.startsWith("https://www.google.com/travel/flights?")).toBe(true);
  });
  it.each([
    ["incomplete", { ...fare, status: "blocked" }],
    ["other provider", { ...fare, provider: "kayak" }],
    ["other route", { ...fare, destination: "BKK" }],
    ["unrequested date", { ...fare, departure: "2026-11-06" }],
    ["stale observation", { ...fare, observed_at: "2026-10-03T08:00:00+00:00" }],
    ["future observation", { ...fare, observed_at: "2026-10-05T08:00:00+00:00" }],
    ["string price", { ...fare, price_eur: "612" }],
    ["negative", { ...fare, price_eur: -1 }],
    ["other market", { ...fare, market: "US" }],
  ])("drops %s", (_n, f) => expect(acceptFare(f, job, now)).toBeNull());
});

