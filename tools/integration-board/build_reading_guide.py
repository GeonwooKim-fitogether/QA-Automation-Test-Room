#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""READING-GUIDE.html 을 만든다 — 예시 보드를 실제로 띄워 화면을 찍고 문서에 끼워 넣는다.

왜 이 스크립트가 있나. 보드 읽는 법은 글로만 적으면 전달되지 않는다. "카드를 누르면
아래 두 밴드에서 얽힌 것만 밝아진다"는 문장은 그 화면을 본 사람에게만 뜻이 통한다.
그래서 이 안내서는 실제 화면 그림을 본문으로 삼는다.

그런데 그림을 손으로 찍어 붙이면 화면이 바뀐 뒤에도 옛 그림이 남는다. 그것이 이 창고가
없애려는 드리프트(코드와 문서가 서로 다른 사실을 말하는 상태)의 그림판이다. 그래서
그림 찍기와 문서 조립을 이 스크립트 하나로 묶어, 화면이 바뀌면 한 번 돌려 다시 만든다.

무엇을 하나:
  1. 번들 예시(board.config.example.json + board.example.json)로 보드를 렌더한다.
     그 예시는 '해소 작업 있음'과 '없음'이 한 판에 다 나오게 채워져 있다.
  2. 같은 예시에서 works[].fixes 를 모두 지운 판을 하나 더 렌더한다 —
     '해소 작업 미보고'(회색)는 데이터에 fixes 가 하나도 없어야 나오기 때문이다.
  3. Chromium 으로 두 판을 띄워 아홉 장을 찍는다. 표식은 내용을 덮지 않는다 —
     테두리 링과 요소 **바깥**의 번호만 쓴다(제목을 가리면 그림이 설명을 잃는다).
  4. READING-GUIDE.src.html 의 {{shot:이름}} 자리에 data URI 로 끼워 넣어
     자체완결 HTML 하나(READING-GUIDE.html)를 만든다.

실행:  python3 tools/integration-board/build_reading_guide.py
준비물: playwright(파이썬) + /opt/pw-browsers/chromium. 없으면 무엇이 없는지 말하고 멈춘다.
"""

import base64
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SKILL = os.path.join(ROOT, ".claude", "skills", "integration-board")
ASSETS = os.path.join(SKILL, "assets")
ENGINE = os.path.join(ASSETS, "integration_board_engine.py")
SRC = os.path.join(SKILL, "READING-GUIDE.src.html")
OUT = os.path.join(SKILL, "READING-GUIDE.html")
CHROMIUM = "/opt/pw-browsers/chromium"

# 표식 색 — 빨강은 "여기를 보라", 청록은 "연결된 것", 초록은 "정상 쪽 신호".
RED, TEAL, GREEN = "#c0392b", "#2b7a78", "#1f7a4d"

# 요소에 테두리 링을 두르고, 필요하면 요소 **왼쪽 바깥**에 번호를 붙인다.
# 내용 위에 겹치지 않는 것이 이 함수의 유일한 규칙이다.
MARK_JS = """
(items) => {
  const wrap = document.getElementById('wrap');
  // 같은 것이 여러 칸에 있으면 전부 링을 두른다 — 캡션이 "두 칸"이라 적었는데
  // 링이 하나만 그려지면 그림과 설명이 어긋난다.
  items.forEach(([sel, n, color]) => {
    document.querySelectorAll(sel).forEach(e => {
      e.style.outline = `3px solid ${color}`;
      e.style.outlineOffset = '3px';
    });
    const el = document.querySelector(sel);
    if (!el || !n) return;
    const r = el.getBoundingClientRect(), w = wrap.getBoundingClientRect();
    const b = document.createElement('div');
    b.textContent = n;
    b.style.cssText = `position:absolute;z-index:60;
      top:${r.top - w.top + r.height / 2 - 14}px; left:${r.left - w.left - 34}px;
      width:28px;height:28px;border-radius:999px;background:${color};color:#fff;
      font:800 16px/28px ui-sans-serif,system-ui;text-align:center;
      box-shadow:0 0 0 3px #fff,0 2px 6px rgba(0,0,0,.35)`;
    wrap.appendChild(b);
  });
}
"""

# 요소를 문서 좌표로 잘라 낸다. 화면 밖에 있어도 되도록 full_page 로 찍는다.
BOX_JS = """e => { const r = e.getBoundingClientRect();
    return {x: r.left + scrollX, y: r.top + scrollY, w: r.width, h: r.height}; }"""


def render(config, data, out):
    subprocess.run([sys.executable, ENGINE, "--config", config, "--data", data, "--out", out],
                   check=True, capture_output=True)


def main():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("playwright(파이썬)가 없습니다: pip install playwright==1.56.0")
    if not os.path.exists(CHROMIUM):
        sys.exit(f"Chromium을 찾을 수 없습니다: {CHROMIUM}")

    cfg = os.path.join(ASSETS, "board.config.example.json")
    tmp = tempfile.mkdtemp(prefix="reading-guide-")
    main_html = os.path.join(tmp, "board.html")
    nofix_html = os.path.join(tmp, "board-nofix.html")

    render(cfg, os.path.join(ASSETS, "board.example.json"), main_html)

    # fixes 를 모두 지운 판 — '해소 작업 미보고' 회색을 보이기 위한 것.
    d = json.load(open(os.path.join(ASSETS, "board.example.json"), encoding="utf-8"))
    for w in d["works"]:
        w.pop("fixes", None)
    nofix_json = os.path.join(tmp, "nofix.json")
    json.dump(d, open(nofix_json, "w", encoding="utf-8"), ensure_ascii=False)
    render(cfg, nofix_json, nofix_html)

    shots = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROMIUM)
        page = browser.new_page(viewport={"width": 1500, "height": 1200})

        def grab(name, sel=None, pad=0, quality=88):
            """이름을 붙여 한 장 찍는다. sel 이 없으면 전체.

            전체 화면은 판의 짜임을 보는 그림이라 글자를 읽을 일이 적으므로 화질을 낮춘다.
            잘라 낸 그림은 그 안의 글자를 실제로 읽어야 하므로 화질을 지킨다.
            """
            if sel is None:
                shots[name] = page.screenshot(full_page=True, type="jpeg", quality=quality)
                return
            box = page.eval_on_selector(sel, BOX_JS)
            shots[name] = page.screenshot(full_page=True, type="jpeg", quality=quality, clip={
                "x": max(box["x"] - pad, 0), "y": max(box["y"] - pad, 0),
                "width": box["w"] + pad * 2, "height": box["h"] + pad * 2})

        def open_main():
            page.goto("file://" + main_html)
            page.wait_for_timeout(500)

        # 1. 전체 — 세 밴드의 머리에 링
        open_main()
        page.evaluate(MARK_JS, [["#bhead1", "", TEAL], ["#bhead2", "", TEAL], ["#bhead3", "", TEAL]])
        grab("01-three-bands", quality=68)

        # 2. 판정 램프
        open_main()
        grab("02-verdict", "#verdict", pad=12)

        # 3. 밴드 1 카드 열 — 가장 급한 카드에 링 + 왼쪽 바깥에 1번
        open_main()
        page.evaluate(MARK_JS, [['[data-tid="ov:ov-spine"]', "1", RED]])
        grab("03-lanes", "#lanes", pad=40)

        # 4·5. 카드를 눌러 연결 보기를 켠 상태 — 전체, 그리고 밴드 2만
        open_main()
        page.click('[data-tid="ov:ov-spine"]')
        page.wait_for_timeout(700)
        grab("04-trace", quality=68)
        page.evaluate(MARK_JS, [['[data-tid="work:PR122"]', "", TEAL]])
        grab("05-trace-band2", "#kb", pad=14)

        # 6. 밴드 3 — 해소 작업 두 줄에 링(없음은 빨강, 있음은 초록)
        open_main()
        page.evaluate(MARK_JS, [[".chk-fx.no", "", RED],
                                [".chk-fx:not(.no):not(.nd)", "", GREEN]])
        grab("06-checks", "#checks", pad=14)

        # 8. 간트
        open_main()
        page.click("#tabGantt")
        page.wait_for_timeout(600)
        grab("07-gantt", "#gantt", pad=10)

        # 9. 이 보드가 못 보는 것을 적은 문단
        open_main()
        grab("08-honesty", "#honesty", pad=10)

        # 7. fixes 가 없는 판 — 회색 '해소 작업 미보고'
        page.goto("file://" + nofix_html)
        page.wait_for_timeout(500)
        page.evaluate(MARK_JS, [[".chk-fx.nd", "", "#7d8a92"]])
        grab("09-checks-unknown", "#checks", pad=14)

        browser.close()

    src = open(SRC, encoding="utf-8").read()
    missing = []

    def sub(m):
        name = m.group(1)
        if name not in shots:
            missing.append(name)
            return m.group(0)
        b64 = base64.b64encode(shots[name]).decode("ascii")
        return (f'<img class="shot" alt="{name}" '
                f'src="data:image/jpeg;base64,{b64}">')

    html = re.sub(r"\{\{shot:([A-Za-z0-9_-]+)\}\}", sub, src)
    if missing:
        sys.exit(f"원본이 찾는 그림이 없습니다: {', '.join(sorted(set(missing)))}")
    left = re.findall(r"\{\{shot:[^}]+\}\}", html)
    if left:
        sys.exit(f"끼워 넣지 못한 자리가 남았습니다: {left}")

    used = sorted(shots)
    open(OUT, "w", encoding="utf-8").write(html)
    print(f"READING-GUIDE.html 을 다시 만들었습니다 → {os.path.relpath(OUT, ROOT)}")
    print(f"  그림 {len(used)}장 · {os.path.getsize(OUT):,} bytes")
    for name in used:
        print(f"    {name:<20} {len(shots[name]):>8,} bytes")


if __name__ == "__main__":
    main()
