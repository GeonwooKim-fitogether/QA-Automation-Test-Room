-- 결과판 보기를 공개로 연다 (2026-10-08 결정 B: 보기는 누구나, 명령은 로그인한 사람만).
-- 측정값 표 6개의 select 정책을 "로그인한 사용자(authenticated)" 에서 "누구나(anon 포함)" 로 바꾼다.
-- bench_command 는 그대로 둔다 — 누가 눌렀는지(requested_by 이메일)가 들어 있어 로그인한 사람만 읽고, 넣는 것도 로그인한 사람만.
do $$
declare t text;
begin
  foreach t in array array['bench','bench_state','bench_sample','bench_cycle','bench_discharge','bench_event'] loop
    execute format('drop policy if exists %I_read on public.%I', t, t);
    execute format('create policy %I_read on public.%I for select to anon, authenticated using (true)', t, t);
  end loop;
end $$;
