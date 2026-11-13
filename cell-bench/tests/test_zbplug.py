"""지그비 플러그(cellbench/zbplug.py) — 보드 없이 주고받기 규칙을 본다. 보드 펌웨어(firmware/zb-plug/main/main.c)와 명령 이름이 맞는지도."""
import math
import re
from pathlib import Path

import pytest

from cellbench import zbplug
from cellbench.zbplug import ZbPlug, parse, reading

FW = Path(__file__).resolve().parents[1] / "firmware" / "zb-plug" / "main" / "main.c"


class FakePort:
    """보드 흉내 — 받은 명령의 표지를 붙여 정해 둔 답을 낸다. 열기 전에 쌓인 옛 줄(stale)을 먼저 낸다."""

    def __init__(self, answers, stale=()):
        self.answers = answers
        self.out = b"".join(f"{s}\n".encode() for s in stale)
        self.sent = []
        self.closed = False

    def write(self, b):
        for line in b.decode().split("\n"):
            parts = line.split()
            if not parts:
                continue
            self.sent.append(line)
            tok = next(p for p in parts if p.startswith("#"))
            ans = self.answers[parts[0]]
            ans = ans.pop(0) if isinstance(ans, list) else ans
            self.out += f"EVT REPORT src=0x1234 ep=1 cl=0x0006 at=0x0000 type=0x10 val=1\n".encode()
            if ans is not None:
                self.out += ans.replace("#T", tok).encode() + b"\n"

    def read(self, n):
        chunk, self.out = self.out[:7], self.out[7:]    # 조금씩 — 줄이 쪼개져 들어와도 맞게 잇는지
        return chunk

    def close(self):
        self.closed = True


def plug(tmp_path, port, **kw):
    p = ZbPlug(port="COM_TEST", lock_path=tmp_path / "zb.lock", **kw)
    p._open = lambda: port
    return p


def test_parse_and_reading():
    kind, tok, kv, rest = parse("STATE #7-3 on=1 w=68.4 src=em on_age=0.2 w_age=0.3 ack=1 pmode=1")
    assert (kind, tok, kv["on"], kv["w"]) == ("STATE", "#7-3", "1", "68.4")
    r = reading(kv, now=5.0)
    assert r.on is True and r.watts == pytest.approx(68.4) and r.src == "em" and r.ack == 1 and r.t == 5.0
    r2 = reading({"on": "?", "w": "nan"})
    assert r2.on is None and math.isnan(r2.watts)


def test_ask_skips_stale_lines_with_other_token(tmp_path):
    port = FakePort({"STATE": "STATE #T on=0 w=0.4 src=em ack=0"},
                    stale=["STATE #99-1 on=1 w=70.0 src=em ack=0", "EVT JOIN ieee=00 short=0x1 cap=0x8e plug=same"])
    seen = []
    kind, kv, _ = plug(tmp_path, port).ask("STATE", on_line=seen.append)
    assert kind == "STATE" and kv["on"] == "0"          # 옛 줄(on=1)을 답으로 읽지 않는다
    assert any(s.startswith("STATE #99-1") for s in seen)
    assert port.closed


def test_err_reply_raises(tmp_path):
    port = FakePort({"STATE": "ERR #T no_plug"})
    with pytest.raises(RuntimeError, match="no_plug"):
        plug(tmp_path, port).ask("STATE")


def test_timeout(tmp_path):
    port = FakePort({"STATE": None})
    with pytest.raises(TimeoutError):
        plug(tmp_path, port, timeout_s=0.3).ask("STATE")


def test_on_checks_state_and_counts(tmp_path, monkeypatch):
    monkeypatch.setattr(zbplug.time, "sleep", lambda s: None)
    port = FakePort({"ON": ["STATE #T on=0 w=0.0 ack=0", "STATE #T on=1 w=70.1 src=em ack=1"],
                     "OFF": "STATE #T on=0 w=0.5 src=em ack=1"})
    p = plug(tmp_path, port)
    r = p.on()                                            # 첫 답은 꺼짐 → 다시 시도해 켜짐
    assert r.on is True and r.watts == pytest.approx(70.1)
    assert p.metrics()["retries_1h"] == 1 and p.toggles_total == 1
    assert p.off().on is False and p.toggles_total == 2


def test_read_unknown_state_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(zbplug.time, "sleep", lambda s: None)
    port = FakePort({"STATE": "STATE #T on=? w=nan src=none ack=0"})
    with pytest.raises(RuntimeError, match="켜짐 여부"):
        plug(tmp_path, port, retries=2).read()


def test_lock_waits_then_gives_up(tmp_path):
    from cellbench.proc import acquire_lock
    held = acquire_lock(tmp_path / "zb.lock")
    try:
        p = plug(tmp_path, FakePort({"STATE": "STATE #T on=1 w=1 ack=0"}), lock_wait_s=0.3)
        with pytest.raises(TimeoutError, match="지그비 보드"):
            p.ask("STATE")
    finally:
        held.close()


def test_firmware_knows_every_command_the_pc_sends():
    src = FW.read_text(encoding="utf-8")
    cmds = set(re.findall(r'!strcmp\(cmd, "([A-Z]+)"\)', src))
    assert {"PING", "JOIN", "CLOSE", "STATE", "ON", "OFF", "INFO", "DESC", "SETUP", "FORGET", "ADOPT"} <= cmds
    # 답 줄의 모양이 PC 가 읽는 이름과 같다
    assert 'out("STATE %s on=%s w=%s src=%s' in src
    assert 'out("ERR %s' in src and 'out("OK %s' in src
