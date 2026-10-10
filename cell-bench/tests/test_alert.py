"""신호등 알림기 — 노랑 1건/1시간, 빨강 즉시 + 확인까지 15분마다, 복구 1건, 시작 시험 알림.

실제 Slack 에는 닿지 않는다: 보내는 함수는 목록에 쌓는 가짜이고, SlackSender 는 hook="" 또는 가짜 post 로만 만든다
(자격 증명 관리자를 읽는 경로는 alert._webhook 을 바꿔 끼워 본다).
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cellbench import alert
from cellbench.alert import Alerter, SlackSender, write_ack

T = 1_800_000_000.0


def mk(tmp_path=None, sent=None, **kw):
    sent = [] if sent is None else sent
    state = tmp_path / "alert_state.json" if tmp_path else None
    ack = tmp_path / "alert_ack.json" if tmp_path else None
    return Alerter(sent.append, state, ack, bench="hq-bench-1", **kw), sent


def test_message_names_the_light_in_words():
    a, sent = mk()
    a.update({"engine": ("red", "엔진이 연속 실패로 멈춤")}, T)
    a.update({"board": ("yellow", "결과판 서버가 응답하지 않음")}, T)
    assert sent == ["[셀 시험대 hq-bench-1] 조치 · 엔진 · 엔진이 연속 실패로 멈춤",
                    "[셀 시험대 hq-bench-1] 준비 · 결과판 · 결과판 서버가 응답하지 않음"]


def test_green_and_first_sight_of_green_send_nothing():
    a, sent = mk()
    assert a.update({"engine": ("green", "엔진 정상"), "net": ("green", "")}, T) == []
    assert sent == []


def test_yellow_once_per_hour_for_same_cause():
    a, sent = mk()
    a.update({"board": ("yellow", "다운")}, T)
    a.update({"board": ("yellow", "다운")}, T + 60)               # 계속 노랑 — 다시 안 보냄
    a.update({"board": ("green", "")}, T + 120)                   # 노랑 → 초록은 조용히
    a.update({"board": ("yellow", "다운")}, T + 600)              # 1시간 안에 같은 원인 — 안 보냄
    assert len(sent) == 1
    a.update({"board": ("green", "")}, T + 3000)
    a.update({"board": ("yellow", "다운")}, T + 3601)             # 1시간 지남 — 다시 보냄
    assert len(sent) == 2
    a.update({"board": ("yellow", "띄우지 못함")}, T + 3700)       # 원인이 바뀜 — 바로 보냄
    assert len(sent) == 3 and "띄우지 못함" in sent[-1]


def test_red_repeats_every_15_minutes_until_acked(tmp_path):
    a, sent = mk(tmp_path)
    a.update({"engine": ("red", "멈춤")}, T)
    a.update({"engine": ("red", "멈춤")}, T + 899)
    assert len(sent) == 1
    a.update({"engine": ("red", "멈춤")}, T + 900)
    assert len(sent) == 2 and "조치 (계속 · 15분째 · 확인 전까지 15분마다)" in sent[-1]
    write_ack(tmp_path / "alert_ack.json", "engine", T - 10)      # 이 빨강이 시작되기 전의 확인 — 소용없다
    a.update({"engine": ("red", "멈춤")}, T + 1800)
    assert len(sent) == 3
    write_ack(tmp_path / "alert_ack.json", "engine", T + 1810)    # 사람이 확인
    for i in range(1, 6):
        a.update({"engine": ("red", "멈춤")}, T + 1800 + 900 * i)
    assert len(sent) == 3
    assert a.snapshot()["engine"] == {"light": "red", "since": T, "reason": "멈춤", "acked": True}


def test_red_cause_change_is_a_new_incident_that_needs_a_new_ack(tmp_path):
    a, sent = mk(tmp_path)
    a.update({"engine": ("red", "멈춤")}, T)
    write_ack(tmp_path / "alert_ack.json", "engine", T + 5)
    a.update({"engine": ("red", "1시간에 3번 되살렸는데 또 멈춤")}, T + 60)
    assert len(sent) == 2 and a.snapshot()["engine"]["acked"] is False
    a.update({"engine": ("red", "1시간에 3번 되살렸는데 또 멈춤")}, T + 960)
    assert len(sent) == 3                                          # 새 사건은 확인 전까지 다시 반복


def test_recovery_sends_one_message(tmp_path):
    a, sent = mk(tmp_path)
    a.update({"plug_on": ("red", "플러그 무응답")}, T)
    a.update({"plug_on": ("green", "이상 없음")}, T + 1380)
    assert len(sent) == 2
    assert sent[-1] == "[셀 시험대 hq-bench-1] 복구 · 플러그 · 지금 정상 (23분 만에) · 이전: 플러그 무응답"
    a.update({"plug_on": ("green", "이상 없음")}, T + 2400)
    assert len(sent) == 2


def test_red_to_yellow_is_one_recovery_message_not_two():
    a, sent = mk()
    a.update({"engine": ("red", "띄우지 못함")}, T)
    a.update({"engine": ("yellow", "되살림")}, T + 60)
    a.update({"engine": ("yellow", "되살림")}, T + 120)
    assert len(sent) == 2 and sent[-1].startswith("[셀 시험대 hq-bench-1] 복구 · 엔진 · 지금 준비: 되살림")


def test_unknown_is_handled_like_red_and_unknown_names_become_unknown():
    a, sent = mk()
    a.update({"cloud": ("unknown", "5분 넘게 소식 없음")}, T)
    a.update({"cloud": ("unknown", "5분 넘게 소식 없음")}, T + 900)
    a.update({"x": ("purple", "모르는 불")}, T)
    assert sent[0].startswith("[셀 시험대 hq-bench-1] 미확인 · 클라우드·알림 ·")   # 차선 키는 차선 이름으로 and "미확인 (계속" in sent[1]
    assert a.snapshot()["x"]["light"] == "unknown"


def test_keys_not_passed_are_left_alone():
    a, sent = mk()
    a.update({"engine": ("red", "멈춤")}, T)
    a.update({"board": ("green", "")}, T + 900)                    # engine 은 이번에 오지 않음 — 반복도 하지 않음
    assert len(sent) == 1 and a.snapshot()["engine"]["light"] == "red"


def test_state_survives_restart_without_duplicates(tmp_path):
    a, sent = mk(tmp_path)
    a.update({"engine": ("red", "멈춤"), "board": ("yellow", "다운")}, T)
    assert len(sent) == 1 and sent[0].count("\n") == 1             # 한 번의 점검에서 나온 두 건은 한 메시지로
    b, sent2 = mk(tmp_path)                                        # 감시자가 다시 떴다
    b.update({"engine": ("red", "멈춤"), "board": ("yellow", "다운")}, T + 60)
    assert sent2 == []
    b.update({"engine": ("red", "멈춤")}, T + 900)                 # 반복 일정은 이어진다
    assert len(sent2) == 1 and "계속" in sent2[0]
    st = json.loads((tmp_path / "alert_state.json").read_text(encoding="utf-8"))
    assert st["keys"]["engine"]["sent_n"] == 2


def test_failed_send_is_kept_and_retried_next_time(tmp_path):
    out, fail = [], {"on": True}

    def send(text):
        if fail["on"]:
            raise OSError("인터넷 없음")
        out.append(text)

    a = Alerter(send, tmp_path / "s.json", None, bench="b")
    a.update({"engine": ("red", "멈춤")}, T)
    a.update({"board": ("yellow", "다운")}, T + 60)
    assert out == []
    fail["on"] = False
    a.update({}, T + 120)
    assert len(out) == 1 and "조치 · 엔진" in out[0] and "준비 · 결과판" in out[0]
    a.update({}, T + 180)
    assert len(out) == 1


def test_without_webhook_state_is_still_recorded(tmp_path):
    a = Alerter(SlackSender(hook=""), tmp_path / "s.json", tmp_path / "ack.json", bench="b")
    assert a.has_webhook() is False
    sent = a.update({"engine": ("red", "멈춤")}, T)
    assert len(sent) == 1                                          # 무엇을 '보냈어야' 하는지는 돌려준다(로그용)
    assert a.snapshot()["engine"]["light"] == "red"
    assert json.loads((tmp_path / "s.json").read_text(encoding="utf-8"))["pending"] == []   # 무음은 실패가 아니다


class FlipSender:
    """웹훅이 나중에 들어오는 상황 — has_webhook 이 바뀐다."""
    def __init__(self):
        self.hook, self.sent = False, []
    def has_webhook(self):
        return self.hook
    def __call__(self, text):
        if self.hook:
            self.sent.append(text)


def test_start_test_message_once_and_deferred_until_webhook_appears():
    s = FlipSender()
    a = Alerter(s, None, None, bench="hq-bench-1")
    assert a.test("감시자 시작 · 알림 시험") is False and s.sent == []
    a.update({"engine": ("green", "")}, T)
    assert s.sent == []
    s.hook = True                                                   # 사람이 tools/remote_setup.py 로 웹훅을 넣었다
    a.update({"engine": ("green", "")}, T + 300)
    assert s.sent == ["[셀 시험대 hq-bench-1] 시험 · 감시자 시작 · 알림 시험"]
    assert a.test("또") is True and len(s.sent) == 1                # 같은 프로세스에서 한 번
    a.update({}, T + 600)
    assert len(s.sent) == 1


def test_write_ack_keeps_other_keys(tmp_path):
    p = tmp_path / "alert_ack.json"
    write_ack(p, "engine", T)
    write_ack(p, "plug_on", T + 5)
    assert json.loads(p.read_text(encoding="utf-8")) == {"engine": T, "plug_on": T + 5}


def test_slack_sender_posts_and_raises_on_failure():
    calls = []
    s = SlackSender(hook="https://hooks.slack.com/x", post=lambda url, text: calls.append((url, text)))
    s("안녕")
    assert calls == [("https://hooks.slack.com/x", "안녕")] and s.has_webhook()

    def boom(url, text):
        raise OSError("down")
    with pytest.raises(OSError):
        SlackSender(hook="https://hooks.slack.com/x", post=boom)("x")
    quiet = SlackSender(hook="", post=boom)
    quiet("x")                                                      # 웹훅이 없으면 무음
    assert quiet.has_webhook() is False


def test_slack_sender_rereads_keyring_only_every_5_minutes(monkeypatch):
    reads = {"n": 0, "value": None}

    def fake_webhook():
        reads["n"] += 1
        return reads["value"]

    monkeypatch.setattr(alert, "_webhook", fake_webhook)
    clock = {"t": T}
    s = SlackSender(clock=lambda: clock["t"], post=lambda u, t: None)
    assert reads["n"] == 1 and s.has_webhook() is False
    clock["t"] += 299
    assert s.has_webhook() is False and reads["n"] == 1
    reads["value"] = "https://hooks.slack.com/new"
    clock["t"] += 2
    assert s.has_webhook() is True and reads["n"] == 2
    clock["t"] += 999
    assert s.has_webhook() is True and reads["n"] == 2              # 있으면 다시 읽지 않는다
