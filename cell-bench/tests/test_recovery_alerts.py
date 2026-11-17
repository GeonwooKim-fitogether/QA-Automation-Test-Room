"""규칙 E — 알림 (자동 복구 기획 5절 · 커밋 7). 장비 없이 돈다.

빨강 반복은 15분 · 1시간 · 그 뒤 4시간(사고 4 의 66건 → 6건). 사람을 부르는 글은 '자동으로 한 일' 다음 '사람이 할 일'.
Dock 본체 버튼은 세트가 모두 꺼졌을 때만 — 옛 글 'Dock 버튼 2초 이상'은 일부만 꺼진 세트에서 켜진 셀을 끈다.
결과판(board/index.html)은 새 이상 종류를 한국어 이름과 색으로 보인다 — 헤드리스 Chrome 으로 그 표를 실제로 돌려 본다.
"""
import html
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cellbench import supervisor as sup
from cellbench.alert import RED_REPEAT_S, Alerter, red_gap
from cellbench.config import Config
from cellbench.record import ALERT_KINDS, EVENT_LIGHT
from test_board_gauge import CHROME, need_chrome
from test_supervisor import T, eng

ROOT = Path(__file__).resolve().parents[1]
BOARD = (ROOT / "board" / "index.html").read_text(encoding="utf-8")
NEW_KINDS = ("dock_stall", "dock_stall_ok", "dock_stall_fail", "cells_off", "dock_wake", "cells_woke", "set_reset", "set_reset_done")


# ---------- 빨강 반복 간격 ----------

def test_red_gap_schedule():
    assert RED_REPEAT_S == (900.0, 3600.0, 14400.0)
    assert [red_gap(n) for n in (1, 2, 3, 4, 9)] == [900.0, 3600.0, 14400.0, 14400.0, 14400.0]


def test_incident4_would_send_6_not_66():
    """사고 4 — 같은 빨강이 16시간 20분 이어졌다(data/alert_state.json sent_n 66). 새 간격이면 6건."""
    sent = []
    a = Alerter(sent.append, None, None, bench="hq-bench-1")
    for m in range(0, 16 * 60 + 20 + 1):
        a.update({"program": ("red", "엔진이 Dock 충전 멈춤으로 스스로 멈춤")}, T + 60 * m)
    assert len(sent) == 6


# ---------- 사람 호출 글 — 두 칸 ----------

def _two_columns(text: str) -> bool:
    i, j = text.find("자동으로 한 일"), text.find("사람이 할 일")
    return 0 <= i < j


def test_supervisor_human_calls_say_what_was_done_then_what_to_do():
    cfg = Config(auto_continue=False)
    stalled = eng(exit="failsafe", why="dock_stall", plug_on=True, ended=T - 30, target_last_cycle=10)
    p = sup.restart_plan("paused", stalled, None, 3, [], T, cfg, resumes=[T - 20000, T - 9000, T - 3000])
    assert p.call_human and _two_columns(p.why) and "플러그가 제 Dock 에 꽂혔는지" in p.why
    p = sup.restart_plan("paused", eng(exit="failsafe", why="failures"), None, 3, [], T, cfg)
    assert p.call_human and _two_columns(p.why)
    p = sup.restart_plan("dead", eng(exit=None), None, 1, [T - 300, T - 200, T - 100], T, cfg)
    assert p.call_human and _two_columns(p.why)
    for text in (sup.PLUG_STUCK, sup.GUARD_STUCK, sup.GUARD_CAPPED, sup.NO_CELLS_HUMAN):
        assert _two_columns(text), text
    p = sup.restart_plan("paused", stalled, None, 3, [], T + 600, cfg)
    assert "자동으로 한 일: 자동 재개 1/3" in p.why and not p.call_human


def test_no_harmful_dock_button_advice_left_in_engine_supervisor_board():
    """'Dock 버튼을 2초 이상'은 켜진 셀을 끈다(Dock 본체 버튼은 3초를 누르고 켜진 셀은 2초에 꺼진다 — recovery-plan 4-3).
    health.py 의 같은 글은 열린 PR #70 이 머지된 뒤 고친다(이 커밋에서 health.py 는 고치지 않는다)."""
    for f in ("cellbench/cycle.py", "cellbench/supervisor.py", "board/index.html"):
        src = (ROOT / f).read_text(encoding="utf-8")
        assert "2초 이상" not in src and "Dock 버튼 2초" not in src, f


# ---------- 결과판 ----------

def test_board_names_every_recovery_kind_and_colors_like_the_recorder():
    ev_red = BOARD.split("const EV_RED")[1].split("const EV_KO")[0]
    ev_ko = BOARD.split("const EV_KO")[1].split("const EV_IC")[0]
    for k in NEW_KINDS:
        assert re.search(rf"\b{k}:'[^']+'", ev_ko), k
        assert (f"'{k}'" in ev_red) == (EVENT_LIGHT[k] == "red"), k
    assert "RESET:'리셋'" in BOARD
    assert (ROOT / "deploy" / "board" / "index.html").read_text(encoding="utf-8") == BOARD   # 클라우드판도 같은 글


@need_chrome
def test_board_maps_run_in_a_browser_and_the_page_has_no_script_error():
    """표 넷(PHK · EV_RED · EV_KO · EV_IC)을 떼어 브라우저에서 돌리고, 결과판 전체를 열어 스크립트 오류가 없는지 본다."""
    start = BOARD.index("const PHK=")
    stop = BOARD.index("};", BOARD.index("const EV_IC=")) + 2
    maps = BOARD[start:stop]
    probe = ("<pre id=o></pre><script>window.onerror=(m,u,l)=>{document.getElementById('o').textContent='@@'+JSON.stringify({ERR:m+' @'+l})+'@@'};"
             "</script><script>" + maps +
             ";document.getElementById('o').textContent='@@'+JSON.stringify({ko:Object.fromEntries(" + json.dumps(list(NEW_KINDS)) +
             ".map(k=>[k,EV_KO[k]||null])),red:" + json.dumps(list(NEW_KINDS)) + ".filter(k=>EV_RED.has(k)),reset:PHK.RESET})+'@@';</script>")
    whole = ("<script>window.__err=[];window.addEventListener('error',e=>window.__err.push(String(e.message)+' @'+e.lineno));"
             "setTimeout(()=>{const d=document.createElement('pre');d.id='zz';d.textContent='@@'+JSON.stringify(window.__err)+'@@';"
             "document.body.appendChild(d)},1500);</script>" + BOARD)
    out = {}
    for name, page in (("probe", probe), ("whole", whole)):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "t.html"
            f.write_text(page, encoding="utf-8")
            dom = subprocess.run([CHROME, "--headless", "--disable-gpu", "--no-sandbox", f"--user-data-dir={d}/p",
                                  "--virtual-time-budget=3000", "--dump-dom", f.as_uri()],
                                 capture_output=True, timeout=120).stdout.decode("utf-8", "replace")
        pid = "o" if name == "probe" else "zz"
        out[name] = json.loads(html.unescape(re.search(rf'id="{pid}">@@(.*?)@@<', dom, re.S).group(1)))
    assert "ERR" not in out["probe"], out["probe"]
    assert out["probe"]["ko"] == {"dock_stall": "Dock 충전 멈춤 · 끊었다 켬", "dock_stall_ok": "Dock 충전 회복",
                                  "dock_stall_fail": "Dock 충전 멈춤 · 모든 플러그 켬", "cells_off": "세트 셀 모두 꺼짐",
                                  "dock_wake": "Dock 켜기 명령", "cells_woke": "셀 다시 들림",
                                  "set_reset": "일부 꺼짐 · 리셋 시작", "set_reset_done": "리셋 끝"}
    assert out["probe"]["red"] == ["dock_stall_fail"] and out["probe"]["reset"] == "리셋"
    assert not [e for e in out["whole"] if "SyntaxError" in e or "ReferenceError" in e], out["whole"]


def test_new_kinds_reach_slack_except_the_command_log():
    quiet = {"dock_wake", "dock_stall", "dock_stall_ok"}                    # 사람이 할 일이 없는 것 — 기록 · 결과판에만
    assert set(NEW_KINDS) - quiet <= ALERT_KINDS and not quiet & ALERT_KINDS
