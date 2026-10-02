#!/usr/bin/env python3
"""폐기하기로 결정한 것이 다시 살아나지 못하게 막는 PreToolUse 훅.

## 왜 이 훅이 필요한가

어떤 체계를 그만 쓰기로 결정하고 파일을 `archive/` 로 옮겨도, 결정은 절반만
집행됩니다. 그 체계를 **다시 만들어 내는 도구**(스킬)는 그대로 남아 있고,
결정문은 세션 시작 시 자동으로 읽히지 않기 때문입니다. 실제로 한 저장소에서
8월에 폐기한 대시보드 체계를 9월의 새 세션이 처음부터 다시 만들었고, 그동안
아무 오류도 아무 검사도 걸리지 않았습니다. 경위는 규칙 부록에 있습니다
(`.claude/rules/reference/past-decisions-history.md`).

되살아나는 경로는 두 갈래뿐입니다.

| 경로 | 이 훅이 보는 도구 |
|---|---|
| 그것을 다시 만들어 내는 **도구를 부른다** | `Skill` |
| 원래 **자리에 파일을 다시 쓴다** | `Write` · `Edit` · `NotebookEdit` |

두 갈래를 각각 막으면 폐기가 유지됩니다.

## 무엇을 읽나

저장소 뿌리의 `.claude/retired.json` 하나뿐입니다. 이 파일이 없으면 훅은 아무
판정도 내지 않고 물러납니다 — 폐기 목록이 없는 저장소에서는 이 훅이 존재하지
않는 것과 같습니다.

## 왜 "확인 요청"이 아니라 "차단"인가

같은 저장소의 `sql-write-guard.py` 가 실측한 결과, 이 원격 실행 환경에서는
`ask`(확인 요청) 판정이 무시되고 도구가 그대로 실행됩니다. 실제로 멈춰 세우는
판정은 `deny` 뿐이라 이 훅도 `deny` 를 냅니다.

## 막힌 것을 푸는 방법

**`.claude/retired.json` 에서 그 항목을 지우는 것 하나뿐입니다.** 일회용 열쇠
같은 우회로를 두지 않았습니다. 폐기를 되돌리는 것은 급한 일이 아니라 결정이고,
항목을 지우는 일은 커밋에 남아 리뷰에 보이기 때문입니다.
"""

import fnmatch
import json
import os
import sys

WATCHED_TOOLS = ("Skill", "Write", "Edit", "NotebookEdit")
RETIRED_FILENAME = ".claude/retired.json"


def repo_root() -> str:
    """훅 파일 위치를 기준으로 저장소 뿌리를 잡는다(.claude/hooks/ 의 두 단계 위)."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def load_retired(root: str):
    """폐기 목록을 읽는다. 없거나 깨졌으면 None — 그때는 아무 판정도 내지 않는다."""
    path = os.path.join(root, RETIRED_FILENAME)
    try:
        with open(path, "r", encoding="utf-8") as fp:
            data = json.load(fp)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def relative_path(root: str, raw: str):
    """도구가 넘긴 경로를 저장소 기준 상대경로로 바꾼다. 저장소 밖이면 None."""
    if not raw:
        return None
    candidate = raw if os.path.isabs(raw) else os.path.join(root, raw)
    try:
        rel = os.path.relpath(os.path.normpath(candidate), root)
    except ValueError:
        return None
    if rel.startswith(".."):
        return None
    return rel.replace(os.sep, "/")


def path_matches(rel: str, glob: str) -> bool:
    """경로가 폐기 항목에 걸리는지 본다.

    와일드카드가 있으면 그대로 대조하고, 없으면 그 경로 자체이거나 그 아래
    전부를 뜻하는 것으로 읽는다(`meta` 는 `meta/x/y.md` 도 잡는다).
    """
    if not glob:
        return False
    glob = glob.replace("\\", "/").lstrip("./")
    if fnmatch.fnmatch(rel, glob):
        return True
    if "*" not in glob and "?" not in glob and "[" not in glob:
        return rel == glob or rel.startswith(glob.rstrip("/") + "/")
    return False


def entries(data: dict, key: str):
    """목록의 한 칸을 꺼낸다. 형태가 다르면 조용히 빈 목록으로 본다."""
    raw = data.get(key)
    return [e for e in raw if isinstance(e, dict)] if isinstance(raw, list) else []


def deny_reason(kind: str, subject: str, entry: dict, data: dict) -> str:
    """왜 막혔는지, 근거가 어디 있는지, 대신 무엇을 쓸지를 한 덩어리로 만든다."""
    decision = (entry.get("decision") or "").strip()
    instead = (entry.get("instead") or "").strip()
    log = (data.get("decision_log") or "").strip()

    parts = [f"{kind} `{subject}` 은(는) 이 저장소에서 쓰지 않기로 폐기된 것입니다."]
    if decision:
        parts.append(f"근거: {decision}.")
    if instead:
        parts.append(f"대신 이것을 쓰십시오: {instead}.")
    else:
        parts.append(
            "대신 무엇을 쓸지가 목록에 적혀 있지 않습니다 — 우회로를 스스로 만들지 말고 "
            "사용자에게 물으십시오."
        )
    if log:
        parts.append(f"관련 결정은 `{log}` 에서 주제어로 검색하면 나옵니다.")
    parts.append(
        f"이것을 되살리는 것이 옳다고 판단되면 조용히 진행하지 말고 사용자에게 결정으로 "
        f"올리십시오. 승인 뒤 `{RETIRED_FILENAME}` 에서 해당 항목을 지우면 통과합니다."
    )
    return " ".join(parts)


def announce(root: str) -> str:
    """세션 시작 때 실어 줄 안내문을 만든다. 목록이 없으면 빈 문자열.

    이 안내가 필요한 이유는 비대칭 하나 때문이다. 규칙(`.claude/rules/`)은 세션
    시작 시 자동으로 읽히는데 **결정 기록은 읽히지 않는다.** 그래서 새 세션은
    "어떻게 일할 것인가"는 알고 들어오면서 "무엇을 하지 않기로 정했나"는 모른 채
    들어온다. 이 안내가 그 한 줄을 채운다.
    """
    data = load_retired(root)
    if not data:
        return ""

    skills = entries(data, "skills")
    paths = entries(data, "paths")
    if not skills and not paths:
        return ""

    lines = ["[past-decisions] 이 저장소에는 폐기 목록이 있다 "
             f"({RETIRED_FILENAME}). 아래는 다시 쓰지 않기로 결정된 것이며, "
             "훅이 실제로 차단한다."]
    for entry in skills:
        name = (entry.get("name") or "?").strip()
        lines.append(f"  - 스킬 `{name}` — 대신: {(entry.get('instead') or '목록에 없음').strip()}"
                     f"  ({(entry.get('decision') or '근거 미기재').strip()})")
    for entry in paths:
        glob = (entry.get("glob") or "?").strip()
        lines.append(f"  - 경로 `{glob}` — 대신: {(entry.get('instead') or '목록에 없음').strip()}"
                     f"  ({(entry.get('decision') or '근거 미기재').strip()})")

    log = (data.get("decision_log") or "").strip()
    if log:
        lines.append(f"  착수 전에 `{log}` 를 이번 작업의 주제어로 한 번 검색한다 "
                     "— 파일 이름이 아니라 주제로 찾는다.")
    return "\n".join(lines)


def emit(decision: str, reason: str) -> None:
    """권한 판정을 표준 출력으로 낸다. deny 는 도구 실행을 막는다."""
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }, ensure_ascii=False))


def judge(payload: dict, root: str):
    """판정이 필요하면 (kind, subject, entry, data) 를, 아니면 None 을 낸다."""
    tool = payload.get("tool_name")
    if tool not in WATCHED_TOOLS:
        return None

    data = load_retired(root)
    if not data:
        return None

    tool_input = payload.get("tool_input") or {}

    if tool == "Skill":
        name = (tool_input.get("skill") or "").strip()
        if not name:
            return None
        # 플러그인 스킬은 `플러그인:스킬` 형태라 뒤쪽 이름으로도 대조한다.
        bare = name.split(":")[-1]
        for entry in entries(data, "skills"):
            listed = (entry.get("name") or "").strip()
            if listed and listed in (name, bare):
                return ("스킬", name, entry, data)
        return None

    rel = relative_path(root, tool_input.get("file_path") or tool_input.get("notebook_path"))
    if rel is None:
        return None
    for entry in entries(data, "paths"):
        if path_matches(rel, (entry.get("glob") or "").strip()):
            return ("경로", rel, entry, data)
    return None


def main() -> int:
    # 세션 시작 훅이 부르는 안내 모드. 판정과 무관하게 목록을 사람과 세션에 보여 준다.
    if "--announce" in sys.argv:
        text = announce(repo_root())
        if text:
            print(text)
        return 0

    try:
        payload = json.load(sys.stdin)
    except Exception:
        # 입력을 못 읽으면 판단하지 않고 원래 권한 흐름에 맡긴다.
        return 0

    verdict = judge(payload, repo_root())
    if verdict is None:
        # 판정하지 않고 물러난다 — 정상 권한 흐름에 맡긴다.
        return 0

    kind, subject, entry, data = verdict
    emit("deny", deny_reason(kind, subject, entry, data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
