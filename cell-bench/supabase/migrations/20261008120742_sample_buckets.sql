-- 결과판 "최근 5사이클 · 전체" 범위용 묶음 뷰 (2026-10-08).
-- 1분 표본을 10분·1시간 단위로 묶어 평균·최저·최고를 미리 내준다 — 넓은 범위를 골라도 점 수가 몇백 개로 유지되게.
-- security_invoker: 뷰를 읽는 사람의 권한으로 bench_sample 의 RLS 를 적용한다 (public_read 정책 → 누구나 읽기).
create or replace function public.bench_sample_bucket(bucket_s int)
returns table (bench_id text, t timestamptz, cycle int, phase text, plug_on boolean, watts numeric, wh numeric,
               batt_min int, batt_avg numeric, batt_max int, cells_alive int, n int)
language sql stable security invoker as $$
  select bench_id,
         to_timestamp(floor(extract(epoch from t) / bucket_s) * bucket_s) as t,
         min(cycle) as cycle,
         mode() within group (order by phase) as phase,
         bool_or(plug_on) as plug_on,
         round(avg(watts)::numeric, 2) as watts,
         max(wh) as wh,
         min(batt_min)::int as batt_min,
         round(avg(batt_avg)::numeric, 1) as batt_avg,
         max(batt_max)::int as batt_max,
         min(cells_alive)::int as cells_alive,
         count(*)::int as n
  from public.bench_sample
  group by bench_id, 2
$$;
create or replace view public.bench_sample_10m with (security_invoker = true) as select * from public.bench_sample_bucket(600);
create or replace view public.bench_sample_1h  with (security_invoker = true) as select * from public.bench_sample_bucket(3600);
grant select on public.bench_sample_10m, public.bench_sample_1h to anon, authenticated;
grant execute on function public.bench_sample_bucket(int) to anon, authenticated;
