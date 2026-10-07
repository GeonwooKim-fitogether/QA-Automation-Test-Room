"""결과판 서버 — data/ 의 CSV·now.json 을 읽어 화면에 내준다. 셀·플러그에는 아무것도 보내지 않는다.

  python serve_board.py            # http://127.0.0.1:8765
  python serve_board.py --port 9000

사이클 실행(run_cycle.py)과 별개 프로세스다. 화면을 꺼도 시험은 계속되고, 시험이 멈춰도 화면은 마지막 기록을 보여 준다.
이 PC 에서만 열리도록 127.0.0.1 에만 붙는다.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
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


def make_handler(data: Path):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):          # 콘솔을 조용히
            pass

        def _send(self, body: bytes, ctype: str, code: int = 200):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj):
            self._send(json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def do_GET(self):
            u = urlparse(self.path); q = parse_qs(u.query)
            if u.path in ("/", "/index.html"):
                return self._send(BOARD.read_bytes(), "text/html; charset=utf-8")
            if u.path == "/api/now":
                p = data / "now.json"
                return self._json(json.loads(p.read_text(encoding="utf-8")) if p.exists() else {})
            if u.path == "/api/cycles":
                return self._json(read_csv(data / "cycles.csv"))
            if u.path == "/api/events":
                return self._json(read_csv(data / "events.csv")[-200:])
            if u.path == "/api/samples":
                files = numbered(data, "samples")
                if not files:
                    return self._json({"cycle": None, "rows": []})
                want = int(q["cycle"][0]) if "cycle" in q else files[-1][0]
                path = dict(files).get(want)
                return self._json({"cycle": want, "rows": read_csv(path) if path else []})
            if u.path == "/api/discharge":
                rows = []
                for _, p in numbered(data, "discharge"):
                    rows += read_csv(p)
                return self._json(rows)
            if u.path == "/api/cells":
                files = numbered(data, "cells")
                return self._json(read_csv(files[-1][1]) if files else [])
            self._send(b"not found", "text/plain", 404)
    return H


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--data", default=str(ROOT / "data"))
    a = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(Path(a.data)))
    print(f"결과판: http://127.0.0.1:{a.port}  (데이터 {a.data})", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
