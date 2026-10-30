-- 레일 시험 실시간 값(qc_now.json)을 클라우드 결과판의 '레일 시험 > 실시간' 화면에서 보기 위한 표 (2026-10-30 김건우 결정: 결과판 주소는 하나).
-- 제어 PC2 의 serve_board.py 가 시험 중에만 2초마다 한 줄(id = 'rail')을 덮어쓴다(cellbench/rail_cloud.py). 줄이 쌓이지 않는다.
-- 시험이 끝나면 '시험 없음'을 한 번 쓰고 멈춘다. 보기는 다른 측정 표와 같이 누구나(2026-10-08 결정 B), 쓰기 정책은 두지 않는다 — PC 만 쓴다.
-- payload 는 jsonb 가 아니라 json 이다 — 파일 그대로(셀 칸 순서까지) 그린다.

create table if not exists public.rail_now (
  id          text primary key check (id = 'rail'),
  payload     json not null,                 -- qc_now.json 그대로 (시험이 끝났으면 step.key = idle)
  file_mtime  timestamptz not null,          -- 그 파일을 쓴 시각 — 화면이 '갱신 멈춤'을 이것으로 안다
  source      text not null,                 -- 올린 PC 이름
  updated_at  timestamptz not null default now()
);

alter table public.rail_now enable row level security;
drop policy if exists rail_now_read on public.rail_now;
create policy rail_now_read on public.rail_now for select to anon, authenticated using (true);
