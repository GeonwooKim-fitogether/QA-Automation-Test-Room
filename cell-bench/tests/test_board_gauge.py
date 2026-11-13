"""추이 분석의 '셀 IC 학습 용량'(board/index.html <gauge> 블록) — 안정화 끝(측정) · IC 재학습 의심 · 기준 선택(n₁) 배선.

순수 함수는 index.html 의 <trend-base> 와 <gauge> 블록을 떼어 헤드리스 Chrome(JS 엔진)으로 돌려 본다. Chrome 이 없으면 건너뛴다.
실데이터 검사는 이 시험대의 FTG(data/ftg · data/set2/ftg)가 있을 때만 돈다 — 도구로 세트 1 사이클 1~8 · 25~30, 세트 2 사이클 1~6 을 뽑아(약 40초)
세션이 손으로 잰 값(2026-11-02)과 맞는지 본다. 다른 경로면 CELLBENCH_REAL_DATA 로 알려 준다.
"""
import csv
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import gauge_from_ftg as tool          # noqa: E402
from cellbench import ftglog           # noqa: E402

SRC = (ROOT / "board" / "index.html").read_text(encoding="utf-8")
CHROMES = [shutil.which("chrome"), shutil.which("msedge"),
           r"C:\Program Files\Google\Chrome\Application\chrome.exe",
           r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"]
CHROME = next((c for c in CHROMES if c and Path(c).exists()), None)
need_chrome = pytest.mark.skipif(CHROME is None, reason="Chrome/Edge 없음")


def block(tag: str) -> str:
    m = re.search(rf"// <{tag}>.*?// </{tag}>", SRC, re.S)
    assert m, f"<{tag}> 블록이 없다"
    return m.group(0)


PRE = """const esc=s=>String(s??'');const $=()=>null;const wOf=()=>300;
const row=(c,s,med,o={})=>({cycle:String(c),serial:String(s),n:String(o.n??2000),fullcap_med:med==null?'':String(med),boots:String(o.boots??1),por:String(o.por??o.boots??1),design_mAh:String(o.design??750),design_fixed:o.fixed==null?(med!=null&&Math.abs(med-(o.design??750))<=1?'1':'0'):String(o.fixed)});
const meds=M=>Object.fromEntries([...M].map(([c,v])=>[c,v]));
const brief=G=>({avail:G.avail,why:G.why,stable:G.stable,hole:G.hole,last:G.last,cyc:G.cyc,meds:meds(G.meds),suspect:Object.fromEntries([...G.suspect]),kind:Object.fromEntries([...G.kind]),skip:ggSkip(G),row:ggExplainRow(G)});
"""


def run_js(cases: dict, prelude: str = "") -> dict:
    page = ("<pre id=o></pre><script>window.onerror=(m,u,l,c)=>{document.getElementById('o').textContent='@@'+JSON.stringify({ERR:m+' @'+l+':'+c})+'@@';};</script><script>" + block("trend-base") + "\n" + block("gauge") + "\n" + PRE + prelude + "\n(async()=>{const r={};" +
            "".join(f"try{{r[{json.dumps(k)}]=await ({v});}}catch(e){{r[{json.dumps(k)}]='ERR '+e.message;}}" for k, v in cases.items()) +
            "document.getElementById('o').textContent='@@'+JSON.stringify(r)+'@@';})();</script>")
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "t.html"
        f.write_text(page, encoding="utf-8")
        out = subprocess.run([CHROME, "--headless", "--disable-gpu", "--no-sandbox", f"--user-data-dir={d}/p",
                              "--dump-dom", f.as_uri()], capture_output=True, timeout=120).stdout.decode("utf-8", "replace")
    r = json.loads(html.unescape(re.search(r"@@(.*?)@@", out, re.S).group(1)))   # --dump-dom 은 <pre> 글자의 < > & 를 엔티티로 낸다
    errs = {k: v for k, v in r.items() if isinstance(v, str) and v.startswith("ERR ")}
    assert not errs, errs
    return r


S1 = [737.2, 733.4, 765.0, 789.7, 802.6, 793.1, 803.2, 802.1, 798.8, 801.8]
S2 = [501.9, 537.9, 556.1, 558.8, 559.3, 564.0, 563.0, 560.2]


@need_chrome
def test_gauge_calc_rules():
    mk = "const mk=(seq,o={})=>seq.flatMap((m,i)=>[row(i+1,11733,m-1,o),row(i+1,11734,m,o),row(i+1,11735,m+3,o)]);"
    r = run_js({
        "s1": f"brief(ggCalc(mk({S1})))",
        "s2": f"brief(ggCalc(mk({S2},{{design:500}})))",
        "short": f"brief(ggCalc(mk({S1[:7]})))",                                       # 5 뒤 세 회가 아직 없다
        "cloud": "brief(ggCalc(null))",
        "nofile": "brief(ggCalc([]))",
        "gap": "brief(ggCalc([row(1,1,700),row(3,1,701),row(5,1,702),row(9,1,700)]))",   # 사이클 2 가 비었다 — 재지 않고 옛 규칙
        "blank": "brief(ggCalc([row(1,1,null),row(1,2,740)]))",                        # 표본 없는 행은 중앙값에서 뺀다
        "sus": "brief(ggCalc([row(25,11741,750,{boots:8}),row(25,11733,794),row(25,11744,750,{boots:4}),row(26,11735,750),row(26,11739,760,{boots:3}),row(26,11740,760,{boots:2})]))",
        "cellSus": "ggCellSuspect(ggCalc([row(25,11741,750,{boots:8}),row(26,11741,751,{boots:7}),row(27,11741,790,{boots:1})]),11741)",
        "cellRows": "ggCellRows(ggCalc([row(26,11741,751),row(25,11741,750)]),11741).map(r=>r.cycle)",
        "head": "ggHeadMark(ggCalc([row(25,11741,750,{boots:8}),row(25,11743,750,{boots:7}),row(25,11744,750,{boots:4}),row(25,11739,750,{boots:3})]),25)",
        "head0": "ggHeadMark(ggCalc([row(25,11741,790)]),25)",
    }, mk)
    assert r["s1"]["stable"] == 5 and r["s1"]["skip"] == 5 and r["s1"]["meds"]["5"] == 802.6 and r["s1"]["cyc"] == list(range(1, 11))
    assert r["s2"]["stable"] == 3 and r["s2"]["skip"] == 3
    assert "안정화 끝(측정) = 사이클 5" in r["s1"]["row"][1] and "사이클 6부터" in r["s1"]["row"][1] and r["s1"]["row"][0] == "IC 학습 용량"
    assert r["short"]["stable"] is None and r["short"]["skip"] is None and "측정 중" in r["short"]["row"][1] and "1~3" in r["short"]["row"][1]
    assert r["cloud"] == {"avail": False, "why": "cloud", "stable": None, "hole": None, "last": None, "cyc": [], "meds": {}, "suspect": {}, "kind": {}, "skip": None, "row": r["cloud"]["row"]}
    assert r["cloud"]["row"][1].startswith("로컬 결과판에서만")
    assert r["nofile"]["avail"] is True and r["nofile"]["skip"] is None and r["nofile"]["row"][1].startswith("기록 없음") and "gauge_from_ftg" in r["nofile"]["row"][1]
    assert r["gap"]["stable"] is None and r["gap"]["skip"] is None and r["gap"]["hole"] == 2 and "사이클 2가 비어 있어" in r["gap"]["row"][1] and "--from 1" in r["gap"]["row"][1]
    assert r["blank"]["meds"] == {"1": 740}
    assert r["sus"]["suspect"] == {"25": [11741, 11744], "26": [11735, 11739]}      # 부팅 ≥3 또는 설계값 고정(11735 는 부팅 1회지만 750 고정)
    assert r["cellSus"] == [25, 26] and r["cellRows"] == ["25", "26"]
    assert r["head"] == " · IC 재학습 의심 4대 (셀 11739, 11741, 11743 외)" and r["head0"] == ""


@need_chrome
def test_trend_base_takes_measured_skip():
    """측정값이 있으면 기준 선택이 사이클 stable+1 부터 — 없으면 기존 1~3 제외 그대로."""
    mk = """const mkD=(n)=>{const D=[],C=[];for(let c=1;c<=n;c++){[1,2].forEach(s=>D.push({cycle:String(c),serial:String(s),start_pct:'100',est_runtime_h:'4'}));C.push({cycle:String(c),note:''});}return {D,C};};
const sk=(n,skip)=>{const {D,C}=mkD(n);const M=skip==null?trendCalc(D,C):trendCalc(D,C,skip);return {base:M.P.base,skip:M.P.skip,why:[1,3,4,5,6].map(c=>tbWhy(M,c)),rule:tbExplain(M.P)[0][1]};};"""
    r = run_js({"d": "sk(16)", "m5": "sk(16,5)", "m3": "sk(16,3)", "wait": "sk(12,5)"}, mk)
    assert r["d"]["base"] == [4, 5, 6, 7, 8, 9, 10, 11, 12, 13] and r["d"]["skip"] == 3 and r["d"]["why"] == ["안정화 구간", "안정화 구간", "", "", ""]
    assert r["m5"]["base"] == [6, 7, 8, 9, 10, 11, 12, 13, 14, 15] and r["m5"]["why"] == ["안정화 구간"] * 4 + [""] and "사이클 6부터" in r["m5"]["rule"]
    assert r["m3"] == r["d"]
    assert r["wait"]["base"] == [6, 7, 8, 9, 10, 11, 12]


def test_wiring_in_board():
    # 받는 곳 — 로컬은 /api/gauge(404 = 'noapi', 그 밖의 실패 = 'fail' → 직전 값 유지), 클라우드는 null. 받은 값이 S.gauge 로 들어가 trendModel 이 n₁ 과 표지에 쓴다
    assert "if(r.status===404)return 'noapi';if(!r.ok)throw new Error('HTTP '+r.status);" in SRC and "gauge:async()=>null" in SRC
    assert "lazy?API.gauge(cs).catch(()=>'fail'):null" in SRC and "gauge:ggKeep(S.gauge,gg,lazy,same)}" in SRC
    assert "G=ggCalc(API.cloud?null:S.gauge??'wait')" in SRC and "TM=trendCalc(D,C,ggSkip(G)??TB.skip);TM.G=G;" in SRC
    # 보이는 곳 — 기준 설명 줄('규칙' 바로 아래) · 셀 카드(표지 + 근거 + 추이) · 히트맵 머리(세모) · 범례('IC 기록은 사이클 N까지')
    assert "ggWithRow(tbExplain(P),M.G)" in SRC
    assert "ggInsp($('tGauge'),M.G,c.s,M.cyc.filter(cy=>M.val(c.s,cy)!=null));" in SRC and '<div id="tGauge" hidden></div>' in SRC
    assert "${ggTag(M.G,c.s,M.cyc[M.cyc.length-1])}" in SRC and "${esc(tbBadge(M,c))}</div>${ggTagWhy(M.G,c.s,M.cyc[M.cyc.length-1])}" in SRC
    assert "$('hmGaugeTo').textContent=ggLegend(M.G)" in SRC and '<span id="hmGaugeTo"></span>' in SRC
    assert "border-bottom:8px solid var(--warn)" in SRC and ".ih .tag.bad" not in SRC
    assert "${M.G.suspect.has(r.c)?' g':''}" in SRC and "tbHead(M,r)+ggHeadMark(M.G,r.c)" in SRC and ".hm .vc.g span::after" in SRC
    assert "IC 재학습 의심 (사이클 머리의 세모)" in SRC and "${API.cloud?'':'<span><i class=\"lg-g\">" in SRC
    # 세트를 바꾸면 옛 세트의 학습 용량을 이어 쓰지 않는다
    assert "dis:[],cellsX:[],gauge:'wait'}; LOADED=false;" in SRC
    # 서버 · 도구 · 문서
    srv = (ROOT / "serve_board.py").read_text(encoding="utf-8")
    assert 'if u.path == "/api/gauge":' in srv and 'read_csv(d / "gauge.csv")' in srv
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "## 셀 IC 학습 용량" in readme and "tools/gauge_from_ftg.py" in readme and "안정화 끝(측정)" in readme and "IC 재학습 의심" in readme
    assert "20초" in readme and "로컬 결과판에서만" in readme


def test_deploy_copy_matches_board():
    assert (ROOT / "deploy" / "board" / "index.html").read_bytes() == (ROOT / "board" / "index.html").read_bytes(), \
        "deploy/board/index.html 이 board/index.html 과 다르다 — python tools/build_cloud_board.py"


# ---- 실데이터 — 이 시험대의 FTG 에서 뽑은 값이 손으로 잰 값과 같은가 ----
def _real_dir() -> Path | None:
    d = Path(os.environ.get("CELLBENCH_REAL_DATA") or ROOT / "data")
    try:   # 이 시험대(세트 1 첫 사이클 2026-10-10 10:35:11)의 기록일 때만
        first = next(csv.DictReader((d / "cycles.csv").open(encoding="utf-8-sig")))
    except (OSError, StopIteration):
        return None
    ok = first.get("start") == "2026-10-10 10:35:11" and (d / "ftg" / "0030").is_dir() and (d / "set2" / "ftg" / "0006").is_dir()
    return d if ok else None


REAL = _real_dir()


@pytest.fixture(scope="module")
def real_rows(tmp_path_factory):
    out = tmp_path_factory.mktemp("gauge")
    p1, _ = tool.run(REAL, 1, 1, 8, out / "s1.csv", False, None, log=lambda *_: None)
    tool.run(REAL, 1, 25, 30, out / "s1.csv", False, None, log=lambda *_: None)
    p2, _ = tool.run(REAL, 2, 1, 6, out / "s2.csv", False, None, log=lambda *_: None)
    return (list(csv.DictReader(p1.open(encoding="utf-8-sig"))), list(csv.DictReader(p2.open(encoding="utf-8-sig"))))


@pytest.mark.skipif(REAL is None, reason="이 시험대의 FTG(data/ftg · data/set2/ftg)가 없음 — CELLBENCH_REAL_DATA 로 경로를 줄 수 있다")
def test_real_data_medians_and_relearn(real_rows):
    s1, s2 = real_rows
    m1, m2 = ftglog.set_medians(s1), ftglog.set_medians(s2)
    for c, want in enumerate((737, 733, 765, 790, 803), 1):               # 세션 실측(2026-11-02) ±2 mAh
        assert abs(m1[c] - want) <= 2, (c, m1[c])
    for c, want in enumerate((502, 538, 556, 559), 1):
        assert abs(m2[c] - want) <= 2, (c, m2[c])
    assert ftglog.stable_end(m1) == 5 and ftglog.stable_end(m2) == 3
    assert {r["design_mAh"] for r in s1} == {"750"} and {r["design_mAh"] for r in s2 if r["cycle"] != "1"} == {"500"}
    assert all(r["design_mAh"] == "" and r["design_fixed"] == "" for r in s2 if r["cycle"] == "1")   # 보정 사이클 — 만충(100 %)에서 부팅해 설계값을 읽지 않는다
    assert all(r["fw_version"] == "0.99.66" for r in s1 + s2)
    short = [(r["cycle"], r["serial"], r["n"]) for r in s1 + s2 if int(r["n"]) <= 1000]
    assert short == [("26", "11735", "236")], short                       # 26회의 11735 만 짧게 기록했다(80분) — 그 회는 750 고정으로 의심에 든다
    sus = {c: sorted(int(r["serial"]) for r in s1 if int(r["cycle"]) == c and ftglog.relearn_suspect(r)) for c in range(25, 31)}
    for c in range(25, 31):
        assert {11741, 11743, 11744} <= set(sus[c]), (c, sus[c])          # 사이클당 4~9번 재부팅 · FullCap 750 고정
        assert all(int(r["boots"]) >= 4 for r in s1 if int(r["cycle"]) == c and r["serial"] in ("11741", "11743", "11744"))
    kinds = ftglog.suspect_kinds(s1)
    assert kinds[(26, 11735)] == "short" and all(v == "pre" for (c, _), v in kinds.items() if c == 1)   # [QA 4] 짧은 기록 · 첫 사이클은 따로
    assert ftglog.stable_end(m1) == 5 and ftglog.record_hole(m1) == 9                                  # 1~8 뒤 25~ — 안정화 끝(5)을 잰 뒤라 상관없다
    normal = [r for r in s1 if int(r["cycle"]) in range(3, 9) and ftglog.relearn_suspect(r)]
    assert normal == [], normal                                            # 안정 구간(3~8회)의 정상 셀은 하나도 잡히지 않는다


@need_chrome
@pytest.mark.skipif(REAL is None, reason="이 시험대의 FTG 가 없음")
def test_real_data_board_matches_python(real_rows):
    s1, s2 = real_rows
    pre = f"const R1={json.dumps(s1)};const R2={json.dumps(s2)};"
    r = run_js({"g1": "brief(ggCalc(R1))", "g2": "brief(ggCalc(R2))", "c41": "ggCellSuspect(ggCalc(R1),11741)", "c33": "ggCellSuspect(ggCalc(R1),11733)"}, pre)
    assert r["g1"]["stable"] == 5 and r["g1"]["skip"] == 5 and r["g2"]["stable"] == 3
    assert {int(k): v for k, v in r["g1"]["meds"].items()} == {c: pytest.approx(v) for c, v in ftglog.set_medians(s1).items()}
    assert r["c41"][-6:] == [25, 26, 27, 28, 29, 30] and 25 not in r["c33"]
    assert all(11741 in r["g1"]["suspect"][str(c)] for c in range(25, 31))
    assert r["g1"]["kind"] == {f"{sr}|{c}": k for (c, sr), k in ftglog.suspect_kinds(s1).items()}   # 결과판과 도구가 같은 이름으로 가른다


@need_chrome
def test_half_file_does_not_measure_stable_end():
    """[QA 1] 사이클 1 이 없거나 판정 구간에 빈 사이클이 있는 반쪽 gauge.csv 는 안정화 끝을 재지 않고 옛 규칙(1~3) + '--from 1 로 다시' 안내."""
    mk = "const mk=(cs,seq)=>cs.flatMap((c,i)=>[row(c,11733,seq[i]-1),row(c,11734,seq[i])]);"
    r = run_js({
        "from25": f"brief(ggCalc(mk([25,26,27,28,29,30],{S1[4:10]})))",     # --from 25 로만 뽑은 파일 — 25 가 '안정' 으로 잡히면 안 된다
        "mid": f"brief(ggCalc(mk([1,2,3,5,6,7,8,9],{S1[:8]})))",              # 1~3 · 5~ — 판정 구간(1 뒤 세 회)에 4 가 비었다
        "late": f"brief(ggCalc(mk([1,2,3,4,5,6,7,8,12],{S1[:9]})))",          # 안정화 끝(5) 판정 뒤의 빈 사이클은 상관없다
        "grow": f"brief(ggCalc(mk([1,2,3],{S1[:3]})))",                       # 아직 쌓이는 중 — 빈 것이 아니라 측정 중
    }, mk)
    f = r["from25"]
    assert f["stable"] is None and f["skip"] is None and f["hole"] == 1 and f["last"] == 30
    assert "IC 기록이 사이클 25부터라 안정화 끝을 잴 수 없다" in f["row"][1] and "--from 1" in f["row"][1] and "사이클 1~3을" in f["row"][1] and "사이클 30까지" in f["row"][1]
    assert r["mid"]["stable"] is None and r["mid"]["hole"] == 4 and "사이클 4가 비어 있어" in r["mid"]["row"][1]
    assert r["late"]["stable"] == 5 and r["late"]["hole"] is None
    assert r["grow"]["hole"] is None and "측정 중" in r["grow"]["row"][1]


@need_chrome
def test_gauge_fetch_states_and_keep():
    """[QA 2] /api/gauge 404 = 옛 서버('noapi'), 5xx · 네트워크 = 'fail'(같은 세트의 직전 값 유지), 빈 배열 = 기록 없음."""
    m = re.search(r"\n  (gauge:async\(k\)=>\{.*?\},)", SRC)
    assert m, "localApi.gauge 가 없다"
    pre = ("const qs=k=>k&&k!==1?'?set='+k:'';let ST=200,BODY=[];"
           "const fetch=async(u)=>{if(ST===0)throw new TypeError('Failed to fetch');return {status:ST,ok:ST>=200&&ST<300,json:async()=>BODY};};"
           "const API={" + m.group(1) + "};const get=async st=>{ST=st;try{return await API.gauge(1);}catch(e){return 'fail';}};"
           "const prev=[row(1,1,740)];")
    r = run_js({
        "ok": "get(200)", "nf": "get(404)", "e5": "get(500)", "net": "get(0)",
        "rowNoapi": "ggExplainRow(ggCalc('noapi'))[1]", "rowFail": "ggExplainRow(ggCalc('fail'))[1]",
        "rowWait": "ggExplainRow(ggCalc('wait'))[1]", "rowEmpty": "ggExplainRow(ggCalc([]))[1]",
        "keepFail": "ggKeep(prev,'fail',true,true)", "keepFailOther": "ggKeep(prev,'fail',true,false)", "keepFailWait": "ggKeep('wait','fail',true,true)",
        "keepNew": "ggKeep(prev,[],true,true)", "keepNoapi": "ggKeep(prev,'noapi',true,true)", "keepLazy": "ggKeep(prev,null,false,true)", "keepLazyOther": "ggKeep(prev,null,false,false)",
    }, pre)
    assert r["ok"] == [] and r["nf"] == "noapi" and r["e5"] == "fail" and r["net"] == "fail"
    assert r["rowNoapi"].startswith("이 결과판 서버는 아직 IC 기능이 없다 — 결과판 서버") and "다시 띄워야 한다" in r["rowNoapi"]
    assert "읽지 못했다" in r["rowFail"] and "불러오는 중" in r["rowWait"]
    assert r["rowEmpty"].startswith("기록 없음 — tools/gauge_from_ftg.py 를 돌리면")
    assert isinstance(r["keepFail"], list) and r["keepFail"][0]["fullcap_med"] == "740"
    assert r["keepFailOther"] == "fail" and r["keepFailWait"] == "fail" and r["keepNew"] == [] and r["keepNoapi"] == "noapi"
    assert r["keepLazy"] == r["keepFail"] and r["keepLazyOther"] == "wait"


@need_chrome
def test_kinds_recent_tag_and_risk():
    """[QA 3 · 4] 첫 사이클 = '학습 전', 표본이 사이클 중앙값의 절반 미만 = '기록 짧음' — 재학습 의심(세모 · 표지)에 세지 않는다.
    카드 머리 표지는 최근 10사이클 안의 의심만, 그 바로 아래에 잔량 % 환산이 틀어진다는 근거 문장."""
    pre = ("const R=[row(1,7,750,{boots:4}),row(1,8,740),"                     # 1회: 부팅 4 — 학습 전
           "row(5,7,750,{boots:5}),row(5,8,790),row(5,9,792),"                 # 5회: 7 은 재학습 의심(오래 전)
           "row(26,7,792),row(26,8,790),row(26,9,750,{n:236}),"                # 26회: 9 는 기록 짧음(236 < 2000/2)
           "row(28,7,750,{boots:6}),row(28,8,790),row(28,9,791),row(30,8,790)];const G=ggCalc(R);")
    r = run_js({
        "g": "brief(G)", "k1": "ggCellKind(G,7,'pre')", "s7": "ggCellSuspect(G,7)", "rec7": "ggRecentSuspect(G,7,30)", "rec8": "ggRecentSuspect(G,8,30)",
        "rec7late": "ggRecentSuspect(G,7,40)", "tag7": "ggTag(G,7,30)", "why7": "ggTagWhy(G,7,30)", "tag9": "ggTag(G,9,30)", "why9": "ggTagWhy(G,9,30)",
        "head26": "ggHeadMark(G,26)", "head28": "ggHeadMark(G,28)", "head31": "ggHeadMark(G,31)", "legend": "ggLegend(G)", "legendCloud": "ggLegend(ggCalc(null))",
        "risk": "GG_RISK",
    }, pre)
    assert r["g"]["kind"] == {"7|1": "pre", "7|5": "relearn", "9|26": "short", "7|28": "relearn"}
    assert r["g"]["suspect"] == {"5": [7], "28": [7]}
    assert r["k1"] == [1] and r["s7"] == [5, 28] and r["rec7"] == [28] and r["rec8"] == [] and r["rec7late"] == []
    assert 'class="tag"' in r["tag7"] and "tag bad" not in r["tag7"] and r["risk"] in r["tag7"] and r["tag9"] == "" and r["why9"] == ""
    assert r["why7"].startswith('<div class="lab" id="tGaugeTagWhy"><b>IC 재학습 의심</b> · 최근 10사이클 중 사이클 28 — ') and "추정 작동시간이 실제보다 길게" in r["why7"]
    assert r["head26"] == "" and r["head28"] == " · IC 재학습 의심 1대 (셀 7)" and r["head31"] == " · IC 기록 없음 (사이클 30까지)"
    assert r["legend"] == " · IC 기록은 사이클 30까지" and r["legendCloud"] == ""


@need_chrome
def test_gauge_chart_shares_runtime_axis():
    """[QA 3] FullCap 그래프의 가로축 = 작동시간 그래프의 사이클 칸. 기록 없는 칸은 비우고 선을 끊는다. 재학습 의심은 채운 세모(주의색), 학습 전 · 기록 짧음은 빈 세모."""
    pre = ("const R=[row(1,7,750,{boots:4}),row(2,7,760),row(5,7,790),row(6,7,750,{boots:5}),row(9,7,795)];const G=ggCalc(R);"
           "const draw=cyc=>{const h=document.createElement('div');ggChart(h,ggCellRows(G,7),{design:750,cyc,kind:r=>ggKindOf(G,r)});return h.innerHTML;};")
    r = run_js({"h": "draw([1,2,3,4,5,6])", "none": "draw([3,4])", "all": "draw(null)"}, pre)
    h = r["h"]
    assert h.count('stroke-linejoin="round"') == 2                                   # 1·2 와 5·6 — 3·4 칸에서 끊긴다
    assert h.count("<circle") == 2 and h.count('fill="var(--warn)"') == 1 and h.count('fill="var(--panel)" stroke="var(--ink-3)"') == 1
    assert "var(--bad)" not in h and "사이클 9" not in h                               # 이탈색을 쓰지 않는다 · 작동시간 그래프 밖(9)은 그리지 않는다
    assert ">1</text>" in h and ">6</text>" in h
    assert "이 사이클 범위에 IC 기록 없음" in r["none"]
    assert "사이클 9" in r["all"]                                                       # 가로축을 안 주면 IC 기록의 사이클 전부


@need_chrome
def test_explain_row_order_and_josa():
    """[QA 6] IC 줄은 '규칙' 바로 아래, 측정 n₁ 이 5 면 '1~5는'(3 이면 '1~3은')."""
    mk = """const mkD=(n)=>{const D=[],C=[];for(let c=1;c<=n;c++){[1,2].forEach(s=>D.push({cycle:String(c),serial:String(s),start_pct:'100',est_runtime_h:'4'}));C.push({cycle:String(c),note:''});}return {D,C};};
const ex=(skip)=>{const {D,C}=mkD(16);const M=trendCalc(D,C,skip);return ggWithRow(tbExplain(M.P),ggCalc([]));};"""
    r = run_js({"e5": "ex(5)", "e3": "ex(3)", "j": "[ggJosa(2,'은','는'),ggJosa(10,'은','는'),ggJosa(9,'을','를'),ggJosa(13,'이','가')]"}, mk)
    assert [k for k, _ in r["e5"]][:2] == ["규칙", "IC 학습 용량"] and [k for k, _ in r["e3"]][:2] == ["규칙", "IC 학습 용량"]
    assert "사이클 1~5는 뺀다" in r["e5"][0][1] and "사이클 1~3은 뺀다" in r["e3"][0][1]
    assert r["j"] == ["는", "은", "를", "이"]
