"""GNSS-STATIC 거르기 다시 내기 — 바뀐 규칙(2026-11-25: 위성 수 살펴봄 · 판정 불가 · 항목별 걸림)으로 이미 끝난 회차를 다시 판정한다. 장비에 닿지 않는다.

두 가지로 쓴다. 둘 다 기본은 보이기만 하고, 파일에 쓰려면 --out(다른 파일) 또는 --write(그 거르기 파일, 쓰기 전에 사본을 남긴다)를 준다.
장비 PC 의 거르기 파일(data/qc_gate_<세트>.json)에 --write 하는 것은 사용자 승인 뒤에만 한다.

  1) 회차 폴더에서: python -X utf8 gnss_static_regate.py <회차 폴더>... [--gate 거르기 파일] [--ftg] [--data 데이터 폴더] [--out 파일]
     회차 폴더의 summary.json(독 배치 · 판정 구간 실시간 값 cn0)으로 1차 거르기를 다시 낸다. --ftg 를 주면 그 회차에 받은 셀 기록과
     두 기록 대조(qc_gate.read_mismatch)까지 한다 — pandas 와 해석 도구(ohcoach-cell-tools)가 있는 파이썬, 그 날의 실시간 기록(data/live_raw)이 필요하다.
     --gate 를 주면 그 파일에 쌓인 앞 회차 위에 얹는다(파일은 고치지 않는다). 회차 폴더는 주는 순서대로 얹는다.
     --apply(실행기 gnss_static_run 이 회차 끝에 부른다): 회차 폴더마다 그 세트의 거르기 파일(<데이터 폴더>/qc_gate_<세트>.json)에 바로 얹어 쓰고,
     회차 폴더에 read_check.json(gate · read)을 남긴다. 실행기가 하는 쓰기와 같은 쓰기다.
  2) 쌓인 거르기를 통째로: python -X utf8 gnss_static_regate.py --recalc <거르기 파일> [--out 파일 | --write]
     거르기 파일에 쌓인 GNSS-STATIC 회차 기록(칸 · 세기 · 위치 비율 · 위성 수 · 조건)만으로 회차를 날짜 순서로 다시 낸다. 다른 시험 칸은 그대로 둔다.
     기종 등록부에 없는 셀(셀 번호 1 등)은 판정 불가가 되고, 셀 번호를 안 쓴 셀은 파일에서 빠진다. 셀마다 지금 상태 → 새 상태를 보인다."""
from __future__ import annotations

import argparse
import copy
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gnss_static_run as G  # noqa: E402
from motionbench import cell_models, qc_gate  # noqa: E402


def from_rounds(dirs: list[Path], doc: dict, say, ftg: bool = False, data: Path | None = None, models: dict | None = None) -> dict[str, dict]:
    """회차 폴더들 → doc 에 차례로 얹는다. {회차: {"gate": 셀마다 결과, "read": 대조}} 를 돌려준다."""
    out = {}
    for d in dirs:
        s = json.loads((Path(d) / "summary.json").read_text(encoding="utf-8"))
        if not s.get("cn0"):
            say(f"{d}: summary.json 에 판정 구간 실시간 값(cn0)이 없다 — 끝나지 않은 회차다. 건너뛴다")
            continue
        source = f"{Path(d).parent.name}/{Path(d).name}"
        say(f"── {source} · 배치 {s.get('layout')} · 감쇠 {s.get('attn_db')} dB · 판정 구간 {s['window']['from']} ~ {s['window']['to'][11:]}")
        G.apply_gate(s, source, doc, say, models=models)
        rec = None
        if ftg and s.get("ftg"):
            fdir = Path(s["ftg"])
            if not fdir.exists() and data:
                fdir = Path(data) / fdir.name
            f = G.round_ftg(s, fdir, data)
            live = {x: (s.get("cn0") or {}).get(str(x)) or {} for x in G.tested_cells(s["columns"], s["rows"])}
            bad, rec = qc_gate.read_mismatch(live, f)
            say("두 기록 대조(실시간 값 대 셀 기록 값):")
            for x, r in sorted(rec.items()):
                lv, fg = live.get(x) or {}, f.get(x) or {}
                say(f"  {x}: {'같음' if r['ok'] else '어긋남' if r['ok'] is False else '못 함'} — 실시간 위성 {lv.get('sats')} · 세기 {lv.get('cn0')} · 위치 {lv.get('gps_rx')} %"
                    f" / 셀 기록 위성 {fg.get('sats')} · 세기 {fg.get('cn0')} · 위치 {fg.get('fix_pct')} % — {r['why']}")
            if bad:
                say(f"읽기 불일치 {sorted(bad)} — 판정 불가로 바꿔 다시 낸다")
                G.apply_gate(s, source, doc, say, extra=bad, read=rec, models=models)
            else:
                G.apply_gate(s, source, doc, lambda m: None, read=rec, models=models)
        out[source] = {"gate": s["gate"], "read": rec}
    return out


def recalc(doc: dict, set_kind: str, models: dict | None = None) -> dict:
    """거르기 파일 doc → GNSS-STATIC 칸만 바뀐 규칙으로 다시 낸 새 doc. 회차는 (날짜, 근거 폴더) 순서로 다시 얹는다."""
    rounds: dict[str, dict] = {}
    for s, per in (doc.get("cells") or {}).items():
        for r in (per.get("GNSS-STATIC") or {}).get("rounds", []):
            g = rounds.setdefault(r["source"], {"date": r.get("date"), "cond": r.get("cond"), "cells": {}})
            g["cells"][int(s)] = {"slot": r["slot"], "cn0": r.get("cn0"), "gps_rx": r.get("gps_rx"), "sats": r.get("sats"), "kind": set_kind}
    new = copy.deepcopy(doc)
    for per in (new.get("cells") or {}).values():
        per.pop("GNSS-STATIC", None)
    new["cells"] = {s: per for s, per in (new.get("cells") or {}).items() if per and int(s) not in cell_models.NO_SERIAL}
    for source, g in sorted(rounds.items(), key=lambda kv: (kv[1]["date"] or "", kv[0])):
        unj = cell_models.unjudgeable(g["cells"], models)
        qc_gate.gnss_static_round(new, g["cells"], g["cond"], source, date=g["date"], unjudged=unj)
    return new


def show_changes(old: dict, new: dict, say) -> int:
    """셀마다 GNSS-STATIC 지금 상태 → 새 상태. 바뀐 셀 수를 돌려준다."""
    n = 0
    for s in sorted({*old.get("cells", {}), *new.get("cells", {})}, key=int):
        a = ((old.get("cells") or {}).get(s) or {}).get("GNSS-STATIC")
        b = ((new.get("cells") or {}).get(s) or {}).get("GNSS-STATIC")
        if not a and not b:
            continue
        sa, sb = (a or {}).get("status", "기록 없음"), (b or {}).get("status", "적지 않음(셀 번호 없음)")
        n += sa != sb
        say(f"  {s}: {sa} → {sb}{'  ← 바뀜' if sa != sb else ''}" + (f" — {b['why']}" if b and sa != sb else ""))
    return n


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("rounds", nargs="*", help="회차 폴더(…/GNSS-STATIC_<날짜>[꼬리표]/r<회차>)")
    ap.add_argument("--gate", help="앞 회차가 쌓인 거르기 파일(읽기만)")
    ap.add_argument("--recalc", help="이 거르기 파일의 GNSS-STATIC 회차를 통째로 다시 낸다")
    ap.add_argument("--ftg", action="store_true", help="두 기록 대조도 한다(셀 기록 · 실시간 기록 필요)")
    ap.add_argument("--data", help="셀 기록 · 실시간 기록이 있는 데이터 폴더(기본 이 작업 트리의 cell-bench/data)")
    ap.add_argument("--models", help="기종 등록부 파일(기본 motionbench/criteria/cell_models.json)")
    ap.add_argument("--out", help="다시 낸 거르기를 이 파일에 쓴다")
    ap.add_argument("--apply", action="store_true", help="실행기용 — 회차를 그 세트의 거르기 파일에 바로 얹어 쓰고 회차 폴더에 read_check.json 을 남긴다")
    ap.add_argument("--write", action="store_true", help="--recalc 파일 자리에 쓴다(쓰기 전에 사본 .bak_<시각> 을 남긴다) — 사용자 승인 뒤에만")
    a = ap.parse_args(argv)
    say = print
    models = cell_models.load(a.models) if a.models else None
    if a.recalc:
        gp = Path(a.recalc)
        old = qc_gate.load(gp)
        new = recalc(old, gp.stem.removeprefix("qc_gate_"), models)
        say(f"{gp.name} — GNSS-STATIC 를 바뀐 규칙(기준 {qc_gate.criteria('GNSS-STATIC')['version']})으로 다시 냈다. 셀마다 지금 → 새 상태:")
        n = show_changes(old, new, say)
        say(f"바뀐 셀 {n}대")
    elif a.apply:
        if not a.rounds or a.gate or a.out or a.write:
            ap.error("--apply 는 회차 폴더만 받는다(--gate · --out · --write 와 함께 쓰지 않는다)")
        data = Path(a.data) if a.data else G.RC.Q.DATA
        for d in map(Path, a.rounds):
            s = json.loads((d / "summary.json").read_text(encoding="utf-8"))
            gp = qc_gate.path(s["layout"].split("-")[0], data)
            doc = qc_gate.load(gp)
            res = from_rounds([d], doc, say, a.ftg, data, models)
            if not res:
                return 1
            qc_gate.save(gp, doc)
            (d / "read_check.json").write_text(json.dumps(next(iter(res.values())), ensure_ascii=False, indent=1), encoding="utf-8")
            say(f"썼다 — {gp.name} · {d.name}/read_check.json")
        return 0
    else:
        if not a.rounds:
            ap.error("회차 폴더나 --recalc 를 준다")
        old = qc_gate.load(Path(a.gate)) if a.gate else {"cells": {}}
        new = copy.deepcopy(old)
        from_rounds([Path(x) for x in a.rounds], new, say, a.ftg, Path(a.data) if a.data else None, models)
        say("셀마다 GNSS-STATIC 지금 → 새 상태:")
        show_changes(old, new, say)
    if a.write:
        if not a.recalc:
            ap.error("--write 는 --recalc 와 함께만 — 회차 폴더로 다시 낸 것은 --out 으로 쓴다")
        bak = gp.with_name(f"{gp.name}.bak_{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(gp, bak)
        qc_gate.save(gp, new)
        say(f"썼다 — {gp} (사본 {bak.name})")
    elif a.out:
        qc_gate.save(Path(a.out), new)
        say(f"썼다 — {a.out}")
    else:
        say("보이기만 했다 — 쓰려면 --out <파일> 또는 (--recalc 와 함께, 사용자 승인 뒤) --write")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
