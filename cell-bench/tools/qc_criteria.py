"""레일 QC 1차 기준 만들기 — 같은 셀을 여러 번 잰 판정 파일들로 시험마다 기준 파일 한 칸을 쓴다.

  python cell-bench/tools/qc_criteria.py GNSS-1     cell-bench/data/collect_<날짜>/r*/judged/protocol_*.json
  python cell-bench/tools/qc_criteria.py FIRMWARE-1 cell-bench/data/collect_<날짜>/r*/judged/protocol_*.json
  python cell-bench/tools/qc_criteria.py GNSS-2     cell-bench/data/collect_<날짜>/r*/judged/protocol_*.json
→ cell-bench/motionbench/criteria/rail_v1.json 의 tests.<시험 ID> 칸을 새로 쓴다(다른 시험 칸은 그대로).
판정 파일 가운데 그 시험 것만 쓴다 — conditions.test 로 고르고, 2026-11-12 전 파일은 conditions.part 글자로 고른다(qc_ids.from_record).
여섯 시험으로 나누기 전 'FIRMWARE-2' 로 적힌 장면 동기 운전 파일은 GNSS-2 로 읽는다.
FIRMWARE-1 칸은 옛 조건(150 ↔ 750 mm 등속 왕복) 기록으로 만든 것이다 — 지금 조건(가속도 단계 운전)의 기준은 아직 없다.
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
from motionbench import qc_criteria as Q, qc_ids  # noqa: E402

OUT = ROOT / "motionbench" / "criteria" / "rail_v1.json"
VERSION = "1차 2026-11-02"
ITEMS = {"GNSS-1": {"cn0_sim": None, "gps_rx": None, "pos_h": None},
         "FIRMWARE-1": {"cn0_sim": None, "gps_rx": None, "gate_on": "accel"},
         "GNSS-2": {"cn0_sim": None, "gps_rx": None, "lock": None, "sog_lag": None, "gps_move": "v300",
               "ftg_move": None, "ftg_on": None, "ftg_dist": None}}     # ftg_* 는 IMU 판정을 거친 속도 — 참고값(살펴봄)으로만 쓴다


def keyfn(kind, docs):
    if kind == "v300":
        keys = {k for d in docs for c in d["cells"].values() for k in ((c["items"].get("gps_move") or {}).get("value") or {}).get("v_ratio", {})}
        if len(keys) != 1:
            raise SystemExit(f"GNSS-2 파일의 장면 속도가 하나가 아니다: {sorted(keys)} — 속도마다 따로 만든다")
        k = keys.pop()
        return lambda v, k=k: (v or {}).get("v_ratio", {}).get(k)
    if kind == "accel":
        acc = {str(int(a)) for d in docs for a in (d.get("conditions") or {}).get("accels_mm_s2") or []}
        a = sorted(acc)[0] if len(acc) == 1 else None
        if a is None:
            raise SystemExit(f"FIRMWARE-1 파일의 가속도가 하나가 아니다: {sorted(acc)} — 가속도마다 따로 만든다")
        return lambda v, a=a: (v or {}).get(a)
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("test", choices=list(ITEMS), help="시험 ID")
    ap.add_argument("files", nargs="+")
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args(argv)
    paths = sorted({p for f in a.files for p in glob.glob(f)})
    docs = [json.loads(Path(p).read_text(encoding="utf-8")) for p in paths]
    docs = [d for d in docs if qc_ids.from_record(d.get("conditions") or {}) == a.test]
    if not docs:
        raise SystemExit(f"{a.test} 판정 파일이 없다")
    valid = [d for d in docs if (d.get("rig") or {}).get("유효", True)]
    cell = {"made": time.strftime("%Y-%m-%d %H:%M:%S"), "files": len(docs), "valid_runs": len(valid),
            "source": [Path(p).name for p in paths],
            "conditions": {k: docs[0]["conditions"].get(k) for k in ("set", "scene", "simulator", "site", "sky")},
            "items": {}}
    for iid, kind in ITEMS[a.test].items():
        c = Q.derive(Q.per_cell(docs, iid, keyfn(kind, docs)), iid)
        if kind == "accel":
            c["key"] = {"accel": str(int(docs[0]["conditions"]["accels_mm_s2"][0]))}
        if kind == "v300":
            c["key"] = {"v_ratio": str(int(docs[0]["conditions"]["speeds_mm_s"][0]))}
        c["rule"] = Q.rule_text(iid, c)
        cell["items"][iid] = c
        print(f"{a.test} {iid:9s} {c['kind']:3s} · {c['rule']}" + (f" · 걸린 셀 {sorted(c.get('fails', {}))}" if c.get("fails") else "")
              + (f" · {c['note']}" if c.get("note") else ""))
    out = Path(a.out)
    doc = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {"version": VERSION, "status": "제안값", "rule": {
        "eligible": f"같은 셀 흔들림 ≤ 셀 사이 흔들림 × {Q.ELIGIBLE_RATIO}", "limit": f"무리 중앙 ± 셀 사이 흔들림 × {Q.K:g} (나쁜 쪽만)",
        "min_runs": Q.MIN_RUNS, "decided": "2026-11-02 사용자 결정 — 데이터를 보기 전에 정한 규칙"}, "tests": {}}
    doc.setdefault("tests", {})[a.test] = cell
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"→ {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
