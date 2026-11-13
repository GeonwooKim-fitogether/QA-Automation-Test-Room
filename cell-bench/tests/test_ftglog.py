"""셀 IC 학습 용량 — FTG 로그 프레임 파서(cellbench/ftglog.py) · 도구(tools/gauge_from_ftg.py) · 결과판 API(/api/gauge).

합성 바이트열로 프레임 경계 · 잘린 프레임 · 체크섬 · LSB 변환 · 집계 · 안정화 끝 규칙을 못 박는다. 실데이터 검사는 test_board_gauge.py 에.
"""
import csv
import json
import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from cellbench import ftglog                       # noqa: E402
import gauge_from_ftg as tool                      # noqa: E402
from test_register import server                   # noqa: E402,F401  (픽스처)


def frame(module: int, msg_id: int, params: bytes = b"", sys_ms: int = 1000, bad_crc: bool = False) -> bytes:
    payload = struct.pack("<IIIbH", sys_ms, 0, 0, module, msg_id) + params
    body = bytes([0x07, len(payload)]) + payload
    crc = 0
    for b in body:
        crc ^= b
    return b"FITO" + body + bytes([(crc ^ 0xFF) if bad_crc else crc])


def fg(repcap: int, repsoc_pct: float, mv: float = 3800, cur: int = -4500, sys_ms: int = 1000) -> bytes:
    return frame(0x16, 0x0002, struct.pack("<HHHh", repcap, int(repsoc_pct * 256), int(mv / 0.078125), cur), sys_ms)


BOOT = (frame(0x00, 0x0027, bytes([0, 99, 66]), 1933) + frame(0x00, 0x0004, b"\x03", 1940) + frame(0x00, 0x0001, b"", 1955)
        + frame(0x16, 0x0005, b"\xb3\xaa", 1975) + frame(0x16, 0x0009, b"", 2300))


def test_frames_boundaries_crc_and_truncation():
    good = fg(7500, 100.0, sys_ms=5)
    junk = b"\x00\x11FITO\x07\x05abc"                 # 센서 자료 속 우연한 'FITO' — 길이 5 는 머리(15)에 못 미친다
    bad = frame(0x16, 0x0002, struct.pack("<HHHh", 1, 12800, 48000, 0), 6, bad_crc=True)   # 체크섬이 틀린 프레임은 버린다
    data = junk + good + bad + fg(3750, 50.0, sys_ms=7) + good[:20]   # 끝은 잘린 프레임
    out = list(ftglog.frames(data))
    assert [(m, i, s) for s, m, i, _ in out] == [(0x16, 2, 5), (0x16, 2, 7)]
    # 프레임 안에 'FITO' 글자가 들어 있어도 경계는 길이로 넘어간다
    inner = frame(0x16, 0x0003, b"FITO\x07\x10", 9) + good
    assert [i for _, _, i, _ in ftglog.frames(inner)] == [3, 2]
    assert list(ftglog.frames(b"")) == [] and list(ftglog.frames(b"FITO\x07")) == []


def test_parse_and_lsb():
    status, events = ftglog.parse(BOOT + fg(7500, 100.0, 4330, 1500, 18000) + fg(2500, 33.3, 3755.0, -4700, 28000))
    assert [e[1] for e in events] == ["FW_VERSION", "RESET_REASON", "POWER_ON", "POR_DETECTED", "INIT_COMPLETE"]
    assert [e[3] for e in events] == [0] * 5                                   # 이벤트는 표본 0개 뒤(파일 순서)
    assert len(status) == 2 and status[0][1] == 7500 and status[1][4] == -4700
    assert ftglog.fullcap_mah(7500, 25600) == pytest.approx(750.0)            # 0.1 mAh × 7500 / 100 %
    assert ftglog.fullcap_mah(2500, int(33.3 * 256)) == pytest.approx(250 / 0.333, rel=1e-3)
    assert ftglog.fullcap_mah(10, 0) is None
    assert ftglog.fw_version_text(bytes([0, 99, 66])) == "0.99.66" and ftglog.fw_version_text(b"\x01") == "01"


def test_summarize_design_from_boot_and_file_order():
    # 앞 부팅의 꼬리(이미 학습된 792.9, sys_ms 큰 값) → 부팅 → 설계값 750 구간 → 학습 → 방전 792.7 (실제 파일 모양)
    tail = fg(2617, 33.0, 3755, -840, 46_365_756)
    charge = b"".join(fg(int(7500 * p / 100), p, 3900, 7000, 18000 + i * 10000) for i, p in enumerate((46.9, 60.0, 74.8, 90.0)))
    dis = b"".join(fg(int(7927 * p / 100), p, 3900, -4500, 9_000_000 + i * 10000) for i, p in enumerate((97.7, 83.2, 68.1, 52.6, 40.0, 34.4)))
    status, events = ftglog.parse(tail + BOOT + charge + dis)
    assert ftglog.design_from_boots(status, events) == 750                    # sys_ms 순이면 792.9 를 집었을 것
    r = ftglog.summarize(status, events)
    assert r["n"] == 11 and r["boots"] == 1 and r["por"] == 1 and r["fw_version"] == "0.99.66" and r["design_mAh"] == 750
    assert r["fullcap_med"] == pytest.approx(792.7, abs=0.5) and r["fullcap_p10"] == pytest.approx(750, abs=1) and r["design_fixed"] == 0
    assert r["repsoc_first"] == 33.0 and r["repsoc_last"] == 34.4 and r["vmin_mV"] == 3755
    # 설계값에 고정된 셀(부팅 6회 · 학습 없음)
    many = b"".join(BOOT + fg(int(7500 * p / 100), p, sys_ms=20000) for p in (40, 45, 50, 55, 60, 65))
    s2, e2 = ftglog.parse(many)
    r2 = ftglog.summarize(s2, e2)
    assert r2["boots"] == 6 and r2["design_fixed"] == 1 and ftglog.relearn_suspect(r2)
    assert ftglog.relearn_suspect({"boots": "3", "design_fixed": "0"}) and not ftglog.relearn_suspect({"boots": "2", "design_fixed": "0"})
    assert not ftglog.relearn_suspect({"boots": "", "design_fixed": ""})
    # 만충에서 부팅한 표본은 설계값 추정에 쓰지 않는다(세트 2 사이클 1 의 511)
    assert ftglog.design_from_boots(*ftglog.parse(BOOT + fg(5110, 100.0, sys_ms=18000) + fg(5000, 90.0, sys_ms=28000))) is None
    # 표본이 없는 파일 — 빈 칸, 터지지 않는다
    r3 = ftglog.summarize(*ftglog.parse(BOOT))
    assert r3["n"] == 0 and r3["fullcap_med"] == "" and r3["design_fixed"] == "" and r3["boots"] == 1
    # 설계값을 직접 주면 그것으로 판정 · apply_design 은 다시 판정
    assert ftglog.summarize(s2, e2, design=500)["design_fixed"] == 0
    assert ftglog.apply_design(dict(r2), 500)["design_fixed"] == 0 and ftglog.apply_design(dict(r2), None)["design_fixed"] == ""
    assert ftglog.cycle_design([{"design_mAh": 750}, {"design_mAh": 759}, {"design_mAh": 750}, {"design_mAh": ""}]) == 750


def test_stable_end_rule_matches_measured_sets():
    # 2026-11-02 실측 세트 중앙값 — 세트 1 은 5회 뒤, 세트 2 는 3회 뒤 안정 (5→6회 −1.2 % 흔들림은 뒤 3회 중앙값으로 넘긴다)
    s1 = dict(enumerate([737.2, 733.4, 765.0, 789.7, 802.6, 793.1, 803.2, 802.1, 798.8, 801.8], 1))
    s2 = dict(enumerate([501.9, 537.9, 556.1, 558.8, 559.3, 564.0, 563.0, 560.2], 1))
    assert ftglog.stable_end(s1) == 5 and ftglog.stable_end(s2) == 3
    assert ftglog.stable_end({c: s1[c] for c in range(1, 9)}) == 5          # 5 뒤 세 회(6 · 7 · 8)가 있어야 — 7회까지면 아직 None
    assert ftglog.stable_end({c: s1[c] for c in range(1, 8)}) is None
    assert ftglog.stable_end({}) is None and ftglog.stable_end({1: 700, 2: 710, 3: 720, 4: 730}) is None   # 계속 오르면 없음
    assert ftglog.stable_end({1: 700, 3: 701, 5: 702, 9: 700}) is None      # 사이클 2 가 비었다 — 1 부터 빈틈없는 기록으로만 잰다(QA 1)
    assert ftglog.stable_end({c: s1[c - 24] for c in range(25, 33)}) is None # --from 25 로만 뽑은 파일 — 25 를 '안정' 으로 잡지 않는다
    assert ftglog.stable_end({**{c: s1[c] for c in range(1, 9)}, 12: 800}) == 5   # 판정 뒤의 빈 사이클은 상관없다
    rows = [{"cycle": "1", "fullcap_med": "740"}, {"cycle": "1", "fullcap_med": "730"}, {"cycle": "1", "fullcap_med": ""}, {"cycle": "2", "fullcap_med": "750"}]
    assert ftglog.set_medians(rows) == {1: 735.0, 2: 750.0}


def _ftg_tree(root: Path, set_dir: str, cycles: dict[int, dict[int, bytes]]):
    for c, cells in cycles.items():
        d = root / set_dir / "ftg" / f"{c:04d}"
        d.mkdir(parents=True)
        for s, data in cells.items():
            (d / f"ftg_{s}_20261010_140244.bin").write_bytes(data)


def test_tool_incremental_force_and_finished_gate(tmp_path):
    learned = lambda mah, soc=40.0: BOOT + fg(int(7500 * soc / 100), soc, sys_ms=18000) + b"".join(fg(int(mah * 10 * p / 100), p, sys_ms=100000 + i) for i, p in enumerate((90.0, 70.0, 50.0)))
    fixed6 = b"".join(BOOT + fg(int(7500 * p / 100), p, sys_ms=20000) for p in (40, 45, 50, 55, 60, 65))
    _ftg_tree(tmp_path, "", {1: {11733: learned(737), 11734: learned(740)}, 2: {11733: learned(765), 11734: fixed6}})
    (tmp_path / "cycles.csv").write_text("cycle,start,note\n1,x,\n2,y,\n", encoding="utf-8")
    out = tmp_path / "g.csv"
    logs = []
    p, seen = tool.run(tmp_path, 1, None, None, out, False, None, log=logs.append)
    assert p == out and seen == [1, 2]
    rows = list(csv.DictReader(out.open(encoding="utf-8-sig")))
    assert [r["cycle"] + ":" + r["serial"] for r in rows] == ["1:11733", "1:11734", "2:11733", "2:11734"]
    assert list(rows[0].keys()) == ftglog.COLUMNS
    by = {(r["cycle"], r["serial"]): r for r in rows}
    assert by[("1", "11733")]["fullcap_med"] == "737.0" and by[("2", "11734")]["boots"] == "6" and by[("2", "11734")]["design_fixed"] == "1"
    assert {r["design_mAh"] for r in rows} == {"750"}
    # 증분 — 사이클 3 을 더하면 그것만 센다. cycles.csv 에 줄이 없는(끝나지 않은) 사이클 4 는 세지 않는다
    _ftg_tree(tmp_path, "", {3: {11733: learned(790)}, 4: {11733: learned(800)}})
    (tmp_path / "cycles.csv").write_text("cycle,start,note\n1,x,\n2,y,\n3,z,\n", encoding="utf-8")
    _, seen = tool.run(tmp_path, 1, None, None, out, False, None, log=logs.append)
    assert seen == [3]
    rows = list(csv.DictReader(out.open(encoding="utf-8-sig")))
    assert [r["cycle"] for r in rows] == ["1", "1", "2", "2", "3"]
    _, seen = tool.run(tmp_path, 1, None, None, out, False, None, log=logs.append)
    assert seen == [] and "이미 있다" in logs[-1]
    _, seen = tool.run(tmp_path, 1, 2, 2, out, True, None, log=logs.append)      # --force 는 그 범위만 다시, 다른 행은 그대로
    assert seen == [2] and len(list(csv.DictReader(out.open(encoding="utf-8-sig")))) == 5
    # 세트 2 는 data/set2/ — 기본 출력은 그 폴더의 gauge.csv. --design 을 주면 그 값으로 판정
    _ftg_tree(tmp_path, "set2", {1: {11594: learned(502)}})
    p2, seen = tool.run(tmp_path, 2, None, None, None, False, 500, log=logs.append)
    assert p2 == tmp_path / "set2" / "gauge.csv" and seen == [1]
    r = list(csv.DictReader(p2.open(encoding="utf-8-sig")))[0]
    assert r["design_mAh"] == "500" and r["design_fixed"] == "0"
    assert tool.main(["--data", str(tmp_path / "nowhere")]) == 2


def test_api_gauge_returns_rows_or_empty(tmp_path, server):
    call, data, _, _ = server()
    assert call("/api/gauge")[1] == [] and call("/api/gauge?set=2")[1] == []
    (data / "gauge.csv").write_text("cycle,serial,n,fullcap_med\n1,11733,2308,736.5\n", encoding="utf-8")
    (data / "set2").mkdir()
    (data / "set2" / "gauge.csv").write_text("cycle,serial,n,fullcap_med\n1,11594,7412,503.5\n", encoding="utf-8")
    assert call("/api/gauge")[1] == [{"cycle": "1", "serial": "11733", "n": "2308", "fullcap_med": "736.5"}]
    assert call("/api/gauge?set=2")[1][0]["serial"] == "11594" and call("/api/gauge?set=9")[0] == 400


def test_engine_does_not_import_ftglog():
    """모듈 추가가 엔진 동작을 바꾸지 않는다 — 엔진 쪽 파일은 ftglog 를 들여오지 않는다."""
    for p in [ROOT / "run_cycle.py", ROOT / "supervise.py", *(ROOT / "cellbench").glob("*.py")]:
        if p.name != "ftglog.py":
            assert "ftglog" not in p.read_text(encoding="utf-8"), p
    assert json.dumps(ftglog.COLUMNS)   # 열 이름은 결과판 <gauge> 블록이 읽는 이름
    for col in ("fullcap_med", "boots", "design_fixed", "design_mAh"):
        assert col in ftglog.COLUMNS


def test_record_hole_and_suspect_kinds():
    """[QA 1 · 4] 기록이 처음 끊기는 사이클(뒤에 기록이 더 있을 때만) · 의심 행을 학습 전 / 기록 짧음 / 재학습 의심으로 가른다 — 결과판 ggHole · ggCalc 와 같은 규칙."""
    assert ftglog.record_hole([25, 26, 27]) == 1 and ftglog.record_hole([1, 2, 3, 5, 6]) == 4
    assert ftglog.record_hole([1, 2, 3]) is None and ftglog.record_hole([]) is None
    row = lambda c, s, n=2000, boots=1, fixed=0: {"cycle": str(c), "serial": str(s), "n": str(n), "boots": str(boots), "design_fixed": str(fixed)}
    rows = [row(1, 7, boots=4), row(1, 8), row(26, 7), row(26, 8), row(26, 9, n=236, fixed=1), row(28, 7, boots=6), row(28, 8)]
    assert ftglog.suspect_kinds(rows) == {(1, 7): "pre", (26, 9): "short", (28, 7): "relearn"}


def _learned(mah, soc=40.0):
    return BOOT + fg(int(7500 * soc / 100), soc, sys_ms=18000) + b"".join(fg(int(mah * 10 * p / 100), p, sys_ms=100000 + i) for i, p in enumerate((90.0, 70.0, 50.0)))


def test_tool_messages_range_open_and_half_file(tmp_path):
    """[QA 1 · 5] 뒤집힌 범위 · 끝나지 않은 사이클만 · 사이클 1 없는 파일 경고 · 세트 중앙값은 진짜 중앙값(짝수면 가운데 둘의 평균)."""
    _ftg_tree(tmp_path, "", {1: {11733: _learned(730), 11734: _learned(740)}, 2: {11733: _learned(760)}, 3: {11733: _learned(770)}})
    (tmp_path / "cycles.csv").write_text("cycle,start,note\n1,x,\n2,y,\n", encoding="utf-8")          # 사이클 3 은 아직 끝나지 않았다
    out, logs = tmp_path / "g.csv", []
    _, seen = tool.run(tmp_path, 1, 5, 2, out, False, None, log=logs.append)
    assert seen == [] and "범위가 뒤집혔다" in logs[-1] and "--force" not in logs[-1] and not out.exists()
    _, seen = tool.run(tmp_path, 1, 3, 3, out, False, None, log=logs.append)
    assert seen == [] and "아직 끝나지 않은 사이클 3" in logs[-1] and "--force" not in logs[-1]
    _, seen = tool.run(tmp_path, 1, 7, 9, out, False, None, log=logs.append)
    assert seen == [] and "FTG 폴더가 없다" in logs[-1] and "--force" not in logs[-1]
    logs.clear()
    _, seen = tool.run(tmp_path, 1, 2, None, out, False, None, log=logs.append)                     # 사이클 2 만 — 사이클 1 이 없는 파일
    assert seen == [2] and any("아직 끝나지 않은 사이클 3" in m and "건너뛴다" in m for m in logs)
    assert logs[-1].startswith("경고:") and "사이클 2 부터라" in logs[-1] and "--from 1" in logs[-1]
    logs.clear()
    _, seen = tool.run(tmp_path, 1, 1, None, out, False, None, log=logs.append)                     # --from 1 로 채우면 경고가 사라진다
    assert seen == [1] and not any(m.startswith("경고:") for m in logs)
    assert any(m.startswith("사이클 1: 셀 2대 · 세트 중앙값 735 mAh") for m in logs), logs        # 730 · 740 → 735(옛 코드는 740)


def test_tool_reads_gzipped_archive(tmp_path):
    """보관하며 gzip 으로 줄인 FTG(.bin.gz)도 읽는다. 같은 셀의 .bin 이 함께 있으면(압축 중) 원본을, 잘린 .gz 는 그 셀만 건너뛴다."""
    import gzip
    _ftg_tree(tmp_path, "", {1: {11733: _learned(730)}})
    d = tmp_path / "ftg" / "0001"
    (d / "ftg_11734_20261010_140244.bin.gz").write_bytes(gzip.compress(_learned(740)))
    (d / "ftg_11733_20261010_140244.bin.gz").write_bytes(gzip.compress(_learned(999))[:40])   # 압축 중 — 원본 .bin 을 쓴다
    (d / "ftg_11735_20261010_140244.bin.gz").write_bytes(gzip.compress(_learned(750))[:40])   # 잘린 .gz 만 있다 — 건너뛴다
    rows = tool.scan_cycle(1, d, None)
    assert [(r["serial"], r["fullcap_med"]) for r in rows] == [(11733, 730.0), (11734, 740.0)]
