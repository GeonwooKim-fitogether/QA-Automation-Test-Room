#!/usr/bin/env python3
"""개발 DB 에 마이그레이션을 적용한 직후, 세션이 끝까지 할 검증 목록을 세션에 넣어 주는 PostToolUse 훅.

## 왜 이 훅이 필요한가

"개발 DB 에 적용한 뒤의 검증은 세션이 끝까지 하고 사람에게는 결과만 보고한다"는 약속은
2026-10-21 과 2026-11-19 두 번 사람의 지적으로 생겼다. 두 번 다 세션이 서버만 켜 두고
"로그인해서 보세요"로 넘겼다. 그 약속은 한 사람의 기억 메모에만 있었고, 세션에는 메모의
한 줄 색인만 실렸다. 두 번째 세션은 부모 세션이 준 작업 지시서에 그 단계가 없어 메모를
놓쳤다. **약속이 글로만 있으면, 그 글을 읽는 경로가 하나라도 끊기면 지켜지지 않는다.**

이 훅은 읽는 경로에 기대지 않는다. 개발 DB 에 마이그레이션이 실제로 적용된 바로 그 순간
도구 결과 옆에 검증 목록을 붙여 넣는다. 세션이 메모를 읽었든, 지시서에 적혀 있었든 상관없이
다음 행동을 고르기 직전에 목록이 눈앞에 있다.

## 언제 발동하나 — 셋이 모두 맞을 때만

| 조건 | 왜 |
|---|---|
| 도구 이름이 `mcp__<서버>__apply_migration` 이다 (서버 이름 무관) | 데스크톱 앱은 커넥터 이름이 UUID 다. 이름 전체로 대조하면 불리지 않는다 |
| `project_id` 가 저장소의 `.claude/dev-db.json` 의 `dev_projects` 에 있다 | 개발 DB 만이 "적용하고 세션이 검증하는" 곳이다. 그 밖의 프로젝트는 사람이 열쇠로 연다 |
| 그 파일이 있다 | 없는 저장소에서는 아무 일도 하지 않는다 — 도입 비용이 없다 |

`execute_sql` 에는 반응하지 않는다. 검증 자체가 `execute_sql` 로 이뤄지므로, 거기에도
반응하면 검증하는 동안 같은 안내가 매번 끼어든다.

## 목록은 어디서 오나 — 저장소의 절차 문서 한 곳

목록의 정본은 저장소의 절차 문서다(값의 주인은 하나). `.claude/dev-db.json` 의
`verify_steps` 가 문서 경로와 구간 이름을 알려 주고, 이 훅은 그 문서에서

    <!-- <구간>:start -->
    ...
    <!-- <구간>:end -->

사이를 그대로 옮긴다. 문서를 고치면 다음 발동부터 바뀐 목록이 나간다. 문서나 표지를 찾지
못하면 **조용히 물러나지 않고** 이 파일의 기본 목록을 보이면서 표지를 확인하라고 적는다 —
안내가 사라지는 것이 이 훅이 막으려는 바로 그 실패이기 때문이다.

## 한계

- 이 훅은 **알려 줄 뿐 막지 않는다.** 세션이 목록을 읽고도 건너뛰는 것까지 막지는 못한다.
  대신 라이브 적용 버튼이 `dev-verified` 라벨(사람이 붙임)을 요구하므로, 검증 없이 라이브로
  넘어가는 길은 따로 막혀 있다.
- 화면만 바꾸고 마이그레이션이 없는 변경에는 발동할 순간이 없다. 그 경우는 하위 세션
  지시서에 검증 담당을 적는 규칙(state-before-action §3)이 받친다.
- Claude Code 는 세션을 시작할 때 훅 설정을 한 번 읽는다. 설정을 바꾼 뒤에는 새 세션부터 돈다.
"""

import json
import os
import sys
import textwrap

MIGRATION_TOOL_SUFFIX = "__apply_migration"
DEV_DB_FILENAME = "dev-db.json"

# 저장소 문서에서 목록을 찾지 못했을 때 보이는 기본 목록. 저장소마다 다른 도구 이름은 넣지 않는다.
DEFAULT_STEPS = """\
1. 동작 — 개발 DB 의 실제 데이터로 바뀐 기능을 실행해 결과를 읽는다. 되돌려야 하는 실행은
   `do $$ … raise exception '결과: %', … $$` 처럼 결과를 오류문에 실어 받아 전부 롤백하고,
   뒤이어 흔적이 0 인지 조회로 확인한다.
2. 화면이 쓰는 계산 — 같은 데이터를 앱의 데이터 층 함수에 넣어 화면이 받을 값을 확인한다.
3. 화면 모양 — 로그인 없이 띄울 수 있는 방법(가짜 데이터로 띄우는 빌드 등)으로 화면을 띄워
   스크린샷을 찍는다.
4. 로그인이 필요해 보일 때 — 화면 증명은 3번의 가짜 데이터 화면으로 한다. 로그인한 화면에서만
   볼 수 있는 확인이 남으면, 사람에게 부탁하기 전에 이 일을 맡긴 지휘 세션(부모 세션)에 먼저
   묻는다. 로그인·화면 확인을 사람에게 부탁하지 않는다.
5. 사람 몫 — 확인 라벨(`dev-verified`)은 사람이 붙인다. 세션은 붙이지 않는다."""


def repo_root() -> str:
    """훅 파일 위치를 기준으로 저장소 뿌리를 잡는다(.claude/hooks/ 의 두 단계 위)."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def load_config():
    """저장소의 .claude/dev-db.json. 없거나 깨졌으면 None — 그러면 아무 일도 하지 않는다."""
    path = os.path.join(repo_root(), ".claude", DEV_DB_FILENAME)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def dev_projects(cfg: dict) -> dict:
    found = {}
    for entry in cfg.get("dev_projects") or []:
        if isinstance(entry, dict) and entry.get("id"):
            found[str(entry["id"])] = str(entry.get("name") or entry["id"])
    return found


def read_steps(cfg: dict):
    """(목록, 정본 위치, 경고). 문서의 구간을 못 찾으면 기본 목록과 경고를 낸다."""
    spec = cfg.get("verify_steps") or {}
    doc = str(spec.get("doc") or "")
    block = str(spec.get("block") or "session-verify")
    where = f"{doc} 의 `{block}` 구간" if doc else "(문서가 지정되지 않음)"
    if not doc:
        return DEFAULT_STEPS, where, (
            f".claude/{DEV_DB_FILENAME} 에 verify_steps.doc 가 없어 기본 목록을 보였습니다."
        )
    try:
        with open(os.path.join(repo_root(), doc), encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return DEFAULT_STEPS, where, f"{doc} 를 읽지 못해 기본 목록을 보였습니다 — 경로를 확인하십시오."
    start, end = f"<!-- {block}:start -->", f"<!-- {block}:end -->"
    i, j = text.find(start), text.find(end)
    if i < 0 or j < 0 or j < i:
        return DEFAULT_STEPS, where, (
            f"{doc} 에서 `{start}` … `{end}` 표지를 찾지 못해 기본 목록을 보였습니다 — 표지를 확인하십시오."
        )
    # 문서에서는 번호 목록 안에 들여 쓴 구간이라, 들여쓰기를 걷어 내고 넣는다. Windows 에서 받은
    # 문서는 줄 끝이 CRLF 일 수 있어 먼저 LF 로 맞춘다.
    block = text[i + len(start):j].replace("\r\n", "\n").strip("\n")
    return textwrap.dedent(block).strip(), where, None


def main() -> int:
    # 한국어 Windows 의 파이프 기본 인코딩(cp949)에서도 판정이 Claude Code 에 닿게 UTF-8 로 고정한다.
    # 고정하지 않으면 줄표(—) 하나에 훅이 죽고, Claude Code 는 오류를 조용히 넘긴다(2026-11-09 실측).
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    tool = str(payload.get("tool_name") or "")
    if not (tool.startswith("mcp__") and tool.endswith(MIGRATION_TOOL_SUFFIX)):
        return 0
    cfg = load_config()
    if not cfg:
        return 0
    tool_input = payload.get("tool_input") or {}
    dev_name = dev_projects(cfg).get(str(tool_input.get("project_id") or ""))
    if not dev_name:
        return 0

    steps, where, warning = read_steps(cfg)
    migration = str(tool_input.get("name") or "(이름 없음)")
    lines = [
        "[개발 DB 적용 뒤 검증 — 세션이 끝까지 한다]",
        f"방금 개발 DB({dev_name})에 마이그레이션 `{migration}` 을 적용했습니다"
        " (적용이 실패했다면 고쳐서 다시 적용한 뒤 이 목록을 따르십시오).",
        "사람에게 \"확인해 보세요\"로 넘기기 전에 아래 검증을 이 세션이 직접 끝내고, 보고에는 결과만"
        " 적으십시오. 사람에게 남는 일은 dev-verified 라벨과 머지뿐입니다 — 로그인·화면 확인을"
        " 사람에게 부탁하지 않습니다(로그인이 꼭 필요하면 지휘 세션에 먼저 묻습니다)."
        " 이 일을 하위 세션에 넘긴다면 지시서에 이 목록을 함께 실으십시오.",
        f"(정본: {where}. 이 안내는 .claude/hooks/dev-verify-reminder.py 가 넣었습니다.)",
    ]
    if warning:
        lines.append(f"(주의: {warning})")
    lines += ["", steps]

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": "\n".join(lines),
        }
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
