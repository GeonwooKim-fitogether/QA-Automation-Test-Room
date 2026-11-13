#!/usr/bin/env python3
"""합의한 보고 양식 밖의 표를 막는 PreToolUse 훅.

## 왜 이 훅이 필요한가

보고할 때마다 표를 새로 만들면 앞에서 합의한 칸이 사라지고, 칸에 문장이 들어가
한눈에 읽히지 않습니다. 하루 동안 같은 지적이 반복되고 장비 운전이 0회였던 사고가
있었습니다(2026-11-13). 경위는 `.claude/rules/reference/report-format-history.md` 에
있습니다. 그래서 표마다 어느 양식의 몇 번째 버전인지 적게 하고, 양식 파일과 대조합니다.

## 무엇을 읽나

저장소 뿌리의 `.claude/report-formats/*.json` (합의한 양식들)입니다. 이 폴더에 양식이
하나도 없으면 훅은 아무 판정도 내지 않고 물러납니다.

## 어디를 보나

| 도구 | 검사하는 글 |
|---|---|
| `Artifact` (게시) | 게시하는 파일이 `.html` 이면 그 안의 표 |
| `Write` | `.html` · `.htm` 파일에 쓰는 내용의 표 |

`Edit` 는 보지 않습니다. 고친 조각만으로는 표 전체를 알 수 없으므로, 게시하는 순간
`Artifact` 에서 전체를 봅니다.

## 표마다 보는 것

1. `data-report-format` 이 있는가. 없으면 막는다. `none` 이면 보고가 아닌 표로 보고 통과.
2. 그 양식이 있는가, `data-report-version` 이 양식의 `version` 과 같은가.
3. 머리글 칸 이름 · 순서가 `columns` 와 같은가.
4. 대분류 칸 값이 `categories` 와 같은 순서로 빠짐없이 오는가(다 못 채워도 틀은 그대로).
5. `column_values` 가 정한 칸(예: 합의 · 진행)에 정한 값만 오는가.
6. 한 칸이 `cell.max_lines` 줄을 넘지 않고, 문장으로 끝나지 않는가.
"""

import glob
import html.parser
import json
import os
import sys

FORMATS_DIR = ".claude/report-formats"
HTML_SUFFIXES = (".html", ".htm")
SENTENCE_ENDINGS = ("다.", "니다", "요.", "까?")
MAX_PROBLEMS = 6


def repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def load_formats(root: str) -> dict:
    """양식 이름 -> 양식. 깨진 파일은 건너뛴다."""
    formats = {}
    for path in sorted(glob.glob(os.path.join(root, FORMATS_DIR, "*.json"))):
        try:
            with open(path, "r", encoding="utf-8") as fp:
                data = json.load(fp)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and isinstance(data.get("columns"), list):
            name = data.get("name") or os.path.splitext(os.path.basename(path))[0]
            formats[name] = data
    return formats


class TableParser(html.parser.HTMLParser):
    """HTML 에서 표마다 속성 · 머리글 · 몸통 줄을 모은다. 표 안의 표는 따로 센다."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []
        self.stack = []  # 열린 표들

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "table":
            table = {"attrs": attrs, "rows": []}
            self.tables.append(table)
            self.stack.append({"table": table, "row": None, "cell": None, "in_thead": False})
            return
        if not self.stack:
            return
        top = self.stack[-1]
        if tag == "thead":
            top["in_thead"] = True
        elif tag == "tr":
            top["row"] = {"head": top["in_thead"], "cells": []}
            top["table"]["rows"].append(top["row"])
        elif tag in ("td", "th") and top["row"] is not None:
            top["cell"] = {"tag": tag, "text": "", "breaks": 0}
            top["row"]["cells"].append(top["cell"])
        elif tag == "br" and top["cell"] is not None:
            top["cell"]["breaks"] += 1

    def handle_endtag(self, tag):
        if not self.stack:
            return
        top = self.stack[-1]
        if tag == "table":
            self.stack.pop()
        elif tag == "thead":
            top["in_thead"] = False
        elif tag in ("td", "th"):
            top["cell"] = None
        elif tag == "tr":
            top["row"] = None

    def handle_data(self, data):
        if self.stack and self.stack[-1]["cell"] is not None:
            self.stack[-1]["cell"]["text"] += data


def clean(text: str) -> str:
    return " ".join(text.split())


def check_table(table: dict, formats: dict, index: int):
    """표 하나의 문제 목록을 낸다. 문제가 없거나 보고가 아닌 표(none)면 빈 목록."""
    where = f"표 {index}"
    name = (table["attrs"].get("data-report-format") or "").strip()
    if name == "none":
        return []
    if not name:
        return [f"{where}: `data-report-format` 이 없습니다 (합의한 양식: {', '.join(sorted(formats))})"]
    fmt = formats.get(name)
    if fmt is None:
        return [f"{where}: 양식 `{name}` 이 `{FORMATS_DIR}/` 에 없습니다"]

    problems = []
    want_version = str(fmt.get("version", ""))
    got_version = (table["attrs"].get("data-report-version") or "").strip()
    if got_version != want_version:
        problems.append(f"{where}: 양식 `{name}` 의 버전은 {want_version} 인데 표는 `{got_version or '없음'}`")

    columns = [str(c) for c in fmt["columns"]]
    rows = table["rows"]
    head_rows = [r for r in rows if r["head"] or (r["cells"] and all(c["tag"] == "th" for c in r["cells"]))]
    head_ids = {id(r) for r in head_rows}
    body_rows = [r for r in rows if id(r) not in head_ids and r["cells"]]
    header = [clean(c["text"]) for c in head_rows[0]["cells"]] if head_rows else []
    if header != columns:
        problems.append(f"{where}: 머리글이 {header or '없음'} 입니다. 양식의 칸은 {columns}")
        return problems  # 칸이 다르면 아래 대조가 의미 없다

    cat_col = fmt.get("category_column") or columns[0]
    cat_idx = columns.index(cat_col) if cat_col in columns else 0
    values = fmt.get("column_values") if isinstance(fmt.get("column_values"), dict) else {}
    cell_rule = fmt.get("cell") if isinstance(fmt.get("cell"), dict) else {}
    max_lines = int(cell_rule.get("max_lines", 2))
    no_sentence = cell_rule.get("no_sentence", True)

    seen = []
    for r_no, row in enumerate(body_rows, 1):
        cells = row["cells"]
        if len(cells) > len(columns):
            problems.append(f"{where} {r_no}줄: 칸이 {len(cells)}개로 양식({len(columns)}개)보다 많습니다")
            continue
        # 앞 칸이 rowspan 으로 묶이면 줄의 칸 수가 줄어든다. 줄의 칸은 뒤쪽 칸들에 맞춘다.
        names = columns[len(columns) - len(cells):]
        for col, cell in zip(names, cells):
            text = clean(cell["text"])
            if col == cat_col and (not seen or seen[-1] != text):
                seen.append(text)
            if cell["breaks"] + 1 > max_lines:
                problems.append(f"{where} {r_no}줄 `{col}`: {cell['breaks'] + 1}줄입니다 (상한 {max_lines}줄)")
            if no_sentence and text.endswith(SENTENCE_ENDINGS):
                problems.append(f"{where} {r_no}줄 `{col}`: 문장입니다 (`{text[-20:]}`). 핵심어와 숫자만 씁니다")
            allowed = values.get(col)
            if isinstance(allowed, list) and text and text not in allowed:
                problems.append(f"{where} {r_no}줄 `{col}`: `{text}` 는 정한 값이 아닙니다 ({' · '.join(allowed)})")

    categories = [str(c) for c in fmt.get("categories") or []]
    if categories and seen != categories:
        problems.append(f"{where}: `{cat_col}` 순서가 {seen} 입니다. 양식은 {categories} (빈 대분류도 줄은 남깁니다)")
    return problems


def html_sources(payload: dict, root: str):
    """검사할 (이름, HTML) 목록."""
    tool = payload.get("tool_name")
    tool_input = payload.get("tool_input") or {}
    if tool == "Write":
        path = tool_input.get("file_path") or ""
        if path.lower().endswith(HTML_SUFFIXES) and isinstance(tool_input.get("content"), str):
            return [(path, tool_input["content"])]
        return []
    if tool == "Artifact":
        if tool_input.get("action") not in (None, "publish") or tool_input.get("asset"):
            return []
        path = tool_input.get("file_path") or ""
        if not path.lower().endswith(HTML_SUFFIXES):
            return []
        full = path if os.path.isabs(path) else os.path.join(root, path)
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as fp:
                return [(path, fp.read())]
        except OSError:
            return []
    return []


def judge(payload: dict, root: str):
    if payload.get("tool_name") not in ("Write", "Artifact"):
        return None
    sources = html_sources(payload, root)
    if not sources:
        return None
    formats = load_formats(root)
    if not formats:
        return None
    problems = []
    for name, text in sources:
        parser = TableParser()
        try:
            parser.feed(text)
        except Exception:  # noqa: BLE001 HTML 을 못 읽으면 판단하지 않는다
            continue
        for i, table in enumerate(parser.tables, 1):
            problems += [f"`{os.path.basename(name)}` {p}" for p in check_table(table, formats, i)]
    if not problems:
        return None
    shown = problems[:MAX_PROBLEMS]
    more = f" 외 {len(problems) - MAX_PROBLEMS}건" if len(problems) > MAX_PROBLEMS else ""
    return (
        "합의한 보고 양식 밖의 표입니다. " + " / ".join(shown) + more + ". "
        f"같은 종류의 보고는 `{FORMATS_DIR}/` 의 양식으로만 씁니다(생성기: `.claude/workflows/report-render.py`). "
        "새 종류의 보고면 양식부터 사용자에게 보여 주고 합의한 뒤 PR 로 양식 파일을 넣으십시오. "
        "양식을 바꾸려면 바꾼 안을 합의하고 version 을 올리십시오. "
        "보고가 아닌 표에만 `data-report-format=\"none\"` 을 붙입니다."
    )


def emit(decision: str, reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }, ensure_ascii=False))


def main() -> int:
    # 한국어 Windows 의 파이프 기본 인코딩(cp949)에서 훅이 죽으면 판정 없이 도구가 실행된다.
    # 세 통로를 모두 UTF-8 로 고정한다. (2026-11-09 실측, 규칙 부록 db-write-permission-history.md)
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
