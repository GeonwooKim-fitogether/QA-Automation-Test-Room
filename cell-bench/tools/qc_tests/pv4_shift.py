"""protocol_v4 를 그대로 부르되 독 배치 Y-shift(스크래치 dock_layout_a.json)를 알게 한다 — 저장소 파일은 건드리지 않는다.
사용: python -X utf8 pv4_shift.py <protocol_v4 인자들>"""
import sys, json
from pathlib import Path
ROOT = Path(r"C:\Dev\dynamic-bench\cell-bench"); sys.path.insert(0, str(ROOT / "tools")); sys.path.insert(0, str(ROOT))
import protocol_v4 as P
extra = json.load(open(Path(__file__).parent / "dock_layout_a.json", encoding="utf-8"))
_orig = P.layout
P.layout = lambda n: extra[n]["columns"] if n in extra else _orig(n)
sys.exit(P.main(sys.argv[1:]))
