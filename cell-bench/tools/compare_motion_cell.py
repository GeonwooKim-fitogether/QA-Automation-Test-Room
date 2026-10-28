"""셀 vs 모터 첫 일관성 비교 v0 — dynamic-bench 기록 파일만 읽는다 — 장비 · 셀에 붙지 않는다.

  .venv/Scripts/python.exe cell-bench/tools/compare_motion_cell.py --motion cell-bench/data/motion_<시각>.csv
        [--live cell-bench/data/live_raw/<시각>.bin] [--imu imu_11746.csv ...] [--out 이름]

정렬은 두 길이다.
  (b) 라이브 speed_history(10 Hz) 와 모터 |속도| 의 상호상관 — 셀마다. 셀이 기록 내내 fix N 이면 'fix 없음'으로 적고 비교하지 않는다.
  (a) 추출 IMU 가속도 CSV 와 모터 가속도 — 파일마다. CSV 첫 칸 = 시각(숫자면 epoch 초, 글자면 ISO · 시간대가 없으면 UTC = FTG 의 time_utc),
      나머지 칸 = 가속도 축(이름에 acc 가 든 칸이 있으면 그 칸만) — 상관이 가장 큰 축을 고른다. 단위는 무엇이든 된다(배율을 맞춘다).
      FTG 파서는 이 저장소에 없다 — ohcoach-cell-tools 의 FtgParser(pandas 필요)로 datetime, acc_x, acc_y, acc_z 를 run 앞뒤만 내보낸다.
동기 창 = 모터가 움직인 첫 무리와 마지막 무리(5초 넘게 멈추면 다른 무리) — 둘이면 드리프트까지.
출력: <out>.md(요약 · 표 · 가정) · <out>.csv(셀 · IMU 파일 한 줄씩). 기본 out = 모터 CSV 옆 compare_<모터 파일 이름>.
"""
from __future__ import annotations

import argparse
import csv
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cellbench import proc  # noqa: E402
from cellbench.protocol import parse_gps  # noqa: E402
from motionbench import align as A  # noqa: E402
from motionbench import cellstats as S  # noqa: E402
from motionbench import celltype  # noqa: E402
from motionbench.live_tap import read_raw  # noqa: E402

PAD = 1.0          # 동기 창 앞뒤 여유 초
SEG_S = 300.0      # 시계 · 패킷 손실을 나눠 보는 덩어리 (5분) — 기획 long-run.md 2-4
FIXED = {"A", "D", "F", "R"}   # 위치를 잡은 fix — N(없음)이나 글자가 아닌 값의 위치 · 시각 · 속도는 쓰지 않는다


def _fixed(pk):
    """위치를 잡은 패킷만 — fix 가 잡힘이고 위도 · 경도가 0,0 이 아니다 (검토 2026-10-28: 0,0 이 끼면 수천 km 튄다)."""
    return [(x, g) for x, g in pk if g.fix in FIXED and (g.lat_deg, g.lon_deg) != (0.0, 0.0)]
IMU_DT = 0.01      # IMU 정렬 격자 초 — ponytail: IMU 표본률을 모른다. 100 Hz 보다 빠르면 솎아 보는 셈이다


def _t(s: str) -> float:
    try:
        return float(s)
    except ValueError:
        d = datetime.fromisoformat(s.strip())
        return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()


def read_imu(path) -> tuple[list[float], dict[str, list[float]]]:
    """IMU CSV → (시각 목록, {축 이름: 값 목록}), 시각 순."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        head, *rows = list(csv.reader(f))
    rows = sorted((_t(r[0]), r) for r in rows if r)
    axes = {}
    for j, name in enumerate(head[1:], 1):
        try:
            axes[name] = [float(r[j]) for _, r in rows]
        except (ValueError, IndexError):
            pass                                   # 숫자가 아닌 칸은 건너뛴다
    acc = {n: v for n, v in axes.items() if "acc" in n.lower()}
    return [x for x, _ in rows], acc or axes


def read_live(path, speed_fmt: str = "<H", newest_first: bool = False) -> dict[int, list]:
    """live_raw → {시리얼: [(수신 시각, Gps)] 시각 순}. 0x09 가 아닌 데이터그램(0x16 등)은 버린다."""
    cells = defaultdict(list)
    for t, d in read_raw(path):
        g = parse_gps(d, speed_fmt, newest_first)
        if g:
            cells[g.serial].append((t, g))
    return {s: sorted(v, key=lambda x: x[0]) for s, v in sorted(cells.items())}


def _win_cols(res: list[dict]) -> dict:
    cols = {}
    for name, x in zip("AB", [res[0], res[-1]] if len(res) > 1 else res):
        cols |= {f"lag_{name}_s": x["lag"], f"r_{name}": x["r"], f"width_{name}_s": x["width"], f"edge_{name}": x["edge"]}
    d = A.drift(res)
    return cols | {"drift_ppm": None if d is None else d * 1e6, "status": A.quality(res)}


def speed_row(serial: int, pk: list, t, p, wins, span, max_lag: float) -> dict:
    """셀 하나의 GPS 상태 + (fix 가 있으면) 속도 정렬 · speed_history 와 hspeed 비교."""
    gs = [g for _, g in pk]
    fixes = Counter(g.fix for g in gs)
    row = {"kind": "cell", "cell": serial, "packets": len(pk), "fix": " ".join(f"{k}:{n}" for k, n in sorted(fixes.items())),
           "sats": statistics.fmean(g.sats for g in gs), "sats_ios": statistics.fmean(g.sats_ios for g in gs),
           "cn0": statistics.fmean(g.cn0 for g in gs), "validity": " ".join(sorted({f"0x{g.validity:02X}" for g in gs})),
           "hist_peak": max((v for g in gs for v in g.speed_history if v < A.NO_VALUE), default=None)}
    if set(fixes) <= {"N"}:
        return row | {"status": "fix 없음"}
    if not wins:
        return row | {"status": "모터 정지"}
    ts, vs = A.speed_series([(x, g.speed_history) for x, g in pk])

    def speed(g):
        return [abs(v) for v in A.motor_speed(t, p, g)]
    res = A.offsets(speed, ts, vs, wins, A.HIST_DT, max_lag)
    row |= _win_cols(res)
    row |= {f"hist_{k}": v for k, v in A.compare(ts, vs, speed, res, span).items()}
    row |= {f"hspeed_{k}": v for k, v in A.compare([x for x, _ in pk], [g.hspeed_raw for g in gs], speed, res, span, fit="scale").items()}
    return row


def imu_row(path, t, p, wins, span, max_lag: float) -> dict:
    """IMU 파일 하나 — 축을 고르고(첫 창의 |r| 이 가장 큰 축) 정렬 · 가속도 비교."""
    it, axes = read_imu(path)
    row = {"kind": "imu", "file": Path(path).name, "imu_from": A.kst(it[0]) if it else None, "imu_to": A.kst(it[-1]) if it else None}
    if not it or not axes or not wins:
        return row | {"status": "IMU 표본 없음" if not it or not axes else "모터 정지"}

    def accel(g):
        return A.motor_accel(t, p, g)
    a0, b0 = wins[0]
    name = max(axes, key=lambda n: abs(A.find_offset(accel, it, axes[n], a0, b0, IMU_DT, max_lag, signed=True)["r"]))
    res = A.offsets(accel, it, axes[name], wins, IMU_DT, max_lag, signed=True)
    row |= {"axis": name} | _win_cols(res)
    return row | {f"acc_{k}": v for k, v in A.compare(it, axes[name], accel, res, span, fit="affine").items()}


def long_sections(cells: dict, t, p, cell_rows: list[dict]) -> list[str]:
    """위치(위도 · 경도) · 속도(움직일 때 vs 멈췄을 때) · 시계 · 패킷 손실 — 셀마다 cell_rows 에 칸을 더하고 보고 글을 돌려준다."""
    by = {r["cell"]: r for r in cell_rows}
    period = S.motor_period(t, p)
    vmax = max((abs(v) for v in A.motor_speed(t, p, A.grid(t[0], 0.1, int((t[-1] - t[0]) / 0.1)))), default=0.0)
    for serial, pk in cells.items():
        row = by[serial]
        fixed = _fixed(pk)
        inside = [(x, g) for x, g in fixed if t[0] <= x <= t[-1]]
        row["pos_n"] = len(inside)
        if period and inside:
            lat0 = statistics.median(g.lat_deg for _, g in inside)          # 기준점은 중앙값 — 첫 패킷 하나에 기대지 않는다
            lon0 = statistics.median(g.lon_deg for _, g in inside)
            xy = [S.to_mm(g.lat_deg, g.lon_deg, lat0, lon0) for _, g in inside]
            r = S.position_fit([x for x, _ in inside], [q[0] for q in xy], [q[1] for q in xy], t, p, period)
            row |= {f"pos_{k}": v for k, v in r.items()}
        ts, vs = A.speed_series([(x, g.speed_history) for x, g in pk])
        mv = [abs(v) if t[0] <= x <= t[-1] else None for x, v in zip(ts, A.motor_speed(t, p, ts))]
        moving = [c for c, m in zip(vs, mv) if m is not None and vmax and m >= 0.8 * vmax]
        still = [c for c, m, x in zip(vs, mv, ts) if (m is None and not t[0] - 60 < x < t[-1] + 60) or (m is not None and m < 1.0)]
        row |= {"speed_moving_med": statistics.median(moving) if moving else None, "speed_still_med": statistics.median(still) if still else None}
        hm = [(g.hspeed_raw, abs(v) if t[0] <= x <= t[-1] else None)
              for (x, g), v in zip(fixed, A.motor_speed(t, p, [x for x, _ in fixed]))]
        h_mov = [h for h, m in hm if m is not None and vmax and m >= 0.8 * vmax]
        h_sti = [h for (h, m), (x, _) in zip(hm, fixed) if (m is None and not t[0] - 60 < x < t[-1] + 60) or (m is not None and m < 1.0)]
        row |= {"hspeed_moving_med": statistics.median(h_mov) if h_mov else None, "hspeed_still_med": statistics.median(h_sti) if h_sti else None}
        clk = [(x, g.utc) for x, g in fixed if g.utc is not None]            # 위치를 못 잡은 패킷의 시각(1980년 등)은 쓰지 않는다
        row |= {f"clock_{k}": v for k, v in S.clock(*zip(*clk)).items()} if clk else {"clock_offset_ms": None, "clock_drift_ppm": None}
        row |= {f"loss_{k}": v for k, v in S.loss([x for x, _ in pk], [g.seq for _, g in pk]).items()}
    md = ["", "## 위치 (위도 · 경도)", ""]
    if not period:
        md.append("왕복이 없어(방향 바뀜 3번 미만) 위치 비교를 하지 않았다.")
    else:
        seen = [r for r in cell_rows if r.get("pos_seen") is True]
        judged = [r for r in cell_rows if r.get("pos_seen") is not None]
        md.append(f"왕복 주기 {period:.1f} 초. **보인다 {len(seen)}/{len(cell_rows)}대**"
                  + ("" if judged else f" — 판정 안 함: {S.BLOCK_S:.0f}초 덩어리가 {S.MIN_BLOCKS}개 미만") + ". "
                  f"판정: 왕복 한 번보다 느린 떠돎을 빼고 {S.BLOCK_S:.0f}초 덩어리마다 기울기(셀 mm / 모터 mm)를 구해, "
                  f"평균이 표준오차의 {S.SEEN_SIGMA:g}배를 넘으면 '보인다' `[제안값]`. 1이면 셀이 모터만큼 움직인 것이다.")
        md += ["", *_table(cell_rows, [("셀", "cell"), ("늦음 초", "pos_lag_s"), ("설명력 r²", "pos_r2"), ("덩어리", "pos_blocks"),
                                       ("기울기 k", "pos_k"), ("방향° (동=0, 북=90)", "pos_dir_deg"), ("표준오차", "pos_se"), ("보인다", "pos_seen")])]
    md += ["", "## 속도 — 움직일 때 vs 멈췄을 때", "",
           f"모터 최고 속도 {vmax:.1f} mm/s. 움직일 때 = 모터 |속도| ≥ 최고의 80 %, 멈췄을 때 = 1 mm/s 미만이거나 모터 기록 밖(앞뒤 60초 떨어진 곳). "
           "원래 속도 = 패킷 41–42 의 거르지 않은 GPS 속도 mm/s(펌웨어 gps.c gSpeed × 3.6 ÷ 3.6, 2 Hz, 위치를 잡은 패킷만). "
           "속도 이력 = 거른 값 — 펌웨어는 셀이 흔들린다고(IMU) 판단할 때만 채우고 아니면 0 을 넣는다(gps.c is_moving). '값 없음'(0xFFFD~0xFFFF)은 뺐다.", "",
           *_table(cell_rows, [("셀", "cell"), ("원래 속도 움직일 때", "hspeed_moving_med"), ("원래 속도 멈췄을 때", "hspeed_still_med"),
                               ("이력 움직일 때", "speed_moving_med"), ("이력 멈췄을 때", "speed_still_med")])]
    md += ["", "## 시계 · 패킷 (5분마다)", "",
           "시계 차 = PC 수신 시각 − 셀 GPS UTC(네트워크 지연 포함). 기울기 + 면 셀 시계가 점점 늦어진다. 손실 = 셀 순번(펌웨어 epoch)의 빈 칸.", "",
           *_table(cell_rows, [("셀", "cell"), ("시계 차 ms", "clock_offset_ms"), ("기울기 ppm", "clock_drift_ppm"), ("받은 것", "loss_got"),
                               ("빠진 것", "loss_lost"), ("손실 %", "loss_lost_pct"), ("재부팅", "loss_restarts")])]
    allt = sorted((x, serial, g) for serial, pk in cells.items() for x, g in pk)
    seg_rows = []
    for idx in S.segments([x for x, _, _ in allt], SEG_S):
        part = [allt[i] for i in idx]
        per = {}
        for x, serial, g in part:
            per.setdefault(serial, []).append((x, g))
        lost = [S.loss([x for x, _ in v], [g.seq for _, g in v]) for v in per.values()]
        offs = [S.clock(*zip(*[(x, g.utc) for x, g in _fixed(v) if g.utc is not None]))["offset_ms"] for v in per.values()
                if any(g.utc is not None for _, g in _fixed(v))]
        exp = sum(r["expected"] for r in lost)
        seg_rows.append({"from": A.kst(part[0][0])[11:19], "cells": len(per),
                         "lost_pct": 100.0 * sum(r["lost"] for r in lost) / exp if exp else None,
                         "offset_ms": statistics.median(offs) if offs else None})
    md += ["", *_table(seg_rows, [("구간 시작", "from"), ("셀", "cells"), ("손실 % (전체)", "lost_pct"), ("시계 차 중앙값 ms", "offset_ms")])]
    return md


def _f(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "예" if v else "아니오"
    if isinstance(v, float):
        return f"{v:.3f}" if abs(v) < 100 else f"{v:.0f}"
    return str(v)


def _table(rows: list[dict], cols: list[tuple[str, str]]) -> list[str]:
    out = ["| " + " | ".join(h for h, _ in cols) + " |", "|" + "---|" * len(cols)]
    return out + ["| " + " | ".join(_f(r.get(k)) for _, k in cols) + " |" for r in rows]


def report(a) -> tuple[str, list[dict]]:
    """인자 → (보고 MD 글, CSV 줄 목록). 파일을 읽기만 한다."""
    t, p = A.load_motion(a.motion)
    if len(t) < 2:
        return f"# 셀 vs 모터 일관성 비교 v0\n\n모터 기록 `{a.motion}` 에 위치가 있는 줄이 없다.\n", []
    spans = A.motion_spans(t, p)
    wins = [(max(t[0], x - PAD), min(t[-1], y + PAD)) for x, y in ([spans[0], spans[-1]] if len(spans) > 1 else spans)]
    span = (wins[0][0], wins[-1][1]) if wins else (t[0], t[-1])
    md = ["# 셀 vs 모터 일관성 비교 v0", "",
          f"- 모터 기록 `{a.motion}`: {A.kst(t[0])} ~ {A.kst(t[-1])} ({t[-1] - t[0]:.1f} 초, {len(t)} 줄). "
          "시각은 이 PC 의 time.time() 이라 셀 라이브 수신 시각과 같은 시계다.",
          f"- 모터가 움직인 무리 {len(spans)}개: " + (", ".join(f"{A.kst(x)[11:23]} 부터 {y - x:.1f} 초" for x, y in spans) or "없음")]
    if not wins:
        md.append("- **모터가 움직이지 않았다 — 맞출 동기 이동이 없어 정렬 · 비교를 하지 않았다.**")
    else:
        md.append(f"- 동기 창 {len(wins)}개(앞뒤 {PAD:.0f} 초 여유): " + ", ".join(f"{A.kst(x)[11:23]}~{A.kst(y)[11:23]}" for x, y in wins)
                  + ("" if len(wins) > 1 else " — 하나뿐이라 드리프트는 구하지 않는다"))
    rows: list[dict] = []
    if a.live:
        cells = read_live(a.live, a.speed_fmt, a.newest_first)
        judge = celltype.judge_set([(s, None) for s in cells])
        md.append(f"- 셀 라이브 `{a.live}`: 셀 {len(cells)}대 · 0x09 {sum(map(len, cells.values()))}개")
        pre = judge["preset"] or {}
        md.append("- 세트 종류(celltype · 등록부): " + (
            f"**{judge['kind']}** — " + ", ".join(f"{m} {n}대" for m, n in judge["models"].items())
            + f" · 도크 {pre['dock']} · 페이로드 {pre['payload_kg']} kg · 상한 {pre['vmax_mm_s']} mm/s, {pre['amax_mm_s2']} mm/s²"
            + f" · 기대 펌웨어 {pre['fw_expected'] or '미확정'} · 파서 {pre['parser'] or '미확정'}"
            if judge["ok"] else f"**시작 차단** — {judge['reason']}"))
        cell_rows = [speed_row(s, pk, t, p, wins, span, a.max_lag) for s, pk in cells.items()]
        rows += cell_rows
        order = Counter(A.history_order(x.speed_history, y.speed_history)
                        for pk in cells.values() for (_, x), (_, y) in zip(pk, pk[1:]))
        no_fix = [r["cell"] for r in cell_rows if r.get("status") == "fix 없음"]
        ok = [r for r in cell_rows if r.get("status") in ("정렬됨", "약함")]
        weak = sum(r["status"] == "약함" for r in ok)
        md += ["", "## 결론 — 속도 (라이브 speed_history · hspeed)", ""]
        if cells and len(no_fix) == len(cells):
            md.append(f"**GPS fix 없음 — {len(cells)}대 전부 기록 내내 fix 가 N 이다. 셀 속도와 모터 속도의 비교는 성립하지 않는다** "
                      "(실내라면 예상된 결과). 위성 수 · C/N0 · validity 는 아래 표에 그대로 남긴다.")
        elif ok:
            lags, rms = [r["lag_A_s"] for r in ok], [r["hist_rms"] for r in ok if r.get("hist_rms") is not None]
            md.append(f"정렬된 셀 {len(ok)}/{len(cells)}대" + (f"(그중 약함 {weak}대)" if weak else "")
                      + f" — 오프셋(셀 = 모터 + lag) 중앙값 {statistics.median(lags):.3f} 초"
                      f" (셀 사이 {min(lags):.3f} ~ {max(lags):.3f}), speed_history 오차 RMS 중앙값 "
                      + (f"{statistics.median(rms):.2f} mm/s." if rms else "— (비교 표본 없음)."))
        else:
            md.append(f"정렬된 셀 0/{len(cells)}대 — 상관 피크가 낮거나 탐색 끝에 걸렸다(아래 표의 r · edge). fix 없음 {len(no_fix)}대.")
        md += ["", *_table(cell_rows, [("셀", "cell"), ("패킷", "packets"), ("fix", "fix"), ("위성 >>3", "sats"), ("위성 iOS &0x1F", "sats_ios"),
                                       ("C/N0", "cn0"), ("validity", "validity"), ("이력 최대 mm/s", "hist_peak"), ("상태", "status")]),
               "", *_table([r for r in cell_rows if "lag_A_s" in r],
                           [("셀", "cell"), ("lag A 초", "lag_A_s"), ("r A", "r_A"), ("폭 A 초", "width_A_s"), ("끝 A", "edge_A"),
                            ("lag B 초", "lag_B_s"), ("r B", "r_B"), ("드리프트 ppm", "drift_ppm"), ("배율 k", "hist_k"),
                            ("오차 평균", "hist_mean"), ("RMS", "hist_rms"), ("p95", "hist_p95"), ("최대", "hist_max"),
                            ("hspeed k", "hspeed_k"), ("hspeed r", "hspeed_r"), ("hspeed RMS", "hspeed_rms")]),
               "", "오차는 mm/s, 셀 − 모터 |속도|. 배율 k = 셀/모터(원점 지나는 직선) — 1 근처면 단위 · 엔디안 가정이 맞다(256 근처면 엔디안이 반대). "
               "hspeed 는 단위를 몰라 k 로 맞춘 뒤의 오차다(1 원값 = 1/k mm/s).",
               f"- speed_history 순서 점검(연속 패킷 쌍): 지금 해석과 맞음 {order['as_is']} · 반대가 맞음 {order['reversed']} · "
               f"판정 불가 {order[None]}(값이 거의 한결같다 — 실내 0 등). 지금 해석: `{a.speed_fmt}` · "
               + ("0번이 최신" if a.newest_first else "끝이 최신") + " — 반대가 많으면 "
               + ("--newest-first 를 빼고" if a.newest_first else "--newest-first 를 붙여") + " 다시 돌린다."]
        md += long_sections(cells, t, p, cell_rows)
    if a.imu:
        imu_rows = [imu_row(f, t, p, wins, span, a.imu_max_lag) for f in a.imu]
        rows += imu_rows
        md += ["", "## IMU 가속도 (추출 FTG → CSV)", "",
               *_table(imu_rows, [("파일", "file"), ("IMU 처음", "imu_from"), ("축", "axis"), ("lag A 초", "lag_A_s"), ("r A", "r_A"),
                                  ("끝 A", "edge_A"), ("lag B 초", "lag_B_s"), ("r B", "r_B"), ("드리프트 ppm", "drift_ppm"),
                                  ("배율 k", "acc_k"), ("RMS mm/s²", "acc_rms"), ("p95", "acc_p95"), ("최대", "acc_max"), ("상태", "status")]),
               "", "lag 는 셀 시계 − PC 시계(+ 지연). r 의 부호는 축 방향이다. 오차는 (셀 − b)/k − 모터 가속도, mm/s². "
               f"피크가 탐색 끝(±{a.imu_max_lag:.0f} 초)에 걸리면 셀 시계가 더 어긋난 것이다 — --imu-max-lag 를 늘린다."]
    md += ["", "## 가정 · 판정 기준", "",
           "- 정렬됨 = 모든 창에서 피크 |r| ≥ 0.5 이고 피크가 탐색 끝이 아님. 약함 = r 은 낮지만 첫 창과 마지막 창의 lag 가 0.05 초 안에서 같음"
           " (잡음이 클 때 — 우연히 맞을 확률은 1 % 아래). 둘 다 `[제안값]`. 합격선은 아직 없다 — 숫자가 나오고 반복하면 비슷한지.",
           "- speed_history: 600 × 16비트 · 표본 i 의 시각 = 수신 시각 − (599 − i) × 0.1 초 — 엔디안 · 부호 · 순서 `[확인 필요]`. hspeed: byte 41 한 바이트 · 단위 `[확인 필요]`.",
           "- 모터 속도 · 가속도는 위치 차분(0x606C 가 PDO 에 없다). 위치 1 pulse ≈ 1 µm."]
    return "\n".join(md) + "\n", rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="셀 vs 모터 일관성 비교 v0 (오프라인)")
    ap.add_argument("--motion", required=True, help="모터 2 ms 기록 CSV (run_motion 이 쓴 data/motion_<시각>.csv)")
    ap.add_argument("--live", help="셀 라이브 원시 기록 (live_tap 이 쓴 data/live_raw/<시각>.bin)")
    ap.add_argument("--imu", nargs="*", default=[], help="IMU 가속도 CSV (첫 칸 시각, 나머지 축) — 여러 개")
    ap.add_argument("--out", help="출력 이름 (.md · .csv 를 붙인다)")
    ap.add_argument("--max-lag", type=float, default=5.0, help="속도 정렬 탐색 ±초 (기본 5)")
    ap.add_argument("--imu-max-lag", type=float, default=30.0, help="IMU 정렬 탐색 ±초 (기본 30 — 셀 시계가 더 어긋났으면 늘린다)")
    ap.add_argument("--speed-fmt", default="<H", help="speed_history 한 칸의 struct 형식 (기본 <H)")
    ap.add_argument("--newest-first", action="store_true", help="speed_history 0번이 최신이라고 본다")
    a = ap.parse_args(argv)
    proc.safe_stdio()
    md, rows = report(a)
    out = Path(a.out) if a.out else Path(a.motion).with_name(f"compare_{Path(a.motion).stem}")
    out.with_name(out.name + ".md").write_text(md, encoding="utf-8")
    if rows:
        cols = list(dict.fromkeys(k for r in rows for k in r))
        with open(out.with_name(out.name + ".csv"), "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, cols)
            w.writeheader()
            w.writerows(rows)
    proc.console(md)
    proc.console(f"→ {out.name}.md" + (f" · {out.name}.csv" if rows else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
