#!/usr/bin/env python3
"""terms-guard 훅의 판단이 맞는지 확인하는 시험.

실행: python3 .claude/hooks/terms-guard.test.py

두 방향을 모두 본다. 금지어가 든 글은 반드시 막혀야 하고(막히지 않으면 훅이 있으나
마나다), 정한 말만 쓴 글과 용어표가 없는 저장소는 반드시 지나가야 한다(멀쩡한 작업이
막히면 다음 세션이 훅을 꺼 버린다).
"""

import json
import os
import pathlib
import subprocess
import sys
import tempfile

for _stream in (sys.stdout, sys.stderr):  # Windows 콘솔(cp949)에서도 한글이 깨지지 않게
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

HERE = pathlib.Path(__file__).parent
GUARD = HERE / "terms-guard.py"

SAMPLE = {
    "agreed": [{"term": "Raw data", "meaning": "가공하지 않은 측정 기록"}],
    "banned": [
        {"word": "원값", "instead": "Raw data", "decision": "2026-11-13 사용자 지시"},
        {"word": "모터 없음", "instead": "운전-정지", "decision": "2026-11-13"},
        {"word": "판", "pattern": "(?<![가-힣])판(?![가-힣])", "decision": "2026-11-13"},
        {"word": "정본", "instead": "시험 문서", "decision": "2026-11-13"},
    ],
    "exclude": ["docs/lessons.md"],
}

# (설명, 도구, 도구 입력, 막혀야 하나). {ROOT} · {OUT} 은 실행 때 채운다.
CASES = [
    ("금지어를 저장소 문서에 쓴다", "Write", {"file_path": "docs/report.md", "content": "원값 그래프"}, True),
    ("띄어쓰기가 든 금지어", "Write", {"file_path": "docs/report.md", "content": "조건: 모터 없음"}, True),
    ("Edit 의 새 글에 금지어", "Edit", {"file_path": "docs/report.md", "old_string": "a", "new_string": "정본에서 읽기"}, True),
    ("노트북 칸에 금지어", "NotebookEdit", {"notebook_path": "a.ipynb", "new_source": "원값 = 1"}, True),
    ("한 글자 금지어가 홀로 쓰였다", "Write", {"file_path": "docs/r.md", "content": "v2 판 기준"}, True),
    ("한 글자 금지어가 다른 낱말 안에 있다", "Write", {"file_path": "docs/r.md", "content": "판정 합격, 판단 보류"}, False),
    ("정한 말만 쓴 글", "Write", {"file_path": "docs/r.md", "content": "Raw data · 운전-정지 · 시험 문서"}, False),
    ("Edit 는 지우는 글을 보지 않는다", "Edit", {"file_path": "docs/r.md", "old_string": "원값", "new_string": "Raw data"}, False),
    ("제외 경로", "Write", {"file_path": "docs/lessons.md", "content": "원값 이라는 말을 쓰지 않기로 했다"}, False),
    ("용어표 자신", "Write", {"file_path": ".claude/terms.json", "content": "{\"banned\":[{\"word\":\"원값\"}]}"}, False),
    ("훅 폴더", "Write", {"file_path": ".claude/hooks/x.test.py", "content": "원값"}, False),
    ("저장소 밖(메모리 · 임시 폴더)은 Write 로 보지 않는다", "Write", {"file_path": "{OUT}/note.md", "content": "원값"}, False),
    ("저장소 밖 파일도 Artifact 게시는 본다", "Artifact", {"file_path": "{OUT}/bad.html"}, True),
    ("깨끗한 Artifact 게시", "Artifact", {"file_path": "{OUT}/good.html"}, False),
    ("Artifact 의 함께 올리는 파일", "Artifact", {"file_path": "{OUT}/good.html", "files": {"data.json": "{OUT}/bad.json"}}, True),
    ("Artifact 읽기 동작은 보지 않는다", "Artifact", {"action": "read", "url": "https://claude.ai/artifact/x"}, False),
    ("훅이 보지 않는 도구", "Bash", {"command": "echo 원값"}, False),
]


def make_repo(tmp: pathlib.Path, terms) -> pathlib.Path:
    (tmp / ".claude" / "hooks").mkdir(parents=True, exist_ok=True)
    (tmp / ".claude" / "hooks" / "terms-guard.py").write_bytes(GUARD.read_bytes())
    if terms is not None:
        (tmp / ".claude" / "terms.json").write_text(json.dumps(terms, ensure_ascii=False), encoding="utf-8")
    return tmp


def run_hook(payload: dict, root: pathlib.Path, env=None):
    proc = subprocess.run(
        [sys.executable, str(root / ".claude" / "hooks" / "terms-guard.py")],
        input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        capture_output=True,
        env=env,
        timeout=20,
    )
    return proc.returncode, proc.stdout.decode("utf-8", errors="replace").strip()


def decision_of(out: str):
    return json.loads(out)["hookSpecificOutput"]["permissionDecision"] if out else None


def fill(value, root, out_dir):
    if isinstance(value, str):
        return value.replace("{ROOT}", str(root)).replace("{OUT}", str(out_dir))
    if isinstance(value, dict):
        return {k: fill(v, root, out_dir) for k, v in value.items()}
    return value


def check_cases() -> int:
    failures = 0
    print("[1] 금지어는 막고, 정한 말과 제외 경로는 지나간다")
    with tempfile.TemporaryDirectory() as raw, tempfile.TemporaryDirectory() as outside:
        root = make_repo(pathlib.Path(raw), SAMPLE)
        out_dir = pathlib.Path(outside)
        (out_dir / "bad.html").write_text("<td>원값</td>", encoding="utf-8")
        (out_dir / "good.html").write_text("<td>Raw data</td>", encoding="utf-8")
        (out_dir / "bad.json").write_text("{\"x\": \"정본\"}", encoding="utf-8")
        for label, tool, tool_input, should_deny in CASES:
            code, out = run_hook({"tool_name": tool, "tool_input": fill(tool_input, root, out_dir)}, root)
            decision = decision_of(out)
            if code != 0:
                print(f"  [실패] 훅이 종료 코드 {code} 로 끝났다: {label}")
                failures += 1
            elif should_deny and decision != "deny":
                print(f"  [실패] 막혔어야 하는데 지나갔다: {label}: {out!r}")
                failures += 1
            elif not should_deny and decision == "deny":
                print(f"  [실패] 지나갔어야 하는데 막혔다: {label}: {out!r}")
                failures += 1
    return failures


def check_reason() -> int:
    """차단 사유에 찾은 말 · 대신 쓸 말 · 근거 · 푸는 법이 실려야 한다."""
    failures = 0
    print("[2] 차단 사유에 찾은 말 · 대신 쓸 말 · 근거 · 푸는 법이 실린다")
    with tempfile.TemporaryDirectory() as raw:
        root = make_repo(pathlib.Path(raw), SAMPLE)
        _, out = run_hook({"tool_name": "Write", "tool_input": {
            "file_path": "docs/r.md", "content": "첫 줄\n원값 과 v2 판"}}, root)
        reason = json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"]
        for needed, what in [("원값", "찾은 말"), ("Raw data", "대신 쓸 말"), ("2행", "줄 번호"),
                             ("2026-11-13 사용자 지시", "근거"), ("사용자에게 물으십시오", "대신 쓸 말이 없을 때 안내"),
                             (".claude/terms.json", "푸는 법")]:
            if needed not in reason:
                print(f"  [실패] 차단 사유에 {what}이(가) 없다: {reason!r}")
                failures += 1
    return failures


def check_absent_list() -> int:
    failures = 0
    print("[3] 용어표가 없거나 깨졌으면 훅은 물러난다")
    for label, terms in [("용어표 없음", None), ("용어표 깨짐", "__broken__"), ("금지어 없음", {"agreed": []})]:
        with tempfile.TemporaryDirectory() as raw:
            root = make_repo(pathlib.Path(raw), None if terms == "__broken__" else terms)
            if terms == "__broken__":
                (root / ".claude" / "terms.json").write_text("{ 이건 JSON 이 아니다", encoding="utf-8")
            _, out = run_hook({"tool_name": "Write", "tool_input": {"file_path": "a.md", "content": "원값"}}, root)
            if out:
                print(f"  [실패] {label}: 판정을 냈다: {out!r}")
                failures += 1
    return failures


def check_windows_encoding() -> int:
    """파이프 기본 인코딩이 cp949 여도 한글 금지어를 찾아 UTF-8 판정을 낸다(한국어 Windows 회귀 방지)."""
    failures = 0
    print("[4] 파이프 기본 인코딩이 cp949 여도 판정을 낸다")
    env = {**os.environ, "PYTHONIOENCODING": "cp949", "PYTHONUTF8": "0"}
    with tempfile.TemporaryDirectory() as raw:
        root = make_repo(pathlib.Path(raw), SAMPLE)
        code, out = run_hook({"tool_name": "Write", "tool_input": {"file_path": "보고/현황.md", "content": "원값"}}, root, env)
        if code != 0 or decision_of(out) != "deny":
            print(f"  [실패] 종료 코드 {code}, UTF-8 deny 판정이 아니다: {out[:200]!r}")
            failures += 1
    return failures


if __name__ == "__main__":
    total = check_cases() + check_reason() + check_absent_list() + check_windows_encoding()
    if total:
        print(f"\n실패 {total}건")
        sys.exit(1)
    print(f"통과: {len(CASES)}가지 상황에서 금지어만 차단됐고, 차단 사유에 찾은 말 · 대신 쓸 말 · "
          f"근거 · 푸는 법이 실렸으며, 용어표가 없거나 깨진 저장소에서는 판정을 내지 않았고, "
          f"cp949 파이프 조건(한국어 Windows)에서도 판정을 UTF-8 로 냈습니다.")
