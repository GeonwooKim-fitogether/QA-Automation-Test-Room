#!/usr/bin/env python3
"""합의한 보고 양식으로 표를 그리는 생성기 견본.

보고 표를 손으로 짜지 않고, 양식 파일과 값 파일을 이 생성기에 넘겨 그린다. 그러면
보고할 때마다 틀이 바뀌지 않고, 표에 양식 이름과 버전이 붙어
`report-format-guard.py` 의 검사를 통과한다. 규칙은 `.claude/rules/report-format.md` 3절.

    python .claude/workflows/report-render.py <양식.json> <값.json> [-o 출력.html] [--fragment]

값 파일의 모양 (견본: templates/report-formats/test-status.data.example.json):

    {"format": "test-status", "version": 1, "subject": "시험 A", "conclusion": "진행",
     "rows": [{"대분류": "목적", "중분류": "", "내용": "...", "합의": "완료", "진행": "완료"}]}

- 칸 값에 줄을 나누려면 문자열 대신 목록(`["첫 줄", "둘째 줄"]`)을 넣는다.
- 값 파일에 없는 대분류도 빈 줄로 남긴다(다 못 채워도 틀은 그대로).
- 양식과 버전이 다르거나, 없는 칸 · 대분류 · 값이 오면 그리지 않고 멈춘다.
"""

import argparse
import html
import json
import sys

SENTENCE_ENDINGS = ("다.", "니다", "요.", "까?")

PAGE = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
:root {{ --bg:#ffffff; --fg:#0a1316; --sub:#44555f; --line:#d5dee4; --head:#f3f6f7; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ --bg:#0f1719; --fg:#e8eef0; --sub:#a7b5bc; --line:#2b3a40; --head:#182327; }} }}
:root[data-theme="dark"] {{ --bg:#0f1719; --fg:#e8eef0; --sub:#a7b5bc; --line:#2b3a40; --head:#182327; }}
body {{ margin:0; padding:16px; background:var(--bg); color:var(--fg); font-family:Pretendard, system-ui, sans-serif; }}
{css}
</style></head><body>
{body}
</body></html>
"""

CSS = """.report-head { display:flex; align-items:baseline; justify-content:space-between; gap:16px; margin:0 0 8px; }
.report-head .subject { font-size:1.6rem; font-weight:700; margin:0; }
.report-head .conclusion { font-weight:600; white-space:nowrap; }
.report-table { width:100%; border-collapse:collapse; font-variant-numeric:tabular-nums; }
.report-table th, .report-table td { border:1px solid var(--line); padding:6px 8px; text-align:left; vertical-align:top; }
.report-table thead th { background:var(--head); color:var(--sub); border-top:1px solid var(--fg); }"""


class FormatError(Exception):
    pass


def cell_lines(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if str(v).strip()]
    return [str(value)] if str(value).strip() else []


def validate(fmt: dict, data: dict) -> None:
    if data.get("format") != fmt.get("name"):
        raise FormatError(f"값 파일의 양식 `{data.get('format')}` 이 양식 파일 `{fmt.get('name')}` 과 다릅니다")
    if str(data.get("version")) != str(fmt.get("version")):
        raise FormatError(f"값 파일은 버전 {data.get('version')}, 양식은 버전 {fmt.get('version')} 입니다. "
                          "양식이 바뀌었으면 값 파일을 새 양식에 맞춥니다")
    columns = fmt["columns"]
    cat_col = fmt.get("category_column") or columns[0]
    categories = fmt.get("categories") or []
    values = fmt.get("column_values") or {}
    max_lines = int((fmt.get("cell") or {}).get("max_lines", 2))
    no_sentence = (fmt.get("cell") or {}).get("no_sentence", True)
    for n, row in enumerate(data.get("rows") or [], 1):
        extra = set(row) - set(columns)
        if extra:
            raise FormatError(f"{n}번째 줄에 양식에 없는 칸이 있습니다: {sorted(extra)}")
        if categories and row.get(cat_col) not in categories:
            raise FormatError(f"{n}번째 줄의 `{cat_col}` 값 `{row.get(cat_col)}` 은 양식에 없습니다 ({categories})")
        for col in columns:
            lines = cell_lines(row.get(col))
            if len(lines) > max_lines:
                raise FormatError(f"{n}번째 줄 `{col}` 이 {len(lines)}줄입니다 (상한 {max_lines}줄)")
            text = " ".join(lines)
            if no_sentence and text.endswith(SENTENCE_ENDINGS):
                raise FormatError(f"{n}번째 줄 `{col}` 이 문장입니다. 핵심어와 숫자만 씁니다")
            allowed = values.get(col)
            if allowed and text and text not in allowed:
                raise FormatError(f"{n}번째 줄 `{col}` 값 `{text}` 은 정한 값이 아닙니다 ({allowed})")


def render_cell(tag: str, value, rowspan: int = 1) -> str:
    inner = "<br>".join(html.escape(line) for line in cell_lines(value))
    span = f' rowspan="{rowspan}"' if rowspan > 1 else ""
    return f"<{tag}{span}>{inner}</{tag}>"


def render(fmt: dict, data: dict) -> str:
    """머리와 표를 그린 HTML 조각을 낸다."""
    validate(fmt, data)
    columns = fmt["columns"]
    cat_col = fmt.get("category_column") or columns[0]
    categories = fmt.get("categories") or []
    rows = data.get("rows") or []

    if categories:
        groups = [(cat, [r for r in rows if r.get(cat_col) == cat] or [{cat_col: cat}]) for cat in categories]
    else:
        groups = [(None, [r]) for r in rows]

    out = [
        '<div class="report-head">',
        f'  <h2 class="subject">{html.escape(str(data.get("subject", "")))}</h2>',
        f'  <span class="conclusion">{html.escape(str(data.get("conclusion", "")))}</span>',
        "</div>",
        f'<table class="report-table" data-report-format="{html.escape(fmt["name"])}" '
        f'data-report-version="{html.escape(str(fmt["version"]))}">',
        "  <thead><tr>" + "".join(f"<th>{html.escape(c)}</th>" for c in columns) + "</tr></thead>",
        "  <tbody>",
    ]
    cat_first = categories and columns[0] == cat_col
    for cat, members in groups:
        for i, row in enumerate(members):
            cells = []
            for col in columns:
                if cat_first and col == cat_col:
                    if i == 0:
                        cells.append(render_cell("td", cat, len(members)))
                    continue
                cells.append(render_cell("td", row.get(col)))
            out.append("    <tr>" + "".join(cells) + "</tr>")
    out += ["  </tbody>", "</table>"]
    return "\n".join(out)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="합의한 보고 양식으로 표를 그린다")
    ap.add_argument("format_file")
    ap.add_argument("data_file")
    ap.add_argument("-o", "--output")
    ap.add_argument("--fragment", action="store_true", help="머리와 표만 낸다(페이지 틀 없이)")
    args = ap.parse_args()

    with open(args.format_file, "r", encoding="utf-8") as fp:
        fmt = json.load(fp)
    with open(args.data_file, "r", encoding="utf-8") as fp:
        data = json.load(fp)
    try:
        body = render(fmt, data)
    except FormatError as err:
        print(f"그리지 않았습니다: {err}", file=sys.stderr)
        return 1
    page = body if args.fragment else PAGE.format(
        title=html.escape(f"{data.get('subject', '')} {fmt.get('title', '')}".strip()), css=CSS, body=body)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fp:
            fp.write(page)
    else:
        sys.stdout.write(page)
    return 0


if __name__ == "__main__":
    sys.exit(main())
