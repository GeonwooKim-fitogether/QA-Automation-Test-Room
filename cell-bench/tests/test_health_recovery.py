"""신호등이 자동 복구를 사람 호출로 읽지 않게 (자동 복구 기획 규칙 E · 커밋 8). 장비 없이 돈다.

엔진이 셀이 모두 꺼졌다고 보고 충전 · Dock 켜기를 기다리는 동안과 일부 꺼짐 리셋 동안에는 now.json metrics.auto_recovery 가 있고(그때만),
신호등은 '셀 수신 전부 끊김' · '셀 잔량 0 %' 를 빨강 대신 노랑 "자동 복구 중 — …" 으로 낸다. 엔진이 사람을 부르면 그 칸이 사라져 다시 빨강이다.
사람 할 일 글에서 'Dock 버튼 2초'(켜진 셀을 끈다)를 뺐다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_health as th
from cellbench import health as H
from cellbench.config import HEALTH
from cellbench.cycle import CycleState

AUTO = {"what": "세트 셀이 모두 꺼짐 — 충전한 뒤 Dock 켜기 명령을 보낸다", "since": th.T - 600}


def issue(h, key, word):
    return next(i for i in th.lane(h, key)["issues"] if word in i["reason"])


def test_all_silent_is_red_without_auto_recovery_and_never_says_two_seconds():
    h = th.run(now=th.now_(phase="RECOVER", cells=th.cells(age=900)))
    i = issue(h, "live", "전부 끊김")
    assert i["light"] == "red" and "2초" not in i["human"]
    assert "모두 꺼졌으면 Dock 버튼 한 번 · 일부만 꺼졌으면 그 셀의 전원 버튼" in i["human"]


def test_all_silent_is_yellow_while_the_engine_recovers_by_itself():
    h = th.run(now=th.now_(phase="RECOVER", cells=th.cells(age=900), metrics=th.metrics(auto_recovery=AUTO)))
    assert th.L(h, "live") == "yellow"
    i = issue(h, "live", "자동 복구 중")
    assert i["reason"] == f"자동 복구 중 — {AUTO['what']}" and i["human"].startswith("없음")
    assert not [x for x in th.lane(h, "live")["issues"] if x["light"] == "red"]


def test_reset_phase_is_yellow_even_without_the_metric_and_names_the_phase():
    h = th.run(now=th.now_(phase="RESET", cells=th.cells(age=900)))
    assert th.L(h, "live") == "yellow" and "리셋" in issue(h, "live", "자동 복구 중")["reason"]
    part = th.cells()
    for c in part[:5]:
        c["age"] = 900
    h = th.run(now=th.now_(phase="RESET", cells=part))
    i = issue(h, "live", "자동 복구 중")                                   # 하나씩 꺼지는 것이 목적 — '끊긴 셀의 전원 확인'을 하지 않는다
    assert i["light"] == "yellow" and len(i["cells"]) == 5
    assert H.PHASE_KO["RESET"] == "리셋" and th.lane(h, "program")["value"].startswith("리셋")


def test_zero_percent_cells_are_yellow_during_recovery_and_red_otherwise():
    h = th.run(now=th.now_(phase="RESET", cells=th.cells(battery=0)))
    i = issue(h, "dock", "자동 복구 중")
    assert i["light"] == "yellow" and len(i["cells"]) == 24 and th.L(h, "dock") == "yellow"
    h = th.run(now=th.now_(phase="DISCHARGE", cells=th.cells(battery=0)))
    i = issue(h, "dock", "셀 잔량 0%")
    assert i["light"] == "red" and "2초" not in i["human"] and "Dock 버튼은 켜진 셀을 끈다" in i["human"]


def test_reset_dwell_expectation_and_human_text():
    assert H.RESET_EXPECT_H == 5.0 and "RESET" not in HEALTH["dwell_expect_h"]     # Config 기본값(run.log 설정 줄)은 그대로(I7)
    assert th.L(th.run(now=th.now_(phase="RESET", since=7.4 * 3600, cells=th.cells(age=900))), "program") == "green"
    h = th.run(now=th.now_(phase="RESET", since=7.6 * 3600, cells=th.cells(age=900)))
    assert th.L(h, "program") == "yellow"
    assert "7시간에 멈추고 부르므로" in next(i for i in th.lane(h, "program")["issues"] if "체류" in i["reason"])["human"]


# ---------- 엔진 — 칸은 자동 복구가 진행 중일 때만 생기고, 사람을 부르면 사라진다 ----------

def _watch_now(r):
    seen = []
    orig = r.rec.now

    def now(payload):
        m = payload.get("metrics") or {}
        seen.append((payload.get("phase"), (m.get("auto_recovery") or {}).get("what")))
        return orig(payload)
    r.rec.now = now
    return seen


def test_engine_metric_while_waiting_for_dock_wake_then_gone(tmp_path, monkeypatch):
    import test_cell_wake as tw
    w, r, data, _ = tw.wake_world(monkeypatch, tmp_path)
    seen = _watch_now(r)
    r._recover(CycleState(cycle=3))
    whats = [w_ for ph, w_ in seen if ph == "RECOVER"]
    assert whats[0] is None                                               # 5분 안 — 아직 모두 꺼짐이 아니다
    assert "세트 셀이 모두 꺼짐 — 충전한 뒤 Dock 켜기 명령을 보낸다" in whats
    assert "세트 셀이 모두 꺼짐 — Dock 켜기 명령 뒤 셀이 켜지기를 기다린다" in whats
    assert all(w_ is None for ph, w_ in seen if ph == "PRECHARGE")         # 끝나면 칸이 사라진다


def test_engine_metric_disappears_once_a_human_is_called(tmp_path, monkeypatch):
    import test_cell_wake as tw
    w, r, data, _ = tw.wake_world(monkeypatch, tmp_path, switch=False)
    w.at(45 * 60, lambda: w.off.clear())
    seen = _watch_now(r)
    r._recover(CycleState(cycle=3))
    rec = [w_ for ph, w_ in seen if ph == "RECOVER"]
    i = rec.index("세트 셀이 모두 꺼짐 — 충전 유지 중 · 30분 안에 돌아오지 않으면 사람을 부른다")
    j = next(k for k in range(i, len(rec)) if rec[k] is None)
    assert j > i and all(x is None for x in rec[j:])                      # 30분 사람 호출 뒤로는 빨강(칸 없음)


def test_engine_metric_during_reset(tmp_path, monkeypatch):
    import test_set_reset as ts
    w, r, data, _ = ts.reset_world(monkeypatch, tmp_path)
    seen = _watch_now(r)
    assert r.run(1) == "done"
    assert {w_ for ph, w_ in seen if ph == "RESET"} == {"일부 꺼짐 리셋 — 셀이 모두 꺼질 때까지 방전한 뒤 Dock 으로 켠다"}
    assert all(w_ is None for ph, w_ in seen if ph in ("DISCHARGE", "CHARGE", "PRECHARGE"))


# ---------- 결과판 — 바꾼 차선이 실제 화면에서 깨지지 않는다 (헤드리스 Chrome) ----------

def _board_text(now: dict) -> dict:
    """결과판 전체를 열고 /api 를 가짜로 답해(health = 이 판정, now = 이 상태) 4초 뒤 화면 글과 스크립트 오류를 돌려준다."""
    import html as _html
    import json
    import re
    import subprocess
    import tempfile
    from test_board_gauge import CHROME
    board = (Path(__file__).resolve().parents[1] / "board" / "index.html").read_text(encoding="utf-8")
    shim = ("<script>window.__err=[];addEventListener('error',e=>__err.push(String(e.message)+' @'+e.lineno));"
            "const __H=" + json.dumps(th.run(now=now), ensure_ascii=False) + ",__N=" + json.dumps(now, ensure_ascii=False) + ";"
            "window.fetch=async(u)=>{u=String(u);const j=u.startsWith('/api/health')?__H:u.startsWith('/api/now')?__N:"
            "(u.startsWith('/api/events')||u.startsWith('/api/cycles')||u.startsWith('/api/samples'))?[]:{};"
            "return {ok:true,status:200,json:async()=>j,text:async()=>JSON.stringify(j)}};"
            "setTimeout(()=>{const d=document.createElement('pre');d.id='zz';d.textContent='@@'+JSON.stringify({err:__err,"
            "body:document.body.innerText})+'@@';document.body.appendChild(d)},4000);</script>")
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "t.html"
        f.write_text(shim + board, encoding="utf-8")
        dom = subprocess.run([CHROME, "--headless", "--disable-gpu", "--no-sandbox", f"--user-data-dir={d}/p",
                              "--virtual-time-budget=8000", "--dump-dom", f.as_uri()], capture_output=True, timeout=120).stdout.decode("utf-8", "replace")
    return json.loads(_html.unescape(re.search(r'id="zz">@@(.*?)@@<', dom, re.S).group(1)))


@__import__("test_board_gauge").need_chrome
def test_board_shows_the_yellow_auto_recovery_lane_and_the_reset_phase():
    r = _board_text(th.now_(phase="RESET", cells=th.cells(age=900, battery=0)))
    assert r["err"] == []
    assert "셀 수신: 자동 복구 중 — 일부 꺼짐 리셋" in r["body"] and "셀 수신이 전부 끊김" not in r["body"]
    assert "경고 · 리셋" in r["body"]
    r = _board_text(th.now_(phase="RECOVER", cells=th.cells(age=900)))
    assert r["err"] == [] and "셀 수신이 전부 끊김" in r["body"] and "자동 복구 중" not in r["body"]
