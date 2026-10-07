"""셀 24대 배터리 표 — 10초 듣고 출력. 셀을 건드리지 않는다."""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cellbench.cells import LiveListener
from cellbench.config import Config

cfg = Config()
lv = LiveListener(cfg); lv.start(); time.sleep(10); lv.stop()
cells = lv.snapshot()
print(f"셀 {len(cells)}대 (10초 수신)")
print(f"{'시리얼':>7} {'IP':>15} {'배터리%':>6} {'심박':>4} {'Wi-Fi dBm':>9} {'상태':>4} {'대기':>4}")
for s in sorted(cells, key=lambda x: cells[x].battery):
    c = cells[s]
    print(f"{s:>7} {c.ip:>15} {c.battery:>6} {c.hr:>4} {c.rssi:>9} {c.state:>4} {'TCP' if c.waiting else '':>4}")
b = [c.battery for c in cells.values()]
if b:
    print(f"배터리: 최저 {min(b)}% · 최고 {max(b)}% · 평균 {sum(b)/len(b):.0f}%")
missing = sorted(set(cfg.serials) - set(cells))
if missing:
    print(f"안 들리는 셀: {missing}")
