"""추이 분석의 세트 선택 — 선택 로직(순수 함수)과 배선이 그대로 있는지 못 박는다.

순수 함수(trendSetIds · pickTrendSet)는 index.html 의 <trend-set> 블록을 떼어 헤드리스 Chrome(JS 엔진)으로 돌려 본다.
Chrome 이 없는 환경에서는 그 검사만 건너뛴다. 화면 렌더(클릭)는 저장소에 브라우저 검사 도구가 없어 pytest 밖이다.
"""
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "board" / "index.html").read_text(encoding="utf-8")
CHROMES = [shutil.which("chrome"), shutil.which("msedge"),
           r"C:\Program Files\Google\Chrome\Application\chrome.exe",
           r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"]
CHROME = next((c for c in CHROMES if c and Path(c).exists()), None)


def block() -> str:
    m = re.search(r"// <trend-set>.*?// </trend-set>", SRC, re.S)
    assert m, "<trend-set> 블록이 없다"
    return m.group(0)


def run_js(cases: dict) -> dict:
    page = ("<pre id=o></pre><script>" + block() + "\nconst r={};" +
            "".join(f"r[{json.dumps(k)}]=({v});" for k, v in cases.items()) +
            "document.getElementById('o').textContent='@@'+JSON.stringify(r)+'@@';</script>")
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "t.html"
        f.write_text(page, encoding="utf-8")
        out = subprocess.run([CHROME, "--headless", "--disable-gpu", "--no-sandbox", f"--user-data-dir={d}/p",
                              "--dump-dom", f.as_uri()], capture_output=True, timeout=60).stdout.decode("utf-8", "replace")
    return json.loads(re.search(r"@@(.*?)@@", out, re.S).group(1))


@pytest.mark.skipif(CHROME is None, reason="Chrome/Edge 없음")
def test_trend_set_pure_logic():
    hl = "{sets:[{id:2,running:false},{id:1,running:true},{id:5,detect:true},{id:3}]}"
    r = run_js({
        "ids": f"trendSetIds({hl})",
        "ids_none": "trendSetIds(null)",
        "ids_empty": "trendSetIds({sets:[]})",
        "ids_new": "trendSetIds({sets:[{id:1},{id:2},{id:3},{id:4}]})",      # 세트가 늘면 선택지도 는다(하드코딩 없음)
        "pick_ok": f"pickTrendSet(2,{hl})",
        "pick_unlisted": f"pickTrendSet(4,{hl})",                           # 목록에 없으면 첫 세트
        "pick_detect": f"pickTrendSet(5,{hl})",                             # 미등록 감지는 선택지가 아니다
        "pick_before_health": "pickTrendSet(2,null)",                       # 신호등 전에는 고른 값을 믿는다(D-1)
        "pick_garbage": "pickTrendSet('x',null)",
    })
    assert r["ids"] == [1, 2, 3]
    assert r["ids_none"] == [1] and r["ids_empty"] == [1]
    assert r["ids_new"] == [1, 2, 3, 4]
    assert r["pick_ok"] == 2 and r["pick_unlisted"] == 1 and r["pick_detect"] == 1
    assert r["pick_before_health"] == 2 and r["pick_garbage"] == 1


def test_trend_set_wiring():
    assert "if(U.view==='trend')return pickTrendSet(U.tset,HL);const k=+U.set;if(U.view!=='set'||!(k>1))return 1;" in SRC   # 기존 curSet 의미 보존
    assert "return !HL?k:runningIds().includes(k)?k:1;" in SRC
    assert "tset:U.tset,cell:U.cell" in SRC                                  # saveU 가 기억
    assert 'id="trSetPick"' in SRC and "renderTrendPick();renderTrendStats()" in SRC
    assert "if(U.view==='trend'&&cs!==curSet())return;" in SRC               # 옛 세트 응답 버림
    assert "sig=`${S.set}|${C.length}|" in SRC and "sig=`${S.set}|${D.length}|" in SRC   # 세트가 바뀌면 캐시 무효
    assert "runSlot().id" not in SRC[SRC.index("function renderTrendStats"):SRC.index("V.proc=")]   # 세트 1 고정 문구 제거
    assert "const hashOf=(v,set)=>v==='set'?`#set-${set}`:v==='reg'?'#register':`#${v}`;" in SRC   # 해시 규약 그대로


def test_deploy_copy_matches_board():
    dep = ROOT / "deploy" / "board" / "index.html"
    assert dep.read_text(encoding="utf-8") == SRC, "python tools/build_cloud_board.py 로 배포 사본을 다시 만든다"
