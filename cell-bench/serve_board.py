"""결과판 서버 — data/ 의 CSV·now.json 을 화면에 내주고, 원격 명령을 받아 파일로 남긴다.

  python serve_board.py                  # http://127.0.0.1:8765 (이 PC 에서만)
  python serve_board.py --port 9000

원격에서 보려면 Tailscale 로 이 포트를 내 기기들에만 연다 (docs/remote-access.md):
  tailscale serve --bg 8765
서버 자신은 계속 127.0.0.1 에만 붙고, 바깥에서 오는 접속은 Tailscale 이 받아 넘긴다.

읽기는 자유, 명령(POST /api/control)은 PIN 이 있어야 한다. PIN 은 tools/remote_setup.py 로 저장한다.
시험 프로그램(run_cycle.py)과는 파일(data/control.json)로만 이어져 있어 서로 죽어도 영향이 없다.

신호등: GET /api/health 는 요청마다 data/ 의 파일(now · supervisor · osinfo · cycles · alert_ack)로 새로 판정한다
(cellbench/health.py) — 감시자가 없어도 화면은 판정된다. POST /api/alert/ack {"key": 차선 키} 는 그 차선의 Slack 반복 알림을
멈춘다(alert.write_ack). 장비를 움직이지 않으므로 PIN 을 받지 않는다. 키는 9개 차선 키만 받는다.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import keyring

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from cellbench import health as healthmod  # noqa: E402
from cellbench.alert import ACK_FILE, SERVICE as REMOTE_SERVICE, write_ack  # noqa: E402
from cellbench.config import Config  # noqa: E402
from cellbench.control import COMMANDS, ControlInbox, PinGuard, read_json  # noqa: E402
from cellbench.supervisor import HEALTH_FILE, OSINFO_FILE, STATE_FILE as SUP_FILE  # noqa: E402

BOARD = ROOT / "board" / "index.html"


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def numbered(data: Path, prefix: str) -> list[tuple[int, Path]]:
    out = []
    for p in data.glob(f"{prefix}_*.csv"):
        m = re.fullmatch(rf"{prefix}_(\d+)\.csv", p.name)
        if m:
            out.append((int(m.group(1)), p))
    return sorted(out)


def bucket_rows(rows: list[dict], bucket_s: int) -> list[dict]:
    """1분(또는 20초) 표본을 bucket_s 초 단위로 묶는다 — 결과판의 '최근 5사이클 · 전체' 범위용.
    평균(watts·batt_avg) · 최저(batt_min·cells) · 최고(batt_max·wh). 클라우드의 bench_sample_bucket() 과 같은 규칙."""
    import datetime as _dt
    out: dict[int, dict] = {}
    for r in rows:
        try:
            t = int(_dt.datetime.fromisoformat(r["time"]).timestamp())
        except (KeyError, ValueError):
            continue
        k = t // bucket_s * bucket_s
        g = out.setdefault(k, {"time": _dt.datetime.fromtimestamp(k).strftime("%Y-%m-%d %H:%M:%S"), "cycle": r.get("cycle"),
                               "phase": r.get("phase"), "plug": r.get("plug"), "_w": [], "_a": [], "wh": 0.0, "batt_min": None, "batt_max": None, "cells": None, "n": 0})
        g["n"] += 1
        if r.get("plug") == "on": g["plug"] = "on"
        for key, lst in (("watts", "_w"), ("batt_avg", "_a")):
            try: lst and g[lst].append(float(r[key]))
            except (KeyError, ValueError, TypeError): pass
        try: g["wh"] = max(g["wh"], float(r.get("wh") or 0))
        except ValueError: pass
        for key, fn in (("batt_min", min), ("batt_max", max), ("cells", min)):
            try:
                v = int(float(r[key])); g[key] = v if g[key] is None else fn(g[key], v)
            except (KeyError, ValueError, TypeError): pass
    res = []
    for k in sorted(out):
        g = out[k]; w, a = g.pop("_w"), g.pop("_a")
        g["watts"] = f"{sum(w)/len(w):.2f}" if w else ""
        g["batt_avg"] = f"{sum(a)/len(a):.1f}" if a else ""
        g["wh"] = f"{g['wh']:.3f}"
        for key in ("batt_min", "batt_max", "cells"):
            g[key] = "" if g[key] is None else g[key]
        g["hr_cells"] = 0
        res.append(g)
    return res


def read_json_retry(path: Path, tries: int = 5) -> dict | None:
    """now.json 은 엔진이 20초마다 바꿔치기하므로 그 순간 읽기가 실패할 수 있다 — 몇 번 다시 읽는다."""
    for _ in range(tries):
        d = read_json(path)
        if d is not None or not path.exists():
            return d
        time.sleep(0.05)
    return None


def health_now(data: Path, now_t: float | None = None, cfg: Config | None = None) -> dict:
    """신호등을 지금 파일들로 새로 판정한다 (cellbench/health.py). 문턱은 엔진·감시자와 같은 설정(코드 기본값 ← bench.json)."""
    if cfg is None:
        try:
            cfg = Config.load()
        except Exception:
            cfg = Config()                  # 설정이 깨졌어도 화면은 판정한다 — 감시자 쪽이 설정 오류를 따로 알린다
    sup = read_json(data / SUP_FILE)
    h = healthmod.compute(read_json_retry(data / "now.json"), sup, read_json(data / OSINFO_FILE),
                          read_csv(data / "cycles.csv"), cfg, time.time() if now_t is None else now_t)
    return healthmod.mark_acks(h, read_json(data / ACK_FILE), (sup or {}).get("alerts"))


def load_pin() -> str | None:
    try:
        return keyring.get_password(REMOTE_SERVICE, "pin")
    except Exception:
        return None


def make_handler(data: Path, guard: PinGuard):
    inbox = ControlInbox(data)

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):          # 콘솔을 조용히
            pass

        def _send(self, body: bytes, ctype: str, code: int = 200):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200):
            self._send(json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", code)

        def _who(self) -> str:
            # Tailscale Serve 를 거치면 원래 주소가 이 헤더로 온다
            return self.headers.get("Tailscale-User-Login") or self.headers.get("X-Forwarded-For") or self.client_address[0]

        def do_GET(self):
            u = urlparse(self.path); q = parse_qs(u.query)
            if u.path in ("/", "/index.html"):
                return self._send(BOARD.read_bytes(), "text/html; charset=utf-8")
            if u.path == "/api/now":
                p = data / "now.json"
                try:
                    return self._json(json.loads(p.read_text(encoding="utf-8")) if p.exists() else {})
                except (OSError, ValueError):
                    return self._json({})
            if u.path == "/api/health":
                return self._json(health_now(data))
            if u.path == "/api/cycles":
                return self._json(read_csv(data / "cycles.csv"))
            if u.path == "/api/events":
                return self._json(read_csv(data / "events.csv")[-200:])
            if u.path == "/api/samples":
                files = numbered(data, "samples")
                if not files:
                    return self._json({"cycle": None, "rows": []})
                rng = q.get("range", ["cycle"])[0]          # cycle(기본) · recent(최근 5사이클, 10분 묶음) · all(전체, 1시간 묶음)
                if rng in ("recent", "all"):
                    pick = files[-5:] if rng == "recent" else files
                    rows = []
                    for cyc, p in pick:
                        rows += [{**r, "cycle": cyc} for r in read_csv(p)]
                    return self._json({"cycle": None, "range": rng, "rows": bucket_rows(rows, 600 if rng == "recent" else 3600)})
                want = int(q["cycle"][0]) if "cycle" in q else files[-1][0]
                path = dict(files).get(want)
                return self._json({"cycle": want, "rows": [{**r, "cycle": want} for r in read_csv(path)] if path else []})
            if u.path == "/api/discharge":
                rows = []
                for _, p in numbered(data, "discharge"):
                    rows += read_csv(p)
                return self._json(rows)
            if u.path == "/api/cells":
                files = numbered(data, "cells")
                return self._json(read_csv(files[-1][1]) if files else [])
            if u.path == "/api/control":
                return self._json({"enabled": guard.enabled, "commands": COMMANDS,
                                   "pending": inbox.pending(), "last_ack": inbox.last_ack(),
                                   "locked": guard.locked(self._who())})
            self._send(b"not found", "text/plain", 404)

        def _body(self) -> dict | None:
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(min(n, 65536)) or b"{}")
            except ValueError:
                return None
            return body if isinstance(body, dict) else None

        def do_POST(self):
            u = urlparse(self.path)
            if u.path == "/api/alert/ack":
                # 사람이 '확인'을 눌렀다 — 그 차선의 Slack 반복(15분마다)만 멈춘다. 장비를 움직이지 않으므로 PIN 을 받지 않는다
                body = self._body()
                key = (body or {}).get("key")
                if key not in healthmod.LANE_KEYS:
                    return self._json({"ok": False, "error": f"알 수 없는 차선 {key!r}"}, 400)
                acks = write_ack(data / ACK_FILE, key)
                return self._json({"ok": True, "key": key, "t": acks[key]})
            if u.path != "/api/control":
                return self._send(b"not found", "text/plain", 404)
            if not guard.enabled:
                return self._json({"ok": False, "error": "원격 제어가 꺼져 있다 (PIN 미설정 — tools/remote_setup.py)"}, 403)
            body = self._body()
            if body is None:
                return self._json({"ok": False, "error": "본문이 JSON 이 아니다"}, 400)
            who = self._who()
            if guard.locked(who):
                return self._json({"ok": False, "error": "PIN 을 여러 번 틀려 10분간 잠김"}, 429)
            if not guard.check(who, str(body.get("pin", ""))):
                return self._json({"ok": False, "error": "PIN 이 틀림"}, 403)
            cmd = body.get("cmd")
            if cmd not in COMMANDS:
                return self._json({"ok": False, "error": f"알 수 없는 명령 {cmd}"}, 400)
            payload = inbox.post(cmd, who)
            return self._json({"ok": True, "posted": payload,
                               "note": "시험 프로그램이 20초 안에 집어 간다. 결과는 last_ack 에 나온다"})

    return H


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--data", default=str(ROOT / "data"))
    a = ap.parse_args()
    guard = PinGuard(load_pin())
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(Path(a.data), guard))
    print(f"결과판: http://127.0.0.1:{a.port}  (데이터 {a.data}) · 원격 제어 {'켜짐' if guard.enabled else '꺼짐 (PIN 미설정)'}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
