"""추이 분석의 기준값 — 첫 사이클이 아니라 '안정화 뒤 10사이클 평균'을 기준으로, '최근 10사이클 평균'을 비교값으로 쓴다. 안정화 구간은 세트마다 데이터로 정한다.

왜: 첫 몇 사이클은 안정화 구간이라 첫 사이클 기준이면 결론이 뒤집혔다(세트 2: 첫 사이클 기준 +8.9 % · 4~13회 평균 기준 −2.5 %).
그리고 사이클 간 흔들림(1.8~2.6 %)이 보려는 변화(노화 ~2.5 %)와 같은 크기라 한 사이클끼리 비교하면 안 된다.
'1~3' 은 눈대중이었다 — 배터리 IC(연료 게이지)의 용량 학습 속도가 세트마다 달라, 실데이터로 재니 세트 2 는 3회, 세트 1 은 5회였다.

규칙(세트 단위로 사이클을 고르고, 셀별 기준은 그 같은 사이클들에서 그 셀의 값 평균)
  A. 안정화 끝 n₁ — 사이클 1~15 중 거르기(아래 (a)(b))를 통과한 (사이클, 셀 평균 est) 점에 y = y∞ − A·exp(−n/τ) 를 맞춰(τ 0.5~8.0 격자 · 각 τ 에서 선형 최소제곱),
     점근값의 1 % 안에 처음 드는 사이클 ceil(−τ·ln(0.01·y∞/A)). A ≤ 0 이면 안정화 없음(n₁ = 1). 유효 점이 4개 미만이면 옛 규칙(1~3 제외).
  B. 보조 판정(정착) — 그 값이 '그 뒤 유효 10회 평균 ±2σ(σ 최소 1 %)' 안에 3회 연속 드는 첫 사이클(후보 1~15). A 와 2회 넘게 다르면 늦은(큰) 쪽을 n₁ 로.
     B 가 정해지기 전(뒤 기록 부족)에는 n₁ 을 확정하지 않는다 — 그래야 한번 정해진 기준이 바뀌지 않는다.
  C. 충전 Wh/%p — cycles.csv 의 charge_wh ÷ (100 − min_batt_at_stop). 거르기 통과 사이클 10~30 의 평균 ±2σ(σ 최소 1 %, 표본 8개부터)가 정상.
     2회 연속 벗어나면 그 사이클부터 '충전량 이상(IC 재학습 의심)' 표지(세트 단위 · 머리 점선 원 — 셀 단위 '셀 IC 재학습'(세모)과 다른 것) — 표지 · 설명에만 쓰고 기준 · 판정에는 쓰지 않는다(기준 후보 거르기에 넣으면 정상 범위가 자라는 동안 기준이 흔들린다).
  1. 사이클 n₁ 부터 차례로 (a) 방전 시작(그 사이클 discharge 행들의 start_pct 최저) 98 % 이상, (b) 사이클 기록의 note 가 비어 있음 —
     을 모두 만족하는 사이클을 앞에서부터 10개 고른다. 사이클 기록이 아직 없는 사이클은 note 를 확인할 수 없어 뺀다.
  2. 기준값 = 고른 10개 평균. n₁ 은 1~15 의 기록(과 보조 판정)으로만 정하므로 뒤 기록이 늘어도 같은 10개다(고정).
  3. 사이클 15 기록 전에는 '안정화 판정 중 (m/15)', 10개가 안 모였으면 '기준 산출 중 (n/10)' — 변화율 · 셀 판정을 내지 않는다.
  비교값 = 같은 거르기를 통과한 최근 10사이클 평균.

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

# 가짜 기록 — n 사이클 × 셀(ser). st = 사이클별 방전 시작 %, note = 사이클별 비고, noRow = 사이클 기록이 없는 사이클, v(s,c) = 셀 값(undefined 면 행 없음),
# wh(c) = 사이클 기록의 충전 Wh(없으면 빈칸 → C 검사 생략). 기본값 4 에서 사이클 1~3 은 SD 만큼 낮춰 지수 적합이 n₁=4 를 내게 한다(옛 규칙과 같은 자리) · flat 이면 낮추지 않는다
MK = """const SD={1:0.5,2:0.2,3:0.07};
const mk=(n,o={})=>{const D=[],C=[],ser=o.ser||[1,2];for(let c=1;c<=n;c++){ser.forEach(s=>{let v=o.v?o.v(s,c):4;if(v===undefined)return;if(v!=null&&!o.flat&&c<=3)v-=SD[c];
  D.push({cycle:String(c),serial:String(s),start_pct:String((o.st||{})[c]??100),est_runtime_h:v==null?'':String(v)});});
  if(!(o.noRow||[]).includes(c))C.push({cycle:String(c),note:(o.note||{})[c]||'',charge_wh:o.wh?String(o.wh(c)):'',min_batt_at_stop:'30'});}return {D,C};};
const calc=(n,o)=>{const {D,C}=mk(n,o);return trendCalc(D,C);};
const r3=x=>x==null?null:Math.round(x*1000)/1000;
const brief=M=>({base:M.P.base,recent:M.P.recent,ready:M.P.ready,ex:M.P.ex,overlap:M.P.overlap,ref:M.ref,refN:M.refN,now:M.now,chg:M.chg,n1:M.P.n1,
  settle:{kind:M.P.settle.kind,m:M.P.settle.m,tau:M.P.settle.tau,yinf:r3(M.P.settle.yinf),A:r3(M.P.settle.A),n1raw:r3(M.P.settle.n1raw),n1A:M.P.settle.n1A,n1B:M.P.settle.n1B,mismatch:M.P.settle.mismatch,late:M.P.settle.late,miss:M.P.settle.miss},
  wh:{has:M.P.wh.has,avail:M.P.wh.avail,mean:r3(M.P.wh.mean),sd:r3(M.P.wh.sd),lo:r3(M.P.wh.lo),hi:r3(M.P.wh.hi),n:M.P.wh.n,flags:[...M.P.wh.flags].sort((a,b)=>a-b)},
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
        "settling": "brief(calc(14))",                                                # 사이클 15 기록 전 — 안정화 판정 중
        "waitB": "brief(calc(15,{st:{12:97,13:97,14:97}}))",                          # 1~15 기록은 모였지만 보조 판정에 필요한 뒤 기록이 모자라다 — n₁ 미확정
        "lt10": "brief(calc(20,{flat:true,v:(s,c)=>5-Math.exp(-c/4)}))",             # n₁=12 · 기준 후보 12~20 = 9개 (보조 판정 8 은 20회 기록에서 정해진다)
        "eq10": "brief(calc(15))",
        "st97": "brief(calc(16,{st:{5:97}}))",                                       # 시작 97 % 는 뺀다
        "st98": "brief(calc(15,{st:{6:98}}))",                                       # 98 % 는 넣는다(경계)
        "st4": "brief(calc(16,{st:{4:97}}))",                                        # 안정화 끝 바로 그 사이클이 빠진다
        "stab": "brief(calc(16,{st:{1:60,2:70,3:80}}))",                             # 1~3 이 거르기에서 빠지면 적합은 남은 점(상수)만 본다 → 적합 1, 보조 판정 4(첫 유효) → 늦은 쪽 4. 1~3 은 어차피 기준에 안 든다
        "note": "brief(calc(17,{note:{8:'restart_in_charge',10:'reconfigure'}}))",   # 비고 있음 → 빼고 뒤로 늘린다
        "norow": "brief(calc(16,{noRow:[7]}))",                                      # 사이클 기록 없음 → note 를 확인할 수 없어 뺀다
        "nocyc": "(()=>{const {D}=mk(20);return brief(trendCalc(D,[]));})()",       # 사이클 기록을 못 받았으면 안정화 판정도 못 한다
        "both": "brief(calc(16,{st:{9:95},note:{9:'reconfigure'}}))",
        "charging": "brief(calc(16,{noRow:[16]}))",                                  # 마지막 사이클은 충전이 끝나야 줄이 생긴다 — 그동안은 빼고 이유를 달리 적는다
        "codes": "brief(calc(18,{note:{5:'charge_timeout',6:'weird_code'}}))",       # 아는 코드는 사람 말로, 모르는 코드는 원문 그대로
    })
    st = r["settling"]
    assert st["ready"] is False and st["n1"] is None and st["base"] == [] and st["text"] == "안정화 판정 중 (14/15)"
    assert st["chg"] is None and all(c["state"] == "wait" and c["why"] == "안정화 판정 중 (14/15)" for c in st["cells"])
    assert [s for _, s in st["vd"]] == ["wait"] * 14 and st["heads"]["2"] == "사이클 2 · 판정 대기 · 안정화 판정 중 (14/15)"
    assert st["explain"][1] == ["안정화", "안정화 판정 중 (14/15) — 사이클 15의 기록이 쌓이면 사이클 1~15의 평균 작동시간으로 안정화 구간을 정한다."]
    assert st["explain"][2] == ["충전량 이상(IC 재학습 의심)", "충전 Wh 기록이 없어 이 표지는 내지 않는다."]
    assert [k for k, _ in st["explain"]] == ["규칙", "안정화", "충전량 이상(IC 재학습 의심)", "진행", "거르기에서 뺀 사이클"]

    wb = r["waitB"]
    assert wb["n1"] is None and wb["settle"]["kind"] == "waitB" and wb["settle"]["n1A"] == 4 and wb["settle"]["n1B"] is None and wb["base"] == []
    assert wb["text"] == "안정화 판정 중 (15/15 · 뒤 기록 대기)" and wb["heads"]["4"] == "사이클 4 · 판정 대기 · 안정화 판정 중 (15/15 · 뒤 기록 대기)"
    assert [s for _, s in wb["vd"]] == ["wait"] * 11 + ["skip"] * 3 + ["wait"]
    assert wb["explain"][1][1] == ("사이클 1~15 기록은 모였다 — 지수 곡선 적합으로는 사이클 4부터 최종값(4.00 h)의 1 % 안에 든다(변화가 사이클 1.0회마다 약 3분의 1로 줄어드는 곡선). "
                                   "보조 판정(값이 뒤 10회 평균 안에 3회 연속 드는 첫 사이클)을 정하려면 뒤 기록이 더 필요해 기다리는 중이며, 그동안 기준을 정하지 않는다.")

    lt = r["lt10"]
    assert lt["n1"] == 12 and lt["ready"] is False and lt["base"] == list(range(12, 21)) and lt["text"] == "기준 산출 중 (9/10)"
    assert lt["chg"] is None and lt["ref"]["avg"] is None and all(c["state"] == "wait" and c["chg"] is None for c in lt["cells"])
    # 10개 전에는 판정하지 않는다 — 안정화 구간은 '판정 제외', 기준 후보는 '판정 대기'. 머리 원 툴팁에 '제외'와 '기준 사이클'이 함께 나오지 않는다(QA #9)
    assert [s for _, s in lt["vd"]] == ["skip"] * 11 + ["wait"] * 9
    assert lt["heads"]["20"] == "사이클 20 · 판정 대기 · 기준 후보 (9/10)" and lt["heads"]["2"] == "사이클 2 · 판정 제외 · 안정화 구간 (사이클 1~11)"
    assert "진행: 기준 산출 중 (9/10)" in lt["tip"] and all(c["why"] == "기준 산출 중 (9/10)" for c in lt["cells"])
    assert lt["badges"]["1"] == "기준 산출 중 (9/10)"
    assert [k for k, _ in lt["explain"]] == ["규칙", "안정화", "충전량 이상(IC 재학습 의심)", "진행", "지금까지 고른 사이클", "뺀 사이클"]

    eq = r["eq10"]
    assert eq["n1"] == 4 and eq["settle"]["kind"] == "fit" and eq["ready"] is True and eq["base"] == list(range(4, 14)) and eq["recent"] == list(range(6, 16))
    assert eq["text"] == "기준 4.00 h (사이클 4~13 평균) · 최근 10회 4.00 h · 0.0% · 최근 10회 중 8회가 기준과 겹침"
    assert [c for c, s in eq["vd"] if s == "skip"] == [1, 2, 3] and all(c["state"] == "ok" for c in eq["cells"])
    assert eq["explain"][1][1] == ("평균 작동시간이 사이클 4부터 최종값(4.00 h)의 1 % 안에 들어와, 그때부터를 안정화 뒤로 본다. 지수 곡선 적합 결과이며, 변화가 사이클 1.0회마다 약 3분의 1로 줄어드는 곡선이다. "
                                   "보조 판정(값이 뒤 10회 평균 안에 3회 연속 드는 첫 사이클)은 사이클 3으로, 1사이클 차이다.")
    assert [k for k, _ in eq["explain"]] == ["규칙", "안정화", "충전량 이상(IC 재학습 의심)", "고른 사이클", "기준 범위에서 뺀 사이클", "최근 10회", "최근 범위에서 뺀 사이클", "겹침"]
    assert "σ" not in eq["tip"] and "진입" not in eq["tip"] and "정착" not in eq["tip"] and "기준은 한번 정해지면 다시 바뀌지 않는다. 사이클 15까지 기록이 모이고, 보조 판정에 필요한 뒤 기록이 몇 회 더 쌓이면 정해진다." in eq["tip"]
    assert "변화율의 + 는 최근 10회의 작동시간이 기준보다 길다는 뜻, − 는 짧다는 뜻이다." in eq["tip"] and "변화율 주의" not in eq["tip"]

    assert r["st97"]["base"] == [4] + list(range(6, 15)) and [x for x in r["st97"]["ex"] if x["c"] >= 4] == [{"c": 5, "why": "방전 시작 97 % (98 % 미만)"}]
    assert "사이클 4~14 중 10회 평균" in r["st97"]["text"] and "사이클 5 — 방전 시작 97 % (98 % 미만)" in r["st97"]["tip"]
    assert r["st98"]["base"] == list(range(4, 14)) and r["st98"]["ex"] == []
    # 첫 기준 사이클 앞(안정화 끝 4 ~ 첫 기준 5)에서 거르기로 빠진 사이클도 '기준 범위에서 뺀 사이클' 에 보인다(QA #6)
    s4 = r["st4"]
    assert s4["n1"] == 4 and s4["base"][0] == 5 and dict(s4["explain"])["기준 범위에서 뺀 사이클"] == ["사이클 4 — 방전 시작 97 % (98 % 미만)"]
    sb = r["stab"]
    assert sb["n1"] == 4 and sb["settle"]["kind"] == "none" and sb["settle"]["n1A"] == 1 and sb["settle"]["n1B"] == 4 and sb["base"] == list(range(4, 14)) and [x["c"] for x in sb["ex"]] == [1, 2, 3]
    assert sb["heads"]["2"] == "사이클 2 · 판정 제외 · 안정화 구간 (사이클 1~3)" and sb["explain"][1][1].startswith("평균 작동시간이 처음부터 평평하거나 내려와 지수 곡선 적합으로는 안정화 구간이 보이지 않는다(사이클 1부터). 보조 판정")

    n = r["note"]
    assert n["base"] == [4, 5, 6, 7, 9, 11, 12, 13, 14, 15]
    assert n["ex"] == [{"c": 8, "why": "충전 중 엔진 교체(재시작)"}, {"c": 10, "why": "세트 구성 변경으로 재시작"}]
    assert "고른 사이클: 4, 5, 6, 7, 9, 11, 12, 13, 14, 15 (사이클 4~15 중 10회 평균)" in n["tip"]
    assert "기준 범위에서 뺀 사이클: 사이클 8 — 충전 중 엔진 교체(재시작) · 사이클 10 — 세트 구성 변경으로 재시작" in n["tip"]
    assert n["heads"]["8"] == "사이클 8 · 판정 제외 · 충전 중 엔진 교체(재시작)" and n["heads"]["4"] == "사이클 4 · 정상 · 기준 사이클"

    assert r["norow"]["base"] == [4, 5, 6] + list(range(8, 15)) and r["norow"]["ex"] == [{"c": 7, "why": "사이클 기록 없음"}]
    assert r["nocyc"]["ready"] is False and r["nocyc"]["n1"] is None and r["nocyc"]["base"] == [] and r["nocyc"]["text"] == "안정화 판정 중 (0/15)"
    assert r["both"]["ex"] == [{"c": 9, "why": "방전 시작 95 % (98 % 미만) · 세트 구성 변경으로 재시작"}]
    assert r["charging"]["ex"] == [{"c": 16, "why": "사이클 기록 전 (충전이 끝나면 기록된다)"}] and r["charging"]["base"] == list(range(4, 14))
    assert r["codes"]["ex"] == [{"c": 5, "why": "충전 시간 초과"}, {"c": 6, "why": "weird_code"}]


@need_chrome
def test_settle_fit_recovers_known_curve():
    """합성 지수 곡선(τ · y∞ · A 를 알고 있음)에서 τ 와 n₁ 을 되찾는다 — A ≤ 0 이면 적합으로는 안정화 없음, 유효 점이 적으면 옛 규칙. 보조 판정과 2회 넘게 다르면 늦은 쪽."""
    r = run_js({
        # y = 5 − 1.0·exp(−c/2.0) → τ=2.0, n₁ = ceil(−2·ln(0.05/1.0)) = ceil(5.99) = 6 → 기준 6~15. 보조 판정 5 (1회 차이)
        "exp2": "brief(calc(20,{flat:true,v:(s,c)=>5-Math.exp(-c/2)}))",
        # τ=4.0 → 적합 12, 보조 판정 8 → 4회 차이 → 늦은 쪽(12)
        "exp4": "brief(calc(30,{flat:true,v:(s,c)=>5-Math.exp(-c/4)}))",
        # 셀마다 다른 수준(4 · 5)이어도 세트 평균(4.5)에 맞춘다
        "cells": "brief(calc(20,{flat:true,v:(s,c)=>(s===1?4:5)-0.8*Math.exp(-c/1.5)}))",
        "flat": "brief(calc(15,{flat:true}))",                                        # 상수 → A=0 → 적합으로는 안정화 없음 · 보조 판정도 1 → 사이클 1부터
        "down": "brief(calc(20,{flat:true,v:(s,c)=>4+0.5*Math.exp(-c/2)}))",          # 내려오는 곡선(A<0) → 적합 1, 보조 판정 4 → 늦은 쪽(4)
        "few": "brief(calc(30,{st:Object.fromEntries(Array.from({length:12},(_,i)=>[i+1,97]))}))",   # 1~12 가 거르기에서 빠져 유효 점 3개 → 옛 규칙 4, 보조 판정은 첫 유효 사이클 13 → 늦은 쪽(13)
        "few_wait": "brief(calc(16,{st:Object.fromEntries(Array.from({length:12},(_,i)=>[i+1,97]))}))",   # 같은 데이터, 보조 판정의 뒤 기록이 모자라면 대기
    })
    e2 = r["exp2"]
    assert e2["settle"]["kind"] == "fit" and e2["settle"]["tau"] == 2.0 and abs(e2["settle"]["yinf"] - 5) < 1e-3 and abs(e2["settle"]["A"] - 1) < 1e-3
    assert abs(e2["settle"]["n1raw"] - 5.991) < 0.01 and e2["n1"] == 6 and e2["base"] == list(range(6, 16)) and e2["settle"]["n1B"] == 5 and e2["settle"]["mismatch"] is False
    assert e2["explain"][1][1] == ("평균 작동시간이 사이클 6부터 최종값(5.00 h)의 1 % 안에 들어와, 그때부터를 안정화 뒤로 본다. 지수 곡선 적합 결과이며, 변화가 사이클 2.0회마다 약 3분의 1로 줄어드는 곡선이다. "
                                   "보조 판정(값이 뒤 10회 평균 안에 3회 연속 드는 첫 사이클)은 사이클 5로, 1사이클 차이다.")
    assert e2["heads"]["5"] == "사이클 5 · 판정 제외 · 안정화 구간 (사이클 1~5)" and [s for _, s in e2["vd"]][:6] == ["skip"] * 5 + ["ok"]
    e4 = r["exp4"]
    assert e4["settle"]["tau"] == 4.0 and e4["settle"]["n1A"] == 12 and e4["settle"]["n1B"] == 8 and e4["settle"]["mismatch"] is True and e4["settle"]["late"] is False
    assert e4["n1"] == 12 and e4["base"] == list(range(12, 22))
    assert e4["explain"][1][1].endswith("보조 판정(값이 뒤 10회 평균 안에 3회 연속 드는 첫 사이클)은 사이클 8 — 두 판정이 2사이클 넘게 달라 늦은 쪽(사이클 12)을 썼다.")
    c = r["cells"]
    assert c["settle"]["tau"] == 1.5 and abs(c["settle"]["yinf"] - 4.5) < 1e-3 and c["settle"]["n1A"] == 5 and c["n1"] == 5   # −1.5·ln(0.045/0.8) = 4.32 → 5
    f = r["flat"]
    assert f["settle"]["kind"] == "none" and f["settle"]["n1A"] == 1 and f["settle"]["n1B"] == 1 and f["n1"] == 1 and f["base"] == list(range(1, 11)) and f["ready"] is True
    assert f["explain"][1][1] == "평균 작동시간이 처음부터 평평하거나 내려와 안정화 구간이 보이지 않는다 — 사이클 1부터 기준 후보로 본다. 보조 판정(값이 뒤 10회 평균 안에 3회 연속 드는 첫 사이클)도 사이클 1로 같다."
    d = r["down"]
    assert d["settle"]["kind"] == "none" and d["settle"]["n1A"] == 1 and d["settle"]["n1B"] == 4 and d["settle"]["late"] is True and d["n1"] == 4 and d["base"] == list(range(4, 14))
    assert d["explain"][1][1] == ("평균 작동시간이 처음부터 평평하거나 내려와 지수 곡선 적합으로는 안정화 구간이 보이지 않는다(사이클 1부터). "
                                  "보조 판정(값이 뒤 10회 평균 안에 3회 연속 드는 첫 사이클)은 사이클 4 — 두 판정이 2사이클 넘게 달라 늦은 쪽(사이클 4)을 썼다. 사이클 4부터를 안정화 뒤로 본다.")
    fw = r["few"]
    assert fw["settle"]["kind"] == "few" and fw["settle"]["n1A"] == 4 and fw["settle"]["n1B"] == 13 and fw["settle"]["late"] is True and fw["n1"] == 13 and fw["base"] == list(range(13, 23))
    assert fw["explain"][1][1].startswith("사이클 1~15 중 거르기를 통과한 사이클이 3개뿐이라 곡선을 맞출 수 없다(예전 규칙대로 사이클 1~3을 안정화 구간으로). 보조 판정")
    assert fw["settle"]["tau"] is None
    assert r["few_wait"]["settle"]["kind"] == "waitB" and r["few_wait"]["n1"] is None and r["few_wait"]["text"] == "안정화 판정 중 (15/15 · 뒤 기록 대기)"


@need_chrome
def test_settle_waits_for_cycle_15_record_and_reports_missing():
    """사이클 15 의 기록이 없으면 방전 기록이 15회 있어도 안정화 판정 중. 1~15 안에 기록이 빠진 사이클은 설명에 적는다(로컬 · 클라우드 값이 다를 수 있다)."""
    r = run_js({"a": "brief(calc(15,{noRow:[15]}))", "b": "brief(calc(15))", "c": "brief(calc(17,{noRow:[15]}))", "d": "brief(calc(20,{noRow:[7,8]}))"})
    assert r["a"]["n1"] is None and r["a"]["text"] == "안정화 판정 중 (14/15)" and r["a"]["settle"]["m"] == 14 and r["a"]["settle"]["miss"] == []   # 아직 마지막이라 '빠짐'이 아니다
    assert r["b"]["n1"] == 4 and r["b"]["settle"]["miss"] == []
    c = r["c"]
    assert c["n1"] == 4 and c["settle"]["m"] == 14 and c["settle"]["miss"] == [15]   # 15 가 영영 비어도(기록 누락) 16 이 기록되면 판정한다
    assert c["explain"][1][1].endswith(" 사이클 15 기록 없음 — 로컬과 클라우드 값이 다를 수 있다.")
    assert r["d"]["settle"]["miss"] == [7, 8] and r["d"]["explain"][1][1].endswith(" 사이클 7, 8 기록 없음 — 로컬과 클라우드 값이 다를 수 있다.")


@need_chrome
def test_base_is_fixed_once_settled():
    """안정화 끝이 정해진 뒤에는 어떤 기록이 늘어도(값이 떨어지고 비고가 생기고 충전 Wh/%p 가 뛰어도) 기준 사이클 · 기준값 · 안정화 적합이 그대로다.
    사이클 16~30 을 한 칸씩 늘리며, 그리고 40 · 80 까지 본다. 충전 Wh/%p 는 기준 후보 거르기에 쓰지 않는다 — 정상 범위가 자라는 동안 기준이 흔들리기 때문."""
    v = "(s,c)=>c<=15?4+0.1*s:3.8-0.001*c"
    wh = "c=>c===7||c>30?80:63"   # 7회 한 번 튀고 31부터 계속 벗어난다 — 어느 쪽도 기준을 바꾸지 않는다
    cases = {f"n{n}": f"brief(calc({n},{{v:{v},wh:{wh},note:{{20:'charge_timeout'}},st:{{25:90}}}}))" for n in list(range(15, 31)) + [40, 80]}
    r = run_js(cases)
    a = r["n15"]
    assert a["n1"] == 4 and a["base"] == list(range(4, 14))
    for k, x in r.items():
        assert x["base"] == a["base"] and x["ref"] == a["ref"] and x["refN"] == a["refN"], k
        assert {f: x["settle"][f] for f in ("kind", "n1A", "n1B", "tau", "yinf", "A", "n1raw")} == {f: a["settle"][f] for f in ("kind", "n1A", "n1B", "tau", "yinf", "A", "n1raw")}, k
    assert a["wh"]["avail"] is False and a["wh"]["n"] == 6 and a["explain"][2][1] == "정상 범위 산출 중 (6/8) — 거르기를 통과한 사이클 10~30의 충전 Wh/%p 가 8회 모이면 정상 범위를 정하고 표지를 낸다."
    b = r["n40"]
    assert b["recent"] == list(range(31, 41)) and b["chg"] < -5
    assert b["text"].startswith("기준 ") and "최근 10회 3.76 h" in b["text"] and b["text"].endswith("%") and "−" in b["text"]
    # 최근 범위 툴팁 — 최근 31~40 에는 빠진 사이클이 없다 · 기준 범위(4~13)에도 없다
    assert "최근 범위에서 뺀 사이클: 없음" in b["tip"] and "기준 범위에서 뺀 사이클: 없음" in b["tip"]
    assert b["overlap"] == 0 and "겹침" not in b["text"] and [k for k, _ in b["explain"]] == ["규칙", "안정화", "충전량 이상(IC 재학습 의심)", "고른 사이클", "기준 범위에서 뺀 사이클", "최근 10회", "최근 범위에서 뺀 사이클", "변화율 주의"]
    assert dict(b["explain"])["변화율 주의"] == "최근 10회 중 사이클 32, 33, 34, 35, 36, 37, 38, 39, 40 은 충전량 이상(IC 재학습 의심) — 변화율이 IC 영향을 받았을 수 있다."
    # 31회부터 Wh/%p 가 2회 연속 벗어났다 → 32 부터 표지 · 판정에서 빼지는 않는다(최근 10회에 그대로 들어 있다). 7회 한 번은 표지도 제외도 없다
    assert b["wh"]["avail"] is True and b["wh"]["n"] == 19 and abs(b["wh"]["mean"] - 0.9) < 1e-9 and b["wh"]["flags"] == list(range(32, 41))
    assert b["explain"][2][1].startswith("플러그가 잰 충전 전력량을 잔량 변화폭으로 나눈 값(충전 Wh/%p)이 정상 범위 0.88~0.92(거르기 통과 사이클 10~30 19회의 평균 0.90 에서 흔들림 폭의 2배 안)를 2회 연속 벗어난 사이클: 32~40. ")
    assert b["heads"]["35"].endswith(" · 충전량 이상(IC 재학습 의심) (충전 Wh/%p 이탈 2회 연속)") and "IC 재학습" not in b["heads"]["31"] and "IC 재학습" not in b["heads"]["7"]


@need_chrome
def test_ic_flag_is_marker_only():
    """C. 충전 Wh/%p — 표지와 설명에만 쓴다. 1회 이탈은 표지가 없고 2회 연속부터 그 사이클에 표지. 표본 8개 미만 · 안정화 구간 · 안정화 끝 미확정이면 표지 없음. σ 하한은 평균의 1 %."""
    r = run_js({
        "one": "brief(calc(20,{wh:c=>c===7?80:63}))",                          # 7회 한 번 이탈 → 기준 그대로 · 표지 없음
        "two": "brief(calc(30,{wh:c=>c===25||c===26?80:63}))",                 # 25 · 26 연속 이탈 → 26 에 표지, 25 에는 없음
        "stab": "brief(calc(20,{wh:c=>c<=3?80:63}))",                          # 1~3 연속 이탈이지만 안정화 구간(n₁=4 전) → 표지 없음
        "k7": "brief(calc(16,{wh:c=>63}))",                                    # 표본 7개 → 산출 중
        "k8": "brief(calc(17,{wh:c=>63}))",                                    # 표본 8개 → 정상 범위 · σ 하한 1 %
        "wait": "brief(calc(14,{wh:c=>c>=13?80:63}))",                         # 안정화 끝 미확정 → 표지 없음
        "none": "brief(calc(20))",                                             # charge_wh 가 전혀 없으면 표지를 내지 않는다
        "stop": "[...tbWh([{cycle:'1',charge_wh:'60',min_batt_at_stop:'40'}],[1],1).w.values()]",   # 100 − 40 = 60 → 1.00
    })
    o = r["one"]
    assert o["base"] == list(range(4, 14)) and o["wh"]["flags"] == [] and dict(o["vd"])[7] == "ok" and o["heads"]["7"] == "사이클 7 · 정상 · 기준 사이클"
    t = r["two"]
    assert t["wh"]["flags"] == [26] and dict(t["vd"])[25] == "ok" and dict(t["vd"])[26] == "ok" and t["recent"] == list(range(21, 31)) and t["base"] == list(range(4, 14))
    assert t["heads"]["26"] == "사이클 26 · 정상 · 충전량 이상(IC 재학습 의심) (충전 Wh/%p 이탈 2회 연속)" and t["heads"]["25"] == "사이클 25 · 정상"
    assert "벗어난 사이클: 26. " in t["explain"][2][1]
    assert r["stab"]["wh"]["flags"] == [] and r["stab"]["n1"] == 4
    assert r["k7"]["wh"]["avail"] is False and r["k7"]["wh"]["n"] == 7 and r["k7"]["explain"][2][1].startswith("정상 범위 산출 중 (7/8)")
    k8 = r["k8"]["wh"]
    assert k8["avail"] is True and k8["n"] == 8 and abs(k8["mean"] - 0.9) < 1e-9 and abs(k8["sd"] - 0.009) < 1e-9 and abs(k8["lo"] - 0.882) < 1e-9 and abs(k8["hi"] - 0.918) < 1e-9
    assert r["wait"]["n1"] is None and r["wait"]["wh"]["flags"] == []
    assert r["none"]["wh"]["has"] is False and r["none"]["wh"]["avail"] is False and r["none"]["base"] == list(range(4, 14))
    assert r["stop"] == [1.0]


@need_chrome
def test_cell_base_uses_same_cycles_and_reports_n():
    """셀별 기준은 세트가 고른 사이클들에서 그 셀의 값 평균 — 값이 빠진 사이클은 빼고 평균하되 n 으로 보인다."""
    r = run_js({
        # 셀 2 는 사이클 5 · 6 에 값이 없다(행 없음) · 셀 3 은 사이클 7 에 빈 값. flat(안정화 없음)이라 기준은 1~10
        "m": "brief(calc(15,{flat:true,ser:[1,2,3],v:(s,c)=>s===2&&(c===5||c===6)?undefined:s===3&&c===7?null:(s===1?4:s===2?5+(c===4?1:0):3)}))",
    })
    m = r["m"]
    assert m["n1"] == 1 and m["base"] == list(range(1, 11))
    assert m["refN"] == {"1": 10, "2": 8, "3": 9, "avg": 10}
    assert abs(m["ref"]["2"] - (6 + 5 * 7) / 8) < 1e-12 and m["ref"]["1"] == 4 and m["ref"]["3"] == 3


@need_chrome
def test_verdicts_use_new_base_and_only_filtered_cycles():
    """판정(가안: −5 % 밖 3연속 → 주의, 주의 2회 → 점검)이 새 기준을 쓰고, 거르기를 통과한 사이클만 센다."""
    r = run_js({
        # 안정화 1~3 이 −12 · −5 · −2 %(기본 SD)여도 주의가 나지 않는다(첫 사이클 기준이었다면 기준 자체가 낮았다)
        "stab": "brief(calc(16))",
        # 셀 2 가 16·17·18 에서 −6 % → 18회에 주의, 19·20·21 에서 또 → 21회에 점검. (하락은 적합 창 1~15 뒤에 둔다 — 2셀 중 1셀의 −6 % 는 세트 평균 −3 % 라 창 안에 있으면 n₁ 이 흔들린다)
        "warn": "brief(calc(21,{v:(s,c)=>s===2&&c>=16?3.76:4}))",
        # 17회는 비고(reconfigure)로 빠진다 — 연속을 끊지 않고 건너뛴다: 16 · 18 · 19 → 19회에 주의
        "skip": "brief(calc(19,{v:(s,c)=>s===2&&c>=16&&c!==17?3.76:4,note:{17:'reconfigure'}}))",
    })
    assert r["stab"]["n1"] == 4 and all(c["state"] == "ok" and c["caution"] == 0 for c in r["stab"]["cells"])
    w = {c["s"]: c for c in r["warn"]["cells"]}
    assert r["warn"]["n1"] == 4 and r["warn"]["base"] == list(range(4, 14))
    assert w[2]["state"] == "inspect" and w[2]["caution"] == 1 and w[2]["inspect"] == 1 and w[2]["streak"] == 6
    assert w[1]["state"] == "ok"
    assert dict(r["warn"]["vd"])[18] == "caution" and dict(r["warn"]["vd"])[21] == "inspect"
    s = {c["s"]: c for c in r["skip"]["cells"]}
    assert s[2]["state"] == "caution" and dict(r["skip"]["vd"])[17] == "skip" and dict(r["skip"]["vd"])[19] == "caution"


@need_chrome
def test_text_and_span():
    r = run_js({
        "t": "tbText({ready:true,base:[4,5,6,7,8,9,10,11,12,13],recent:[62,63,64,65,66,67,68,69,70,71]},4.70,4.5826)",
        "gap": "tbSpan([4,6,7,8,9,10,15,16,17,18])",
        "run": "tbSpan([4,5,6,7,8,9,10,11,12,13])",
        "pct": "[tbPct(-2.5),tbPct(0),tbPct(8.94),tbPct(-0.04),tbPct(0.04)]",
        "fix": "[tbFix1(-0.04),tbFix1(-0.06),tbFix1(1.25),tbSign(-1.24),tbSign(0.0)]",   # QA C: '−0.0' 을 쓰지 않는다
        "zone": "[tbZone(null),tbZone(-1),tbZone(-2.5),tbZone(-3),tbZone(-5),tbZone(-6)]",
        "runs": "[tbRuns([69,75,85,86,87]),tbRuns([2,3]),tbRuns([5]),tbRuns([])]",
        "stab": "[tbStab(null),tbStab(1),tbStab(2),tbStab(3),tbStab(6)]",                 # QA #7: "1~1" 이 아니라 "사이클 1"
    })
    assert r["t"] == "기준 4.70 h (사이클 4~13 평균) · 최근 10회 4.58 h · −2.5%"
    assert r["gap"] == "사이클 4~18 중 10회 평균" and r["run"] == "사이클 4~13 평균"
    assert r["pct"] == ["−2.5%", "0.0%", "+8.9%", "0.0%", "0.0%"]
    assert r["fix"] == ["0.0", "−0.1", "1.3", "−1.2", "0.0"]
    assert r["zone"] == ["", "", "", "관찰 구간", "관찰 구간", "이탈 구간 (−5 % 밖)"]
    assert r["runs"] == ["69, 75, 85~87", "2, 3", "5", ""]
    assert r["stab"] == ["", "안정화 구간 없음", "안정화 사이클 1", "안정화 사이클 1~2", "안정화 사이클 1~5"]


@need_chrome
def test_stab_span_wording_in_heads():
    """안정화 구간이 한 사이클뿐이면 '사이클 1', 둘 이상이면 '사이클 1~k' — '1~1' 이라 쓰지 않는다(QA #7)."""
    r = run_js({
        # 적합 2, 보조 판정 2 → 안정화 구간은 사이클 1 하나
        "one": "brief(calc(20,{flat:true,v:(s,c)=>c===1?3.9:4}))",
        "five": "brief(calc(20,{flat:true,v:(s,c)=>5-Math.exp(-c/2)}))",
    })
    o = r["one"]
    assert o["n1"] == 2 and o["heads"]["1"] == "사이클 1 · 판정 제외 · 안정화 구간 (사이클 1)" and "1~1)" not in o["tip"] and "1~1 " not in o["tip"]
    assert r["five"]["heads"]["3"] == "사이클 3 · 판정 제외 · 안정화 구간 (사이클 1~5)"


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
        "warn": "brief(calc(21,{v:(s,c)=>s===2&&c>=16?3.76:4}))",
        "caut": "brief(calc(18,{v:(s,c)=>s===2&&c>=16?3.76:4}))",
    })
    o = r["one"]
    assert {c["s"]: c["state"] for c in o["cells"]} == {1: "ok", 2: "out"}
    assert o["badges"]["2"] == "범위 밖 — 사이클 20 한 회 −7.0%" and o["badges"]["1"] == "정상 — 사이클 20 한 회 0.0%"
    assert abs([c for c in o["cells"] if c["s"] == 2][0]["chg"] - (-0.7)) < 1e-9
    assert r["caut"]["badges"]["2"] == "주의 — 사이클 18까지 3사이클 연속 −5 % 밖"
    assert r["warn"]["badges"]["2"] == "점검 — 사이클 21에서 두 번째 주의(3사이클 연속 −5 % 밖)"


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
    assert "box.innerHTML=tbExplain(P).map(" in _between("function renderTrendBase", "let HMsig")   # <gauge> 의 IC 줄은 tbExplain 이 P.ic 로 '규칙' 바로 아래 끼운다
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


def test_wiring_for_auto_settling():
    """안정화 자동 판정(PR #34)의 배선 — 순수 함수가 화면 · 문서에 실제로 닿는 자리를 못 박는다."""
    assert "const TB={n:10,minStart:98,fitN:15,tau:[0.5,8.0,0.1],within:0.01,minPts:4,legacySkip:3,whLo:10,whHi:30,whMin:8,sig:2};" in SRC and "TB.skip" not in SRC
    heat = _between("function renderHeat", "function defaultTrendCell")
    # 히트맵: 안정화 구간은 n₁ 로(하드코딩 없음), 충전량 이상(IC 재학습 의심)은 머리 원에만 점선 테두리(모양) + 툴팁 글 — 셀 칸에는 그리지 않는다(QA #5)
    assert "M.cyc.filter(c=>c<M.P.n1)" in heat and "ic=M.P.wh.flags" in heat and "${ic.has(r.c)?' ic':''}" in heat
    assert "h-ic" not in SRC and "tbIc(M,cy)" not in heat and 'title="${esc(tbHead(M,r)+ggHeadMark(M.G,r.c))}"' in heat
    assert ".hm .vc.ic span{outline:1.5px dashed" in SRC
    view = _between("V.trend={", "// ───── 4.")
    assert "outline:1.5px dashed var(--ink-2);outline-offset:-1.5px\"></i>충전량 이상(IC 재학습 의심) · 머리 점선 원 · 표지만" in view
    # 판정 기록의 대기 문구 · 통계 카드의 대기 문구는 tbText 하나에서(안정화 판정 중 / 기준 산출 중) · 요약 줄은 tbStab
    assert "wait=tbText(P)" in _between("function renderTrendStats", "function renderTrendBase")
    assert "사이클 ${TB.fitN}까지 기록이 모이고 보조 판정에 필요한 뒤 기록이 몇 회 더 쌓여 안정화 구간이 정해진 뒤" in heat and "사이클 ${M.P.n1} 이후" in heat
    assert "`· ${tbStab(P.n1)}${P.src==='ic'?' (IC 측정)':''} · 고른 사이클" in _between("function renderTrendBase", "let HMsig") and "whEx" not in SRC
    # 절차 · 기준 화면 · README 의 규칙 문장 — 충전 Wh/%p 는 표지에만
    assert "기준은 안정화 구간을 뺀 뒤, 방전 시작이 98 % 이상이고 비고가 없는 사이클을 앞에서부터 10회 골라 평균한 값이다." in SRC
    assert "사이클 15까지 기록이 모이면 기준이 정해지고" not in SRC and "기준은 한번 정해지면 다시 바뀌지 않는다. 사이클 15까지 기록이 모이고, 보조 판정에 필요한 뒤 기록이 몇 회 더 쌓이면 정해진다." in SRC
    # 판정 대기 중 셀 카드는 "최근 0회 평균 —h" 대신 "기준 확정 전"(QA M4)
    assert "<span>최근 ${P.ready?c.nowN:TB.n}회 평균</span>${P.ready?" in SRC and "기준 확정 전</b>" in SRC
    assert "시정수" not in SRC.replace("// 시정수", "") and "사이클 1~3을 뺀 뒤" not in SRC and "'충전량 이상(IC 재학습 의심)' 표지만 붙이고 판정에서 빼지는 않는다" in SRC
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "1~3 안정화 구간" not in readme and "사이클 1~3 을 빼고" not in readme
    assert "A. 안정화 끝(주 규칙, 지수 수렴 적합)" in readme and "B. 보조 판정(정착)" in readme and "C. 충전 Wh/%p — '충전량 이상(IC 재학습 의심)' 표지" in readme
    assert "표지와 설명에만 쓰고 기준 · 판정에는 쓰지 않는다" in readme and "뒤 기록 대기" in readme and "정상 범위 산출 중 (k/8)" in readme and "로컬과 클라우드 값이 다를 수 있다" in readme


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
    """세트 1 · 2 의 실기록에서 안정화 끝 · 보조 판정 · 충전 Wh/%p 정상 범위 · 고른 사이클이 따로 계산한 값과 같다.
    기대값은 2026-11-02 세션이 파이썬으로 실데이터를 재서 낸 것 — 세트 1 τ≈2.7 · y∞≈3.813 · 5.9 → 6, 세트 2 τ≈1.0 · y∞≈4.931 · 3.3 → 4."""
    s1, s2 = _load(REAL), _load(REAL / "set2")
    pre = f"const S1={json.dumps({'C': s1[0], 'D': s1[1]}, ensure_ascii=False)};const S2={json.dumps({'C': s2[0], 'D': s2[1]}, ensure_ascii=False)};"
    r = run_js({"y4": "brief(trendCalc(S1.D,S1.C))", "x6": "brief(trendCalc(S2.D,S2.C))"}, pre)
    y4, x6 = r["y4"], r["x6"]
    # 세트 1(Y4): 안정화 끝 6 (보조 판정 4 — 2회 차이, 불일치 아님). 5 시작 95 % · 11 · 14 restart_in_charge · 12 · 13 reconfigure 는 거르기에서 빠진다. 충전 Wh/%p 는 기준에 안 쓴다
    st = y4["settle"]
    assert st["kind"] == "fit" and abs(st["tau"] - 2.7) < 0.1 + 1e-9 and abs(st["yinf"] - 3.813) < 0.01 and abs(st["n1raw"] - 5.9) < 0.1 and y4["n1"] == 6
    assert st["n1B"] == 4 and st["mismatch"] is False and st["miss"] == []
    assert y4["wh"]["avail"] is True and y4["wh"]["n"] == 15 and abs(y4["wh"]["mean"] - 0.890) < 0.001 and abs(y4["wh"]["sd"] - 0.031) < 0.001
    assert y4["base"] == [6, 7, 8, 9, 10, 15, 16, 17, 18, 19]
    assert [x for x in y4["ex"] if x["c"] <= 19] == [
        {"c": 5, "why": "방전 시작 95 % (98 % 미만)"}, {"c": 11, "why": "충전 중 엔진 교체(재시작)"}, {"c": 12, "why": "세트 구성 변경으로 재시작"},
        {"c": 13, "why": "세트 구성 변경으로 재시작"}, {"c": 14, "why": "충전 중 엔진 교체(재시작)"}]
    assert y4["explain"][1][1] == ("평균 작동시간이 사이클 6부터 최종값(3.81 h)의 1 % 안에 들어와, 그때부터를 안정화 뒤로 본다. 지수 곡선 적합 결과이며, 변화가 사이클 2.7회마다 약 3분의 1로 줄어드는 곡선이다. "
                                   "보조 판정(값이 뒤 10회 평균 안에 3회 연속 드는 첫 사이클)은 사이클 4로, 2사이클 차이다.")
    # 시험이 계속 돌아 실기록이 늘어나므로 표지는 2026-11-02 에 잰 범위(사이클 87까지)만 고정해 본다
    assert y4["explain"][2][1].startswith("플러그가 잰 충전 전력량을 잔량 변화폭으로 나눈 값(충전 Wh/%p)이 정상 범위 0.83~0.95(거르기 통과 사이클 10~30 15회의 평균 0.89 에서 흔들림 폭의 2배 안)를 2회 연속 벗어난 사이클: 69, 75, 85~87")
    assert [c for c in y4["wh"]["flags"] if c <= 87] == [69, 75, 85, 86, 87]   # 2 는 안정화 구간이라 표지 없음
    assert y4["heads"]["5"] == "사이클 5 · 판정 제외 · 안정화 구간 (사이클 1~5)" and y4["heads"]["7"] == "사이클 7 · 정상 · 기준 사이클" and "IC 재학습" not in y4["heads"]["2"]
    ic_recent = [c for c in y4["recent"] if c in y4["wh"]["flags"]]   # 최근 10회 안의 IC 표지 → 변화율 주의 줄
    assert (dict(y4["explain"]).get("변화율 주의") == f"최근 10회 중 사이클 {', '.join(map(str, ic_recent))} 은 충전량 이상(IC 재학습 의심) — 변화율이 IC 영향을 받았을 수 있다.") if ic_recent else "변화율 주의" not in dict(y4["explain"])
    assert y4["heads"]["86"].endswith(" · 충전량 이상(IC 재학습 의심) (충전 Wh/%p 이탈 2회 연속)")
    # 세트 2(X6): 안정화 끝 4 (보조 판정 3). 8 · 10 restart_in_charge 제외 — 옛 규칙(1~3 제외)과 같은 기준
    st = x6["settle"]
    assert st["kind"] == "fit" and abs(st["tau"] - 1.0) < 0.1 + 1e-9 and abs(st["yinf"] - 4.931) < 0.01 and abs(st["n1raw"] - 3.3) < 0.1 and x6["n1"] == 4
    assert st["n1B"] == 3 and st["mismatch"] is False
    assert abs(x6["wh"]["mean"] - 0.643) < 0.001 and abs(x6["wh"]["sd"] - 0.032) < 0.001 and [c for c in x6["wh"]["flags"] if c <= 69] == [66, 69]
    assert x6["base"] == [4, 5, 6, 7, 9, 11, 12, 13, 14, 15]
    assert [x for x in x6["ex"] if x["c"] <= 15] == [{"c": 8, "why": "충전 중 엔진 교체(재시작)"}, {"c": 10, "why": "충전 중 엔진 교체(재시작)"}]
    for got, (cyc, dis) in ((y4, s1), (x6, s2)):
        assert got["ready"] is True and len(got["recent"]) == 10
        avg, cells = _py_base(dis, got["base"])
        assert abs(got["ref"]["avg"] - avg) < 1e-9
        assert all(abs(got["ref"][str(s)] - v) < 1e-9 for s, v in cells.items())
