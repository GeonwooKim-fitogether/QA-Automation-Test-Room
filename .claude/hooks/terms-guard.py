#!/usr/bin/env python3
"""사용자와 정한 말 밖의 말(금지어)이 문서와 보고에 들어가지 못하게 막는 PreToolUse 훅.

## 왜 이 훅이 필요한가

정한 말을 풀어 쓰거나 바꿔 부르거나 혼자 지은 말을 쓰면, 보고를 읽을 때마다 사람이
다시 해석해야 합니다. 같은 지적이 하루에 여러 번 반복된 사고가 있었고(2026-11-13),
원칙은 이미 글로 있었습니다. 글은 읽혀야 작동하므로, 쓰는 순간 막는 장치로 옮깁니다.
경위는 `.claude/rules/reference/report-format-history.md` 에 있습니다.

## 무엇을 읽나

저장소 뿌리의 `.claude/terms.json` 하나뿐입니다. 이 파일이 없으면 훅은 아무 판정도
내지 않고 물러납니다. 구조는 `retired-guard.py` 의 폐기 목록을 따랐습니다.

| 칸 | 뜻 |
|---|---|
| `banned[].word` | 쓰지 않기로 한 말. 기본은 글자 그대로 찾는다 |
| `banned[].pattern` | 다른 낱말 안에 섞이는 말을 좁히는 정규식(있으면 이것으로 찾는다) |
| `banned[].instead` | 대신 쓸 말 |
| `banned[].decision` | 언제 누가 정했나 |
| `exclude` | 검사하지 않을 저장소 안 경로(glob) |

## 어디를 보나

| 도구 | 검사하는 글 |
|---|---|
| `Write` · `Edit` · `NotebookEdit` | 저장소 안에 쓰는 내용 (저장소 밖은 보지 않는다) |
| `Artifact` | 게시하는 파일과 함께 올리는 파일 (위치와 상관없이 본다) |

용어표 자신과 `.claude/hooks/` 아래는 항상 보지 않습니다. 금지어를 정의하거나
시험하는 자리이기 때문입니다.

## 왜 "차단"인가

`sql-write-guard.py` 가 실측한 대로 `ask` 판정은 무시되고 도구가 그대로 실행되는
환경이 있습니다. 실제로 멈춰 세우는 판정은 `deny` 뿐입니다.

## 막힌 것을 푸는 방법

글에서 그 말을 대신 쓸 말로 바꾸는 것이 기본입니다. 금지 자체가 틀렸다면 사용자와
합의한 뒤 `.claude/terms.json` 에서 그 항목을 지웁니다. 그 변경은 커밋으로 남습니다.
"""

import fnmatch
import json
import os
import re
import sys

WATCHED_TOOLS = ("Write", "Edit", "NotebookEdit", "Artifact")
TERMS_FILENAME = ".claude/terms.json"
ALWAYS_EXCLUDED = (TERMS_FILENAME, ".claude/hooks/*", ".claude/hooks/**")
# 게시 파일로 함께 올리는 것 가운데 글로 읽을 수 있는 것만 본다.
TEXT_SUFFIXES = (".html", ".htm", ".md", ".txt", ".json", ".csv", ".svg", ".js", ".css")


def repo_root() -> str:
    """훅 파일 위치를 기준으로 저장소 뿌리를 잡는다(.claude/hooks/ 의 두 단계 위)."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def load_terms(root: str):
    """용어표를 읽는다. 없거나 깨졌으면 None, 그때는 아무 판정도 내지 않는다."""
    try:
        with open(os.path.join(root, TERMS_FILENAME), "r", encoding="utf-8") as fp:
            data = json.load(fp)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def banned_entries(data: dict):
    raw = data.get("banned")
    return [e for e in raw if isinstance(e, dict) and (e.get("word") or e.get("pattern"))] \
        if isinstance(raw, list) else []


def absolute(root: str, raw: str) -> str:
    return os.path.normpath(raw if os.path.isabs(raw) else os.path.join(root, raw))


def relative_path(root: str, raw: str):
    """저장소 기준 상대경로. 저장소 밖이면 None."""
    if not raw:
        return None
    try:
        rel = os.path.relpath(absolute(root, raw), root)
    except ValueError:
        return None
    if rel.startswith(".."):
        return None
    return rel.replace(os.sep, "/")


def excluded(rel: str, data: dict) -> bool:
    globs = list(ALWAYS_EXCLUDED)
    extra = data.get("exclude")
    if isinstance(extra, list):
        globs += [g for g in extra if isinstance(g, str)]
    for glob in globs:
        glob = glob.replace("\\", "/")
        while glob.startswith("./"):  # lstrip("./") 는 `.claude` 의 점까지 지워 쓰지 않는다
            glob = glob[2:]
        if fnmatch.fnmatch(rel, glob):
            return True
        if not any(ch in glob for ch in "*?[") and (rel == glob or rel.startswith(glob.rstrip("/") + "/")):
            return True
    return False


def read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fp:
            return fp.read()
    except OSError:
        return ""


def artifact_sources(tool_input: dict, root: str):
    """Artifact 호출이 게시하는 파일들의 절대경로를 모은다."""
    paths = []
    if tool_input.get("file_path") and not tool_input.get("asset"):
        paths.append(absolute(root, tool_input["file_path"]))
    files = tool_input.get("files")
    base = absolute(root, tool_input["root"]) if tool_input.get("root") else root
    items = []
    if isinstance(files, dict):
        for published, src in files.items():
            if isinstance(src, str):
                items.append(src)
            elif isinstance(src, dict) and isinstance(src.get("from"), str):
                items.append(src["from"])
    elif isinstance(files, list):
        items = [f.get("path") for f in files if isinstance(f, dict) and isinstance(f.get("path"), str)]
    for src in items:
        if src.lower().endswith(TEXT_SUFFIXES):
            paths.append(absolute(base, src))
    return paths


def texts_to_check(payload: dict, root: str, data: dict):
    """검사할 (이름, 글) 목록을 낸다. 볼 것이 없으면 빈 목록."""
    tool = payload.get("tool_name")
    tool_input = payload.get("tool_input") or {}

    if tool == "Artifact":
        if tool_input.get("action") not in (None, "publish"):
            return []
        out = []
        for path in artifact_sources(tool_input, root):
            rel = relative_path(root, path)
            if rel is not None and excluded(rel, data):
                continue
            out.append((rel or path, read_text(path)))
        return out

    rel = relative_path(root, tool_input.get("file_path") or tool_input.get("notebook_path"))
    if rel is None or excluded(rel, data):
        return []
    if tool == "Write":
        text = tool_input.get("content")
    elif tool == "Edit":
        text = tool_input.get("new_string")
    else:  # NotebookEdit
        text = tool_input.get("new_source")
    return [(rel, text)] if isinstance(text, str) and text else []


def find_hits(text: str, entries):
    """글에서 금지어를 찾아 (항목, 찾은 글자, 줄 번호) 목록을 낸다."""
    hits = []
    for entry in entries:
        pattern = entry.get("pattern")
        try:
            rx = re.compile(pattern) if pattern else re.compile(re.escape(entry["word"]))
        except (re.error, KeyError, TypeError):
            continue
        m = rx.search(text)
        if m:
            line = text.count("\n", 0, m.start()) + 1
            hits.append((entry, m.group(0), line))
    return hits


def deny_reason(found) -> str:
    parts = ["사용자와 정하지 않은 말(용어표의 금지어)이 들어 있습니다."]
    for name, entry, word, line in found:
        instead = (entry.get("instead") or "").strip()
        decision = (entry.get("decision") or "").strip()
        piece = f"`{name}` {line}행의 `{word}`"
        piece += f" 대신 `{instead}`" if instead else " (대신 쓸 말이 용어표에 없음, 지어내지 말고 사용자에게 물으십시오)"
        if decision:
            piece += f" [근거: {decision}]"
        parts.append(piece + ".")
    parts.append(
        f"정한 말로 바꾼 뒤 다시 쓰십시오. 금지가 틀렸다고 판단되면 조용히 우회하지 말고 "
        f"사용자와 합의한 뒤 `{TERMS_FILENAME}` 에서 그 항목을 지우십시오."
    )
    return " ".join(parts)


def judge(payload: dict, root: str):
    """막아야 하면 사유 문자열을, 아니면 None 을 낸다."""
    if payload.get("tool_name") not in WATCHED_TOOLS:
        return None
    data = load_terms(root)
    if not data:
        return None
    entries = banned_entries(data)
    if not entries:
        return None
    found = []
    for name, text in texts_to_check(payload, root, data):
        for entry, word, line in find_hits(text, entries):
            found.append((name, entry, word, line))
    return deny_reason(found) if found else None


def emit(decision: str, reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }, ensure_ascii=False))


def main() -> int:
    # 한국어 Windows 는 파이프의 기본 인코딩이 cp949 다. 그대로 두면 판정문의 글자를
    # 내보내다 훅이 죽고(exit 1, Claude Code 는 판정 없이 도구를 그대로 실행한다),
    # 입력의 한글이 깨져 금지어 대조가 빗나간다. 세 통로를 모두 UTF-8 로 고정한다.
    # (2026-11-09 실측, 규칙 부록 db-write-permission-history.md)
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    reason = judge(payload, repo_root())
    if reason:
        emit("deny", reason)
    return 0


if __name__ == "__main__":
    sys.exit(main())
