-- 레일 시험 세트 결정에 '재시험'을 더한다 (2026-11-03 사용자 결정 5: 세트 결정은 합격 · 불합격 · 재시험).
-- 20261102112546_rail_disposition.sql 은 doc->>'verdict' 검사를 이름 없는 check 로 만들었다. Postgres 가 붙인 이름은 보통
-- rail_disposition_doc_check 이지만 확실하지 않으므로, 이 표의 check 제약 가운데 verdict 를 보는 것을 이름과 관계없이 찾아 지운 뒤
-- 이름 있는 제약으로 다시 건다. 두 번 돌려도 같은 결과다(지울 것이 없으면 지나가고, 새 제약은 지운 뒤 건다).
-- 셀 조치 값(retest · exclude · hold)은 표 검사에 없으므로 바꿀 것이 없다. 이미 저장된 줄은 pass · fail 뿐이라 새 검사에 걸리지 않는다.

do $$
declare c record;
begin
  for c in
    select con.conname
      from pg_constraint con
     where con.conrelid = 'public.rail_disposition'::regclass
       and con.contype = 'c'
       and pg_get_constraintdef(con.oid) ilike '%verdict%'
  loop
    execute format('alter table public.rail_disposition drop constraint %I', c.conname);
  end loop;
end $$;

alter table public.rail_disposition drop constraint if exists rail_disposition_doc_check;
alter table public.rail_disposition drop constraint if exists rail_disposition_verdict_check;
alter table public.rail_disposition
  add constraint rail_disposition_verdict_check check (doc->>'verdict' in ('pass', 'fail', 'retest'));
