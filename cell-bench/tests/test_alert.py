"""신호등 알림기 — 노랑 1건/1시간, 빨강 즉시 + 확인까지 15분마다, 복구 1건, 시작 시험 알림.

실제 Slack 에는 닿지 않는다: 보내는 함수는 목록에 쌓는 가짜이고, SlackSender 는 hook="" 또는 가짜 post 로만 만든다
(웹훅을 찾는 경로는 keyring.get_password 와 cloud.fetch_slack_webhook · urlopen 을 가짜로 바꿔 끼워 본다 —
실제 자격 증명 관리자·Supabase 에는 닿지 않는다).
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
                    "[셀 시험대 hq-bench-1] 경고 · 결과판 · 결과판 서버가 응답하지 않음"]


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
    assert len(sent) == 2 and sent[-1].startswith("[셀 시험대 hq-bench-1] 복구 · 엔진 · 지금 경고: 되살림")


def test_unknown_is_handled_like_red_and_unknown_names_become_unknown():
    a, sent = mk()
    a.update({"cloud": ("unknown", "5분 넘게 소식 없음")}, T)
    a.update({"cloud": ("unknown", "5분 넘게 소식 없음")}, T + 900)
    a.update({"x": ("purple", "모르는 불")}, T)
    assert sent[0].startswith("[셀 시험대 hq-bench-1] 신호 없음 · 클라우드·알림 ·")   # 차선 키는 차선 이름으로 and "미확인 (계속" in sent[1]
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
    assert len(out) == 1 and "조치 · 엔진" in out[0] and "경고 · 결과판" in out[0]
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

    def fake_find():
        reads["n"] += 1
        return reads["value"], ("keyring" if reads["value"] else None)

    monkeypatch.setattr(alert, "find_webhook", fake_find)
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


# ---- 웹훅 찾기 — keyring 이 먼저, 없으면 클라우드 Vault (find_webhook) ----

from types import SimpleNamespace                                   # noqa: E402

from cellbench import cloud as cloud_mod                            # noqa: E402

HOOK = "https://hooks.slack.com/services/TFAKE/BFAKE/fake-secret-xyz"
SECRET = "fake-secret-xyz"


def sources(monkeypatch, kr=None, cloud=None):
    """keyring(cell-bench-remote 의 slack_webhook)과 클라우드가 돌려줄 값을 정한다. 클라우드를 몇 번 불렀는지 센다."""
    calls = {"cloud": 0}
    monkeypatch.setattr(alert.keyring, "get_password",
                        lambda svc, name: kr if (svc, name) == (alert.SERVICE, "slack_webhook") else None)

    def fake_fetch():
        calls["cloud"] += 1
        return cloud() if callable(cloud) else cloud
    monkeypatch.setattr(cloud_mod, "fetch_slack_webhook", fake_fetch)
    return calls


def cloud_http(monkeypatch, body=None, exc=None):
    """끝에서 끝까지 — keyring 의 웹훅은 비고 클라우드 키만 있으며, Supabase 응답은 가짜 urlopen 이 준다."""
    creds = {("cell-bench-cloud", "url"): "https://x.supabase.co", ("cell-bench-cloud", "service_key"): "svc-key"}
    monkeypatch.setattr(alert.keyring, "get_password", lambda svc, name: creds.get((svc, name)))
    hits = []

    class R:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return body

    def urlopen(req, timeout=None):
        hits.append(req.full_url)
        if exc is not None:
            raise exc
        return R()
    monkeypatch.setattr(cloud_mod.urllib.request, "urlopen", urlopen)
    return hits


class SyncThread:
    """make_notifier 의 보내는 스레드를 그 자리에서 돌린다 — 검사가 기다리지 않아도 되게."""
    def __init__(self, target, args=(), daemon=None):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


def sync_threads(monkeypatch):
    monkeypatch.setattr(alert, "threading", SimpleNamespace(Thread=SyncThread, Lock=alert.threading.Lock))


def test_keyring_webhook_wins_and_cloud_is_not_asked(monkeypatch):
    calls = sources(monkeypatch, kr="https://hooks.slack.com/pc", cloud=HOOK)
    assert alert.find_webhook() == ("https://hooks.slack.com/pc", "keyring") and calls["cloud"] == 0
    s = SlackSender(post=lambda u, t: None)
    assert s.has_webhook() and s.source == "keyring" and calls["cloud"] == 0


def test_cloud_webhook_is_used_when_keyring_is_empty_end_to_end(monkeypatch):
    hits = cloud_http(monkeypatch, body=('"' + HOOK + '"').encode())
    posted = []
    s = SlackSender(post=lambda url, text: posted.append((url, text)))
    assert hits == ["https://x.supabase.co/rest/v1/rpc/bench_slack_webhook"]
    assert s.has_webhook() is True and s.source == "cloud"
    s("안녕")
    assert posted == [(HOOK, "안녕")]
    assert Alerter(s, None, None).webhook_source() == "cloud"


def test_cloud_webhook_is_kept_in_memory_only(monkeypatch):
    cloud_http(monkeypatch, body=('"' + HOOK + '"').encode())
    monkeypatch.setattr(alert.keyring, "set_password", lambda *a: pytest.fail("keyring 에 썼다"))
    s = SlackSender(post=lambda url, text: None)
    assert s.source == "cloud"                                      # 받았지만 keyring 에는 쓰지 않았다


@pytest.mark.parametrize("body,exc", [(b'""', None), (b"null", None), (b'"http://hooks.slack.com/x"', None),
                                      (b'"not a url"', None), (None, OSError("down"))])
def test_odd_cloud_answer_means_silence(monkeypatch, capsys, body, exc):
    cloud_http(monkeypatch, body=body, exc=exc)
    posted = []
    s = SlackSender(post=lambda url, text: posted.append(url))
    s("x")
    assert s.has_webhook() is False and s.source is None and posted == []
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""


def test_no_cloud_keys_means_silence_without_any_request(monkeypatch):
    monkeypatch.setattr(alert.keyring, "get_password", lambda svc, name: None)
    monkeypatch.setattr(cloud_mod.urllib.request, "urlopen", lambda *a, **k: pytest.fail("클라우드 키가 없는데 요청함"))
    assert alert.find_webhook() == (None, None)
    assert SlackSender(post=lambda u, t: pytest.fail("보냄")).has_webhook() is False


def test_sender_asks_cloud_again_only_every_5_minutes(monkeypatch):
    box = {"v": None}
    calls = sources(monkeypatch, kr=None, cloud=lambda: box["v"])
    clock = {"t": T}
    s = SlackSender(clock=lambda: clock["t"], post=lambda u, t: None)
    assert calls["cloud"] == 1 and s.has_webhook() is False
    box["v"] = HOOK
    clock["t"] += 299
    assert s.has_webhook() is False and calls["cloud"] == 1
    clock["t"] += 2
    assert s.has_webhook() is True and s.source == "cloud" and calls["cloud"] == 2
    clock["t"] += 3600
    assert s.has_webhook() is True and calls["cloud"] == 2          # 받은 뒤에는 다시 묻지 않는다


def test_notifier_finds_cloud_webhook_lazily_and_rechecks_every_5_minutes(monkeypatch):
    sync_threads(monkeypatch)
    box = {"v": None}
    calls = sources(monkeypatch, kr=None, cloud=lambda: box["v"])
    clock = {"t": T}
    posted = []
    send = alert.make_notifier(clock=lambda: clock["t"], post=lambda url, text: posted.append((url, text)))
    assert calls["cloud"] == 0                                      # 만들 때는 찾지 않는다 — 엔진 시작을 붙잡지 않게
    send("a")
    assert calls["cloud"] == 1 and posted == []                     # 없음 → 무음
    box["v"] = HOOK
    clock["t"] += 299
    send("b")
    assert calls["cloud"] == 1 and posted == []                     # 5분 전에는 다시 찾지 않는다
    clock["t"] += 2
    send("c")
    assert calls["cloud"] == 2 and posted == [(HOOK, "c")]          # 5분 뒤 다시 찾아 이어 받는다 (엔진을 다시 띄우지 않고)
    send("d")
    send("d")                                                       # 30초 안의 같은 글은 한 번
    assert calls["cloud"] == 2 and posted == [(HOOK, "c"), (HOOK, "d")]


def test_notifier_keyring_wins_and_fixed_hook_never_looks(monkeypatch):
    sync_threads(monkeypatch)
    calls = sources(monkeypatch, kr="https://hooks.slack.com/pc", cloud=HOOK)
    posted = []
    alert.make_notifier(post=lambda url, text: posted.append(url))("x")
    assert posted == ["https://hooks.slack.com/pc"] and calls["cloud"] == 0
    silent = alert.make_notifier(hook="", post=lambda url, text: pytest.fail("보냄"))
    silent("x")
    assert calls["cloud"] == 0


def test_webhook_value_never_reaches_supervisor_report_or_log(monkeypatch, tmp_path):
    from test_supervisor import World, cfg_for, eng, now_json, put
    from cellbench.supervisor import tick
    cloud_http(monkeypatch, body=('"' + HOOK + '"').encode())
    put(tmp_path, eng(), now_json(4000), cycles=1)
    w = World()
    w.alerter = Alerter(SlackSender(post=lambda url, text: None), tmp_path / "alert_state.json", None, bench="hq-bench-1")
    rep, lines = tick(cfg_for(tmp_path), tmp_path, None, w.deps(), T)
    assert rep["slack"] is True and rep["slack_from"] == "cloud"   # 신호등의 'Slack 미연결' 노랑이 사라진다
    dumped = (json.dumps(rep, ensure_ascii=False) + "\n".join(lines)
              + (tmp_path / "alert_state.json").read_text(encoding="utf-8"))
    assert "Slack 미연결" not in dumped and SECRET not in dumped and "hooks.slack.com" not in dumped


def test_notifier_keeps_messages_sent_while_the_cloud_lookup_is_slow(monkeypatch):
    """진짜 스레드로 — 찾는 중(클라우드가 느림)에 들어온 글을 버리지 않고, 찾기는 한 번만 한다."""
    import threading
    entered, release, done = threading.Event(), threading.Event(), threading.Event()
    n = {"find": 0}

    def slow_find():
        n["find"] += 1
        entered.set()
        release.wait(2)
        return HOOK, "cloud"
    monkeypatch.setattr(alert, "find_webhook", slow_find)
    got = []

    def post(url, text):
        got.append(text)
        if len(got) == 2:
            done.set()
    send = alert.make_notifier(post=post)
    send("a")
    assert entered.wait(2)
    send("b")                                                       # 찾는 중에 들어온 글
    release.set()
    assert done.wait(2) and sorted(got) == ["a", "b"] and n["find"] == 1
