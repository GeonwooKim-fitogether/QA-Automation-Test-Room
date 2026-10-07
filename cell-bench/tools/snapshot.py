"""셀 파일 크기 스냅숏 (0x11 → 0x26). 지우지 않는다. 측정이 셀당 약 20초 멈춘다.

  python tools/snapshot.py out.json                # 기준점
  python tools/snapshot.py now.json base.json      # 기준점 대비 시간당 증가량
"""
import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cellbench.cells import CellLink, LiveListener
from cellbench.config import Config

cfg = Config()
out_path = Path(sys.argv[1]); prev = json.load(open(sys.argv[2], encoding="utf-8")) if len(sys.argv) > 2 else None
lv = LiveListener(cfg); lv.start(); time.sleep(5)
alive = [s for s in cfg.serials if s in lv.alive()]
print(f"{time.strftime('%H:%M:%S')} 라이브 {len(alive)}대")
res = CellLink(cfg, lv).status(alive)
lv.stop()
now = {str(s): dict(size=r.size, battery=r.battery, query=r.t_start or time.time(), resume_s=r.resume_s, error=r.error)
       for s, r in res.items()}
# 조회 시각: 0x11 직후를 쓰기 위해 serve 안에서 t_start 를 안 찍는 경우가 있어 지금 시각으로 대신한다
t_query = time.time()
for v in now.values():
    v["query"] = t_query
json.dump(now, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
sizes = [v["size"] for v in now.values() if v["size"]]
print(f"크기 응답 {len(sizes)}/{len(alive)}대 · {min(sizes)/1048576:.2f}~{max(sizes)/1048576:.2f} MB → {out_path}")
if prev:
    rates = []
    for k, v in now.items():
        p = prev.get(k)
        if not p or not v["size"] or not p.get("size"): continue
        hrs = (v["query"] - p["query"]) / 3600
        if hrs > 0: rates.append((v["size"] - p["size"]) / 1048576 / hrs)
    if rates:
        a = sum(rates) / len(rates)
        print(f"{len(rates)}대 평균 {a:.2f} MB/h (최소 {min(rates):.2f} · 최대 {max(rates):.2f}) → 6h {a*6:.1f} MB/셀 · 24대 {a*6*24:.0f} MB")
