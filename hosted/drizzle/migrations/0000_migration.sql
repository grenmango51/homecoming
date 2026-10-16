-- Flight Finder hosted queue. Owner-only; worker writes go through service-only RPCs.
create table public.app_owner_config (
  id boolean primary key default true check (id),
  owner_email text not null
);
insert into public.app_owner_config(owner_email) values ('owner@example.com');
grant all on public.app_owner_config to service_role;
alter table public.app_owner_config enable row level security;

create or replace function public.is_flight_owner(_uid uuid default auth.uid())
returns boolean language sql stable security definer set search_path = public, auth as $$
  select exists (
    select 1 from auth.users u, public.app_owner_config c
    where u.id = _uid and lower(u.email) = lower(c.owner_email)
      and u.email_confirmed_at is not null and u.is_anonymous is not true
  )
$$;
revoke all on function public.is_flight_owner(uuid) from public, anon;
grant execute on function public.is_flight_owner(uuid) to authenticated, service_role;

create type public.flight_job_status as enum ('queued','running','complete','partial','failed','cancelled');

create table public.flight_jobs (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null,
  idempotency_key uuid not null,
  query jsonb not null,
  pairs jsonb not null,
  status public.flight_job_status not null default 'queued',
  message text not null default 'Waiting for the search server…',
  cancel_requested boolean not null default false,
  lease_token uuid,
  lease_expires_at timestamptz,
  claimed_at timestamptz,
  started_at timestamptz,
  finished_at timestamptz,
  providers jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  unique (owner_id, idempotency_key),
  check (octet_length(query::text) <= 8192),
  check (jsonb_typeof(pairs) = 'array' and jsonb_array_length(pairs) between 1 and 49)
);
-- One active (running) job globally.
create unique index flight_jobs_one_running on public.flight_jobs ((true)) where status = 'running';
create index flight_jobs_owner_created on public.flight_jobs (owner_id, created_at desc);
grant select on public.flight_jobs to authenticated;
grant all on public.flight_jobs to service_role;
alter table public.flight_jobs enable row level security;
create policy "owner reads own jobs" on public.flight_jobs for select to authenticated
  using (owner_id = auth.uid() and public.is_flight_owner(auth.uid()));

create table public.flight_fares (
  job_id uuid not null references public.flight_jobs(id) on delete cascade,
  provider text not null check (provider in ('google_flights','skyscanner')),
  origin text not null check (origin ~ '^[A-Z]{3}$'),
  destination text not null check (destination ~ '^[A-Z]{3}$'),
  departure date not null,
  return_date date,
  price_eur numeric(10,2) not null check (price_eur > 0 and price_eur < 100000),
  observed_at timestamptz not null,
  search_url text not null,
  primary key (job_id, provider, departure, return_date)
);
-- primary key cannot include null return_date; use unique index with coalesce instead
alter table public.flight_fares drop constraint flight_fares_pkey;
create unique index flight_fares_unique on public.flight_fares (job_id, provider, departure, coalesce(return_date, '0001-01-01'::date));
grant select on public.flight_fares to authenticated;
grant all on public.flight_fares to service_role;
alter table public.flight_fares enable row level security;
create policy "owner reads own fares" on public.flight_fares for select to authenticated
  using (public.is_flight_owner(auth.uid()) and exists (select 1 from public.flight_jobs j where j.id = job_id and j.owner_id = auth.uid()));

create table public.flight_worker_status (
  id boolean primary key default true check (id),
  last_seen_at timestamptz,
  state text,
  version text
);
insert into public.flight_worker_status(id) values (true);
grant select on public.flight_worker_status to authenticated;
grant all on public.flight_worker_status to service_role;
alter table public.flight_worker_status enable row level security;
create policy "owner reads worker status" on public.flight_worker_status for select to authenticated
  using (public.is_flight_owner(auth.uid()));

-- Expire leases whose worker vanished, and jobs past the 30 min budget (+5 min cleanup).
-- Expired jobs are failed, never re-queued, so a job cannot execute twice.
create or replace function public.flight_reap() returns void language plpgsql security definer set search_path = public as $$
begin
  update public.flight_jobs set status = case when exists (select 1 from public.flight_fares f where f.job_id = flight_jobs.id) then 'partial'::flight_job_status else 'failed'::flight_job_status end,
    message = case when started_at < now() - interval '35 minutes' then 'Time limit reached; completed prices are shown.' else 'The search server stopped responding. Completed prices are shown.' end,
    finished_at = now(), lease_token = null
  where status = 'running' and (lease_expires_at < now() or started_at < now() - interval '35 minutes');
  update public.flight_jobs set status = 'cancelled', message = 'Search stopped before it started.', finished_at = now()
  where status = 'queued' and cancel_requested;
  update public.flight_jobs set status = 'failed', message = 'The search server did not pick up this search within an hour.', finished_at = now()
  where status = 'queued' and created_at < now() - interval '1 hour';
end $$;

-- Owner submit: owner check, idempotency, rate limit, bounded queue.
create or replace function public.flight_submit(_query jsonb, _pairs jsonb, _idem uuid)
returns uuid language plpgsql security definer set search_path = public as $$
declare uid uuid := auth.uid(); existing uuid; new_id uuid;
begin
  if uid is null or not public.is_flight_owner(uid) then raise exception 'forbidden' using errcode = '42501'; end if;
  perform pg_advisory_xact_lock(hashtext('flight_submit'));
  select id into existing from public.flight_jobs where owner_id = uid and idempotency_key = _idem;
  if existing is not null then return existing; end if;
  perform public.flight_reap();
  if (select count(*) from public.flight_jobs where owner_id = uid and created_at > now() - interval '1 hour') >= 10 then
    raise exception 'rate_limited' using errcode = 'P0001'; end if;
  if (select count(*) from public.flight_jobs where status in ('queued','running')) >= 1 then
    raise exception 'busy' using errcode = 'P0001'; end if;
  insert into public.flight_jobs(owner_id, idempotency_key, query, pairs, providers)
  values (uid, _idem, _query, _pairs, jsonb_build_object(
    'google_flights', jsonb_build_object('verified',0,'expected',jsonb_array_length(_pairs),'statuses','{}'::jsonb),
    'skyscanner', jsonb_build_object('verified',0,'expected',jsonb_array_length(_pairs),'statuses','{}'::jsonb)))
  returning id into new_id;
  return new_id;
end $$;
revoke all on function public.flight_submit(jsonb, jsonb, uuid) from public, anon;
grant execute on function public.flight_submit(jsonb, jsonb, uuid) to authenticated;

create or replace function public.flight_cancel(_job uuid) returns void language plpgsql security definer set search_path = public as $$
declare uid uuid := auth.uid();
begin
  if uid is null or not public.is_flight_owner(uid) then raise exception 'forbidden' using errcode = '42501'; end if;
  update public.flight_jobs set cancel_requested = true,
    status = case when status = 'queued' then 'cancelled'::flight_job_status else status end,
    message = case when status = 'queued' then 'Search stopped before it started.' else 'Stopping search…' end,
    finished_at = case when status = 'queued' then now() else finished_at end
  where id = _job and owner_id = uid and status in ('queued','running');
end $$;
revoke all on function public.flight_cancel(uuid) from public, anon;
grant execute on function public.flight_cancel(uuid) to authenticated;

-- Worker RPCs: service_role only (called by the hosted worker endpoints after WORKER_TOKEN check).
create or replace function public.flight_worker_heartbeat(_state text, _version text) returns void language sql security definer set search_path = public as $$
  update public.flight_worker_status set last_seen_at = now(), state = left(_state, 32), version = left(_version, 64) where id;
$$;

create or replace function public.flight_worker_claim(_lease_seconds int) returns setof public.flight_jobs language plpgsql security definer set search_path = public as $$
declare j public.flight_jobs;
begin
  perform pg_advisory_xact_lock(hashtext('flight_claim'));
  perform public.flight_reap();
  update public.flight_worker_status set last_seen_at = now(), state = 'idle' where id;
  if exists (select 1 from public.flight_jobs where status = 'running') then return; end if;
  update public.flight_jobs set status = 'running', lease_token = gen_random_uuid(),
    lease_expires_at = now() + make_interval(secs => least(greatest(_lease_seconds, 30), 300)),
    claimed_at = now(), started_at = now(), message = 'Fetching prices…'
  where id = (select id from public.flight_jobs where status = 'queued' and not cancel_requested order by created_at limit 1 for update skip locked)
  returning * into j;
  if j.id is not null then return next j; end if;
end $$;

-- Lease-checked progress. Returns cancel flag; raises 'stale_lease' if token mismatched or job finished.
create or replace function public.flight_worker_progress(_job uuid, _lease uuid, _providers jsonb, _fares jsonb, _lease_seconds int)
returns boolean language plpgsql security definer set search_path = public as $$
declare j public.flight_jobs; f jsonb;
begin
  select * into j from public.flight_jobs where id = _job for update;
  if j.id is null or j.status <> 'running' or j.lease_token is distinct from _lease or j.lease_expires_at < now() then
    raise exception 'stale_lease' using errcode = 'P0001'; end if;
  for f in select * from jsonb_array_elements(coalesce(_fares, '[]'::jsonb)) loop
    insert into public.flight_fares(job_id, provider, origin, destination, departure, return_date, price_eur, observed_at, search_url)
    values (_job, f->>'provider', f->>'origin', f->>'destination', (f->>'departure')::date, nullif(f->>'return_date','')::date,
            (f->>'price_eur')::numeric, (f->>'observed_at')::timestamptz, f->>'search_url')
    on conflict (job_id, provider, departure, coalesce(return_date, '0001-01-01'::date)) do update
      set price_eur = excluded.price_eur, observed_at = excluded.observed_at
      where excluded.observed_at > flight_fares.observed_at;
  end loop;
  update public.flight_jobs set providers = coalesce(_providers, providers),
    lease_expires_at = now() + make_interval(secs => least(greatest(_lease_seconds, 30), 300))
  where id = _job;
  update public.flight_worker_status set last_seen_at = now(), state = 'searching' where id;
  return j.cancel_requested;
end $$;

create or replace function public.flight_worker_finish(_job uuid, _lease uuid, _outcome text, _message text)
returns void language plpgsql security definer set search_path = public as $$
declare j public.flight_jobs; n int; expected int;
begin
  select * into j from public.flight_jobs where id = _job for update;
  if j.id is null or j.status <> 'running' or j.lease_token is distinct from _lease then
    raise exception 'stale_lease' using errcode = 'P0001'; end if;
  select count(*) into n from public.flight_fares where job_id = _job;
  expected := jsonb_array_length(j.pairs) * 2;
  update public.flight_jobs set
    status = case when j.cancel_requested or _outcome = 'cancelled' then 'cancelled'::flight_job_status
                  when n = expected and _outcome = 'done' then 'complete'::flight_job_status
                  when n > 0 then 'partial'::flight_job_status else 'failed'::flight_job_status end,
    message = case when j.cancel_requested or _outcome = 'cancelled' then 'Search stopped. Completed prices are kept.'
                   when n = expected and _outcome = 'done' then 'Search finished.'
                   when _outcome = 'timeout' then 'Time limit reached; completed prices are shown.'
                   else coalesce(nullif(left(_message, 200), ''), 'Some prices could not be verified. Check provider status below.') end,
    finished_at = now(), lease_token = null, lease_expires_at = null
  where id = _job;
end $$;

revoke all on function public.flight_reap() from public, anon, authenticated;
revoke all on function public.flight_worker_heartbeat(text, text) from public, anon, authenticated;
revoke all on function public.flight_worker_claim(int) from public, anon, authenticated;
revoke all on function public.flight_worker_progress(uuid, uuid, jsonb, jsonb, int) from public, anon, authenticated;
revoke all on function public.flight_worker_finish(uuid, uuid, text, text) from public, anon, authenticated;
grant execute on function public.flight_reap() to service_role;
grant execute on function public.flight_worker_heartbeat(text, text) to service_role;
grant execute on function public.flight_worker_claim(int) to service_role;
grant execute on function public.flight_worker_progress(uuid, uuid, jsonb, jsonb, int) to service_role;
grant execute on function public.flight_worker_finish(uuid, uuid, text, text) to service_role;
