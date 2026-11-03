"""레일 QC 1차 기준 만들기 — 같은 셀을 여러 번 잰 판정 파일들로 파트마다 기준 파일 한 장을 쓴다.

  python cell-bench/tools/qc_criteria.py A cell-bench/data/collect_20261102/A/protocol_*.json
  python cell-bench/tools/qc_criteria.py C cell-bench/data/collect_20261102/C/protocol_*.json
  python cell-bench/tools/qc_criteria.py B cell-bench/data/collect_20261102/B/protocol_*.json
→ cell-bench/motionbench/criteria/rail_v1.json 의 그 파트 칸을 새로 쓴다(다른 파트 칸은 그대로).
규칙은 motionbench/qc_criteria.py 머리말. 판정 도구(tools/protocol_v4.py)가 이 파일을 읽어 합불을 낸다.
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from motionbench import qc_criteria as Q  # noqa: E402

OUT = ROOT / "motionbench" / "criteria" / "rail_v1.json"
VERSION = "1차 2026-11-02"
ITEMS = {"A": {"cn0_sim": None, "gps_rx": None, "pos_h": None},
         "C": {"cn0_sim": None, "gps_rx": None, "gate_on": "accel"},
         "B": {"cn0_sim": None, "gps_rx": None, "lock": None, "sog_lag": None, "gps_move": "v300"}}


def keyfn(kind, docs):
    if kind == "v300":
        keys = {k for d in docs for c in d["cells"].values() for k in ((c["items"].get("gps_move") or {}).get("value") or {}).get("v_ratio", {})}
        if len(keys) != 1:
            raise SystemExit(f"파트 B 파일의 장면 속도가 하나가 아니다: {sorted(keys)} — 속도마다 따로 만든다")
        k = keys.pop()
        return lambda v, k=k: (v or {}).get("v_ratio", {}).get(k)
    if kind == "accel":
        acc = {str(int(a)) for d in docs for a in (d.get("conditions") or {}).get("accels_mm_s2") or []}
        a = sorted(acc)[0] if len(acc) == 1 else None
        if a is None:
            raise SystemExit(f"파트 C 파일의 가속도가 하나가 아니다: {sorted(acc)} — 가속도마다 따로 만든다")
        return lambda v, a=a: (v or {}).get(a)
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("part", choices=list(ITEMS))
    ap.add_argument("files", nargs="+")
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args(argv)
    paths = sorted({p for f in a.files for p in glob.glob(f)})
    docs = [json.loads(Path(p).read_text(encoding="utf-8")) for p in paths]
    docs = [d for d in docs if (d.get("conditions") or {}).get("part") == a.part]
    if not docs:
        raise SystemExit(f"파트 {a.part} 판정 파일이 없다")
    valid = [d for d in docs if (d.get("rig") or {}).get("유효", True)]
    part = {"made": time.strftime("%Y-%m-%d %H:%M:%S"), "files": len(docs), "valid_runs": len(valid),
            "source": [Path(p).name for p in paths],
            "conditions": {k: docs[0]["conditions"].get(k) for k in ("set", "scene", "simulator", "site", "sky")},
            "items": {}}
    for iid, kind in ITEMS[a.part].items():
        c = Q.derive(Q.per_cell(docs, iid, keyfn(kind, docs)), iid)
        if kind == "accel":
            c["key"] = {"accel": str(int(docs[0]["conditions"]["accels_mm_s2"][0]))}
        if kind == "v300":
            c["key"] = {"v_ratio": str(int(docs[0]["conditions"]["speeds_mm_s"][0]))}
        c["rule"] = Q.rule_text(iid, c)
        part["items"][iid] = c
        print(f"{a.part} {iid:9s} {c['kind']:3s} · {c['rule']}" + (f" · 걸린 셀 {sorted(c.get('fails', {}))}" if c.get("fails") else ""))
    out = Path(a.out)
    doc = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {"version": VERSION, "status": "제안값", "rule": {
        "eligible": f"같은 셀 흔들림 ≤ 셀 사이 흔들림 × {Q.ELIGIBLE_RATIO}", "limit": f"무리 중앙 ± 셀 사이 흔들림 × {Q.K:g} (나쁜 쪽만)",
        "min_runs": Q.MIN_RUNS, "decided": "2026-11-02 사용자 결정 — 데이터를 보기 전에 정한 규칙"}, "parts": {}}
    doc["parts"][a.part] = part
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"→ {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
