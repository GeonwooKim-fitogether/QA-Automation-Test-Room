#!/usr/bin/env python3
"""report-format-guard 훅과 생성기 견본(report-render.py)이 맞게 동작하는지 확인하는 시험.

실행: python3 .claude/hooks/report-format-guard.test.py

세 방향을 본다. 양식 밖의 표는 반드시 막혀야 하고, 생성기가 그린 표와 보고가 아닌 표
(none)와 양식 폴더가 없는 저장소는 반드시 지나가야 하며, 생성기는 양식에 맞지 않는 값을
그리지 않아야 한다.
"""

import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

HERE = pathlib.Path(__file__).parent
REPO = HERE.parent.parent
GUARD = HERE / "report-format-guard.py"
RENDER = REPO / ".claude" / "workflows" / "report-render.py"
FORMAT_FILE = REPO / "templates" / "report-formats" / "test-status.json"
DATA_FILE = REPO / "templates" / "report-formats" / "test-status.data.example.json"

spec = importlib.util.spec_from_file_location("report_render", RENDER)
render_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(render_mod)

# templates/ 는 창고에만 있고 아래 저장소로 내려가지 않는다. 그곳에서도 이 시험이 돌도록
# 견본이 없으면 아래 사본을 쓴다(창고에서는 견본 파일 자체를 시험한다).
FALLBACK_FMT = {'$comment': '보고 양식 견본: 시험별 현황 표. 쓰는 저장소는 이 파일을 .claude/report-formats/test-status.json 으로 복사하고, 사용자와 합의한 뒤 PR 로 넣는다. 규칙: .claude/rules/report-format.md 3절', 'name': 'test-status', 'title': '시험별 현황', 'version': 1, 'header': {'subject': '시험 이름', 'subject_style': '큰 글씨, 왼쪽', 'conclusion': '결론', 'conclusion_style': '같은 줄 오른쪽 끝, 한 줄'}, 'columns': ['대분류', '중분류', '내용', '합의', '진행'], 'category_column': '대분류', 'categories': ['목적', '목표', '시험 조건', '절차', '합격 기준', '결과', '이슈', '다음'], 'column_values': {'합의': ['완료', '진행', '예정', '미정'], '진행': ['완료', '진행', '예정', '미정']}, 'cell': {'max_lines': 2, 'no_sentence': True}, 'history': [{'version': 1, 'date': '2026-11-13', 'change': '처음 합의. 열 다섯 · 대분류 여덟 · 머리 배치', 'agreed_by': '사용자', 'pr': 'Template-repository#109'}]}
FALLBACK_DATA = {'$comment': 'report-render.py 에 넘기는 값 파일 견본. 칸 이름은 양식의 columns 와 같고, 대분류는 양식의 categories 안에서 고른다. 빠진 대분류는 생성기가 빈 줄로 남긴다(틀 고정).', 'format': 'test-status', 'version': 1, 'subject': '시험 A', 'conclusion': '진행', 'rows': [{'대분류': '목적', '중분류': '', '내용': '수신 성능 확인', '합의': '완료', '진행': '완료'}, {'대분류': '목표', '중분류': '', '내용': '24대 합격', '합의': '완료', '진행': '진행'}, {'대분류': '시험 조건', '중분류': '신호원', '내용': '장면 1', '합의': '완료', '진행': '완료'}, {'대분류': '시험 조건', '중분류': '배치', '내용': '24대', '합의': '완료', '진행': '완료'}, {'대분류': '결과', '중분류': '합격', '내용': '20대', '합의': '', '진행': '진행'}]}
FMT = json.loads(FORMAT_FILE.read_text(encoding="utf-8")) if FORMAT_FILE.exists() else FALLBACK_FMT
DATA = json.loads(DATA_FILE.read_text(encoding="utf-8")) if DATA_FILE.exists() else FALLBACK_DATA
GOOD = render_mod.render(FMT, DATA)


def table(head, rows, fmt="test-status", version="1"):
    attrs = "" if fmt is None else f' data-report-format="{fmt}" data-report-version="{version}"'
    h = "<thead><tr>" + "".join(f"<th>{c}</th>" for c in head) + "</tr></thead>"
    b = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table{attrs}>{h}<tbody>{b}</tbody></table>"


COLS = FMT["columns"]
CATS = FMT["categories"]
FULL = [[c, "", "", "", ""] for c in CATS]

# (설명, HTML, 막혀야 하나)
HTML_CASES = [
    ("생성기가 그린 표", GOOD, False),
    ("손으로 짰지만 양식과 같은 표", table(COLS, FULL), False),
    ("보고가 아닌 표(none)", table(["항목", "값"], [["a", "1"]], fmt="none"), False),
    ("표가 없는 페이지", "<p>본문</p>", False),
    ("양식 표시가 없는 새 표", table(COLS, FULL, fmt=None), True),
    ("없는 양식 이름", table(COLS, FULL, fmt="weekly"), True),
    ("버전이 다르다", table(COLS, FULL, version="2"), True),
    ("칸 이름을 바꿨다", table(["대분류", "세부", "내용", "합의", "진행"], FULL), True),
    ("칸 순서를 바꿨다", table(["대분류", "중분류", "내용", "진행", "합의"], FULL), True),
    ("대분류 하나를 뺐다", table(COLS, FULL[1:]), True),
    ("대분류 순서를 바꿨다", table(COLS, [FULL[1], FULL[0]] + FULL[2:]), True),
    ("칸에 문장", table(COLS, [[CATS[0], "", "수신 성능을 확인한다.", "", ""]] + FULL[1:]), True),
    ("한 칸이 3줄", table(COLS, [[CATS[0], "", "a<br>b<br>c", "", ""]] + FULL[1:]), True),
    ("한 칸이 2줄", table(COLS, [[CATS[0], "", "a<br>b", "", ""]] + FULL[1:]), False),
    ("상태 칸에 정하지 않은 값", table(COLS, [[CATS[0], "", "", "끝남", ""]] + FULL[1:]), True),
]


def make_repo(tmp: pathlib.Path, with_formats=True) -> pathlib.Path:
    (tmp / ".claude" / "hooks").mkdir(parents=True, exist_ok=True)
    (tmp / ".claude" / "hooks" / "report-format-guard.py").write_bytes(GUARD.read_bytes())
    if with_formats:
        (tmp / ".claude" / "report-formats").mkdir()
        (tmp / ".claude" / "report-formats" / "test-status.json").write_text(
            json.dumps(FMT, ensure_ascii=False), encoding="utf-8")
    return tmp


def run_hook(payload: dict, root: pathlib.Path, env=None):
    proc = subprocess.run(
        [sys.executable, str(root / ".claude" / "hooks" / "report-format-guard.py")],
        input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        capture_output=True, env=env, timeout=20,
    )
    return proc.returncode, proc.stdout.decode("utf-8", errors="replace").strip()


def decision_of(out: str):
    return json.loads(out)["hookSpecificOutput"]["permissionDecision"] if out else None


def check_html_cases() -> int:
    failures = 0
    print("[1] 양식 밖의 표는 막고, 양식대로 그린 표 · none 표는 지나간다 (Write 와 Artifact 모두)")
    with tempfile.TemporaryDirectory() as raw, tempfile.TemporaryDirectory() as outside:
        root = make_repo(pathlib.Path(raw))
        for label, page, should_deny in HTML_CASES:
            page_file = pathlib.Path(outside) / "page.html"
            page_file.write_text(page, encoding="utf-8")
            for tool, tool_input in [("Write", {"file_path": str(page_file), "content": page}),
                                     ("Artifact", {"file_path": str(page_file)})]:
                code, out = run_hook({"tool_name": tool, "tool_input": tool_input}, root)
                decision = decision_of(out)
                if code != 0:
                    print(f"  [실패] {tool} 훅이 종료 코드 {code}: {label}")
                    failures += 1
                elif should_deny and decision != "deny":
                    print(f"  [실패] {tool} 막혔어야 하는데 지나갔다: {label}")
                    failures += 1
                elif not should_deny and decision == "deny":
                    print(f"  [실패] {tool} 지나갔어야 하는데 막혔다: {label}: {out!r}")
                    failures += 1
    return failures


def check_scope() -> int:
    failures = 0
    print("[2] 양식 폴더가 없는 저장소 · HTML 이 아닌 파일 · 읽기 동작은 보지 않는다")
    bad = table(COLS, FULL, fmt=None)
    with tempfile.TemporaryDirectory() as raw:
        root = make_repo(pathlib.Path(raw), with_formats=False)
        _, out = run_hook({"tool_name": "Write", "tool_input": {"file_path": "a.html", "content": bad}}, root)
        if out:
            print(f"  [실패] 양식 폴더가 없는데 판정을 냈다: {out!r}")
            failures += 1
    with tempfile.TemporaryDirectory() as raw:
        root = make_repo(pathlib.Path(raw))
        for label, payload in [
            ("마크다운 파일", {"tool_name": "Write", "tool_input": {"file_path": "a.md", "content": bad}}),
            ("Edit", {"tool_name": "Edit", "tool_input": {"file_path": "a.html", "new_string": bad}}),
            ("Artifact 읽기", {"tool_name": "Artifact", "tool_input": {"action": "read", "url": "x"}}),
        ]:
            _, out = run_hook(payload, root)
            if out:
                print(f"  [실패] {label}: 판정을 냈다: {out!r}")
                failures += 1
    return failures


def check_reason() -> int:
    failures = 0
    print("[3] 차단 사유에 문제 위치 · 양식 폴더 · 생성기 · 합의 절차가 실린다")
    with tempfile.TemporaryDirectory() as raw:
        root = make_repo(pathlib.Path(raw))
        _, out = run_hook({"tool_name": "Write", "tool_input": {
            "file_path": "a.html", "content": table(COLS, FULL, fmt=None)}}, root)
        reason = json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"]
        for needed in ["표 1", "test-status", ".claude/report-formats/", "report-render.py", "합의", "none"]:
            if needed not in reason:
                print(f"  [실패] 차단 사유에 `{needed}` 가 없다: {reason!r}")
                failures += 1
    return failures


def check_renderer() -> int:
    failures = 0
    print("[4] 생성기는 빠진 대분류를 빈 줄로 남기고, 양식에 맞지 않는 값은 그리지 않는다")
    for cat in CATS:
        if f">{cat}</td>" not in GOOD:
            print(f"  [실패] 생성기 결과에 대분류 `{cat}` 줄이 없다")
            failures += 1
    if 'data-report-format="test-status"' not in GOOD or 'data-report-version="1"' not in GOOD:
        print("  [실패] 생성기 결과에 양식 이름 · 버전이 붙지 않았다")
        failures += 1
    bad_inputs = [
        ("버전이 다르다", {**DATA, "version": 2}),
        ("없는 대분류", {**DATA, "rows": [{"대분류": "배경", "내용": "x"}]}),
        ("없는 칸", {**DATA, "rows": [{"대분류": "목적", "비고": "x"}]}),
        ("3줄", {**DATA, "rows": [{"대분류": "목적", "내용": ["a", "b", "c"]}]}),
        ("문장", {**DATA, "rows": [{"대분류": "목적", "내용": "확인한다."}]}),
        ("정하지 않은 상태 값", {**DATA, "rows": [{"대분류": "목적", "합의": "OK"}]}),
    ]
    for label, data in bad_inputs:
        try:
            render_mod.render(FMT, data)
        except render_mod.FormatError:
            continue
        print(f"  [실패] 생성기가 막지 않았다: {label}")
        failures += 1
    return failures


def check_windows_encoding() -> int:
    failures = 0
    print("[5] 파이프 기본 인코딩이 cp949 여도 판정을 낸다")
    env = {**os.environ, "PYTHONIOENCODING": "cp949", "PYTHONUTF8": "0"}
    with tempfile.TemporaryDirectory() as raw:
        root = make_repo(pathlib.Path(raw))
        code, out = run_hook({"tool_name": "Write", "tool_input": {
            "file_path": "보고/현황.html", "content": table(COLS, FULL, fmt=None)}}, root, env)
        if code != 0 or decision_of(out) != "deny":
            print(f"  [실패] 종료 코드 {code}, UTF-8 deny 판정이 아니다: {out[:200]!r}")
            failures += 1
    return failures


if __name__ == "__main__":
    total = check_html_cases() + check_scope() + check_reason() + check_renderer() + check_windows_encoding()
    if total:
        print(f"\n실패 {total}건")
        sys.exit(1)
    print(f"통과: {len(HTML_CASES)}가지 표를 Write 와 Artifact 두 길로 넣어 양식 밖의 표만 차단됐고, "
          f"양식 폴더가 없거나 HTML 이 아닌 곳에서는 판정을 내지 않았으며, 생성기는 빠진 대분류를 "
          f"빈 줄로 남기고 양식에 맞지 않는 값 {6}종을 그리지 않았고, cp949 파이프 조건에서도 판정을 냈습니다.")
