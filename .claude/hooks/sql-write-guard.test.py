#!/usr/bin/env python3
"""sql-write-guard 훅의 판단이 맞는지 확인하는 시험.

실행: python3 .claude/hooks/sql-write-guard.test.py

두 방향을 모두 본다. 조회는 명시적 allow 로 통과해야 하고(침묵하면 확인 창이
계속 떠서 훅을 넣은 의미가 없다 — 실측: 허용 목록만으로는 확인 창이 사라지지
않았다), 데이터를 바꾸는 쿼리는 반드시 걸려야 한다(걸리지 않으면 훅이 있으나
마나다).
"""

import importlib.util
import json
import pathlib
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).parent
GUARD = HERE / "sql-write-guard.py"

spec = importlib.util.spec_from_file_location("guard", GUARD)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

# 통과해야 하는 쿼리 — 읽기만 한다
SHOULD_PASS = [
    "select 1;",
    "select item_number, name from item where item_number in ('SMT18-0099');",
    "with vals as (select 1 as a) select * from vals;",
    "select attrs_non_rev->>'mfr' mfr from item order by item_number;",
    "show search_path;",
    "select count(*) from spec_value_provenance where source = 'datasheet';",
    # 문자열 안에 위험해 보이는 단어가 있어도 값일 뿐이므로 통과해야 한다
    "select * from log where action = 'delete';",
    "select 'drop table x' as note;",
    # 주석 안의 단어도 마찬가지
    "select 1; -- delete from item",
    "/* update 예정 */ select 1;",
    "select * from item offset 10 limit 5;",
]

# 반드시 걸려야 하는 쿼리 — 데이터나 구조를 바꾼다
SHOULD_DENY = [
    "delete from item where item_number = 'SMT18-0099';",
    "drop table item;",
    "truncate item;",
    "update item set name = 'x' where item_number = 'y';",
    "insert into item (item_number) values ('SMT99-9999');",
    "alter table item add column foo text;",
    "create index on item (item_number);",
    # 앞 문장이 조회라도 뒤에 쓰기가 붙어 있으면 걸려야 한다
    "select 1; delete from item;",
    # 쓰기를 품은 CTE
    "with d as (delete from item returning *) select * from d;",
    # select ... into 는 표를 만든다
    "select * into backup_item from item;",
    "grant select on item to anon;",
    "",
    "   ",
]


def check_classify() -> int:
    failures = 0
    for sql in SHOULD_PASS:
        ok, reason = guard.classify(sql)
        if not ok:
            print(f"  [실패] 통과해야 하는데 걸렸다: {sql!r} — {reason}")
            failures += 1
    for sql in SHOULD_DENY:
        ok, _ = guard.classify(sql)
        if ok:
            print(f"  [실패] 걸려야 하는데 통과했다: {sql!r}")
            failures += 1
    return failures


def run_hook(payload: dict) -> str:
    result = subprocess.run(
        [sys.executable, str(GUARD)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"훅이 0 이 아닌 코드로 끝났다: {result.returncode}"
    return result.stdout.strip()


def decision_of(out: str):
    try:
        return json.loads(out)["hookSpecificOutput"]["permissionDecision"]
    except Exception:
        return None


def check_hook_output() -> int:
    failures = 0

    # 조회는 침묵이 아니라 명시적 allow 를 내야 한다. 침묵하면 정상 권한 흐름으로
    # 넘어가는데, 이 원격 환경에서는 그 흐름이 허용 목록을 무시하고 확인 창을
    # 띄우는 것이 실측됐기 때문이다.
    out = run_hook({
        "tool_name": "mcp__Supabase__execute_sql",
        "tool_input": {"query": "select 1;"},
    })
    if decision_of(out) != "allow":
        print(f"  [실패] 조회의 판정이 allow 가 아니다: {out!r}")
        failures += 1

    out = run_hook({
        "tool_name": "mcp__Supabase__execute_sql",
        "tool_input": {"query": "delete from item;"},
    })
    if decision_of(out) != "deny":
        print(f"  [실패] 삭제 쿼리의 판정이 deny 가 아니다: {out!r}")
        failures += 1

    # 다른 도구의 호출에는 참견하지 않아야 한다 — allow 도 deny 도 내지 않는다
    out = run_hook({
        "tool_name": "Bash",
        "tool_input": {"command": "delete from item"},
    })
    if out:
        print(f"  [실패] 다른 도구인데 훅이 참견했다: {out}")
        failures += 1

    # 마이그레이션은 내용과 무관하게 전부 막혀야 한다. 읽기만 하는 SQL 을 담아도
    # 마이그레이션으로 적용하는 행위 자체가 데이터베이스를 바꾸기 때문이다.
    #
    # 이름을 **실제로 push 된 파일**로 쓴다. 존재하지 않는 이름을 쓰면 이제 적용 전 push
    # 판정에 먼저 걸려서, 이 시험이 재려는 "내용 무관 차단"이 아니라 다른 이유로 통과한다.
    # 두 원인이 섞이면 한쪽이 고장나도 초록불이 켜진다.
    pushed = pushed_migration_name()
    for query in ("alter table item add column foo text;", "select 1;"):
        out = run_hook({
            "tool_name": "mcp__Supabase__apply_migration",
            "tool_input": {"name": pushed, "query": query},
        })
        if decision_of(out) != "deny":
            print(f"  [실패] 마이그레이션이 막히지 않았다 ({query!r}): {out!r}")
            failures += 1
        if pushed and "적용 전 push 판정" in reason_of(out):
            print(f"  [실패] push 된 파일인데 push 판정에 걸렸다: {out!r}")
            failures += 1

    return failures


def reason_of(out: str) -> str:
    try:
        return json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"]
    except Exception:
        return ""


def pushed_migration_name():
    """실제로 커밋·push 된 마이그레이션 하나의 이름(.sql 제외)을 찾는다.

    없으면 None 을 돌려주고, 그것을 쓰는 시험은 건너뛴다. 이 시험 파일이 원격 없는
    복제본에서도 돌아야 하기 때문이다.
    """
    root = pathlib.Path(guard.repo_root())
    mig = root / "supabase" / "migrations"
    if not mig.is_dir():
        return None
    for f in sorted(mig.glob("*.sql"), reverse=True):
        name = f.stem
        state, _ = guard.migration_push_state(name)
        if state == "pushed":
            return name
    return None


def check_push_gate() -> int:
    """적용 전 push 판정 — 2026-08-18 되돌림 사고의 원인 절반을 막는 쪽."""
    failures = 0

    # ① 저장소에 파일이 없는 이름으로 적용하면 막혀야 한다. 적용 이력에는 이름이 박히는데
    #    저장소에 파일이 없으면 다음 사람이 무엇이 적용됐는지 확인할 방법이 없다(고아).
    out = run_hook({
        "tool_name": "mcp__Supabase__apply_migration",
        "tool_input": {"name": "29990101000000_does_not_exist", "query": "select 1;"},
    })
    if decision_of(out) != "deny" or "적용 전 push 판정" not in reason_of(out):
        print(f"  [실패] 저장소에 없는 마이그레이션이 push 판정에 걸리지 않았다: {out!r}")
        failures += 1

    # ② 그 판정은 **열쇠로 넘어갈 수 없어야** 한다. push 여부는 사용자의 승인으로 대체될 수
    #    없다 — 승인하든 안 하든, push 하지 않고 적용하면 다음 세션이 그것을 볼 방법이 없다.
    key = pathlib.Path(guard.unlock_path())
    if key.exists():
        print("  [건너뜀] 열쇠 파일이 이미 있어 ② 는 돌리지 않는다")
    else:
        key.write_text("시험용 열쇠\n")
        try:
            out = run_hook({
                "tool_name": "mcp__Supabase__apply_migration",
                "tool_input": {"name": "29990101000000_does_not_exist", "query": "select 1;"},
            })
            if decision_of(out) != "deny":
                print(f"  [실패] 열쇠로 push 판정을 넘어갔다: {out!r}")
                failures += 1
            if not key.exists():
                print("  [실패] push 판정에 막혔는데 열쇠가 소비됐다 — 열쇠는 남아 있어야 한다")
                failures += 1
        finally:
            if key.exists():
                key.unlink()

    return failures


def check_unlock() -> int:
    """일회용 열쇠가 딱 한 번만 통하는지 확인한다."""
    failures = 0
    key = pathlib.Path(guard.unlock_path())
    existed = key.exists()
    if existed:  # 사람이 만들어 둔 열쇠를 시험이 소비해 버리면 안 된다
        print("  [건너뜀] 열쇠 파일이 이미 있어 이 시험은 돌리지 않는다")
        return 0

    key.write_text("시험용 열쇠\n")
    out = run_hook({
        "tool_name": "mcp__Supabase__execute_sql",
        "tool_input": {"query": "delete from item;"},
    })
    # 열쇠를 소비한 통과도 명시적 allow 다. 열쇠를 만든 행위가 곧 승인이므로
    # 확인 창을 또 띄우지 않기 위해서다.
    if decision_of(out) != "allow":
        print(f"  [실패] 열쇠가 있는데도 allow 가 아니다: {out!r}")
        failures += 1
    if key.exists():
        print("  [실패] 열쇠를 쓰고도 파일이 남아 있다 — 일회용이 아니다")
        failures += 1
        key.unlink()

    # 열쇠 직후의 **같은 호출 재판정**은 몇 번이든 통과해야 한다.
    #
    # 왜 통과가 정답인가. 원격 실행 환경에서는 한 번의 도구 호출에 이 훅이 여러 번 발동한다.
    # 일회용 열쇠만 두면 첫 발동이 열쇠를 소진하고 다음 발동이 열쇠를 못 찾아 차단하며, 그
    # 차단이 최종 판정으로 남아 **승인이 있어도 쓰기가 영영 통하지 않았다.** 그래서 열쇠를
    # 쓸 때 영수증을 남기고 같은 지문의 재판정을 함께 통과시킨다.
    #
    # 횟수를 세지 않는 이유는 실측이다 — 발동 횟수도 발동 사이의 간격도 환경이 정하고 훅이
    # 통제할 수 없다(2026-08-31 에 한 호출의 두 발동이 481초 떨어져 있었다). 승인의 범위를
    # 좁히는 일은 횟수가 아니라 아래의 지문 대조가 한다.
    for i in (1, 2, 3):
        out = run_hook({
            "tool_name": "mcp__Supabase__execute_sql",
            "tool_input": {"query": "delete from item;"},
        })
        if decision_of(out) != "allow":
            print(f"  [실패] 같은 호출의 재판정 {i}회째가 막혔다 — 훅의 다중 발동을 견디지 못한다: {out!r}")
            failures += 1
            break

    # **다른 쿼리는 막혀야 한다.** 이것이 넉넉한 유효시간을 상쇄하는 자리다. 영수증이 살아
    # 있어도 지문이 다르면 남의 승인이므로, 여기서 deny 가 나오지 않으면 장치의 뜻이 무너진다.
    out = run_hook({
        "tool_name": "mcp__Supabase__execute_sql",
        "tool_input": {"query": "delete from change;"},
    })
    if decision_of(out) != "deny":
        print(f"  [실패] 승인받지 않은 다른 쿼리가 영수증을 빌려 썼다: {out!r}")
        failures += 1

    # 남은 영수증은 시험이 치운다(다음 시험에 새어 나가지 않게).
    receipt = pathlib.Path(guard.receipt_path())
    if receipt.exists():
        receipt.unlink()

    # **만료된 영수증은 통하지 않는다.** 유효시간을 늘렸으므로 상한이 실제로 지켜지는지 본다.
    receipt.write_text(f"{'0' * 32} {time.time() - guard.RECEIPT_TTL_SECONDS - 10}")
    out = run_hook({
        "tool_name": "mcp__Supabase__execute_sql",
        "tool_input": {"query": "delete from item;"},
    })
    if decision_of(out) != "deny":
        print(f"  [실패] 만료된 영수증이 통했다: {out!r}")
        failures += 1
    if receipt.exists():
        print("  [실패] 만료된 영수증이 남아 있다 — 그 자리에서 버려야 한다")
        failures += 1
        receipt.unlink()

    return failures


def check_bash_gate() -> int:
    """셸 감시면 — 자체 호스팅 통로에 승인 관문이 서는지, 그리고 **일반 작업은 안 막는지**.

    두 방향을 같은 무게로 본다. 라이브 데이터베이스를 바꾸는 스크립트 실행은 반드시
    걸려야 하고(걸리지 않으면 관문이 없는 것이다), 그 밖의 셸 명령은 아무 판정도 받지
    않아야 한다(막히면 훅이 세션의 일을 방해하는 장치가 되어 사람이 훅을 꺼 버린다).
    """
    failures = 0

    # ① 마이그레이션 적용 스크립트 — 저장소에 없는 파일이면 push 판정에 먼저 걸린다.
    out = run_hook({
        "tool_name": "Bash",
        "tool_input": {"command": "sh selfhost/apply-migration.sh supabase/migrations/29990101000000_nope.sql"},
    })
    if decision_of(out) != "deny" or "적용 전 push 판정" not in reason_of(out):
        print(f"  [실패] 셸 마이그레이션 적용이 push 판정에 걸리지 않았다: {out!r}")
        failures += 1

    # ①-2 인터프리터로 부르는 통로도 같은 판정을 받아야 한다. 첫 자리가 `python3` 이라
    #     감싸개를 벗기지 않으면 목록에 이름을 등재해도 걸리지 않는다(2026-09-09 실측).
    out = run_hook({
        "tool_name": "Bash",
        "tool_input": {"command": "python3 selfhost/ssm-sql.py apply supabase/migrations/29990101000000_nope.sql"},
    })
    if decision_of(out) != "deny" or "적용 전 push 판정" not in reason_of(out):
        print(f"  [실패] 인터프리터로 부른 라이브 SQL 경로가 push 판정에 걸리지 않았다: {out!r}")
        failures += 1

    # ② 인자가 없어도(또는 마이그레이션 파일이 아니어도) 열쇠 없이는 막혀야 한다.
    for command in (
        "sh selfhost/apply-migration.sh",
        "sh selfhost/deploy-stack.sh --restore-force",
        "cd /home/user/Fitstack && sh selfhost/deploy-stack.sh",
        'bash -c "sh selfhost/apply-migration.sh"',
        "python3 selfhost/ssm-sql.py apply",
        "./selfhost/ssm-sql.py query",
    ):
        out = run_hook({"tool_name": "Bash", "tool_input": {"command": command}})
        if decision_of(out) != "deny":
            print(f"  [실패] 라이브 데이터베이스를 바꾸는 명령이 막히지 않았다: {command!r} -> {out!r}")
            failures += 1

    # ③ 막으면 안 되는 것 — 조회 스크립트, 그 파일을 읽는 명령, 평범한 작업.
    #    여기서 무엇이든 판정이 나오면 그것이 곧 오탐이다.
    for command in (
        'sh selfhost/query.sh "select count(*) from item"',
        "cat selfhost/apply-migration.sh",
        "grep -n apply-migration.sh selfhost/README.md",
        "sed -n 1,40p selfhost/deploy-stack.sh",
        "git status --porcelain",
        "cd app && pnpm test",
        # 인터프리터를 감싸개로 본 뒤에도, 목록에 없는 파일을 부르는 명령은 그대로 통과해야
        # 한다. 여기서 판정이 나오면 파이썬을 쓰는 모든 작업이 멈춘다.
        "python3 .claude/hooks/sql-write-guard.test.py",
        "python3 scripts/migration-object-probe.mjs --emit-sql",
        'python3 -c "print(1)"',
        "cat selfhost/ssm-sql.py",
    ):
        out = run_hook({"tool_name": "Bash", "tool_input": {"command": command}})
        if out:
            print(f"  [실패] 평범한 셸 명령에 판정이 나왔다(오탐): {command!r} -> {out!r}")
            failures += 1

    # ④ 열쇠는 셸 통로에서도 통하고, 그 승인이 **다른 명령으로 번지지 않는다.**
    key = pathlib.Path(guard.unlock_path())
    receipt = pathlib.Path(guard.receipt_path())
    if key.exists():
        print("  [건너뜀] 열쇠 파일이 이미 있어 ④ 는 돌리지 않는다")
    else:
        approved = "sh selfhost/deploy-stack.sh --restore-force"
        key.write_text("시험용 열쇠\n")
        try:
            out = run_hook({"tool_name": "Bash", "tool_input": {"command": approved}})
            if decision_of(out) != "allow":
                print(f"  [실패] 열쇠가 있는데 셸 통로가 막혔다: {out!r}")
                failures += 1

            # 같은 명령의 재판정은 영수증으로 통과해야 한다(한 호출에 훅이 여러 번 발동한다).
            out = run_hook({"tool_name": "Bash", "tool_input": {"command": approved}})
            if decision_of(out) != "allow":
                print(f"  [실패] 같은 명령의 재판정이 영수증으로 통과하지 못했다: {out!r}")
                failures += 1

            # 다른 명령은 그 영수증을 빌려 쓸 수 없어야 한다 — 지문에 명령을 넣은 이유다.
            out = run_hook({
                "tool_name": "Bash",
                "tool_input": {"command": "sh selfhost/apply-migration.sh"},
            })
            if decision_of(out) != "deny":
                print(f"  [실패] 승인받지 않은 다른 셸 명령이 영수증을 빌려 썼다: {out!r}")
                failures += 1
        finally:
            if key.exists():
                key.unlink()
            if receipt.exists():
                receipt.unlink()

    return failures


def check_actions_gate() -> int:
    """워크플로 감시면 — 적용을 저장소의 자동 작업으로 옮겨도 관문이 따라오는지.

    셸 감시면과 같은 두 방향을 본다. 라이브 데이터베이스를 바꾸는 워크플로를 부르는 요청은
    반드시 걸려야 하고, 빌드·검사·배포 워크플로를 부르는 요청에는 아무 판정도 없어야 한다.
    """
    failures = 0
    repo = {"owner": "fitogether-org", "repo": "Fitstack"}

    # ① 적용 워크플로 + 저장소에 없는 파일 → push 판정에 먼저 걸린다(열쇠보다 앞).
    out = run_hook({
        "tool_name": "mcp__github__actions_run_trigger",
        "tool_input": dict(repo, method="run_workflow", workflow_id="db-migration-apply.yml",
                           ref="main",
                           inputs={"file": "supabase/migrations/29990101000000_nope.sql",
                                   "confirm": "APPLY"}),
    })
    if decision_of(out) != "deny" or "적용 전 push 판정" not in reason_of(out):
        print(f"  [실패] 워크플로로 부른 마이그레이션 적용이 push 판정에 걸리지 않았다: {out!r}")
        failures += 1

    # ② 파일 입력이 없어도 열쇠 없이는 막혀야 한다 — 부르는 행위 자체가 라이브 쓰기다.
    out = run_hook({
        "tool_name": "mcp__github__actions_run_trigger",
        "tool_input": dict(repo, method="run_workflow",
                           workflow_id=".github/workflows/db-migration-apply.yml", ref="main"),
    })
    if decision_of(out) != "deny":
        print(f"  [실패] 적용 워크플로 실행 요청이 막히지 않았다: {out!r}")
        failures += 1

    # ③ 막으면 안 되는 것 — 다른 워크플로, 그리고 새 실행이 아닌 요청.
    #    여기서 판정이 나오면 그것이 곧 오탐이고, 관계없는 작업이 멈춘다.
    for tool_input in (
        dict(repo, method="run_workflow", workflow_id="ci.yml", ref="main"),
        dict(repo, method="run_workflow", workflow_id="deploy-aws.yml", ref="main"),
        dict(repo, method="run_workflow", workflow_id="branch-cleanup.yml", ref="main"),
        dict(repo, method="cancel_workflow_run", run_id=12345),
        dict(repo, method="rerun_failed_jobs", run_id=12345),
    ):
        out = run_hook({"tool_name": "mcp__github__actions_run_trigger", "tool_input": tool_input})
        if out:
            print(f"  [실패] 관계없는 워크플로 요청에 판정이 나왔다(오탐): {tool_input!r} -> {out!r}")
            failures += 1

    # ④ 열쇠는 이 통로에서도 통하고, 그 승인이 **다른 워크플로로 번지지 않는다.**
    key = pathlib.Path(guard.unlock_path())
    receipt = pathlib.Path(guard.receipt_path())
    if key.exists():
        print("  [건너뜀] 열쇠 파일이 이미 있어 ④ 는 돌리지 않는다")
    else:
        approved = dict(repo, method="run_workflow", workflow_id="db-migration-apply.yml",
                        ref="main", inputs={"confirm": "APPLY"})
        key.write_text("시험용 열쇠\n")
        try:
            out = run_hook({"tool_name": "mcp__github__actions_run_trigger", "tool_input": approved})
            if decision_of(out) != "allow":
                print(f"  [실패] 열쇠가 있는데 워크플로 통로가 막혔다: {out!r}")
                failures += 1

            out = run_hook({"tool_name": "mcp__github__actions_run_trigger", "tool_input": approved})
            if decision_of(out) != "allow":
                print(f"  [실패] 같은 요청의 재판정이 영수증으로 통과하지 못했다: {out!r}")
                failures += 1

            # 입력이 다르면 다른 승인이어야 한다 — 지문에 워크플로·브랜치·입력을 넣은 이유다.
            out = run_hook({
                "tool_name": "mcp__github__actions_run_trigger",
                "tool_input": dict(repo, method="run_workflow",
                                   workflow_id="db-migration-apply.yml", ref="main",
                                   inputs={"confirm": "APPLY", "file": "supabase/migrations/other.sql"}),
            })
            if decision_of(out) != "deny":
                print(f"  [실패] 승인받지 않은 다른 실행 요청이 영수증을 빌려 썼다: {out!r}")
                failures += 1
        finally:
            if key.exists():
                key.unlink()
            if receipt.exists():
                receipt.unlink()

    return failures


if __name__ == "__main__":
    total = (check_classify() + check_hook_output() + check_unlock()
             + check_push_gate() + check_bash_gate() + check_actions_gate())
    if total:
        print(f"\n실패 {total}건")
        sys.exit(1)
    print(f"✓ 통과 — 조회 {len(SHOULD_PASS)}건은 확인 창 없이 자동 승인되고, "
          f"쓰기 {len(SHOULD_DENY)}건은 모두 차단됐으며, "
          f"마이그레이션은 내용과 무관하게 차단되고, "
          f"열쇠는 같은 지문의 재판정만 통과시켰고 다른 쿼리·만료 영수증은 막았으며, "
          f"push 되지 않은 마이그레이션은 열쇠로도 넘어가지 못했으며, "
          f"셸로 라이브 데이터베이스를 바꾸는 명령은 막히고 평범한 셸 명령은 그대로 통과했으며, "
          f"라이브 데이터베이스를 바꾸는 워크플로 실행 요청은 막히고 다른 워크플로 요청은 그대로 통과했습니다.")
