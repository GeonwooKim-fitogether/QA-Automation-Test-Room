"""레일 시험 화면 — 결과판 첫 화면에서 클릭만으로 레일 시험 목록 · 결과에 닿는지(진입점), 클라우드판이 표 rail_run 에서 읽는지,
그리고 화면 어댑터가 v1 · v4 판정 파일을 qc-protocol-v4.md 7-2 · 7-6 대로 옮기는지 본다.

브라우저 검사는 헤드리스 Chrome 을 한 번만 띄운다(test_board_trend_set 과 같은 --dump-dom 방식). 클릭은 시험 서버가 내보내는
시험 페이지(/__e2e.html)가 같은 출처의 iframe 안 결과판을 눌러 가며 하고, 본 것을 JSON 으로 남긴다 — 새 의존성 없이 진입점에서
출발하는 흐름을 그대로 밟는다. 실시간 화면은 시험 서버가 /__mode 로 qc_now 응답을 바꿔 가며(정상 · 갱신 멈춤 · NaN · 끊김)
상태 표시와 기록 유지를 본다. Chrome/Edge 가 없으면 그 검사만 건너뛰고, 소스 검사(아래쪽)는 늘 돈다.
"""
import importlib.util
import json
import re
import shutil
import subprocess
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

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
BROKEN = "protocol_20261028-000000.json"     # 이 검사가 만드는 깨진 파일 — 서버가 읽지 못함(목록에 '읽지 못함')
NAN = "protocol_20261028-120000.json"        # 이 검사가 만드는 NaN 이 든 파일 — 서버는 넘기지만 브라우저가 JSON 으로 읽지 못함
FMT_ERR = "파일 형식 오류 — 쓰는 프로그램 확인"


def _load(path):
    spec = importlib.util.spec_from_file_location(Path(path).stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


demo = _load(ROOT / "board" / "demo_qc_now.py")


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
  // ① 클라우드판 — 제어 PC2 가 올린 표 rail_run(시험 서버의 가짜 /rest/v1)에서 목록을 읽고, 줄을 누르면 결과를 그린다. 실시간 화면 링크는 없다
  const cf=await frame('/cloud/#rail');const cd=()=>cf.contentDocument;
  await until(()=>cd().querySelectorAll('#lst tr[data-run]').length>=7);
  out.cloud={railNav:!!cd().querySelector('#nav button[data-v="rail"]'),perfNav:!!cd().querySelector('#nav button[data-v="perf"]'),
    navN:cd().querySelectorAll('#nav button').length,h1:cd().querySelector('#view h1').textContent,hash:cf.contentWindow.location.hash,liveHidden:cd().getElementById('railLive').hidden,
    rows:[...cd().querySelectorAll('#lst tr[data-run]')].map(r=>r.dataset.run),sub:cd().querySelector('#view .ph .sub').textContent};
  cd().querySelector('#lst tr[data-run="__SIM__"]').click();
  await until(()=>cd().querySelectorAll('#f1 .dt[data-s]').length===24&&cd().querySelector('#f2 table.mx'));
  out.cloudRun={hash:cf.contentWindow.location.hash,tiles:cd().querySelectorAll('#f1 .dt[data-s]').length,rows:cd().querySelectorAll('#f2 tbody tr').length,
    legend:cd().getElementById('f1l').textContent,liveHidden:cd().getElementById('railLive').hidden};
  cd().getElementById('railLive').click();
  const cfl=()=>{const fr=cd().getElementById('railLiveF');return fr&&fr.contentDocument;};
  await until(()=>{const x=cfl();const t=x&&x.getElementById('topLz');return t&&t.textContent.includes('시험 중');});
  out.cloudLive={hash:cf.contentWindow.location.hash,crumb:cd().getElementById('crumb').textContent,src:cd().getElementById('railLiveF').getAttribute('src'),
    top:cfl().getElementById('topLz').textContent,embed:cfl().documentElement.classList.contains('embed'),
    toBoard:getComputedStyle(cfl().getElementById('toBoard')).display,btnHidden:cd().getElementById('railLive').hidden};
  cf.remove();
  localStorage.clear();   // 두 결과판이 이 시험 서버에서는 같은 출처라 '마지막 화면' 기억을 나눠 쓴다 — 실제로는 출처가 달라 나누지 않는다
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
    broken:{ver:d().querySelector('#lst tr[data-run="__BROKEN__"] td:nth-child(3)').textContent,text:d().querySelector('#lst tr[data-run="__BROKEN__"]').textContent},
    live:{hidden:a.hidden,href:a.getAttribute('href'),target:a.target,rel:a.getAttribute('rel')},sub:d().querySelector('#view .ph .sub').textContent};
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
  d().getElementById('railLive').click();
  await until(()=>{const fr=d().getElementById('railLiveF');return fr&&fr.contentDocument&&fr.contentDocument.getElementById('topLz');});
  out.localLive={hash:f.contentWindow.location.hash,src:d().getElementById('railLiveF').getAttribute('src'),
    embed:d().getElementById('railLiveF').contentDocument.documentElement.classList.contains('embed')};
  d().querySelector('#crumb button[data-v="rail"]').click();
  await until(()=>d().querySelectorAll('#lst tr[data-run]').length>=7);
  // NaN 이 든 판정 파일 — 서버 응답 없음이 아니라 파일 형식 오류로 짚는다
  d().querySelector('#lst tr[data-run="__NAN__"]').click();
  await until(()=>(d().querySelector('#railV .smsg')||{}).textContent);
  out.nanRun=d().querySelector('#railV .smsg').textContent;
  f.remove();
  const lv=await fetch('/rail-live');out.livePage={status:lv.status,title:/레일 시험 · 실시간/.test(await lv.text())};
  // ④ 실시간 화면 — 시험 서버가 qc_now 응답을 바꾼다: 정상 → 갱신 멈춤 → NaN → 끊김 → 정상
  const top=()=>ld().getElementById('topLz').textContent, lvEl=()=>ld().getElementById('lv');
  const snap=()=>({top:top(),frozen:lvEl().classList.contains('frozen'),svg:!!ld().querySelector('#recWrap svg'),paths:ld().querySelectorAll('#recWrap svg path').length,
    empty:!!ld().querySelector('#recWrap .empty'),opacity:ld().querySelector('#recWrap svg')?getComputedStyle(ld().querySelector('#recWrap svg')).opacity:null,
    rem:(ld().querySelector('#now .rem')||{}).textContent||'',upd:ld().getElementById('upd').textContent,toBoard:{target:ld().getElementById('toBoard').target,rel:ld().getElementById('toBoard').getAttribute('rel')}});
  await fetch('/__mode?m=live');
  const lf=await frame('/rail-live');const ld=()=>lf.contentDocument;
  await until(()=>top().includes('시험 중')&&ld().querySelectorAll('#recWrap svg path').length>0);
  await sleep(3000);
  out.liveOk=snap();
  await fetch('/__mode?m=stale');
  await until(()=>top().includes('갱신 멈춤'));
  out.liveStale=snap();
  await fetch('/__mode?m=nan');
  await until(()=>top().includes('파일 형식 오류'));
  out.liveNan=snap();
  await fetch('/__mode?m=down');
  await until(()=>top().includes('결과판 서버 응답 없음'));
  out.liveDown=snap();
  await fetch('/__mode?m=live');
  await until(()=>top().includes('시험 중'));
  out.liveBack=snap();
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
    for k, v in {"__V1__": V1, "__SIM__": SIM, "__VOID__": VOID, "__V5__": V5, "__FAC__": FAC, "__REP__": REP,
                 "__BROKEN__": BROKEN, "__NAN__": NAN}.items():
        page = page.replace(k, v)
    return page


def e2e_server(data: Path):
    H = sb.make_handler(data, PinGuard(None), data / "bench.json")
    page = harness().encode("utf-8")
    # 클라우드 주소를 비워 같은 시험 서버의 /rest/v1 로 묻게 한다 — 가짜 클라우드는 표 rail_run 만 답한다(표본 7개)
    cloud_cfg = b"window.CLOUD_CONFIG={url:'',anon:'test',bench:'test'};"
    gen = demo.Demo("C", w0=time.time() - 5)            # 파트 C 를 막 시작한 데모 — 실시간 화면이 '시험 중'을 그린다
    mode = {"m": None, "last": None}                    # /__mode?m=live|stale|nan|down — 실시간 화면이 받는 qc_now 를 바꾼다

    class E2E(H):
        def do_GET(self):
            if self.path == "/__e2e.html":
                return self._send(page, "text/html; charset=utf-8")
            if self.path == "/cloud/":
                return self._send(sb.BOARD.read_bytes(), "text/html; charset=utf-8")
            if self.path.startswith("/cloud/rail-live.html"):
                return self._send(sb.RAIL_LIVE.read_bytes(), "text/html; charset=utf-8")
            if self.path.startswith("/rest/v1/rail_now?"):          # 가짜 클라우드 — 제어 PC2 가 방금 올린 실시간 한 줄(데모 파트 C)
                return self._json([{"payload": gen.frame(time.time()), "file_mtime": datetime.now(timezone.utc).isoformat()}])
            if self.path == "/cloud/cloud-config.js":
                return self._send(cloud_cfg, "text/javascript; charset=utf-8")
            if self.path.startswith("/rest/v1/rail_run?"):
                q = parse_qs(urlparse(self.path).query)
                if q.get("select") == ["summary"]:
                    rows = [] if int(q.get("offset", ["0"])[0]) else [
                        {"summary": sb.rail_summary(p.name, json.loads(p.read_text(encoding="utf-8")))}
                        for p in sorted(FIX.glob("protocol_*.json"), reverse=True)]
                    return self._json(rows)
                name = q.get("name", [""])[0].removeprefix("eq.")
                found = sb.rail_name_ok(name) and (FIX / name).exists()
                return self._send(b'[{"doc":' + (FIX / name).read_bytes() + b"}]" if found else b"[]", "application/json; charset=utf-8")
            if self.path == "/api/rail/cloud":                   # 이 PC 가 9개 중 7개를 올렸고, 깨진 파일 · NaN 파일은 못 올렸다
                return self._json({"state": "ok", "local": 9, "uploaded": 7,
                                   "bad": {BROKEN: "읽지 못함", NAN: "파일 형식 오류 — NaN"}})
            if self.path.startswith("/__mode?m="):
                mode["m"] = self.path.split("=", 1)[1]
                return self._send(b"ok", "text/plain; charset=utf-8")
            if self.path == "/api/qc_now" and mode["m"]:
                m = mode["m"]
                if m == "down":                          # 응답 없이 끊는다 — 브라우저 fetch 가 실패한다
                    self.close_connection = True
                    return None
                if m == "nan":                           # 약속 밖의 NaN — 서버는 그대로 넘기고 화면이 형식 오류로 드러낸다
                    return self._send(b'{"t": NaN, "step": {"key": "C1", "index": 1}}', "application/json; charset=utf-8")
                if m == "live" or mode["last"] is None:
                    mode["last"] = {**gen.frame(time.time()), "_file_age_s": 0.4}
                    return self._json(mode["last"])
                return self._json({**mode["last"], "_file_age_s": 42.0})   # stale — 마지막 파일이 42초째 그대로
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
        (data / BROKEN).write_text("{깨진 파일", encoding="utf-8")
        (data / NAN).write_text('{"stamp": "20261028-120000", "conditions": {"protocol": "v4", "sky": "lab"}, "cells": {}, '
                                '"rig": {"유효": true}, "group": {"x": NaN}}', encoding="utf-8")
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
    assert lst["rows"] == sorted(lst["rows"], reverse=True) and len(lst["rows"]) == 9 and lst["rows"][-1] == BROKEN   # 최신이 앞
    assert "이 판은 아직 지원 안 함" in lst["unsup"]
    assert lst["broken"]["ver"] == "—" and "읽지 못함" in lst["broken"]["text"]          # 읽지 못한 파일의 판은 모른다(v1 아님)
    # 클릭 한 번으로 실시간 화면을 이름 있는 창으로 — 다시 누르면 같은 창(noopener 를 두면 이름 있는 창을 다시 쓰지 않는다)
    assert lst["live"] == {"hidden": False, "href": "#rail/live", "target": "", "rel": None}   # 새 창이 아니라 결과판 안의 '실시간'
    ll = seen["localLive"]
    assert ll == {"hash": "#rail/live", "src": "/rail-live?embed=1", "embed": True}
    run = seen["run"]
    assert run["hash"] == f"#rail/run/{SIM}" and run["h1"] == "2026-10-29 17:20" and "레일 시험" in run["crumb"]
    assert run["tiles"] == 24 and run["rows"] == 24 and run["f3"] > 0 and run["f4"] > 0  # 그림 1 · 2 · 3 · 4 가 그려졌다
    assert "합격 20" in run["legend"] and "불합격 2" in run["legend"] and "보류 2" in run["legend"]
    assert run["ship"] is True and run["critNull"] is False and run["liveHidden"] is False
    assert seen["back"] == "#rail"
    assert seen["livePage"] == {"status": 200, "title": True}                          # 링크가 가리키는 실시간 화면이 실제로 나온다


def test_run_with_nan_is_a_file_format_error_not_a_dead_server(seen):
    assert FMT_ERR in seen["nanRun"] and "NaN" in seen["nanRun"] and "서버 응답 없음" not in seen["nanRun"]


# ---- 실시간 화면: 갱신 멈춤 · 형식 오류 · 서버 끊김 — 상태 표시만 바꾸고 기록은 지우지 않는다 -----------------------------------
def test_live_stale_replaces_running_and_dims_frozen_values(seen):
    ok, st = seen["liveOk"], seen["liveStale"]
    assert "시험 중" in ok["top"] and ok["frozen"] is False and ok["opacity"] == "1" and ok["paths"] > 0
    assert "갱신 멈춤 · 42초 전" in st["top"] and "시험 중" not in st["top"]               # 상태 표지 자체가 바뀐다
    assert st["frozen"] is True and st["opacity"] == "0.45" and st["svg"] is True       # 굳은 값은 흐리게, 기록은 그대로
    assert "마지막 소식" in st["rem"] and "마지막 소식" in st["upd"]
    assert ok["toBoard"] == {"target": "cb-board", "rel": None}                         # 결과판도 이름 있는 창 하나를 다시 쓴다


def test_live_keeps_the_record_when_the_file_breaks_or_the_server_drops(seen):
    nan, down, back = seen["liveNan"], seen["liveDown"], seen["liveBack"]
    assert FMT_ERR in nan["top"] and "NaN" in nan["top"] and "서버 응답 없음" not in nan["top"]
    assert "결과판 서버 응답 없음" in down["top"]
    for s in (nan, down):
        assert s["svg"] is True and s["empty"] is False and s["paths"] > 0 and s["frozen"] is True   # 직전 기록을 지우지 않는다
    assert "시험 중" in back["top"] and back["frozen"] is False


def test_cloud_board_reads_rail_from_the_cloud_table(seen):
    c, r = seen["cloud"], seen["cloudRun"]
    assert c["railNav"] is True and c["perfNav"] is False and c["navN"] == 5             # 관제 · 추이 · 절차 · 점검 · 레일 시험
    assert c["h1"] == "레일 시험" and c["hash"] == "#rail" and c["liveHidden"] is False  # 클라우드에도 '실시간'이 있다
    assert c["rows"] == sorted((p.name for p in FIX.glob("protocol_*.json")), reverse=True) and len(c["rows"]) == 7
    assert "제어 PC2 가 올린 판정 7개" in c["sub"] and "data/protocol" not in c["sub"]
    assert r["hash"] == f"#rail/run/{SIM}" and r["tiles"] == 24 and r["rows"] == 24       # 클라우드의 파일로 그림 1 · 2 를 그렸다
    assert "합격 20" in r["legend"] and r["liveHidden"] is False
    lv = seen["cloudLive"]
    assert lv["hash"] == "#rail/live" and "실시간" in lv["crumb"] and lv["src"] == "rail-live.html?embed=1"
    assert "시험 중" in lv["top"] and lv["embed"] is True and lv["toBoard"] == "none" and lv["btnHidden"] is True   # 표 rail_now 를 읽었다


def test_control_pc_list_says_what_reached_the_cloud(seen):
    sub = seen["list"]["sub"]
    assert "클라우드에 올림 7/9" in sub and "2개 못 올림 — 읽지 못함" in sub


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
    assert s["flag"] == "벗어남" and s["normal"] == "벗어나지 않음" and s["hold"] == "보류" and s["holdItem"] == "보류" and s["info"] == "정보용"
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
    assert rows[BROKEN] == ["?", True, 0, None] and rows[NAN] == ["v4", False, 0, True]   # 깨진 파일은 판을 모른다 · NaN 요약은 서버가 넘긴다


# ---- 소스 검사 — 브라우저 없이도 돈다 -------------------------------------------------------------------------------------
def test_board_opens_rail_and_live_on_both():
    assert "['check','점검','shield'],...(CLOUD?[]:[['perf','성능 비교','monitor']]),['rail','레일 시험','rail']];" in SRC   # 메뉴: 둘 다
    assert "function railRoute(h){let s,m;" in SRC and "(CLOUD&&/^rail/.test(U.view))" not in SRC
    assert "CLOUD?await API.railRuns():(await railGet('/api/rail/runs')).j" in SRC                    # 클라우드는 표 rail_run
    assert "CLOUD?await API.railRun(name):await railGet('/api/rail/run?name='" in SRC
    assert "rail_run?select=summary&order=name.desc" in SRC and "rail_run?select=doc&name=eq." in SRC
    assert 'id="railLive" href="#rail/live"' in SRC and "$('railLive').hidden=cur.nav!=='rail'||U.view==='railLive';" in SRC
    assert "if(s==='rail/live')return{view:'railLive'};" in SRC and "V.railLive={nav:'rail'" in SRC
    assert "${CLOUD?'rail-live.html':'/rail-live'}?embed=1" in SRC
    assert "V.rail={nav:'rail'" in SRC and "V.railRun={nav:'rail'" in SRC and "V.railCmp={nav:'rail'" in SRC


def test_board_names_the_failure_and_the_review_mark():
    rail_js = SRC[SRC.index("// ───── 5-2. 레일 시험"):SRC.index("// ───── 6. 세트 등록")]
    for page in (rail_js, LIVE):
        assert f"FMT_ERR='{FMT_ERR}'" in page                                           # 해석 실패는 서버 끊김과 다른 말로
    assert '<i class="ho"></i>벗어나지 않음</span>' in rail_js and "'정상'?'벗어나지 않음'" in rail_js   # 7-2 의 '벗어남' 과 짝
    assert "j='정상'" not in rail_js and "<i class=\"ho\"></i>정상<" not in rail_js
    assert "protocol:s.error?'—'" in rail_js                                            # 읽지 못한 파일의 판은 '—'


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
    assert 'href="/#rail" target="cb-board"' in LIVE                                    # 결과판으로 — 이름 있는 창 하나를 다시 쓴다
    assert "_file_age_s" in LIVE                                                        # 갱신 멈춤은 서버가 잰 파일 나이로
    assert ".lv.frozen #diaWrap svg,.lv.frozen #lad,.lv.frozen #now,.lv.frozen #recWrap svg{opacity:.45}" in LIVE   # 굳은 값 흐리게
    for page in (LIVE, SRC):
        assert not re.search(r"""(src|href)=["']https?://""", page)                    # 외부 리소스 없음(시험망에 인터넷이 없다)
        assert "fonts.googleapis" not in page


def test_rail_screens_do_not_say_hz():
    rail_js = SRC[SRC.index("// ───── 5-2. 레일 시험"):SRC.index("// ───── 6. 세트 등록")]
    for text in (LIVE, rail_js):
        assert not re.search(r"(?<!dB)Hz", text)                                        # 'Hz' 대신 '1초마다' (dBHz 는 C/N0 단위)


def test_cloud_deploy_carries_the_same_live_page():
    """클라우드판의 '실시간'은 deploy/board/rail-live.html 을 연다 — tools/build_cloud_board.py 가 원본을 그대로 복사한다."""
    assert (ROOT / "deploy" / "board" / "rail-live.html").read_bytes() == (ROOT / "board" / "rail-live.html").read_bytes()
    assert "const CL=(()=>{try{return window.parent!==window&&window.parent.CLOUD_CONFIG||null;}" in LIVE
    assert "rest/v1/rail_now?select=payload,file_mtime&id=eq.rail" in LIVE and "setInterval(pollCloud,2000)" in LIVE


def test_live_diff_card_uses_the_protocol_group_rule():
    """'차이' 카드는 새 기준을 만들지 않는다 — 무리에서 벗어난 셀은 qc_protocol.robust_z 와 같은 식(중앙값 · MAD × 1.4826, |z| ≥ 3)."""
    assert 'id="diffWrap"' in LIVE and "drawDiff();" in LIVE
    assert "const Z_LOOK=3" in LIVE and "*1.4826" in LIVE
    assert "ZF={sog:5," in LIVE                                                          # 속도 바닥값 = qc_protocol Z_FLOOR.still_speed
    from motionbench import qc_protocol
    assert qc_protocol.Z_FLOOR["still_speed"] == 5.0 and qc_protocol.CRITERIA["gps_z"] == 3


def test_live_scales_ignore_one_gps_spike():
    """GPS 를 다시 잡는 순간 한 셀이 수만 mm/s 로 튀어도 속도 칸 · 왕복 비율의 세로 폭이 그 값에 맞춰지지 않는다 — 넘는 값은 끝에 붙이고 적는다."""
    import re
    lanes = re.findall(r"\{t:'(?:원래 속도 · 장면|속도|속도 · 장면)',[^\n]*", LIVE)
    assert len(lanes) == 3 and all("rob:" in ln for ln in lanes)            # 파트 A · C · B 의 속도 칸
    assert "pctl(cv,0.98)" in LIVE and "넘친 값 최대" in LIVE
    assert "Math.abs(m-1)+6*sd" in LIVE and "끝에 붙인 점" in LIVE           # 왕복 비율: 1.00 ± (|가운데 − 1| + 6σ)


def test_live_sky_tiles_name_the_three_skies():
    """하늘 조건 세 칸 — conditions.sky 의 값 이름(terrace · simulator · repeater)과 맞아야 강조된다."""
    assert "const SKY={terrace:" in LIVE and "simulator:" in LIVE and "repeater:" in LIVE
    assert 'id="skyRow"' in LIVE and "drawSky(v,cor);" in LIVE
