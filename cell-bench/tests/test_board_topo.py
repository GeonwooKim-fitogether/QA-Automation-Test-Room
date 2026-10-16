"""결과판 '시험실 구성' — 위 띠(제어 PC ─유선 인터넷─ 클라우드 · LiveHub) 와 세트마다 한 줄([세트] ┄블루투스┄ [보드 묶음]).

선 색의 판정은 순수 함수(index.html 의 <topo-link> 블록)라 헤드리스 Chrome 으로 떼어 돌린다(test_board_chart_floor 와 같은 방식).
그리기(<topo-draw> 블록 — HTML 격자)도 전역을 흉내 낸 견본으로 떼어 그려 보고, 줄기 · 가지 색 규칙 · 전원만 · 빈 세트 접기 · 세트별 허브를
DOM 으로 확인한다. 실제 화면(노드 · 누르면 상세 · 좁은 카드)은 임시 data 복사본을 보는 결과판 서버를 찍어 눈으로 확인했다 — 이 저장소 CI 에는
브라우저가 없어 Chrome 이 없으면 건너뛴다. 원문 검사는 브라우저 없이도 돈다.
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
needs_chrome = pytest.mark.skipif(CHROME is None, reason="Chrome/Edge 없음")


def helpers(*names) -> str:
    return "\n".join(re.search(rf"^const {n}=.*$", SRC, re.M).group(0) for n in names)


def block(tag="topo-link") -> str:
    m = re.search(rf"// <{tag}>.*?// </{tag}>", SRC, re.S)
    assert m, f"<{tag}> 블록이 없다"
    return m.group(0)


def run_page(script: str) -> dict:
    page = "<pre id=o></pre><script>" + script + "\ndocument.getElementById('o').textContent='@@'+JSON.stringify(r)+'@@';</script>"
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "t.html"
        f.write_text(page, encoding="utf-8")
        out = subprocess.run([CHROME, "--headless", "--disable-gpu", "--no-sandbox", f"--user-data-dir={d}/p",
                              "--dump-dom", f.as_uri()], capture_output=True, timeout=60).stdout.decode("utf-8", "replace")
    m = re.search(r"@@(.*?)@@", out, re.S)
    assert m, out[-2000:]
    return json.loads(m.group(1))


def run_js(cases: dict) -> dict:
    return run_page(helpers("pad", "dur", "agoTxt") + "\n" + block() + "\nconst r={};" +
                    "".join(f"r[{json.dumps(k)}]=({v});" for k, v in cases.items()))


@needs_chrome
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


# ---------- 그리기 (<topo-draw>) — 전역을 흉내 낸 견본으로 그려 DOM 을 읽는다 ----------

T = 1_800_000_000


def hub(sid, name, alive, total, state="joined", first=0):
    return {"set": sid, "hub": name, "total": total, "alive": alive,
            "boards": [{"serial": first + i, "set": sid, "port": i + 1, "board": f"HRSIM-{first + i:04X}",
                        "state": state if i >= alive else "joined"} for i in range(total)]}


def power(sid, total, first):
    return {"set": sid, "hubs": [f"P{sid}"], "total": total,
            "boards": [{"serial": first + i, "port": i + 1, "board": f"HRSIM-{first + i:04X}", "hub": f"P{sid}"} for i in range(total)]}


def scene(kinds, hubs=None, pw=None, sets=None, wifi="green", boards_age=10, usb=48, usb_limit=None):
    """kinds = 세트 자리 5칸의 종류(run · idle · empty …). 세트 k 의 now 는 셀 24 대가 다 들린다."""
    return {"slots": [{"id": i + 1, "kind": k} for i, k in enumerate(kinds)],
            "hr": {"sets": sets or {}, "hubs": hubs or {}, "power": pw or {}, "boards_t": T - boards_age, "stale_s": 180,
                   "lost_alarm": 3, "usb_devices": usb, "usb_limit": usb_limit},
            "wifi": wifi}


DRAW = r"""
const SC=__SC__;
const NOW=__T__;
Date.now=()=>NOW*1000;
const cells=n=>Array.from({length:24},(_,i)=>({serial:n*100+i,age:i<n?0.3:0.3,waiting:false,hr:70}));
let S={now:{t:NOW,phase:'DISCHARGE',expected:24,cells:cells(24),plug:{on:true,w:60},metrics:{cloud:{enabled:true,ok_t:NOW-5}}}};
let HL={hr:SC.hr,lanes:[{key:'wireless',light:SC.wifi,value:'LiveHub 닿음'}]}, U={tn:null}, NETERR=false, HFAIL=false;
const engineStat=()=>({cls:'ok',text:'실행 중'}), setSlots=()=>SC.slots, liveOK=()=>true, plain=t=>t;
const hrOf=k=>(HL.hr.sets||{})[String(k)]||null, nowFor=k=>S.now;
__HELPERS__
__LINK__
__DRAW__
const host=document.createElement('div');document.body.appendChild(host);topo(host);
const tp=host.querySelector('.tp'), st=tp.getAttribute('style');
const v=n=>(st.match(new RegExp('--'+n+':([^;]+)'))||[])[1];
const r={tl:v('tlc'),tls:v('tls'),tr:v('trc'),trs:v('trs'),nousb:host.querySelector('.tp-top').classList.contains('nousb'),
  gapR:!!host.querySelector('.tp-gap .tr'),
  rows:[...host.querySelectorAll('.tp-row')].map(row=>({set:row.querySelector('.sn').dataset.tn,
    gl:row.querySelector('.gl').className,gr:row.querySelector('.gr').className,bc:row.style.getPropertyValue('--bc'),
    solo:row.querySelector('.hb')?row.querySelector('.hb').classList.contains('solo'):null,
    nohr:(row.querySelector('.nohr')||{}).textContent||null,
    chips:[...row.querySelectorAll('.hc')].map(c=>({k:c.dataset.tn,cls:c.className,t:c.textContent}))})),
  emp:[...host.querySelectorAll('.tp-emp .e')].map(e=>e.textContent),
  meter:(host.querySelector('.tp-pc .meter small')||{}).textContent,
  keys:[...host.querySelectorAll('[data-tn]')].map(b=>b.dataset.tn), buttons:[...host.querySelectorAll('[data-tn]')].every(b=>b.tagName==='BUTTON')};
U.tn='pw3';topo(host);r.pw3=(host.querySelector('#topoDr')||{}).textContent||'';
U.tn='hub1B';topo(host);r.hub1B=(host.querySelector('#topoDr')||{}).textContent||'';
U.tn='pc';topo(host);r.pc=(host.querySelector('#topoDr')||{}).textContent||'';
"""


def draw(sc: dict) -> dict:
    p_block = re.search(r"^const P=\{.*?^\};", SRC, re.S | re.M).group(0)
    js = (DRAW.replace("__SC__", json.dumps(sc, ensure_ascii=False)).replace("__T__", str(T))
          .replace("__HELPERS__", "\n".join([p_block, helpers("I", "pad", "hms", "dur", "agoTxt", "esc", "aliveOf")]))
          .replace("__LINK__", block()).replace("__DRAW__", block("topo-draw")))
    return run_page(js)


@needs_chrome
def test_draw_now_one_watched_set_and_folded_empties():
    # 지금 TestPC: 세트 1 = 허브 A 16 · B 8 (감시), 세트 2 = 짝 표 없음, 3 · 4 · 5 비어 있음
    r = draw(scene(["run", "run", "empty", "empty", "empty"], hubs={"1A": hub(1, "A", 16, 16), "1B": hub(1, "B", 8, 8, first=16)},
                   sets={"1": {"paired": 24, "ok": 24, "lost": [], "wait": [], "extract": False, "timed": True}}))
    assert r["buttons"] and r["keys"] == ["pc", "cloud", "lh", "set1", "hub1A", "hub1B", "set2"]
    assert [x["set"] for x in r["rows"]] == ["set1", "set2"] and r["emp"] == ["세트 3", "세트 4", "세트 5"]   # 빈 세트는 칩 한 줄
    assert r["tl"] == "var(--ok)" and r["tls"] == "solid" and r["tr"] == "var(--ok)" and r["trs"] == "solid"  # 정상이면 줄기 초록
    s1, s2 = r["rows"]
    assert "l-ok" in s1["gl"] and "l-ok" in s2["gl"] and s2["gl"].endswith("end")
    assert [c["k"] for c in s1["chips"]] == ["hub1A", "hub1B"] and all("l-ok" in c["cls"] for c in s1["chips"])
    assert "허브 A" in s1["chips"][0]["t"] and "보드 16/16" in s1["chips"][0]["t"]
    assert s1["gr"] == "gr end2" and s2["gr"] == "gr no" and s1["bc"] == "var(--ok)"                  # USB 줄기는 마지막 감시 줄에서 끝
    assert s2["nohr"].startswith("짝 표 없음") and s2["chips"] == []
    assert not r["nousb"] and r["gapR"] and "48 / 127" in r["meter"] and "64" in r["meter"]


@needs_chrome
def test_draw_five_sets_power_only_and_one_warn_branch():
    lost = [{"serial": 3000 + i} for i in range(2)]
    r = draw(scene(["run"] * 5,
                   hubs={"1A": hub(1, "A", 16, 16), "1B": hub(1, "B", 5, 8, state="unread", first=16),
                         "2A": hub(2, "A", 16, 16, first=100), "2B": hub(2, "B", 8, 8, first=116)},
                   pw={"3": power(3, 24, 3000), "4": power(4, 24, 4000), "5": power(5, 24, 5000)},
                   sets={"3": {"paired": 24, "ok": 22, "lost": lost, "wait": [], "extract": False, "timed": True}}, usb=96))
    rows = {x["set"]: x for x in r["rows"]}
    assert r["tr"] == "var(--ok)"                                             # 허브 하나만 노랑이면 줄기는 초록, 그 가지만 노랑
    assert "l-warn" in rows["set1"]["chips"][1]["cls"] and "l-ok" in rows["set1"]["chips"][0]["cls"]
    assert [c["k"] for c in rows["set2"]["chips"]] == ["hub2A", "hub2B"]       # 세트마다 자기 허브 (같은 이름 A · B)
    pw3 = rows["set3"]["chips"]
    assert [c["k"] for c in pw3] == ["pw3"] and "pw" in pw3[0]["cls"] and "l-" not in pw3[0]["cls"]   # 전원만 — USB 가지 없음
    assert "전원만 · 보드 24" in pw3[0]["t"] and "보드 상태 안 읽음" in pw3[0]["t"]
    assert rows["set3"]["bc"] == "var(--warn)" and rows["set3"]["solo"] is True    # 블루투스 점선 = 그 세트의 끊긴 셀 수
    assert rows["set2"]["gr"] == "gr end2" and rows["set3"]["gr"] == "gr no" and rows["set5"]["gl"].endswith("end")
    assert r["emp"] == [] and "96 / 127" in r["meter"]
    assert "P3" in r["pw3"] and "셀이 받는 심박 수만 본다" in r["pw3"]
    assert "세트 1 · 세트 2" in r["pc"] and "세트 3 · 세트 4 · 세트 5" in r["pc"] and "32개 초과" in r["pc"]


@needs_chrome
def test_draw_faults_hub_gone_all_gone_wifi_down_and_stale_boards():
    gone = hub(1, "B", 0, 8, state="off", first=16)
    r = draw(scene(["run", "run", "empty", "empty", "empty"], hubs={"1A": hub(1, "A", 16, 16), "1B": gone},
                   pw={"2": power(2, 24, 2000)}, wifi="red"))
    s1 = r["rows"][0]
    assert "l-bad" in s1["chips"][1]["cls"] and "USB 없음" in s1["chips"][1]["t"] and r["tr"] == "var(--ok)"
    assert r["tl"] == "var(--bad)" and r["tls"] == "dashed"                   # 시험망 Wi-Fi 가 빨강이면 왼쪽 줄기가 빨강 점선
    assert "USB 선 · 허브 전원을 확인" in r["hub1B"] and "세트 1 허브 B" in r["hub1B"]
    r = draw(scene(["run", "empty", "empty", "empty", "empty"], hubs={"1A": hub(1, "A", 0, 16, state="off"), "1B": gone}))
    assert r["tr"] == "var(--bad)" and r["trs"] == "dashed"                   # 감시 허브가 전부 사라지면 오른쪽 줄기가 끊김
    r = draw(scene(["run", "empty", "empty", "empty", "empty"], hubs={"1A": hub(1, "A", 16, 16)}, boards_age=999, usb=None))
    assert r["tr"] == "var(--line-2)" and "l-none" in r["rows"][0]["chips"][0]["cls"]   # 보드 기록이 오래되면 모름(회색 점선)
    assert "모름 / 127" in r["meter"]


@needs_chrome
def test_draw_mixed_set_and_no_usb_hubs():
    r = draw(scene(["run", "empty", "empty", "empty", "empty"], hubs={"1A": hub(1, "A", 16, 16)}, pw={"1": power(1, 8, 500)}))
    s1 = r["rows"][0]
    assert [c["k"] for c in s1["chips"]] == ["pw1", "hub1A"] and s1["solo"] is False and s1["gr"] == "gr end2"   # 둘 다 그린다
    r = draw(scene(["run", "empty", "empty", "empty", "empty"], pw={"1": power(1, 24, 500)}, usb_limit=40))
    assert r["nousb"] and not r["gapR"] and r["rows"][0]["gr"] == "gr no" and r["tr"] == "var(--line-2)"   # 감시 허브가 없으면 USB 선 없음
    assert "48 / 127" in r["meter"] and "40" in r["meter"] and "실측 한도" in r["pc"]


def test_topology_wiring():
    assert "const cm=(n.metrics||{}).cloud, ck=cloudLink(" in SRC                       # 클라우드 = 엔진의 전송 통계
    assert "const hr=(HL&&HL.hr)||null, bFresh=" in SRC                                  # 허브 · 전원만 · USB = 신호등의 심박 요약
    assert "bt=ho?btLink({boards},s.id,ho.lost,alarm):null" in SRC                      # 세트 → 보드 묶음 블루투스
    assert "hubLink(x,bFresh)" in SRC and "l.key==='wireless'" in SRC                   # 가지 = 허브 · 왼쪽 줄기 = 무선 차선
    assert "function topoDetail(key){" in SRC and "if(key==='pc')" in SRC and "if(key==='lh')" in SRC and "key.startsWith('pw')" in SRC
    assert "['보내지 못하고 버린 수'," in SRC and "포트별 보드" in SRC and "<th>원인</th>" in SRC    # 상세
    assert "@container (max-width:440px)" in SRC and ".tpw{container-type:inline-size}" in SRC   # 좁은 카드는 카드 폭으로
    assert "<button type=\"button\" class=\"nd tp-pc" in SRC                           # 노드는 진짜 버튼 — 키보드로도 연다
