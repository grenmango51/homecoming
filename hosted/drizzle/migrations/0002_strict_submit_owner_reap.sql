-- Strict search validation inside PostgreSQL (mirrors src/local_search.validate_search).
create or replace function public.flight_parse_date(_v jsonb)
returns date language plpgsql immutable set search_path = public as $$
declare s text; d date;
begin
  if _v is null or jsonb_typeof(_v) <> 'string' then return null; end if;
  s := _v #>> '{}';
  if s !~ '^\d{4}-\d{2}-\d{2}$' then return null; end if;
  begin d := to_date(s, 'YYYY-MM-DD'); exception when others then return null; end;
  if to_char(d, 'YYYY-MM-DD') <> s then return null; end if;
  return d;
end $$;

create or replace function public.flight_validate_search(_q jsonb)
returns jsonb language plpgsql stable set search_path = public as $$
declare
  today date := (now() at time zone 'utc')::date;
  horizon date := today + 365;
  keys text[]; o text; d text; tt text; radius int; minimum int; dep date; ret date;
  pairs jsonb := '[]'::jsonb; dd date; rr date; i int; k int;
begin
  if _q is null or jsonb_typeof(_q) <> 'object' or octet_length(_q::text) > 8192 then
    raise exception 'invalid_search' using errcode = '22023'; end if;
  select array_agg(x order by x) into keys from jsonb_object_keys(_q) x;
  if keys is distinct from array['departure','destination','flex_days','min_stay_nights','origin','return_date','trip_type'] then
    raise exception 'invalid_search' using errcode = '22023'; end if;
  if jsonb_typeof(_q->'origin') <> 'string' or jsonb_typeof(_q->'destination') <> 'string'
     or jsonb_typeof(_q->'trip_type') <> 'string'
     or jsonb_typeof(_q->'flex_days') <> 'number' or jsonb_typeof(_q->'min_stay_nights') <> 'number' then
    raise exception 'invalid_search' using errcode = '22023'; end if;
  o := _q->>'origin'; d := _q->>'destination'; tt := _q->>'trip_type';
  if o !~ '^[A-Z]{3}$' or d !~ '^[A-Z]{3}$' or o = d or tt not in ('roundtrip','oneway') then
    raise exception 'invalid_search' using errcode = '22023'; end if;
  if (_q->>'flex_days') !~ '^[0-9]$' or (_q->>'min_stay_nights') !~ '^[0-9]{1,3}$' then
    raise exception 'invalid_search' using errcode = '22023'; end if;
  radius := (_q->>'flex_days')::int; minimum := (_q->>'min_stay_nights')::int;
  if radius not in (0,1,3) or minimum < 1 or minimum > 365 then
    raise exception 'invalid_search' using errcode = '22023'; end if;
  dep := public.flight_parse_date(_q->'departure');
  if dep is null or dep < today or dep > horizon then
    raise exception 'invalid_search' using errcode = '22023'; end if;
  if tt = 'oneway' then
    if jsonb_typeof(_q->'return_date') <> 'null' then raise exception 'invalid_search' using errcode = '22023'; end if;
    ret := null;
  else
    ret := public.flight_parse_date(_q->'return_date');
    if ret is null or ret <= dep or ret - dep < minimum or ret > horizon then
      raise exception 'invalid_search' using errcode = '22023'; end if;
  end if;
  for i in -radius..radius loop
    dd := dep + i;
    continue when dd < today or dd > horizon;
    if ret is null then
      pairs := pairs || jsonb_build_array(jsonb_build_array(to_char(dd,'YYYY-MM-DD'), null));
    else
      for k in -radius..radius loop
        rr := ret + k;
        if rr - dd >= minimum and rr <= horizon then
          pairs := pairs || jsonb_build_array(jsonb_build_array(to_char(dd,'YYYY-MM-DD'), to_char(rr,'YYYY-MM-DD')));
        end if;
      end loop;
    end if;
  end loop;
  return jsonb_build_object(
    'query', jsonb_build_object('origin', o, 'destination', d, 'departure', to_char(dep,'YYYY-MM-DD'),
      'return_date', case when ret is null then 'null'::jsonb else to_jsonb(to_char(ret,'YYYY-MM-DD')) end,
      'trip_type', tt, 'flex_days', radius, 'min_stay_nights', minimum),
    'pairs', pairs);
end $$;
revoke all on function public.flight_parse_date(jsonb) from public, anon, authenticated;
revoke all on function public.flight_validate_search(jsonb) from public, anon, authenticated;

-- Submit: validates and computes pairs itself. Supplied pairs must equal the computed ones (or be null).
create or replace function public.flight_submit(_query jsonb, _pairs jsonb, _idem uuid)
returns uuid language plpgsql security definer set search_path = public as $$
declare uid uuid := auth.uid(); existing uuid; new_id uuid; v jsonb;
begin
  if uid is null or not public.is_flight_owner(uid) then raise exception 'forbidden' using errcode = '42501'; end if;
  if _idem is null then raise exception 'invalid_search' using errcode = '22023'; end if;
  v := public.flight_validate_search(_query);
  if _pairs is not null and _pairs is distinct from v->'pairs' then
    raise exception 'invalid_search' using errcode = '22023'; end if;
  perform pg_advisory_xact_lock(hashtext('flight_submit'));
  select id into existing from public.flight_jobs where owner_id = uid and idempotency_key = _idem;
  if existing is not null then return existing; end if;
  perform public.flight_reap();
  if (select count(*) from public.flight_jobs where owner_id = uid and created_at > now() - interval '1 hour') >= 10 then
    raise exception 'rate_limited' using errcode = 'P0001'; end if;
  if (select count(*) from public.flight_jobs where status in ('queued','running')) >= 1 then
    raise exception 'busy' using errcode = 'P0001'; end if;
  insert into public.flight_jobs(owner_id, idempotency_key, query, pairs, providers)
  values (uid, _idem, v->'query', v->'pairs', jsonb_build_object(
    'google_flights', jsonb_build_object('verified',0,'expected',jsonb_array_length(v->'pairs'),'statuses','{}'::jsonb),
    'skyscanner', jsonb_build_object('verified',0,'expected',jsonb_array_length(v->'pairs'),'statuses','{}'::jsonb)))
  returning id into new_id;
  return new_id;
end $$;
revoke all on function public.flight_submit(jsonb, jsonb, uuid) from public, anon;
grant execute on function public.flight_submit(jsonb, jsonb, uuid) to authenticated;

-- Owner-checked reap used by session/job polling so a lost worker cannot leave a job "running" forever.
create or replace function public.flight_owner_refresh()
returns void language plpgsql security definer set search_path = public as $$
begin
  if auth.uid() is null or not public.is_flight_owner(auth.uid()) then
    raise exception 'forbidden' using errcode = '42501'; end if;
  perform public.flight_reap();
end $$;
revoke all on function public.flight_owner_refresh() from public, anon;
grant execute on function public.flight_owner_refresh() to authenticated;

-- Progress/finish reject expired leases and jobs beyond the 30-minute budget (+ cleanup margin).
create or replace function public.flight_worker_progress(_job uuid, _lease uuid, _providers jsonb, _fares jsonb, _lease_seconds integer)
returns boolean language plpgsql security definer set search_path = public as $$
declare j public.flight_jobs; f jsonb;
begin
  select * into j from public.flight_jobs where id = _job for update;
  if j.id is null or j.status <> 'running' or j.lease_token is distinct from _lease
     or j.lease_expires_at is null or j.lease_expires_at < now()
     or j.started_at < now() - interval '35 minutes' then
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
    lease_expires_at = least(now() + make_interval(secs => least(greatest(_lease_seconds, 30), 300)), started_at + interval '35 minutes')
  where id = _job;
  update public.flight_worker_status set last_seen_at = now(), state = 'searching' where id;
  return j.cancel_requested;
end $$;

create or replace function public.flight_worker_finish(_job uuid, _lease uuid, _outcome text, _message text)
returns void language plpgsql security definer set search_path = public as $$
declare j public.flight_jobs; n int; expected int;
begin
  select * into j from public.flight_jobs where id = _job for update;
  if j.id is null or j.status <> 'running' or j.lease_token is distinct from _lease
     or j.lease_expires_at is null or j.lease_expires_at < now()
     or j.started_at < now() - interval '35 minutes' then
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
revoke all on function public.flight_worker_progress(uuid, uuid, jsonb, jsonb, int) from public, anon, authenticated;
revoke all on function public.flight_worker_finish(uuid, uuid, text, text) from public, anon, authenticated;
grant execute on function public.flight_worker_progress(uuid, uuid, jsonb, jsonb, int) to service_role;
grant execute on function public.flight_worker_finish(uuid, uuid, text, text) to service_role;
