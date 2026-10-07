"""셀 24대 배터리 표 — 셀을 건드리지 않는다.

시험(run_cycle.py)이 돌고 있으면 셀 신호 포트를 쓸 수 없으므로 그 프로그램이 남긴 data/now.json 을 읽는다.
"""
import json, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cellbench.cells import LiveListener, PortBusy
from cellbench.config import Config

cfg = Config()
try:
    lv = LiveListener(cfg); lv.start(); time.sleep(10); lv.stop()
    rows = [dict(serial=s, ip=c.ip, battery=c.battery, hr=c.hr, rssi=c.rssi, state=c.state, waiting=c.waiting)
            for s, c in lv.snapshot().items()]
    src = "10초 수신"
except PortBusy:
    p = ROOT / cfg.data_dir / "now.json"
    now = json.loads(p.read_text(encoding="utf-8"))
    rows = now.get("cells", [])
    src = f"시험 중 · run_cycle 의 now.json ({time.time() - now['t']:.0f}초 전)"

print(f"셀 {len(rows)}대 ({src})")
print(f"{'시리얼':>7} {'IP':>15} {'배터리%':>6} {'심박':>4} {'Wi-Fi dBm':>9} {'상태':>4} {'대기':>4}")
for c in sorted(rows, key=lambda r: r["battery"]):
    print(f"{c['serial']:>7} {c['ip']:>15} {c['battery']:>6} {c['hr']:>4} {c['rssi']:>9} {c['state']:>4} {'TCP' if c['waiting'] else '':>4}")
b = [c["battery"] for c in rows]
if b:
    print(f"배터리: 최저 {min(b)}% · 최고 {max(b)}% · 평균 {sum(b)/len(b):.0f}%")
missing = sorted(set(cfg.serials) - {c["serial"] for c in rows})
if missing:
    print(f"안 들리는 셀: {missing}")
