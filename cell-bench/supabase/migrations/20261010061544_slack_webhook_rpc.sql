-- Slack 웹훅 건네주기 (2026-10-10) — 시험대 PC 가 Slack 웹훅을 클라우드 Vault 에서 받아 쓰게 한다.
-- Supabase 프로젝트 cell-bench(rmlxafxegabeqqtuvyyb) 전용. 사람 승인 뒤 별도 단계로 적용한다(이 파일만으로는 아무것도 바뀌지 않는다).
--
-- 무엇을 하나
--   public.bench_slack_webhook() 이 Vault 의 비밀 cell_bench_slack_webhook 값을 돌려준다(없으면 null).
--   그 비밀은 클라우드 심박 감시(20261009235950_heartbeat_watch)가 이미 쓰는 것과 같은 하나다 — 웹훅의 단일 원천은 Vault 다.
--   TestPC 의 감시자·엔진(cellbench/alert.py)은 자격 증명 관리자(keyring cell-bench-remote)에 웹훅이 없을 때만
--   service_role 키로 이 함수를 부르고(cellbench/cloud.py fetch_slack_webhook), 받은 값은 메모리에만 둔다(keyring·파일에 쓰지 않는다).
--   그래서 TestPC 앞에서 웹훅을 다시 입력하지 않아도 PC 쪽 알림이 나간다.
--
-- 누가 부르나 — service_role(TestPC) 만. 결과판(anon · 로그인 사용자)은 부를 수 없다.
--   Supabase 는 public 에 만든 함수에 anon · authenticated 실행 권한을 기본으로 주므로, public 만이 아니라 둘을 이름으로 거둔다.
--
-- 적용 전에는 PC 의 호출이 실패(함수 없음)하고 None 으로 끝난다 — keyring 에 웹훅이 없으면 지금처럼 기록만 하고 보내지 않는다. 시험은 멈추지 않는다.
--
-- 적용 뒤 확인 (값은 화면에 꺼내지 않고 있음·없음과 권한만 본다)
--   select public.bench_slack_webhook() is not null as has_hook;                                   -- true 여야 한다
--   select has_function_privilege('anon',          'public.bench_slack_webhook()', 'execute');     -- false
--   select has_function_privilege('authenticated', 'public.bench_slack_webhook()', 'execute');     -- false
--   select has_function_privilege('service_role',  'public.bench_slack_webhook()', 'execute');     -- true
--
-- 되돌리기
--   drop function if exists public.bench_slack_webhook();
--   PC 는 함수가 없으면 None 을 받고 keyring 웹훅만 쓰는 예전 동작으로 돌아간다. Vault 의 비밀은 심박 감시가 쓰므로 지우지 않는다.

create or replace function public.bench_slack_webhook()
returns text
language sql
stable
security definer
set search_path = ''
as $$
  select ds.decrypted_secret
    from vault.decrypted_secrets ds
   where ds.name = 'cell_bench_slack_webhook'
   limit 1;
$$;

-- 결과판(anon · authenticated)이 RPC 로 부르지 못하게 — 부르는 것은 TestPC 의 service_role 뿐이다
revoke execute on function public.bench_slack_webhook() from public, anon, authenticated;
grant execute on function public.bench_slack_webhook() to service_role;

-- PostgREST 가 새 함수를 곧바로 /rest/v1/rpc/bench_slack_webhook 으로 내보이게 (Supabase 는 보통 스스로 다시 읽지만, 이 한 줄은 무해하다)
notify pgrst, 'reload schema';
