-- 셀 배터리 IC 로 잰 사이클별 방전 용량 — 클라우드 결과판 '추이 분석' 의 주 지표(Q70 · FullCap · 평균 방전 전류)를 위한 표.
-- 사용시간은 부하 전류가 바뀌면 용량과 무관하게 달라져(2026-11 실측: 전류 −4~5 %, 용량 −2 %, 사용시간 +1~2 %) 열화 지표로 쓸 수 없다.
-- 한 행 = 세트 · 사이클 · 셀 하나 = TestPC 의 data/(set{k}/)fg_cycles.csv 한 줄 (계산: cellbench/ftglog.py discharge()).
-- 결과판 서버(serve_board.py 의 FgFollow)가 service_role 키로 넣는다(upsert). 엔진은 이 표를 모른다 — 엔진 재시작이 필요 없다.
-- 보기는 다른 측정 표와 같이 누구나(2026-10-08 결정 B). anon · authenticated 의 쓰기 정책은 두지 않는다 — PC 만 쓴다.
-- 되돌리기: drop table public.bench_fg_cycle;  (다른 표 · 엔진과 엮이지 않아 지워도 시험에 영향이 없다. 결과판은 표가 없으면 '클라우드에 용량 표가 아직 없다' 로 보인다)

create table if not exists public.bench_fg_cycle (
  bench_id     text not null references public.bench(id),
  set_id       smallint not null default 1 check (set_id between 1 and 5),
  cycle        integer not null,
  serial       integer not null,
  n_rec        integer,                 -- 그 FTG 의 IC 상태 기록 수
  mean_ma      numeric(7,2),            -- 방전 구간 평균 전류(mA, 크기)
  dis_mah      numeric(7,1),            -- 방전 구간 전하량(mAh)
  q70_mah      numeric(7,1),            -- 잔량 100 → 30 % 폭(70 %p)으로 환산한 전하량(mAh)
  fullcap_mah  numeric(7,1),            -- IC 학습 용량(잔량 40~90 % 구간의 중앙값, mAh)
  soc_hi       numeric(5,1),            -- 방전 구간 최고 잔량(%)
  soc_lo       numeric(5,1),            -- 방전 구간 최저 잔량(%)
  mean_v       numeric(6,4),            -- 방전 구간 평균 전압(V)
  dis_h        numeric(6,3),            -- 방전 구간 길이(h)
  updated_at   timestamptz not null default now(),
  primary key (bench_id, set_id, cycle, serial)
);

alter table public.bench_fg_cycle enable row level security;
drop policy if exists bench_fg_cycle_read on public.bench_fg_cycle;
create policy bench_fg_cycle_read on public.bench_fg_cycle for select to anon, authenticated using (true);
