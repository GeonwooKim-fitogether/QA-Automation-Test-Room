"""라이브허브 와이파이 지킴이 — 20초마다 motionbench.wifi.ensure 를 부르고, 끊김 · 다시 붙음 · 안 보임을 data/wifi_watch.log 에 적는다.
python cell-bench/tools/wifi_watch.py  (시험 내내 따로 띄워 둔다)"""
import sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from motionbench import wifi
LOG = ROOT / "data" / "wifi_watch.log"
def say(m):
    with open(LOG, "a", encoding="utf-8") as f: f.write(time.strftime("%m-%d %H:%M:%S ") + m + "\n")
say(f"지킴이 시작 — 라이브허브 {wifi.expected()}")
last = None
while True:
    try:
        r = wifi.ensure(say)
        why = r.get("why")
        if why != last:
            say(why or f"연결됨 — {r.get('current')}")
            last = why
    except Exception as e:
        say(f"점검 실패 — {e!r}")
    time.sleep(20)
