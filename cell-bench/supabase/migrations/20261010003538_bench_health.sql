-- 신호등 (2026-10-10) — 시험대 PC 의 감시자가 1분마다 올리는 9개 차선 판정. 클라우드 결과판 관제 탭 맨 위의 '시험대 띠 · 차선 9개'가 읽는다.
-- Supabase 프로젝트 cell-bench(rmlxafxegabeqqtuvyyb) 전용. 사람 승인 뒤 별도 단계로 적용한다(이 파일만으로는 아무것도 바뀌지 않는다).
--
-- 무엇이 들어오나
--   cellbench/health.py 의 compute() 결과(data/health.json 과 같은 내용)를 payload 에 그대로, 전체 불과 띠 한 줄을 light · reason 에 꺼내 둔다.
--   TestPC 의 감시자(supervise.py)가 service_role 키로 시험대마다 한 줄을 덮어쓴다(cellbench/cloud.py Cloud.health — bench_state 와 같은 꼴).
--   결과판은 updated_at 이 5분(payload.stale_after_s) 넘게 묵으면 스스로 전체를 '미확인'으로 그린다 — PC 가 죽어도 불이 켜진다.
--
-- 적용 전에는 감시자의 전송이 실패(표 없음)하고 Cloud 가 다시 해 보다 버린다. 시험은 멈추지 않는다. 결과판의 띠는 '신호등 자료 없음'.
--
-- 적용 뒤 확인
--   select bench_id, updated_at, light, reason from public.bench_health;     -- 1분마다 updated_at 이 바뀌나
--
-- 되돌리기
--   drop table if exists public.bench_health;

create table if not exists public.bench_health (
  bench_id    text primary key references public.bench(id),
  updated_at  timestamptz not null default now(),
  light       text not null check (light in ('green', 'yellow', 'red', 'unknown')),   -- 정상 · 준비 · 조치 · 미확인
  reason      text,                                                                  -- 띠 한 줄 ('준비 2건 · 조치 0건 — ...')
  payload     jsonb not null                                                         -- 차선 9개 · 세트 · 심박 · 감시자 상태
);

-- 읽기는 누구나(측정값 표와 같은 결 — 20261008111235_public_read), 쓰기는 service_role(TestPC) 만 — RLS 가 막고 service_role 은 RLS 를 거치지 않는다
alter table public.bench_health enable row level security;
drop policy if exists bench_health_read on public.bench_health;
create policy bench_health_read on public.bench_health for select to anon, authenticated using (true);
