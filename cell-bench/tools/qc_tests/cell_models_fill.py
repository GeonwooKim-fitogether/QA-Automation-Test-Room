"""셀 기종 등록부 채우기 — 받은 셀 기록(ftg_<셀>_<받은 시각>.bin)의 시작 메시지에서 기종 · 펌웨어를 읽어
motionbench/criteria/cell_models.json 의 cells 에 적는다(motionbench/cell_models.py, 2026-11-25).

- 같은 셀이 여러 파일에 나오면 받은 시각이 가장 늦은 파일을 쓴다(펌웨어를 바꿨으면 바뀐 값이 남는다).
- 파일 이름의 셀 번호와 시작 메시지의 셀 번호가 다르면 적지 않는다(주소로 받은 ftg_ip<끝자리>_… 파일은 시작 메시지의 번호를 쓴다).
- 셀 번호를 안 쓴 셀(번호 1 등, cell_models.NO_SERIAL)은 적지 않는다 — 셀끼리 구별할 수 없다.
- 형식 표(formats)는 고치지 않는다. 새 (기종, 펌웨어)는 표에 없으므로 그 셀은 판정 불가(기종 확인 필요)로 남는다 — 비교 절차를 거친 뒤 사람이 표에 더한다.
장비에 닿지 않는다. --write 없이는 무엇을 적을지만 보인다.
사용: python -X utf8 cell_models_fill.py <셀 기록 폴더 또는 .bin 파일>... [--write] [--table <등록부 파일>]"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # <작업 트리>/cell-bench
sys.path.insert(0, str(ROOT))
from motionbench import cell_models as M  # noqa: E402

NAME = re.compile(r"^ftg_(?P<who>\d+|ip\d+)_(?P<t>\d{8}_\d{6})\.bin$")


def files(paths: list[str], say=print) -> list[Path]:
    out = []
    for p in map(Path, paths):
        out += sorted(p.glob("ftg_*.bin")) if p.is_dir() else [p]
    for f in out:
        if not NAME.match(f.name):
            say(f"적지 않음: {f} — 이름이 ftg_<셀>_<받은 시각>.bin 꼴이 아니다")
    return [f for f in out if NAME.match(f.name)]


def rel(f: Path) -> str:
    """근거로 적을 경로 — data 폴더 기준(제어 PC 의 cell-bench/data), 아니면 폴더/파일."""
    parts = f.resolve().parts
    return "/".join(parts[parts.index("data") + 1:]) if "data" in parts else f"{f.parent.name}/{f.name}"


def scan(fs: list[Path], say=print) -> tuple[dict[int, dict], list[str]]:
    """파일들 → ({셀: 등록부 한 줄}, 적지 않은 이유들). 받은 시각 순서로 읽어 늦은 것이 이긴다."""
    got, skip = {}, []
    for f in sorted(fs, key=lambda f: NAME.match(f.name)["t"]):
        m = NAME.match(f.name)
        info = M.file_info(f)
        if info is None:
            skip.append(f"{rel(f)} — 시작 메시지 없음"); continue
        if len(info["kinds"]) > 1:
            say(f"  {rel(f)}: 파일 안에 기종 · 펌웨어가 {len(info['kinds'])}가지 {info['kinds']} — 마지막 값을 쓴다")
        s = info["serial"]
        if m["who"].isdigit() and int(m["who"]) != s:
            skip.append(f"{rel(f)} — 파일 이름 셀 {m['who']} ≠ 시작 메시지 셀 {s}"); continue
        if s in M.NO_SERIAL:
            skip.append(f"{rel(f)} — 셀 번호를 안 쓴 셀(번호 {s}) {info['model']} {info['fw']}"); continue
        t = m["t"]
        got[s] = {"model": info["model"], "fw": info["fw"], "source": rel(f), "date": f"{t[:4]}-{t[4:6]}-{t[6:8]}"}
    return got, skip


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("paths", nargs="+", help="셀 기록 폴더(ftg_*.bin 이 든 곳) 또는 파일")
    ap.add_argument("--table", default=str(M.FILE), help="등록부 파일(기본 motionbench/criteria/cell_models.json)")
    ap.add_argument("--write", action="store_true", help="등록부 파일에 적는다(없으면 보이기만)")
    a = ap.parse_args(argv)
    table = M.load(a.table)
    got, skip = scan(files(a.paths))
    old = table.get("cells") or {}
    for s, c in sorted(got.items()):
        prev = old.get(str(s))
        same = prev and (prev.get("model"), prev.get("fw")) == (c["model"], c["fw"])
        ok, why = M.check(s, {**table, "cells": {str(s): c}})
        print(f"{s}: {c['model']} {c['fw']} ({c['date']} {c['source']})"
              + ("" if same else f" · 등록부 {prev['model']} {prev['fw']} 에서 바뀜" if prev else " · 새로 등록")
              + ("" if ok else f" · {why}"))
    for w in skip:
        print(f"적지 않음: {w}")
    if not a.write:
        print(f"보이기만 했다 — 적으려면 --write ({len(got)}대)")
        return 0
    table["cells"] = {str(k): v for k, v in sorted(({**{int(k): v for k, v in old.items()}, **got}).items())}
    Path(a.table).write_text(json.dumps(table, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"적음 — {a.table} · 셀 {len(table['cells'])}대")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
