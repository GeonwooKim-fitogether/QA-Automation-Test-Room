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
const brief=M=>({base:M.P.base,recent:M.P.recent,ready:M.P.ready,ex:M.P.ex,ref:M.ref,refN:M.refN,now:M.now,chg:M.chg,
  vd:M.vd.map(r=>[r.c,r.status]),cells:M.cells.map(c=>({s:c.s,state:c.state,chg:c.chg,baseN:c.baseN,streak:c.streak,caution:c.caution,inspect:c.inspect})),
  text:tbText(M.P,M.ref.avg,M.now.avg),tip:tbTip(M.P)});
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
        "charging": "brief(calc(14,{noRow:[14]}))",                                 # 마지막 사이클은 충전이 끝나야 줄이 생긴다 — 그동안은 빼고 이유를 달리 적는다
    })
    lt = r["lt10"]
    assert lt["ready"] is False and lt["base"] == list(range(4, 13)) and lt["text"] == "기준 산출 중 (9/10)"
    assert lt["chg"] is None and lt["ref"]["avg"] is None and all(c["state"] == "wait" and c["chg"] is None for c in lt["cells"])
    assert all(s == "skip" for _, s in lt["vd"]), "10개 전에는 셀 판정을 내지 않는다"
    assert lt["tip"].startswith("기준 산출 중 (9/10)")

    eq = r["eq10"]
    assert eq["ready"] is True and eq["base"] == list(range(4, 14)) and eq["recent"] == list(range(4, 14))
    assert eq["text"] == "기준 4.00 h (사이클 4~13 평균) · 최근 10회 4.00 h · +0.0%"
    assert [c for c, s in eq["vd"] if s == "skip"] == [1, 2, 3] and all(c["state"] == "ok" for c in eq["cells"])

    assert r["st97"]["base"] == [4] + list(range(6, 15)) and r["st97"]["ex"] == [{"c": 5, "why": "방전 시작 97 %"}]
    assert "사이클 4~14 중 10회 평균" in r["st97"]["text"] and "5회 방전 시작 97 %" in r["st97"]["tip"]
    assert r["st98"]["base"] == list(range(4, 14)) and r["st98"]["ex"] == []
    assert r["stab"]["base"] == list(range(4, 14)) and r["stab"]["ex"] == []

    n = r["note"]
    assert n["base"] == [4, 5, 6, 7, 9, 11, 12, 13, 14, 15]
    assert n["ex"] == [{"c": 8, "why": "restart_in_charge"}, {"c": 10, "why": "reconfigure"}]
    assert "고른 사이클: 4, 5, 6, 7, 9, 11, 12, 13, 14, 15" in n["tip"] and "기준 범위에서 제외: 8회 restart_in_charge · 10회 reconfigure" in n["tip"]

    assert r["norow"]["base"] == [4, 5, 6] + list(range(8, 15)) and r["norow"]["ex"] == [{"c": 7, "why": "사이클 기록 없음"}]
    assert r["nocyc"]["ready"] is False and r["nocyc"]["base"] == [] and r["nocyc"]["text"] == "기준 산출 중 (0/10)"
    assert r["both"]["ex"] == [{"c": 9, "why": "방전 시작 95 % · reconfigure"}]
    assert r["charging"]["ex"] == [{"c": 14, "why": "사이클 기록 전 (충전 중이면 끝날 때 기록)"}] and r["charging"]["base"] == list(range(4, 14))


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
    assert "최근 범위에서 제외: 없음" in b["tip"] and "기준 범위에서 제외: 없음" in b["tip"]


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
        "pct": "[tbPct(-2.5),tbPct(0),tbPct(8.94)]",
    })
    assert r["t"] == "기준 4.70 h (사이클 4~13 평균) · 최근 10회 4.58 h · −2.5%"
    assert r["gap"] == "사이클 4~18 중 10회 평균" and r["run"] == "사이클 4~13 평균"
    assert r["pct"] == ["−2.5%", "+0.0%", "+8.9%"]


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
        {"c": 5, "why": "방전 시작 95 %"}, {"c": 11, "why": "restart_in_charge"}, {"c": 12, "why": "reconfigure"},
        {"c": 13, "why": "reconfigure"}, {"c": 14, "why": "restart_in_charge"}]
    # 세트 2(X6): 8 · 10 restart_in_charge 제외
    assert x6["base"] == [4, 5, 6, 7, 9, 11, 12, 13, 14, 15]
    assert [x for x in x6["ex"] if x["c"] <= 15] == [{"c": 8, "why": "restart_in_charge"}, {"c": 10, "why": "restart_in_charge"}]
    for got, (cyc, dis) in ((y4, s1), (x6, s2)):
        assert got["ready"] is True and len(got["recent"]) == 10
        avg, cells = _py_base(dis, got["base"])
        assert abs(got["ref"]["avg"] - avg) < 1e-9
        assert all(abs(got["ref"][str(s)] - v) < 1e-9 for s, v in cells.items())
