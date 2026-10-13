"""자동 합류 — 세트 k 를 PIN 한 번으로 보정 → 문턱 도출 → 짝 검증 → 운전 켜기 → 다음 경계 합류까지 잇는다.

엔진 안에서만 돈다(셀 라이브 포트를 엔진이 독점). 판정은 pair.py · derive.py(순수), 한 단계의 실행은 verifier.PairVerifier,
이 파일은 그 단계들을 **시간 맞춰 끊김 없이** 잇는 순서와 상태 기록(data/set{k}/onboard.json)이다.

  (a) 사전 확인 + (b) 기록 전용 보정(추출 · 필요하면 준비 방전 · 켬 끔 기록)   PairVerifier(mode=calib)
  (c) 문턱 자동 도출 — 애매하면 inconclusive 로 멈춘다                         derive.derive_thresholds → pair_thresholds.json
  (d) 판정 검증(빠른 + 패턴, 도출한 문턱)                                        PairVerifier(mode=verify, rule=도출한 문턱)
  (e) 운전 관문을 보고 bench.json 의 run:true 를 원자적으로 쓴다                 config.set_run (결과판 '/api/run' 과 같은 쓰기)
  (f) 다음 만충 경계에 엔진이 스스로 재구성 → 세트 k 가 운전 목록에 들어 첫 방전을 시작하면 합류 완료  VerifyManager.tick 이 poll

어느 단계든 실패하면 플러그 k 를 켠 채 멈추고 알린다. 짝 검증이 실패하면(짝 이상) 운전을 켜지 않는다.
엔진이 다시 시작되면 (f) 의 '합류 기다림' 말고는 이어 가지 않고 aborted 로 끝낸다 — 사람이 다시 누른다.
세트 1 은 건드리지 않는다.

onboard.json — step(단계) · started · ended · result(진행 중이면 null, 아니면 joined · failed · inconclusive · aborted · refused) · why
"""
from __future__ import annotations

import csv
import threading
import time
from pathlib import Path

from . import derive as D
from .config import DERIVE, ONBOARD, PAIR, set_dir, set_entry, set_run
from .control import read_json, write_json_atomic

ONBOARD_FILE = "onboard.json"
RESULT_LABEL = {"joined": "합류 완료", "failed": "실패", "inconclusive": "판정 불가", "aborted": "중단", "refused": "시작 안 함"}
STEP_LABEL = {"precheck": "사전 확인", "calib": "보정 기록", "derive": "문턱 도출", "verify": "짝 검증", "enable": "운전 켜기",
              "joining": "합류 기다림", "joined": "합류 완료"}


def onboard_path(cfg, k: int) -> Path:
    return set_dir(cfg, k) / ONBOARD_FILE


def read_onboard(cfg, k: int) -> dict:
    return read_json(onboard_path(cfg, k)) or {}


def is_running(doc: dict | None) -> bool:
    """진행 중인 합류인가 — 결과가 아직 없다."""
    return bool(doc) and doc.get("result") is None and bool(doc.get("step"))


def load_calib_rows(path) -> list:
    """calib_<시각>.csv → [(시각, 플러그 켬 여부, {시리얼: 배터리 %})] (derive.Rows)."""
    with open(path, encoding="utf-8-sig", newline="") as f:
        rd = csv.reader(f)
        head = next(rd, None) or []
        cols = [int(c) for c in head[3:]]
        rows = []
        for line in rd:
            if len(line) < 3:
                continue
            try:
                t, on = float(line[0]), line[1].strip().lower() == "true"
                b = {c: int(float(v)) for c, v in zip(cols, line[3:]) if v != ""}
            except ValueError:
                continue
            rows.append((t, on, b))
    return rows


def _plug_note(plug, sleep) -> str:
    """끝날 때 플러그 k 가 켜졌는지 한 번 확인하고(꺼져 있으면 끊지 않고 켠다) 알림 글에 쓸 한 줄을 돌려준다."""
    from .plug import ensure_on
    try:
        r = plug.read()
        if not r.on:
            r = ensure_on(plug, sleep=sleep)
        return f"플러그 켜짐 · {r.watts:.0f} W"
    except Exception as e:
        return f"플러그 상태를 확인하지 못함 — {type(e).__name__}: {e} (감시자가 켠다)"


def finish_doc(mgr, k: int, doc: dict, result: str, why: str, plug: str = "") -> dict:
    """합류를 끝내고(result) onboard.json 을 닫고 알린다 — 결과판 · Slack. joined 는 노랑, 나머지는 빨강(onboard_failed)."""
    now = mgr.now()
    doc.update(result=result, why=why, ended=now, t=now)
    if result == "joined":
        doc["step"] = "joined"
    write_json_atomic(onboard_path(mgr.cfg, k), doc)
    rec = mgr.rec_for(k)
    if result == "joined":
        rec.event(0, "ONBOARD", "onboard_joined", "-", f"자동 합류 완료 — {why}")
    else:
        rec.event(0, "ONBOARD", "onboard_failed", "-",
                  f"자동 합류 {RESULT_LABEL.get(result, result)} ({STEP_LABEL.get(doc.get('step'), doc.get('step'))} 단계) — {why}"
                  + (f" · {plug}" if plug else "") + " · 사람이 확인 후 다시 누른다")
    return doc


def write_refused(mgr, k: int, why: str) -> None:
    """시작 요청을 받지 못한 이유를 남긴다 — 진행 중인 합류의 기록은 덮지 않는다."""
    if is_running(read_onboard(mgr.cfg, k)):
        return
    now = mgr.now()
    finish_doc(mgr, k, {"set": k, "step": "precheck", "started": now, "steps": []}, "refused", why)


class Onboarder:
    """세트 k 하나의 자동 합류 — run() 이 (a) ~ (e) 를 한 스레드에서 끊김 없이 잇는다. (f) 는 핸드오프 뒤 VerifyManager.tick 이 이어 본다."""

    def __init__(self, mgr, runner, k: int, rec, plug, link, by: str = ""):
        self.mgr, self.runner, self.k = mgr, runner, k
        self.rec, self.plug, self.link = rec, plug, link
        self.cfg = mgr.cfg
        self.by = by
        self.now = mgr.now
        self.cancelled = threading.Event()        # 멈춤 요청 — 단계 사이에서 본다(도는 검증기에는 mgr 가 따로 전한다)
        self.beat = self.now()
        self.doc: dict = {}
        self.verifier = None

    # --- 기록 ---
    def _save(self, **kw) -> None:
        self.doc.update(kw, t=self.now())
        write_json_atomic(onboard_path(self.cfg, self.k), self.doc)

    def _step(self, step: str, note: str = "") -> None:
        steps = list(self.doc.get("steps") or []) + [{"step": step, "t": self.now(), "note": note}]
        self._save(step=step, steps=steps[-20:])

    def _event(self, kind: str, text: str) -> None:
        self.rec.event(0, "ONBOARD", kind, "-", text)

    # --- 순서 ---
    def run(self) -> dict:
        self.doc = {"set": self.k, "step": "precheck", "result": None, "why": "", "started": self.now(), "ended": None,
                    "steps": [], "by": self.by}
        self._save()
        self._event("onboard_start", "자동 합류 시작 — 셀 추출 · 보정 기록 · 문턱 도출 · 짝 검증 · 운전 켜기. 어느 단계든 실패하면 "
                                     f"플러그 {self.k} 를 켠 채 멈추고 알린다. 세트 1 은 건드리지 않는다")
        try:
            res = self._flow()
        except Exception as ex:                    # 감싸개 — 어떤 오류도 합류를 '진행 중'으로 남기지 않는다
            res = ("failed", f"자동 합류 중 오류 — {type(ex).__name__}: {ex}")
        if res is not None:
            self._end(*res)
        return self.doc

    def _end(self, result: str, why: str) -> None:
        """실패 · 중단 · 판정 불가 — 플러그 k 를 켠 채 닫는다(검증기가 이미 켰다. 여기서 한 번 더 확인한다)."""
        finish_doc(self.mgr, self.k, self.doc, result, why, _plug_note(self.plug, self.mgr.sleep))

    def _can_skip(self) -> bool:
        """이미 통과한 짝 검증(24시간 안)과 도출한 문턱이 있으면 처음부터 다시 하지 않는다 — 운전 관문이 닫혀 멈췄던 합류를 다시 누를 때."""
        from .verifier import load_thresholds, pair_ok
        doc = read_json(set_dir(self.cfg, self.k) / "pair.json") or {}
        recent = self.now() - float(doc.get("ended") or 0) <= ONBOARD["skip_pass_max_age_s"]
        return bool(pair_ok(self.cfg, self.k) and doc.get("mode") == "verify" and recent and load_thresholds(self.cfg, self.k))

    def _flow(self):
        from .verifier import rule_for
        if self._can_skip():
            return self._enable("이미 통과한 짝 검증 · 도출한 문턱이 있다 — 운전 켜기 단계부터")
        # (a)(b) 사전 확인 + 기록 전용 보정
        self._step("calib")
        v = self._verifier("calib", PAIR)
        doc = self._run(v)
        if self.cancelled.is_set():
            return ("aborted", "사람이 멈춤 — 보정 기록 단계")
        st = doc.get("state")
        if st != "calib_done":
            why = f"{doc.get('why')}"
            return ("refused", why) if st == "refused" else ("failed" if st == "failed" else "inconclusive", f"보정 기록 단계 — {why}")
        self._event("onboard_calib_done", f"보정 기록 끝 — {doc.get('calib_file')} · 이어서 문턱을 도출한다")
        # (c) 문턱 도출
        self._step("derive")
        res = D.derive_thresholds(load_calib_rows(v.calib_path), v.gk, v.groups, PAIR, DERIVE)
        self._save(derive={k: res[k] for k in ("verdict", "code", "why", "thresholds", "evidence")})
        if res["verdict"] != "derived":
            if res["fault"]:                       # 대조군이 플러그를 따라 움직임 = 짝이 틀렸다는 증거 → 짝 이상과 같은 안전 동작
                self.mgr.fault(f"세트 {self.k} 자동 합류 문턱 도출 — {res['why']}")
                return ("failed", f"문턱 도출 단계 — {res['why']} (짝 이상: 등록된 모든 플러그를 켜고 운전 세트를 세웠다)")
            return ("inconclusive", f"문턱 도출 단계 — 애매해서 정하지 않았다 [{res['code']}] {res['why']}")
        from .verifier import THRESHOLDS_FILE, fingerprint
        e = set_entry(self.cfg, self.k) or {}
        write_json_atomic(set_dir(self.cfg, self.k) / THRESHOLDS_FILE,
                          {"set": self.k, "state": "derived", "fingerprint": fingerprint(e), "derived_at": self.now(),
                           "calib_file": doc.get("calib_file"), "thresholds": res["thresholds"], "evidence": res["evidence"],
                           "rule": {k: DERIVE[k] for k in ("resp_ratio", "noise_mult", "separation", "noise_quantile")},
                           "why": res["why"]})
        if self.cancelled.is_set():
            return ("aborted", "사람이 멈춤 — 문턱 도출 뒤")
        # (d) 판정 검증 — 보정 직후라 추출은 다시 하지 않고, 준비 방전도 70 % + 여유를 넘을 때만
        self._step("verify", f"도출한 문턱 {res['thresholds']}")
        v2 = self._verifier("verify", rule_for(self.cfg, self.k), skip_extract=True, prep_slack=ONBOARD["prep_slack_pct"])
        doc2 = self._run(v2)
        if self.cancelled.is_set():
            return ("aborted", "사람이 멈춤 — 짝 검증 단계")
        st = doc2.get("state")
        if st != "passed":
            return ("failed" if st == "failed" else "refused" if st == "refused" else "inconclusive",
                    f"짝 검증 단계 — {doc2.get('why')}" + (" (짝 이상: 등록된 모든 플러그를 켜고 운전 세트를 세웠다)" if st == "failed" else ""))
        self._event("onboard_verified", f"짝 검증 통과 — {doc2.get('why')}. 운전 관문을 보고 운전을 켠다")
        return self._enable()

    def _enable(self, note: str = ""):
        """(e) 운전 관문(보정 · 짝 · 클라우드 판 · 설정)을 모두 보고 닫혀 있으면 운전을 켜지 않는다. 열려 있으면 bench.json 에 run:true."""
        from .bench import gate_problems
        self._step("enable", note)
        cfg = self.mgr.reload()
        problems = [p for p in gate_problems(cfg, self.k, self.mgr.schema_v2()) if not p.startswith("운전이 꺼져 있다")]
        if problems:
            return ("failed", "운전 켜기 단계 — 운전 관문이 닫혀 있어 운전을 켜지 않았다: " + " · ".join(problems)
                    + ". 짝 검증은 통과해 기록이 남았다 — 관문이 열린 뒤 '자동 합류 시작'을 다시 누르면 운전 켜기부터 이어 간다")
        try:
            set_run(self.k, True, self.mgr.bench_path)
        except (KeyError, ValueError, OSError) as ex:
            return ("failed", f"운전 켜기 단계 — bench.json 을 고치지 못함 ({type(ex).__name__}: {ex})")
        (set_dir(self.cfg, self.k) / "hold").unlink(missing_ok=True)
        self._event("onboard_run_on", f"운전을 켰다 — 세트 1 의 다음 만충 경계에서 엔진이 스스로 다시 시작해 세트 {self.k} 가 합류한다 "
                                      f"(최대 {ONBOARD['join_wait_max_s'] // 3600} 시간 기다린다)")
        self._save(step="joining", joining_since=self.now(), why="운전을 켰다 — 다음 만충 경계에서 합류한다")
        return None                                # 핸드오프 — 끝난 것이 아니다. 이후는 VerifyManager.tick → poll_joining 이 본다

    # --- 도우미 ---
    def _verifier(self, mode: str, rule: dict, **opt):
        return self.mgr.new_verifier(self.runner, self.k, mode, self.rec, self.plug, self.link, rule, **opt)

    def _run(self, v) -> dict:
        self.verifier = v
        self.mgr.attach(v)
        try:
            doc = v.run()
        finally:
            self.mgr.detach(v)
        self.rec.event(0, "PAIR", "pair_check", "-", f"세트 {self.k} 짝 검증 끝 · {doc.get('state')} · {doc.get('why')}")
        return doc


# ---------- 핸드오프 뒤 — 합류 기다림 (VerifyManager.tick 이 20초마다 부른다) ----------

def poll_joining(mgr) -> None:
    """운전을 켠 세트가 합류했는지 본다.

    · 엔진이 아직 단일 모드(운전 목록에 없음): 경계를 기다린다. 사람이 운전을 껐거나 관문이 다시 닫혔거나 한도(join_wait_max_s)를 넘으면 끝낸다 —
      실패로 끝내면 run:true 를 되돌려 나중에 말없이 합류하는 일이 없게 한다.
    · 운전 목록에 들었음(다중 모드): 그 세트 now.json 의 단계가 DISCHARGE 면 첫 방전 시작 = 합류 완료. 세트 정지로 가면 실패.
    """
    from .bench import gate_problems
    for k in mgr.others():
        doc = read_onboard(mgr.cfg, k)
        if not (is_running(doc) and doc.get("step") == "joining"):
            continue
        now = mgr.now()
        if k in mgr.running:
            ph = (read_json(set_dir(mgr.cfg, k) / "now.json") or {}).get("phase")
            if ph == "DISCHARGE":
                cyc = (read_json(set_dir(mgr.cfg, k) / "now.json") or {}).get("cycle")
                finish_doc(mgr, k, doc, "joined", f"세트 {k} 가 운전 목록에 들어 첫 방전을 시작했다 (사이클 {cyc}) — 세트 1 과 함께 돈다")
            elif ph in ("HOLD", "DONE", "STOPPED"):
                finish_doc(mgr, k, doc, "failed", f"운전 목록에는 들었지만 세트 {k} 가 {ph} 로 갔다 — 플러그는 켠 채 지킨다")
            elif not doc.get("in_run_list"):
                doc.update(in_run_list=now, t=now, why="운전 목록에 들었다 — 예비 충전부터 하고 첫 방전을 기다린다")
                write_json_atomic(onboard_path(mgr.cfg, k), doc)
            elif now - float(doc["in_run_list"]) > ONBOARD["first_discharge_max_s"]:
                finish_doc(mgr, k, doc, "failed", f"운전 목록에 든 뒤 {ONBOARD['first_discharge_max_s'] // 3600} 시간 안에 첫 방전이 시작되지 않았다")
            continue
        try:
            cfg = mgr.reload()
        except Exception:
            continue
        e = set_entry(cfg, k) or {}
        if e.get("run") is not True:
            finish_doc(mgr, k, doc, "aborted", "기다리는 동안 운전이 꺼졌다(사람이 껐거나 등록을 바꿈) — 합류하지 않는다")
            continue
        problems = gate_problems(cfg, k, mgr.schema_v2())
        late = now - float(doc.get("joining_since") or now) > ONBOARD["join_wait_max_s"]
        if problems or late:
            why = ("기다리는 동안 운전 관문이 닫혔다 — " + " · ".join(problems)) if problems else \
                  f"{ONBOARD['join_wait_max_s'] // 3600} 시간이 지나도 세트 1 의 만충 경계에서 재구성되지 않았다 (감시자 일시 중지 · 추출 중 · 엔진 이상 확인)"
            try:
                set_run(k, False, mgr.bench_path)              # 말없이 나중에 합류하지 않게 운전을 되돌린다
                why += " — 운전(run)을 껐다"
            except (KeyError, ValueError, OSError) as ex:
                why += f" — 운전을 끄지 못함({type(ex).__name__}) 직접 확인"
            finish_doc(mgr, k, doc, "failed", why)


def recover(mgr, running_ids) -> None:
    """엔진이 (다시) 시작할 때 — 진행 중이던 합류를 정리한다. '합류 기다림'만 이어 가고(경계 재구성은 의도한 재시작이다),
    그 밖의 단계는 이어 가지 않고 aborted 로 끝낸다(플러그 k 는 시작하며 inherit_plugs 가 켰다). 죽은 검증기가 남긴 pair.json 의 '검증 중'도 닫는다."""
    for k in mgr.others():
        doc = read_onboard(mgr.cfg, k)
        if not is_running(doc) or doc.get("step") == "joining":
            continue
        pj = read_json(set_dir(mgr.cfg, k) / "pair.json") or {}
        if pj.get("state") == "running":
            pj.update(state="inconclusive", why="엔진이 다시 시작돼 검증이 중단됐다", step="done", ended=mgr.now())
            write_json_atomic(set_dir(mgr.cfg, k) / "pair.json", pj)
        finish_doc(mgr, k, doc, "aborted", "엔진이 다시 시작돼 진행 중이던 단계를 이어 가지 않는다 — 플러그는 시작하며 켰다",
                   "플러그 켬(시작 때 inherit_plugs)")
