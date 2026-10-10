"""다중 세트 조정자 BenchRunner (명세 2-1 · 2-2 · 2-7 · 2-8 · 4절).

운전 목록이 둘 이상일 때만 쓴다. 세트마다 지금의 CycleRunner 를 스레드 하나로 돌리고, 조정자(주 스레드)는 사이클 판정을 하지 않는다.
조정자의 일: 명령 나누기 · 엔진 심박(beat.json) · 세트 심박 감시와 울타리 · 짝 검증 요청 · 끝내기(재구성 · 경계 종료 · 모두 끝남).

울타리(D9): 세트 실행기의 심박이 FENCE_S 넘게 멈춘 것이 2회 연속이면 모든 플러그를 켜고 exit=fenced 로 끝낸다 — 감시자가 되살린다.
추출 문 토큰도 그 재시작으로 풀린다. 부분 울타리(한 세트만 끄기 막기)는 두지 않는다(A-H2).
"""
from __future__ import annotations

import csv
import threading
import time
from pathlib import Path
from typing import Callable

from .config import PAIR, Config, run_gate_problems, set_cfg, set_dir, set_entry
from .control import ControlInbox, command_expired, read_json, write_json_atomic
from .shared import ExtractGate, LinkGuard

BEAT_FILE = "beat.json"
END_REQUEST = "end_request"
FENCE_S = 600.0                    # 세트 실행기 심박 멈춤 문턱 — 정상 최악 공백 약 290초보다 크게(명세 2-4)
FENCE_TICKS = 2
DEFAULT_TARGET = 500               # 세트별 목표 사이클 (D7)
CHARGE_SIDE = {"PRECHARGE", "PLUG_ON", "EXTRACT", "EXTRACT_WAIT", "CHARGE", "RECOVER", "HOLD", "DONE", "STOPPED"}
DISCHARGE_SIDE = {"DISCHARGE", "THRESHOLD", "PLUG_OFF"}


def hold_path(cfg: Config, set_id: int) -> Path:
    """세트 정지 표지 — 세트 1 은 data/hold, 세트 k 는 data/set{k}/hold (명세 2-1). 있으면 그 세트는 HOLD 로 시작한다."""
    return set_dir(cfg, set_id) / "hold"


def completed_cycles(path: Path) -> int:
    """완료 사이클 수 — 방전 시작과 만충이 모두 적힌 줄 (D7)."""
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            return sum(1 for r in csv.DictReader(f) if r.get("discharge_start") and r.get("full_at"))
    except OSError:
        return 0


def target_cycles(cfg: Config, set_id: int) -> int:
    e = set_entry(cfg, set_id) or {}
    t = e.get("target_cycles", DEFAULT_TARGET)
    return t if isinstance(t, int) and not isinstance(t, bool) and t > 0 else DEFAULT_TARGET


def gate_problems(cfg: Config, set_id: int, schema_v2: bool) -> list[str]:
    """세트 k 를 운전 목록에 넣지 못하는 이유 전부 (명세 2-1). 빈 목록이면 운전 목록에 든다."""
    from .verifier import pair_ok
    p = run_gate_problems(cfg, set_id)
    if not pair_ok(cfg, set_id):
        p.append("짝 검증 통과 기록이 없거나 지문이 다르다")
    if not PAIR.get("calibrated"):
        p.append("보정 실측(U2) 전")
    if not schema_v2:
        p.append("클라우드 스키마 판 2 확인 전(U1)")
    return p


def desired_ids(cfg: Config, schema_v2: bool) -> tuple[list[int], dict]:
    """(운전 목록, {세트 id: 못 드는 이유}) — 세트 1 은 언제나. 세트 정지 표지가 있어도 관문을 지나면 목록에 든다(HOLD 로 시작)."""
    ids, why = [1], {}
    for s in cfg.sets if isinstance(cfg.sets, list) else []:
        k = s.get("id") if isinstance(s, dict) else None
        if not isinstance(k, int) or isinstance(k, bool) or k == 1 or s.get("run") is not True:
            continue
        p = gate_problems(cfg, k, schema_v2)
        if p:
            why[k] = p
        else:
            ids.append(k)
    return ids, why


def start_with_precharge(cfg: Config, set_id: int, args_precharge: bool, prev_exit: str | None) -> bool:
    """세트의 시작 단계 (명세 2-3). --precharge 면 모두 예비 충전. 아니면 마지막 now.json 단계로 — 기록이 없거나 충전 쪽이면 예비 충전,
    방전 쪽이거나 FULL 에서 reconfigure 로 끝났으면 방전부터."""
    if args_precharge:
        return True
    ph = (read_json(set_dir(cfg, set_id) / "now.json") or {}).get("phase")
    if ph is None:
        return True
    if ph in DISCHARGE_SIDE or (ph == "FULL" and prev_exit == "reconfigure"):
        return False
    return True


def fault_plugs(cfg: Config, own: int) -> list[dict]:
    """세트 own 의 실행기가 짝 이상 때 켤 다른 플러그 전부 — 운전하지 않는 등록 세트까지. 세트 1 은 실효 MAC(--config 로 덮었을 수 있다)."""
    out = []
    for s in cfg.sets if isinstance(cfg.sets, list) else []:
        k = s.get("id") if isinstance(s, dict) else None
        if not isinstance(k, int) or k == own:
            continue
        if k == 1:
            out.append({"id": 1, "mac": str(cfg.plug_mac).upper(), "ip": cfg.plug_ip_hint})
        elif s.get("plug_mac"):
            out.append({"id": k, "mac": str(s["plug_mac"]).upper(), "ip": str(s.get("plug_ip") or "")})
    return out


class SetThread:
    """세트 하나 — 실행기와 그 스레드, 끝난 모양."""

    def __init__(self, k: int, runner, cycles: int, precharge: bool, hold_why: str | None):
        self.k, self.runner, self.cycles, self.precharge, self.hold_why = k, runner, cycles, precharge, hold_why
        self.kind: str | None = None          # done · stopped · failsafe · reconfigure · interrupted · crash
        self.terminal = threading.Event()     # 운전을 끝내고 HOLD · DONE 에 들었다(또는 스레드가 끝났다)
        self.stale_ticks = 0
        self.thread = threading.Thread(target=self._main, daemon=True, name=f"set-{k}")

    def _main(self) -> None:
        r = self.runner
        try:
            if self.hold_why:
                self.kind = "stopped"
                self.terminal.set()
                r.hold(self.hold_why)
                return
            if self.cycles <= 0:
                self.kind = "done"
            else:
                self.kind = r.run(self.cycles, precharge=self.precharge)
            if self.kind == "reconfigure":
                self.terminal.set()
                return
            self.terminal.set()
            why = {"done": "목표 사이클 도달", "stopped": "세트 정지", "failsafe": "연속 실패 · 짝 이상"}.get(self.kind, self.kind)
            r.hold(why, phase="DONE" if self.kind == "done" else "HOLD")
        except KeyboardInterrupt:
            self.kind = self.kind or "interrupted"
        except Exception as e:                # 스레드 감싸개 — 그 세트만 플러그 켬 + HOLD (명세 4절)
            self.kind = "failsafe"
            r.rec.log(f"세트 {self.k} 실행기 오류 — {type(e).__name__}: {e}")
            try:
                r.rec.event(r.current.cycle if r.current else 0, "-", "set_hold", "-", f"실행기 오류 {type(e).__name__}: {e}")
                r.plug_on_at_exit = r._safe_end(r.current)
            except Exception:
                pass
            self.terminal.set()
            try:
                r.hold(f"실행기 오류 {type(e).__name__}")
            except Exception:
                pass
        finally:
            self.terminal.set()


class BenchRunner:
    def __init__(self, cfg: Config, args, ids: list[int], rec0, cloud, build: Callable, plugs: list,
                 verify=None, desired: Callable[[], list[int] | None] | None = None, now: Callable[[], float] = None,
                 sleep: Callable[[float], None] = None, paused: Callable[[], bool] = lambda: False):
        self.cfg, self.args, self.ids, self.rec0, self.cloud = cfg, args, ids, rec0, cloud
        self.plugs, self.verify = plugs, verify
        self.desired = desired or (lambda: None)
        self.now = now or time.time
        self._sleep = sleep or time.sleep
        self.paused = paused
        self.halt = threading.Event()
        self.gate = ExtractGate(clock=self.now)
        self.link_guard = LinkGuard([str(k) for k in ids])
        self.queues: dict[int, list] = {k: [] for k in ids}
        self.sets: dict[int, SetThread] = {}
        self.reconfigure = False
        self._end_req = False
        self.fault_why: str | None = None
        self.started = self.now()
        prev = read_json(Path(cfg.data_dir) / "engine.json") or {}
        for k in ids:
            r = build(k)
            r.name, r.gate, r.link_guard, r._halt = str(k), self.gate, self.link_guard, self.halt
            r.commands = (lambda kk=k: self._drain(kk))
            r.owns_bench = k == 1
            r.on_pair_fault = self._pair_fault
            r.boundary_hook = self._boundary
            r.fault_plugs = fault_plugs(cfg, k)
            # (d) 교차 감시는 운전하지 않는 세트만 본다 — 운전 세트는 자기 방전으로 늘 내려간다(코드 검토 1)
            r._cross.groups = {g: v for g, v in r._cross.groups.items() if g not in ids}
            hold = hold_path(cfg, k)
            why = None
            if hold.exists():
                why = (hold.read_text(encoding="utf-8", errors="replace").strip() or "세트 정지 표지")[:200]
            done = completed_cycles(set_dir(cfg, k) / "cycles.csv")
            self.sets[k] = SetThread(k, r, target_cycles(cfg, k) - done,
                                     start_with_precharge(cfg, k, bool(getattr(args, "precharge", False)), prev.get("exit")), why)

    # --- 세트 실행기가 부르는 것 ---
    def _drain(self, k: int) -> list:
        q, self.queues[k] = self.queues[k], []
        return q

    def _pair_fault(self, why: str) -> None:
        """한 세트에서 짝 이상 — 실제 짝을 모르므로 모든 운전 세트를 HOLD 로(명세 3-6). 플러그는 그 세트의 _pair_fault 가 모두 켠다."""
        self.fault_why = why
        for k, s in self.sets.items():
            s.runner.hold_request = f"다른 세트의 짝 이상 — {why}"[:300]

    def _boundary(self, runner) -> bool:
        """만충 직후 — 다시 시작할 차례인가 (재구성 · 경계 종료 요청). 감시자 일시 중지면 미룬다. 추출 문이 차 있으면 다음 경계로."""
        if not self._want_restart() or self.paused() or self.gate.holder is not None:
            return False
        self.reconfigure = True
        return True

    def _want_restart(self) -> bool:
        p = Path(self.cfg.data_dir) / END_REQUEST
        if p.exists():                       # 경계 종료 요청 — 읽는 순간 지운다(명세 2-8). 끝난 이유는 reconfigure
            p.unlink(missing_ok=True)
            self._end_req = True
            self.rec0.log("경계 종료 요청(end_request)을 받았다 — 다음 경계에서 플러그를 켜고 끝낸다")
        if self._end_req:
            return True
        want = self.desired()
        return want is not None and sorted(want) != sorted(self.ids)

    # --- 명령 나누기 (명세 2-7) ---
    def route(self) -> None:
        now = self.now()
        boxes = [(None, ControlInbox(self.cfg.data_dir))] + [(k, ControlInbox(set_dir(self.cfg, k))) for k in self.ids if k != 1]
        items = []
        for k_file, box in boxes:
            c = box.take()
            if c:
                items.append((c, c.get("source", "?"), (lambda res, b=box, n=c.get("cmd"): b.ack(str(n), res)), k_file))
        for c in self.cloud.poll_commands(len(self.ids)) if hasattr(self.cloud, "poll_commands") else []:
            items.append((c, c.get("requested_by") or "cloud", (lambda res, cid=c.get("id"): self.cloud.ack(cid, res)), None))
        for cmd, who, ack, k_file in items:
            name = cmd.get("cmd")
            target = cmd.get("set", cmd.get("set_id"))
            target = k_file if target in (None, "") and k_file is not None else target
            why = command_expired(cmd, self.cfg.command_max_age_s, now, since=self.started)
            if why:
                self._reject(f"원격 명령 {name} ({who}) 실행하지 않고 버림 — {why}", ack, "manual_expired")
                continue
            if target in (None, ""):
                if name == "stop_safe":
                    self.rec0.event(0, "-", "manual", "-", f"원격 명령 전체 정지 ({who})")
                    ack("받음 — 모든 세트 플러그 ON 으로 두고 멈춘다")
                    self.stop_all = True
                    for s in self.sets.values():
                        self.queues[s.k].append(({"cmd": "stop_safe", "t": now}, who, lambda res: None))
                else:
                    self._reject(f"원격 명령 {name} ({who}) — 세트를 지정하지 않아 버림(여러 세트가 돈다)", ack)
                continue
            try:
                k = int(target)
            except (TypeError, ValueError):
                k = None
            if k not in self.sets:
                self._reject(f"원격 명령 {name} ({who}) — 세트 {target} 는 운전 중이 아님", ack)
                continue
            if name == "resume_set":
                hold_path(self.cfg, k).unlink(missing_ok=True)
                ack(f"세트 {k} 정지 표지를 지웠다 — 다음 재구성 경계부터 돈다")
                continue
            if name == "stop_safe":
                hold_path(self.cfg, k).write_text(f"세트 정지 ({who})", encoding="utf-8")
            self.queues[k].append(({**cmd, "set": k}, who, ack))

    stop_all = False

    def _reject(self, text: str, ack, kind: str = "manual_rejected") -> None:
        self.rec0.event(0, "-", kind, "-", text)
        ack("실행하지 않고 버림 — " + text.split(" — ", 1)[-1])

    # --- 심박 · 울타리 ---
    def _beat(self) -> dict:
        out = {}
        for k, s in self.sets.items():
            last = getattr(s.runner.rec, "_last_now", None) or {}
            out[str(k)] = {"beat": last.get("beat") or self.started, "phase": last.get("phase"), "kind": s.kind}
        doc = {"t": self.now(), "beat": self.now(), "mode": "multi", "sets": out}
        write_json_atomic(Path(self.cfg.data_dir) / BEAT_FILE, doc)
        return out

    def _fence_due(self, beats: dict) -> int | None:
        for k, s in self.sets.items():
            if s.terminal.is_set() and s.kind in ("reconfigure", "interrupted"):
                continue
            age = self.now() - float(beats[str(k)]["beat"] or self.started)
            limit = FENCE_S * (2 if beats[str(k)]["phase"] in ("EXTRACT",) else 1)
            s.stale_ticks = s.stale_ticks + 1 if age > limit else 0
            if s.stale_ticks >= FENCE_TICKS:
                return k
        return None

    def all_plugs_on(self, why: str) -> None:
        """모든 운전 세트의 플러그를 병렬로 켠다 — 울타리 · 재구성 · 끝내기 (A-L6)."""
        def one(r):
            try:
                rr = r.plug.on() if why == "reconfigure" else r.plug.recharge(gap_s=self.cfg.stuck_rule()[2])
                r.plug_known = rr.on
            except Exception as e:
                self.rec0.log(f"{why}: 세트 {r.name} 플러그 켜기 실패 — {e}")
        ths = [threading.Thread(target=one, args=(s.runner,), daemon=True) for s in self.sets.values() if s.runner.plug is not None]
        for t in ths:
            t.start()
        for t in ths:
            t.join(timeout=120)

    # --- 고리 ---
    def run(self) -> tuple[str, bool | None]:
        self.rec0.log(f"다중 모드 시작 · 운전 세트 {self.ids}")
        for s in self.sets.values():
            s.thread.start()
        kind = None
        try:
            while True:
                self._sleep(self.cfg.poll_s)
                self.route()
                beats = self._beat()
                if self.verify is not None:
                    try:
                        self.verify(self.sets[1].runner)
                    except Exception as e:
                        self.rec0.log(f"짝 검증 관리자 오류 — {e}")
                fenced = self._fence_due(beats)
                if fenced is not None:
                    kind = "fenced"
                    self.rec0.event(0, "-", "fenced", "-", f"세트 {fenced} 실행기 심박이 {FENCE_S:.0f}초 넘게 멈춤(2회) — "
                                                          f"모든 플러그를 켜고 엔진을 다시 띄운다")
                    break
                if self.reconfigure and any(s.kind == "reconfigure" for s in self.sets.values()):
                    kind = "reconfigure"
                    break
                ended = all(s.terminal.is_set() for s in self.sets.values())
                want = ended and self._want_restart()
                if ended and want and not self.reconfigure and not self.paused() and self.gate.holder is None:
                    kind = "reconfigure"        # 모두 HOLD · DONE — 조정자가 20초 고리에서 판정한다(명세 2-8)
                    break
                if ended and not want:
                    kinds = {s.kind for s in self.sets.values()}
                    kind = "failsafe" if kinds & {"failsafe"} or self.fault_why else "stopped" if "stopped" in kinds else "done"
                    break
        except KeyboardInterrupt:
            kind = "interrupted"
        self.halt.set()
        for s in self.sets.values():
            s.thread.join(timeout=150)
        if kind in ("fenced", "reconfigure", "failsafe", "stopped", "done"):
            self.all_plugs_on(kind)
        on = all(s.runner.plug_known for s in self.sets.values()) if kind != "interrupted" else None
        self.rec0.log(f"다중 모드 끝 · {kind}")
        return kind, on
