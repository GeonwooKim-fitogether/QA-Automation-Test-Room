"""LabSat 3 원격 제어 (motionbench/labsat.py) — 장비 없이 돈다. 응답 글자는 2026-10-30 실측에서 가져왔다."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from motionbench import labsat  # noqa: E402

TYPE_RAW = ("\r\nRLL03-1\r\n00071272\r\n01.09 build 1099\r\n01A5:00C8\r\nRom Code 0044\r\n-OPTIONS-\r\nRecord -NO\r\n1PPS -YES\r\n"
            "CAN -NO\r\nRS232 -NO\r\nDIG -NO\r\nI2C -NO\r\nOCXO -NO\r\n\x1b[0m\r\n\x1b[0mLABSATV3 >")
PLAY_RAW = "\r\nPLAY:\x1b[32mS01-Static-GLE-8h:DUR:00:00:21\x1b[0m\r\n\x1b[0m\r\n\x1b[0mLABSATV3 >"
ERR_RAW = "\r\n\x1b[1m\x1b[31mERR\x1b[0m\r\n\x1b[0m\r\n\x1b[0mLABSATV3 >"


def test_parse_type_reads_the_real_reply():
    t = labsat.parse_type(TYPE_RAW)
    assert (t["product"], t["serial"], t["firmware"]) == ("RLL03-1", "00071272", "01.09 build 1099")
    assert t["options"]["Record"] is False and t["options"]["1PPS"] is True and t["options"]["OCXO"] is False


def test_parse_status_and_err():
    assert labsat.parse_status(PLAY_RAW) == ("S01-Static-GLE-8h", 21)
    assert labsat.parse_status(ERR_RAW) is None
    assert labsat.is_err(ERR_RAW) and not labsat.is_err(PLAY_RAW)


@pytest.mark.parametrize("args, want", [
    (("S01-Static-GLE-8h",), "PLAY:FILE:S01-Static-GLE-8h"),
    (("S01-Static-GLE-8h", 60), "PLAY:FILE:S01-Static-GLE-8h:FROM:60"),
    (("S02_Square-GI", None, 300), "PLAY:FILE:S02_Square-GI:FOR:300"),
    (("a", 1, 2), "PLAY:FILE:a:FROM:1:FOR:2"),
])
def test_play_cmd(args, want):
    assert labsat.play_cmd(*args) == want


@pytest.mark.parametrize("args", [("bad:name",), ("x\rPLAY:STOP",), ("",), ("a", -1), ("a", None, 1.5), ("a", True)])
def test_play_cmd_refuses_before_sending(args):
    with pytest.raises(ValueError):
        labsat.play_cmd(*args)


class FakeTransport:
    sent: list = []
    reply = PLAY_RAW

    def connect(self):
        pass

    def cmd(self, text):
        FakeTransport.sent.append(text)
        return FakeTransport.reply

    def close(self):
        pass


def test_play_sends_and_returns_time_and_raises_on_err():
    FakeTransport.sent, FakeTransport.reply = [], "\r\nOK\r\nLABSATV3 >"
    ls = labsat.LabSat(FakeTransport)
    assert ls.play("S01-Static-GLE-8h", 0) > 0
    assert FakeTransport.sent == ["PLAY:FILE:S01-Static-GLE-8h:FROM:0"]
    FakeTransport.reply = ERR_RAW
    with pytest.raises(RuntimeError):
        ls.play("NOPE")


def test_status_through_fake():
    FakeTransport.sent, FakeTransport.reply = [], PLAY_RAW
    assert labsat.LabSat(FakeTransport).status() == ("S01-Static-GLE-8h", 21)
    assert FakeTransport.sent == ["PLAY:?"]
