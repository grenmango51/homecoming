// Pure validation shared by the hosted backend. Mirrors src/local_search.validate_search
// in the Python repo; the worker re-validates with the original Python code.

export type TripType = "roundtrip" | "oneway";
export interface SearchQuery {
  origin: string;
  destination: string;
  departure: string;
  return_date: string | null;
  trip_type: TripType;
  flex_days: 0 | 1 | 3;
  min_stay_nights: number;
}
export type Pair = [string, string | null];

const KEYS = ["origin", "destination", "departure", "return_date", "trip_type", "flex_days", "min_stay_nights"];
export const MAX_PAYLOAD_BYTES = 8192;

interface RawSearch { origin?: unknown; destination?: unknown; departure?: unknown; return_date?: unknown; trip_type?: unknown; flex_days?: unknown; min_stay_nights?: unknown }
interface RawFare { provider?: unknown; origin?: unknown; destination?: unknown; departure?: unknown; return_date?: unknown; price_eur?: unknown; observed_at?: unknown; status?: unknown; market?: unknown; passengers?: unknown; cabin?: unknown }

export class InputError extends Error {}

function parseIso(value: unknown): Date | null {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
  const d = new Date(`${value}T00:00:00Z`);
  return Number.isNaN(d.getTime()) || d.toISOString().slice(0, 10) !== value ? null : d;
}
const iso = (d: Date) => d.toISOString().slice(0, 10);
const addDays = (d: Date, n: number) => new Date(d.getTime() + n * 86400000);
const days = (a: Date, b: Date) => Math.round((b.getTime() - a.getTime()) / 86400000);

export function utcToday(now = new Date()): Date {
  return new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
}

export function validateSearch(raw: unknown, today: Date = utcToday()): { query: SearchQuery; pairs: Pair[] } {
  if (raw === null || typeof raw !== "object" || Array.isArray(raw)) throw new InputError("Search must be a JSON object.");
  if (JSON.stringify(raw).length > MAX_PAYLOAD_BYTES) throw new InputError("Search is too large.");
  const obj = raw as RawSearch;
  const keys = Object.keys(obj);
  if (keys.length !== KEYS.length || !keys.every((k) => KEYS.includes(k))) throw new InputError("Unexpected search fields.");
  const { origin, destination, trip_type, flex_days, min_stay_nights } = obj;
  if (typeof origin !== "string" || typeof destination !== "string" || !/^[A-Z]{3}$/.test(origin) || !/^[A-Z]{3}$/.test(destination))
    throw new InputError("Enter three-letter airport codes.");
  if (origin === destination) throw new InputError("Choose different airports.");
  if (trip_type !== "roundtrip" && trip_type !== "oneway") throw new InputError("Choose round trip or one way.");
  if (typeof flex_days !== "number" || !Number.isInteger(flex_days) || ![0, 1, 3].includes(flex_days))
    throw new InputError("Flexible dates must be 0, 1 or 3 days.");
  if (typeof min_stay_nights !== "number" || !Number.isInteger(min_stay_nights) || min_stay_nights < 1 || min_stay_nights > 365)
    throw new InputError("Minimum stay must be between 1 and 365 nights.");
  const dep = parseIso(obj.departure);
  if (!dep) throw new InputError("Choose valid travel dates.");
  let ret: Date | null = null;
  if (trip_type === "roundtrip") {
    ret = parseIso(obj.return_date);
    if (!ret) throw new InputError("Choose valid travel dates.");
  } else if (obj.return_date !== null) {
    throw new InputError("One-way searches have no return date.");
  }
  const horizon = addDays(today, 365);
  if (dep < today || dep > horizon) throw new InputError("Departure must be within the next year.");
  if (ret && (ret <= dep || days(dep, ret) < min_stay_nights || ret > horizon))
    throw new InputError("Return must meet your minimum stay and be within the next year.");
  const pairs: Pair[] = [];
  for (let o = -flex_days; o <= flex_days; o++) {
    const d = addDays(dep, o);
    if (d < today || d > horizon) continue;
    if (!ret) { pairs.push([iso(d), null]); continue; }
    for (let r = -flex_days; r <= flex_days; r++) {
      const rd = addDays(ret, r);
      if (days(d, rd) >= min_stay_nights && rd <= horizon) pairs.push([iso(d), iso(rd)]);
    }
  }
  if (!pairs.length) throw new InputError("No travel dates match these settings.");
  return {
    query: { origin, destination, departure: iso(dep), return_date: ret ? iso(ret) : null, trip_type, flex_days: flex_days as 0 | 1 | 3, min_stay_nights },
    pairs,
  };
}

const yymmdd = (s: string) => s.slice(2, 4) + s.slice(5, 7) + s.slice(8, 10);

export function googleUrl(origin: string, dest: string, dep: string, ret: string | null): string {
  const q = ret
    ? `Flights to ${dest} from ${origin} on ${dep} through ${ret}`
    : `Flights to ${dest} from ${origin} on ${dep} one way`;
  return `https://www.google.com/travel/flights?q=${encodeURIComponent(q)}&hl=en&gl=FI&curr=EUR`;
}
export function skyscannerUrl(origin: string, dest: string, dep: string, ret: string | null): string {
  return `https://www.skyscanner.fi/transport/flights/${origin.toLowerCase()}/${dest.toLowerCase()}/${yymmdd(dep)}${ret ? `/${yymmdd(ret)}` : ""}/?adultsv2=1&cabinclass=economy&rtn=${ret ? 1 : 0}&currency=EUR&sortby=cheapest&outboundaltsenabled=false&inboundaltsenabled=false`;
}

export interface PublishedFare {
  provider: "google_flights" | "skyscanner";
  origin: string; destination: string; departure: string; return_date: string | null;
  price_eur: number; observed_at: string; search_url: string;
}

/** Accept a worker-reported fare only if it matches this job exactly; rebuild the link ourselves. */
export function acceptFare(raw: unknown, job: { query: SearchQuery; pairs: Pair[]; started_at: string }, now = new Date()): PublishedFare | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const f = raw as RawFare;
  if (f.status !== "complete" || f.market !== "FI" || f.passengers !== 1 || f.cabin !== "economy") return null;
  const provider = f.provider;
  if (provider !== "google_flights" && provider !== "skyscanner") return null;
  if (f.origin !== job.query.origin || f.destination !== job.query.destination) return null;
  const ret = f.return_date ?? null;
  if (typeof f.departure !== "string" || (ret !== null && typeof ret !== "string")) return null;
  if (!job.pairs.some(([d, r]) => d === f.departure && r === ret)) return null;
  if (typeof f.price_eur !== "number" || !Number.isFinite(f.price_eur) || f.price_eur <= 0 || f.price_eur >= 100000) return null;
  if (typeof f.observed_at !== "string") return null;
  const obs = new Date(f.observed_at);
  if (Number.isNaN(obs.getTime()) || !/[zZ]|[+-]\d{2}:\d{2}$/.test(f.observed_at)) return null;
  if (obs < new Date(job.started_at) || obs.getTime() > now.getTime() + 60000) return null;
  const url = provider === "google_flights"
    ? googleUrl(job.query.origin, job.query.destination, f.departure, ret as string | null)
    : skyscannerUrl(job.query.origin, job.query.destination, f.departure, ret as string | null);
  return { provider, origin: job.query.origin, destination: job.query.destination, departure: f.departure,
    return_date: ret as string | null, price_eur: Math.round(f.price_eur * 100) / 100, observed_at: obs.toISOString(), search_url: url };
}

const STATUS_RE = /^[a-z_]{1,32}$/;
/** Sanitise provider progress counters reported by the worker. */
export function acceptProviders(raw: unknown, expected: number): Record<string, { verified: number; expected: number; statuses: Record<string, number> }> | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const out: Record<string, { verified: number; expected: number; statuses: Record<string, number> }> = {};
  for (const p of ["google_flights", "skyscanner"]) {
    const v = (raw as Record<string, { statuses?: unknown } | undefined>)[p];
    const statuses: Record<string, number> = {};
    if (v && typeof v.statuses === "object" && v.statuses && !Array.isArray(v.statuses)) {
      for (const [k, n] of Object.entries(v.statuses).slice(0, 12)) {
        if (STATUS_RE.test(k) && typeof n === "number" && Number.isInteger(n) && n >= 0 && n <= 1000) statuses[k] = n;
      }
    }
    out[p] = { verified: 0, expected, statuses };
  }
  return out;
}

