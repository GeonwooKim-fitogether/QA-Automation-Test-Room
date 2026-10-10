"""짝 검증기 — 엔진 안에서만 돈다 (다중 세트 명세 3-2 ~ 3-5).

셀 라이브 포트(UDP 60222)를 엔진이 독점하므로, 엔진이 도는 동안 셀 배터리를 들을 수 있는 곳은 엔진뿐이다. 그래서 외부 도구가 아니라
엔진 안의 스레드로 돈다. 판정은 cellbench/pair.py(순수), 이 파일은 순서 · 플러그 · 기록이다.

  사전 확인 → 세트 k 셀 추출 · 삭제 → (필요하면) 준비 방전 → 충전 확인 → 빠른 검증 → 패턴 검증 → 통과 · 실패 · 판정 불가
  mode="calib"(U2 보정 실측)는 같은 순서로 돌되 판정하지 않고 모든 셀의 값을 calib_<시각>.csv 에 적는다.

지키는 것
  · Pk(검증하는 세트의 플러그) 하나만 만진다. 어떤 길로 끝나든 Pk 를 켠 채(끊었다 켜기) 끝낸다(finally).
  · 10초마다 심박(self.beat)을 남긴다 — 멈추면 VerifyManager 가 Pk 를 켜고 cancelled 를 세운다. 깨어난 검증기는 플러그를 더 만지지 않는다.
  · 짝 이상(빠른 · 패턴 실패, 준비 방전 30분 3 %p 미달)은 on_fault 로 알린다 — 엔진이 '모든 플러그 켬'(명세 3-6)을 한다.
  · 보정 전(config.PAIR['calibrated'] 가 아님)에는 판정 검증을 시작하지 않는다 — 기록 전용만 된다(U2 · 세트 k 운전 차단).
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import statistics
import threading
import time
from pathlib import Path
from typing import Callable

from . import pair as P
from .config import PAIR, SAFETY, Config, expand_serials, set_cfg, set_dir, set_entry
from .control import read_json, write_json_atomic

SAMPLE_S = 10.0
BUSY_WAIT_MAX_S = 3600.0          # 운전 세트의 단계 전환을 기다리는 최대 시간 — 넘으면 판정 불가로 끝낸다
STALL_S = 120.0                    # 검증기 심박이 이만큼 멈추면 멈춘 것으로 본다
PREP_MAX_S = 3 * 3600.0            # 준비 방전 최대 시간
PREP_BLIND_S = 600.0               # 준비 방전 중 세트 셀 중앙값을 이만큼 못 읽으면 Pk 를 켜고 판정 불가로 끝낸다(코드 검토 2)
WATCH_S = 20.0                     # 검증 중 감시 스레드가 멈춤 요청 · 심박을 보는 간격


def fingerprint(entry: dict) -> str:
    """짝 지문 — 플러그 MAC(대문자) · 정렬한 시리얼 · 등록 시각(registered_at, 등록마다 바뀐다 — D11 · A-H5)."""
    ser = sorted(expand_serials(entry.get("serials", [])))
    raw = f"{str(entry.get('plug_mac', '')).upper()}|{','.join(map(str, ser))}|{entry.get('registered_at', '')}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def pair_ok(cfg: Config, set_id: int) -> bool:
    """세트 k 의 pair.json 이 '통과'이고 지문이 지금 bench.json 과 같은가 (운전 관문)."""
    e = set_entry(cfg, set_id)
    doc = read_json(set_dir(cfg, set_id) / "pair.json") or {}
    return bool(e) and doc.get("state") == "passed" and doc.get("fingerprint") == fingerprint(e)


class Stopped(Exception):
    """멈춤 요청 · 멈춘 것으로 판정됨 — 판정 없이 Pk 를 켜고 끝낸다."""


class Inconclusive(Exception):
    """판정할 수 없어 끝낸다 — 플러그는 run() 의 finally 가 켠다."""


class PairVerifier:
    def __init__(self, cfg: Config, set_id: int, live, plug, link, rec, gate=None, mode: str = "verify",
                 groups: dict | None = None, busy: Callable[[], str | None] | None = None,
                 phases: Callable[[], dict] | None = None, on_fault: Callable[[str], None] | None = None,
                 now: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep):
        self.cfg, self.k = cfg, set_id
        self.kcfg = set_cfg(cfg, set_id)
        self.live, self.plug, self.link, self.rec, self.gate = live, plug, link, rec, gate
        self.mode = mode
        self.gk = list(self.kcfg.serials)
        self.groups = groups or {}
        self.busy = busy or (lambda: None)
        self.phases = phases or (lambda: {})
        self.on_fault = on_fault or (lambda why: None)
        self.now, self._sleep = now, sleep
        self.dir = set_dir(cfg, set_id)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.beat = now()
        self.cancelled = threading.Event()      # 멈춤 요청 · 멈춘 것으로 판정됨 — 다음 기다림에서 끝난다
        self.stalled = False                     # 관리자가 '멈춤'으로 보고 Pk 를 이미 켰다 — 그때만 끝에서 다시 켜지 않는다
        self.samples: P.Samples = []
        self.doc: dict = {}
        self.off_s = 0.0
        self._calib = None

    # --- 도우미 ---
    def _save(self, **kw) -> None:
        self.doc.update(kw, t=self.now(), beat=self.beat)
        write_json_atomic(self.dir / "pair.json", self.doc)

    def _wait(self, s: float) -> None:
        end = self.now() + s
        while self.now() < end:
            if self.cancelled.is_set():
                raise Stopped("멈춤")
            self._sleep(min(SAMPLE_S, end - self.now()))
            self.beat = self.now()

    def _heard(self) -> dict[int, int]:
        snap, t = self.live.snapshot(), self.now()
        return {s: c.battery for s, c in snap.items() if t - c.t <= 15 and not c.waiting}

    def _sample(self, plug_on: bool) -> None:
        b = self._heard()
        self.samples.append((self.now(), b))
        if self._calib is not None:
            r = self._read()
            self._calib.writerow([round(self.now(), 1), plug_on, r.watts if r else ""] + [b.get(s, "") for s in self._calib_cols])

    def _record(self, seconds: float, plug_on: bool) -> None:
        end = self.now() + seconds
        while self.now() < end:
            self._sample(plug_on)
            self._wait(min(SAMPLE_S, end - self.now()))
            if not plug_on:
                self.off_s += SAMPLE_S

    def _median_k(self) -> float | None:
        b = self._heard()
        xs = [b[s] for s in self.gk if s in b]
        return float(statistics.median(xs)) if xs else None

    def _read(self):
        try:
            return self.plug.read()
        except Exception:
            return None

    def _set(self, on: bool):
        if self.cancelled.is_set():               # 끄기 · 켜기 모두 멈춘 뒤에는 하지 않는다 — 끝의 켜기는 run() 의 finally 가 한다
            raise Stopped("멈춤")
        return self.plug.recharge(gap_s=self.cfg.stuck_rule()[2]) if on else self.plug.off()

    # --- 순서 ---
    def run(self) -> dict:
        e = set_entry(self.cfg, self.k) or {}
        self._save(set=self.k, mode=self.mode, state="running", step="precheck", started=self.now(),
                   fingerprint=fingerprint(e) if e else None, why="")
        try:
            if self.mode == "calib":
                f = open(self.dir / f"calib_{time.strftime('%Y%m%d_%H%M%S', time.gmtime(self.now() + 9 * 3600))}.csv",
                         "w", newline="", encoding="utf-8-sig")
                self._calib_cols = sorted(set(self.gk) | {s for g in self.groups.values() for s in g})
                self._calib = csv.writer(f)
                self._calib.writerow(["t", "plug_on", "watts"] + self._calib_cols)
            result = self._steps()
        except Stopped as s:
            result = {"state": "inconclusive", "why": f"검증을 멈춤 — {s}"}
        except Inconclusive as s:
            result = {"state": "inconclusive", "why": str(s)}
        except Exception as ex:
            result = {"state": "inconclusive", "why": f"검증 중 오류 — {type(ex).__name__}: {ex}"}
        finally:
            if self._calib is not None:
                f.close()
            if not self.stalled:                  # 멈춘 것으로 판정됐으면 관리자가 이미 켰다 (멈춤 요청은 여기서 켠다)
                try:
                    self.plug.recharge(gap_s=self.cfg.stuck_rule()[2])
                except Exception as ex:
                    self.rec.log(f"짝 검증 끝 — 플러그 {self.kcfg.plug_mac} 켜기 실패: {ex}")
        med = self._median_k()
        self._save(**result, step="done", ended=self.now(), off_minutes=round(self.off_s / 60, 1), median_end=med)
        if result["state"] == "failed":
            self.on_fault(f"세트 {self.k} 짝 검증 실패 — {result['why']}")
        return self.doc

    def _steps(self) -> dict:
        if self.mode != "calib" and not PAIR.get("calibrated"):
            return {"state": "refused", "why": "보정 실측(U2) 전 — 판정 검증은 열리지 않는다. 기록 전용(calib)만 된다"}
        heard = self._heard()
        miss = [s for s in self.gk if s not in heard]
        if miss:
            return {"state": "refused", "why": f"세트 셀 {len(self.gk) - len(miss)}/{len(self.gk)} 만 들린다 — {miss[:4]}"}
        if self._read() is None:
            return {"state": "refused", "why": f"플러그 {self.kcfg.plug_mac} 를 읽지 못함"}
        self._save(step="extract", median_start=self._median_k())
        self._extract()
        med = self._median_k()
        if med is not None and med > PAIR["prep_target_pct"]:
            self._save(step="prep_discharge")
            why = self._prep(med)
            if why:
                return {"state": "failed", "why": why}
        self._save(step="charge_check")
        r = self._set(True)
        t0 = self.now()
        while not (r and r.on and r.watts == r.watts and r.watts >= self.cfg.plug_on_min_w):
            if self.now() - t0 > 300:
                return {"state": "inconclusive", "why": f"켠 뒤 5분 안에 충전 전력({self.cfg.plug_on_min_w:g} W)이 안 보임"}
            self._wait(SAMPLE_S)
            r = self._read()
        self._record(120, True)
        quick = self._quick()
        if quick["verdict"] != "pass" and self.mode != "calib":
            return {"state": "failed", "why": quick["why"]}
        pattern = self._pattern()
        if self.mode == "calib":
            return {"state": "calib_done", "why": "기록 전용 — 판정하지 않았다"}
        if pattern["verdict"] == "unknown":
            return {"state": "inconclusive", "why": f"95 % 이상 셀이 많아 판정 불가 {pattern['unknown']}"}
        if pattern["verdict"] == "fail":
            return {"state": "failed", "why": f"따르지 않은 세트 셀 {pattern['not_following']} · 따른 다른 셀 {pattern['foreign']}"}
        return {"state": "passed", "why": "빠른 · 패턴 검증 통과"}

    def _extract(self) -> None:
        """검증 전 세트 k 셀 추출 · 삭제 — 운전하지 않는 세트는 지워 주는 사람이 없어 약 46시간이면 저장이 가득 찬다(B-M1)."""
        token = None
        if self.gate is not None:
            self.gate.queue(f"verify-{self.k}")
            t0 = self.now()
            while (token := self.gate.try_take(f"verify-{self.k}")) is None:
                if self.now() - t0 > SAFETY["extract_wait_s"]:
                    self.gate.leave(f"verify-{self.k}")
                    raise Stopped("추출 문을 30분 넘게 기다림")
                self._wait(SAMPLE_S)
        try:
            self.link.extract(self.gk, self.dir / "ftg" / "pair")
        finally:
            if self.gate is not None:
                self.gate.release(token)

    def _prep(self, med: float) -> str | None:
        """준비 방전 — 70 % 까지 끈다(최대 3시간). 30분 안에 3 %p 못 내려가면 짝 이상(명세 3-2).
        시간 울타리(3시간)는 셀이 들리든 말든 먼저 본다. 세트 셀 중앙값을 PREP_BLIND_S 넘게 못 읽으면 판정 불가로 끝낸다 —
        고치기 전에는 못 읽는 동안 'continue' 가 울타리까지 건너뛰어, 셀이 안 들리면 Pk 가 꺼진 채 끝없이 돌 수 있었다(코드 검토 2)."""
        self._set(False)
        t0, m0 = self.now(), med
        checked = False
        blind = None
        while True:
            self._record(60, False)
            if self.now() - t0 > PREP_MAX_S:
                return None
            m = self._median_k()
            if m is None:
                blind = blind if blind is not None else self.now()
                if self.now() - blind >= PREP_BLIND_S:
                    raise Inconclusive(f"준비 방전 중 세트 {self.k} 셀이 {PREP_BLIND_S / 60:.0f}분 동안 들리지 않음 — "
                                       f"플러그 {self.kcfg.plug_mac} 를 켜고 판정 불가로 끝냈다")
                continue
            blind = None
            if not checked and m0 - m >= SAFETY["start_drop_pct"]:
                checked = True
            if not checked and self.now() - t0 >= SAFETY["start_s"]:
                return (f"준비 방전 {SAFETY['start_s'] / 60:.0f}분에 세트 셀 중앙값 {m - m0:+.0f}%p — "
                        f"{SAFETY['start_drop_pct']:g}%p 도 안 내려갔다. 이 플러그가 이 Dock 을 먹이지 않는다")
            if m <= PAIR["prep_target_pct"]:
                return None

    def _not_busy(self) -> None:
        t0 = self.now()
        while (why := self.busy()):
            if self.now() - t0 > BUSY_WAIT_MAX_S:
                raise Stopped(f"운전 세트의 단계 전환과 겹쳐 {BUSY_WAIT_MAX_S / 60:.0f}분 기다림 — {why}")
            self._save(step="waiting", why=f"미룸 — {why}")
            self._wait(60)

    def _quick(self) -> dict:
        self._not_busy()
        self._save(step="quick")
        ph0 = self.phases()
        on0 = self.now(); self._record(PAIR["quick_on_s"], True)
        self._set(False)
        off0 = self.now(); self._record(PAIR["quick_off_s"], False); off1 = self.now()
        self._set(True)
        ph1 = self.phases()
        skip = {g for g in self.groups if ph0.get(g) != ph1.get(g)}      # 창 안에 자기 플러그가 바뀐 무리는 ③ 에서 뺀다
        q = P.quick_judge(self.samples, (on0, off0), (off0, off1), self.gk, self.groups, PAIR, skip)
        self._save(quick={k: v for k, v in q.items() if k != "r"} | {"skipped": sorted(map(str, skip))})
        return q

    def _pattern(self) -> dict:
        self._not_busy()
        self._save(step="pattern")
        segs = []
        for i in range(5):
            on = i % 2 == 0
            if i:
                self._set(on)
            t0 = self.now(); self._record(PAIR["pattern_step_s"], on)
            segs.append((t0, self.now(), on))
        self._set(True)
        r = P.pattern_judge(self.samples, segs, self.gk, PAIR)
        self._save(pattern={k: v for k, v in r.items() if k != "cells"})
        return r


# ---------- 엔진 안에서 검증기를 띄우고 지키는 관리자 ----------

def single_busy(runner) -> str | None:
    """단일 모드 — 세트 1 의 단계 전환이 검증 창(약 8 · 25분)과 겹칠 것 같으면 그 이유(명세 3-2 '시작하지 않는 때')."""
    st = runner.current
    if st is None:
        return "세트 1 이 사이클을 시작하는 중"
    if st.phase in ("THRESHOLD", "PLUG_ON", "EXTRACT", "EXTRACT_WAIT", "FULL", "PLUG_OFF", "RECOVER", "HOLD"):
        return f"세트 1 이 {st.phase}"
    b = runner._batts()
    if st.phase == "DISCHARGE" and b and min(b) <= runner.cfg.discharge_stop_pct + 8:
        return "세트 1 이 곧 기준선"
    if st.phase in ("CHARGE", "PRECHARGE") and b and min(b) >= 97:
        return "세트 1 이 곧 만충"
    return None


class VerifyManager:
    """세트 1 실행기의 표본 고리(단일 모드) · 조정자(다중 모드)가 20초마다 tick 을 부른다.

    · data/set{k}/pair_request.json ({"action": "start" | "abort", "mode": "verify" | "calib"}) 을 읽고 지운다
    · 검증기는 한 번에 하나. 띄우면 engine.json 의 verifying 에 세트 · MAC · 주소를 적고(감시자 · atexit 가 Pk 도 켠다), 끝나면 지운다
    · 검증기 심박이 STALL_S 넘게 멈추면 Pk 를 켜고 cancelled · stalled 를 세운다(A-H3)
    · 검증이 실패하면 실행기의 external_fault 를 세운다 — 실행기가 다음 표본에서 '모든 플러그 켬'(명세 3-6)을 한다
    · 검증기가 있으면 단일 모드에서도 추출 문을 켠다 — 세트 1 의 추출과 겹치지 않게(명세 3-7)
    """

    def __init__(self, cfg: Config, live, rec0, make_plug, make_link, make_rec, plugs: list,
                 now: Callable[[], float] = time.time, start_thread: bool = True,
                 sleep: Callable[[float], None] | None = None):
        self.cfg, self.live, self.rec0 = cfg, live, rec0
        self.make_plug, self.make_link, self.make_rec = make_plug, make_link, make_rec
        self.plugs, self.now, self.start_thread = plugs, now, start_thread
        self.sleep = sleep or (lambda s: time.sleep(s))
        self.watcher: threading.Thread | None = None
        self.active: PairVerifier | None = None
        self.active_plug = None
        self.thread: threading.Thread | None = None
        self._mu = threading.Lock()

    def busy_ids(self) -> set:
        """지금 짝 검증 중인 세트 — 교차 감시 (d) 가 그 세트를 빼고 본다(검증기가 그 플러그를 끄고 켠다)."""
        v = self.active
        return {v.k} if v is not None else set()

    def others(self) -> list[int]:
        return [s["id"] for s in self.cfg.sets if isinstance(s, dict) and isinstance(s.get("id"), int) and s["id"] != 1]

    def tick(self, runner) -> None:
        with self._mu:
            self._watch_stall()
            for k in self.others():
                path = set_dir(self.cfg, k) / "pair_request.json"
                req = read_json(path)
                if req is None:
                    continue
                try:
                    path.unlink()
                except OSError:
                    pass
                if req.get("action") == "abort":
                    if self.active is not None and self.active.k == k:
                        self.active.cancelled.set()
                        self.rec0.log(f"짝 검증 멈춤 요청 — 세트 {k}")
                elif self.active is not None:
                    self.rec0.log(f"세트 {k} 짝 검증 요청 — 세트 {self.active.k} 검증이 도는 중이라 받지 않음")
                else:
                    self._start(runner, k, "calib" if req.get("mode") == "calib" else "verify")

    def _groups(self, runner, k: int) -> dict:
        g = {"세트 1": list(runner.cfg.serials)}
        reg = set(runner.cfg.serials)
        for s in self.cfg.sets:
            if isinstance(s, dict) and isinstance(s.get("id"), int) and s["id"] not in (1, k):
                g[f"세트 {s['id']}"] = expand_serials(s.get("serials", []))
        for s in self.cfg.sets:
            if isinstance(s, dict):
                reg |= set(expand_serials(s.get("serials", [])))
        snap = self.live.snapshot()
        g["미등록"] = sorted(x for x in snap if x not in reg)
        return {n: v for n, v in g.items() if v}

    def _start(self, runner, k: int, mode: str) -> None:
        from .shared import ExtractGate
        kcfg = set_cfg(self.cfg, k)
        rec = self.make_rec(k, kcfg)
        plug = self.make_plug(kcfg)
        if runner.gate is None:
            runner.gate = ExtractGate()
        v = PairVerifier(self.cfg, k, self.live, plug, self.make_link(kcfg, rec), rec, gate=runner.gate, mode=mode,
                         groups=self._groups(runner, k), busy=lambda: single_busy(runner),
                         phases=lambda: {"세트 1": runner.current.phase if runner.current else None},
                         on_fault=lambda why: setattr(runner, "external_fault", why), now=self.now)
        self.active, self.active_plug = v, plug
        self.plugs.append(plug)
        self.rec0.engine_update(verifying=[{"id": k, "mac": kcfg.plug_mac.upper(), "ip": kcfg.plug_ip_hint}])
        rec.event(0, "PAIR", "pair_check", "-", f"세트 {k} 짝 검증 시작 ({'기록 전용 보정' if mode == 'calib' else '판정'})")
        self.rec0.log(f"세트 {k} 짝 검증 시작 · {mode}")
        if self.start_thread:
            self.thread = threading.Thread(target=self._run, args=(v,), daemon=True, name=f"pair-verifier-{k}")
            self.thread.start()
            # 표본 고리가 돌지 않는 동안(단일 모드에서 세트 1 의 RECOVER · 추출 — 수십 분)에도 멈춤 요청 · 심박을 본다(코드 검토 2)
            self.watcher = threading.Thread(target=self._watch_loop, args=(v,), daemon=True, name=f"pair-watch-{k}")
            self.watcher.start()

    def _watch_loop(self, v: PairVerifier) -> None:
        while self.active is v:
            self.sleep(WATCH_S)
            try:
                self.watch()
            except Exception as e:
                self.rec0.log(f"짝 검증 감시 오류 — {type(e).__name__}: {e}")

    def watch(self) -> None:
        """멈춤 요청과 심박만 본다 — 시작 요청은 tick 의 몫이다(실행기의 고리 밖에서 새 검증을 띄우지 않는다)."""
        with self._mu:
            self._watch_stall()
            v = self.active
            if v is None:
                return
            path = set_dir(self.cfg, v.k) / "pair_request.json"
            req = read_json(path)
            if req is not None and req.get("action") == "abort":
                try:
                    path.unlink()
                except OSError:
                    pass
                v.cancelled.set()
                self.rec0.log(f"짝 검증 멈춤 요청 — 세트 {v.k}")

    def _run(self, v: PairVerifier) -> None:
        try:
            doc = v.run()
            v.rec.event(0, "PAIR", "pair_check", "-", f"세트 {v.k} 짝 검증 끝 · {doc.get('state')} · {doc.get('why')}")
        finally:
            self.finish(v)

    def finish(self, v: PairVerifier) -> None:
        with self._mu:
            if self.active is v:
                if self.active_plug in self.plugs:
                    self.plugs.remove(self.active_plug)
                self.active = self.active_plug = None
                self.rec0.engine_update(verifying=None)

    def _watch_stall(self) -> None:
        v = self.active
        if v is None or v.stalled or self.now() - v.beat <= STALL_S:
            return
        v.stalled = True
        v.cancelled.set()
        why = f"짝 검증기가 {self.now() - v.beat:.0f}초 동안 멈춤 — 플러그 {v.kcfg.plug_mac} 를 켜고 판정 불가로 끝냈다"
        self.rec0.log(why)
        try:
            self.make_plug(v.kcfg).recharge(gap_s=self.cfg.stuck_rule()[2])
        except Exception as e:
            self.rec0.log(f"멈춘 검증기의 플러그 켜기 실패 — {type(e).__name__}: {e} (engine.json verifying 로 감시자가 이어서 켠다)")
            return
        v._save(state="inconclusive", why=why)
