#!/usr/bin/env python3
"""branch-guard 훅의 판단이 맞는지 확인하는 시험.

실행: python3 .claude/hooks/branch-guard.test.py

두 방향을 모두 본다. 규칙 밖의 브랜치·PR 은 반드시 막혀야 하고(막히지 않으면 훅이
있으나 마나다), 정상 작업과 무관한 셸 명령은 반드시 지나가야 한다(멀쩡한 작업이
막히면 다음 세션이 훅을 꺼 버린다).

바깥 세계(현재 브랜치, gh 조회)는 함수를 바꿔 끼워 재현한다. 그래서 이 시험은 git
저장소도 gh 인증도 필요 없다. 마지막 절만 훅을 실제 프로세스로 돌려 입출력 규약을
확인한다.
"""

import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile

for _stream in (sys.stdout, sys.stderr):  # Windows 콘솔(cp949)에서도 한글·기호가 깨지지 않게
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

HERE = pathlib.Path(__file__).parent
GUARD = HERE / "branch-guard.py"

spec = importlib.util.spec_from_file_location("branch_guard", GUARD)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def payload(command, session="s1", cwd="/repo"):
    return {"tool_name": "Bash", "session_id": session, "cwd": cwd,
            "tool_input": {"command": command}}


def with_world(branch="feat/x", merged=None):
    """현재 브랜치와 gh 조회 결과를 정해 둔다."""
    guard.current_branch = lambda cwd: branch
    guard.merged_pr_exists = lambda name, cwd: merged


def use_state_dir(tmp):
    """세션 기록을 임시 폴더에 쓰게 한다 — 시험이 진짜 .claude/state 를 더럽히지 않게."""
    guard.state_path = lambda sid: str(pathlib.Path(tmp) / "state" / sid)


# (설명, 명령, 세션, 현재 브랜치, gh 답, 막혀야 하나)
CASES = [
    # 브랜치 생성 — 형식
    ("형식에 맞는 생성", "git checkout -b feat/gen-task-order", "a1", "main", None, False),
    ("switch -c 형식 맞음", "git switch -c fix/gen-task-order", "a2", "main", None, False),
    ("git branch <이름> 형식 맞음", "git branch docs/readme-polish", "a3", "main", None, False),
    ("종류 없는 이름", "git checkout -b my-branch", "a4", "main", None, True),
    ("허용되지 않은 종류", "git checkout -b wip/thing", "a5", "main", None, True),
    ("대문자·밑줄", "git checkout -b feat/Gen_Task", "a6", "main", None, True),
    ("claude/ 접두어(클라우드 세션 이름)를 로컬에서 흉내", "git checkout -b claude/foo-abc123", "a7", "main", None, True),
    ("--create= 꼴", "git switch --create=feat/one-two", "a8", "main", None, False),
    # 세션 = 브랜치 하나
    ("같은 세션 두 번째 브랜치", "git checkout -b feat/second-one", "a1", "main", None, True),
    ("같은 세션 같은 이름 재시도", "git checkout -b feat/gen-task-order", "a1", "main", None, False),
    ("다른 세션은 자기 브랜치를 만들 수 있다", "git checkout -b feat/other-thing", "b1", "main", None, False),
    ("&& 로 이어진 명령 안의 생성", "git fetch && git checkout -b feat/chained-one && git push", "c1", "main", None, False),
    ("&& 안의 형식 위반", "git fetch && git checkout -b bad_name", "c2", "main", None, True),
    ("만든 직후 같은 명령에서 PR 열기", "git switch -c feat/then-pr && gh pr create --draft --title t", "c5", "main", False, False),
    ("sudo·env 감싸개를 벗긴다", "env FOO=1 git checkout -b oops", "c3", "main", None, True),
    ("git -C 폴더 지정", "git -C /somewhere checkout -b nope", "c4", "main", None, True),
    # 이름 바꾸기
    ("브랜치 이름 바꾸기 -m", "git branch -m feat/new-name", "d1", "feat/old", None, True),
    ("브랜치 이름 바꾸기 -M", "git branch -M old new", "d2", "feat/old", None, True),
    # main push
    ("origin main 으로 push", "git push origin main", "e1", "feat/x", None, True),
    ("HEAD:main 으로 push", "git push origin HEAD:main", "e2", "feat/x", None, True),
    ("main 에 서서 refspec 없이 push", "git push", "e3", "main", None, True),
    ("main 에 서서 -u origin main", "git push -u origin main", "e4", "main", None, True),
    ("작업 브랜치에서 push", "git push -u origin feat/x", "e5", "feat/x", None, False),
    ("작업 브랜치에서 refspec 없이 push", "git push", "e6", "feat/x", None, False),
    # gh pr create
    ("main 에서 PR 열기", "gh pr create --title t", "f1", "main", False, True),
    ("머지된 PR 이 있는 브랜치에서 또 열기", "gh pr create --fill", "f2", "feat/x", True, True),
    ("머지된 PR 없는 브랜치에서 열기", "gh pr create --draft --title t", "f3", "feat/x", False, False),
    ("--head 로 머지된 브랜치 지정", "gh pr create --head feat/reused --title t", "f4", "feat/x", True, True),
    ("gh 로 잴 수 없으면 막지 않는다", "gh pr create --title t", "f5", "feat/x", None, False),
    # 판정 밖
    ("브랜치 목록", "git branch -a", "g1", "main", None, False),
    ("브랜치 삭제", "git branch -d feat/done", "g2", "main", None, False),
    ("체크아웃(생성 아님)", "git checkout main", "g3", "feat/x", None, False),
    ("switch(생성 아님)", "git switch feat/x", "g4", "main", None, False),
    ("무관한 명령", "npm test && cat README.md", "g5", "main", None, False),
    ("gh pr view", "gh pr view --json number", "g6", "main", None, False),
    ("문자열 안의 단어는 명령이 아니다", "echo 'git checkout -b nope'", "g7", "main", None, False),
]


def check_cases() -> int:
    failures = 0
    print("[1] 규칙 밖은 막고, 정상 작업은 지나간다")
    with tempfile.TemporaryDirectory() as tmp:
        use_state_dir(tmp)
        for label, cmd, session, branch, merged, should_deny in CASES:
            with_world(branch, merged)
            reason = guard.judge(payload(cmd, session))
            if should_deny and reason is None:
                print(f"  [실패] 막혔어야 하는데 지나갔다 — {label}: {cmd!r}")
                failures += 1
            elif not should_deny and reason is not None:
                print(f"  [실패] 지나갔어야 하는데 막혔다 — {label}: {reason!r}")
                failures += 1
    return failures


def check_reason() -> int:
    """차단 사유에 근거(규칙 파일)와 여는 법(열쇠)이 함께 실려야 한다."""
    failures = 0
    print("[2] 차단 사유에 근거와 여는 법이 실린다")
    with tempfile.TemporaryDirectory() as tmp:
        use_state_dir(tmp)
        with_world("main")
        reason = guard.judge(payload("git checkout -b bad", "r1"))
        for needle in (guard.RULE_PATH, guard.UNLOCK_FILENAME, "승인"):
            if needle not in (reason or ""):
                print(f"  [실패] 사유에 `{needle}` 이(가) 없다: {reason!r}")
                failures += 1
    return failures


def check_process() -> int:
    """훅을 실제 프로세스로 돌려 입출력 규약(deny JSON, 열쇠 소비)을 확인한다."""
    failures = 0
    print("[3] 프로세스로 돌려도 같은 판정을 내고, 열쇠는 한 번만 통한다")
    with tempfile.TemporaryDirectory() as raw:
        root = pathlib.Path(raw)
        hooks = root / ".claude" / "hooks"
        hooks.mkdir(parents=True)
        (hooks / "branch-guard.py").write_bytes(GUARD.read_bytes())

        def run(p):
            # 훅은 UTF-8 로 내보낸다. 읽는 쪽도 맞춰야 Windows 기본 코드페이지에서 깨지지 않는다.
            proc = subprocess.run([sys.executable, str(hooks / "branch-guard.py")],
                                  input=json.dumps(p), capture_output=True,
                                  encoding="utf-8", errors="replace", timeout=30)
            return proc.stdout.strip()

        def decision(out):
            return json.loads(out)["hookSpecificOutput"]["permissionDecision"] if out else None

        # 형식 위반은 바깥 세계 없이도 막힌다.
        out = run(payload("git checkout -b nope", "p1", raw))
        if decision(out) != "deny":
            print(f"  [실패] 프로세스 실행에서 deny 가 아니다: {out!r}")
            failures += 1

        # 무관한 명령은 출력이 없다(판정하지 않는다).
        out = run(payload("ls -la", "p2", raw))
        if out:
            print(f"  [실패] 무관한 명령에 출력이 있다: {out!r}")
            failures += 1

        # 열쇠가 있으면 allow 를 내고 열쇠를 지운다. 두 번째는 다시 deny.
        key = root / ".claude" / "branch-unlock"
        key.write_text("시험용 열쇠\n", encoding="utf-8")
        out = run(payload("git checkout -b nope", "p3", raw))
        if decision(out) != "allow":
            print(f"  [실패] 열쇠가 있는데 allow 가 아니다: {out!r}")
            failures += 1
        if key.exists():
            print("  [실패] 열쇠를 쓰고도 파일이 남아 있다 — 일회용이 아니다")
            failures += 1
        out = run(payload("git checkout -b nope", "p3", raw))
        if decision(out) != "deny":
            print(f"  [실패] 열쇠를 쓴 뒤 두 번째가 막히지 않았다: {out!r}")
            failures += 1

        # 세션 기록이 .claude/state/branch-guard/ 아래에 남는다.
        run(payload("git checkout -b feat/recorded-one", "p4", raw))
        state = root / ".claude" / "state" / "branch-guard" / "p4"
        if not state.exists() or state.read_text(encoding="utf-8").strip() != "feat/recorded-one":
            print(f"  [실패] 세션 기록이 없거나 다르다: {state}")
            failures += 1
    return failures


def main() -> int:
    failures = check_cases() + check_reason() + check_process()
    if failures:
        print(f"\n실패 {failures}건")
        return 1
    print("\n모두 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
