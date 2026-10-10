"""KimQA D-1 ~ D-11 — 결과판(board/index.html)의 세트 화면 고침이 그대로 있는지 원문으로 못 박는다.

화면 동작 자체는 헤드리스 Chrome(Playwright)으로 눌러 보며 확인했다 — 그 검사는 이 저장소의 CI 에 브라우저가 없어 pytest 밖에 있다.
여기서는 그 동작을 떠받치는 줄이 빠지거나 되돌아가지 않았는지만 본다(되돌리면 같은 결함이 조용히 돌아온다).
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "board" / "index.html").read_text(encoding="utf-8")
DEPLOY = ROOT / "deploy" / "board" / "index.html"


def test_d1_first_refresh_asks_for_the_set_and_waits():
    assert "return !HL?k:runningIds().includes(k)?k:1;" in SRC              # 신호등 전에는 화면이 원하는 세트
    assert "if(curSet()!==cs){cs=curSet();" in SRC                         # 신호등을 받고 다른 세트면 다시 받는다
    assert "S={set:cs," in SRC and "const setReady=()=>" in SRC
    assert "if(!setReady())return `보낼 수 없음 — 세트 ${U.set} 값을 불러오는 중`" in SRC
    assert "const nowFor=k=>+k===S.set?S.now:" in SRC


def test_d2_stop_all_pending_locks_set_commands():
    assert "stop_all_pending" in SRC and "전체 정지 대기 중" in SRC
    assert "(!!full&&!all)" in SRC


def test_d3_pair_actions_confirm_first_and_can_be_cancelled():
    assert "function pairWhat(k,mode)" in SRC and "준비 방전" in SRC and "약 40분" in SRC
    assert "reopen(()=>{IS.pc=m;" in SRC and "'요청 취소':'검증 멈춤'" in SRC


def test_d4_gates_and_run_lock_are_shown():
    assert "운전 관문" in SRC and "v.gates" in SRC and "운전 켜기 잠김" in SRC


def test_d5_set_stop_wording_and_pending_name():
    assert "만 멈춘다 — 플러그" in SRC and "다른 세트는 그대로 돈다" in SRC and "다시 돌리기:" in SRC
    assert "`세트 ${U.set} 정지`" in SRC


def test_d6_d7_d9_multi_views_follow_each_set():
    assert "l.head||headOf(l.value)" in SRC                                   # 점검 — 가장 나쁜 세트
    assert "EVBY[sid]" in SRC and "API.events(k)" in SRC                     # 최근 활동 — 모든 운전 세트
    assert "const cyc=nowFor(s.id).cycle;" in SRC                            # 왼쪽 목록 — 세트별 사이클


def test_d8_running_set_screen_has_run_off_and_pair():
    assert "+U.set>1&&!API.cloud?`<section class=\"card\" aria-label=\"짝 · 운전\">" in SRC
    assert "function runOffWhat(k,running)" in SRC and "등록 해제 잠김" in SRC


def test_d11_no_internal_words_and_locked_buttons_look_locked():
    assert "보정(U2)" not in SRC and "esc(req.action)" not in SRC
    assert ".btn.primary:disabled,.btn.danger:disabled{" in SRC and "border-style:dashed" in SRC
    assert "const plain=" in SRC


def test_deploy_copy_matches_board():
    """클라우드 결과판 배포 폴더는 tools/build_cloud_board.py 로 다시 만든다 — 결과판을 고치고 빠뜨리지 않게."""
    if not DEPLOY.exists():
        return
    dep = DEPLOY.read_text(encoding="utf-8")
    body = lambda s: s[s.index("<script>\n(function(){"):]
    assert body(dep) == body(SRC)
