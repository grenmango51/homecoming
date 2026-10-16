import { createServerFn } from "@tanstack/react-start";
import { requireSupabaseAuth } from "@/integrations/supabase/auth-middleware";
import { InputError, validateSearch } from "./flight-query";

const WORKER_OFFLINE_AFTER_MS = 90_000;
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

type Ctx = { supabase: any; userId: string };
export interface ProviderProgress { verified: number; expected: number; statuses: { [status: string]: number } }
export interface JobSnapshot {
  id: string; status: string; message: string; queued: boolean; elapsed_seconds: number;
  query: { origin: string; destination: string; departure: string; return_date: string | null; trip_type: string; flex_days: number; min_stay_nights: number };
  fares: { provider: string; origin: string; destination: string; departure: string; return_date: string | null; price_eur: number; observed_at: string; search_url: string; status: string; market: string; passengers: number; cabin: string }[];
  providers: { google_flights: ProviderProgress; skyscanner: ProviderProgress };
}
export interface WorkerInfo { online: boolean; last_seen_at: string | null; state: string | null }

async function assertOwner({ supabase, userId }: Ctx) {
  const { data, error } = await supabase.rpc("is_flight_owner", { _uid: userId });
  if (error || data !== true) throw new Response("Forbidden", { status: 403 });
}

async function snapshot(supabase: any, jobId: string): Promise<JobSnapshot | null> {
  const { data: job } = await supabase.from("flight_jobs")
    .select("id,status,query,pairs,message,providers,created_at,started_at,finished_at,cancel_requested")
    .eq("id", jobId).maybeSingle();
  if (!job) return null;
  const { data: fares } = await supabase.from("flight_fares")
    .select("provider,origin,destination,departure,return_date,price_eur,observed_at,search_url").eq("job_id", jobId);
  const rows = (fares ?? []).map((f: any) => ({ ...f, price_eur: Number(f.price_eur), status: "complete", market: "FI", passengers: 1, cabin: "economy" }));
  const expected = (job.pairs as unknown[]).length;
  const providers: any = {};
  for (const p of ["google_flights", "skyscanner"]) {
    const info = (job.providers as any)?.[p] ?? {};
    providers[p] = { verified: rows.filter((r: any) => r.provider === p).length, expected, statuses: info.statuses ?? {} };
  }
  const start = job.started_at ?? job.created_at;
  const end = job.finished_at ? new Date(job.finished_at).getTime() : Date.now();
  return {
    id: job.id, status: job.status, query: job.query, message: job.message, fares: rows, providers,
    queued: job.status === "queued", elapsed_seconds: Math.max(0, Math.round((end - new Date(start).getTime()) / 1000)),
  };
}

async function workerStatus(supabase: any): Promise<WorkerInfo> {
  const { data } = await supabase.from("flight_worker_status").select("last_seen_at,state").maybeSingle();
  const seen = data?.last_seen_at ? new Date(data.last_seen_at).getTime() : 0;
  return { online: !!seen && Date.now() - seen < WORKER_OFFLINE_AFTER_MS, last_seen_at: data?.last_seen_at ?? null, state: data?.state ?? null };
}

export const getFlightSession = createServerFn({ method: "GET" })
  .middleware([requireSupabaseAuth])
  .handler(async ({ context }): Promise<{ owner: boolean; job: JobSnapshot | null; worker: WorkerInfo | null }> => {
    const ctx = context as unknown as Ctx;
    const { data: isOwner } = await ctx.supabase.rpc("is_flight_owner", { _uid: ctx.userId });
    if (isOwner !== true) return { owner: false, job: null, worker: null };
    await ctx.supabase.rpc("flight_owner_refresh");
    const { data: latest } = await ctx.supabase.from("flight_jobs").select("id").eq("owner_id", ctx.userId)
      .order("created_at", { ascending: false }).limit(1).maybeSingle();
    return { owner: true, job: latest ? await snapshot(ctx.supabase, latest.id) : null, worker: await workerStatus(ctx.supabase) };
  });

export const submitFlightSearch = createServerFn({ method: "POST" })
  .middleware([requireSupabaseAuth])
  .validator((input: unknown) => {
    if (!input || typeof input !== "object" || Array.isArray(input)) throw new Error("Invalid request.");
    const { search, idempotency_key, ...rest } = input as Record<string, unknown>;
    if (Object.keys(rest).length) throw new Error("Invalid request.");
    if (typeof idempotency_key !== "string" || !UUID_RE.test(idempotency_key)) throw new Error("Invalid request.");
    return { search, idempotency_key };
  })
  .handler(async ({ data, context }): Promise<{ error: string | null; job: JobSnapshot | null; worker: WorkerInfo | null }> => {
    const ctx = context as unknown as Ctx;
    await assertOwner(ctx);
    const fail = (error: string) => ({ error, job: null, worker: null });
    let validated;
    try { validated = validateSearch(data.search); }
    catch (e) { if (e instanceof InputError) return fail(e.message); throw e; }
    const { data: jobId, error } = await ctx.supabase.rpc("flight_submit", {
      _query: validated.query, _pairs: validated.pairs, _idem: data.idempotency_key,
    });
    if (error) {
      if (/busy/.test(error.message)) return fail("A search is already running. Stop it before starting another.");
      if (/rate_limited/.test(error.message)) return fail("You've started 10 searches in the last hour. Please wait a little.");
      if (/invalid_search/.test(error.message)) return fail("Check the airports and travel dates.");
      if (/forbidden/.test(error.message)) throw new Response("Forbidden", { status: 403 });
      return fail("Could not start the search. Please try again.");
    }
    return { error: null, job: await snapshot(ctx.supabase, jobId as string), worker: await workerStatus(ctx.supabase) };
  });

export const getFlightJob = createServerFn({ method: "GET" })
  .middleware([requireSupabaseAuth])
  .validator((input: { id: string }) => {
    if (!input || typeof input.id !== "string" || !UUID_RE.test(input.id)) throw new Error("Invalid request.");
    return { id: input.id };
  })
  .handler(async ({ data, context }) => {
    const ctx = context as unknown as Ctx;
    await assertOwner(ctx);
    await ctx.supabase.rpc("flight_owner_refresh");
    const job = await snapshot(ctx.supabase, data.id);
    if (!job) throw new Response("Not found", { status: 404 });
    return { job, worker: await workerStatus(ctx.supabase) };
  });

export const cancelFlightJob = createServerFn({ method: "POST" })
  .middleware([requireSupabaseAuth])
  .validator((input: { id: string }) => {
    if (!input || typeof input.id !== "string" || !UUID_RE.test(input.id)) throw new Error("Invalid request.");
    return { id: input.id };
  })
  .handler(async ({ data, context }) => {
    const ctx = context as unknown as Ctx;
    await assertOwner(ctx);
    const { error } = await ctx.supabase.rpc("flight_cancel", { _job: data.id });
    if (error) throw new Response("Forbidden", { status: 403 });
    return { job: await snapshot(ctx.supabase, data.id), worker: await workerStatus(ctx.supabase) };
  });

