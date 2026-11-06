"""추이 분석의 '셀 IC 학습 용량'(board/index.html <gauge> 블록) — 안정화 끝(측정) · IC 재학습 의심 · 기준 선택(n₁) 배선.

순수 함수는 index.html 의 <trend-base> 와 <gauge> 블록을 떼어 헤드리스 Chrome(JS 엔진)으로 돌려 본다. Chrome 이 없으면 건너뛴다.
실데이터 검사는 이 시험대의 FTG(data/ftg · data/set2/ftg)가 있을 때만 돈다 — 도구로 세트 1 사이클 1~8 · 25~30, 세트 2 사이클 1~6 을 뽑아(약 40초)
세션이 손으로 잰 값(2026-11-02)과 맞는지 본다. 다른 경로면 CELLBENCH_REAL_DATA 로 알려 준다.
"""
import csv
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


PRE = """const esc=s=>String(s??'');const $=()=>null;
const row=(c,s,med,o={})=>({cycle:String(c),serial:String(s),n:'2000',fullcap_med:med==null?'':String(med),boots:String(o.boots??1),por:String(o.por??o.boots??1),design_mAh:String(o.design??750),design_fixed:o.fixed==null?(med!=null&&Math.abs(med-(o.design??750))<=1?'1':'0'):String(o.fixed)});
const meds=M=>Object.fromEntries([...M].map(([c,v])=>[c,v]));
const brief=G=>({avail:G.avail,stable:G.stable,cyc:G.cyc,meds:meds(G.meds),suspect:Object.fromEntries([...G.suspect]),skip:ggSkip(G),row:ggExplainRow(G)});
"""


def run_js(cases: dict, prelude: str = "") -> dict:
    page = ("<pre id=o></pre><script>window.onerror=(m,u,l,c)=>{document.getElementById('o').textContent='@@'+JSON.stringify({ERR:m+' @'+l+':'+c})+'@@';};</script><script>" + block("trend-base") + "\n" + block("gauge") + "\n" + PRE + prelude + "\nconst r={};" +
            "".join(f"try{{r[{json.dumps(k)}]=({v});}}catch(e){{r[{json.dumps(k)}]='ERR '+e.message;}}" for k, v in cases.items()) +
            "document.getElementById('o').textContent='@@'+JSON.stringify(r)+'@@';</script>")
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "t.html"
        f.write_text(page, encoding="utf-8")
        out = subprocess.run([CHROME, "--headless", "--disable-gpu", "--no-sandbox", f"--user-data-dir={d}/p",
                              "--dump-dom", f.as_uri()], capture_output=True, timeout=120).stdout.decode("utf-8", "replace")
    r = json.loads(re.search(r"@@(.*?)@@", out, re.S).group(1))
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
        "gap": "brief(ggCalc([row(1,1,700),row(3,1,701),row(5,1,702),row(9,1,700)]))",   # 빠진 사이클은 건너뛰고 다음 기록으로
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
    assert r["cloud"] == {"avail": False, "stable": None, "cyc": [], "meds": {}, "suspect": {}, "skip": None, "row": r["cloud"]["row"]}
    assert r["cloud"]["row"][1].startswith("로컬 결과판에서만")
    assert r["nofile"]["avail"] is True and r["nofile"]["skip"] is None and r["nofile"]["row"][1].startswith("기록 없음") and "gauge_from_ftg" in r["nofile"]["row"][1]
    assert r["gap"]["stable"] == 1 and r["blank"]["meds"] == {"1": 740}
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
    assert r["m5"]["base"] == [6, 7, 8, 9, 10, 11, 12, 13, 14, 15] and r["m5"]["why"] == ["안정화 구간"] * 4 + [""] and "1~5은 뺀다. 사이클 6부터" in r["m5"]["rule"]
    assert r["m3"] == r["d"]
    assert r["wait"]["base"] == [6, 7, 8, 9, 10, 11, 12]


def test_wiring_in_board():
    # 받는 곳 — 로컬은 /api/gauge(옛 서버면 빈 배열), 클라우드는 null. 받은 값이 S.gauge 로 들어가 trendModel 이 n₁ 과 표지에 쓴다
    assert "gauge:(k)=>getJ('/api/gauge'+qs(k)).catch(()=>[])" in SRC and "gauge:async()=>null" in SRC
    assert "lazy?API.gauge(cs):null" in SRC and "gauge:lazy?(gg||[]):same?S.gauge:[]" in SRC
    assert "G=ggCalc(API.cloud?null:S.gauge||[])" in SRC and "TM=trendCalc(D,C,ggSkip(G)??TB.skip);TM.G=G;" in SRC
    # 보이는 곳 셋 — 기준 설명 줄 · 셀 카드(표지 + 추이) · 히트맵 머리(세모)
    assert "tbExplain(P).concat([ggExplainRow(M.G)])" in SRC
    assert "ggInsp($('tGauge'),M.G,c.s);" in SRC and '<div id="tGauge" hidden></div>' in SRC and 'id="tGaugeTag"' in SRC
    assert "${M.G.suspect.has(r.c)?' g':''}" in SRC and "tbHead(M,r)+ggHeadMark(M.G,r.c)" in SRC and ".hm .vc.g span::after" in SRC
    assert "IC 재학습 의심 (사이클 머리의 세모)" in SRC and "${API.cloud?'':'<span><i class=\"lg-g\">" in SRC
    # 세트를 바꾸면 옛 세트의 학습 용량을 이어 쓰지 않는다
    assert "dis:[],cellsX:[],gauge:[]}; LOADED=false;" in SRC
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
