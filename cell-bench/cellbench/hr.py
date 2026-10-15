"""심박(블루투스) 연결 — 셀과 심박대 흉내 보드(firmware/hr-sim)의 짝 · 보드 상태 읽기 · 판정 재료.

셀은 0x20 으로 받은 이름의 심박대에 블루투스로 붙어 라이브(1320B 의 51번째 바이트)에 심박을 싣는다. 심박대 대신 ESP32-C3 보드가
'HRSIM-xxxx' 이름으로 광고하고, 전원 USB 허브 두 대(A · B)에 꽂혀 제어 PC 의 USB 로 5초마다 상태 줄을 낸다.

  HRSIM name=HRSIM-4CF0 bpm=60 conns=1        ← 상태 줄 (conns = 그 보드에 붙은 기기 수)

짝 표의 정본은 bench.json 의 세트 줄이다 — 허브 포트는 사람이 꽂은 대로 적는다(USB 위치로 앞면 번호를 추정하지 않는다:
허브 안의 4포트 칩 배치가 허브마다 달라 틀린 번호가 나왔다, 2026-10-15 실측). 보드는 USB 시리얼 번호(= 보드 기본 MAC)로 찾는다.

  {"id": 1, ..., "hr_pairs": {"11733": {"board": "HRSIM-4CF0", "no": 1, "hub": "A", "port": 1}, ...}}

누가 쓰나
  감시자(supervise.py) — BoardWatch 스레드가 hr_board_every_s 마다 짝 표에 있는 보드만 짧게 열어 data/hr_boards.json 을 쓴다.
                         엔진과 분리한 까닭: 시리얼 포트 읽기(보드당 최대 6초)가 엔진의 20초 표본을 늦추면 안 되고, 감시자는 엔진이
                         멈춰도 도는 프로세스라 '보드가 꺼졌나'를 엔진과 무관하게 본다.
  신호등(health._hr)     — judge() 로 세트의 '심박' 차선과 결과판의 심박 요약을 만든다.
  엔진(cycle._watch_hr)   — repair_due() 로 다시 붙일 셀을 고른다 (자동 재연결).
판정 함수는 모두 순수하다(파일·장비에 닿지 않는다). 장비에 닿는 것은 read_board(시리얼 포트 하나 읽기)와 list_boards 뿐이다.
"""
from __future__ import annotations

import importlib.util
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

NAME_RE = re.compile(r"HRSIM-[0-9A-F]{4}")
LINE_RE = re.compile(r"HRSIM name=(HRSIM-[0-9A-Fa-f]{4}) bpm=(\d+) conns=(-?\d+)")
USB_VID, USB_PID = 0x303A, 0x1001            # ESP32-C3 USB-Serial/JTAG
BOARDS_FILE = "hr_boards.json"
FLASH_ALL = Path(__file__).resolve().parents[1] / "firmware" / "hr-sim" / "flash_all.py"

# 끊긴 셀의 원인 — 셀 심박이 0 인데 짝 보드가 어떤 상태인가로 가른다. 이유 글은 늘 같은 글이다(신호등 약속 1)
CAUSE = {
    "unknown": "보드 상태 모름 — 감시자의 보드 읽기(hr_boards.json)가 없거나 오래됨",
    "off": "보드 꺼짐 — 전원 · USB 케이블 · 허브 포트 확인",
    "unread": "보드 상태 줄을 못 읽음 — USB 는 보이는데 응답 없음(보드 멈춤 · 포트를 다른 프로그램이 씀)",
    "unjoined": "셀이 안 붙음 — 보드는 켜져 있고 연결 0",
    "other": "다른 기기 연결 또는 셀 쪽 문제 — 보드에 연결이 있는데 셀 심박 0",
}
CAUSE_OF_STATE = {"unknown": "unknown", "off": "off", "unread": "unread", "free": "unjoined", "joined": "other"}


def thresholds(cfg) -> dict:
    """심박 문턱 — config.HR 위에 bench.json 의 health 를 덮는다(health.thresholds 와 같은 자리)."""
    from .config import HR
    over = getattr(cfg, "health", None)
    return {**HR, **({k: v for k, v in over.items() if k in HR} if isinstance(over, dict) else {})}


def _num(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x else None


# ---------- 이름 ----------

_flash = None


def name_for_mac(mac: str) -> str:
    """보드 기본 MAC → 광고 이름. 굽는 스크립트(firmware/hr-sim/flash_all.py)의 규칙을 그대로 쓴다 — 규칙이 두 곳에 있으면 어긋난다."""
    global _flash
    if _flash is None:
        spec = importlib.util.spec_from_file_location("hr_flash_all", FLASH_ALL)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _flash = mod
    return _flash.name_for_mac(mac)


def parse_line(text: str) -> dict | None:
    """상태 줄 하나 → {name, bpm, conns}. 상태 줄이 아니면 None."""
    m = LINE_RE.search(text or "")
    if not m:
        return None
    return {"name": m.group(1).upper(), "bpm": int(m.group(2)), "conns": int(m.group(3))}


# ---------- 짝 표 ----------

def pairs_of(entry) -> dict[int, dict]:
    """세트 줄 하나의 짝 표 → {셀 시리얼: {board, no, hub, port}}. 읽을 수 없는 칸은 건너뛴다(문제는 pair_problems 가 알린다).
    값은 {"board": "HRSIM-4CF0", ...} 또는 이름 문자열 하나를 받는다."""
    hp = entry.get("hr_pairs") if isinstance(entry, dict) else None
    if not isinstance(hp, dict):
        return {}
    out: dict[int, dict] = {}
    for k, v in hp.items():
        try:
            serial = int(k)
        except (TypeError, ValueError):
            continue
        if isinstance(v, str):
            v = {"board": v}
        if not isinstance(v, dict) or not NAME_RE.fullmatch(str(v.get("board", ""))):
            continue
        out[serial] = {"board": str(v["board"]), "no": v.get("no"), "hub": str(v.get("hub") or ""), "port": v.get("port")}
    return out


def pair_problems(sets) -> list[str]:
    """짝 표의 문제 (config.validate 가 부른다). 짝 표가 없는 세트는 보지 않는다.
    보는 것: 객체인가 · 셀 시리얼이 그 세트의 셀인가 · 보드 이름 형식(HRSIM-XXXX) · 한 보드가 두 셀에 묶였나 · 같은 허브 포트가 두 번인가."""
    from .config import expand_serials
    problems: list[str] = []
    owner: dict[str, str] = {}
    slot: dict[tuple, str] = {}
    for i, s in enumerate(sets if isinstance(sets, list) else []):
        if not isinstance(s, dict) or "hr_pairs" not in s:
            continue
        name = f"세트 {s.get('id', f'#{i + 1}')}"
        hp = s["hr_pairs"]
        if not isinstance(hp, dict):
            problems.append(f"{name}: hr_pairs 는 객체여야 한다 ({{\"셀 시리얼\": {{\"board\": \"HRSIM-XXXX\", ...}}}})")
            continue
        try:
            mine = set(expand_serials(s.get("serials", [])))
        except ValueError:
            mine = set()
        for k, v in hp.items():
            try:
                serial = int(k)
            except (TypeError, ValueError):
                problems.append(f"{name}: hr_pairs 의 키가 셀 시리얼이 아니다: {k!r}")
                continue
            if mine and serial not in mine:
                problems.append(f"{name}: hr_pairs 의 셀 {serial} 은 이 세트의 셀이 아니다")
            b = v.get("board") if isinstance(v, dict) else v
            if not isinstance(b, str) or not NAME_RE.fullmatch(b):
                problems.append(f"{name}: 셀 {serial} 의 짝 보드 이름이 HRSIM-XXXX(16진 대문자 4자리) 가 아니다: {b!r}")
                continue
            who = f"{name} 셀 {serial}"
            if b in owner:
                problems.append(f"보드 {b} 가 두 셀에 묶였다: {owner[b]} · {who}")
            else:
                owner[b] = who
            if isinstance(v, dict) and v.get("hub") not in (None, "") and v.get("port") not in (None, ""):
                key = (str(v["hub"]), str(v["port"]))
                if key in slot:
                    problems.append(f"허브 {key[0]} 포트 {key[1]} 가 두 번 적혔다: {slot[key]} · {who}")
                else:
                    slot[key] = who
    return problems


def all_pairs(sets) -> dict[int, dict]:
    """모든 세트의 짝 — {셀 시리얼: {board, no, hub, port, set}}."""
    out: dict[int, dict] = {}
    for s in sets if isinstance(sets, list) else []:
        for serial, p in pairs_of(s).items():
            out[serial] = {**p, "set": s.get("id")}
    return out


def watched_names(sets) -> set[str]:
    """감시자가 읽을 보드 — 짝 표에 있는 보드만(짝 없는 보드는 굽는 중일 수 있어 열지 않는다)."""
    return {p["board"] for p in all_pairs(sets).values()}


# ---------- 보드 읽기 (장비) ----------

def list_boards() -> dict[str, str]:
    """{보드 MAC(대문자): 포트}. ESP32-C3 USB-Serial/JTAG 의 USB 시리얼 번호가 그 보드의 기본 MAC 이다."""
    from serial.tools import list_ports
    return {str(p.serial_number).upper(): p.device for p in list_ports.comports()
            if p.vid == USB_VID and p.pid == USB_PID and p.serial_number}


def read_board(port: str, timeout_s: float = 6.0) -> dict | None:
    """포트 하나를 열어 상태 줄 하나를 받으면 바로 닫는다. 시간 안에 못 받으면 None.
    여는 순간 DTR · RTS 를 올리지 않는다 — ESP32-C3 USB-Serial/JTAG 는 이 두 선으로 칩을 리셋하고, 리셋되면 셀과의 블루투스 연결이 끊긴다
    (2026-10-15 실측: 이렇게 열면 부팅 줄 없이 다음 상태 줄만 오고 conns 가 그대로다)."""
    import serial
    s = serial.Serial()
    s.port, s.baudrate, s.timeout = port, 115200, 0.5
    s.dtr = False
    s.rts = False
    s.open()
    try:
        buf, end = "", time.monotonic() + timeout_s
        while time.monotonic() < end:
            buf += s.read(256).decode("utf-8", "replace")
            *full, buf = buf.split("\n")
            for line in full:
                p = parse_line(line)
                if p:
                    return p
        return None
    finally:
        s.close()


def scan(names: set[str], prev: dict | None, now: float, timeout_s: float = 6.0,
         ports: dict[str, str] | None = None, reader: Callable[[str, float], dict | None] | None = None) -> dict:
    """짝 표의 보드를 한꺼번에(보드마다 스레드 하나) 읽어 hr_boards.json 의 내용을 만든다 — 보드가 24개여도 timeout_s 남짓.
    {t, boards: {이름: {mac, port, t_seen, bpm, conns, alive, error?}}}. alive = 이번에 상태 줄을 받았다.
    못 받은 보드는 마지막으로 본 시각(t_seen)을 이어 둔다. 실패는 삼키고 error 칸에 남긴다."""
    reader = reader or read_board
    if ports is None:
        try:
            ports = list_boards()
        except Exception as e:                       # pyserial 이 없거나 장치 목록을 못 읽음
            return {"t": now, "boards": {}, "error": f"USB 장치 목록을 읽지 못함: {type(e).__name__}: {e}"}
    targets: dict[str, tuple[str, str]] = {}
    for mac, port in ports.items():
        try:
            nm = name_for_mac(mac)
        except ValueError:
            continue
        if nm in names:
            targets[nm] = (mac, port)

    def one(nm: str):
        try:
            r = reader(targets[nm][1], timeout_s)
            return r if r else "상태 줄 없음"
        except Exception as e:                       # 포트 점유 · 장치가 그 순간 빠짐
            return f"{type(e).__name__}: {e}"[:120]

    results: dict[str, object] = {}
    if targets:
        with ThreadPoolExecutor(max_workers=len(targets)) as ex:
            results = dict(zip(targets, ex.map(one, targets)))
    prevb = (prev or {}).get("boards") if isinstance(prev, dict) and isinstance((prev or {}).get("boards"), dict) else {}
    boards: dict[str, dict] = {}
    for nm in sorted(names):
        old = prevb.get(nm) if isinstance(prevb.get(nm), dict) else {}
        r = results.get(nm)
        if isinstance(r, dict):
            mac, port = targets[nm]
            boards[nm] = {"mac": mac, "port": port, "t_seen": now, "bpm": r["bpm"], "conns": r["conns"], "alive": True}
        elif nm in targets:
            mac, port = targets[nm]
            boards[nm] = {"mac": mac, "port": port, "t_seen": old.get("t_seen"), "bpm": old.get("bpm"), "conns": old.get("conns"),
                          "alive": False, "error": str(r)}
        else:
            boards[nm] = {"mac": old.get("mac"), "port": None, "t_seen": old.get("t_seen"), "bpm": None, "conns": None,
                          "alive": False, "error": "USB 장치 없음"}
    return {"t": now, "boards": boards}


class BoardWatch(threading.Thread):
    """감시자 안에서 hr_board_every_s 마다 보드를 읽어 data/hr_boards.json 을 쓴다. 짝 표가 없으면 아무 포트도 열지 않는다.
    load_cfg 는 감시자와 같은 설정 읽기(매번 새로 — 짝 표를 고치면 다음 바퀴부터 따른다). 예외는 삼키고 로그만(1시간에 한 번)."""

    def __init__(self, data: Path, load_cfg: Callable[[], object], log: Callable[[str], None] = lambda m: None,
                 scanner: Callable[..., dict] = scan, clock: Callable[[], float] = time.time):
        super().__init__(daemon=True, name="hr-board-watch")
        self.data, self.load_cfg, self.log, self.scanner, self.clock = Path(data), load_cfg, log, scanner, clock
        self._stop = threading.Event()
        self._said: dict[str, float] = {}
        self._last_n: tuple | None = None

    def stop(self) -> None:
        self._stop.set()

    def _say(self, msg: str) -> None:
        head, now = msg.split(" — ")[0], time.time()
        if now - self._said.get(head, 0.0) >= 3600:
            self._said[head] = now
            self.log(msg)

    def once(self) -> float:
        """한 바퀴 — 다음 바퀴까지 쉴 초를 돌려준다."""
        from .control import read_json, write_json_atomic
        try:
            cfg = self.load_cfg()
        except Exception as e:
            self._say(f"심박 보드 읽기: 설정을 읽지 못함 — {type(e).__name__}: {e}")
            return 60.0
        th = thresholds(cfg)
        names = watched_names(getattr(cfg, "sets", None))
        if not names:
            return float(th["hr_board_every_s"])
        path = self.data / BOARDS_FILE
        doc = self.scanner(names, read_json(path), self.clock(), float(th["hr_board_read_s"]))
        if doc.get("error"):
            self._say(f"심박 보드 읽기 실패 — {doc['error']}")
        elif not write_json_atomic(path, doc):
            self._say(f"심박 보드 읽기: {BOARDS_FILE} 를 쓰지 못함 — 결과판이 읽는 중이었을 수 있다(다음 바퀴에 다시)")
        alive = sum(1 for b in (doc.get("boards") or {}).values() if b.get("alive"))
        n = (alive, len(names))
        if n != self._last_n:
            self._last_n = n
            self.log(f"심박 보드 {alive}/{len(names)} 응답")
        return float(th["hr_board_every_s"])

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                wait = self.once()
            except Exception as e:
                self._say(f"심박 보드 읽기 중 예외 — {type(e).__name__}: {e}")
                wait = 60.0
            self._stop.wait(wait)


# ---------- 판정 (순수) ----------

def board_state(name: str, doc: dict | None, now_t: float, stale_s: float) -> tuple[str, dict | None]:
    """짝 보드의 상태 — unknown(읽은 기록이 없거나 오래됨) · off(USB 에 없음 · 오래 안 보임) · unread(USB 는 있는데 상태 줄 없음)
    · free(켜짐 · 연결 0) · joined(켜짐 · 연결 1 이상). 돌려주는 것 = (상태, 보드 칸)."""
    t = _num(doc.get("t")) if isinstance(doc, dict) else None
    if t is None or now_t - t > stale_s:
        return "unknown", None
    boards = doc.get("boards") if isinstance(doc.get("boards"), dict) else {}
    b = boards.get(name)
    if not isinstance(b, dict):
        return "unknown", None
    seen = _num(b.get("t_seen"))
    if b.get("alive") and seen is not None and now_t - seen <= stale_s:
        return ("joined" if (_num(b.get("conns")) or 0) >= 1 else "free"), b
    if b.get("port"):
        return "unread", b
    return "off", b


def times(c, now: float) -> tuple[float | None, float]:
    """라이브 수신기의 셀 하나(CellLive) → (라이브가 이어진 초 up_s, 심박이 연속 0 인 초 hr0_s). 심박이 있으면 hr0_s = 0.
    hr0_s 는 '라이브가 다시 이어진 뒤'부터 센다 — 재부팅 직후의 0 이 그 앞의 0 과 이어 붙지 않게."""
    up = getattr(c, "t_up", 0.0) or 0.0
    up_s = now - up if up else None
    if c.hr > 0:
        return up_s, 0.0
    since = max(getattr(c, "t_hr", 0.0) or 0.0, up or c.t)
    return up_s, max(0.0, now - since)


def judge(pairs: dict[int, dict], now_doc: dict | None, boards_doc: dict | None, now_t: float, th: dict, gap_s: float) -> dict:
    """세트 하나의 심박 요약 — 신호등 '심박' 차선과 결과판이 쓴다.
      ok      심박 > 0 인 짝 셀 수
      lost    hr_zero_s 넘게 연속 0 인 셀 [{serial, board, no, hub, port, cause, why, conns, zero_s}]
      wait    0 이지만 아직 판정하지 않는 셀 (짧음 · 재부팅 직후 hr_rejoin_grace_s · 엔진이 시간을 안 적음)
      skip    라이브가 끊겼거나 대기 모드 — 그건 '셀 수신' 차선의 일이다
    추출(EXTRACT) 중에는 셀이 블루투스를 놓으므로 판정하지 않는다(extract=True)."""
    now_doc = now_doc if isinstance(now_doc, dict) else {}
    cells = {c.get("serial"): c for c in now_doc.get("cells") or [] if isinstance(c, dict)}
    out = {"paired": len(pairs), "ok": 0, "lost": [], "wait": [], "skip": [], "extract": now_doc.get("phase") == "EXTRACT",
           "timed": any("hr0_s" in c for c in cells.values())}
    for s, p in sorted(pairs.items()):
        c = cells.get(s)
        age = _num(c.get("age")) if c else None
        if c is None or age is None or age > gap_s or c.get("waiting"):
            out["skip"].append(s)
            continue
        if (_num(c.get("hr")) or 0) > 0:
            out["ok"] += 1
            continue
        if out["extract"]:
            out["wait"].append(s)
            continue
        up, zero = _num(c.get("up_s")), _num(c.get("hr0_s"))
        if (up is not None and up < th["hr_rejoin_grace_s"]) or zero is None or zero < th["hr_zero_s"]:
            out["wait"].append(s)
            continue
        state, b = board_state(p["board"], boards_doc, now_t, th["hr_board_stale_s"])
        code = CAUSE_OF_STATE[state]
        out["lost"].append({"serial": s, "board": p["board"], "no": p.get("no"), "hub": p.get("hub"), "port": p.get("port"),
                            "cause": code, "why": CAUSE[code], "conns": (b or {}).get("conns"), "zero_s": round(zero)})
    return out


def repair_due(pairs: dict[int, dict], cells: dict[int, dict], boards_doc: dict | None, now_t: float,
               last_try: dict[int, float], th: dict, phase: str | None, gate_busy: bool, gap_s: float) -> list[int]:
    """자동 재연결할 셀 — 아래가 모두 맞을 때만 (엔진 cycle._watch_hr).
      · 설정이 켜져 있다(hr_repair) · 추출(EXTRACT) 중이 아니다 · 다른 세트가 추출 문을 잡고 있지 않다(gate_busy)
      · 셀 라이브가 들리고 대기 모드가 아니며 심박 0 이 hr_repair_after_s 넘게 이어졌다
      · 짝 보드가 켜져 있고 연결이 0 이다(free) — 보드가 꺼졌거나 다른 기기가 붙어 있으면 이름을 다시 보내도 소용없다
      · 그 셀에 마지막으로 보낸 지 hr_repair_every_s 가 지났다
    cells = {시리얼: {hr, age, waiting, hr0_s}} (now.json 의 셀 칸과 같은 꼴)."""
    if not th.get("hr_repair") or phase == "EXTRACT" or gate_busy:
        return []
    due = []
    for s, p in sorted(pairs.items()):
        c = cells.get(s)
        if not c or (_num(c.get("age")) or 0) > gap_s or c.get("waiting") or (_num(c.get("hr")) or 0) > 0:
            continue
        if (_num(c.get("hr0_s")) or 0) < th["hr_repair_after_s"]:
            continue
        if board_state(p["board"], boards_doc, now_t, th["hr_board_stale_s"])[0] != "free":
            continue
        if now_t - last_try.get(s, float("-inf")) < th["hr_repair_every_s"]:
            continue
        due.append(s)
    return due


def hubs(sets, boards_doc: dict | None, now_t: float, stale_s: float) -> dict:
    """허브별 보드 — {허브: {total, alive, boards: [{no, port, board, serial, set, state, conns, bpm}]}}. 결과판 '시험실 구성'의 재료.
    alive = 상태 줄을 받는 보드(free · joined). 허브가 비어 있는 짝은 '?' 허브로 묶는다."""
    out: dict[str, dict] = {}
    for serial, p in sorted(all_pairs(sets).items()):
        state, b = board_state(p["board"], boards_doc, now_t, stale_s)
        h = out.setdefault(p["hub"] or "?", {"total": 0, "alive": 0, "boards": []})
        h["total"] += 1
        h["alive"] += state in ("free", "joined")
        h["boards"].append({"no": p.get("no"), "port": p.get("port"), "board": p["board"], "serial": serial, "set": p.get("set"),
                            "state": state, "conns": (b or {}).get("conns"), "bpm": (b or {}).get("bpm")})
    for h in out.values():
        h["boards"].sort(key=lambda x: (_num(x["port"]) if _num(x["port"]) is not None else 1e9, x["serial"]))
    return dict(sorted(out.items()))
