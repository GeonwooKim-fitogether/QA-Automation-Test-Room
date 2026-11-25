"""셀 기종 · 펌웨어 확인(motionbench/cell_models.py, 2026-11-25) — 장비 · 실제 셀 기록 없이 돈다.
불량 시료(셀 번호 1)는 Y 셀(CLBY-4B)이 아니라 CLBY-2A 였는데, 실시간 해독기가 모든 셀을 Y 형식으로 읽어 위성 수를 잘못 냈다.
그래서 판정 전에 '셀 번호 → 기종 · 펌웨어'(등록부)와 '확인한 (기종, 펌웨어)의 형식'(알려진 형식 표)을 보고, 없으면 판정 불가로 둔다."""
from __future__ import annotations

import json
import struct
import sys
from functools import reduce
from operator import xor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "qc_tests"))
from cellbench import protocol as P  # noqa: E402
from motionbench import cell_models as M  # noqa: E402

# 2026-11-24 실측 인사말(data/ftg_full_*/result.json 의 hello) — Y 셀 10679 와 시료(셀 번호 1)
HELLO_Y = bytes.fromhex("4649544f102729b70010434c42593442006300030000000000000000000000000000000000000000000000000160")
HELLO_2A = bytes.fromhex("4649544f102700010010434c4259324100630001000000000000000000000000000000000000000000000000017c")


def start_frame(serial: int, model: str, fw: tuple[int, int, int]) -> bytes:
    """셀 기록의 시작 메시지 한 개 — 'COACH' 4 79 내용 체크섬. 셀 번호는 셀 기록 판 V2 처럼 큰 끝으로 적는다."""
    a, b, c = model[:2].encode(), model[2:4].encode(), model[4:6].encode()
    body = struct.pack("<H2s2s2sBB3s", struct.unpack("<H", struct.pack(">H", serial))[0], a, b, c, fw[0], fw[1], bytes(fw))
    body = body.ljust(79, b"\0")
    head = bytes([4, 79])
    return b"COACH" + head + body + bytes([reduce(xor, head + body, 0)]) + b"\r\n"


def table(cells: dict) -> dict:
    t = M.load()
    return {**t, "cells": cells}


def test_hello_carries_model_and_fw_major_minor():
    assert P.parse_hello(HELLO_Y) == {"serial": 10679, "model": "CLBY-4B", "fw": "0.99"}
    assert P.parse_hello(HELLO_2A) == {"serial": 1, "model": "CLBY-2A", "fw": "0.99"}
    bad = bytearray(HELLO_Y); bad[45] ^= 1
    assert P.parse_hello(bytes(bad)) is None and P.parse_hello(HELLO_Y[:40]) is None   # 체크섬 · 길이가 틀리면 읽지 않는다


def test_start_messages_give_model_and_full_fw(tmp_path):
    data = b"\0" * 10 + start_frame(10679, "CLBY4B", (0, 99, 66)) + b"x" * 100 + start_frame(10679, "CLBY4B", (0, 99, 71))
    ss = M.starts(data)
    assert [(s["serial"], s["model"], s["fw"]) for s in ss] == [(10679, "CLBY-4B", "0.99.66"), (10679, "CLBY-4B", "0.99.71")]
    broken = bytearray(start_frame(10680, "CLBY4B", (0, 99, 71))); broken[20] ^= 0xFF
    assert M.starts(bytes(broken)) == []                                              # 체크섬이 틀린 메시지는 버린다
    f = tmp_path / "ftg_10679_20261124_201746.bin"; f.write_bytes(data)
    info = M.file_info(f)
    assert (info["serial"], info["model"], info["fw"], info["starts"], len(info["kinds"])) == (10679, "CLBY-4B", "0.99.71", 2, 2)   # 마지막 값


def test_check_needs_registry_and_an_exact_known_format():
    t = table({"10679": {"model": "CLBY-4B", "fw": "0.99.71"}, "10680": {"model": "CLBY-4B", "fw": "0.99.73"},
               "11570": {"model": "CLBX-6B", "fw": "0.99.68"}, "34351": {"model": "CLBX-5E", "fw": "0.99.55"}})
    assert M.check(10679, t) == (True, "CLBY-4B 0.99.71") and M.check(11570, t)[0]
    ok, why = M.check(10680, t)
    assert not ok and "0.99.73 은 알려진 형식 표에 없다" in why                        # 버전 범위로 묶지 않는다 — 새 버전은 비교 절차 뒤에
    assert not M.check(34351, t)[0] and not M.check(10681, t)[0]
    ok, why = M.check(1, t)
    assert not ok and why.startswith("기종 확인 필요 — 셀 번호를 안 쓴 셀")
    half = {**t, "formats": [dict(t["formats"][0], fields={**t["formats"][0]["fields"], "sats": {"checked": False}})]}
    ok, why = M.check(10679, half)
    assert not ok and "sats 칸을 확인하지 않았다" in why
    assert set(M.unjudgeable([10679, 10680, 1], t)) == {10680, 1}


def test_known_formats_have_evidence_for_every_checked_field():
    """알려진 형식 표의 줄마다 근거(저장소 · 커밋)가 있고, 판정에 쓰는 칸이 모두 확인돼 있다. CLBY-2A 는 표에 없다."""
    t = M.load()
    for f in t["formats"]:
        assert "cell-y4-ESP32-S3-firmware" in f["source"] and all(f["fields"][k]["checked"] for k in M.FIELDS)
    assert {(f["model"], f["fw"]) for f in t["formats"]} == {("CLBY-4B", "0.99.71"), ("CLBY-4B", "0.99.68"), ("CLBX-6B", "0.99.68")}
    assert all(c["model"] != "CLBY-2A" for c in t["cells"].values()) and t["unnumbered"][0]["model"] == "CLBY-2A"
    assert M.check(10679, t)[0] and M.check(11570, t)[0]


def test_hello_mismatch_against_the_registry():
    t = table({"10679": {"model": "CLBY-4B", "fw": "0.99.71"}})
    assert M.hello_mismatch(10679, P.parse_hello(HELLO_Y), t) is None
    assert M.hello_mismatch(10679, {"serial": 10679, "model": "CLBY-2A", "fw": "0.99"}, t) == "인사말 기종 CLBY-2A ≠ 등록부 CLBY-4B"
    assert M.hello_mismatch(10679, {"serial": 10679, "model": "CLBY-4B", "fw": "1.00"}, t) == "인사말 펌웨어 1.00 ≠ 등록부 0.99.71"
    assert M.hello_mismatch(10681, P.parse_hello(HELLO_Y), t) is None and M.hello_mismatch(10679, None, t) is None


def test_fill_tool_skips_unnumbered_and_name_mismatch(tmp_path, capsys):
    import cell_models_fill as F
    d = tmp_path / "data" / "ftg_y_20261124-201744"; d.mkdir(parents=True)
    (d / "ftg_10679_20261124_201746.bin").write_bytes(start_frame(10679, "CLBY4B", (0, 99, 71)))
    (d / "ftg_10680_20261124_201746.bin").write_bytes(start_frame(10699, "CLBY4B", (0, 99, 71)))   # 이름과 내용이 다르다
    (d / "ftg_ip239_20261124_105712.bin").write_bytes(start_frame(1, "CLBY2A", (0, 99, 42)))
    tab = tmp_path / "models.json"
    tab.write_text(json.dumps({**M.load(), "cells": {}}, ensure_ascii=False), encoding="utf-8")
    assert F.main([str(d), "--table", str(tab), "--write"]) == 0
    out = capsys.readouterr().out
    cells = M.load(tab)["cells"]
    assert cells["10679"] == {"model": "CLBY-4B", "fw": "0.99.71", "source": "ftg_y_20261124-201744/ftg_10679_20261124_201746.bin", "date": "2026-11-24"}
    assert "10699" not in cells and "1" not in cells
    assert "파일 이름 셀 10680 ≠ 시작 메시지 셀 10699" in out and "셀 번호를 안 쓴 셀(번호 1) CLBY-2A 0.99.42" in out
