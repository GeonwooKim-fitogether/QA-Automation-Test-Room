-- 레일 시험 QC 판정(세트 단위)을 클라우드 결과판에서 저장 · 보기 위한 표 (2026-11-02 김건우 결정: 세트를 펼쳐 QC 판정을 남긴다).
-- 열쇠(name)는 세트의 셀 구성 — 셀 시리얼을 정렬해 쉼표로 이은 것. 화면의 '세트 1' 같은 번호는 보이는 순서라 열쇠로 쓰지 않는다.
-- doc 은 serve_board.rail_disp_clean 과 같은 모양(verdict · actions · comment · by · at · criteria_version · tentative · runs · history).
-- 판정 파일(rail_run)과 따로 둔다 — 판정 파일은 시험 PC 가 쓰고 결과판은 읽기만 하므로 사람이 내린 판정을 섞지 않는다.
-- 보기는 rail_run 과 같이 누구나(2026-10-08 결정 B). 쓰기는 결과판에 로그인한 계정만 — 누가 언제 저장했는지는 트리거가
-- 로그인한 계정(auth.uid())과 시각으로 덮어써 남긴다. 화면이 보낸 by 글자는 표시용이다. 지우기 정책은 두지 않는다.

create table if not exists public.rail_disposition (
  name      text primary key check (length(name) <= 4000 and name ~ '^[0-9A-Za-z_-]{1,20}(,[0-9A-Za-z_-]{1,20})*$'),
  doc       jsonb not null check (doc->>'verdict' in ('pass', 'fail')),
  saved_by  uuid,
  saved_at  timestamptz not null default now()
);

create or replace function public.rail_disposition_stamp() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  new.saved_by := auth.uid();
  new.saved_at := now();
  return new;
end $$;

drop trigger if exists rail_disposition_stamp on public.rail_disposition;
create trigger rail_disposition_stamp before insert or update on public.rail_disposition
  for each row execute function public.rail_disposition_stamp();

alter table public.rail_disposition enable row level security;
drop policy if exists rail_disposition_read on public.rail_disposition;
create policy rail_disposition_read on public.rail_disposition for select to anon, authenticated using (true);
drop policy if exists rail_disposition_insert on public.rail_disposition;
create policy rail_disposition_insert on public.rail_disposition for insert to authenticated with check (true);
drop policy if exists rail_disposition_update on public.rail_disposition;
create policy rail_disposition_update on public.rail_disposition for update to authenticated using (true) with check (true);
