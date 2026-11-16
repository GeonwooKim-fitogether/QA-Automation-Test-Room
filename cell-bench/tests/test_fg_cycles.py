"""방전 용량(Q70 · FullCap · 평균 방전 전류) — 계산(cellbench/ftglog.py) · 도구(tools/fg_cycles.py) · 결과판(<fg> 블록) · 따라가기(cellbench/fg_follow.py).

사용시간 대신 쓰는 주 지표라, 정의가 지시(2026-11-14)와 한 글자도 어긋나지 않는지와 결과판 JS 가 Python 과 같은 값을 내는지를 본다.
실데이터 검사는 이 시험대의 fg_cycles.csv 가 있을 때만 돈다(CELLBENCH_REAL_DATA 로 경로를 줄 수 있다).
"""
import csv
import gzip
import json
import os
import re
import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import fg_cycles as tool                # noqa: E402
from cellbench import ftglog           # noqa: E402
from cellbench.fg_follow import FgFollow, batches, cloud_row  # noqa: E402
from test_board_gauge import CHROME, SRC, block, need_chrome  # noqa: E402


def st(n, dt_ms=10_000, ma=-140.0, soc0=100.0, soc1=30.0, cap0=750.0, t0=0):
    """방전 표본 n 개 — 잔량이 soc0 → soc1 로 고르게 내려간다. (sys_ms, repcap_raw, repsoc_raw, vcell_raw, cur_raw)"""
    out = []
    for i in range(n):
        soc = soc0 + (soc1 - soc0) * i / (n - 1)
        out.append((t0 + i * dt_ms, int(cap0 * soc / 100 / 0.1), int(round(soc * 256)), int(3.9 / 0.078125e-3), int(round(ma / 0.03125))))
    return out


def test_discharge_integrates_by_sys_ms_not_fixed_step():
    r = ftglog.discharge(st(1261))                            # 10초 × 1260 = 3.5 h · 140 mA → 490 mAh
    assert r["n_rec"] == 1261
    assert r["dis_mah"] == pytest.approx(140 * 3.5, rel=1e-3)
    assert r["q70_mah"] == pytest.approx(r["dis_mah"], rel=2e-3)   # 100 → 30 % 그대로라 환산이 같다
    assert r["mean_ma"] == pytest.approx(140.0)
    assert r["dis_h"] == pytest.approx(3.5, rel=1e-3)
    assert r["fullcap_mah"] == pytest.approx(750, abs=1)
    assert r["soc_hi"] == 100.0 and r["soc_lo"] == 30.0
    r20 = ftglog.discharge(st(1261, dt_ms=20_000))            # 간격이 20초면 실제로 두 배 — 고정값을 가정하지 않는다
    assert r20["dis_mah"] == pytest.approx(2 * r["dis_mah"], rel=1e-3)


def test_discharge_gaps_reboots_and_short():
    s = st(500)
    gap = s[:250] + [(t + 600_000, *x) for t, *x in s[250:]]  # 10분 비었다 → 30초로 자른다
    assert ftglog.discharge(gap)["dis_h"] == pytest.approx((499 * 10 + 20) / 3600, rel=1e-3)
    boot = s[:250] + [(t - s[250][0], *x) for t, *x in s[250:]]   # 재부팅 — sys_ms 가 0 부터 → 그 한 칸은 10초
    assert ftglog.discharge(boot)["dis_h"] == pytest.approx(499 * 10 / 3600, rel=1e-3)
    short = ftglog.discharge(st(99))
    assert short["n_rec"] == 99 and short["q70_mah"] == "" and short["dis_mah"] == ""
    charge = ftglog.discharge(st(500, ma=+500))               # 충전 · 대기 표본은 방전이 아니다
    assert charge["q70_mah"] == ""
    half = ftglog.discharge(st(800, soc0=100, soc1=65))       # 70 %p 폭으로 환산
    assert half["q70_mah"] == pytest.approx(half["dis_mah"] * 70 / 35, rel=2e-3)


def row(c, s, q, hi=99.5, lo=31.0, fc=None, ma=140.0, h=3.5):
    return {"cycle": str(c), "serial": str(s), "n_rec": "2000", "mean_ma": str(ma), "dis_mah": str(q), "q70_mah": "" if q is None else str(q),
            "fullcap_mah": str(fc if fc is not None else q * 1.45 if q else ""), "soc_hi": str(hi), "soc_lo": str(lo), "mean_v": "3.9", "dis_h": str(h)}


def sample_rows():
    """세트 1 모양 — 셀 6대(11741 은 제외 셀), 사이클 1~25. 사이클 22 는 비고, 사이클 23 은 유효 셀이 모자람."""
    rows = []
    for c in range(1, 26):
        for k, s in enumerate((11733, 11734, 11735, 11736, 11737, 11741)):
            q = 540 + k - (c > 20) * 10 + (s == 11741) * 200
            if c == 23 and k < 4:
                rows.append(row(c, s, q, hi=96.0))             # 시작 96 % — 무효
            else:
                rows.append(row(c, s, q, ma=140 - (c > 20) * 5))
    return rows


def test_set_series_rules():
    ser = ftglog.fg_set_series(sample_rows(), 1, skip_cycles={22})
    assert 22 not in ser and 23 not in ser                    # 비고 · 유효 셀 모자람
    assert ser[5]["n"] == 5                                   # 11741 은 빠진다
    assert ser[5]["q70"] == 542                               # 540~544 의 중앙값 — 11741(740)이 끼면 543
    assert ftglog.fg_baseline(ser, "q70") == 542
    assert ser[21]["q70"] == 532 and ser[21]["ma"] == 135
    assert not ftglog.fg_valid(row(1, 1, 500, lo=46)) and ftglog.fg_valid(row(1, 1, 500, hi=98, lo=45))


def _js(cases: dict) -> dict:
    from test_board_gauge import run_js
    return run_js(cases, prelude="\n" + block("fg") + "\nconst API={cloud:false};const has=v=>v!==''&&v!=null&&isFinite(+v);const curSet=()=>1;\n")


@need_chrome
def test_board_matches_python_rules():
    rows = sample_rows()
    C = [{"cycle": str(c), "note": "hold" if c == 22 else ""} for c in range(1, 26)]
    r = _js({"F": f"(()=>{{const F=fgCalc({json.dumps(rows)},{json.dumps(C)},1);return {{ser:F.ser,base:F.base,now:F.now,chg:F.chg,drop:F.drop,ready:F.ready}};}})()",
             "cloudNone": "fgCalc('notable',[],1).why", "empty": "fgCalc([],[],1).why", "noapi": "fgCalc('noapi',[],1).why",
             "ex": "FGX"})
    ser = ftglog.fg_set_series(rows, 1, skip_cycles={22})
    js = {x["c"]: x for x in r["F"]["ser"]}
    assert sorted(js) == sorted(ser)
    for c, v in ser.items():
        assert js[c]["q70"] == pytest.approx(v["q70"]) and js[c]["ma"] == pytest.approx(v["ma"]) and js[c]["fullcap"] == pytest.approx(v["fullcap"])
    assert r["F"]["base"]["q70"] == pytest.approx(ftglog.fg_baseline(ser, "q70"))
    assert [d[0] for d in r["F"]["drop"]] == [22, 23] and r["F"]["drop"][0][1] == "비고 hold"
    last10 = list(ser)[-10:]
    assert r["F"]["now"]["q70"] == pytest.approx(sum(ser[c]["q70"] for c in last10) / 10)
    assert "표가 아직 없다" in r["cloudNone"] and "fg_cycles.csv" in r["empty"] and "옛 판" in r["noapi"]
    assert {int(s): w for s, w in r["ex"]["1"].items()} == ftglog.FG_EXCLUDE[1]     # 결과판과 Python 의 제외 목록이 같다


def test_board_wiring():
    assert "fg:async(k)=>{const r=await fetch('/api/fg'" in SRC                         # 로컬
    assert "bench_fg_cycle?" in SRC                                                    # 클라우드
    assert "lazy?API.fg(cs).catch(()=>'fail'):null" in SRC and "fg:ggKeep(S.fg,fgr,lazy,same)" in SRC
    assert "방전 용량 Q70 · 기준 대비" in SRC and "평균 방전 전류" in SRC and "평균 작동시간 · 보조" in SRC
    assert SRC.count("부하 전류가 변하면 달라지는 값") >= 2                              # 작동시간 카드와 셀 × 사이클 머리
    assert 'id="fgCharts"' in SRC and "renderFgCard();" in SRC
    dep = (ROOT / "deploy" / "board" / "index.html").read_text(encoding="utf-8")
    assert dep == SRC                                                                   # 클라우드 사본도 같은 판


def test_migration_matches_columns():
    mig = next((ROOT / "supabase" / "migrations").glob("*_bench_fg_cycle.sql")).read_text(encoding="utf-8")
    for col in ftglog.FG_COLUMNS:
        assert re.search(rf"^\s+{col}\s", mig, re.M), col
    assert "primary key (bench_id, set_id, cycle, serial)" in mig and "for select to anon, authenticated" in mig
    assert "insert" not in mig.lower().split("create policy")[1]                        # 쓰기 정책 없음 — PC(service_role)만 쓴다


def _ftg(path: Path, status):
    buf = bytearray()
    for sys_ms, rc, rs, vc, cur in status:
        pl = struct.pack("<IIIbH", sys_ms, 0, 0, 0x16, 0x0002) + struct.pack("<HHHh", rc, rs, vc, cur)
        body = bytes([0x07, len(pl)]) + pl
        x = 0
        for b in body:
            x ^= b
        buf += b"FITO" + body + bytes([x]) + b"\r\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = bytes(buf)
    path.write_bytes(gzip.compress(data) if path.suffix == ".gz" else data)


def _bench(tmp: Path, cycles=(1, 2, 3), note=None):
    for c in cycles:
        _ftg(tmp / "ftg" / f"{c:04d}" / f"ftg_11733_20261101_000000.bin.gz", st(1261))      # 압축된 것
        _ftg(tmp / "ftg" / f"{c:04d}" / f"ftg_11734_20261101_000000.bin", st(1261, ma=-120))
    with open(tmp / "cycles.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["cycle", "note"])
        for c in cycles[:-1]:                                  # 마지막 사이클은 아직 안 끝났다
            w.writerow([c, note or ""])


def test_tool_incremental_and_unfinished(tmp_path):
    _bench(tmp_path)
    out, seen = tool.run(tmp_path, 1, log=lambda m: None)
    assert seen == [1, 2]                                      # 3 은 cycles.csv 에 줄이 없다
    rows = list(csv.DictReader(open(out, encoding="utf-8")))
    assert list(rows[0]) == ftglog.FG_COLUMNS and len(rows) == 4
    assert float(rows[0]["q70_mah"]) == pytest.approx(490, rel=3e-3) and float(rows[1]["mean_ma"]) == pytest.approx(120)
    assert tool.run(tmp_path, 1, log=lambda m: None)[1] == []  # 증분 — 다시 세지 않는다


class FakeCloud(FgFollow):
    """클라우드 흉내 — 올린 행을 (세트, 사이클, 셀)로 기억한다. fail_post=n 이면 n 번째 POST 에서 끊긴다(한 번만)."""

    def __init__(self, data, sets=(1,), have=(), fail=None, fail_post=None, calls=None):
        run = lambda d, k, log: (calls.append(k) if calls is not None else None, tool.run(d, k, log=log))
        super().__init__(data, lambda: list(sets), [run], url="https://x", key="k", bench_id="b", log=lambda m: None)
        self.store = {tuple(x) for x in have}
        self.fail, self.fail_post, self.posts = fail, fail_post, []

    def _req(self, method, path, body=None, prefer=None, timeout=15.0):
        if self.fail:
            raise OSError(self.fail)
        if method == "GET":
            rows = [{"set_id": k, "cycle": c} for k, c, _ in sorted(self.store)]
            off = int(path.split("offset=")[1])
            return rows[off:off + 1000]
        self.posts.append(body)
        if self.fail_post is not None and len(self.posts) == self.fail_post:
            self.fail_post = None
            raise OSError("연결 끊김")
        self.store |= {(r["set_id"], r["cycle"], r["serial"]) for r in body}


def test_follow_computes_and_uploads_only_new(tmp_path):
    _bench(tmp_path)
    fc = FakeCloud(tmp_path, have=[(1, 1, 11733), (1, 1, 11734)])
    assert fc.step() == {1: 2}                                 # 사이클 1 은 이미 클라우드에 다 있다 — 2 의 두 셀만
    assert {r["cycle"] for r in fc.posts[0]} == {2} and fc.posts[0][0]["bench_id"] == "b" and fc.posts[0][0]["set_id"] == 1
    assert fc.step() == {1: 0}                                 # 다시 올리지 않는다
    part = FakeCloud(tmp_path, have=[(1, 1, 11733)])           # 사이클 1 이 한 셀만 있다 — 통째로 다시
    assert part.step() == {1: 4} and len(part.store) == 4
    bad = FakeCloud(tmp_path, fail="HTTP 404")
    bad.step()
    assert bad.status()["state"] == "fail" and "404" in bad.status()["error"]
    off = FgFollow(tmp_path, lambda: [1], [], url="", key="", bench_id="", log=lambda m: None)
    assert off.step() == {} and off.status()["state"] == "off"


def test_follow_cloud_failure_does_not_stop_local_sets(tmp_path):
    """QA #67-1 — 표가 없어(404) 올리기가 실패해도 세트 2 의 로컬 집계는 회차마다 돈다."""
    _bench(tmp_path)
    _bench(tmp_path / "set2")
    calls = []
    fc = FakeCloud(tmp_path, sets=(1, 2), fail="HTTP 404", calls=calls)
    for _ in range(3):
        fc.step()
    assert calls == [1, 2] * 3
    assert (tmp_path / "set2" / "fg_cycles.csv").exists()
    assert fc.status()["state"] == "fail"


def test_follow_partial_upload_recovers_whole_cycle(tmp_path):
    """QA #67-2 — 묶음은 사이클 경계에서 자르고, 끊기면 다음 회차에 모자란 사이클을 통째로 다시 보낸다."""
    rows = [{"cycle": str(c), "serial": str(s)} for c in range(1, 23) for s in range(24)]
    parts = batches(rows, 500)
    assert all(len({r["cycle"] for r in p} & {r["cycle"] for r in q}) == 0 for i, p in enumerate(parts) for q in parts[i + 1:])
    assert sum(map(len, parts)) == 528 and len(parts) == 2 and len(parts[0]) == 504
    _bench(tmp_path, cycles=tuple(range(1, 24)))
    fc = FakeCloud(tmp_path)
    import cellbench.fg_follow as ff
    old = ff.BATCH
    ff.BATCH = 10                                              # 사이클 하나 = 2행 → 묶음 여러 개
    try:
        fc.fail_post = 2
        fc.step()                                              # 둘째 묶음에서 끊긴다
        assert fc.status()["state"] == "fail"
        fc.step()
    finally:
        ff.BATCH = old
    assert len(fc.store) == 44 and fc.status()["state"] == "ok"   # 22사이클 × 2셀 — 빠진 셀 없음


def test_cloud_row_types():
    r = cloud_row("b", 2, {"cycle": "5", "serial": "11594", "n_rec": "7411", "mean_ma": "77.8", "dis_mah": "", "q70_mah": "387.1",
                           "fullcap_mah": "559", "soc_hi": "100", "soc_lo": "30.2", "mean_v": "3.9", "dis_h": "4.9"})
    assert r["cycle"] == 5 and r["n_rec"] == 7411 and r["dis_mah"] is None and r["q70_mah"] == 387.1 and r["set_id"] == 2
    json.dumps(r, allow_nan=False)


def test_serve_board_endpoint_and_follow_flag():
    src = (ROOT / "serve_board.py").read_text(encoding="utf-8")
    assert 'if u.path == "/api/fg":' in src and 'read_csv(d / "fg_cycles.csv")' in src
    assert "--no-follow" in src and "FG_FOLLOW = fg_follower(Path(a.data))" in src and 'u.path == "/api/fg/status"' in src


def _real() -> Path | None:
    d = Path(os.environ.get("CELLBENCH_REAL_DATA") or ROOT / "data")
    return d if (d / "fg_cycles.csv").exists() else None


@pytest.mark.skipif(_real() is None, reason="이 시험대의 fg_cycles.csv 가 없음")
def test_real_baseline_matches_ops_session():
    """운영 세션 실측(2026-11-14, 사이클 6~20): Y Q70 549 · FullCap 798 · 전류 142.7 / X Q70 387 · FullCap 559 · 전류 77.8 — ±1 %."""
    d = _real()
    for k, q, fc, ma in ((1, 549, 798, 142.7), (2, 387, 559, 77.8)):
        sd = ftglog.set_dir(d, k)
        ser = ftglog.fg_set_series(tool.read_rows(sd / "fg_cycles.csv"), k, tool.noted_cycles(sd))
        assert ftglog.fg_baseline(ser, "q70") == pytest.approx(q, rel=0.01)
        assert ftglog.fg_baseline(ser, "fullcap") == pytest.approx(fc, rel=0.01)
        assert ftglog.fg_baseline(ser, "ma") == pytest.approx(ma, rel=0.01)
