#!/usr/bin/env python3
"""dev-verify-reminder 훅의 판단이 맞는지 확인하는 시험.

실행: python3 .claude/hooks/dev-verify-reminder.test.py

두 방향을 모두 본다. 개발 DB 에 마이그레이션을 적용한 직후에는 반드시 검증 목록이 세션에
들어가야 하고(들어가지 않으면 약속이 다시 글로만 남는다), 그 밖의 도구·프로젝트에서는 아무것도
내지 않아야 한다(검증 중에 매번 끼어들면 사람이 훅을 꺼 버린다).

훅은 자기 위치로 저장소 뿌리를 잡으므로, 임시 폴더에 훅을 복사하고 dev-db.json 과 절차 문서를
꾸며 돌린다. 진짜 저장소의 파일은 건드리지 않는다.
"""

import json
import os
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
HOOK = HERE / "dev-verify-reminder.py"

# 한국어 Windows 와 같은 조건 — 파이프의 기본 인코딩이 cp949 다(2026-11-09 실측 사고).
CP949_ENV = {**os.environ, "PYTHONIOENCODING": "cp949", "PYTHONUTF8": "0"}

DESKTOP = "mcp__2697e772-cf1b-40ff-9116-9515d43dc978__apply_migration"
DOC_STEPS = "1. 실데이터로 실행하고 되돌린다 — 표지 안의 문장"
# 실제 절차 문서처럼 번호 목록 안에 들여 쓴 구간 — 훅은 들여쓰기를 걷어 내고 넣어야 한다.
DOC = (f"# 절차\n\n앞 문단\n\n1. 단계\n\n   <!-- session-verify:start -->\n   {DOC_STEPS}\n"
       f"   <!-- session-verify:end -->\n\n뒤 문단\n")
CFG = {
    "dev_projects": [{"id": "p-dev", "name": "시험-dev"}],
    "verify_steps": {"doc": "docs/proc.md", "block": "session-verify"},
}


def make_root(tmp, cfg=CFG, doc=DOC):
    root = pathlib.Path(tmp)
    hooks = root / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    (hooks / HOOK.name).write_bytes(HOOK.read_bytes())
    if cfg is not None:
        (root / ".claude" / "dev-db.json").write_text(
            cfg if isinstance(cfg, str) else json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    if doc is not None:
        (root / "docs").mkdir()
        (root / "docs" / "proc.md").write_text(doc, encoding="utf-8")
    return hooks / HOOK.name


def run(script, payload):
    """cp949 조건으로 돌려 (종료 코드, 넣은 문맥 또는 None)을 낸다."""
    proc = subprocess.run(
        [sys.executable, str(script)],
        input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        capture_output=True, env=CP949_ENV, timeout=30,
    )
    out = proc.stdout.decode("utf-8").strip()
    if not out:
        return proc.returncode, None
    data = json.loads(out)
    assert data["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    return proc.returncode, data["hookSpecificOutput"]["additionalContext"]


def apply_payload(tool=DESKTOP, project="p-dev"):
    return {"hook_event_name": "PostToolUse", "tool_name": tool,
            "tool_input": {"project_id": project, "name": "20261119000000_x", "query": "select 1;"},
            "tool_response": {"success": True}}


def main() -> int:
    failures = 0

    def fail(msg):
        nonlocal failures
        print(f"  [실패] {msg}")
        failures += 1

    print("[발동] 개발 DB 적용 직후에는 문서의 목록을 넣는다 (서버 이름 무관, cp949 조건)")
    for tool in (DESKTOP, "mcp__Supabase__apply_migration", "mcp__claude_ai_Supabase__apply_migration"):
        with tempfile.TemporaryDirectory() as tmp:
            code, ctx = run(make_root(tmp), apply_payload(tool))
            if code != 0:
                fail(f"{tool}: 종료 코드 {code}")
            elif not ctx or DOC_STEPS not in ctx or "시험-dev" not in ctx or "20261119000000_x" not in ctx:
                fail(f"{tool}: 문서의 목록·대상·이름이 문맥에 없다: {ctx!r}")
            elif "앞 문단" in ctx or "뒤 문단" in ctx:
                fail(f"{tool}: 표지 밖의 문장까지 옮겼다: {ctx!r}")
            elif f"\n{DOC_STEPS}" not in ctx:
                fail(f"{tool}: 구간의 들여쓰기를 걷어 내지 않았다: {ctx!r}")

    print("[침묵] 개발 DB 적용이 아니면 아무것도 내지 않는다")
    with tempfile.TemporaryDirectory() as tmp:
        script = make_root(tmp)
        for payload, why in (
            (apply_payload(project="p-live"), "위임 밖 프로젝트"),
            (apply_payload(project=None), "프로젝트 없음"),
            (apply_payload(tool=DESKTOP.replace("apply_migration", "execute_sql")), "조회·검증 통로"),
            (apply_payload(tool="Bash"), "다른 도구"),
            (apply_payload(tool="apply_migration"), "mcp 가 아닌 같은 이름"),
        ):
            code, ctx = run(script, payload)
            if code != 0 or ctx is not None:
                fail(f"{why}: 침묵해야 하는데 냈다 (코드 {code}): {ctx!r}")
    for cfg, why in ((None, "dev-db.json 없음"), ("{깨짐", "dev-db.json 깨짐")):
        with tempfile.TemporaryDirectory() as tmp:
            code, ctx = run(make_root(tmp, cfg=cfg), apply_payload())
            if code != 0 or ctx is not None:
                fail(f"{why}: 침묵해야 하는데 냈다 (코드 {code}): {ctx!r}")

    print("[대비] 문서·표지를 못 찾으면 기본 목록과 경고를 낸다 — 조용히 사라지지 않는다")
    for doc, why in ((None, "문서 없음"), ("# 표지 없는 문서\n", "표지 없음")):
        with tempfile.TemporaryDirectory() as tmp:
            code, ctx = run(make_root(tmp, doc=doc), apply_payload())
            if code != 0 or not ctx or "주의:" not in ctx or "dev-verified" not in ctx:
                fail(f"{why}: 기본 목록과 경고가 없다 (코드 {code}): {ctx!r}")
            # 사람에게 로그인을 부탁하지 않는다 — 로그인이 꼭 필요하면 지휘 세션에 먼저 묻는다(2026-11-19).
            elif "로그인해 주세요" in ctx or "지휘 세션" not in ctx:
                fail(f"{why}: 사람에게 로그인을 부탁하거나 지휘 세션에 묻는 안내가 없다: {ctx!r}")

    print("[원칙] 머리 안내문은 사람 몫을 라벨·머지로만 적는다")
    with tempfile.TemporaryDirectory() as tmp:
        code, ctx = run(make_root(tmp), apply_payload())
        head = (ctx or "").split("\n\n", 1)[0]
        if "로그인 비밀번호 입력" in head or "지휘 세션" not in head:
            fail(f"머리 안내문이 사람에게 로그인을 남기거나 지휘 세션을 말하지 않는다: {head!r}")

    print("[입력 깨짐] 읽을 수 없는 입력에는 0 으로 물러난다")
    with tempfile.TemporaryDirectory() as tmp:
        proc = subprocess.run([sys.executable, str(make_root(tmp))], input=b"not json",
                              capture_output=True, env=CP949_ENV, timeout=30)
        if proc.returncode != 0 or proc.stdout.strip():
            fail(f"깨진 입력에 0 으로 조용히 물러나지 않았다: {proc.returncode} {proc.stdout!r}")

    return failures


if __name__ == "__main__":
    total = main()
    if total:
        print(f"\n실패 {total}건")
        sys.exit(1)
    print("✓ 통과 — 개발 DB 에 적용한 직후에는 서버 이름과 무관하게 문서의 검증 목록이 세션에 들어가고, "
          "그 밖의 도구·프로젝트·설정 없음에는 침묵했으며, 문서를 못 찾으면 기본 목록과 경고를 냈고, "
          "파이프 기본 인코딩이 cp949 인 조건에서도 종료 코드 0 과 UTF-8 JSON 을 냈습니다.")
