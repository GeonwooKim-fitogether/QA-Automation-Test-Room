"""결과판 서버 — data/ 의 CSV·now.json 을 화면에 내주고, 원격 명령을 받아 파일로 남긴다.

  python serve_board.py                  # http://127.0.0.1:8765 (이 PC 에서만)
  python serve_board.py --port 9000

원격에서 보려면 Tailscale 로 이 포트를 내 기기들에만 연다 (docs/remote-access.md):
  tailscale serve --bg 8765
서버 자신은 계속 127.0.0.1 에만 붙고, 바깥에서 오는 접속은 Tailscale 이 받아 넘긴다.

읽기는 자유, 명령(POST /api/control)은 PIN 이 있어야 한다. PIN 은 tools/remote_setup.py 로 저장한다.
시험 프로그램(run_cycle.py)과는 파일(data/control.json)로만 이어져 있어 서로 죽어도 영향이 없다.

세트 등록 (관제 → '세트 k 등록' 화면 · 판정은 cellbench/register.py):
  GET  /api/register               설정의 세트 · 미등록 셀(엔진의 now.json heard) · 플러그(data/plugs.json) · 엔진이 도는가
  POST /api/register/plugs/scan    시험망의 플러그를 즉석에서 한 번 찾는다(5초 남짓). 읽기만이라 PIN 을 받지 않는다
  POST /api/register {pin, plug_mac, serials}  PIN 필수(원격 명령과 같은 PinGuard — 잘못 묶이면 다른 세트 전원을 끊을 수 있다).
                                   10초 검사를 통과하면 bench.json 의 sets 끝에 세트를 붙인다(config.add_set). 실패면 저장하지 않는다
  POST /api/register/remove {pin, set_id}  등록 해제 — 짝을 잘못 묶었을 때 되돌린다. PIN 필수. 첫 세트(운전 중)는 거부.
                                   bench.json 에서 그 줄만 지운다(register.remove_set — 원자적 쓰기). 장비는 움직이지 않는다
  운전 중인 엔진이 쥔 셀·플러그(now.json 의 cells · engine.json 의 args.config)는 bench.json 에 없어도 '운전 중'으로 막는다(F13).
  PIN 을 틀리면 응답에 남은 시도 횟수(left)를 싣는다 — 0 이 되면 원격 제어(안전 정지 포함)도 함께 10분 잠긴다.
  장비에 닿는 일(플러그 찾기·읽기, 셀 라이브 듣기)은 make_handler 의 devices 를 거친다 — 실물은 register.RealDevices(plug.py · cells.py),
  검사·화면 확인은 가짜를 넘긴다. --bench 로 bench.json 경로를 바꿀 수 있다(화면 확인용 임시 설정).

신호등: GET /api/health 는 요청마다 data/ 의 파일(now · engine · supervisor · osinfo · cycles · alert_ack)로 새로 판정한다
(cellbench/health.py) — 감시자가 없어도 화면은 판정된다. POST /api/alert/ack {"key": 차선 키, "reason": 화면이 보던 이유} 는
그 차선의 Slack 반복 알림을 멈춘다. 확인은 그 원인에 묶인다 — alert_ack.json 에 {키: 시각}(알림기가 읽는 꼴)과 함께
확인한 이유를 적고(health.ack_record), 원인이 바뀌면 화면에 '확인' 단추가 다시 선다. 장비를 움직이지 않으므로 PIN 을 받지 않는다.
키는 9개 차선 키만 받는다.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import keyring

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from cellbench import health as healthmod  # noqa: E402
from cellbench import register as regmod  # noqa: E402
from cellbench.alert import ACK_FILE, SERVICE as REMOTE_SERVICE  # noqa: E402
from cellbench.config import (BENCH_FILE, Config, add_set, compact_serials, expand_serials, next_set_id,  # noqa: E402
                              registered_serials)
from cellbench.control import COMMANDS, ControlInbox, PinGuard, read_json, write_json_atomic  # noqa: E402
from cellbench.record import ENGINE_FILE  # noqa: E402
from cellbench.supervisor import HEALTH_FILE, OSINFO_FILE, PLUGS_FILE, STATE_FILE as SUP_FILE  # noqa: E402

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


def health_now(data: Path, now_t: float | None = None, cfg: Config | None = None, bench: Path | str | None = BENCH_FILE) -> dict:
    """신호등을 지금 파일들로 새로 판정한다 (cellbench/health.py). 문턱은 엔진·감시자와 같은 설정(코드 기본값 ← bench.json)."""
    if cfg is None:
        try:
            cfg = Config.load(None, bench_path=bench)
        except Exception:
            cfg = Config()                  # 설정이 깨졌어도 화면은 판정한다 — 감시자 쪽이 설정 오류를 따로 알린다
    sup = read_json(data / SUP_FILE)
    now_t = time.time() if now_t is None else now_t
    now, eng = read_json_retry(data / "now.json"), read_json(data / ENGINE_FILE)
    live = healthmod.engine_state(now or {}, eng, cfg, now_t)[0] == "live"
    try:
        busy = regmod.engine_claims(cfg, now, eng, live, bench)  # 미등록 감지 칩이 등록 화면과 같은 수가 되게 (F13)
    except Exception:
        busy = None                         # 신호등 판정은 이것 때문에 멈추지 않는다 — 감지 수만 bench.json 기준이 된다
    h = healthmod.compute(now, sup, read_json(data / OSINFO_FILE), read_csv(data / "cycles.csv"), cfg, now_t,
                          read_json(data / PLUGS_FILE), eng, busy)
    return healthmod.mark_acks(h, read_json(data / ACK_FILE), (sup or {}).get("alerts"))


def engine_now(data: Path, cfg: Config, now_t: float) -> tuple[dict | None, bool, dict | None]:
    """(now.json, 엔진이 지금 도는가, engine.json) — 심박이 감시자의 멈춤 문턱 안이고 일부러 끝나지 않았으면 돈다.
    engine.json 의 끝난 이유·시각도 본다 — Ctrl+C 직후 몇 분 동안 멈춘 엔진의 옛 감지 목록을 믿지 않게."""
    now = read_json_retry(data / "now.json")
    eng = read_json(data / ENGINE_FILE)
    return now, healthmod.engine_state(now or {}, eng, cfg, now_t)[0] == "live", eng


def pin_left(guard: PinGuard, who: str = "", now_t: float | None = None) -> int:
    """잠기기까지 남은 PIN 시도 횟수 — 원격 제어·등록이 같은 잠금을 쓰고, 실패는 서버 전체에 하나로 센다(QA H-3)."""
    return guard.left(now_t)


def register_view(data: Path, cfg: Config, now_t: float, bench: Path | str | None = BENCH_FILE) -> dict:
    """등록 화면의 재료 (GET /api/register). 장비에 닿지 않는다 — 파일(now.json · engine.json · plugs.json)과 설정만 읽는다."""
    now, live, eng = engine_now(data, cfg, now_t)
    claims = regmod.engine_claims(cfg, now, eng, live, bench)
    heard, why = regmod.heard_from_now(now, cfg, now_t, live, claims["serials"])
    doc = read_json(data / PLUGS_FILE) or {}
    sets = []
    for i, s in enumerate(cfg.sets if isinstance(cfg.sets, list) else []):
        if not isinstance(s, dict):
            continue
        try:
            ser = expand_serials(s.get("serials", []))
        except ValueError:
            ser = []
        sets.append({"id": s.get("id"), "label": s.get("label", ""), "plug_mac": s.get("plug_mac"),
                     "plug_alias": s.get("plug_alias"), "serials": compact_serials(ser), "n": len(ser), "running": i == 0,
                     "registered_at": s.get("registered_at"), "registered_by": s.get("registered_by")})
    busy = sorted(claims["serials"])
    return {"t": now_t, "cells_per_set": cfg.cells_per_set, "fresh_s": cfg.live_gap_alarm_s, "low_pct": regmod.LOW_PCT,
            "listen_s": cfg.register_listen_s, "next_id": next_set_id(cfg.sets), "sets": sets,
            "registered_serials": sorted(set(registered_serials(cfg)) | set(busy)), "busy_serials": busy,
            "busy_set": claims["sid"], "busy_note": claims["note"], "engine_running": live,
            "heard": heard or [], "heard_note": why,
            "plugs": regmod.plug_rows(cfg, doc, now, live, claims), "plugs_t": doc.get("t"), "plugs_by": doc.get("by"),
            "plugs_error": doc.get("error"), "scan_every_s": cfg.plug_scan_every_s,
            "pair_check": regmod.PAIR_CHECK, "mispair": regmod.MISPAIR}


def load_pin() -> str | None:
    try:
        return keyring.get_password(REMOTE_SERVICE, "pin")
    except Exception:
        return None


def make_handler(data: Path, guard: PinGuard, bench: Path | str = BENCH_FILE, devices=None):
    """devices: 장비 통로(플러그 찾기·읽기 · 셀 라이브 듣기). 기본은 실물(register.RealDevices) — 검사·화면 확인은 가짜를 넘긴다."""
    inbox = ControlInbox(data)
    devices = devices or regmod.RealDevices()
    scan_lock, check_lock = threading.Lock(), threading.Lock()     # 탐색·검사는 한 번에 하나씩 (같은 장비를 겹쳐 부르지 않게)

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
            """기록에 남길 보낸 사람. Tailscale Serve 를 거치면 원래 사용자·주소가 헤더로 오는데, 그 프록시는 이 PC 안(127.0.0.1)에서
            붙는다 — 그래서 헤더는 127.0.0.1 에서 온 접속일 때만 믿는다(밖에서 온 접속이 헤더로 남을 사칭하지 못하게, QA H-3).
            잠금 판단에는 쓰지 않는다(PinGuard 는 서버 전체 하나로 센다)."""
            peer = self.client_address[0]
            if peer in ("127.0.0.1", "::1"):
                return self.headers.get("Tailscale-User-Login") or self.headers.get("X-Forwarded-For") or peer
            return peer

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
                return self._json(health_now(data, bench=bench))
            if u.path == "/api/register":
                try:
                    cfg = Config.load(None, bench_path=bench)
                except Exception as e:
                    return self._json({"ok": False, "error": f"설정을 읽지 못함 — {type(e).__name__}: {e}"}, 500)
                who = self._who()
                return self._json({**register_view(data, cfg, time.time(), bench), "enabled": guard.enabled,
                                   "locked": guard.locked(who), "pin_left": pin_left(guard, who),
                                   "pin_max": guard.max_fail, "pin_lock_min": round(guard.lock_s / 60)})
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

        def _register_scan(self):
            """'다시 찾기' — 시험망의 플러그를 즉석에서 한 번 찾아 data/plugs.json 을 새로 쓴다. 실패하면 옛 결과를 지우지 않는다."""
            if not scan_lock.acquire(blocking=False):
                return self._json({"ok": False, "error": "이미 찾는 중"}, 409)
            try:
                cfg = Config.load(None, bench_path=bench)
                try:
                    found = devices.scan_plugs(cfg)
                except Exception as e:
                    return self._json({"ok": False, "error": f"플러그를 찾지 못함 — {type(e).__name__}: {e}"[:300]}, 502)
                write_json_atomic(data / PLUGS_FILE, {"t": time.time(), "plugs": list(found), "by": "board"})
                return self._json({"ok": True, **register_view(data, cfg, time.time(), bench)})
            finally:
                scan_lock.release()

        def _pin(self, body: dict | None, what: str, allow_locked: bool = False):
            """PIN 관문 — 통과하면 (who, None), 아니면 (who, 보낼 응답 인자). 틀리면 남은 시도 횟수(left)를 함께 싣는다.
            allow_locked(안전 정지)는 잠금 중에도 맞는 PIN 이면 통과한다(QA H-2)."""
            if not guard.enabled:
                return None, ({"ok": False, "error": f"원격 제어 PIN 이 없어 {what} 수 없다 (제어 PC 에서 python tools/remote_setup.py)"}, 403)
            if body is None:
                return None, ({"ok": False, "error": "본문이 JSON 이 아니다"}, 400)
            who = self._who()
            if guard.locked(who) and not allow_locked:
                return who, ({"ok": False, "error": "PIN 을 여러 번 틀려 10분간 잠김 — 안전 정지만 맞는 PIN 으로 보낼 수 있다", "left": 0}, 429)
            if not guard.check(who, str(body.get("pin", "")), allow_locked=allow_locked):
                return who, ({"ok": False, "error": "PIN 이 틀림", "left": pin_left(guard)}, 403)
            return who, None

        def _register(self):
            """세트 등록 — PIN → 10초 검사 → 통과하면 bench.json 의 sets 끝에 붙인다. 실패면 저장하지 않고 이유를 돌려준다."""
            body = self._body()
            who, err = self._pin(body, "등록할")
            if err:
                return self._json(*err)
            if not check_lock.acquire(blocking=False):
                return self._json({"ok": False, "error": "다른 등록 검사가 진행 중"}, 409)
            try:
                cfg = Config.load(None, bench_path=bench)           # 잠금 안에서 읽는다 — 동시에 두 세트가 같은 id 를 받지 않게
                try:
                    mac, serials = regmod.parse_request(body, cfg)
                except ValueError as e:
                    return self._json({"ok": False, "error": str(e)}, 400)
                now, live, eng = engine_now(data, cfg, time.time())
                claims = regmod.engine_claims(cfg, now, eng, live, bench)        # 운전 중인 엔진의 셀·플러그 (F13)
                found = {str(p.get("mac") or "").upper(): p for p in (read_json(data / PLUGS_FILE) or {}).get("plugs") or []
                         if isinstance(p, dict)}
                res = regmod.run_check(cfg, mac, serials, devices, now, live, plug_ip=(found.get(mac) or {}).get("ip"),
                                       claims=claims)
                new, err = None, None
                if res["ok"]:
                    new = regmod.new_set(res["set_id"], mac, serials, who, (found.get(mac) or {}).get("alias"))
                    try:
                        add_set(new, bench)
                    except (OSError, ValueError, KeyError) as e:
                        new, err = None, f"{type(e).__name__}: {e}"
                        res.update(ok=False, note=f"검사는 통과했지만 bench.json 에 저장하지 못함 — {err}")
                return self._json({**res, "saved": new is not None, "set": new, "error": err})
            except Exception as e:
                return self._json({"ok": False, "error": f"검사 중 오류 — {type(e).__name__}: {e}"}, 500)
            finally:
                check_lock.release()

        def _register_remove(self):
            """등록 해제 — PIN → bench.json 에서 그 세트 한 줄을 지운다(register.remove_set). 첫 세트(운전 중)는 거부.
            짝을 잘못 묶었을 때 되돌리는 길이다. 등록 검사와 같은 잠금 안에서 해 두 쓰기가 겹치지 않게 한다."""
            body = self._body()
            who, err = self._pin(body, "해제할")
            if err:
                return self._json(*err)
            sid = body.get("set_id")
            if sid is None or isinstance(sid, bool):
                return self._json({"ok": False, "error": "set_id 가 없다"}, 400)
            if not check_lock.acquire(blocking=False):
                return self._json({"ok": False, "error": "등록 검사가 진행 중 — 끝난 뒤 다시"}, 409)
            try:
                try:
                    removed = regmod.remove_set(sid, bench)
                except regmod.SetRefused as e:
                    return self._json({"ok": False, "error": str(e)}, 400)
                except (OSError, ValueError, KeyError) as e:
                    return self._json({"ok": False, "error": f"bench.json 을 고치지 못함 — {type(e).__name__}: {e}"}, 500)
                return self._json({"ok": True, "removed": removed, "by": who})
            finally:
                check_lock.release()

        def _ack(self):
            """사람이 '확인'을 눌렀다 — 그 차선의 Slack 반복(15분마다)만 멈춘다. 장비를 움직이지 않으므로 PIN 을 받지 않는다.
            확인은 그 원인에 묶는다: 화면이 보던 이유(reason)를 함께 적고, 없으면 지금 판정의 이유를 적는다(health.ack_record)."""
            body = self._body()
            key = (body or {}).get("key")
            if key not in healthmod.LANE_KEYS:
                return self._json({"ok": False, "error": f"알 수 없는 차선 {key!r}"}, 400)
            lane = next((l for l in health_now(data, bench=bench)["lanes"] if l["key"] == key), {})
            seen = body.get("reason")
            reason = seen[:2000] if isinstance(seen, str) and seen else lane.get("reason", "")
            light = body.get("light") if body.get("light") in ("red", "unknown", "yellow") else lane.get("light", "")
            t = time.time()
            acks = healthmod.ack_record(read_json(data / ACK_FILE), key, reason, light, t)
            if not write_json_atomic(data / ACK_FILE, acks):
                return self._json({"ok": False, "error": "alert_ack.json 을 쓰지 못함"}, 500)
            return self._json({"ok": True, "key": key, "t": t, "reason": reason})

        def do_POST(self):
            """처리 중 예외가 나도 연결을 그냥 끊지 않고 오류로 답한다 — 화면이 '서버 응답 없음'으로 오해하지 않게(QA M-8)."""
            try:
                return self._post()
            except Exception as e:
                try:
                    return self._json({"ok": False, "error": f"서버 오류 — {type(e).__name__}: {e}"[:300]}, 500)
                except Exception:
                    return None

        def _post(self):
            u = urlparse(self.path)
            if u.path == "/api/register/plugs/scan":
                return self._register_scan()
            if u.path == "/api/register":
                return self._register()
            if u.path == "/api/register/remove":
                return self._register_remove()
            if u.path == "/api/alert/ack":
                return self._ack()
            if u.path != "/api/control":
                return self._send(b"not found", "text/plain", 404)
            body = self._body()
            cmd = (body or {}).get("cmd")
            if body is not None and cmd not in COMMANDS:
                return self._json({"ok": False, "error": f"알 수 없는 명령 {cmd}"}, 400)
            who, err = self._pin(body, "원격 명령을 보낼", allow_locked=(cmd == "stop_safe"))
            if err:
                return self._json(*err)
            payload = inbox.post(cmd, who)
            if payload is None:
                return self._json({"ok": False, "error": "명령 파일을 쓰지 못함 — 잠시 뒤 다시"}, 503)
            return self._json({"ok": True, "posted": payload,
                               "note": "시험 프로그램이 20초 안에 집어 간다. 결과는 last_ack 에 나온다"})

    return H


def main():
    from cellbench import proc
    proc.safe_stdio()                       # 창 없이 띄워도(감시자) '—' 같은 글자 출력으로 죽지 않게 (검토 F1)
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--data", default=str(ROOT / "data"))
    ap.add_argument("--bench", default=str(BENCH_FILE), help="시험대 설정 파일 (세트 등록이 고치는 곳). 기본 cell-bench/bench.json")
    a = ap.parse_args()
    guard = PinGuard(load_pin())
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(Path(a.data), guard, Path(a.bench)))
    print(f"결과판: http://127.0.0.1:{a.port}  (데이터 {a.data}) · 원격 제어 {'켜짐' if guard.enabled else '꺼짐 (PIN 미설정)'}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
