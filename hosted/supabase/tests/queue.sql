-- Self-checking queue test. Ends with RAISE 'ALL TESTS PASSED' so everything is rolled back.
do $$
declare j1 uuid; c1 int; c2 int; lease uuid; flag boolean; ok boolean := false; st text;
begin
  perform set_config('request.jwt.claims', json_build_object('sub', gen_random_uuid()::text, 'role','authenticated')::text, true);
  begin perform public.flight_submit('{}'::jsonb, '[["2026-11-05","2026-12-09"]]'::jsonb, gen_random_uuid());
  exception when others then ok := sqlerrm = 'forbidden'; end;
  if not ok then raise exception 'TEST FAIL non-owner submit allowed'; end if;
  ok := false;
  begin perform public.flight_cancel(gen_random_uuid()); exception when others then ok := sqlerrm = 'forbidden'; end;
  if not ok then raise exception 'TEST FAIL non-owner cancel'; end if;
  delete from public.flight_jobs where status in ('queued','running');  -- rolled back

  insert into public.flight_jobs(owner_id, idempotency_key, query, pairs)
  values (gen_random_uuid(), gen_random_uuid(), '{"origin":"HEL"}', '[["2026-11-05","2026-12-09"]]') returning id into j1;
  select count(*) into c1 from public.flight_worker_claim(60);
  select count(*) into c2 from public.flight_worker_claim(60);
  if c1 <> 1 or c2 <> 0 then raise exception 'TEST FAIL claim % %', c1, c2; end if;
  select lease_token into lease from public.flight_jobs where id = j1;
  ok := false;
  begin perform public.flight_worker_progress(j1, gen_random_uuid(), null, '[]', 60); exception when others then ok := sqlerrm = 'stale_lease'; end;
  if not ok then raise exception 'TEST FAIL wrong lease accepted'; end if;
  perform public.flight_worker_progress(j1, lease, null,
    '[{"provider":"skyscanner","origin":"HEL","destination":"HAN","departure":"2026-11-05","return_date":"2026-12-09","price_eur":600,"observed_at":"2026-10-04T10:00:00Z","search_url":"https://www.skyscanner.fi/x"}]', 60);
  update public.flight_jobs set cancel_requested = true where id = j1;
  flag := public.flight_worker_progress(j1, lease, null, '[]', 60);
  if not flag then raise exception 'TEST FAIL cancel flag'; end if;
  perform public.flight_worker_finish(j1, lease, 'cancelled', '');
  select status into st from public.flight_jobs where id = j1;
  if st <> 'cancelled' then raise exception 'TEST FAIL status %', st; end if;
  ok := false;
  begin perform public.flight_worker_finish(j1, lease, 'done', ''); exception when others then ok := sqlerrm = 'stale_lease'; end;
  if not ok then raise exception 'TEST FAIL replay finish'; end if;
  insert into public.flight_jobs(owner_id, idempotency_key, query, pairs)
  values (gen_random_uuid(), gen_random_uuid(), '{}', '[["2026-11-05",null]]') returning id into j1;
  perform public.flight_worker_claim(60);
  update public.flight_jobs set lease_expires_at = now() - interval '1 second' where id = j1;
  select count(*) into c1 from public.flight_worker_claim(60);
  select status into st from public.flight_jobs where id = j1;
  if st <> 'failed' or c1 <> 0 then raise exception 'TEST FAIL reap % %', st, c1; end if;
  raise exception 'ALL TESTS PASSED';
end $$;

