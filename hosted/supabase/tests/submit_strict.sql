-- Direct-RPC regression tests for flight_submit, owner refresh and worker finish.
-- Self-checking; ends with RAISE 'ALL TESTS PASSED' so every change (including the
-- temporary owner test double below) is rolled back.
do $$
declare
  owner uuid := '00000000-0000-4000-8000-0000000000aa';
  today date := (now() at time zone 'utc')::date;
  dep text := to_char(today + 40, 'YYYY-MM-DD');
  ret text := to_char(today + 70, 'YYYY-MM-DD');
  good jsonb; bad jsonb; j uuid; lease uuid; ok boolean; st text; p jsonb; n int;
begin
  -- Test double: treat only this synthetic uid as owner (rolled back at the end).
  execute $f$create or replace function public.is_flight_owner(_uid uuid default auth.uid()) returns boolean
    language sql stable security definer set search_path = public as
    'select _uid = ''00000000-0000-4000-8000-0000000000aa''::uuid'$f$;
  delete from public.flight_jobs where status in ('queued','running');

  good := jsonb_build_object('origin','HEL','destination','HAN','departure',dep,'return_date',ret,
    'trip_type','roundtrip','flex_days',3,'min_stay_nights',21);

  -- Non-owner and anonymous are rejected for submit and refresh.
  perform set_config('request.jwt.claims', json_build_object('sub', gen_random_uuid()::text, 'role','authenticated')::text, true);
  ok := false; begin perform public.flight_submit(good, null, gen_random_uuid()); exception when others then ok := sqlerrm = 'forbidden'; end;
  if not ok then raise exception 'TEST FAIL non-owner submit'; end if;
  ok := false; begin perform public.flight_owner_refresh(); exception when others then ok := sqlerrm = 'forbidden'; end;
  if not ok then raise exception 'TEST FAIL non-owner refresh'; end if;
  perform set_config('request.jwt.claims', '{"role":"anon"}', true);
  ok := false; begin perform public.flight_owner_refresh(); exception when others then ok := sqlerrm = 'forbidden'; end;
  if not ok then raise exception 'TEST FAIL anon refresh'; end if;

  perform set_config('request.jwt.claims', json_build_object('sub', owner::text, 'role','authenticated')::text, true);

  -- Malicious / malformed payloads, all via the direct RPC.
  foreach bad in array array[
    good || '{"cmd":"rm -rf /"}',                       -- unknown key
    good - 'min_stay_nights',                           -- missing key
    good || '{"flex_days":true}',                       -- boolean as number
    good || '{"min_stay_nights":"21"}',                 -- string as number
    good || '{"flex_days":2}',                          -- not 0/1/3
    good || '{"flex_days":1.0}',                        -- non-integer literal
    good || '{"min_stay_nights":0}',
    good || '{"min_stay_nights":366}',
    good || '{"origin":"hel"}',
    good || '{"origin":"HAN"}',                         -- same airports
    good || '{"destination":"HA N"}',
    good || '{"departure":"2026-02-30"}',               -- invalid date
    good || '{"departure":"20261105"}',
    good || jsonb_build_object('departure', to_char(today - 1,'YYYY-MM-DD')),
    good || jsonb_build_object('departure', to_char(today + 366,'YYYY-MM-DD')),
    good || jsonb_build_object('return_date', to_char(today + 50,'YYYY-MM-DD')),   -- stay < 21
    good || jsonb_build_object('return_date', to_char(today + 400,'YYYY-MM-DD')),
    good || '{"trip_type":"multicity"}',
    good || '{"trip_type":"oneway"}',                   -- oneway with non-null return
    good || '{"return_date":null}',                     -- roundtrip without return
    '[1,2]'::jsonb, '"HEL"'::jsonb,
    good || jsonb_build_object('origin', repeat('A', 9000))
  ] loop
    ok := false;
    begin perform public.flight_submit(bad, null, gen_random_uuid()); exception when others then ok := sqlerrm = 'invalid_search'; end;
    if not ok then raise exception 'TEST FAIL accepted %', left(bad::text, 120); end if;
  end loop;

  -- Supplied pairs that differ from the computed ones are rejected.
  ok := false;
  begin perform public.flight_submit(good, jsonb_build_array(jsonb_build_array(dep, to_char(today + 300,'YYYY-MM-DD'))), gen_random_uuid());
  exception when others then ok := sqlerrm = 'invalid_search'; end;
  if not ok then raise exception 'TEST FAIL forged pairs accepted'; end if;

  -- Valid submit: pairs computed in the database (+/-3 => 49 pairs), query normalised.
  j := public.flight_submit(good, null, gen_random_uuid());
  select pairs, status::text into p, st from public.flight_jobs where id = j;
  if jsonb_array_length(p) <> 49 or st <> 'queued' then raise exception 'TEST FAIL pairs % %', jsonb_array_length(p), st; end if;
  if p->0 <> jsonb_build_array(to_char(today + 37,'YYYY-MM-DD'), to_char(today + 67,'YYYY-MM-DD')) then
    raise exception 'TEST FAIL pair order %', p->0; end if;
  -- Matching supplied pairs accepted (idempotent key returns same job).
  delete from public.flight_jobs where id = j;
  j := public.flight_submit(good, p, '00000000-0000-4000-8000-0000000000bb');
  if public.flight_submit(good, p, '00000000-0000-4000-8000-0000000000bb') <> j then raise exception 'TEST FAIL idempotency'; end if;
  delete from public.flight_jobs where id = j;

  -- One-way: up to 7 departures, return null.
  j := public.flight_submit(good || '{"trip_type":"oneway","return_date":null}', null, gen_random_uuid());
  select pairs into p from public.flight_jobs where id = j;
  if jsonb_array_length(p) <> 7 or jsonb_typeof(p->0->1) <> 'null' then raise exception 'TEST FAIL oneway %', p; end if;

  -- Owner refresh reaps a running job whose worker vanished.
  update public.flight_jobs set status = 'running', lease_token = gen_random_uuid(), started_at = now() - interval '5 minutes',
    lease_expires_at = now() - interval '1 second' where id = j returning lease_token into lease;
  perform public.flight_owner_refresh();
  select status::text into st from public.flight_jobs where id = j;
  if st <> 'failed' then raise exception 'TEST FAIL owner refresh did not reap: %', st; end if;

  -- finish/progress reject an expired lease even with the right token...
  update public.flight_jobs set status = 'running', finished_at = null, lease_token = lease,
    lease_expires_at = now() - interval '1 second' where id = j;
  ok := false; begin perform public.flight_worker_finish(j, lease, 'done', ''); exception when others then ok := sqlerrm = 'stale_lease'; end;
  if not ok then raise exception 'TEST FAIL finish with expired lease'; end if;
  ok := false; begin perform public.flight_worker_progress(j, lease, null, '[]', 60); exception when others then ok := sqlerrm = 'stale_lease'; end;
  if not ok then raise exception 'TEST FAIL progress with expired lease'; end if;
  -- ...and beyond the time budget.
  update public.flight_jobs set lease_expires_at = now() + interval '1 minute', started_at = now() - interval '36 minutes' where id = j;
  ok := false; begin perform public.flight_worker_finish(j, lease, 'done', ''); exception when others then ok := sqlerrm = 'stale_lease'; end;
  if not ok then raise exception 'TEST FAIL finish beyond budget'; end if;
  -- Valid lease still finishes.
  update public.flight_jobs set started_at = now() - interval '1 minute' where id = j;
  perform public.flight_worker_finish(j, lease, 'done', '');
  select status::text into st from public.flight_jobs where id = j;
  if st <> 'failed' then raise exception 'TEST FAIL finish no fares => %', st; end if;

  raise exception 'ALL TESTS PASSED';
end $$;

