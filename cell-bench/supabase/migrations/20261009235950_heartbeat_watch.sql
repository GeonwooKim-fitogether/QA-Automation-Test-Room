-- 클라우드 심박 감시 (FMEA P3 · 2026-10-10) — 시험대 PC 가 통째로 죽거나 인터넷이 끊겨 아무 말도 못 할 때를 클라우드 쪽에서 잡는다.
-- Supabase 프로젝트 cell-bench(rmlxafxegabeqqtuvyyb) 전용. TestPC 가 아니라 클라우드에 적용되며, 사람 승인 뒤 별도 단계로 적용한다.
--
-- 무엇을 하나
--   pg_cron 이 1분마다 public.bench_heartbeat_check() 를 부른다. 시험대마다 bench_state.updated_at(PC 가 20초마다 올리는 '지금 상태')이
--   5분 넘게 묵었으면 그 시험대를 미확인(lost)으로 바꾸고, bench_event 에 heartbeat_lost 를 남기고, Slack 으로 알린다.
--   미확인이 이어지면 60분마다 다시 알린다. 다시 갱신되면 정상(ok)으로 돌리고 heartbeat_back 을 남기고 "복구"를 알린다.
--   엔진이 일부러 끝난 상태(phase 가 DONE · STOPPED)면 갱신이 멈추는 것이 정상이라 울리지 않는다.
--   Slack 웹훅은 Supabase Vault 의 비밀 cell_bench_slack_webhook 에서 읽는다. 비밀(또는 Vault)이 없으면 기록만 하고 전송은 건너뛴다(오류 없음).
--   PC 쪽 감시자(supervise.py)는 PC 가 살아 있어야 알릴 수 있다 — 이 파일은 PC 가 침묵할 때의 두 번째 층이다.
--
-- 적용 전에 사람이 할 일
--   1. 확장 켜기 — 아래 create extension 이 권한 때문에 실패하면 대시보드 → Database → Extensions 에서 pg_cron 과 pg_net 을 켠 뒤 다시 적용한다
--      (pg_cron 은 Integrations → Cron 에서 켜도 같다).
--   2. Slack 웹훅을 Vault 에 넣는다 — SQL Editor 에서 한 번. 주소는 이 파일에 넣지 않는다(자리표시자를 실제 주소로 바꿔서 실행):
--        select vault.create_secret('https://hooks.slack.com/services/XXXX/YYYY/ZZZZ', 'cell_bench_slack_webhook', '셀 시험대 클라우드 심박 감시');
--      주소를 바꿀 때:
--        select vault.update_secret((select id from vault.secrets where name = 'cell_bench_slack_webhook'), 'https://hooks.slack.com/services/새주소');
--
-- 적용 뒤 확인
--   select jobname, schedule, command, active from cron.job where jobname = 'cell-bench-heartbeat';   -- 등록됐나
--   select status, start_time from cron.job_run_details order by start_time desc limit 5;            -- 1분마다 도나
--   select * from public.bench_watch;                                                                -- 시험대별 판정
--   select id, status_code, content from net._http_response order by created desc limit 5;          -- Slack 전송 결과 (6시간 보관)
--
-- 되돌리기
--   select cron.unschedule('cell-bench-heartbeat');
--   drop function if exists public.bench_heartbeat_check();
--   drop table if exists public.bench_watch;
--   확장(pg_cron · pg_net)은 다른 작업이 쓸 수 있어 지우지 않는다. pg_cron 을 끄면 등록된 작업이 전부 지워진다.
--   웹훅 비밀: delete from vault.secrets where name = 'cell_bench_slack_webhook';
--
-- 한계 — 판정은 PC 가 적어 보낸 시각(updated_at)과 클라우드의 지금 시각을 비교한다. PC 시계가 5분 넘게 늦으면 거짓 경보, 빠르면 늦은 경보가 난다
--   (Windows 시간 동기화가 켜져 있으면 몇 초 안이다). 사람이 Ctrl+C 로 엔진을 멈추면 단계가 DONE·STOPPED 가 아니라 울린다 — 의도한 것이다.
--
-- 근거 문서 (2026-10-10 확인)
--   pg_cron 켜기          https://supabase.com/docs/guides/cron/install         create extension pg_cron with schema pg_catalog
--   같은 이름 재등록       https://supabase.com/docs/guides/cron/quickstart      같은 이름으로 schedule 하면 기존 작업을 덮어쓴다(upsert)
--   pg_net · http_post    https://supabase.com/docs/guides/database/extensions/pg_net   비동기 · 트랜잭션이 커밋된 뒤에 보낸다
--   Vault                 https://supabase.com/docs/guides/database/vault       vault.create_secret · vault.decrypted_secrets

create extension if not exists pg_cron with schema pg_catalog;
grant usage on schema cron to postgres;
grant all privileges on all tables in schema cron to postgres;
create extension if not exists pg_net with schema extensions;

-- 시험대별 클라우드 판정 (결과판·신호등이 읽는다)
create table if not exists public.bench_watch (
  bench_id          text primary key references public.bench(id),
  state             text not null default 'ok' check (state in ('ok', 'lost')),
  lost_since        timestamptz,              -- 미확인이 된 사건의 마지막 갱신 시각 (PC 가 마지막으로 말한 때)
  last_notified_at  timestamptz,              -- 마지막으로 Slack 을 보낸 때 (60분 반복의 기준)
  updated_at        timestamptz               -- 이 줄을 마지막으로 바꾼 때
);

-- 읽기는 누구나(측정값 표와 같은 결 — 20261008111235_public_read), 쓰기는 아래 함수만(소유자 권한)
alter table public.bench_watch enable row level security;
drop policy if exists bench_watch_read on public.bench_watch;
create policy bench_watch_read on public.bench_watch for select to anon, authenticated using (true);

create or replace function public.bench_heartbeat_check()
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  stale    constant interval := interval '5 minutes';
  renotify constant interval := interval '60 minutes';
  hook     text;
  r        record;
  w        public.bench_watch%rowtype;
  mins     int;
  last_kst text;
  msg      text;
begin
  -- Slack 웹훅. Vault 가 없거나 비밀이 없으면 null — 기록만 하고 전송은 건너뛴다
  begin
    select ds.decrypted_secret into hook
      from vault.decrypted_secrets ds
     where ds.name = 'cell_bench_slack_webhook'
     limit 1;
  exception when others then
    hook := null;
  end;

  for r in select s.bench_id, s.updated_at, s.cycle, s.phase from public.bench_state s loop
    insert into public.bench_watch (bench_id) values (r.bench_id) on conflict (bench_id) do nothing;
    select * into w from public.bench_watch where bench_id = r.bench_id for update;
    msg := null;
    mins := floor(extract(epoch from (now() - r.updated_at)) / 60)::int;
    last_kst := to_char(r.updated_at at time zone 'Asia/Seoul', 'MM-DD HH24:MI') || ' KST';

    if r.updated_at < now() - stale and coalesce(r.phase, '') not in ('DONE', 'STOPPED') then
      if w.state <> 'lost' then
        update public.bench_watch
           set state = 'lost', lost_since = r.updated_at, last_notified_at = now(), updated_at = now()
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
      update public.bench_watch
         set state = 'ok', lost_since = null, updated_at = now()
       where bench_id = r.bench_id;
      insert into public.bench_event (bench_id, t, cycle, phase, kind, serial, detail)
      values (r.bench_id, now(), r.cycle, r.phase, 'heartbeat_back', '-',
              format('시험대 PC 소식이 다시 들어옴 — %s분 만에', mins));
      msg := format('[셀 시험대 %s] 복구 · 클라우드 감시 · 시험대 PC 소식이 다시 들어옴 (%s분 만에)', r.bench_id, mins);
    end if;

    if msg is not null and hook is not null then
      begin
        perform net.http_post(url := hook,
                              body := jsonb_build_object('text', msg),
                              headers := '{"Content-Type": "application/json"}'::jsonb,
                              timeout_milliseconds := 5000);
      exception when others then
        raise warning 'cell-bench heartbeat: Slack 전송 실패 — %', sqlerrm;
      end;
    end if;
  end loop;
end;
$$;

-- 결과판(anon · authenticated)이 RPC 로 부르지 못하게 — 1분마다 부르는 것은 pg_cron(소유자)뿐이다
revoke execute on function public.bench_heartbeat_check() from public, anon, authenticated;

-- 1분마다. 같은 이름으로 다시 등록하면 덮어쓰므로 이 파일을 다시 적용해도 작업이 둘이 되지 않는다
select cron.schedule('cell-bench-heartbeat', '* * * * *', 'select public.bench_heartbeat_check()');
