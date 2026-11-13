"""FTG 안의 로그 프레임을 읽어 셀 배터리 IC(MAX17260, ModelGauge m5 EZ)의 학습 용량(FullCap)을 집계한다 — 순수 함수만.

엔진(run_cycle.py · cellbench/cycle.py)은 이 모듈을 쓰지 않는다. 쓰는 곳은 tools/gauge_from_ftg.py(보관된 FTG → gauge.csv)와 검사뿐이다.

FTG 로그 프레임(펌웨어 fito_log.c · fito_log_types.h):
  b"FITO" | 0x07 | len(1) | payload(len) | crc(1)        crc = 0x07 부터 payload 끝까지의 XOR
  payload = sys_ms u32 | utc_time u32 | utc_date u32 | module i8 | msg_id u16 | params     (머리 15바이트, 리틀엔디언)
연료 게이지(module 0x16) 상태 0x0002 의 params = repcap u16 | repsoc u16 | avgvcell u16 | avgcurrent i16.
LSB(Y4B/X6B, 외부 50 mΩ 센스 저항): 용량 0.1 mAh · 잔량 1/256 % · 전압 0.078125 mV.
학습 용량 FullCap ≈ repcap / (repsoc / 256 / 100) — IC 가 '지금 잔량 비율로 환산한 꽉 찬 용량'.
"""
from __future__ import annotations

import statistics
import struct
from collections import Counter
from pathlib import Path

HDR = b"FITO\x07"
HEAD_LEN = 15                 # payload 머리 — sys_ms · utc_time · utc_date · module · msg_id
MOD_SYSTEM, MOD_FUEL_GAUGE = 0x00, 0x16
FG_STATUS = 0x0002
FG_EVENTS = {0x0001: "BATTERY_LOW", 0x0003: "DEVNAME", 0x0005: "POR_DETECTED", 0x0006: "POR_NOT_DETECTED",
             0x0009: "INIT_COMPLETE", 0x000A: "DESIGNCAP_MISMATCH", 0x000B: "RESET_FAILED"}
SYS_EVENTS = {0x0001: "POWER_ON", 0x0002: "POWER_OFF", 0x0004: "RESET_REASON", 0x0027: "FW_VERSION"}
CAP_LSB_MAH = 0.1
PCT_LSB = 1 / 256.0
V_LSB_MV = 0.078125

# gauge.csv 의 열 — 결과판(/api/gauge → board/index.html <gauge> 블록)이 이 이름으로 읽는다
COLUMNS = ["cycle", "serial", "n", "fullcap_med", "fullcap_p10", "fullcap_p90", "boots", "por", "design_mAh", "design_fixed",
           "fw_version", "vmin_mV", "repsoc_first", "repsoc_last"]
RELEARN_BOOTS = 3             # 한 사이클에 부팅이 이만큼이면 IC 가 학습할 틈이 없다
DESIGN_SOC_MAX = 99.0         # 설계값 추정에 쓰는 부팅 직후 표본의 잔량 상한(%)
DESIGN_TOL_MAH = 1.0          # 중앙값이 설계값 ±1 mAh 면 '설계값 고정'(학습이 전혀 반영되지 않음)
STABLE_TOL = 0.01             # 안정화 끝 — 한 사이클의 세트 중앙값이 그 뒤 STABLE_WIN 회의 중앙값과 1 % 이내
STABLE_WIN = 3
SHORT_FRAC = 0.5              # 표본 수가 그 사이클 셀들의 중앙값의 이 비율 미만이면 '기록 짧음'


def frames(data: bytes):
    """체크섬이 맞고 머리가 온전한 로그 프레임만 (sys_ms, module, msg_id, params) 로 낸다.
    헤더 글자가 센서 자료 속에 우연히 나오거나 프레임이 끝에서 잘려 있어도 앞뒤 프레임은 그대로 읽힌다."""
    n, i = len(data), 0
    while True:
        i = data.find(HDR, i)
        if i < 0 or i + 6 > n:
            return
        ln = data[i + 5]
        p0, p1 = i + 6, i + 6 + ln
        if p1 >= n:                      # 끝에서 잘린 프레임(또는 길이가 큰 우연한 헤더) — 그 뒤의 짧은 프레임은 계속 찾는다
            i += 5
            continue
        x = 0
        for b in data[i + 4:p1]:
            x ^= b
        if x != data[p1] or ln < HEAD_LEN:
            i += 5                       # 체크섬 불일치(우연한 'FITO') · 머리가 모자란 프레임 — 다음 헤더부터
            continue
        pl = data[p0:p1]
        sys_ms = struct.unpack_from("<I", pl, 0)[0]
        module = struct.unpack_from("<b", pl, 12)[0]
        msg_id = struct.unpack_from("<H", pl, 13)[0]
        yield sys_ms, module, msg_id, pl[HEAD_LEN:]
        i = p1 + 1


def parse(data: bytes) -> tuple[list[tuple], list[tuple]]:
    """→ (상태 표본, 이벤트). 표본 = (sys_ms, repcap_raw, repsoc_raw, avgvcell_raw, avgcurrent_raw), 이벤트 = (sys_ms, 이름, params, 표본 위치).
    표본 위치 = 그 이벤트 시점까지 쌓인 표본 수 — sys_ms 는 부팅마다 0 부터 다시 세므로 파일 순서로 앞뒤를 가른다
    (한 파일은 앞 부팅의 꼬리 표본 몇 개로 시작한다)."""
    status, events = [], []
    for sys_ms, module, msg_id, par in frames(data):
        if module == MOD_FUEL_GAUGE:
            if msg_id == FG_STATUS:
                if len(par) >= 8:
                    rc, rs, vc, cur = struct.unpack_from("<HHHh", par, 0)
                    status.append((sys_ms, rc, rs, vc, cur))
            elif msg_id in FG_EVENTS:
                events.append((sys_ms, FG_EVENTS[msg_id], bytes(par), len(status)))
        elif module == MOD_SYSTEM and msg_id in SYS_EVENTS:
            events.append((sys_ms, SYS_EVENTS[msg_id], bytes(par), len(status)))
    return status, events


def fullcap_mah(repcap_raw: int, repsoc_raw: int) -> float | None:
    """학습 용량(mAh) — 잔량이 0 이면 나눌 수 없어 None."""
    if repsoc_raw <= 0:
        return None
    return repcap_raw * CAP_LSB_MAH / (repsoc_raw * PCT_LSB / 100.0)


def percentile(sorted_vals: list[float], q: float) -> float | None:
    if not sorted_vals:
        return None
    return sorted_vals[min(len(sorted_vals) - 1, int(q * len(sorted_vals)))]


def fw_version_text(par: bytes) -> str:
    """FW_VERSION 의 3바이트(major.minor.build) → '0.99.66'. 짧으면 16진 원문."""
    return ".".join(str(b) for b in par[:3]) if len(par) >= 3 else par.hex()


def design_from_boots(status: list[tuple], events: list[tuple]) -> int | None:
    """IC 가 다시 초기화된 직후(INIT_COMPLETE 뒤 첫 표본)의 FullCap 은 설계값(DesignCap) 그 자체다 — 그 값(정수 반올림)의 최빈값.
    실측: Y4B 750.0 ± 0.2, X6B 500 (2026-11-02). 초기화 기록이 없으면 None. 파일 순서(표본 위치)로 찾는다 — sys_ms 는 부팅마다 되돌아간다.
    만충(잔량 DESIGN_SOC_MAX 이상)에서 부팅한 표본은 쓰지 않는다 — 꽉 찬 채 POR 하면 IC 가 설계값이 아닌 값(세트 2 사이클 1 실측 511)을 잡는다."""
    firsts = []
    for _, name, _, k in events:
        if name == "INIT_COMPLETE" and k < len(status) and status[k][2] * PCT_LSB < DESIGN_SOC_MAX:
            v = fullcap_mah(status[k][1], status[k][2])
            if v is not None:
                firsts.append(int(round(v)))
    return Counter(firsts).most_common(1)[0][0] if firsts else None


def summarize(status: list[tuple], events: list[tuple], design: int | None = None) -> dict:
    """한 셀 · 한 사이클(FTG 파일 하나)의 집계 — gauge.csv 한 행(cycle · serial 제외). design 을 주지 않으면 부팅 직후 값에서 읽는다."""
    fc = sorted(v for v in (fullcap_mah(rc, rs) for _, rc, rs, _, _ in status) if v is not None)
    names = [e[1] for e in events]
    fw = next((fw_version_text(e[2]) for e in events if e[1] == "FW_VERSION"), "")
    if design is None:
        design = design_from_boots(status, events)
    med = statistics.median(fc) if fc else None
    fixed = "" if med is None or design is None else int(abs(med - design) <= DESIGN_TOL_MAH)
    r1 = lambda v: "" if v is None else round(v, 1)
    return {"n": len(status), "fullcap_med": r1(med), "fullcap_p10": r1(percentile(fc, 0.1)), "fullcap_p90": r1(percentile(fc, 0.9)),
            "boots": names.count("POWER_ON"), "por": names.count("POR_DETECTED"),
            "design_mAh": "" if design is None else design, "design_fixed": fixed, "fw_version": fw,
            "vmin_mV": round(min(s[3] for s in status) * V_LSB_MV) if status else "",
            "repsoc_first": round(status[0][2] * PCT_LSB, 1) if status else "",
            "repsoc_last": round(status[-1][2] * PCT_LSB, 1) if status else ""}


def apply_design(row: dict, design: int | None) -> dict:
    """한 행의 설계값을 바꿔 쓰고 '설계값 고정' 을 다시 판정한다 — 세트의 설계값은 하나이므로 도구가 사이클의 최빈값으로 맞춘다
    (충전이 거의 끝난 채 부팅한 셀은 첫 표본이 이미 조금 학습돼 759 · 512 처럼 읽힌다)."""
    med = _num(row.get("fullcap_med"))
    row["design_mAh"] = "" if design is None else design
    row["design_fixed"] = "" if med is None or design is None else int(abs(med - design) <= DESIGN_TOL_MAH)
    return row


def cycle_design(rows: list[dict]) -> int | None:
    """사이클 안 셀들의 설계값 최빈값."""
    vals = [int(r["design_mAh"]) for r in rows if r.get("design_mAh") not in ("", None)]
    return Counter(vals).most_common(1)[0][0] if vals else None


def _num(v):
    try:
        return None if v in ("", None) else float(v)
    except (TypeError, ValueError):
        return None


def relearn_suspect(row: dict) -> bool:
    """'IC 재학습 의심' — 한 사이클에 부팅 RELEARN_BOOTS 회 이상이거나 FullCap 중앙값이 설계값에 고정. 결과판의 ggSuspect 와 같은 규칙."""
    boots = _num(row.get("boots"))
    return (boots is not None and boots >= RELEARN_BOOTS) or _num(row.get("design_fixed")) == 1


def suspect_kinds(rows: list[dict]) -> dict[tuple[int, int], str]:
    """규칙(relearn_suspect)에 걸린 행을 셋으로 가른다 — 결과판의 ggKind 와 같은 규칙. 키 (cycle, serial).
    'pre'(학습 전) = 사이클 1 — IC 가 아직 한 번도 학습하지 않았다. 'short'(기록 짧음) = 표본 수가 그 사이클 셀들의 중앙값의 SHORT_FRAC 미만 —
    학습 뒤 방전 구간이 모자라 중앙값이 설계값 쪽으로 끌린다(세트 1 사이클 26 의 11735, 표본 236개). 나머지가 'relearn'(IC 재학습 의심)."""
    ns: dict[int, list[float]] = {}
    for r in rows:
        n = _num(r.get("n"))
        if n is not None:
            ns.setdefault(int(r["cycle"]), []).append(n)
    nmed = {c: statistics.median(v) for c, v in ns.items()}
    out = {}
    for r in rows:
        if not relearn_suspect(r):
            continue
        c, n = int(r["cycle"]), _num(r.get("n"))
        out[(c, int(r["serial"]))] = "pre" if c == 1 else "short" if n is not None and nmed.get(c) and n < nmed[c] * SHORT_FRAC else "relearn"
    return out


def set_medians(rows: list[dict]) -> dict[int, float]:
    """사이클 → 세트 FullCap 중앙값(셀별 중앙값들의 중앙값). 표본이 없는 행은 뺀다. 사이클 오름차순."""
    by: dict[int, list[float]] = {}
    for r in rows:
        v = _num(r.get("fullcap_med"))
        if v is not None:
            by.setdefault(int(r["cycle"]), []).append(v)
    return {c: statistics.median(by[c]) for c in sorted(by)}


def record_hole(cycles) -> int | None:
    """사이클 1 부터 이어진 기록이 처음 끊기는 사이클 — 그 뒤에 기록이 더 있을 때만(아직 쌓이는 중이면 None).
    기록이 사이클 25 부터면 1, 1~3 · 5~ 면 4. 결과판의 ggHole 과 같은 규칙."""
    cs = set(cycles)
    k = 1
    while k in cs:
        k += 1
    return k if any(c > k for c in cs) else None


def stable_end(meds: dict[int, float], tol: float = STABLE_TOL, win: int = STABLE_WIN) -> int | None:
    """안정화 끝(측정) — 세트 중앙값이 그 뒤 win 회의 중앙값과 tol 이내인 첫 사이클.
    사이클 1 부터 빈틈없이 이어진 기록만 본다 — 앞이 비어 있으면(예: --from 25 로만 뽑은 파일) 그 앞에서 이미 안정됐을 수 있어
    '첫 사이클' 을 잴 수 없다. 그때는 None(결과판은 옛 규칙 1~3 제외). 뒤 win 회가 아직 없어도 None.
    한 사이클의 흔들림(세트 1 의 5→6회 −1.2 %)에 끌리지 않게 뒤 구간을 중앙값으로 본다.
    실측(2026-11-02): 세트 1 737 · 733 · 765 · 790 · 803 · 793 · 803 · 802 → 5, 세트 2 502 · 538 · 556 · 559 · 559 · 564 → 3."""
    cs = []
    while len(cs) + 1 in meds:
        cs.append(len(cs) + 1)
    for i, c in enumerate(cs):
        nxt = cs[i + 1:i + 1 + win]
        if len(nxt) < win:
            return None
        m = meds[c]
        if m and abs(statistics.median(meds[k] for k in nxt) / m - 1) <= tol:
            return c
    return None


def set_dir(data: Path, k: int) -> Path:
    """세트 k 의 기록 폴더 — 1 은 data/, 2~ 는 data/set{k}/ (serve_board.set_folder 와 같은 규칙)."""
    return data if k == 1 else data / f"set{k}"
