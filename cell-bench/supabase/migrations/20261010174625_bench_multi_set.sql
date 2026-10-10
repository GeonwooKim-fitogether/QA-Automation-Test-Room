-- 다중 세트 (2026-10-11) — 측정 표에 세트 칸(set_id)을 더하고, 세트마다 행이 갈리도록 기본키를 바꾼다. 명세: cell-bench/docs/design/multi-set-plan.md 5-3.
-- Supabase 프로젝트 cell-bench(rmlxafxegabeqqtuvyyb) 전용 — 이것이 운영 환경이다(비운영 환경이 따로 없다).
--
-- 상태: **적용하지 않았다 · 미검증.** 이 SQL 은 auth.email() · 역할 · pg_cron · pg_net · vault 가 있어야 돌아서 맨 Postgres 로는 시험할 수 없고,
--       이 PC 에는 Supabase 로컬 스택도 브랜치 DB 도 없다(브랜치 DB 는 비용이 드는 일이라 만들지 않았다). 적용은 사용자 결정 U1 이다.
--
-- 적용 순서 (명세 5-4)
--   1. 이 브랜치의 엔진 · 감시자 · 결과판 서버가 TestPC 에서 단일 모드로 한 사이클을 끝까지 돈 뒤
--   2. 첫 줄 확인 — 도는 엔진이 판 2 를 아는가 (값이 2 여야 한다. 없으면 옛 엔진이라 적용하지 않는다):
--        select payload->>'cloud_schema' from public.bench_state where bench_id = 'hq-bench-1';
--   3. 사용자 승인 → 데이터베이스 쓰기 규칙의 열쇠 절차로 적용 (적용 도구가 트랜잭션을 감싸므로 begin/commit 은 넣지 않았다)
--   4. 적용 뒤: 엔진의 다음 upsert 가 42P10 으로 한 번 실패 → 재탐침 → 판 2 모양으로 재시도. bench_state 에 set_id = 1 한 줄인지 본다
--        select bench_id, set_id, updated_at from public.bench_state;
--        select public.bench_schema_version();      -- 2
--
-- 기존 행은 모두 set_id = 1 (세트 1). 상수 기본값 칸 추가는 Postgres 11 이상에서 기존 행을 고쳐 쓰지 않는다.
-- 기본키를 바꾸는 동안 표가 잠깐 잠긴다 — TestPC 의 전송은 기다리거나 재시도한다.
--
-- 되돌리기 — 세트 2 이상의 행을 지워야 옛 기본키로 돌아갈 수 있다(지운 행은 되살릴 수 없다).
--   각 표 delete … where set_id <> 1 → 기본키 원래대로(bench_state (bench_id) · bench_sample (bench_id, t) · bench_cycle (bench_id, cycle) ·
--   bench_discharge (bench_id, cycle, serial)) → 명령 check 원래대로 → 묶음 뷰 · 함수를 지우고 20261008120742_sample_buckets.sql 재적용 →
--   20261009235950_heartbeat_watch.sql 의 함수 재적용 → drop function public.bench_schema_version() → 각 표 drop column set_id.
--   되돌린 뒤 엔진의 판 2 요청은 PGRST204 로 실패해 재탐침으로 판 1 이 된다.

-- 1) 세트 칸
alter table public.bench_state     add column if not exists set_id smallint not null default 1 check (set_id between 1 and 5);
alter table public.bench_sample    add column if not exists set_id smallint not null default 1 check (set_id between 1 and 5);
alter table public.bench_cycle     add column if not exists set_id smallint not null default 1 check (set_id between 1 and 5);
alter table public.bench_discharge add column if not exists set_id smallint not null default 1 check (set_id between 1 and 5);
alter table public.bench_event     add column if not exists set_id smallint not null default 1 check (set_id between 1 and 5);
alter table public.bench_command   add column if not exists set_id smallint check (set_id between 1 and 5);  -- null = 세트 지정 없음 (전체 정지 · 옛 결과판)

-- 2) 기본키 — 세트마다 '지금 상태' 한 줄, 같은 초의 표본 · 같은 번호의 사이클이 세트마다 따로
alter table public.bench_state     drop constraint bench_state_pkey,     add primary key (bench_id, set_id);
alter table public.bench_sample    drop constraint bench_sample_pkey,    add primary key (bench_id, set_id, t);
alter table public.bench_cycle     drop constraint bench_cycle_pkey,     add primary key (bench_id, set_id, cycle);
alter table public.bench_discharge drop constraint bench_discharge_pkey, add primary key (bench_id, set_id, cycle, serial);
create index if not exists bench_event_set_t on public.bench_event (bench_id, set_id, t desc);

-- 3) 명령 종류에 '세트 다시 켜기' — 인라인 check 의 기본 이름(bench_command_cmd_check)을 가정한다. 적용 전에 실제 이름을 확인한다:
--      select conname from pg_constraint where conrelid = 'public.bench_command'::regclass and contype = 'c';
--    이름이 다르면 아래 drop 이 아무것도 지우지 않아 옛 check 가 남고 resume_set 이 거부된다
alter table public.bench_command drop constraint if exists bench_command_cmd_check;
alter table public.bench_command add constraint bench_command_cmd_check check (cmd in ('plug_on', 'plug_off', 'stop_safe', 'resume_set'));

-- 4) 표본 묶음 — 반환 칸이 바뀌어 create or replace 로는 안 된다. 뷰 → 함수 순으로 지우고 다시 만든다
drop view if exists public.bench_sample_10m;
drop view if exists public.bench_sample_1h;
drop function if exists public.bench_sample_bucket(int);
create function public.bench_sample_bucket(bucket_s int)
returns table (bench_id text, set_id smallint, t timestamptz, cycle int, phase text, plug_on boolean, watts numeric,
               wh numeric, batt_min int, batt_avg numeric, batt_max int, cells_alive int, n int)
language sql stable security invoker as $$
  select bench_id, set_id, to_timestamp(floor(extract(epoch from t) / bucket_s) * bucket_s),
         min(cycle), mode() within group (order by phase), bool_or(plug_on), round(avg(watts)::numeric, 2), max(wh),
         min(batt_min)::int, round(avg(batt_avg)::numeric, 1), max(batt_max)::int, min(cells_alive)::int, count(*)::int
  from public.bench_sample
  group by bench_id, set_id, 3
$$;
create view public.bench_sample_10m with (security_invoker = true) as select * from public.bench_sample_bucket(600);
create view public.bench_sample_1h  with (security_invoker = true) as select * from public.bench_sample_bucket(3600);
grant select on public.bench_sample_10m, public.bench_sample_1h to anon, authenticated;
grant execute on function public.bench_sample_bucket(int) to anon, authenticated;

-- 5) 클라우드 심박 — 세트마다 한 줄이 생기므로 시험대 단위로 묶어 본다. 바뀐 곳은 고리의 머리와 '일부러 끝남' 조건(10분 안에 갱신된 세트만)뿐이다.
--    그대로 두면 세트 줄마다 판정해, 운전 목록에서 빠진 세트의 멈춘 옛 줄 하나가 1분마다 '소식 없음'과 '복구'를 번갈아 낸다(bench_watch 기본키가 bench_id 하나)
create or replace function public.bench_heartbeat_check()
returns void language plpgsql security definer set search_path = '' as $$
declare
  stale    constant interval := interval '5 minutes';
  renotify constant interval := interval '60 minutes';
  hook text; r record; w public.bench_watch%rowtype; mins int; last_kst text; msg text;
begin
  begin
    select ds.decrypted_secret into hook from vault.decrypted_secrets ds where ds.name = 'cell_bench_slack_webhook' limit 1;
  exception when others then hook := null;
  end;
  for r in
    select b.bench_id, max(b.updated_at) as updated_at,
           (array_agg(b.cycle order by b.set_id))[1] as cycle,
           (array_agg(b.phase order by b.set_id))[1] as phase,
           bool_and(coalesce(b.phase, '') in ('DONE', 'STOPPED'))
             filter (where b.updated_at >= b.last_at - interval '10 minutes') as ended
      from (select s.*, max(s.updated_at) over (partition by s.bench_id) as last_at from public.bench_state s) b
     group by b.bench_id
  loop
    insert into public.bench_watch (bench_id) values (r.bench_id) on conflict (bench_id) do nothing;
    select * into w from public.bench_watch where bench_id = r.bench_id for update;
    msg := null;
    mins := floor(extract(epoch from (now() - r.updated_at)) / 60)::int;
    last_kst := to_char(r.updated_at at time zone 'Asia/Seoul', 'MM-DD HH24:MI') || ' KST';
    if r.updated_at < now() - stale and not coalesce(r.ended, false) then
      if w.state <> 'lost' then
        update public.bench_watch set state = 'lost', lost_since = r.updated_at, last_notified_at = now(), updated_at = now()
         where bench_id = r.bench_id;
        insert into public.bench_event (bench_id, t, cycle, phase, kind, serial, detail)
        values (r.bench_id, now(), r.cycle, r.phase, 'heartbeat_lost', '-',
                format('시험대 PC 소식 없음 — 마지막 갱신 %s (%s분 전)', last_kst, mins));
        msg := format('[셀 시험대 %s] 미확인 · 클라우드 감시 · 시험대 PC 에서 %s분째 소식 없음 (마지막 갱신 %s) — PC 전원·인터넷·시험 프로그램 확인',
                      r.bench_id, mins, last_kst);
      elsif w.last_notified_at is null or w.last_notified_at <= now() - renotify then
        update public.bench_watch set last_notified_at = now(), updated_at = now() where bench_id = r.bench_id;
        msg := format('[셀 시험대 %s] 미확인 (계속 · %s분째 · 60분마다) · 클라우드 감시 · 시험대 PC 소식 없음 (마지막 갱신 %s)',
                      r.bench_id, mins, last_kst);
      end if;
    elsif w.state = 'lost' then
      mins := floor(extract(epoch from (r.updated_at - coalesce(w.lost_since, r.updated_at))) / 60)::int;
      update public.bench_watch set state = 'ok', lost_since = null, updated_at = now() where bench_id = r.bench_id;
      insert into public.bench_event (bench_id, t, cycle, phase, kind, serial, detail)
      values (r.bench_id, now(), r.cycle, r.phase, 'heartbeat_back', '-', format('시험대 PC 소식이 다시 들어옴 — %s분 만에', mins));
      msg := format('[셀 시험대 %s] 복구 · 클라우드 감시 · 시험대 PC 소식이 다시 들어옴 (%s분 만에)', r.bench_id, mins);
    end if;
    if msg is not null and hook is not null then
      begin
        perform net.http_post(url := hook, body := jsonb_build_object('text', msg),
                              headers := '{"Content-Type": "application/json"}'::jsonb, timeout_milliseconds := 5000);
      exception when others then raise warning 'cell-bench heartbeat: Slack 전송 실패 — %', sqlerrm;
      end;
    end if;
  end loop;
end;
$$;
revoke execute on function public.bench_heartbeat_check() from public, anon, authenticated;

-- 6) 스키마 판 — 엔진 · 감시자 · 클라우드 결과판의 탐침이 읽는다
create or replace function public.bench_schema_version() returns int language sql immutable as $$ select 2 $$;
grant execute on function public.bench_schema_version() to anon, authenticated, service_role;
notify pgrst, 'reload schema';
