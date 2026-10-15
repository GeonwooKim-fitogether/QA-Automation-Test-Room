"""결과판 '시험실 구성' — 제어 PC → 유선 인터넷 → 클라우드, 제어 PC → USB → 심박 허브 A · B, 허브 → 세트(블루투스)의 선 색.

선 색의 판정은 순수 함수(index.html 의 <topo-link> 블록)라 헤드리스 Chrome 으로 떼어 돌린다(test_board_chart_floor 와 같은 방식).
그리기(노드 · 선 · 누르면 상세 · 좁은 화면)는 임시 data 복사본을 보는 결과판 서버를 Playwright 로 찍어 확인했다 — 이 저장소 CI 에는
브라우저가 없어 그 검사는 pytest 밖이다. 여기서는 그 동작을 떠받치는 줄이 빠지지 않았는지도 원문으로 못 박는다.
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
    m = re.search(r"// <topo-link>.*?// </topo-link>", SRC, re.S)
    assert m, "<topo-link> 블록이 없다"
    helpers = [re.search(rf"^const {n}=.*$", SRC, re.M).group(0) for n in ("pad", "dur", "agoTxt")]
    return "\n".join(helpers) + "\n" + m.group(0)


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
def test_link_colors_pure_logic():
    N = 1_000_000
    r = run_js({
        # 클라우드: 최근 성공 → 초록 · 실패가 성공보다 뒤 → 빨강 · 오래된 성공 → 노랑 · 꺼짐 · 엔진 소식 없음 · 통계 없음 → 회색
        "ok": f"cloudLink({{enabled:true,ok_t:{N - 20},fail_t:{N - 4000}}},{N},true)",
        "fail": f"cloudLink({{enabled:true,ok_t:{N - 400},fail_t:{N - 30}}},{N},true)",
        "old": f"cloudLink({{enabled:true,ok_t:{N - 900}}},{N},true)",
        "off": f"cloudLink({{enabled:false,ok_t:{N - 5}}},{N},true)",
        "dead": f"cloudLink({{enabled:true,ok_t:{N - 5}}},{N},false)",
        "none": f"cloudLink(null,{N},true)",
        # 허브: 다 받음 · 일부 · 하나도 · 기록 오래됨
        "hall": "hubLink({total:16,alive:16},true)", "hpart": "hubLink({total:8,alive:4},true)",
        "hzero": "hubLink({total:8,alive:0},true)", "hstale": "hubLink({total:8,alive:8},false)",
        # 블루투스: 그 허브 보드와 짝인 셀 중 끊긴 셀 수
        "b0": "btLink({boards:[{set:1,serial:1},{set:1,serial:2},{set:2,serial:9}]},1,[],3)",
        "b1": "btLink({boards:[{set:1,serial:1},{set:1,serial:2}]},1,[{serial:2},{serial:7}],3)",
        "b3": "btLink({boards:[{set:1,serial:1},{set:1,serial:2},{set:1,serial:3}]},1,[{serial:1},{serial:2},{serial:3}],3)",
        "bnone": "btLink({boards:[{set:2,serial:9}]},1,[],3)",
    })
    assert r["ok"] == {"cls": "ok", "sub": "마지막 전송 20초 전"}
    assert r["fail"]["cls"] == "bad" and r["fail"]["sub"].startswith("실패 중 · 마지막 성공")
    assert r["old"]["cls"] == "warn" and r["off"] == {"cls": "none", "sub": "클라우드 꺼짐"}
    assert r["dead"] == {"cls": "none", "sub": "엔진 소식 없음"} and r["none"]["cls"] == "none"
    assert r["hall"] == {"cls": "ok", "sub": "보드 16/16"} and r["hpart"] == {"cls": "warn", "sub": "보드 4/8"}
    assert r["hzero"]["cls"] == "bad" and r["hstale"] == {"cls": "none", "sub": "보드 ?/8 · 기록 없음"}
    assert r["b0"] == {"cls": "ok", "n": 0, "total": 2} and r["b1"] == {"cls": "warn", "n": 1, "total": 2}
    assert r["b3"]["cls"] == "bad" and r["bnone"] is None


def test_topology_wiring():
    assert "const cm=(n.metrics||{}).cloud, ck=cloudLink(" in SRC                       # 클라우드 = 엔진의 전송 통계
    assert "const hr=(HL&&HL.hr)||null, hubs=(hr&&hr.hubs)||{}" in SRC                  # 허브 = 신호등의 심박 요약
    assert "btLink(hubs[hkey],s.id,((hrOf(s.id)||{}).lost),alarm)" in SRC                # 허브 → 세트 블루투스
    assert "data-tn=\"${key}\" role=\"button\" tabindex=\"0\"" in SRC and "function topoDetail(key){" in SRC
    assert "['보내지 못하고 버린 수'," in SRC and "포트별 보드" in SRC and "<th>원인</th>" in SRC    # 상세 셋
    assert "ev.key==='Enter'||ev.key===' '" in SRC                                      # 키보드로도 연다
    assert "sm=W<520" in SRC                                                            # 좁은 화면 배치
