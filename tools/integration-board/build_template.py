#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""디자인 시안(HTML 한 장) → 스킬 템플릿(assets/board_template.html) 파생 도구.

언제 쓰나
---------
**평소에는 쓰지 않는다.** 템플릿은 지금 직접 손보는 정본이고, 작은 수정은 그 파일을
고치면 된다. 이 도구는 오직 한 경우에만 쓴다 — **디자이너가 판을 새로 그린 시안을
가져왔을 때**, 그 시안의 CSS와 마크업을 손으로 옮겨 적지 않고 기계적으로 옮기기 위해서다.
손으로 옮기면 오타 한 글자로 시안과 화면이 달라지고, 그 차이는 나중에 아무도 못 찾는다.

무엇을 하나
-----------
1. 시안에서 `<script>` 이전(머리 + CSS + 마크업)만 취한다.
2. 그 마크업에 박혀 있는 **고정 문구 자리를 빈 컨테이너로 바꾼다**(치환표 REPLACEMENTS).
   문구는 전부 엔진의 text에서 오므로, 템플릿에는 어떤 언어의 낱말도 남으면 안 된다.
3. `<script>` 블록은 **기존 템플릿에서 그대로 가져온다**(기본값). 검증된 렌더러를
   시안 쪽 스크립트로 되돌리지 않기 위해서다. 다른 것을 쓰려면 --script로 지정한다.
4. 산출물을 검사한다 — 주입 지점 1개, `<html lang>` 표식 1개, 화면에 남은 한글 0.

경고
----
이 도구는 **손으로 고친 템플릿을 통째로 갈아엎는다.** 그래서 기본값은 새 파일
(`board_template.generated.html`)로 쓰는 것이고, 정본을 덮어쓰려면 --force가 필요하다.
덮어쓰기 전에 반드시 diff로 무엇이 사라지는지 확인한다.

실행
----
    python3 tools/build_template.py --mockup <시안.html>            # 미리보기 파일로 출력
    python3 tools/build_template.py --mockup <시안.html> --force     # 정본을 덮어씀
"""

import argparse
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
# 이 도구는 창고 전용이라 스킬 폴더 밖(tools/integration-board/)에 있다.
SKILL = HERE.parents[1] / ".claude" / "skills" / "integration-board"
ASSETS = SKILL / "assets"
TEMPLATE = ASSETS / "board_template.html"
MARKER = "/*__BOARD_DATA__*/null"
LANG_MARKER = '<html lang="ko">'

# 시안에 박혀 있는 고정 문구 → 빈 컨테이너. 모든 항목은 "정확히 1회 일치"여야 한다.
# 시안의 문구가 조금이라도 달라지면 여기서 멈춘다(조용히 어긋난 템플릿을 만들지 않는다).
REPLACEMENTS = [
    ("문서 제목",
     re.compile(r"<title>.*?</title>", re.S),
     "<title>Integration Board</title>"),
    ("머리 — 제목 3종",
     re.compile(r'<span class="eyebrow">.*?</span>\s*<h1>.*?</h1>\s*<p class="sub">.*?</p>', re.S),
     '<span class="eyebrow" id="eyebrow"></span>\n      <h1 id="btitle"></h1>\n'
     '      <p class="sub" id="bsub"></p>'),
    ("테마 버튼",
     re.compile(r'<button class="ghost" id="themebtn" type="button">.*?</button>', re.S),
     '<button class="ghost" id="themebtn" type="button"></button>'),
    ("범례",
     re.compile(r'<div class="legend">.*?</div>\s*(?=<!--|\s*<section)', re.S),
     '<div class="legend" id="legend"></div>\n\n  '),
    ("밴드 1 머리",
     re.compile(r'<div class="bhead">\s*<span class="bn">[^<]*1</span>.*?</div>', re.S),
     '<div class="bhead" id="bhead1"></div>'),
    ("밴드 2 머리(보기 전환 탭 포함)",
     re.compile(r'<div class="bhead">\s*<span class="bn">[^<]*2</span>.*?</div>\s*</div>', re.S),
     '<div class="bhead" id="bhead2"></div>'),
    ("밴드 2 안내문",
     re.compile(r'<p class="b2note">.*?</p>', re.S),
     '<p class="b2note" id="b2note"></p>'),
    ("공용 자산 레인 제목",
     re.compile(r'<div class="assets">\s*<div class="slabel">.*?</div>', re.S),
     '<div class="assets" id="assetsBlock">\n          <div class="slabel" id="assetsTitle"></div>'),
    ("밴드 3 구획과 머리",
     re.compile(r'<section class="band">\s*<div class="bhead">\s*<span class="bn">[^<]*3</span>.*?</div>', re.S),
     '<section class="band" id="band3">\n    <div class="bhead" id="bhead3"></div>'),
    ("간트 범례",
     re.compile(r'<div class="gg-legend">.*?</div>', re.S),
     '<div class="gg-legend" id="ganttlegend"></div>'),
    ("연결 보기 칩",
     re.compile(r'<span class="tk">.*?</span>(\s*<b id="tchipname">)', re.S),
     r'<span class="tk" id="tchiplabel"></span>\1'),
    ("연결 보기 해제 버튼",
     re.compile(r'<button type="button" id="tchipoff">.*?</button>', re.S),
     '<button type="button" id="tchipoff"></button>'),
]

HANGUL = re.compile(r"[가-힣ㄱ-ㆎ]")


def extract_script(path):
    text = path.read_text(encoding="utf-8")
    m = re.search(r"<script>\n(.*)</script>", text, re.S)
    if not m:
        sys.exit(f"기존 템플릿에서 <script> 블록을 찾지 못했습니다: {path}")
    return m.group(1)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--mockup", required=True, help="디자이너가 준 시안 HTML")
    ap.add_argument("--script", help="쓸 렌더러 JS 파일 (기본: 현재 템플릿의 <script> 블록)")
    ap.add_argument("--out", help="산출 경로 (기본: assets/board_template.generated.html)")
    ap.add_argument("--force", action="store_true", help="정본 board_template.html을 덮어쓴다")
    args = ap.parse_args(argv)

    mockup = pathlib.Path(args.mockup)
    if not mockup.exists():
        sys.exit(f"시안을 찾을 수 없습니다: {mockup}")
    out = pathlib.Path(args.out) if args.out else \
        (TEMPLATE if args.force else ASSETS / "board_template.generated.html")

    html = mockup.read_text(encoding="utf-8")
    head, sep, _ = html.partition("<script>")
    if not sep:
        sys.exit("시안에서 <script> 블록을 찾지 못했습니다 — 판을 잘못 준 것 같습니다")
    if head.count("<script") != 0:
        sys.exit("시안에 <script>가 둘 이상입니다 — 이 도구의 가정이 깨졌습니다")

    for name, pattern, repl in REPLACEMENTS:
        n = len(pattern.findall(head))
        if n != 1:
            sys.exit(f"치환 실패 — {name}: 시안에서 {n}회 일치(정확히 1회여야 합니다)")
        head = pattern.sub(repl, head, count=1)

    head = re.sub(r"<html[^>]*>", LANG_MARKER, head, count=1)
    script = pathlib.Path(args.script).read_text(encoding="utf-8") if args.script \
        else extract_script(TEMPLATE)
    result = head + "<script>\n" + script + "</script>\n</body>\n</html>\n"

    # ── 산출 검사 — 조용히 어긋난 템플릿을 내보내지 않는다 ──────────────
    problems = []
    if result.count(MARKER) != 1:
        problems.append(f"주입 지점 {MARKER}이 {result.count(MARKER)}개입니다(1개여야 함)")
    if result.count(LANG_MARKER) != 1:
        problems.append(f"{LANG_MARKER} 표식이 {result.count(LANG_MARKER)}개입니다(1개여야 함)")
    visible = re.sub(r"<!--.*?-->", "", head, flags=re.S)
    visible = re.sub(r"/\*.*?\*/", "", visible, flags=re.S)
    leftovers = sorted(set(HANGUL.findall(visible)))
    if leftovers:
        problems.append("템플릿 본문에 화면 문구가 남아 있습니다(전부 엔진 text에서 와야 합니다): "
                        + "".join(leftovers[:40]))
    if problems:
        for p in problems:
            print("  · " + p, file=sys.stderr)
        sys.exit("산출 검사 실패 — 아무 것도 쓰지 않았습니다")

    if out == TEMPLATE:
        print("경고: 정본 템플릿을 덮어씁니다. 손으로 고친 부분이 사라질 수 있습니다.")
    out.write_text(result, encoding="utf-8")
    print(f"wrote {out}  ({len(result):,} bytes, {result.count(chr(10)) + 1} lines)")
    if out != TEMPLATE:
        print(f"정본과 비교: diff {TEMPLATE} {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
