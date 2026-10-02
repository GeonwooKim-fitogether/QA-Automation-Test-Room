#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""검증 실패 경로 확인 — 일부러 망가뜨린 입력을 넣어 엔진이 '명확히' 실패하는지 본다.

각 항목은 (이름, config 변형, data 변형, 오류 메시지에 반드시 들어가야 할 조각)이다.
엔진이 종료 코드 2로 죽고 그 조각이 메시지에 있으면 통과.
'조용히 넘어감'(코드 0으로 HTML 생성)은 실패로 본다.

마지막에는 반대 방향도 본다 — **맞는 입력은 통과해야 한다**(하위호환 확인).

실행:  python3 tools/test_validation.py
"""

import copy
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
# 이 도구는 창고 전용이라 스킬 폴더 밖(tools/integration-board/)에 있다.
ROOT = os.path.dirname(os.path.dirname(HERE))
SKILL = os.path.join(ROOT, ".claude", "skills", "integration-board")
ASSETS = os.path.join(SKILL, "assets")
ENGINE = os.path.join(ASSETS, "integration_board_engine.py")
BASE_CFG = json.load(open(os.path.join(ASSETS, "board.config.example.json"), encoding="utf-8"))
BASE_DATA = json.load(open(os.path.join(ASSETS, "board.example.json"), encoding="utf-8"))
WEB_CFG = os.path.join(ASSETS, "board.config.web-example.json")
WEB_DATA = os.path.join(ASSETS, "board.web-example.json")


def work(d, wid):
    return next(w for w in d["works"] if w["id"] == wid)


def card(c, key):
    return next(x for x in c["cards"] if x["key"] == key)


CASES = [
    # ── 없는 것을 가리키는 참조 ─────────────────────────────────────────
    ("config에 없는 공용 자산을 참조",
     None, lambda d: work(d, "PR105-B").__setitem__("touches", "tokenz"),
     "공용 자산 'tokenz'를 찾을 수 없습니다"),

    ("배열로 준 touches 안에 없는 자산",
     None, lambda d: work(d, "PR105-A").__setitem__("touches", ["token", "ghost"]),
     "공용 자산 'ghost'를 찾을 수 없습니다"),

    ("알 수 없는 상태 값",
     None, lambda d: work(d, "PR111").__setitem__("status", "merged"),
     "상태 'merged'를 찾을 수 없습니다"),

    ("config에 없는 제품 영역",
     None, lambda d: work(d, "PR120").__setitem__("area", "firmware"),
     "제품 영역 'firmware'를 찾을 수 없습니다"),

    ("없는 작업을 위반이 참조",
     None, lambda d: d["violations"].__setitem__(0, {"check": "spine", "ref": "PR999",
                                                     "severity": "block"}),
     "작업 'PR999'를 찾을 수 없습니다"),

    ("없는 자동 검사를 위반이 참조",
     None, lambda d: d["violations"][0].__setitem__("check", "spinez"),
     "자동 검사 'spinez'를 찾을 수 없습니다"),

    ("없는 자동 검사를 해소 작업이 참조",
     None, lambda d: work(d, "PR122").__setitem__("fixes", "spinez"),
     "자동 검사 'spinez'를 찾을 수 없습니다"),

    ("배열로 준 fixes 안에 없는 검사",
     None, lambda d: work(d, "PR122").__setitem__("fixes", ["spine", "ghostcheck"]),
     "자동 검사 'ghostcheck'를 찾을 수 없습니다"),

    ("없는 작업을 선행 작업으로 지정",
     None, lambda d: work(d, "PR112").__setitem__("deps", ["PR000"]),
     "작업 'PR000'를 찾을 수 없습니다"),

    ("카드가 없는 자동 검사를 가리킴",
     lambda c: card(c, "ov-spine").__setitem__("metric", {"kind": "check", "check": "ghost"}), None,
     "자동 검사 'ghost'를 찾을 수 없습니다"),

    ("카드가 없는 관심사 열에 속함",
     lambda c: card(c, "ov-token").__setitem__("lane", "misc"), None,
     "관심사 열(lane) 'misc'를 찾을 수 없습니다"),

    # ── 필수 누락 · 여분 필드(산문 슬롯 시도) ───────────────────────────
    ("필수 항목 누락 (작업 제목)",
     None, lambda d: work(d, "PR111").pop("title"),
     "필수 항목이 빠졌습니다"),

    ("스키마에 없는 여분 필드 (산문 슬롯 시도)",
     None, lambda d: work(d, "PR111").__setitem__("comment", "이번 주에 잘 진행되고 있습니다"),
     "스키마에 없는 항목입니다"),

    ("data 최상위에 여분 필드 (요약문 끼워넣기 시도)",
     None, lambda d: d.__setitem__("summary", "전반적으로 순조롭습니다"),
     "data.summary: 스키마에 없는 항목입니다"),

    ("카드에 자유 서술 필드를 추가하려는 시도",
     lambda c: card(c, "ov-token").__setitem__("reading", "3팀이 서로 다르게 고쳤다"), None,
     "스키마에 없는 항목입니다"),

    ("data.updated에 자유 서술",
     None, lambda d: d.__setitem__("updated", "이번 주 금요일 회의 직전 기준"),
     "YYYY-MM-DD 또는 YYYY-MM-DD HH:MM"),

    # ── 알 수 없는 값 ────────────────────────────────────────────────────
    ("알 수 없는 위반 무게",
     None, lambda d: d["violations"][0].__setitem__("severity", "critical"),
     "알 수 없는 위반 무게(severity)입니다"),

    ("막힘 사유에 없는 값",
     None, lambda d: work(d, "PR111").__setitem__("block", "stuck"),
     "알 수 없는 막힘 사유(block)입니다"),

    ("알 수 없는 지표 종류",
     lambda c: card(c, "ov-undated").__setitem__("metric", {"kind": "vibes"}), None,
     "알 수 없는 지표 종류(kind)입니다"),

    ("정본에 없는 색 이름",
     lambda c: c["statuses"][0].__setitem__("tone", "#ff00ff"), None,
     "알 수 없는 색 이름입니다"),

    ("알 수 없는 문구 키",
     lambda c: c.__setitem__("text", {"laneOK": "문제 없음"}), None,
     "알 수 없는 문구 키입니다"),

    ("알 수 없는 검사 실행 결과",
     None, lambda d: d["checkRuns"].__setitem__("spine", "green"),
     "알 수 없는 실행 결과입니다"),

    ("선언하지 않은 검사의 실행 결과",
     None, lambda d: d["checkRuns"].__setitem__("ghostcheck", "pass"),
     "data.checkRuns.ghostcheck: 스키마에 없는 항목입니다"),

    # ── 서로 어긋나는 선언 ───────────────────────────────────────────────
    ("통과라고 보고했는데 위반이 올라와 있음",
     None, lambda d: d["checkRuns"].__setitem__("spine", "pass"),
     "실행 결과를 'pass'로 보고했는데"),

    ("팀 명부에 없는 팀",
     None, lambda d: work(d, "PR111").__setitem__("team", "유령팀"),
     "config.teams 명부에 없는 팀입니다"),

    ("작업 id 중복",
     None, lambda d: d["works"].append(dict(work(d, "PR111"))),
     "id가 중복됩니다"),

    ("drift 카드가 있는데 data.drift가 없음",
     None, lambda d: d.pop("drift"),
     "data.drift가 없습니다"),

    ("drift 데이터가 있는데 config.drift가 없음",
     lambda c: c.pop("drift"), None,
     "config.drift가 선언돼 있지 않습니다"),

    # ── 날짜 · 범위 ──────────────────────────────────────────────────────
    ("계획 시작일만 있고 종료일이 없음",
     None, lambda d: work(d, "PR111").pop("planEnd"),
     "planStart와 planEnd는 함께"),

    ("종료일이 시작일보다 빠름",
     None, lambda d: work(d, "PR111").__setitem__("planEnd", "2026-07-01"),
     "계획 종료일이 시작일보다 빠릅니다"),

    ("날짜 형식이 틀림",
     None, lambda d: d.__setitem__("today", "2026/07/26"),
     "날짜는 YYYY-MM-DD 형식이어야 합니다"),

    ("달력에 없는 날짜",
     None, lambda d: work(d, "PR111").__setitem__("planEnd", "2026-02-31"),
     "달력에 없는 날짜입니다"),

    ("완료일 형식이 틀림",
     None, lambda d: work(d, "PR113").__setitem__("completedAt", "어제"),
     "날짜는 YYYY-MM-DD 형식이어야 합니다"),

    ("진척률이 범위를 벗어남",
     None, lambda d: work(d, "PR111").__setitem__("progress", 140),
     "100 이하여야 하는데 140입니다"),

    ("눈금 간격이 축 길이보다 큼",
     lambda c: c.__setitem__("axis", {"days": 30, "tickDays": 60}), None,
     "축 길이(days)보다 눈금 간격이 큽니다"),

    ("경계값이 0 이하",
     lambda c: c.__setitem__("thresholds", {"laneMaxCards": 0}), None,
     "1 이상이어야 하는데 0입니다"),

    # ── 문구 서식 ────────────────────────────────────────────────────────
    ("문구에 없는 자리표시자",
     lambda c: c.__setitem__("text", {"laneMore": "그 외 {count}건"}), None,
     "쓸 수 없는 자리표시자입니다"),

    ("문구에 숫자 강조 표시를 넣으려는 시도",
     lambda c: c.__setitem__("text", {"laneOk": "[[이상 없음]]"}), None,
     "강조 표시는 문구(text)에 쓸 수 없습니다"),

    ("엔진이 고정한 도출 문장을 덮어쓰려는 시도",
     lambda c: c.__setitem__("text", {"readUndated": "{n}건 남았다"}), None,
     "알 수 없는 문구 키입니다"),

    # ── 일반 카운터 지표 ─────────────────────────────────────────────────
    ("count 지표에 조건이 없음",
     lambda c: card(c, "ov-undated").__setitem__("metric", {"kind": "count", "match": {}}), None,
     "match가 최소 1개 필요합니다"),

    ("count 지표가 작업에 없는 항목으로 셈",
     lambda c: card(c, "ov-undated").__setitem__(
         "metric", {"kind": "count", "match": {"mood": "bad"}}), None,
     "셀 수 없습니다: mood"),

    # ── 타입 ────────────────────────────────────────────────────────────
    ("touches에 객체를 넣음 (예전에는 크래시)",
     None, lambda d: work(d, "PR105-A").__setitem__("touches", {"a": 1}),
     "문자열 배열이어야 하는데"),

    ("deps에 숫자를 넣음",
     None, lambda d: work(d, "PR112").__setitem__("deps", 3),
     "문자열 배열이어야 하는데"),

    ("상태 라벨에 숫자를 넣음",
     lambda c: c["statuses"][0].__setitem__("label", 7), None,
     "문자열이어야 하는데"),
]

# 통과해야 하는 입력 — 새 기능과 하위호환이 실제로 동작하는지 반대 방향으로 본다.
GOOD_CASES = [
    ("기본 예시 그대로", None, None),
    ("checkRuns가 아예 없는 예전 data (하위호환)",
     None, lambda d: d.pop("checkRuns")),
    ("touches가 문자열 하나인 예전 형식 (하위호환)",
     None, lambda d: work(d, "PR105-A").__setitem__("touches", "token")),
    ("teams 명부를 선언하지 않은 예전 config (하위호환)",
     lambda c: c.pop("teams"), None),
    ("작업을 가리키지 않는 위반 (저장소 단위)",
     None, lambda d: d["violations"].append({"check": "e2e", "severity": "warn"})),
    ("한 작업이 여러 자산을 건드림",
     None, lambda d: work(d, "PR111").__setitem__("touches", ["token", "contract"])),
    ("경계값을 프로젝트가 다시 정함",
     lambda c: c.__setitem__("thresholds", {"stallRiskDays": 3, "delayRiskDays": 7,
                                            "recentDays": 30, "laneMaxCards": 10}), None),
    ("일반 카운터 지표",
     lambda c: card(c, "ov-undated").__setitem__(
         "metric", {"kind": "count", "match": {"block": "waiting"}, "warnAt": 1, "riskAt": 5}), None),
    ("축 밖으로 나가는 계획 (거부하지 않고 표식으로 알린다)",
     None, lambda d: work(d, "PR111").update({"planStart": "2027-05-01", "planEnd": "2027-06-01"})),
]


def run(cfg, data):
    with tempfile.TemporaryDirectory() as tmp:
        cp, dp = os.path.join(tmp, "c.json"), os.path.join(tmp, "d.json")
        op = os.path.join(tmp, "o.html")
        json.dump(cfg, open(cp, "w", encoding="utf-8"), ensure_ascii=False)
        json.dump(data, open(dp, "w", encoding="utf-8"), ensure_ascii=False)
        p = subprocess.run([sys.executable, ENGINE, "--config", cp, "--data", dp, "--out", op],
                           capture_output=True, text=True)
        return p.returncode, p.stdout + p.stderr, os.path.exists(op)


def run_html(cfg, data):
    """렌더까지 하고 만들어진 HTML 본문을 돌려준다.

    D절은 '거부되는가'가 아니라 '무슨 문장이 나오는가'를 보기 때문에 본문이 필요하다.
    """
    with tempfile.TemporaryDirectory() as tmp:
        cp, dp = os.path.join(tmp, "c.json"), os.path.join(tmp, "d.json")
        op = os.path.join(tmp, "o.html")
        json.dump(cfg, open(cp, "w", encoding="utf-8"), ensure_ascii=False)
        json.dump(data, open(dp, "w", encoding="utf-8"), ensure_ascii=False)
        p = subprocess.run([sys.executable, ENGINE, "--config", cp, "--data", dp, "--out", op],
                           capture_output=True, text=True)
        html = open(op, encoding="utf-8").read() if os.path.exists(op) else ""
        return p.returncode, p.stdout + p.stderr, html


def run_files(cfg_path, data_path):
    with tempfile.TemporaryDirectory() as tmp:
        op = os.path.join(tmp, "o.html")
        p = subprocess.run([sys.executable, ENGINE, "--config", cfg_path,
                            "--data", data_path, "--out", op], capture_output=True, text=True)
        return p.returncode, p.stdout + p.stderr, os.path.exists(op)


def main():
    npass = nfail = 0

    print("A. 잘못된 입력은 막혀야 한다 (종료 코드 2 · 명확한 메시지 · HTML 미생성)")
    for name, mutc, mutd, expect in CASES:
        cfg, data = copy.deepcopy(BASE_CFG), copy.deepcopy(BASE_DATA)
        if mutc:
            mutc(cfg)
        if mutd:
            mutd(data)
        rc, out, made = run(cfg, data)
        good = (rc == 2) and (expect in out) and not made
        npass += good
        nfail += not good
        print(f"  {'통과' if good else '실패'}  {name}")
        if not good:
            print(f"        종료 코드 {rc} · HTML 생성 {made} · 기대 문구 {expect!r}")
            print("        " + out.strip().replace("\n", "\n        ")[:900])

    print("\nB. 맞는 입력은 통과해야 한다 (종료 코드 0 · HTML 생성)")
    for name, mutc, mutd in GOOD_CASES:
        cfg, data = copy.deepcopy(BASE_CFG), copy.deepcopy(BASE_DATA)
        if mutc:
            mutc(cfg)
        if mutd:
            mutd(data)
        rc, out, made = run(cfg, data)
        good = rc == 0 and made
        npass += good
        nfail += not good
        print(f"  {'통과' if good else '실패'}  {name}")
        if not good:
            print("        " + out.strip().replace("\n", "\n        ")[:900])

    print("\nC. 도메인이 다른 번들 예시(사내 웹 서비스)도 그대로 렌더돼야 한다")
    rc, out, made = run_files(WEB_CFG, WEB_DATA)
    good = rc == 0 and made
    npass += good
    nfail += not good
    print(f"  {'통과' if good else '실패'}  board.config.web-example.json + board.web-example.json")
    if not good:
        print("        " + out.strip()[:900])

    print("\nD. 사실이 없으면 초록불이 아니라 '알 수 없다'로 나와야 한다")
    # 보드가 저지를 수 있는 가장 위험한 거짓말은 '거부'가 아니라 '조용한 초록불'이다.
    # 아무도 멈춘 기간을 안 적었는데 "멈춘 작업 없음"이라고 말하면, 읽는 사람은
    # 모르는 상태를 안전한 상태로 착각한다. 그래서 두 방향을 함께 못 박는다.
    for name, strip, want, avoid in (
        ("멈춘 기간 미보고 → 회색 '알 수 없다'",
         ("stalledDays",), "멈춘 기간이 보고되지 않았다", "멈춰 있는 작업 없음"),
        # planStart도 함께 지운다 — 계획일은 짝이어야 하므로 한쪽만 지우면 검증에서 먼저 막힌다.
        ("계획일·예상일 미보고 → 회색 '알 수 없다'",
         ("planStart", "planEnd", "eta"),
         "계획일과 예상일이 보고되지 않았다", "계획보다 늦은 작업 없음"),
    ):
        data = copy.deepcopy(BASE_DATA)
        for w in data["works"]:
            for f in strip:
                w.pop(f, None)
        rc, out, html = run_html(copy.deepcopy(BASE_CFG), data)
        good = rc == 0 and want in html and avoid not in html
        npass += good
        nfail += not good
        print(f"  {'통과' if good else '실패'}  {name}")
        if not good:
            print(f"        종료 코드 {rc} · 기대 문구 있음 {want in html} · "
                  f"금지 문구 없음 {avoid not in html}")
            print("        " + out.strip()[:600])

    # 반대 방향 — 사실을 적고 그 사실이 '정상'이면 초록 문장이 그대로 나와야 한다.
    data = copy.deepcopy(BASE_DATA)
    for w in data["works"]:
        w["stalledDays"] = 0
        if w.get("planEnd"):
            w["eta"] = w["planEnd"]
    rc, out, html = run_html(copy.deepcopy(BASE_CFG), data)
    good = rc == 0 and "멈춰 있는 작업 없음" in html and "계획보다 늦은 작업 없음" in html
    npass += good
    nfail += not good
    print(f"  {'통과' if good else '실패'}  사실을 적고 정상이면 초록 문장이 나온다")
    if not good:
        print("        " + out.strip()[:600])

    # ── E절 · 판정 사유가 실제 원인을 이름으로 대는가 ──────────────────────
    #
    # 예전에는 위험이면 원인과 무관하게 "공용 자산 충돌이 풀리지 않았다"가 나왔다.
    # 화면에서 사람이 가장 먼저 읽는 줄이 거짓을 말할 수 있었으므로, 판정을 몰고 간
    # 카드의 제목과 읽는 문장이 사유에 들어가는지 확인한다.
    def verdict_why(cfg, data):
        rc, out, html = run_html(cfg, data)
        if rc != 0:
            return None, out
        m = re.search(r'"verdict":\s*(\{.*?\}),\s*"decides"', html, re.S)
        return (json.loads(m.group(1)) if m else None), out

    def check(name, cond, detail=""):
        nonlocal npass, nfail
        npass += bool(cond)
        nfail += not cond
        print(f"  {'통과' if cond else '실패'}  {name}")
        if not cond and detail:
            print("        " + str(detail)[:400])

    # E-1. 원인이 하나면 그 카드 이름과 읽는 문장이 사유에 들어간다.
    data = copy.deepcopy(BASE_DATA)
    v, out = verdict_why(copy.deepcopy(BASE_CFG), data)
    if v is None:
        check("판정 사유에 원인 카드가 들어간다", False, out)
    else:
        # 카드 제목을 그냥 부분 문자열로 찾으면 안 된다. 실제로 옛 문장
        # "…해소 전에는 본선 반영을 멈춘다"가 카드 제목 '본선 반영'을 우연히 품고 있어
        # 깨진 엔진에서도 통과해 버렸다. 그래서 엔진이 제목을 감쌀 때 쓰는 따옴표까지
        # 함께 확인해, 우연한 부분 일치로는 통과할 수 없게 한다.
        titles = [c["title"] for c in BASE_CFG["cards"]]
        check("판정 사유가 원인 카드의 이름을 댄다",
              any(t and f"‘{t}’" in v["why"] for t in titles), v["why"])
        check("판정 사유에 옛 하드코딩 문장이 남지 않았다",
              "공용 자산 충돌이 풀리지 않았다" not in v["why"], v["why"])

    # E-2. 프로젝트가 사유를 직접 적었으면 엔진이 덮어쓰지 않는다.
    cfg = copy.deepcopy(BASE_CFG)
    cfg["verdicts"] = {"risk": {"why": "우리가 직접 적은 사유"},
                       "warn": {"why": "우리가 직접 적은 사유"}}
    v, out = verdict_why(cfg, copy.deepcopy(BASE_DATA))
    check("config로 적은 판정 사유를 엔진이 존중한다",
          v is not None and v["why"] == "우리가 직접 적은 사유", (v or out))

    # E-3. 아무 문제가 없으면 정상 문장이 나오고, 원인을 지어내지 않는다.
    data = copy.deepcopy(BASE_DATA)
    for w in data["works"]:
        w["stalledDays"] = 0
        w.pop("block", None)
        if w.get("planEnd"):
            w["eta"] = w["planEnd"]
        else:
            w["planStart"], w["planEnd"] = "2026-07-01", "2026-07-20"
            w["eta"] = "2026-07-20"
    data["violations"] = []
    data["checkRuns"] = {k: "pass" for k in data.get("checkRuns", {})}
    v, out = verdict_why(copy.deepcopy(BASE_CFG), data)
    check("전부 정상이면 정상 판정이 나온다", v is not None and v["word"] == "정상", (v or out))

    print(f"\n결과: 통과 {npass} · 실패 {nfail}")
    return 1 if nfail else 0


if __name__ == "__main__":
    sys.exit(main())
