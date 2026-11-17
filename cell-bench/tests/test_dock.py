"""Dock 명령 모듈 (cellbench/dock.py) · 세트 줄 dock 블록 검사 (config.dock_problems) · 수동 도구 (tools/dock_cli.py). 장비 없이 돈다.

배경(recovery-plan 4절 · 커밋 4): 세트 셀이 모두 꺼졌을 때 Dock 에 CELL_EN_INIT · CELL_AON 을 보내 셀 24대를 다시 켠다.
Dock 펌웨어(pc_interface.c)는 바이트마다 echo 하고 100 ms 쉬며, 명령을 strncmp(받은 줄, 명령, 받은 길이)로 판정한다 —
그래서 빈 줄은 CELL_EN_INIT(켜진 셀을 끈다)이 되고, 명령의 앞부분만 받으면 다른 명령이 될 수 있다.
가짜 Dock(FakeDock)은 그 판정을 그대로 흉내 내어, 이 모듈이 의도한 명령 말고는 어떤 명령도 실행시키지 않는지 본다.
진짜 COM 포트는 이 파일 안에서 열 수 없다(_no_real_serial).
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from cellbench import dock as D  # noqa: E402
from cellbench.config import DOCK, Config, dock_problems, validate  # noqa: E402

FW_NAMES = ["CELL_EN_INIT", "CELL_OFF", "CELL_DOF", "CELL_DON", "CELL_MES", "CELL_AON", "DOCK_SEL", "CELL_OF2"]   # 펌웨어의 판정 순서


def fw_parse(line: bytes) -> str | None:
    """펌웨어 get_type_of_message 흉내 — strncmp(받은 줄, 명령, 받은 길이) == 0 이면 그 명령. 받은 줄이 명령의 앞부분이면 맞는다."""
    s = line.decode("ascii")
    for n in FW_NAMES + [f"CELL{i}_ON" for i in range(1, 25)]:
        if n.startswith(s):
            return n
    return None


@pytest.fixture(autouse=True)
def _no_real_serial(monkeypatch):
    """이 파일의 어떤 검사도 진짜 직렬 포트를 열거나 장치 목록을 읽지 않는다 — 기본 함수를 쓰면 바로 실패한다."""
    import serial
    from serial.tools import list_ports

    def boom(*a, **kw):
        raise AssertionError("검사 안에서 진짜 직렬 포트에 닿으려 했다")
    monkeypatch.setattr(serial, "Serial", boom)
    monkeypatch.setattr(list_ports, "comports", boom)


class Clock:
    def __init__(self, t: float = 1000.0):
        self.t = t
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.t += s


class FakeDock:
    """Dock 펌웨어의 PC 쪽 흉내. 바이트 하나를 받으면 (바쁘지 않을 때) 곧바로 echo 하고 100 ms 쉰다.
    \\n 에서 줄을 fw_parse 로 판정해 실행 목록에 넣고 응답을 붙인다. 0x20~0x7F 밖(\\r)은 버린다."""

    def __init__(self, clock: Clock, echo: bool = True, no_reply: tuple = (), garble_at: int | None = None):
        self.clock, self.echo, self.no_reply, self.garble_at = clock, echo, set(no_reply), garble_at
        self.buf = bytearray()           # 펌웨어 DataBuff
        self.out: list[tuple[float, int]] = []   # PC 로 갈 바이트 (준비 시각, 값)
        self.busy = 0.0
        self.got = 0                     # 받은 바이트 수 (garble_at 용)
        self.executed: list[str] = []

    def feed(self, b: int) -> None:
        t = max(self.clock.t, self.busy)
        self.busy = t + 0.1
        if self.echo:
            self.out.append((t, ord("X") if self.got == self.garble_at else b))
        self.got += 1
        if 0x20 <= b < 0x80:
            self.buf.append(b)
        elif b == 0x0A:
            cmd = fw_parse(bytes(self.buf))
            self.buf.clear()
            if cmd:
                self.executed.append(cmd)
                if cmd not in self.no_reply:
                    reply = b"  INITIAL OK!\r\n" if cmd == "CELL_EN_INIT" else b"OK!\r\n"
                    self.out += [(t, c) for c in reply]


class FakePort:
    """pyserial Serial 흉내 — 열기 전의 DTR · RTS 를 기록하고, read 는 timeout 만큼 가짜 시계를 민다."""

    def __init__(self, dock: FakeDock, log: list, fail_open: Exception | None = None, fail_write_at: int | None = None):
        self.dock, self.log, self.fail_open, self.fail_write_at = dock, log, fail_open, fail_write_at
        self.port = self.baudrate = self.timeout = self.write_timeout = None
        self.dtr = self.rts = True          # pyserial 의 기본값 — 내리지 않으면 열 때 올라간다
        self.is_open = False
        self.written = bytearray()

    def open(self):
        self.log.append(("open", self.port, self.baudrate, self.dtr, self.rts))
        if self.fail_open:
            raise self.fail_open
        self.is_open = True

    def close(self):
        self.log.append(("close", self.port))
        self.is_open = False

    def reset_input_buffer(self):
        assert self.is_open                 # 이미 도착한 바이트(앞 명령 응답의 줄 끝 등)를 버린다
        self.dock.out = [(t, b) for t, b in self.dock.out if t > self.dock.clock.t]

    def write(self, b: bytes):
        assert self.is_open
        if self.fail_write_at is not None and len(self.written) >= self.fail_write_at:
            raise OSError("장치가 빠짐")
        for x in b:
            self.written.append(x)
            self.dock.feed(x)
        return len(b)

    def read(self, n: int = 1) -> bytes:
        assert self.is_open
        clk = self.dock.clock
        if self.dock.out and self.dock.out[0][0] <= clk.t + self.timeout:
            t, b = self.dock.out.pop(0)
            clk.t = max(clk.t, t)
            return bytes([b])
        clk.t += self.timeout
        return b""


def port_info(device, serial_number=None, vid=0x0403, pid=0x6001):
    return SimpleNamespace(device=device, serial_number=serial_number, vid=vid, pid=pid, description=f"USB Serial Port({device})")


PORTS = [port_info("COM28", "A50285BIA"), port_info("COM29", "7"), port_info("COM5", None, 0x303A, 0x1001)]


def bench(dock1=None, dock2=None):
    s1 = {"id": 1, "plug_mac": "20:E1:5D:E6:9C:77", "serials": "11733-11756"}
    s2 = {"id": 2, "plug_mac": "20:E1:5D:E6:97:5B", "serials": "11594-11617"}
    if dock1 is not None:
        s1["dock"] = dock1
    if dock2 is not None:
        s2["dock"] = dock2
    return Config(sets=[s1, s2])


Y_ON = {"port": "COM28", "usb_serial": "A50285BI", "power_on": True, "wake": ["CELL_EN_INIT", "CELL_AON"], "baud": 115200}


class Rig:
    """Dock 하나 + 가짜 포트 · 가짜 장치 목록 · 가짜 시계. made = 만든 포트(= 연 시도) 목록."""

    def __init__(self, cfg, set_id=1, ports=PORTS, **dock_kw):
        self.clock = Clock()
        self.fw = FakeDock(self.clock, **dock_kw)
        self.log: list = []
        self.made: list[FakePort] = []
        self.listed = 0
        self.fail_open = None
        self.fail_write_at = None

        def make():
            p = FakePort(self.fw, self.log, self.fail_open, self.fail_write_at)
            self.made.append(p)
            return p

        def lst():
            self.listed += 1
            return list(ports)
        self.dock = D.Dock(cfg, set_id, make_port=make, list_ports=lst, sleep=self.clock.sleep, clock=self.clock)

    @property
    def written(self) -> bytes:
        return b"".join(bytes(p.written) for p in self.made)


# ---------- 열기 · 보내기 ----------

def test_dtr_rts_are_lowered_before_open_and_port_is_closed():
    rig = Rig(bench(Y_ON))
    r = rig.dock.send("CELL_AON", all_off_confirmed=True)
    assert r.ok, r.reason
    assert rig.log[0] == ("open", "COM28", 115200, False, False)       # 여는 순간 DTR · RTS 가 이미 내려가 있다
    assert rig.log[-1] == ("close", "COM28") and not rig.made[0].is_open


def test_cell_aon_echo_and_ok_reply():
    rig = Rig(bench(Y_ON))
    r = rig.dock.send("CELL_AON", all_off_confirmed=True)
    assert r.ok and r.echo_ok and r.ok_mark and r.reason == ""
    assert rig.written == b"CELL_AON\r\n" and r.wrote == 10
    assert rig.fw.executed == ["CELL_AON"]
    assert r.reply.startswith("OK!") and r.raw.startswith(b"CELL_AON\r\n")
    assert r.port == "COM28" and r.found_by == "usb_serial" and "0403:6001" in r.device
    assert 0.9 <= r.seconds <= 1.5                                    # 바이트마다 100 ms — 10바이트에 약 1초


def test_cell_en_init_reply_is_initial_ok():
    rig = Rig(bench(Y_ON))
    r = rig.dock.send("CELL_EN_INIT", all_off_confirmed=True)
    assert r.ok and "INITIAL OK!" in r.reply and rig.fw.executed == ["CELL_EN_INIT"]


def test_no_echo_sends_only_first_byte_and_never_the_newline():
    rig = Rig(bench(Y_ON), echo=False)                                # Dock 이 아닌 장치 · 다른 펌웨어
    r = rig.dock.send("CELL_AON", all_off_confirmed=True)
    assert not r.ok and not r.echo_ok and "echo 가 없다" in r.reason
    assert rig.written == b"C" and r.wrote == 1 and rig.fw.executed == []
    assert 1.0 <= r.seconds <= 1.1                                    # 바이트 하나의 상한(byte_s)
    assert rig.log[-1] == ("close", "COM28")


def test_garbled_echo_stops_before_the_newline():
    rig = Rig(bench(Y_ON), garble_at=3)
    r = rig.dock.send("CELL_AON", all_off_confirmed=True)
    assert not r.ok and "echo 가 다르다" in r.reason
    assert rig.written == b"CELL" and rig.fw.executed == []          # 줄 끝이 가지 않아 Dock 은 아무것도 실행하지 않았다


def test_echo_but_no_reply_times_out_at_reply_s():
    rig = Rig(bench(Y_ON), no_reply=("CELL_AON",))
    r = rig.dock.send("CELL_AON", all_off_confirmed=True)
    assert not r.ok and r.echo_ok and not r.ok_mark and "OK!" in r.reason
    assert DOCK["reply_s"] <= r.seconds <= DOCK["reply_s"] + 0.1


def test_open_failure_and_unplug_mid_write_are_results_not_exceptions():
    rig = Rig(bench(Y_ON))
    rig.fail_open = PermissionError(13, "Access is denied")          # 다른 프로그램이 포트를 쥐고 있다
    r = rig.dock.send("CELL_AON", all_off_confirmed=True)
    assert not r.ok and "열지 못함" in r.reason and r.wrote == 0
    rig2 = Rig(bench(Y_ON))
    rig2.fail_write_at = 4
    r2 = rig2.dock.send("CELL_AON", all_off_confirmed=True)
    assert not r2.ok and "끊김" in r2.reason and r2.wrote == 4 and rig2.log[-1] == ("close", "COM28")


# ---------- 거부 — 포트 목록도 읽지 않고 포트도 만들지 않는다 ----------

@pytest.mark.parametrize("cmd", ["CELL_MES", "CELL_OFF", "CELL_DOF", "CELL_OF2", "CELL_DON", "DOCK_SEL", "CELL3_ON", "",
                                 "PING", "cell_aon", "CELL_AON\n", "CELL_AON ", "CELL_", "rm -rf", None])
def test_allowlist_refuses_everything_else(cmd):
    rig = Rig(bench(Y_ON))
    r = rig.dock.send(cmd, all_off_confirmed=True)
    assert not r.ok and "허용 목록" in r.reason
    assert rig.made == [] and rig.listed == 0


def test_allowlist_is_exactly_two_commands():
    assert DOCK["allowed"] == ("CELL_AON", "CELL_EN_INIT")           # 넓히려면 이 검사부터 고쳐야 한다(불변식 I14)


@pytest.mark.parametrize("confirm", [False, None, "yes", 1])
def test_press_needs_all_off_confirmed_true(confirm):
    rig = Rig(bench(Y_ON))
    r = rig.dock.send("CELL_AON", all_off_confirmed=confirm)
    w = rig.dock.wake(all_off_confirmed=confirm)
    assert not r.ok and "all_off_confirmed" in r.reason and not w.ok and "all_off_confirmed" in w.reason
    assert rig.made == [] and rig.listed == 0


@pytest.mark.parametrize("dock", [{**Y_ON, "power_on": False}, {k: v for k, v in Y_ON.items() if k != "power_on"}])
def test_switch_off_sends_nothing_and_opens_nothing(dock):
    rig = Rig(bench(dock))
    r = rig.dock.send("CELL_AON", all_off_confirmed=True)
    w = rig.dock.wake(all_off_confirmed=True)
    assert not r.ok and "power_on" in r.reason and not w.ok and "power_on" in w.reason and w.steps == []
    assert rig.made == [] and rig.listed == 0 and rig.fw.executed == []


def test_manual_tool_path_ignores_switch_but_not_confirmation():
    rig = Rig(bench({**Y_ON, "power_on": False}))
    assert not rig.dock.send("CELL_AON", manual=True).ok and rig.made == []      # 확인 없이는 사람 도구도 못 보낸다
    r = rig.dock.send("CELL_AON", all_off_confirmed=True, manual=True)
    assert r.ok and rig.fw.executed == ["CELL_AON"]


def test_no_dock_block_or_bad_block_refuses_everything():
    for cfg, word in [(bench(), "dock 블록이 없다"), (bench({"port": "COM28", "power_on": "true"}), "true/false"),
                      (bench({"port": "COM28"}, {"port": "com28"}), "두 세트")]:
        rig = Rig(cfg)
        for r in (rig.dock.probe(), rig.dock.send("CELL_AON", all_off_confirmed=True, manual=True)):
            assert not r.ok and word in r.reason, (word, r.reason)
        assert word in rig.dock.wake(all_off_confirmed=True, manual=True).reason
        assert rig.made == [] and rig.listed == 0
    assert "설정에 없다" in Rig(bench(Y_ON), set_id=3).dock.probe().reason


# ---------- 정체 확인 (probe) ----------

def test_probe_ignores_switch_and_executes_nothing():
    rig = Rig(bench({"port": "COM28", "usb_serial": "A50285BI"}))      # 스위치 없음(꺼짐)
    r = rig.dock.probe()
    assert r.ok and r.echo_ok and r.cmd == "PING" and r.reply == ""
    assert rig.written == b"PING\r\n" and rig.fw.executed == []      # 펌웨어가 모르는 줄 — 셀에 닿지 않는다


def test_probe_reports_no_echo():
    rig = Rig(bench({"port": "COM28"}), echo=False)
    r = rig.dock.probe()
    assert not r.ok and "echo" in r.reason and rig.written == b"P"


def test_stale_partial_line_fails_safe_and_probe_clears_it():
    rig = Rig(bench(Y_ON))
    rig.fw.buf += b"CELL_"                                            # 앞선 시도가 echo 실패로 멈추며 남긴 앞부분
    r = rig.dock.send("CELL_AON", all_off_confirmed=True)
    assert not r.ok and rig.fw.executed == []                         # "CELL_CELL_AON" — 어떤 명령도 아니다. OK! 가 없어 실패로 보고
    rig.fw.buf += b"CELL_E"
    assert rig.dock.probe().ok and rig.fw.executed == [] and rig.fw.buf == b""
    assert rig.dock.send("CELL_AON", all_off_confirmed=True).ok and rig.fw.executed == ["CELL_AON"]


def test_firmware_prefix_rule_never_turns_our_lines_into_another_command():
    """펌웨어는 명령의 앞부분도 그 명령으로 친다. 그래서 (1) 빈 줄은 CELL_EN_INIT 이고 — 이 모듈은 빈 줄을 보내지 않는다,
    (2) 이 모듈이 보내는 줄은 정확히 그 명령(또는 PROBE 는 아무것도 아님)이고, (3) 중간에 멈춰 남은 앞부분 뒤에 어떤 줄이 붙어도 명령이 되지 않는다."""
    assert fw_parse(b"") == "CELL_EN_INIT" and fw_parse(b"CELL_O") == "CELL_OFF"     # 위험이 실제로 있다는 것
    lines = list(DOCK["allowed"]) + [D.PROBE]
    for line in lines:
        assert fw_parse(line.encode()) == (None if line == D.PROBE else line)
        for k in range(1, len(line)):
            for nxt in lines:
                assert fw_parse((line[:k] + nxt).encode()) is None, (line[:k], nxt)


# ---------- 포트 찾기 ----------

def test_find_by_usb_serial_accepts_ftdi_channel_suffix_and_wins_over_port_name():
    port, how, dev, why = D.find_port({"port": "COM5", "usb_serial": "A50285BI"}, PORTS)
    assert (port, how, why) == ("COM28", "usb_serial", "") and "0403:6001" in dev
    assert D.find_port({"usb_serial": "A50285BIA"}, PORTS)[0] == "COM28"         # 접미사째 적어도 된다
    assert D.find_port({"usb_serial": "A50285B"}, PORTS)[0] is None             # 앞부분만 같은 것은 아니다


def test_usb_serial_not_found_does_not_fall_back_to_port_name():
    rig = Rig(bench({**Y_ON, "usb_serial": "ZZZ999"}))
    r = rig.dock.send("CELL_AON", all_off_confirmed=True)
    assert not r.ok and "ZZZ999" in r.reason and r.port is None
    assert rig.listed == 1 and rig.made == []                         # 목록만 읽고 포트는 만들지 않았다


def test_two_ports_with_same_serial_is_ambiguous():
    ports = PORTS + [port_info("COM40", "A50285BI")]
    port, _, _, why = D.find_port({"usb_serial": "A50285BI"}, ports)
    assert port is None and "2개" in why


def test_find_by_port_name_when_no_serial():
    assert D.find_port({"port": "com29"}, PORTS)[:2] == ("COM29", "port")
    port, _, _, why = D.find_port({"port": "COM30"}, PORTS)
    assert port is None and "COM30" in why and "USB 자리" in why


def test_port_list_failure_is_a_result():
    rig = Rig(bench(Y_ON))
    rig.dock.list_ports = lambda: (_ for _ in ()).throw(OSError("목록 실패"))
    r = rig.dock.send("CELL_AON", all_off_confirmed=True)
    assert not r.ok and "포트 목록" in r.reason and rig.made == []


# ---------- 순서 (wake) ----------

def test_wake_runs_in_order_with_gap_and_opens_per_command():
    rig = Rig(bench(Y_ON))
    w = rig.dock.wake(all_off_confirmed=True)
    assert w.ok and w.reason == "" and [s.cmd for s in w.steps] == ["CELL_EN_INIT", "CELL_AON"]
    assert rig.fw.executed == ["CELL_EN_INIT", "CELL_AON"] and rig.clock.slept == [DOCK["gap_s"]] == [2.0]
    assert [e[0] for e in rig.log] == ["open", "close", "open", "close"]   # 명령마다 열고 바로 닫는다


def test_wake_stops_at_first_failure():
    rig = Rig(bench(Y_ON), no_reply=("CELL_EN_INIT",))
    w = rig.dock.wake(all_off_confirmed=True)
    assert not w.ok and len(w.steps) == 1 and w.reason.startswith("CELL_EN_INIT 실패")
    assert rig.written == b"CELL_EN_INIT\r\n" and rig.clock.slept == []      # CELL_AON 은 가지 않았다


def test_wake_refuses_whole_sequence_before_sending_anything():
    rig = Rig(bench({**Y_ON, "wake": ["CELL_EN_INIT", "CELL_MES"]}))        # 설정 검사가 먼저 막는다
    w = rig.dock.wake(all_off_confirmed=True)
    assert not w.ok and "허용 목록" in w.reason and rig.made == []
    rig2 = Rig(bench({"port": "COM28"}))
    assert "비어 있다" in rig2.dock.wake(all_off_confirmed=True, manual=True).reason


# ---------- 설정 검사 ----------

def test_valid_dock_blocks_pass_validate_and_do_not_add_config_fields(tmp_path):
    cfg = bench(Y_ON, {"port": "COM29", "power_on": False})
    assert validate(cfg) == [] and dock_problems(cfg.sets) == []
    p = tmp_path / "bench.json"
    p.write_text(json.dumps({"sets": cfg.sets}), encoding="utf-8")
    loaded = Config.load(None, bench_path=p)
    assert validate(loaded) == [] and loaded.sets[0]["dock"] == Y_ON
    assert not hasattr(Config(), "dock") and "dock" not in json.loads(Config().dump())   # run.log '설정:' 줄은 그대로


@pytest.mark.parametrize("dock, word", [
    ("COM28", "객체여야"),
    ({"port": "COM28", "wakeup": ["CELL_AON"]}, "알 수 없는 키"),
    ({"power_on": False}, "하나는 있어야"),
    ({"port": ""}, "빈 칸이 아닌"),
    ({"port": "COM28", "usb_serial": 123}, "빈 칸이 아닌"),
    ({"port": "COM28", "power_on": "false"}, "true/false"),
    ({"port": "COM28", "power_on": 1}, "true/false"),
    ({"port": "COM28", "wake": ["CELL_EN_INIT", "CELL_MES"]}, "허용 목록"),
    ({"port": "COM28", "wake": ["CELL_OFF"]}, "허용 목록"),
    ({"port": "COM28", "wake": "CELL_AON"}, "목록이어야"),
    ({"port": "COM28", "wake": []}, "목록이어야"),
    ({"port": "COM28", "power_on": True}, "wake 순서가 없다"),
    ({"port": "COM28", "baud": "115200"}, "baud"),
    ({"port": "COM28", "baud": 0}, "baud"),
])
def test_bad_dock_block_is_a_validate_problem(dock, word):
    probs = validate(bench(dock))
    assert any(word in p for p in probs), probs


def test_same_dock_in_two_sets_is_rejected():
    assert any("dock.port" in p and "두 세트" in p for p in validate(bench({"port": "COM28"}, {"port": "com28"})))
    assert any("dock.usb_serial" in p for p in validate(bench({"usb_serial": "A50285BI"}, {"usb_serial": "a50285bi"})))
    assert validate(bench({"port": "COM28"}, {"port": "COM29"})) == []


# ---------- 수동 도구 ----------

def cli(tmp_path, cfg_sets, argv, **rig_kw):
    import dock_cli
    p = tmp_path / "bench.json"
    p.write_text(json.dumps({"sets": cfg_sets}), encoding="utf-8")
    rigs: list[Rig] = []

    def make(cfg, sid):
        rigs.append(Rig(cfg, sid, **rig_kw))
        return rigs[-1].dock
    code = dock_cli.main(argv + ["--bench", str(p)], make_dock=make)
    return code, rigs[0]


def test_cli_without_yes_only_previews(tmp_path, capsys):
    code, rig = cli(tmp_path, bench({**Y_ON, "power_on": False}).sets, ["send", "--set", "1", "--cmd", "CELL_AON", "--all-off"])
    out = capsys.readouterr().out
    assert code == 0 and rig.made == [] and "보내지 않았다" in out and "COM28" in out and "CELL_AON\\r\\n" in out


def test_cli_yes_without_all_off_is_refused(tmp_path, capsys):
    code, rig = cli(tmp_path, bench(Y_ON).sets, ["wake", "--set", "1", "--yes"])
    assert code == 1 and rig.made == [] and "all_off_confirmed" in capsys.readouterr().out


def test_cli_refuses_forbidden_command(tmp_path, capsys):
    for extra in ([], ["--all-off", "--yes"]):
        code, rig = cli(tmp_path, bench(Y_ON).sets, ["send", "--set", "1", "--cmd", "CELL_MES"] + extra)
        assert code == 1 and rig.made == [] and "허용 목록" in capsys.readouterr().out


def test_cli_sends_with_yes_and_all_off_even_when_switch_is_off(tmp_path, capsys):
    code, rig = cli(tmp_path, bench({**Y_ON, "power_on": False}).sets, ["wake", "--set", "1", "--all-off", "--yes"])
    assert code == 0 and rig.fw.executed == ["CELL_EN_INIT", "CELL_AON"] and "순서 통과" in capsys.readouterr().out


def test_cli_probe(tmp_path, capsys):
    code, rig = cli(tmp_path, bench({"port": "COM28", "usb_serial": "A50285BI"}).sets, ["probe", "--set", "1"])
    assert code == 0 and rig.written == b"PING\r\n" and "정체 확인 통과" in capsys.readouterr().out
    code, rig = cli(tmp_path, bench({"port": "COM28"}).sets, ["probe", "--set", "1"], echo=False)
    assert code == 1 and "echo" in capsys.readouterr().out
