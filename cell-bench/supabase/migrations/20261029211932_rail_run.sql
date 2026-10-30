-- 레일 시험(동적 성능 시험) 판정 파일을 클라우드 결과판에서 보기 위한 표 (2026-10-30 김건우 결정: 클라우드 결과판에서 레일 결과를 본다).
-- 제어 PC2 의 serve_board.py 가 service_role 키로 data/protocol_<시각>.json 을 통째로 넣는다(upsert, cellbench/rail_cloud.py).
-- 보기는 다른 측정 표와 같이 누구나(2026-10-08 결정 B). anon · authenticated 의 쓰기 정책은 두지 않는다 — PC 만 쓴다.
-- doc 은 jsonb 가 아니라 json 이다 — jsonb 는 키 순서를 바꾸는데, 결과판은 파일의 셀 · 항목 순서대로 그린다.
-- 파일을 통째로 넣으므로 프로토콜 판(v1 · v4 …)이 바뀌어도 이 표는 바뀌지 않는다.

create table if not exists public.rail_run (
  name        text primary key check (name ~ '^protocol_[0-9]{8}-[0-9]{6}\.json$'),
  t           timestamptz,                   -- 파일 이름의 시각 (시험한 때)
  summary     json not null,                 -- 목록 한 줄 — serve_board.rail_summary 와 같은 모양
  doc         json not null,                 -- 판정 파일 그대로
  source      text not null,                 -- 올린 PC 이름
  file_mtime  timestamptz not null,          -- 올릴 때 파일을 마지막으로 쓴 시각 — 판정을 다시 쓰면 다시 올린다
  uploaded_at timestamptz not null default now()
);

alter table public.rail_run enable row level security;
drop policy if exists rail_run_read on public.rail_run;
create policy rail_run_read on public.rail_run for select to anon, authenticated using (true);
