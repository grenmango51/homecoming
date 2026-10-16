import { createFileRoute } from "@tanstack/react-router";
import { timingSafeEqual } from "crypto";
import { acceptFare, acceptProviders, type Pair, type SearchQuery } from "@/lib/flight-query";

// Outbound-only worker API. The Ubuntu worker authenticates with WORKER_TOKEN
// (Authorization: Bearer ...). Missing/short secret => every call fails closed.
const MAX_BODY = 64 * 1024;
const LEASE_SECONDS = 120;
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", "cache-control": "no-store" } });

function authorized(request: Request): boolean {
  const secret = process.env["WORKER_TOKEN"];
  if (!secret || secret.length < 32) return false;
  const header = request.headers.get("authorization") ?? "";
  if (!header.startsWith("Bearer ")) return false;
  const a = Buffer.from(header.slice(7));
  const b = Buffer.from(secret);
  return a.length === b.length && timingSafeEqual(a, b);
}

async function readBody(request: Request): Promise<Record<string, unknown> | null> {
  if ((request.headers.get("content-type") ?? "").split(";")[0] !== "application/json") return null;
  const text = await request.text();
  if (text.length > MAX_BODY) return null;
  try {
    const v = JSON.parse(text);
    return v && typeof v === "object" && !Array.isArray(v) ? v : null;
  } catch { return null; }
}

export const Route = createFileRoute("/api/public/worker/$action")({
  server: {
    handlers: {
      POST: async ({ request, params }) => {
        if (!authorized(request)) return json(401, { error: "unauthorized" });
        const body = await readBody(request);
        if (!body) return json(400, { error: "bad_request" });
        const { supabaseAdmin } = await import("@/integrations/supabase/client.server");
        const db = supabaseAdmin as any;

        switch (params.action) {
          case "heartbeat": {
            const state = typeof body["state"] === "string" && /^[a-z_]{1,32}$/.test(body["state"]) ? body["state"] : "idle";
            const version = typeof body["version"] === "string" ? body["version"].replace(/[^\w.\-]/g, "").slice(0, 64) : "";
            const { error } = await db.rpc("flight_worker_heartbeat", { _state: state, _version: version });
            return error ? json(500, { error: "db" }) : json(200, { ok: true });
          }
          case "claim": {
            const { data, error } = await db.rpc("flight_worker_claim", { _lease_seconds: LEASE_SECONDS });
            if (error) return json(500, { error: "db" });
            const job = (data ?? [])[0];
            if (!job) return json(200, { job: null });
            return json(200, { job: { id: job.id, lease_token: job.lease_token, search: job.query, pairs: job.pairs,
              started_at: job.started_at, lease_seconds: LEASE_SECONDS, runtime_budget_seconds: 1800 } });
          }
          case "progress": {
            const { job_id, lease_token } = body;
            if (typeof job_id !== "string" || !UUID_RE.test(job_id) || typeof lease_token !== "string" || !UUID_RE.test(lease_token))
              return json(400, { error: "bad_request" });
            const { data: job } = await db.from("flight_jobs").select("query,pairs,started_at").eq("id", job_id).maybeSingle();
            if (!job) return json(409, { error: "stale_lease" });
            const ctx = { query: job.query as SearchQuery, pairs: job.pairs as Pair[], started_at: job.started_at as string };
            const fares = Array.isArray(body["fares"]) ? body["fares"].slice(0, 200).map((f) => acceptFare(f, ctx)).filter(Boolean) : [];
            const providers = acceptProviders(body["providers"], ctx.pairs.length);
            const { data: cancel, error } = await db.rpc("flight_worker_progress", {
              _job: job_id, _lease: lease_token, _providers: providers, _fares: fares, _lease_seconds: LEASE_SECONDS,
            });
            if (error) return /stale_lease/.test(error.message) ? json(409, { error: "stale_lease" }) : json(500, { error: "db" });
            return json(200, { cancel: cancel === true, accepted: fares.length });
          }
          case "finish": {
            const { job_id, lease_token, outcome } = body;
            if (typeof job_id !== "string" || !UUID_RE.test(job_id) || typeof lease_token !== "string" || !UUID_RE.test(lease_token)
              || !["done", "failed", "timeout", "cancelled"].includes(outcome as string))
              return json(400, { error: "bad_request" });
            const { error } = await db.rpc("flight_worker_finish", { _job: job_id, _lease: lease_token, _outcome: outcome, _message: "" });
            if (error) return /stale_lease/.test(error.message) ? json(409, { error: "stale_lease" }) : json(500, { error: "db" });
            return json(200, { ok: true });
          }
          default:
            return json(404, { error: "not_found" });
        }
      },
    },
  },
});

