#!/usr/bin/env python3
"""retired-guard 훅의 판단이 맞는지 확인하는 시험.

실행: python3 .claude/hooks/retired-guard.test.py

두 방향을 모두 본다. 폐기된 스킬·경로는 반드시 막혀야 하고(막히지 않으면 훅이
있으나 마나다), 폐기되지 않은 것과 목록이 없는 저장소는 반드시 지나가야 한다
(멀쩡한 작업이 막히면 다음 세션이 훅을 꺼 버린다).
"""

import importlib.util
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
GUARD = HERE / "retired-guard.py"

spec = importlib.util.spec_from_file_location("retired_guard", GUARD)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

SAMPLE = {
    "decision_log": "decisions.md",
    "skills": [{
        "name": "progress-dashboard",
        "decision": "decisions.md 결정 1 (2026-08-27)",
        "instead": "workflows/board/build.mjs 로 정본을 조립한다",
    }],
    "paths": [
        {"glob": "dashboard.config.json", "decision": "결정 1", "instead": "workflows/board/board.json"},
        {"glob": "meta", "decision": "결정 1", "instead": "workflows/board/cards/"},
        {"glob": "docs/*.dashboard.html", "decision": "결정 1", "instead": "정본 하나"},
    ],
}

# (도구, 도구 입력, 막혀야 하나)
CASES = [
    ("폐기된 스킬", "Skill", {"skill": "progress-dashboard"}, True),
    ("폐기된 스킬(플러그인 접두어)", "Skill", {"skill": "someplugin:progress-dashboard"}, True),
    ("멀쩡한 스킬", "Skill", {"skill": "frontend-design"}, False),
    ("폐기된 파일을 다시 쓴다", "Write", {"file_path": "dashboard.config.json"}, True),
    ("폐기된 파일을 절대경로로 쓴다", "Write", {"file_path": "{ROOT}/dashboard.config.json"}, True),
    ("폐기된 폴더 아래에 쓴다", "Write", {"file_path": "meta/work-packages.md"}, True),
    ("폐기된 폴더 자체", "Edit", {"file_path": "meta"}, True),
    ("와일드카드에 걸리는 경로", "Write", {"file_path": "docs/team.dashboard.html"}, True),
    ("이름만 비슷한 멀쩡한 경로", "Write", {"file_path": "metadata/readme.md"}, False),
    ("보관본을 읽고 고치는 것은 막지 않는다", "Edit", {"file_path": "archive/2026-08/meta/x.md"}, False),
    ("무관한 파일", "Write", {"file_path": "docs/lessons.md"}, False),
    ("훅이 보지 않는 도구", "Bash", {"command": "rm -rf meta"}, False),
]


def run_hook(payload: dict, cwd: pathlib.Path) -> str:
    """훅을 실제 프로세스로 돌려 표준 출력을 받는다."""
    proc = subprocess.run(
        [sys.executable, str(cwd / ".claude" / "hooks" / "retired-guard.py")],
        input=json.dumps(payload),
        capture_output=True,
        # 훅은 UTF-8 로 내보낸다. 읽는 쪽도 맞춰야 Windows 기본 코드페이지에서 깨지지 않는다.
        encoding="utf-8",
        errors="replace",
        timeout=20,
    )
    return proc.stdout.strip()


# 한국어 Windows 와 같은 조건 — 파이프의 기본 인코딩이 cp949 다. Linux 에서도 이 환경
# 변수로 같은 조건을 만들 수 있어, 어디서 돌려도 같은 회귀를 잡는다.
CP949_ENV = {**os.environ, "PYTHONIOENCODING": "cp949", "PYTHONUTF8": "0"}

# 실제 폐기 목록처럼 근거에 줄표(—)가 들고, 경로가 한글인 목록.
SAMPLE_KO = {
    "decision_log": "decisions.md",
    "skills": [{
        "name": "progress-dashboard",
        "decision": "decisions.md 결정 1 (2026-08-27) — 현황판을 정본 하나로 통일한다",
        "instead": "workflows/board/build.mjs 로 정본을 조립한다",
    }],
    "paths": [{"glob": "보관함", "decision": "결정 1 — 정본 하나", "instead": "workflows/board/"}],
}


def run_hook_cp949(cwd: pathlib.Path, payload=None, args=()):
    """훅을 파이프 기본 인코딩이 cp949 인 조건으로 돌려 (종료 코드, 표준 출력 바이트)를 낸다.

    Claude Code 는 UTF-8 JSON 을 넘기고 UTF-8 로 읽는다. 훅이 스스로 입출력을 UTF-8 로
    고정하지 않으면 판정문의 줄표(—)를 내보내다 죽거나(exit 1 — Claude Code 는 판정 없이
    도구를 그대로 실행한다) 입력의 한글 경로가 깨져 대조가 빗나간다(2026-11-09 실측).
    """
    proc = subprocess.run(
        [sys.executable, str(cwd / ".claude" / "hooks" / "retired-guard.py"), *args],
        input=json.dumps(payload or {}, ensure_ascii=False).encode("utf-8"),
        capture_output=True,
        env=CP949_ENV,
        timeout=20,
    )
    return proc.returncode, proc.stdout


def check_windows_encoding() -> int:
    """파이프 기본 인코딩이 cp949 여도 훅이 판정과 안내를 내는지 본다(한국어 Windows 회귀 방지)."""
    failures = 0
    print("[4] 파이프 기본 인코딩이 cp949 여도 판정과 안내를 낸다")
    with tempfile.TemporaryDirectory() as raw:
        root = make_repo(pathlib.Path(raw), SAMPLE_KO)
        for label, payload in [
            # 근거의 줄표가 판정문에 실린다. 고치기 전에는 여기서 죽었다.
            ("줄표가 든 근거의 스킬 차단", {"tool_name": "Skill", "tool_input": {"skill": "progress-dashboard"}}),
            # 고치기 전에는 입력의 한글이 깨져 대조가 빗나가고, 조용히 통과했다.
            ("한글 경로에 쓰기", {"tool_name": "Write", "tool_input": {"file_path": "보관함/메모.md"}}),
        ]:
            code, out = run_hook_cp949(root, payload)
            try:
                hso = json.loads(out.decode("utf-8"))["hookSpecificOutput"] if code == 0 else None
            except Exception:  # noqa: BLE001 — 무엇이든 실패로 보고한다
                hso = None
            if not hso or hso["permissionDecision"] != "deny" or "—" not in hso["permissionDecisionReason"]:
                print(f"  [실패] {label}: 종료 코드 {code}, UTF-8 deny 판정이 아니다 — {out[:200]!r}")
                failures += 1

        # 세션 시작 안내 모드도 줄표를 내보낸다. 죽으면 안내가 통째로 사라진다.
        code, out = run_hook_cp949(root, args=("--announce",))
        try:
            text = out.decode("utf-8") if code == 0 else ""
        except UnicodeDecodeError:
            text = ""
        if "—" not in text or "보관함" not in text:
            print(f"  [실패] 안내 모드: 종료 코드 {code}, UTF-8 안내문이 아니다 — {out[:200]!r}")
            failures += 1
    return failures


def decision_of(out: str):
    if not out:
        return None
    return json.loads(out)["hookSpecificOutput"]["permissionDecision"]


def make_repo(tmp: pathlib.Path, retired) -> pathlib.Path:
    """훅 사본과 (있으면) 폐기 목록을 갖춘 가짜 저장소를 만든다."""
    (tmp / ".claude" / "hooks").mkdir(parents=True, exist_ok=True)
    (tmp / ".claude" / "hooks" / "retired-guard.py").write_bytes(GUARD.read_bytes())
    if retired is not None:
        (tmp / ".claude" / "retired.json").write_text(
            json.dumps(retired, ensure_ascii=False), encoding="utf-8")
    return tmp


def check_cases() -> int:
    failures = 0
    print("[1] 폐기된 것은 막고, 멀쩡한 것은 지나간다")
    with tempfile.TemporaryDirectory() as raw:
        root = make_repo(pathlib.Path(raw), SAMPLE)
        for label, tool, tool_input, should_deny in CASES:
            filled = {k: v.replace("{ROOT}", str(root)) if isinstance(v, str) else v
                      for k, v in tool_input.items()}
            out = run_hook({"tool_name": tool, "tool_input": filled}, root)
            decision = decision_of(out)
            if should_deny and decision != "deny":
                print(f"  [실패] 막혔어야 하는데 지나갔다 — {label}: {out!r}")
                failures += 1
            elif not should_deny and decision == "deny":
                print(f"  [실패] 지나갔어야 하는데 막혔다 — {label}: {out!r}")
                failures += 1
    return failures


def check_reason() -> int:
    """차단 사유에 근거와 대안이 함께 실려야 한다 — 이것이 없으면 막힌 세션이
    우회로를 스스로 발명하게 되고, 그것이 이 규칙이 막으려던 사고 그 자체다."""
    failures = 0
    print("[2] 차단 사유에 근거·대안·되살리는 법이 함께 실린다")
    with tempfile.TemporaryDirectory() as raw:
        root = make_repo(pathlib.Path(raw), SAMPLE)
        out = run_hook({"tool_name": "Skill", "tool_input": {"skill": "progress-dashboard"}}, root)
        reason = json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"]
        for needed, what in [
            ("결정 1", "폐기 근거"),
            ("workflows/board/build.mjs", "대신 쓸 것"),
            ("decisions.md", "결정 로그 위치"),
            (".claude/retired.json", "되살리는 방법"),
        ]:
            if needed not in reason:
                print(f"  [실패] 차단 사유에 {what}이(가) 없다: {reason!r}")
                failures += 1
    return failures


def check_absent_list() -> int:
    """목록이 없거나 깨진 저장소에서는 아무 일도 하지 않아야 한다."""
    failures = 0
    print("[3] 폐기 목록이 없으면 훅은 물러난다")
    for label, retired in [("목록 파일이 없다", None), ("목록이 깨졌다", "__broken__")]:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            make_repo(root, None)
            if retired == "__broken__":
                (root / ".claude" / "retired.json").write_text("{ 이건 JSON 이 아니다", encoding="utf-8")
            out = run_hook({"tool_name": "Skill", "tool_input": {"skill": "progress-dashboard"}}, root)
            if out:
                print(f"  [실패] {label}: 판정을 냈다 — {out!r}")
                failures += 1
    return failures


if __name__ == "__main__":
    total = check_cases() + check_reason() + check_absent_list() + check_windows_encoding()
    if total:
        print(f"\n실패 {total}건")
        sys.exit(1)
    print(f"✓ 통과 — {len(CASES)}가지 상황에서 폐기된 스킬·경로만 차단됐고, "
          f"차단 사유에 근거·대안·결정 로그·되살리는 법이 모두 실렸으며, "
          f"폐기 목록이 없거나 깨진 저장소에서는 훅이 아무 판정도 내지 않았으며, "
          f"파이프 기본 인코딩이 cp949 인 조건(한국어 Windows)에서도 판정과 안내를 UTF-8 로 냈습니다.")
