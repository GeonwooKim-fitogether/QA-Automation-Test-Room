"""세트 등록 — 플러그 1 + 셀 24 를 한 세트로 묶기 전의 10초 검사.

FMEA 8.5 "세트 짝 오등록 → 세트 k 를 끄려다 다른 세트의 전원을 끊음"의 첫 관문이다. 짝은 사람이 정하고(등록 화면),
저장하기 전에 프로그램이 아래 넷을 같은 순서로 본다. ①~③ 이 모두 통과해야 bench.json 에 세트를 붙인다(config.add_set).

  ① 플러그 응답      그 MAC 의 플러그를 한 번 읽는다(plug.read_plug). 이미 등록된 플러그에는 접속하지 않는다
  ② 셀 n대 수신     엔진이 돌면 now.json 의 heard(15초 이내), 엔진이 없으면 셀 라이브를 10초 직접 듣는다(cells.LiveListener)
  ③ 다른 세트와 겹침 시리얼·플러그 MAC 이 다른 세트와 겹치지 않고, 붙인 뒤의 설정이 validate 를 통과한다
  ④ 플러그 전력      가안 — 켜짐이고 register_power_min_w ~ max_w 면 '24대 규모'. 벗어나면 주의만, 저장은 막지 않는다
                     (Dock 이 1.4 W 로 멈춰 있는 일은 10-10 실측처럼 있을 수 있고, 그건 등록이 아니라 운전 안전망 P4 의 일이다)

판정(heard_from_now · cells_item · overlap_item · plug_item · power_item · judge)은 순수 함수라 장비 없이 검사한다.
장비에 닿는 일은 Devices 를 거친다 — 실물(RealDevices)은 plug.py · cells.py 만 쓰고, 검사·화면 확인에서는 가짜로 바꾼다.
"""
from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from .config import (Config, compact_serials, expand_serials, next_set_id, registered_macs, registered_serials,
                     validate)

_MAC = re.compile(r"[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}")
ORDER = ("plug", "cells", "overlap", "power")      # 화면의 줄 순서 (시안 C)
LOW_PCT = 20                                       # 잔량이 이 % 미만이면 화면에 '잔량 이상' 꼬리표 — 선택은 막지 않는다


def _item(key: str, label: str, state: str, detail: str, tag: str | None = None) -> dict:
    """검사 한 줄. state = ok(통과) · bad(실패 — 저장 안 함) · warn(주의 — 저장은 함)."""
    return {"key": key, "label": label, "state": state, "detail": detail, "tag": tag}


def _wa(word) -> str:
    """숫자로 끝나는 말 뒤의 '와/과' — 세트 2 와 · 세트 1 과 (끝 숫자를 읽은 소리에 받침이 없으면 '와')."""
    return "와" if str(word)[-1:] in "2459" else "과"


def _list(xs, n: int = 4) -> str:
    xs = list(xs)
    return ", ".join(map(str, xs[:n])) + (f" 외 {len(xs) - n}대" if len(xs) > n else "")


# ---------- 요청 해석 ----------

def parse_request(body, cfg: Config) -> tuple[str, list[int]]:
    """POST /api/register 본문 → (플러그 MAC 대문자, 시리얼 목록). 형식이 틀리면 ValueError (장비에 닿기 전에 거른다).
    시리얼은 정수 목록 또는 범위 표기("11594-11617")를 받고, 정확히 cells_per_set 개 · 중복 없음이어야 한다."""
    if not isinstance(body, dict):
        raise ValueError("본문이 JSON 객체가 아니다")
    mac = body.get("plug_mac")
    if not isinstance(mac, str) or not _MAC.fullmatch(mac.strip()):
        raise ValueError(f"플러그 MAC 형식이 아니다(AA:BB:CC:DD:EE:FF): {mac!r}")
    raw = body.get("serials")
    if isinstance(raw, list) and any(isinstance(x, bool) or not isinstance(x, (int, str)) for x in raw):
        raise ValueError("시리얼은 숫자여야 한다")
    serials = expand_serials(raw if raw is not None else [])
    if len(set(serials)) != len(serials):
        raise ValueError("시리얼이 중복된다")
    if len(serials) != cfg.cells_per_set:
        raise ValueError(f"셀은 정확히 {cfg.cells_per_set}대여야 한다 (지금 {len(serials)}대)")
    return mac.strip().upper(), sorted(serials)


# ---------- 감지 재료 ----------

def heard_from_now(now: dict | None, cfg: Config, now_t: float, engine_live: bool) -> tuple[list[dict] | None, str]:
    """엔진이 now.json 에 남긴 미등록 셀(heard) → (목록, 사유). 목록이 None 이면 엔진에게서 알 수 없다는 뜻이다.

    나이(age)는 now.json 을 쓴 시각 기준이므로 지금까지 흐른 시간을 더한다. 지금 설정에 등록된 시리얼은 뺀다 —
    등록 직후에는 돌고 있는 엔진이 옛 설정으로 그 셀을 아직 미등록으로 적기 때문이다.
    """
    if not engine_live:
        return None, "엔진이 돌지 않아 감지 목록이 없다 — 저장할 때 10초 동안 직접 듣는다"
    heard = (now or {}).get("heard")
    if not isinstance(heard, list):
        return None, "엔진이 감지 목록(heard)을 아직 쓰지 않는다 — 새 코드로 바꾼 엔진부터 보인다"
    lag = max(0.0, now_t - float((now or {}).get("t") or now_t))
    reg = registered_serials(cfg)
    out = []
    for c in heard:
        if not isinstance(c, dict) or not isinstance(c.get("serial"), int) or c["serial"] in reg:
            continue
        age = float(c.get("age") or 0) + lag
        if age <= cfg.heard_window_s:
            out.append({**c, "age": round(age, 1)})
    return sorted(out, key=lambda c: c["serial"]), ("" if out else "어느 세트에도 속하지 않은 셀이 들리지 않는다")


def plug_rows(cfg: Config, doc: dict | None, now: dict | None, engine_live: bool) -> list[dict]:
    """등록 화면의 플러그 표 — 미등록(탐색 결과 data/plugs.json) 먼저, 그 뒤에 등록된 세트의 플러그(설정에서).

    등록된 플러그에는 접속하지 않으므로 그 줄의 상태는 엔진이 now.json 에 쓴 운전 중인 플러그 값(세트 1)만 보인다.
    """
    owners = registered_macs(cfg)
    rows = []
    for p in (doc or {}).get("plugs") or []:
        mac = str((p or {}).get("mac") or "").upper()
        if mac and mac not in owners:
            rows.append({"mac": mac, "ip": p.get("ip"), "alias": p.get("alias"), "on": p.get("on"), "watts": p.get("watts"),
                         "set": None, "error": p.get("error")})
    found = {str((p or {}).get("mac") or "").upper(): p for p in (doc or {}).get("plugs") or []}
    plug_now = (now or {}).get("plug") if isinstance((now or {}).get("plug"), dict) else {}
    for i, s in enumerate(cfg.sets if isinstance(cfg.sets, list) else []):
        if not isinstance(s, dict) or not isinstance(s.get("plug_mac"), str):
            continue
        mac = s["plug_mac"].upper()
        live = i == 0 and engine_live
        rows.append({"mac": mac, "ip": (found.get(mac) or {}).get("ip"), "alias": s.get("plug_alias"),
                     "on": plug_now.get("on") if live else None, "watts": plug_now.get("w") if live else None,
                     "set": s.get("id")})
    return rows


# ---------- 검사 넷 (순수) ----------

def plug_item(mac: str, owner, reading, seconds, error: str | None) -> dict:
    """① 플러그 응답. owner 가 있으면 이미 등록된 플러그라 접속하지 않았다."""
    label = "플러그 응답"
    if owner is not None:
        return _item("plug", label, "bad", f"{mac} · 세트 {owner} 의 플러그 — 쓰는 중이라 접속하지 않음")
    if error or reading is None:
        return _item("plug", label, "bad", f"{mac} · 응답 없음 — {error or '읽지 못함'}")
    return _item("plug", label, "ok", f"{mac} · {seconds:.1f}초" if seconds is not None else mac)


def cells_item(serials: list[int], heard: dict[int, dict] | None, cfg: Config, error: str | None = None) -> dict:
    """② 셀 n대 수신 — 고른 시리얼이 모두 live_gap_alarm_s(15초) 안에 들렸나. heard 는 {시리얼: {age, rssi, ...}}."""
    n = len(serials)
    label = f"셀 {n}대 수신"
    if heard is None:
        return _item("cells", label, "bad", error or "셀 수신을 확인할 수 없음")
    fresh = [s for s in serials if s in heard and float(heard[s].get("age") or 0) <= cfg.live_gap_alarm_s]
    missing = [s for s in serials if s not in fresh]
    if missing:
        return _item("cells", label, "bad", f"{len(fresh)}/{n} · {_list(missing)} 미수신 {cfg.live_gap_alarm_s:g}초")
    rssi = [float(heard[s]["rssi"]) for s in fresh if isinstance(heard[s].get("rssi"), (int, float))]
    return _item("cells", label, "ok", f"{n}/{n}" + (f" · 평균 {sum(rssi) / len(rssi):.0f} dBm" if rssi else ""))


def overlap_item(cfg: Config, mac: str, serials: list[int], new_id) -> dict:
    """③ 다른 세트와 겹침 없음 — 시리얼·플러그 MAC 이 등록된 것과 겹치지 않고, 붙인 뒤의 설정이 validate 를 통과한다."""
    label = "다른 세트와 겹침 없음"
    reg, macs = registered_serials(cfg), registered_macs(cfg)
    common = [s for s in serials if s in reg]
    if common:
        by = sorted({str(reg[s]) for s in common})
        return _item("overlap", label, "bad", f"세트 {', '.join(by)} {_wa(by[-1])} 공통 시리얼 {len(common)}대 · {_list(common)}")
    if mac.upper() in macs:
        return _item("overlap", label, "bad", f"플러그 {mac} 는 세트 {macs[mac.upper()]} 의 것")
    sets = list(cfg.sets if isinstance(cfg.sets, list) else [])
    cand = replace(cfg, sets=sets + [{"id": new_id, "plug_mac": mac, "serials": compact_serials(serials)}])
    problems = validate(cand)
    if problems:
        return _item("overlap", label, "bad", "설정 검사 — " + "; ".join(problems[:2]))
    others = [str(s.get("id")) for s in sets if isinstance(s, dict)]
    return _item("overlap", label, "ok", (f"세트 {', '.join(others)} {_wa(others[-1])} 공통 시리얼 0 · 플러그 겹침 없음" if others
                                          else "등록된 세트 없음"))


def power_item(reading, cfg: Config) -> dict:
    """④ 플러그 전력 24대 규모 — 가안. 주의(warn)는 저장을 막지 않는다."""
    lo, hi = cfg.register_power_min_w, cfg.register_power_max_w
    label = f"플러그 전력 {cfg.cells_per_set}대 규모"
    after = "운전 안전망(P4)이 첫 충전에서 끊었다 켠다 · 저장은 막지 않음"
    if reading is None:
        return _item("power", label, "warn", "플러그 응답이 없어 볼 수 없음", "가안")
    w = reading.watts
    if not reading.on:
        return _item("power", label, "warn", f"꺼짐 — 켜야 Dock 이 충전한다 · {after}", "가안")
    if w != w:                                          # NaN — 전력을 못 읽음
        return _item("power", label, "warn", "켜짐 · 전력을 읽지 못함 · 저장은 막지 않음", "가안")
    if w < lo:
        return _item("power", label, "warn", f"{w:.1f} W — Dock 이 멈춰 있을 수 있음(10-10 실측 1.4 W) · {after}", "가안")
    if w > hi:
        return _item("power", label, "warn", f"{w:.1f} W — {cfg.cells_per_set}대 규모({lo:g}~{hi:g} W)보다 큼 · 다른 기기가 물렸는지 확인 · 저장은 막지 않음", "가안")
    return _item("power", label, "ok", f"{w:.1f} W · {cfg.cells_per_set}대 규모 범위 ({lo:g}~{hi:g} W)", "가안")


def judge(items: list[dict]) -> dict:
    """네 줄을 시안 순서로 놓고 통과 여부를 정한다 — ①~③ 이 모두 ok 여야 통과, ④ 는 주의만."""
    by = {i["key"]: i for i in items}
    ordered = [by[k] for k in ORDER if k in by]
    ok = all(by.get(k, {}).get("state") == "ok" for k in ORDER[:3])
    bad = [i for i in ordered if i["state"] == "bad"]
    if ok:
        note = ""
    elif any(i["key"] == "cells" for i in bad):
        note = "선택은 유지됨 · 미수신 셀의 전원·위치를 확인한 뒤 다시 검사"
    elif any(i["key"] == "plug" for i in bad):
        note = "선택은 유지됨 · 플러그 전원과 시험망(2.4 GHz) 연결을 확인한 뒤 다시 검사"
    else:
        note = "선택은 유지됨 · 겹치는 시리얼·플러그를 빼고 다시 고른다"
    return {"ok": ok, "checks": ordered, "note": note}


# ---------- 장비 (실물 · 가짜를 바꿔 끼운다) ----------

class RealDevices:
    """실제 장비 통로 — 플러그는 plug.py, 셀 라이브는 cells.py 만 거친다. 검사·화면 확인은 같은 꼴의 가짜를 넘긴다."""

    def scan_plugs(self, cfg: Config) -> list[dict]:
        from .plug import discover_plugs
        return discover_plugs(cfg)

    def read_plug(self, cfg: Config, mac: str, ip: str | None):
        from .plug import read_plug
        return read_plug(cfg, mac, ip)

    def listen(self, cfg: Config, seconds: float) -> dict[int, dict]:
        """셀 라이브(UDP 60222)를 seconds 동안 듣고 {시리얼: {ip, battery, rssi, age}}. 다른 프로그램이 포트를 쓰면 PortBusy."""
        from .cells import LiveListener
        lst = LiveListener(cfg)
        lst.start()
        try:
            time.sleep(seconds)
            snap, now = lst.snapshot(), time.time()
        finally:
            lst.stop()
        return {s: {"ip": c.ip, "battery": c.battery, "rssi": c.rssi, "age": round(now - c.t, 1)}
                for s, c in snap.items() if not c.waiting}


def run_check(cfg: Config, mac: str, serials: list[int], devices, now: dict | None, engine_live: bool,
              plug_ip: str | None = None, now_t: float | None = None) -> dict:
    """10초 검사 — ① 과 ② 를 함께 돌리고(둘 다 10초 남짓) ③ ④ 를 판정한다 → judge() 의 결과 + seconds · t · set_id."""
    t0 = time.time()
    now_t = t0 if now_t is None else now_t
    new_id = next_set_id(cfg.sets)
    owner = registered_macs(cfg).get(mac.upper())

    def plug():
        if owner is not None:
            return None, None, None
        try:
            r, secs = devices.read_plug(cfg, mac, plug_ip)
            return r, secs, None
        except Exception as e:
            return None, None, f"{type(e).__name__}: {e}"[:160]

    def cells():
        if engine_live:
            heard, why = heard_from_now(now, cfg, now_t, True)
            return ({c["serial"]: c for c in heard} if heard is not None else None), why
        try:
            return devices.listen(cfg, cfg.register_listen_s), None
        except Exception as e:                          # cells.PortBusy — 다른 프로그램이 셀 라이브 포트를 쥐고 있다
            return None, f"셀 라이브를 들을 수 없음 — {e}"[:200]

    with ThreadPoolExecutor(max_workers=2) as ex:
        fp, fc = ex.submit(plug), ex.submit(cells)
        (reading, secs, perr), (heard, cerr) = fp.result(), fc.result()
    res = judge([plug_item(mac, owner, reading, secs, perr), cells_item(serials, heard, cfg, cerr),
                 overlap_item(cfg, mac, serials, new_id), power_item(reading, cfg)])
    res.update(seconds=round(time.time() - t0, 1), t=time.time(), set_id=new_id,
               source="engine" if engine_live else "listen")
    return res


def new_set(set_id, mac: str, serials: list[int], who: str | None = None, alias: str | None = None) -> dict:
    """bench.json 에 붙일 세트 한 줄. serials 는 범위 표기로 줄여 사람이 읽을 수 있게 한다."""
    s = {"id": set_id, "plug_mac": mac.upper(), "serials": compact_serials(serials), "registered_at": round(time.time())}
    if alias:
        s["plug_alias"] = alias
    if who:
        s["registered_by"] = who
    return s
