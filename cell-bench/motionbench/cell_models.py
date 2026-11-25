"""셀 기종 · 펌웨어 확인 — QC 판정 전에 '이 셀의 데이터를 우리가 아는 형식으로 읽는가'를 본다(2026-11-25 사용자 결정).

왜: 실시간 데이터 해독기(cellbench/protocol.py parse_gps)는 모든 셀을 Y 셀 펌웨어 형식으로 읽는다(위성 수 = byte 48 >> 3).
불량 시료(셀 번호 1)는 다른 세대 CLBY-2A(펌웨어 0.99.42)라 byte 48 을 다르게 적어, 셀 기록에 위성 13개로 적힌 회차가 실시간으로는 1개로 읽혔다.
그 잘못 읽은 값이 GNSS-STATIC 기준 0.2 의 '위성 수 비율' 항목의 유일한 근거였다.

표 둘 — criteria/cell_models.json:
  formats: 확인한 (기종, 펌웨어 버전)마다 한 줄. 버전 범위로 묶지 않는다. 줄마다 확인한 칸(FIELDS)과 근거(저장소 · 커밋 · 파일 행).
  cells:   셀 번호 → 기종 · 펌웨어 · 근거(받은 셀 기록 파일의 시작 메시지). tools/qc_tests/cell_models_fill.py 가 채운다.
판정할 수 있는 셀 = cells 에 있고, 그 (기종, 펌웨어)가 formats 에 있고, FIELDS 가 모두 확인된 셀. 아니면 '판정 불가(기종 확인 필요)' —
시험은 같이 돌려 기록은 남기되, 그 셀의 값은 다른 셀의 기준 계산에 섞지 않는다(motionbench/qc_gate.py).

시험 전에 TCP 로 기종을 따로 묻지 않는다 — 접속은 늘 0x26(재부팅)으로 끝나고, CLBY-2A 는 그 뒤 측정으로 돌아오지 않는다(2026-11-24 실측).
이미 하는 상태 묻기(저장량 확인)에서 받은 인사말의 기종 · 펌웨어 앞자리(hello_mismatch)만 등록부와 대조한다.
cell_types.json(motionbench/celltype.py)은 24시간 엔진의 세트 종류(X · Y) 판단용 '기대' 표이고, 이 등록부는 셀마다 '기록에서 읽은' 기종 · 펌웨어다.
파일 읽기 말고는 순수 함수다 — 장비에 닿지 않는다.
"""
from __future__ import annotations

import json
import struct
from functools import reduce
from operator import xor
from pathlib import Path

FILE = Path(__file__).resolve().parent / "criteria" / "cell_models.json"
FIELDS = ("fix", "sats", "cn0", "ftg")     # GNSS-STATIC 판정이 쓰는 칸 — 실시간 byte 47 · 48 · 49 와 셀 기록 GPS 메시지
NEED_MODEL = "기종 확인 필요"
# 셀 번호를 안 쓴 셀 — 셀끼리 구별할 수 없어 거르기 파일에 적지 않는다(같은 번호의 다른 셀 기록과 섞인다).
# 1 은 2026-11-24 시료(CLBY-2A)에서 실측, 0 · 0xFFFF 는 빈 메모리 값(추정).
NO_SERIAL = frozenset({0, 1, 0xFFFF})

# 셀 기록(FTG)의 시작 메시지 — 'COACH' · 형 4 · 길이 79 · 내용 · 체크섬(형 · 길이 · 내용의 XOR).
# 내용 앞 13바이트: 셀 번호 u16(셀 기록 판 V2 는 큰 끝 — 해석 도구 ohcoach-cell-tools StartMessage) · 기종 글자 2 · 2 · 2 · 펌웨어 주 · 부 · 펌웨어 3바이트(V2 = 자리마다 한 바이트)
# 펌웨어 쪽 근거: cell-y4-ESP32-S3-firmware v0.99.71-all main/encoder.c 53행 encode_hw_info_start.
COACH, START_TYPE, START_LEN = b"COACH", 0x04, 79
_START = struct.Struct("<H2s2s2sBB3s")


def load(path: Path | None = None) -> dict:
    return json.loads(Path(path or FILE).read_text(encoding="utf-8"))


def starts(data: bytes) -> list[dict]:
    """셀 기록 바이트 → 체크섬이 맞는 시작 메시지마다 {serial, model, fw, at}. 셀이 켜질 때마다 하나씩 쌓인다."""
    out, i, head = [], 0, COACH + bytes([START_TYPE, START_LEN])
    while (i := data.find(head, i)) >= 0:
        body = data[i + 5:i + 7 + START_LEN]                 # 형 · 길이 · 내용
        end = i + 7 + START_LEN
        if end < len(data) and reduce(xor, body, 0) == data[end]:
            ser, a, b, c, _maj, _min, fw = _START.unpack_from(data, i + 7)
            try:
                model = f"{(a + b).decode('ascii')}-{c.decode('ascii')}"
            except UnicodeDecodeError:
                model = None
            out.append({"serial": struct.unpack(">H", struct.pack("<H", ser))[0], "model": model,
                        "fw": ".".join(str(x) for x in fw), "at": i})
        i += len(head)
    return out


def file_info(path: Path) -> dict | None:
    """셀 기록 파일 하나 → {serial, model, fw, starts, kinds}. serial · model · fw 는 마지막 시작 메시지(가장 최근에 켜진 때) 값이고,
    kinds 는 파일 안에 나온 (기종, 펌웨어) 가지 수다(펌웨어를 중간에 바꾸면 2 이상). 시작 메시지가 없으면 None."""
    ss = starts(Path(path).read_bytes())
    if not ss:
        return None
    last = ss[-1]
    return {"serial": last["serial"], "model": last["model"], "fw": last["fw"], "starts": len(ss),
            "kinds": sorted({(s["serial"], s["model"], s["fw"]) for s in ss})}


def fmt(table: dict, model: str | None, fw: str | None) -> dict | None:
    """알려진 형식 표에서 (기종, 펌웨어)가 정확히 같은 줄. 버전 범위로 찾지 않는다."""
    return next((f for f in table.get("formats") or [] if f.get("model") == model and f.get("fw") == fw), None)


def check(serial: int, table: dict) -> tuple[bool, str]:
    """셀 하나를 판정할 수 있나 → (판정 가능, 근거 한 줄). 판정할 수 없으면 근거가 '기종 확인 필요 — …' 로 시작한다."""
    if serial in NO_SERIAL:
        return False, f"{NEED_MODEL} — 셀 번호를 안 쓴 셀(번호 {serial})이라 등록부로 가를 수 없다"
    c = (table.get("cells") or {}).get(str(serial))
    if not c:
        return False, f"{NEED_MODEL} — 기종 등록부에 없는 셀(받은 셀 기록으로 등록: tools/qc_tests/cell_models_fill.py)"
    f = fmt(table, c.get("model"), c.get("fw"))
    if f is None:
        return False, f"{NEED_MODEL} — {c.get('model')} {c.get('fw')} 은 알려진 형식 표에 없다(비교 절차를 거친 뒤 표에 더한다)"
    if miss := [k for k in FIELDS if not ((f.get("fields") or {}).get(k) or {}).get("checked")]:
        return False, f"{NEED_MODEL} — {c.get('model')} {c.get('fw')} 의 {'·'.join(miss)} 칸을 확인하지 않았다"
    return True, f"{c['model']} {c['fw']}"


def unjudgeable(serials, table: dict | None = None) -> dict[int, str]:
    """판정할 수 없는 셀 → {셀: 근거}. table 이 없으면 기준 파일을 읽는다."""
    table = load() if table is None else table
    return {s: why for s in serials for ok, why in [check(s, table)] if not ok}


def hello_mismatch(serial: int, hello: dict | None, table: dict) -> str | None:
    """상태 묻기에서 받은 인사말(cellbench.protocol.parse_hello)과 등록부가 어긋나면 그 이유, 맞거나 대조할 것이 없으면 None.
    인사말의 펌웨어는 '0.99' 까지라 등록부 펌웨어의 앞 두 자리와만 견준다."""
    c = (table.get("cells") or {}).get(str(serial))
    if not hello or not c:
        return None
    if hello.get("model") != c.get("model"):
        return f"인사말 기종 {hello.get('model')} ≠ 등록부 {c.get('model')}"
    if ".".join(str(c.get("fw") or "").split(".")[:2]) != hello.get("fw"):
        return f"인사말 펌웨어 {hello.get('fw')} ≠ 등록부 {c.get('fw')}"
    return None
