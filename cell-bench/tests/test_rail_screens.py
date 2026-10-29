"""레일 시험 화면 — 결과판 첫 화면에서 클릭만으로 레일 시험 목록 · 결과에 닿는지(진입점), 클라우드판에서는 메뉴가 숨는지,
그리고 화면 어댑터가 v1 · v4 판정 파일을 qc-protocol-v4.md 7-2 · 7-6 대로 옮기는지 본다.

브라우저 검사는 헤드리스 Chrome 을 한 번만 띄운다(test_board_trend_set 과 같은 --dump-dom 방식). 클릭은 시험 서버가 내보내는
시험 페이지(/__e2e.html)가 같은 출처의 iframe 안 결과판을 눌러 가며 하고, 본 것을 JSON 으로 남긴다 — 새 의존성 없이 진입점에서
출발하는 흐름을 그대로 밟는다. Chrome/Edge 가 없으면 그 검사만 건너뛰고, 소스 검사(아래쪽)는 늘 돈다.
"""
import json
import re
import shutil
import subprocess
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import serve_board as sb
from cellbench.control import PinGuard

ROOT = Path(__file__).resolve().parents[1]
FIX = Path(__file__).resolve().parent / "fixtures" / "rail"
SRC = (ROOT / "board" / "index.html").read_text(encoding="utf-8")
LIVE = (ROOT / "board" / "rail-live.html").read_text(encoding="utf-8")
CHROMES = [shutil.which("chrome"), shutil.which("msedge"),
           r"C:\Program Files\Google\Chrome\Application\chrome.exe",
           r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"]
CHROME = next((c for c in CHROMES if c and Path(c).exists()), None)

V1 = "protocol_20261029-155408.json"         # 실제 v1 시험(한글 키 verdict.판정)
SIM = "protocol_20261029-172000.json"        # v4 시뮬레이터 A · C, sog_band 등급 경계 4개
VOID = "protocol_20261029-193000.json"       # v4 장비 무효
V5 = "protocol_20261029-210000.json"         # 결과판이 모르는 판
FAC = "protocol_20261030-090000.json"        # v4 공장, sog_band 등급 경계 없음, gate_* 갈래 보류
REP = "protocol_20261030-140000.json"        # v4 중계기, gps_rx 정보용


def rail_block() -> str:
    m = re.search(r"// <rail-adapt>.*?// </rail-adapt>", SRC, re.S)
    assert m, "<rail-adapt> 블록이 없다"
    return m.group(0)


# 시험 페이지 — ① 클라우드판(/cloud/#rail)에 레일 메뉴가 없는지 ② 첫 화면(/)에서 클릭으로 목록 → 결과 ③ 어댑터 규칙.
# 결과는 <pre id=o> 에 @@JSON@@ 으로 남긴다. 기다림은 setTimeout 이라 --virtual-time-budget 으로 빨리 감긴다
HARNESS = """<!doctype html><meta charset="utf-8"><pre id="o"></pre><div id="box"></div>
<script>
const out={}, sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function until(fn,ms=30000){const t0=Date.now();while(Date.now()-t0<ms){try{const v=fn();if(v)return v;}catch(e){}await sleep(100);}return null;}
function frame(src){const f=document.createElement('iframe');f.style.cssText='width:1440px;height:900px';f.src=src;document.getElementById('box').appendChild(f);
  return new Promise(r=>f.onload=()=>r(f));}
const RAIL=(function(){ /*BLOCK*/
  return {adaptRun,listModel,verOf};})();
(async()=>{
 try{
  // ① 클라우드판 — 주소로 #rail 을 열어도 관제로, 내비에 레일 시험이 없다
  const cf=await frame('/cloud/#rail');const cd=()=>cf.contentDocument;
  await until(()=>cd().querySelector('#view h1'));
  out.cloud={railNav:!!cd().querySelector('#nav button[data-v="rail"]'),perfNav:!!cd().querySelector('#nav button[data-v="perf"]'),
    navN:cd().querySelectorAll('#nav button').length,h1:cd().querySelector('#view h1').textContent,hash:cf.contentWindow.location.hash,liveHidden:cd().getElementById('railLive').hidden};
  cf.remove();
  // ② 제어 PC 결과판 — 첫 화면에서 출발
  const f=await frame('/');const d=()=>f.contentDocument;
  const nav=await until(()=>d().querySelector('#nav button[data-v="rail"]'));
  await until(()=>d().querySelector('#view h1'));
  out.first={h1:d().querySelector('#view h1').textContent,liveHidden:d().getElementById('railLive').hidden};
  nav.click();
  await until(()=>d().querySelectorAll('#lst tr[data-run]').length>=7);
  const a=d().getElementById('railLive');
  out.list={hash:f.contentWindow.location.hash,rows:[...d().querySelectorAll('#lst tr[data-run]')].map(r=>r.dataset.run),
    navCurrent:(d().querySelector('#nav button[aria-current="page"]')||{}).dataset.v,
    unsup:(d().querySelector('#lst tr[data-run="__V5__"]')||{}).textContent||'',
    live:{hidden:a.hidden,href:a.getAttribute('href'),target:a.target}};
  d().querySelector('#lst tr[data-run="__SIM__"]').click();
  await until(()=>d().querySelectorAll('#f1 .dt[data-s]').length===24&&d().querySelector('#f2 table.mx')&&d().querySelector('#f3 svg')&&d().querySelector('#f4 svg'));
  const v=d().getElementById('railV');
  out.run={hash:f.contentWindow.location.hash,h1:d().querySelector('#view h1').textContent,tiles:d().querySelectorAll('#f1 .dt[data-s]').length,
    rows:d().querySelectorAll('#f2 tbody tr').length,f3:d().querySelectorAll('#f3 svg circle').length,f4:d().querySelectorAll('#f4 svg circle').length,
    legend:d().getElementById('f1l').textContent,ship:v.textContent.includes('대조 시험 전에는 합격을 출하 근거로 쓰지 않는다'),
    critNull:/\\bnull\\b/.test(d().querySelector('#f2 thead').textContent),crumb:d().getElementById('crumb').textContent,
    liveHidden:d().getElementById('railLive').hidden};
  // 위치 표시(crumb)로 목록에 돌아간다
  d().querySelector('#crumb button[data-v="rail"]').click();
  await until(()=>d().querySelectorAll('#lst tr[data-run]').length>=7);
  out.back=f.contentWindow.location.hash;
  const lv=await fetch('/rail-live');out.livePage={status:lv.status,title:/레일 시험 · 실시간/.test(await lv.text())};
  // ③ 어댑터 규칙 — 서버가 넘기는 판정 파일 그대로
  const raw={};for(const n of ['__V1__','__SIM__','__VOID__','__V5__','__FAC__','__REP__'])raw[n]=await (await fetch('/api/rail/run?name='+n)).json();
  const M=n=>RAIL.adaptRun(n,raw[n]);
  const kinds=m=>Object.fromEntries((m.items||[]).map(i=>[i.id,i.kind]));
  const v1=M('__V1__'), sim=M('__SIM__'), vo=M('__VOID__'), v5=M('__V5__'), fac=M('__FAC__'), rep=M('__REP__');
  out.adapt={
    ver:[v1.ver,sim.ver,v5.ver,RAIL.verOf({})],
    v1:{counts:v1.counts,kinds:kinds(v1),speed:v1.speed&&v1.speed.src,accel:v1.accel,has:v1.has,c11620:v1.cells['11620'].verdict,review:Object.values(v1.cells).reduce((s,c)=>s+c.review.length,0)},
    sim:{counts:sim.counts,sample:sim.sample,kinds:kinds(sim),speed:sim.speed.type,edges:sim.speed.edges,edgeStatus:sim.speed.edgeStatus,accel:sim.accel.panels.map(p=>p.id),
      flag:sim.cells['10681'].items.gate_on.j,normal:sim.cells['10677'].items.gate_on.j,hold:sim.cells['10693'].verdict,holdItem:sim.cells['10693'].items.lock.j,info:sim.cells['10677'].items.sog_lag.j},
    void:{counts:vo.counts,rig:vo.rig.valid,reasons:vo.rig.reasons.length},
    v5:{unsupported:!!v5.unsupported,protocol:v5.protocol},
    fac:{kinds:kinds(fac),edges:fac.speed.edges},
    rep:{sky:rep.top.sky,kinds:kinds(rep)},
  };
  // sky=repeater 인 파일의 GPS 항목(gps_move · pos_* · sog_*)은 갈래가 합불이어도 정보용으로 보인다(7-2)
  const fake={conditions:{protocol:'v4',sky:'repeater'},criteria:{sog_band:{kind:'합불'},clock:{kind:'합불'}},
    cells:{'1':{items:{sog_band:{value:3,verdict:'pass'},clock:{value:5,verdict:'fail'}},verdict:{overall:'fail',flags:[],reasons:['x']}}}};
  const fm=RAIL.adaptRun('protocol_20261030-000000.json',fake);
  out.forced={kinds:kinds(fm),forced:fm.items.filter(i=>i.forced).map(i=>i.id),j:fm.cells['1'].items.sog_band.j,clock:fm.cells['1'].items.clock.j};
  // 목록 줄 — 서버 요약(GET /api/rail/runs)을 그대로
  out.list2=(await (await fetch('/api/rail/runs')).json()).map(s=>{const m=RAIL.listModel(s);return [m.name,m.ver,m.unsupported,m.counts['합격'],m.rig];});
 }catch(e){out.error=String(e&&e.stack||e);}
 document.getElementById('o').textContent='@@'+JSON.stringify(out)+'@@';
})();
</script>"""


def harness() -> str:
    page = HARNESS.replace("/*BLOCK*/", rail_block() + "\n")
    for k, v in {"__V1__": V1, "__SIM__": SIM, "__VOID__": VOID, "__V5__": V5, "__FAC__": FAC, "__REP__": REP}.items():
        page = page.replace(k, v)
    return page


def e2e_server(data: Path):
    H = sb.make_handler(data, PinGuard(None), data / "bench.json")
    page = harness().encode("utf-8")
    cloud_cfg = b"window.CLOUD_CONFIG={url:'http://127.0.0.1:9',anon:'test',bench:'test'};"   # 닿지 않는 주소 — 클라우드 모드만 켠다

    class E2E(H):
        def do_GET(self):
            if self.path == "/__e2e.html":
                return self._send(page, "text/html; charset=utf-8")
            if self.path == "/cloud/":
                return self._send(sb.BOARD.read_bytes(), "text/html; charset=utf-8")
            if self.path == "/cloud/cloud-config.js":
                return self._send(cloud_cfg, "text/javascript; charset=utf-8")
            return super().do_GET()

    srv = ThreadingHTTPServer(("127.0.0.1", 0), E2E)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture(scope="module")
def seen():
    if CHROME is None:
        pytest.skip("Chrome/Edge 없음")
    with tempfile.TemporaryDirectory() as d:
        data = Path(d) / "data"
        data.mkdir()
        for p in FIX.glob("protocol_*.json"):
            shutil.copy(p, data / p.name)
        srv = e2e_server(data)
        try:
            url = f"http://127.0.0.1:{srv.server_address[1]}/__e2e.html"
            res = subprocess.run([CHROME, "--headless", "--disable-gpu", "--no-sandbox", f"--user-data-dir={d}/p",
                                  "--virtual-time-budget=120000", "--dump-dom", url], capture_output=True, timeout=120)
        finally:
            srv.shutdown()
    m = re.search(r"@@(.*?)@@", res.stdout.decode("utf-8", "replace"), re.S)
    assert m, "시험 페이지가 결과를 남기지 않았다"
    out = json.loads(m.group(1))
    assert "error" not in out, out.get("error")
    return out


# ---- 진입점: 첫 화면 → 내비 '레일 시험' → 목록 → 줄 클릭 → 결과 (communication 7-2 의 배선 확인) ----------------------------
def test_entry_from_first_screen_to_run_by_clicks(seen):
    assert seen["first"]["h1"] == "관제" and seen["first"]["liveHidden"] is True     # 첫 화면은 관제, '실시간 화면' 링크는 레일 화면에서만
    lst = seen["list"]
    assert lst["hash"] == "#rail" and lst["navCurrent"] == "rail"
    assert lst["rows"] == sorted(lst["rows"], reverse=True) and len(lst["rows"]) == 7 and lst["rows"][-1] == V1   # 최신이 앞
    assert "이 판은 아직 지원 안 함" in lst["unsup"]
    assert lst["live"] == {"hidden": False, "href": "/rail-live", "target": "_blank"}    # 클릭 한 번으로 실시간 화면을 새 창으로
    run = seen["run"]
    assert run["hash"] == f"#rail/run/{SIM}" and run["h1"] == "2026-10-29 17:20" and "레일 시험" in run["crumb"]
    assert run["tiles"] == 24 and run["rows"] == 24 and run["f3"] > 0 and run["f4"] > 0  # 그림 1 · 2 · 3 · 4 가 그려졌다
    assert "합격 20" in run["legend"] and "불합격 2" in run["legend"] and "보류 2" in run["legend"]
    assert run["ship"] is True and run["critNull"] is False and run["liveHidden"] is False
    assert seen["back"] == "#rail"
    assert seen["livePage"] == {"status": 200, "title": True}                          # 링크가 가리키는 실시간 화면이 실제로 나온다


def test_cloud_board_has_no_rail_menu(seen):
    c = seen["cloud"]
    assert c["railNav"] is False and c["perfNav"] is False and c["navN"] == 4             # 관제 · 추이 · 절차 · 점검만
    assert c["h1"] == "관제" and c["hash"] == "#ops" and c["liveHidden"] is True         # 주소로 #rail 을 열어도 관제로


# ---- 어댑터 규칙 (v4 7-2 · 7-6) — 판정은 다시 하지 않고 파일의 글자를 옮긴다 ----------------------------------------------
def test_adapter_reads_v1_and_v4_shapes(seen):
    a = seen["adapt"]
    assert a["ver"] == ["v1", "v4", "unsupported", "?"]
    v1 = a["v1"]
    assert v1["counts"] == {"합격": 23, "불합격": 1, "보류": 0, "무효": 0, "기타": 0} and v1["c11620"] == "불합격"
    assert v1["kinds"]["rx"] == "pf" and v1["kinds"]["gps"] == "rv" and v1["kinds"]["cn0"] == "val"
    assert v1["speed"] == "speeds" and v1["accel"] is None and v1["has"] is False      # 그림 4 는 '이 시험에 없음'
    assert v1["review"] == 5                                                           # verdict.GPS_z 의 칸 수 그대로
    s = a["sim"]
    assert s["counts"] == {"합격": 20, "불합격": 2, "보류": 2, "무효": 0, "기타": 0} and s["sample"] is True
    assert s["kinds"]["clock"] == "pf" and s["kinds"]["gate_on"] == "rv" and s["kinds"]["sog_band"] == "info"   # criteria.kind 그대로
    assert s["speed"] == "band" and s["edges"] == [150, 300, 450, 600] and s["edgeStatus"] == "제안값"
    assert s["accel"] == ["imu", "gate_on", "gate_off", "gate_share"]
    assert s["flag"] == "벗어남" and s["normal"] == "정상" and s["hold"] == "보류" and s["holdItem"] == "보류" and s["info"] == "정보용"
    assert a["void"] == {"counts": {"합격": 0, "불합격": 0, "보류": 0, "무효": 24, "기타": 0}, "rig": False, "reasons": 1}
    assert a["v5"] == {"unsupported": True, "protocol": "v5"}
    assert a["fac"]["kinds"]["gate_on"] == "hold" and a["fac"]["edges"] is None        # 갈래 보류 · 등급 경계 없음
    assert a["rep"]["sky"] == "repeater" and a["rep"]["kinds"]["gps_rx"] == "info"


def test_adapter_repeater_gps_items_are_info_only(seen):
    f = seen["forced"]
    assert f["kinds"] == {"clock": "pf", "sog_band": "info"} and f["forced"] == ["sog_band"]
    assert f["j"] == "정보용" and f["clock"] == "불합격"                                # GPS 항목만 정보용, 나머지 판정은 그대로


def test_list_rows_come_from_the_server_summary(seen):
    rows = {r[0]: r[1:] for r in seen["list2"]}
    assert rows[V1] == ["v1", False, 23, True] and rows[V5][:2] == ["unsupported", True]
    assert rows[VOID] == ["v4", False, 0, False] and rows[SIM] == ["v4", False, 20, True]


# ---- 소스 검사 — 브라우저 없이도 돈다 -------------------------------------------------------------------------------------
def test_board_opens_rail_only_on_the_control_pc():
    assert "...(CLOUD?[]:[['rail','레일 시험','rail']])" in SRC                    # 메뉴: 클라우드에는 없다
    assert "function railRoute(h){if(CLOUD)return null;" in SRC                     # 주소 #rail… 도 클라우드에서는 열지 않는다
    assert "(CLOUD&&/^rail/.test(U.view))" in SRC                                   # 저장된 화면이 레일이어도 클라우드는 관제로
    assert "getJ('/api/rail/runs')" in SRC and "'/api/rail/run?name='" in SRC
    assert 'id="railLive" href="/rail-live" target="_blank"' in SRC and "$('railLive').hidden=cur.nav!=='rail';" in SRC
    assert "V.rail={nav:'rail'" in SRC and "V.railRun={nav:'rail'" in SRC and "V.railCmp={nav:'rail'" in SRC


def test_rail_styles_stay_under_the_rail_root():
    css = SRC[SRC.index("/* ── 레일 시험 (목록 · 결과 · 비교)"):SRC.index("</style>")]
    rules = re.findall(r"([^{}]+)\{[^{}]*\}", re.sub(r"/\*.*?\*/", "", css, flags=re.S))
    for sel in (s.strip() for r in rules for s in r.split(",")):
        if sel.startswith("@media") or not sel:
            continue
        assert sel.startswith(".rail") or sel.startswith("#railLive") or sel.startswith(":root"), sel   # 기존 화면에 새지 않게
    assert ":root{--g1:#C9EBD6; --g2:#CDEEF1; --g3:#F6E8B4; --g4:#F5D3BB; --g5:#EEC2BF}" in SRC       # 더한 토큰은 --g1~--g5 뿐


def test_live_page_reads_the_server_and_nothing_outside():
    assert "fetch('/api/qc_now',{cache:'no-store'})" in LIVE and "setInterval(poll,1000)" in LIVE
    assert "mock(" not in LIVE and "Q.get('part')" not in LIVE and "Q.get('state')" not in LIVE   # 시안의 모의 생성기 · 주소 인자를 뺐다
    assert 'href="/#rail"' in LIVE                                                      # 결과판으로 돌아가는 링크
    assert "_file_age_s" in LIVE                                                        # 갱신 멈춤은 서버가 잰 파일 나이로
    for page in (LIVE, SRC):
        assert not re.search(r"""(src|href)=["']https?://""", page)                    # 외부 리소스 없음(시험망에 인터넷이 없다)
        assert "fonts.googleapis" not in page


def test_rail_screens_do_not_say_hz():
    rail_js = SRC[SRC.index("// ───── 5-2. 레일 시험"):SRC.index("// ───── 6. 세트 등록")]
    for text in (LIVE, rail_js):
        assert not re.search(r"(?<!dB)Hz", text)                                        # 'Hz' 대신 '1초마다' (dBHz 는 C/N0 단위)
