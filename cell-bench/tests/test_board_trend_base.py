"""추이 분석의 기준값 — 첫 사이클이 아니라 '안정화 뒤 10사이클 평균'을 기준으로, '최근 10사이클 평균'을 비교값으로 쓴다.

왜: 첫 2~3사이클은 안정화 구간이라 첫 사이클 기준이면 결론이 뒤집혔다(세트 2: 첫 사이클 기준 +8.9 % · 4~13회 평균 기준 −2.5 %).
그리고 사이클 간 흔들림(1.8~2.6 %)이 보려는 변화(노화 ~2.5 %)와 같은 크기라 한 사이클끼리 비교하면 안 된다.

규칙(세트 단위로 사이클을 고르고, 셀별 기준은 그 같은 사이클들에서 그 셀의 값 평균)
  1. 사이클 1~3 은 안정화 구간으로 뺀다.
  2. 사이클 4부터 차례로 (a) 방전 시작(그 사이클 discharge 행들의 start_pct 최저) 98 % 이상, (b) 사이클 기록의 note 가 비어 있음 —
     을 모두 만족하는 사이클을 앞에서부터 10개 고른다. 사이클 기록이 아직 없는 사이클은 note 를 확인할 수 없어 뺀다.
  3. 기준값 = 고른 10개 평균. 앞에서부터 고르므로 뒤 기록이 늘어도 같은 10개다(고정).
  4. 10개가 안 모였으면 '기준 산출 중 (n/10)' — 변화율 · 셀 판정을 내지 않는다.
  비교값 = 같은 거르기의 최근 10사이클 평균.

순수 함수는 index.html 의 <trend-base> 블록을 떼어 헤드리스 Chrome(JS 엔진)으로 돌려 본다. Chrome 이 없으면 건너뛴다.
실데이터 검사는 이 시험대의 기록(data/ · data/set2/)이 있을 때만 돈다 — 다른 경로면 CELLBENCH_REAL_DATA 로 알려 준다.
"""
import csv
import json
import os
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
need_chrome = pytest.mark.skipif(CHROME is None, reason="Chrome/Edge 없음")

# 가짜 기록 — n 사이클 × 셀(ser). st = 사이클별 방전 시작 %, note = 사이클별 비고, noRow = 사이클 기록이 없는 사이클, v(s,c) = 셀 값(undefined 면 행 없음)
MK = """const mk=(n,o={})=>{const D=[],C=[],ser=o.ser||[1,2];for(let c=1;c<=n;c++){ser.forEach(s=>{const v=o.v?o.v(s,c):4;if(v===undefined)return;
  D.push({cycle:String(c),serial:String(s),start_pct:String((o.st||{})[c]??100),est_runtime_h:v==null?'':String(v)});});
  if(!(o.noRow||[]).includes(c))C.push({cycle:String(c),note:(o.note||{})[c]||''});}return {D,C};};
const calc=(n,o)=>{const {D,C}=mk(n,o);return trendCalc(D,C);};
const brief=M=>({base:M.P.base,recent:M.P.recent,ready:M.P.ready,ex:M.P.ex,overlap:M.P.overlap,ref:M.ref,refN:M.refN,now:M.now,chg:M.chg,
  vd:M.vd.map(r=>[r.c,r.status]),cells:M.cells.map(c=>({s:c.s,state:c.state,chg:c.chg,baseN:c.baseN,streak:c.streak,caution:c.caution,inspect:c.inspect,why:c.why})),
  text:tbText(M.P,M.ref.avg,M.now.avg),tip:tbTip(M.P),explain:tbExplain(M.P),heads:Object.fromEntries(M.vd.map(r=>[r.c,tbHead(M,r)])),
  badges:Object.fromEntries(M.cells.map(c=>[c.s,tbBadge(M,c)]))});
"""


def block() -> str:
    m = re.search(r"// <trend-base>.*?// </trend-base>", SRC, re.S)
    assert m, "<trend-base> 블록이 없다"
    return m.group(0)


def run_js(cases: dict, prelude: str = "") -> dict:
    page = ("<pre id=o></pre><script>" + block() + "\n" + MK + prelude + "\nconst r={};" +
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


@need_chrome
def test_pick_cycles_rules():
    r = run_js({
        "lt10": "brief(calc(12))",                                                   # 4~12 = 9개
        "eq10": "brief(calc(13))",
        "st97": "brief(calc(14,{st:{5:97}}))",                                       # 시작 97 % 는 뺀다
        "st98": "brief(calc(13,{st:{6:98}}))",                                       # 98 % 는 넣는다(경계)
        "stab": "brief(calc(13,{st:{1:60,2:70,3:80}}))",                             # 1~3 은 무엇이든 보지 않는다
        "note": "brief(calc(15,{note:{8:'restart_in_charge',10:'reconfigure'}}))",   # 비고 있음 → 빼고 뒤로 늘린다
        "norow": "brief(calc(14,{noRow:[7]}))",                                      # 사이클 기록 없음 → note 를 확인할 수 없어 뺀다
        "nocyc": "(()=>{const {D}=mk(20);return brief(trendCalc(D,[]));})()",       # 사이클 기록을 못 받았으면 아무것도 고르지 않는다
        "both": "brief(calc(14,{st:{9:95},note:{9:'reconfigure'}}))",
        "charging": "brief(calc(14,{noRow:[14]}))",
        "codes": "brief(calc(16,{note:{5:'charge_timeout',6:'weird_code'}}))",           # 아는 코드는 사람 말로, 모르는 코드는 원문 그대로                                 # 마지막 사이클은 충전이 끝나야 줄이 생긴다 — 그동안은 빼고 이유를 달리 적는다
    })
    lt = r["lt10"]
    assert lt["ready"] is False and lt["base"] == list(range(4, 13)) and lt["text"] == "기준 산출 중 (9/10)"
    assert lt["chg"] is None and lt["ref"]["avg"] is None and all(c["state"] == "wait" and c["chg"] is None for c in lt["cells"])
    # 10개 전에는 판정하지 않는다 — 1~3 은 '판정 제외', 4~12(기준 후보)는 '판정 대기'. 머리 원 툴팁에 '제외'와 '기준 사이클'이 함께 나오지 않는다(QA #9)
    assert [s for _, s in lt["vd"]] == ["skip"] * 3 + ["wait"] * 9
    assert lt["heads"]["12"] == "사이클 12 · 판정 대기 · 기준 후보 (9/10)" and lt["heads"]["2"] == "사이클 2 · 판정 제외 · 안정화 구간"
    assert "진행: 기준 산출 중 (9/10)" in lt["tip"] and all(c["why"] == "기준 산출 중 (9/10)" for c in lt["cells"])
    assert lt["badges"]["1"] == "기준 산출 중 (9/10)"

    eq = r["eq10"]
    assert eq["ready"] is True and eq["base"] == list(range(4, 14)) and eq["recent"] == list(range(4, 14))
    assert eq["text"] == "기준 4.00 h (사이클 4~13 평균) · 최근 10회 4.00 h · 0.0% · 최근 10회 중 10회가 기준과 겹침"
    assert [c for c, s in eq["vd"] if s == "skip"] == [1, 2, 3] and all(c["state"] == "ok" for c in eq["cells"])

    assert r["st97"]["base"] == [4] + list(range(6, 15)) and r["st97"]["ex"] == [{"c": 5, "why": "방전 시작 97 % (98 % 미만)"}]
    assert "사이클 4~14 중 10회 평균" in r["st97"]["text"] and "사이클 5 — 방전 시작 97 % (98 % 미만)" in r["st97"]["tip"]
    assert r["st98"]["base"] == list(range(4, 14)) and r["st98"]["ex"] == []
    assert r["stab"]["base"] == list(range(4, 14)) and r["stab"]["ex"] == []

    n = r["note"]
    assert n["base"] == [4, 5, 6, 7, 9, 11, 12, 13, 14, 15]
    assert n["ex"] == [{"c": 8, "why": "충전 중 엔진 교체(재시작)"}, {"c": 10, "why": "세트 구성 변경으로 재시작"}]
    assert "고른 사이클: 4, 5, 6, 7, 9, 11, 12, 13, 14, 15 (사이클 4~15 중 10회 평균)" in n["tip"]
    assert "기준 범위에서 뺀 사이클: 사이클 8 — 충전 중 엔진 교체(재시작) · 사이클 10 — 세트 구성 변경으로 재시작" in n["tip"]
    assert n["heads"]["8"] == "사이클 8 · 판정 제외 · 충전 중 엔진 교체(재시작)" and n["heads"]["4"] == "사이클 4 · 정상 · 기준 사이클"

    assert r["norow"]["base"] == [4, 5, 6] + list(range(8, 15)) and r["norow"]["ex"] == [{"c": 7, "why": "사이클 기록 없음"}]
    assert r["nocyc"]["ready"] is False and r["nocyc"]["base"] == [] and r["nocyc"]["text"] == "기준 산출 중 (0/10)"
    assert r["both"]["ex"] == [{"c": 9, "why": "방전 시작 95 % (98 % 미만) · 세트 구성 변경으로 재시작"}]
    assert r["charging"]["ex"] == [{"c": 14, "why": "사이클 기록 전 (충전이 끝나면 기록된다)"}] and r["charging"]["base"] == list(range(4, 14))
    assert r["codes"]["ex"] == [{"c": 5, "why": "충전 시간 초과"}, {"c": 6, "why": "weird_code"}]


@need_chrome
def test_base_is_fixed_and_recent_moves():
    """앞의 10개가 정해지면 뒤 기록이 늘어도(값이 떨어지고 비고가 생겨도) 기준 사이클과 기준값이 그대로다."""
    v = "(s,c)=>c<=13?4+0.01*c+0.1*s:3.8-0.001*c"
    r = run_js({
        "a": f"brief(calc(13,{{v:{v}}}))",
        "b": f"brief(calc(40,{{v:{v},note:{{20:'charge_timeout'}},st:{{25:90}}}}))",
    })
    a, b = r["a"], r["b"]
    assert a["base"] == b["base"] == list(range(4, 14))
    assert a["ref"] == b["ref"] and a["refN"] == b["refN"]
    assert b["recent"] == list(range(31, 41)) and b["chg"] < -5
    assert b["text"].startswith("기준 ") and "최근 10회 3.76 h" in b["text"] and b["text"].endswith("%") and "−" in b["text"]
    # 최근 범위 툴팁 — 최근 31~40 에는 빠진 사이클이 없다 · 기준 범위(4~13)에도 없다
    assert "최근 범위에서 뺀 사이클: 없음" in b["tip"] and "기준 범위에서 뺀 사이클: 없음" in b["tip"]
    assert b["overlap"] == 0 and "겹침" not in b["text"] and [k for k, _ in b["explain"]] == ["규칙", "고른 사이클", "기준 범위에서 뺀 사이클", "최근 10회", "최근 범위에서 뺀 사이클"]


@need_chrome
def test_cell_base_uses_same_cycles_and_reports_n():
    """셀별 기준은 세트가 고른 사이클들에서 그 셀의 값 평균 — 값이 빠진 사이클은 빼고 평균하되 n 으로 보인다."""
    r = run_js({
        # 셀 2 는 사이클 5 · 6 에 값이 없다(행 없음) · 셀 3 은 사이클 7 에 빈 값
        "m": "brief(calc(13,{ser:[1,2,3],v:(s,c)=>s===2&&(c===5||c===6)?undefined:s===3&&c===7?null:(s===1?4:s===2?5+(c===4?1:0):3)}))",
    })
    m = r["m"]
    assert m["base"] == list(range(4, 14))
    assert m["refN"] == {"1": 10, "2": 8, "3": 9, "avg": 10}
    assert abs(m["ref"]["2"] - (6 + 5 * 7) / 8) < 1e-12 and m["ref"]["1"] == 4 and m["ref"]["3"] == 3


@need_chrome
def test_verdicts_use_new_base_and_only_filtered_cycles():
    """판정(가안: −5 % 밖 3연속 → 주의, 주의 2회 → 점검)이 새 기준을 쓰고, 거르기를 통과한 사이클만 센다."""
    r = run_js({
        # 안정화 1~3 이 −10 % 여도 주의가 나지 않는다(첫 사이클 기준이었다면 기준 자체가 낮았다)
        "stab": "brief(calc(16,{v:(s,c)=>c<=3?3.6:4}))",
        # 셀 2 가 14·15·16 에서 −6 % → 16회에 주의, 17·18·19 에서 또 → 19회에 점검
        "warn": "brief(calc(19,{v:(s,c)=>s===2&&c>=14?3.76:4}))",
        # 15회는 비고(reconfigure)로 빠진다 — 연속을 끊지 않고 건너뛴다: 14 · 16 · 17 → 17회에 주의
        "skip": "brief(calc(17,{v:(s,c)=>s===2&&c>=14&&c!==15?3.76:4,note:{15:'reconfigure'}}))",
    })
    assert all(c["state"] == "ok" and c["caution"] == 0 for c in r["stab"]["cells"])
    w = {c["s"]: c for c in r["warn"]["cells"]}
    assert w[2]["state"] == "inspect" and w[2]["caution"] == 1 and w[2]["inspect"] == 1 and w[2]["streak"] == 6
    assert w[1]["state"] == "ok"
    assert dict(r["warn"]["vd"])[16] == "caution" and dict(r["warn"]["vd"])[19] == "inspect"
    s = {c["s"]: c for c in r["skip"]["cells"]}
    assert s[2]["state"] == "caution" and dict(r["skip"]["vd"])[15] == "skip" and dict(r["skip"]["vd"])[17] == "caution"


@need_chrome
def test_text_and_span():
    r = run_js({
        "t": "tbText({ready:true,base:[4,5,6,7,8,9,10,11,12,13],recent:[62,63,64,65,66,67,68,69,70,71]},4.70,4.5826)",
        "gap": "tbSpan([4,6,7,8,9,10,15,16,17,18])",
        "run": "tbSpan([4,5,6,7,8,9,10,11,12,13])",
        "pct": "[tbPct(-2.5),tbPct(0),tbPct(8.94),tbPct(-0.04),tbPct(0.04)]",
        "fix": "[tbFix1(-0.04),tbFix1(-0.06),tbFix1(1.25),tbSign(-1.24),tbSign(0.0)]",   # QA C: '−0.0' 을 쓰지 않는다
        "zone": "[tbZone(null),tbZone(-1),tbZone(-2.5),tbZone(-3),tbZone(-5),tbZone(-6)]",
    })
    assert r["t"] == "기준 4.70 h (사이클 4~13 평균) · 최근 10회 4.58 h · −2.5%"
    assert r["gap"] == "사이클 4~18 중 10회 평균" and r["run"] == "사이클 4~13 평균"
    assert r["pct"] == ["−2.5%", "0.0%", "+8.9%", "0.0%", "0.0%"]
    assert r["fix"] == ["0.0", "−0.1", "1.3", "−1.2", "0.0"]
    assert r["zone"] == ["", "", "", "관찰 구간", "관찰 구간", "이탈 구간 (−5 % 밖)"]


@need_chrome
def test_overlap_right_after_base():
    """기준이 정해진 직후 최근 10회가 기준 사이클과 겹치는 동안 — 변화율은 내되 겹친 횟수를 함께 적는다(QA #5)."""
    r = run_js({"a": "brief(calc(18))", "b": "brief(calc(23))"})
    a, b = r["a"], r["b"]
    assert a["recent"] == list(range(9, 19)) and a["overlap"] == 5 and a["chg"] is not None
    assert a["text"].endswith("· 최근 10회 중 5회가 기준과 겹침") and a["explain"][-1][0] == "겹침"
    assert b["overlap"] == 0 and "겹침" not in b["text"]


@need_chrome
def test_badge_basis_and_zone():
    """셀 배지(정상 · 범위 밖)는 판정을 거친 마지막 한 사이클이 근거고, 큰 숫자는 최근 10회 평균이다 — 둘이 엇갈려도 근거를 글로 보인다(QA #1)."""
    r = run_js({
        # 셀 2 는 사이클 20 한 회만 −7 % → 배지 '범위 밖', 큰 숫자(최근 10회 평균)는 −0.7 %
        "one": "brief(calc(20,{v:(s,c)=>s===2&&c===20?3.72:4}))",
        "warn": "brief(calc(19,{v:(s,c)=>s===2&&c>=14?3.76:4}))",
        "caut": "brief(calc(16,{v:(s,c)=>s===2&&c>=14?3.76:4}))",
    })
    o = r["one"]
    assert {c["s"]: c["state"] for c in o["cells"]} == {1: "ok", 2: "out"}
    assert o["badges"]["2"] == "범위 밖 — 사이클 20 한 회 −7.0%" and o["badges"]["1"] == "정상 — 사이클 20 한 회 0.0%"
    assert abs([c for c in o["cells"] if c["s"] == 2][0]["chg"] - (-0.7)) < 1e-9
    assert r["caut"]["badges"]["2"] == "주의 — 사이클 16까지 3사이클 연속 −5 % 밖"
    assert r["warn"]["badges"]["2"] == "점검 — 사이클 19에서 두 번째 주의(3사이클 연속 −5 % 밖)"


@need_chrome
def test_cell_without_base_values_explains_why():
    """기준 사이클 10회에 값이 하나도 없는 셀 — 판정 대기를 유지하되 이유를 적는다(QA #4)."""
    r = run_js({"m": "brief(calc(20,{v:(s,c)=>s===2&&c<=13?undefined:4}))"})
    c2 = [c for c in r["m"]["cells"] if c["s"] == 2][0]
    why = "기준 사이클 10회에 이 셀 값이 없다 — 운전 중 교체된 셀일 수 있다"
    assert r["m"]["ready"] is True and c2["state"] == "wait" and c2["why"] == why and r["m"]["badges"]["2"] == why
    assert [c for c in r["m"]["cells"] if c["s"] == 1][0]["why"] == ""


def _between(a: str, b: str) -> str:
    return SRC[SRC.index(a):SRC.index(b)]


def test_wiring_for_qa31_fixes():
    """QA PR #31 결과의 배선 — 화면에 실제로 닿는 자리를 못 박는다."""
    view = _between("V.trend={", "// ───── 4.")
    # #2 기준 설명: 키보드 · 터치로 열리는 <details> 가 통계 카드 바로 아래, 갱신마다 안쪽만 다시 그린다
    assert '<div class="stats" id="trStats"></div>\n    <details class="card fold" id="tbFold"><summary><span style="white-space:nowrap">기준 설명</span>' in view
    assert "renderTrendStats();renderTrendBase();renderHeat()" in view
    assert "tbExplain(P).concat([ggExplainRow(M.G)]).map(" in _between("function renderTrendBase", "let HMsig")   # <gauge> 줄을 덧붙여 같은 자리에
    # #2 375px: '기준 대비' · '주의 · 점검' 카드 아랫줄은 줄바꿈 허용
    stats = _between("function renderTrendStats", "function renderTrendBase")
    assert stats.count('<span class="foot wrap">') == 2 and ".stat .foot.wrap span{white-space:normal" in SRC
    # #6 행 호버가 기준 테두리를 덮지 않는다 — 기준 규칙이 호버 규칙 뒤에, 같은 특이성으로
    assert SRC.index(".hm .row-b:hover>.hc{") < SRC.index(".hm .row-b>.hc.h-base{")
    # #7 제외 칸은 판정 색 대신 중립 바탕, 범례 견본과 같은 모양
    heat = _between("function renderHeat", "function defaultTrendCell")
    assert "${xs.has(cy)?'h-x':'h-'+vtone(p)}" in heat and ".hm .hc.h-x{background:var(--bg)" in SRC and "h-x{opacity" not in SRC
    assert 'background:var(--bg);box-shadow:inset 0 0 0 1px var(--line)"></i>제외 (판정에 안 씀)' in view
    # #9 머리 원 툴팁은 tbHead 하나에서 · C 히트맵 칸 숫자는 tbFix1('−0.0' 없음)
    assert 'title="${esc(tbHead(M,r)+ggHeadMark(M.G,r.c))}"' in heat and "neg(p.toFixed(1))" not in heat   # <gauge> 재학습 표지를 같은 툴팁에 덧붙인다
    # #1 · #8 셀 카드: 배지 근거와 큰 숫자 근거를 글로, 관찰 구간 낱말
    insp = _between("function trendInsp", "let CTsig")
    assert "배지 근거</b> · ${esc(tbBadge(M,c))}" in insp and "큰 숫자</b> · " in insp and "tbZone(c.chg)" in insp
    # #4 기준 없는 셀: 그 이유를 기준 칸 · 그래프 빈 자리에
    assert "esc(c.why)" in insp and "empty:c.why||tbText(P)" in insp
    # A 세트 상세 → 추이 분석은 보던 세트로
    assert "go('trend',{tset:S.set,tsel:s,insp:true})" in SRC and "if('tset' in o)U.tset=o.tset;" in SRC
    # #3 절차 · 기준 화면의 규칙 문장은 화살표 사슬이 아니라 연결어가 있는 문장
    assert "사이클 종료 → 셀별 작동시간" not in SRC and "사이클이 끝날 때마다 셀별 작동시간을 기준과 비교한다." in SRC
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "'기준 설명' 줄을 펼치면" in readme and "마우스를 올리면 보인다" not in readme


# ---- 실데이터 — 이 시험대 기록에서 고른 사이클이 손으로 거른 것과 같은지 ----
def _real_dir() -> Path | None:
    d = Path(os.environ.get("CELLBENCH_REAL_DATA") or ROOT / "data")
    try:   # 이 시험대(세트 1 첫 사이클 2026-10-10 10:35:11)의 기록일 때만 — 다른 PC 의 data/ 로 오판하지 않는다
        first = next(csv.DictReader((d / "cycles.csv").open(encoding="utf-8-sig")))
    except (OSError, StopIteration):
        return None
    return d if first.get("start") == "2026-10-10 10:35:11" and (d / "set2" / "cycles.csv").exists() else None


REAL = _real_dir()


def _load(d: Path):
    cyc = list(csv.DictReader((d / "cycles.csv").open(encoding="utf-8-sig")))
    dis = []
    for p in sorted(d.glob("discharge_*.csv")):
        dis += list(csv.DictReader(p.open(encoding="utf-8-sig")))
    return cyc, dis


def _py_base(dis, cycles):
    """같은 기준을 파이썬으로 따로 계산 — 셀 평균의 사이클 평균(세트) · 셀별 평균."""
    by = {}
    for r in dis:
        if r["est_runtime_h"] not in ("", None):
            by.setdefault(int(r["cycle"]), {})[int(r["serial"])] = float(r["est_runtime_h"])
    avg = sum(sum(by[c].values()) / len(by[c]) for c in cycles) / len(cycles)
    cells = {}
    for s in sorted({s for c in cycles for s in by[c]}):
        v = [by[c][s] for c in cycles if s in by[c]]
        cells[s] = sum(v) / len(v)
    return avg, cells


@need_chrome
@pytest.mark.skipif(REAL is None, reason="이 시험대의 실기록(data/ · data/set2/)이 없음 — CELLBENCH_REAL_DATA 로 경로를 줄 수 있다")
def test_real_data_selection_matches_hand_filter():
    s1, s2 = _load(REAL), _load(REAL / "set2")
    pre = f"const S1={json.dumps({'C': s1[0], 'D': s1[1]}, ensure_ascii=False)};const S2={json.dumps({'C': s2[0], 'D': s2[1]}, ensure_ascii=False)};"
    r = run_js({"y4": "brief(trendCalc(S1.D,S1.C))", "x6": "brief(trendCalc(S2.D,S2.C))"}, pre)
    y4, x6 = r["y4"], r["x6"]
    # 세트 1(Y4): 5 시작 95 % · 11 · 14 restart_in_charge · 12 · 13 reconfigure 제외
    assert y4["base"] == [4, 6, 7, 8, 9, 10, 15, 16, 17, 18]
    assert [x for x in y4["ex"] if x["c"] <= 18] == [
        {"c": 5, "why": "방전 시작 95 % (98 % 미만)"}, {"c": 11, "why": "충전 중 엔진 교체(재시작)"}, {"c": 12, "why": "세트 구성 변경으로 재시작"},
        {"c": 13, "why": "세트 구성 변경으로 재시작"}, {"c": 14, "why": "충전 중 엔진 교체(재시작)"}]
    # 세트 2(X6): 8 · 10 restart_in_charge 제외
    assert x6["base"] == [4, 5, 6, 7, 9, 11, 12, 13, 14, 15]
    assert [x for x in x6["ex"] if x["c"] <= 15] == [{"c": 8, "why": "충전 중 엔진 교체(재시작)"}, {"c": 10, "why": "충전 중 엔진 교체(재시작)"}]
    for got, (cyc, dis) in ((y4, s1), (x6, s2)):
        assert got["ready"] is True and len(got["recent"]) == 10
        avg, cells = _py_base(dis, got["base"])
        assert abs(got["ref"]["avg"] - avg) < 1e-9
        assert all(abs(got["ref"][str(s)] - v) < 1e-9 for s, v in cells.items())
