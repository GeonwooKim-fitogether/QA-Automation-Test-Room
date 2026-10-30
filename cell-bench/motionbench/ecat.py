"""EtherCAT 통로 — pysoem 을 가져다 쓰는 유일한 파일.

M1 은 SAFE-OP 까지만 간다. SAFE-OP 에서는 드라이브가 출력 PDO 를 적용하지 않고, 그 출력도 늘 cia402.m1_output(Controlword 0)이다. 이중 안전.
M2(run_motion.py move)만 OP 를 요청하고(go), WRITABLE 의 RAM 객체만 SDO 로 쓴다(sdo_put). EEPROM 쓰기 · 0x1010 저장 · DC(SYNC0) 켜기는 없다.
Windows 는 Npcap 이 필요하다. 어댑터가 안 열리면 우회하지 않고 그대로 멈춘다.
"""
from __future__ import annotations

import struct
import threading
import time
from collections import deque

import pysoem

from . import cia402, rt

IFNAME = r"\Device\NPF_{6D0E574A-8D90-4065-9679-6A2DBF525343}"   # Realtek USB GbE (records §1) — ponytail: PC 가 바뀌면 여기를 고친다
IDENTITY = ("MBDLN25BL", 0x066F, 0x60540006)                       # 이름 · Vendor · Product code (records §7)
PERIOD_S = 0.002          # FreeRUN 2 ms. DC 는 켜지 않는다 — config_dc · dc_sync 를 부르지 않는다
RECV_US = 20_000          # 응답 프레임을 기다리는 시간 = 허용 최대 지연 20 ms. SOEM 기본 2 ms 로는 이 USB 어댑터의
                          # 늦은 응답이 WKC 오류로 세어졌다 — 2 ms 넘는 응답 0.6 %, 최대 5.6 ms, 잃은 프레임 0 (2026-10-22 진단 1분)
DONE = "시간 다 됨"        # 정상 종료의 이유

# SDO 로 읽기만 하는 객체. 0x1C32:01 은 DC 를 끈 것(0 = FreeRUN)의 증거로 읽는다.
SDO_TABLE = [
    (0x1018, 4, "Identity — serial number", "<I"),
    (0x607D, 1, "Software position limit — min", "<i"),
    (0x607D, 2, "Software position limit — max", "<i"),
    (0x607F, 0, "Max profile velocity", "<I"),
    (0x6080, 0, "Max motor speed", "<I"),
    (0x605A, 0, "Quick stop option code", "<h"),
    (0x6085, 0, "Quick stop deceleration", "<I"),
    (0x60C5, 0, "Max acceleration", "<I"),
    (0x1C32, 1, "SM2 sync mode (0 = FreeRUN)", "<H"),
    # 랜선 시험 뒤 scan 용 — PRE-OP 에서 PDO 매핑보다 먼저 읽으므로 SAFE-OP 가 거부돼도(통신 알람) 남는다. 없는 객체는 오류로 적힐 뿐이다
    (0x6064, 0, "Position actual value", "<i"),
    (0x603F, 0, "Error code", "<H"),
    (0x6041, 0, "Statusword", "<H"),
    (0x6007, 0, "Abort connection option code", "<h"),
    (0x605E, 0, "Fault reaction option code", "<h"),
    (0x3510, 0, "Pr5.10", "<h"),
    (0x3614, 0, "Pr6.14", "<h"),
    # 끝 터치 준비 — 미는 힘 상한. Pr5.21 = 1 이면 양방향 Pr0.13, 실제 상한은 0x6072 와 Pr0.13 중 작은 쪽
    # (매뉴얼 SX-DSV03241 PDF 161쪽). 60E0h · 60E1h 는 Pr5.21 = 5 일 때만 쓰인다. 읽기만 한다
    (0x6072, 0, "Max torque (0.1 %)", "<H"),
    (0x60E0, 0, "Positive torque limit value", "<H"),
    (0x60E1, 0, "Negative torque limit value", "<H"),
    (0x3013, 0, "Pr0.13 1st torque limit", "<h"),
    (0x3522, 0, "Pr5.22 2nd torque limit", "<h"),
    (0x3521, 0, "Pr5.21 torque limit selection", "<h"),
]

emergencies: list[str] = []      # SDO 를 읽다 끼어든 비상 메시지(EMCY) — 기록용


class Stop(Exception):
    """M1 을 즉시 멈추는 이유. 부르는 쪽이 shut() 으로 INIT 로 내리고 어댑터를 닫는다."""


def sdo(slave, index: int, sub: int, fmt: str, release_gil: bool = False):
    """SDO 읽기 하나 → (값, None) 또는 (None, 오류 한 줄). EMCY 가 끼어들면 적어 두고 한 번 더 읽는다."""
    err = ""
    for _ in range(2):
        try:
            raw = slave.sdo_read(index, sub, release_gil=release_gil)
        except pysoem.Emergency as e:
            err = f"Emergency error_code 0x{e.error_code:04X} reg 0x{e.error_reg:02X}"
            emergencies.append(f"0x{index:04X}:{sub:02X} 읽는 중 — {err}")
            continue
        except pysoem.SdoError as e:
            return None, f"SDO abort 0x{e.abort_code:08X} {e.desc}"
        except pysoem.WkcError as e:
            return None, f"WkcError wkc={getattr(e, 'wkc', None)}"
        except Exception as e:                  # MailboxError · PacketError
            return None, f"{type(e).__name__}: {e}"
        if len(raw) < struct.calcsize(fmt):
            return None, f"{len(raw)} byte 만 왔다 ({raw.hex()})"
        return struct.unpack_from(fmt, raw)[0], None
    return None, err


def _must(slave, index: int, sub: int, fmt: str, release_gil: bool = False):
    v, err = sdo(slave, index, sub, fmt, release_gil)
    if err:
        raise Stop(f"SDO 0x{index:04X}:{sub:02X} 읽기 실패 — {err}")
    return v


def read_map(slave, assign: int) -> list[tuple[int, int, int]]:
    """PDO 할당(0x1C12 출력 · 0x1C13 입력)을 따라가 실제 매핑 [(index, subindex, bit 수)] 를 SDO 로 읽는다."""
    got = []
    for i in range(1, _must(slave, assign, 0, "<B") + 1):
        pdo = _must(slave, assign, i, "<H")
        for j in range(1, _must(slave, pdo, 0, "<B") + 1):
            e = _must(slave, pdo, j, "<I")
            got.append((e >> 16, (e >> 8) & 0xFF, e & 0xFF))
    return got


def _hexmap(entries) -> list[str]:
    return [f"0x{i:04X}:{s:02X}/{b}" for i, s, b in entries]


def _reach(m, s, want: int, name: str, info: dict) -> None:
    """요청한 EtherCAT 상태에 닿을 때까지 기다리고(최대 2초) 결과를 info[name] 에 적는다. 못 닿으면 Stop."""
    got = m.state_check(want, 2_000_000)
    m.read_state()
    info[name] = {"got": got, "al_status": f"0x{s.al_status:04X} {pysoem.al_status_code_to_string(s.al_status)}"}
    if got != want:
        raise Stop(f"{name} 에 못 감 — 상태 {got}, AL {info[name]['al_status']}")


def bring_up(m, info: dict):
    """슬레이브 1대를 찾아 기록과 대조하고 SAFE-OP 까지 간다 → (slave, 지금 위치). 어긋나면 Stop.
    info 는 부르는 쪽 것이라, 중간에 멈춰도 거기까지 알아낸 것이 남는다(drives.json)."""
    n = m.config_init()                         # 슬레이브를 INIT 로 내리고 SII 를 읽어 PRE-OP 를 요청한다 (SDO · EEPROM 쓰기 없음)
    info["slaves"] = n
    if n != 1:
        raise Stop(f"슬레이브 수 {n} — 1대여야 한다")
    s = m.slaves[0]
    info["slave"] = {"pos": 1, "name": s.name, "man": f"0x{s.man:04X}", "id": f"0x{s.id:08X}", "rev": f"0x{s.rev:08X}"}
    if (s.name, s.man, s.id) != IDENTITY:
        raise Stop(f"기록과 다른 장비 {info['slave']}")
    _reach(m, s, pysoem.PREOP_STATE, "PRE_OP", info)   # config_init 은 요청만 하고 기다리지 않는다 — 메일박스(SDO)는 PRE-OP 부터
    info["sdo"] = []
    for index, sub, name, fmt in SDO_TABLE:
        v, err = sdo(s, index, sub, fmt)
        info["sdo"].append({"obj": f"0x{index:04X}:{sub:02X}", "name": name, "value": v, "error": err})
    rx, tx = read_map(s, 0x1C12), read_map(s, 0x1C13)
    info["pdo_map"] = {"rx": _hexmap(rx), "tx": _hexmap(tx), "match": rx == cia402.RX_MAP and tx == cia402.TX_MAP}
    if not info["pdo_map"]["match"]:
        raise Stop("PDO 매핑이 기록(records §7)과 다르다")
    pos = _must(s, 0x6064, 0, "<i")             # 첫 출력의 Target 을 지금 위치로
    m.config_map()                              # PDO 를 IO 맵에 놓고 SAFE-OP 를 요청한다. OP 는 요청하지 않는다
    _reach(m, s, pysoem.SAFEOP_STATE, "SAFE_OP", info)
    if (len(s.output), len(s.input)) != (cia402.OUT.size, cia402.IN.size):
        raise Stop(f"PDO 크기 출력 {len(s.output)} · 입력 {len(s.input)} byte — 기록은 9 · 23")
    s.output = cia402.m1_output(pos)
    if s.output != cia402.m1_output(pos):
        raise Stop("출력 버퍼에 쓴 것이 그대로 읽히지 않는다")
    info["expected_wkc"] = m.expected_wkc
    return s, pos


class Cycler(threading.Thread):
    """2 ms 주기 스레드 — 출력(늘 Controlword 0)을 쓰고 보내고 받고, 매 주기 Statusword 를 검사하고, 행을 deque 에 넣는다.
    파일은 만지지 않는다. stop 이 채워지면 멈춘다(안전 규칙 위반 · 시간 다 됨 · 바깥 요청).
    ctl(M2 의 m2.Axis)을 주면 출력과 매 주기 검사를 ctl 이 맡는다 — 이상이면 멈추지 않고 Quick stop 을 걸고 계속 보낸다."""

    def __init__(self, m, s, pos: int, seconds: float, ctl=None):
        super().__init__(name="ecat-cycle", daemon=True)
        self.m, self.s, self.pos, self.seconds, self.ctl = m, s, pos, seconds, ctl
        self.rows: deque = deque()
        self.stop: str | None = None

    def run(self) -> None:
        self.priority = rt.raise_thread()      # 백신 · 보안 프로그램보다 먼저 CPU 를 받게 (motionbench/rt.py)
        try:
            self._loop()
        except Exception as e:                  # 예외로 죽어도 이유는 남긴다 — 부르는 쪽이 INIT 로 내린다
            self.stop = self.stop or f"주기 스레드 예외 {type(e).__name__}: {e}"

    def _loop(self) -> None:
        m, s, exp = self.m, self.s, self.m.expected_wkc
        T = int(PERIOD_S * 1e9)
        nxt = time.perf_counter_ns()
        end = nxt + int(self.seconds * 1e9)
        prev, bad = None, 0
        while self.stop is None:
            nxt += T
            wait = nxt - time.perf_counter_ns()
            if wait > 0:
                time.sleep(wait / 1e9)          # Python 3.11 의 Windows sleep 은 고해상도 대기 타이머 — 15.6 ms 틱에 묶이지 않는다
            else:
                nxt = time.perf_counter_ns()    # 늦었으면 따라잡으려 몰아 보내지 않는다
            if nxt >= end:
                self.stop = DONE
                break
            s.output = self.ctl.output() if self.ctl else cia402.m1_output(self.pos)
            t_send = time.perf_counter_ns()
            m.send_processdata()
            wkc = m.receive_processdata(RECV_US)
            t_mono, t_wall = time.perf_counter_ns(), time.time()
            rtt_us = (t_mono - t_send) // 1000  # 보내기 직전 → 응답을 받은 직후 (USB 어댑터 지연이 여기 보인다)
            if wkc != exp:
                bad += 1
                self.rows.append((t_wall, t_mono, rtt_us, wkc, None))
                if bad >= 3 and self.ctl:       # M2: Quick stop 을 걸고 계속 보낸다 (끊기면 드라이브 워치독이 마지막 보루)
                    self.ctl.lost(wkc, exp)
                elif bad >= 3:                  # 통신 두절 — 매 주기 검사를 할 수 없으니 멈춘다 (3주기)
                    self.stop = f"WKC 3주기 연속 불일치 (마지막 {wkc}, 기대 {exp})"
                continue
            bad = 0
            v = cia402.decode(s.input)
            self.pos = v.pos                    # 다음 출력의 Target = 방금 읽은 위치
            self.rows.append((t_wall, t_mono, rtt_us, wkc, v))
            if self.ctl:
                self.ctl.step(prev, v, t_mono)
            else:
                why = cia402.m1_problem(prev, v)
                if why:
                    self.stop = why
            prev = cia402.state(v.sw)


# M2 가 쓸 수 있는 SDO 는 이것뿐이다 — 전부 RAM 값. 0x1010 저장 · EEPROM · Pr(0x3xxx) 쓰기는 없다.
WRITABLE = {(0x6081, 0), (0x6083, 0), (0x6084, 0), (0x6085, 0), (0x607D, 1), (0x607D, 2),
            (0x6072, 0)}   # 0x6072 Max torque — 끝 터치 동안만 낮추고 끝에 처음 값으로 되돌린다


def sdo_put(slave, index: int, sub: int, fmt: str, value: int, release_gil: bool = False) -> int:
    """허용된 SDO 하나를 쓰고 다시 읽어 같은지 본다 → 읽은 값. 허용 밖 · 실패 · 불일치면 Stop.
    release_gil = True — 주기 스레드가 도는 동안(서보 온) 부를 때. 기본(False)이면 SDO 동안 주기 스레드가 멈춘다."""
    if (index, sub) not in WRITABLE:
        raise Stop(f"0x{index:04X}:{sub:02X} 는 쓰기 허용 목록에 없다")
    try:
        slave.sdo_write(index, sub, struct.pack(fmt, value), release_gil=release_gil)
    except Exception as e:
        raise Stop(f"SDO 0x{index:04X}:{sub:02X} 쓰기 실패 — {type(e).__name__}: {e}")
    got = _must(slave, index, sub, fmt, release_gil)
    if got != value:
        raise Stop(f"SDO 0x{index:04X}:{sub:02X} 에 {value} 를 썼는데 {got} 로 읽힌다")
    return got


def go(m, s, want: int, seconds: float = 3.0) -> str:
    """주기 스레드가 도는 동안 EtherCAT 상태를 요청하고 기다린다 → AL 상태 한 줄. 짧게(5 ms) 나눠 확인한다 —
    pysoem 호출이 GIL 을 오래 쥐면 주기 스레드가 그동안 멈추기 때문이다."""
    m.state = want
    m.write_state()
    end = time.monotonic() + seconds
    while m.state_check(want, 5_000) != want and time.monotonic() < end:
        time.sleep(0.01)
    m.read_state()
    return f"상태 {s.state} · AL 0x{s.al_status:04X} {pysoem.al_status_code_to_string(s.al_status)}"


def shut(m) -> str:
    """INIT 로 내리고 어댑터를 닫는다. 내려간 결과를 한 줄로 돌려준다(기록용)."""
    try:
        m.state = pysoem.INIT_STATE
        m.write_state()
        got = m.state_check(pysoem.INIT_STATE, 2_000_000)
        return "INIT" if got == pysoem.INIT_STATE else f"INIT 확인 못 함 (상태 {got})"
    except Exception as e:
        return f"INIT 요청 실패 {type(e).__name__}: {e}"
    finally:
        m.close()
