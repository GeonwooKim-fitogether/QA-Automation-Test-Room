-- 셀 시험대 클라우드 결과판 — Supabase 프로젝트 cell-bench(rmlxafxegabeqqtuvyyb) 전용 표.
-- TestPC 가 service_role 키로 쓰고(upsert/insert), 결과판은 로그인한 사용자가 읽는다.
-- 표본은 1분마다 1행(하루 1,440행), 30일 지난 표본은 cron 이 지운다. 상태는 1행을 계속 덮어쓴다.

create table if not exists public.bench (
  id          text primary key,                    -- 시험대 식별자 (예: 'hq-bench-1')
  name        text not null,
  cells       integer not null default 24,
  created_at  timestamptz not null default now()
);

create table if not exists public.bench_state (                -- 결과판 '운영' 장 · now.json 그대로
  bench_id    text primary key references public.bench(id),
  updated_at  timestamptz not null default now(),
  cycle       integer,
  phase       text,
  payload     jsonb not null                                     -- now.json 전체 (셀 24칸 포함)
);

create table if not exists public.bench_sample (               -- 1분마다 1행
  bench_id    text not null references public.bench(id),
  t           timestamptz not null,
  cycle       integer,
  phase       text,
  plug_on     boolean,
  watts       numeric(7,2),
  wh          numeric(9,3),
  batt_min    smallint, batt_avg numeric(5,1), batt_max smallint,
  cells_alive smallint,
  primary key (bench_id, t)
);

create table if not exists public.bench_cycle (                -- cycles.csv 1줄 = 1행
  bench_id    text not null references public.bench(id),
  cycle       integer not null,
  row         jsonb not null,                                    -- cycles.csv 의 열 그대로
  updated_at  timestamptz not null default now(),
  primary key (bench_id, cycle)
);

create table if not exists public.bench_discharge (            -- 셀별 추정 작동시간
  bench_id    text not null references public.bench(id),
  cycle       integer not null,
  serial      integer not null,
  start_pct   smallint, end_pct smallint, hours numeric(6,3), pct_per_h numeric(6,2), est_runtime_h numeric(6,2),
  primary key (bench_id, cycle, serial)
);

create table if not exists public.bench_event (                -- events.csv
  id          bigint generated always as identity primary key,
  bench_id    text not null references public.bench(id),
  t           timestamptz not null,
  cycle       integer, phase text, kind text not null, serial text, detail text
);

create table if not exists public.bench_command (              -- 원격 명령: 결과판이 넣고 TestPC 가 집어 간다
  id          bigint generated always as identity primary key,
  bench_id    text not null references public.bench(id),
  cmd         text not null check (cmd in ('plug_on','plug_off','stop_safe')),
  requested_by text,
  requested_at timestamptz not null default now(),
  taken_at    timestamptz,
  result      text
);

-- 읽기는 로그인한 사용자만, 쓰기는 service_role 만 (TestPC). 결과판의 명령 넣기는 로그인 사용자 insert 허용.
alter table public.bench           enable row level security;
alter table public.bench_state     enable row level security;
alter table public.bench_sample    enable row level security;
alter table public.bench_cycle     enable row level security;
alter table public.bench_discharge enable row level security;
alter table public.bench_event     enable row level security;
alter table public.bench_command   enable row level security;

do $$ declare t text; begin
  foreach t in array array['bench','bench_state','bench_sample','bench_cycle','bench_discharge','bench_event','bench_command'] loop
    execute format('create policy %I_read on public.%I for select to authenticated using (true)', t, t);
  end loop;
end $$;
create policy bench_command_insert on public.bench_command for insert to authenticated with check (requested_by = auth.email());

insert into public.bench (id, name, cells) values ('hq-bench-1', '본사 셀 시험대 1호 (Dock DKP2 · 24셀)', 24) on conflict do nothing;
