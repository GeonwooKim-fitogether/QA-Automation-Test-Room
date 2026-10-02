#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""통합 현황판(Integration Board) 고정 엔진.

무엇을 하나
-----------
프로젝트가 소유한 **config**(어휘·관심사 정의)와 매 실행 갱신되는 **data**(사실)를 읽어,
검증하고, 숫자와 판정을 **직접 계산한 뒤**, 고정 템플릿(board_template.html)에 주입해
자체완결 HTML 한 장을 만든다.

왜 이렇게 하나
--------------
사람이나 LLM이 매번 보고서를 새로 쓰면 판이 매번 달라지고, 숫자는 손으로 옮겨 적히다 틀린다.
그래서 이 엔진은 세 가지를 구조적으로 막는다.

1. **산문 슬롯이 없다.** data에 자유 서술 필드가 없다. 화면에 나오는 모든 문장은
   (a) config에 선언된 고정 설명이거나 (b) 이 엔진이 데이터에서 계산한 문장이다.
2. **스키마를 어기면 렌더하지 않는다.** 필수 누락·알 수 없는 값·없는 id 참조·여분 필드는
   전부 검증에서 걸려 종료 코드 2로 실패한다. 조용히 넘어가지 않는다.
3. **보고되지 않은 것을 통과로 그리지 않는다.** 자동 검사는 실행 결과(data.checkRuns)가
   보고됐을 때만 '통과'가 되고, 보고가 없으면 회색 '정보 없음'으로 남는다.

무엇이 프로젝트마다 달라지나
----------------------------
어휘(팀·제품 영역·공용 자산·자동 검사의 이름과 설명), 무엇을 지켜볼지(카드), 화면 문구(text),
경계값(thresholds), 시간 축(axis)이 config에서 온다. 반대로 **도출 문장(READ)·색·레이아웃·
계산 규칙은 엔진이 고정한다** — 이것이 매 실행 같은 판이 나오는 이유다.

실행
----
    python3 integration_board_engine.py --config <config.json> --data <data.json> --out <out.html>

인자를 모두 생략하면 번들된 예시(board.config.example.json + board.example.json)로 데모를 만든다.

의존성
------
파이썬 표준 라이브러리만 쓴다. 외부 패키지 0. 산출 HTML도 인라인 CSS/JS로 외부 요청 0.

스키마 정본: ../reference/board-schema.md
"""

import argparse
import json
import os
import re
import sys
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_PATH = os.path.join(HERE, "board_template.html")
MARKER = "/*__BOARD_DATA__*/null"

# ══════════════════════════════════════════════════════════════════════════
# 1. 엔진 상수 — 판정 규칙의 골격은 여기 한 곳에 고정한다.
#    경계값(며칠부터 위험인가 등)만 config.thresholds로 열려 있고, data는 못 바꾼다.
# ══════════════════════════════════════════════════════════════════════════

DEFAULT_THRESHOLDS = {
    "stallRiskDays": 14,   # 이 일수 이상 멈춰 있으면 '위험', 1일 이상이면 '주의'
    "delayRiskDays": 30,   # 계획보다 이 일수 이상 늦으면 '위험', 1일 이상이면 '주의'
    "recentDays": 7,       # "최근"의 정의 — 오늘로부터 뒤로 며칠
    "laneMaxCards": 3,     # 관심사 열에 처음부터 펼쳐 두는 카드 수. 나머지는 버튼으로 펼친다.
}

SEV_ORDER = {"risk": 0, "warn": 1, "ok": 2, "info": 3}   # 급한 것이 위로
SEV_EM = {"risk": "r", "warn": "a", "ok": "g", "info": "i"}  # 숫자 강조 색 클래스

# 색은 미리 정해 둔 이름 중에서만 고른다 — 프로젝트가 새 색을 지어낼 수 없게.
TONES = {
    "red": "var(--red)", "amber": "var(--amber)", "green": "var(--green)",
    "blue": "var(--blue)", "teal": "var(--teal)", "dim": "var(--dim)",
    "muted": "var(--muted)", "neutral": "var(--border-strong)",
}

BLOCK_VALUES = ("conflict", "waiting")       # 작업이 막힌 사정
VIOLATION_SEVERITIES = ("block", "warn")     # 위반의 무게
RUN_RESULTS = ("pass", "fail", "skipped", "never")   # 검사 실행 결과 보고
GREY_CHECK_STATES = ("skipped", "never", "unknown")  # 초록이 아니라 회색으로 그리는 상태
METRIC_KINDS = (
    "asset", "check", "area_stalled", "area_delay", "undated", "status_recent",
    "drift", "count",
)
COUNT_FIELDS = ("area", "status", "team", "block", "touches", "drift")
KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
STAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2})?)?$")
PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")
EMPH_RE = re.compile(r"\[\[(.*?)\]\]", re.S)

# 격자 한 줄에 놓을 수 있는 최대 열 수 — 이보다 많으면 줄을 고르게 나눈다(_grid_cols).
MAX_COLS = {"lanes": 5, "kanban": 6, "checks": 7, "assets": 4}

# ── 화면 문구 기본값 ──────────────────────────────────────────────────────
# config의 text 절로 하나씩 덮어쓸 수 있다. config가 비어도 보드가 그대로 동작하도록
# 전부 기본값을 갖는다. {n} 같은 자리표시자를 쓸 수 있는 문구는 PLACEHOLDERS에 적혀 있고,
# 거기 없는 이름을 쓰면 검증에서 거부한다.
TEXT = {
    # 머리·범례
    "metaPrefix": "갱신", "metaSuffix": "· 자동", "metaNote": "고정 계기판 · 같은 링크로 재게시",
    "themeLight": "테마: 밝게", "themeDark": "테마: GC 다크",
    "legendTitle": "색으로 읽는 법", "legendConflict": "충돌 · 위반",
    "legendWaiting": "대기 · 주의", "legendTrace": "연결 보기 (카드를 누르면 켜짐)",
    # 밴드 1
    "band1Hint": "카드를 누르면 아래 두 밴드에서 연결된 항목만 밝아집니다",
    "verdictKicker": "종합 판정", "decideCount": "결정 필요",
    "decideHint": "사람이 정해 주기 전에는 진행할 수 없는 것", "decideBadge": "결정 필요",
    "verdictNoData": "자동 검사 {unknown}개는 실행 결과가 보고되지 않았다 — 그만큼 이 판정은 덜 안다",
    # 판정 사유 — 무엇이 이 판정을 몰고 갔는지 이름을 대는 문장.
    "verdictWhyRisk": "‘{title}’이(가) 막고 있다{more} — {reading}. 해소 전에는 본선 반영을 멈춘다",
    "verdictWhyWarn": "‘{title}’이(가) 걸려 있다{more} — {reading}. 막지는 않지만 결정이 밀려 있다",
    "verdictWhyMore": " (그 외 {rest}건)",
    "countUnit": "건", "laneOk": "이상 없음",
    "laneMore": "그 외 {n}건 펼치기", "laneLess": "접기",
    "meanLabel": "뜻", "whyLabel": "걸리면",
    # 밴드 2
    "viewSwitchLabel": "실행 현황 보기 전환",
    "viewNote": "같은 작업 목록을, 칸반은 상태 축으로 간트는 시간 축으로 봅니다.",
    "tabKanban": "칸반", "tabKanbanEn": "Kanban", "tabGantt": "간트", "tabGanttEn": "Gantt",
    "assetsTitle": "공용 자산 — 여러 팀이 같이 쓰는 것",
    "blockConflict": "충돌", "blockWaiting": "대기",
    "areaBadgeConflict": "충돌 {n}", "areaBadgeWaiting": "대기 {n}",
    "areaCount": "{n}건", "areaCountUndated": "{n}건 · 미정 {u}건",
    "ganttAxisLabel": "제품 영역 / 작업",
    "ganttEmptyArea": '계획 날짜가 있는 작업 없음 — 아래 "일정 미정"에 있습니다',
    "ganttDelayTip": "예상 종료가 계획을 넘김",
    "ganttLegPlan": "계획", "ganttLegFill": "실행(진척률)",
    "ganttLegDelay": "지연 — 예상 종료가 계획을 넘김",
    "ganttLegDep": "의존 — 앞 작업이 끝나야 시작",
    "ganttLegToday": "오늘", "ganttLegDue": "목표일",
    "ganttLegHint": "영역 행을 누르면 그 안의 작업이 펼쳐집니다",
    "axisOffBefore": "축 이전 {n}건", "axisOffAfter": "축 이후 {n}건",
    "axisOffTip": "시간 축 범위 밖이라 막대를 그리지 않았습니다",
    "undatedGroup": "일정 미정", "undatedCount": "{n}건",
    "undatedRow": "계획 시작·종료일 미정 — 시간 축에 올릴 수 없음",
    # 밴드 3
    "checkPass": "통과", "checkWarn": "주의", "checkBlock": "위반",
    "checkSkipped": "건너뜀", "checkNever": "실행된 적 없음", "checkUnknown": "정보 없음",
    "vioBlock": "막힘", "vioWarn": "주의", "vioNoTarget": "특정 작업 지정 없음",
    "okNone": "이상 없음", "noData": "실행 결과 보고 없음",
    # 걸린 검사 옆에 "그럼 이걸 누가 고치고 있나"를 적는 자리.
    "fixSome": "해소 작업 {n}건", "fixNone": "해소 작업 없음", "fixUnknown": "해소 작업 미보고",
    "traceChip": "연결 보기", "traceOff": "해제",
    # 분모가 '전체 팀 수'인지 '그 자산을 건드린 팀 수'인지를 문장이 스스로 말한다.
    # 말해 주지 않으면 팀 명부를 넣고 빼는 것만으로 같은 상황의 숫자가 달라져 보인다.
    "assetScopeAll": "전체", "assetScopeTouched": "건드린",
    "assetBadgeConflict": "{all}팀 중 {n}팀 충돌",
    "assetBadgeWaiting": "{n}팀 등록 대기",
    "assetBadgeOk": "이상 없음",
}

# 문구 키마다 쓸 수 있는 자리표시자. 여기 없는 이름을 쓰면 검증에서 거부한다
# (오타 {count} 같은 것이 화면에 그대로 찍히는 일을 막는다).
PLACEHOLDERS = {
    "laneMore": {"n"}, "areaBadgeConflict": {"n"}, "areaBadgeWaiting": {"n"},
    "areaCount": {"n"}, "areaCountUndated": {"n", "u"}, "undatedCount": {"n"},
    "axisOffBefore": {"n"}, "axisOffAfter": {"n"},
    "assetBadgeConflict": {"n", "all"}, "assetBadgeWaiting": {"n", "all"},
    "verdictNoData": {"unknown"}, "fixSome": {"n"},
    "verdictWhyRisk": {"title", "more", "reading"},
    "verdictWhyWarn": {"title", "more", "reading"},
    "verdictWhyMore": {"rest"},
}

# ── 도출 문장 — **엔진이 고정한다. config로 바꿀 수 없다.** ────────────────
# 화면의 계산 문장은 전부 여기서 나온다. 문장을 프로젝트가 바꿀 수 있게 열어 두면
# 같은 숫자가 프로젝트마다 다른 뜻으로 읽히고, "매 실행 같은 판"이 무너진다.
# `[[ ]]`로 감싼 구간이 숫자 강조(색 굵은 글씨)로 그려지며, 강조 색은 심각도에서 나온다.
# 단위 명사("팀", "건", "일")를 문장 안에 함께 두는 것이 중요하다 — 밖으로 빼면
# countUnit 같은 값 하나가 비었을 때 문장이 "1이 계획보다 14일 늦다"처럼 깨진다.
READ = {
    "assetConflict": "{all}팀 중 [[{n}팀]]이 서로 다르게 고쳤다",
    "assetWaiting": "[[{n}팀]]이 등록을 마치고 승인을 기다린다",
    "assetOk": "충돌 없음 — [[{n}팀]]이 같은 값을 쓴다",
    "checkHit": "위반 후보 [[{n}건]] · ",
    "checkClean": "위반 없음 · ",
    "checkNoRun": "실행 결과 보고 없음 · ",
    "checkTail": "자동 검사 {all}개 중 [[{pass}개]] 통과",
    "checkTailUnknown": "자동 검사 {all}개 중 [[{pass}개]] 통과 · 미보고 {unknown}개",
    # 걸린 검사에만 붙는 꼬리. '없음'은 밴드 1까지 올려 말하고(위반보다 급하므로),
    # '아무도 안 적었다'는 여기서 말하지 않고 밴드 3의 검사 칸에 회색으로 남긴다.
    "checkFixNone": " · [[해소 작업 없음]]",
    "checkFixSome": " · 해소 작업 [[{n}건]]",
    "stallNone": "멈춰 있는 작업 없음",
    "stallNoData": "멈춘 기간이 보고되지 않았다 — 이 구획은 아직 알 수 없다",
    "stall": "{label}{josa} [[{days}일]]째 움직이지 않는다",
    "delayNone": "계획보다 늦은 작업 없음",
    "delayNoData": "계획일과 예상일이 보고되지 않았다 — 늦었는지 알 수 없다",
    "delay": "[[{n}건]]이 계획보다 [[{days}일]] 늦다",
    "undatedNone": "모든 작업이 시간 축에 올라가 있다",
    "undated": "[[{n}건]]이 아직 시간 축에 올라가지 못했다",
    "recent": "최근 {days}일 {label} [[{n}건]]",
    "drift": "{gapLabel} [[{gaps}건]] 누적 · {pivotLabel} [[{pivots}건]]",
    "countNone": "해당하는 작업 없음",
    "count": "해당 작업 [[{n}건]]",
}

DEFAULT_BANDS = {
    "overview": {"n": "밴드 1", "title": "종합 판정", "en": "Executive Overview",
                 "caption": "지금 무엇을 결정해야 하나"},
    "delivery": {"n": "밴드 2", "title": "실행 현황", "en": "Delivery Status",
                 "caption": "일이 어디까지 왔고 어디서 막혔나"},
    "quality":  {"n": "밴드 3", "title": "품질 기준선", "en": "Quality Baseline",
                 "caption": "지켜야 할 선이 지켜지고 있나"},
}

DEFAULT_VERDICTS = {
    # 위험·주의의 이유는 판정을 몰고 간 카드에서 지어내므로(compose 부분 참고) 아래 문장은
    # 몰고 간 카드를 찾지 못한 예외적인 경우에만 쓰인다. 그래서 원인을 특정하지 않는다.
    "risk": {"word": "위험", "tone": "red",
             "why": "막는 것이 있다 — 해소 전에는 본선 반영을 멈춘다"},
    "warn": {"word": "주의", "tone": "amber", "why": "막는 것은 없지만 결정이 밀려 있다"},
    "ok":   {"word": "정상", "tone": "green",
             "why": "막는 것도 걸린 것도 없다 — 순서대로 본선에 반영할 수 있다"},
}

DEFAULT_AXIS = {"leadDays": 14, "days": 84, "tickDays": 7}


# ══════════════════════════════════════════════════════════════════════════
# 2. 검증 — 어기면 렌더하지 않는다
# ══════════════════════════════════════════════════════════════════════════

class Validator:
    """오류를 모아 두었다가 한 번에 전부 보여 준다.

    하나 고치고 다시 돌리면 다음 오류가 나오는 방식은 사람을 지치게 한다.
    그래서 치명적이지 않은 오류는 모두 모아서 함께 낸다.

    또 하나의 규칙: **타입이 틀려도 예외로 죽지 않는다.** 어떤 필드에 무엇이 들어와도
    검증 오류로 바뀌어야 한다. 파이썬 스택 트레이스는 쓰는 사람에게 아무 것도 알려 주지 않는다.
    """

    def __init__(self):
        self.errors = []

    def err(self, path, message, hint=None):
        self.errors.append((path, message, hint))
        return False

    # ── 기본 형태 ────────────────────────────────────────────────────────
    def obj(self, path, value, required=(), optional=()):
        if not isinstance(value, dict):
            return self.err(path, f"객체(JSON object)여야 하는데 {_typename(value)}가 왔습니다")
        ok = True
        for key in required:
            if key not in value:
                ok = self.err(f"{path}.{key}", "필수 항목이 빠졌습니다")
        allowed = set(required) | set(optional)
        for key in sorted(set(value) - allowed):
            ok = self.err(f"{path}.{key}", "스키마에 없는 항목입니다",
                          "쓸 수 있는 항목: " + ", ".join(sorted(allowed)))
        return ok

    def lst(self, path, value, min_len=0):
        if not isinstance(value, list):
            return self.err(path, f"배열(JSON array)이어야 하는데 {_typename(value)}가 왔습니다")
        if len(value) < min_len:
            return self.err(path, f"항목이 최소 {min_len}개 필요한데 {len(value)}개입니다")
        return True

    def text(self, path, value, allow_empty=False):
        if not isinstance(value, str):
            return self.err(path, f"문자열이어야 하는데 {_typename(value)}가 왔습니다")
        if not allow_empty and not value.strip():
            return self.err(path, "빈 문자열은 쓸 수 없습니다")
        return True

    def key(self, path, value):
        if not self.text(path, value):
            return False
        if not KEY_RE.match(value):
            return self.err(path, f"id로 쓸 수 없는 값입니다: {value!r}",
                            "영문/숫자로 시작하고 영문·숫자·  . _ : - 만 쓸 수 있습니다")
        return True

    def enum(self, path, value, allowed, what):
        # 해시할 수 없는 값(배열·객체)이 와도 여기서 조용히 오류로 바뀐다.
        if not isinstance(value, (str, int, float, bool, type(None))) or value not in allowed:
            return self.err(path, f"알 수 없는 {what}입니다: {value!r}",
                            "쓸 수 있는 값: " + ", ".join(sorted(str(a) for a in allowed)))
        return True

    def ref(self, path, value, pool, what):
        if not isinstance(value, str) or value not in pool:
            shown = value if isinstance(value, str) else repr(value)
            return self.err(path, f"{what} '{shown}'를 찾을 수 없습니다",
                            ("선언된 값: " + ", ".join(sorted(pool))) if pool
                            else f"{what}가 하나도 선언돼 있지 않습니다")
        return True

    def strlist(self, path, value):
        """문자열 배열. 문자열 하나가 오면 [그것]으로 흡수한다(하위호환)."""
        if isinstance(value, str):
            return [value]
        if not isinstance(value, list):
            self.err(path, f"문자열 배열이어야 하는데 {_typename(value)}가 왔습니다")
            return None
        out = []
        for i, item in enumerate(value):
            if self.text(f"{path}[{i}]", item):
                out.append(item)
        return out

    def day(self, path, value):
        if not isinstance(value, str) or not DATE_RE.match(value):
            return self.err(path, f"날짜는 YYYY-MM-DD 형식이어야 합니다: {value!r}")
        try:
            date.fromisoformat(value)
        except ValueError:
            return self.err(path, f"달력에 없는 날짜입니다: {value!r}")
        return True

    def stamp(self, path, value):
        """날짜 또는 날짜+시각. 자유 서술이 끼어들지 못하게 형식을 고정한다."""
        if not isinstance(value, str) or not STAMP_RE.match(value):
            return self.err(path, "YYYY-MM-DD 또는 YYYY-MM-DD HH:MM 형식이어야 합니다: "
                                  f"{value!r}")
        if not _is_day(value[:10]):
            return self.err(path, f"달력에 없는 날짜입니다: {value!r}")
        return True

    def whole(self, path, value, lo=None, hi=None):
        if isinstance(value, bool) or not isinstance(value, int):
            return self.err(path, f"정수여야 하는데 {_typename(value)}가 왔습니다")
        if lo is not None and value < lo:
            return self.err(path, f"{lo} 이상이어야 하는데 {value}입니다")
        if hi is not None and value > hi:
            return self.err(path, f"{hi} 이하여야 하는데 {value}입니다")
        return True

    def flag(self, path, value):
        if not isinstance(value, bool):
            return self.err(path, f"true/false여야 하는데 {_typename(value)}가 왔습니다")
        return True


def _is_day(value):
    """실제로 달력에 있는 YYYY-MM-DD인가. 형식만 맞는 '2026-02-31'은 False."""
    if not isinstance(value, str) or not DATE_RE.match(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _typename(value):
    return {dict: "객체", list: "배열", str: "문자열", bool: "true/false",
            int: "정수", float: "소수", type(None): "null"}.get(type(value), type(value).__name__)


def _order(items, keyfield, kept):
    """선언 순서를 그대로 보존한 id 목록. 배열이 아니거나 모양이 이상하면 빈 목록."""
    if not isinstance(items, list):
        return []
    out = []
    for item in items:
        k = item.get(keyfield) if isinstance(item, dict) else None
        if isinstance(k, str) and k in kept and k not in out:
            out.append(k)
    return out


def _keyed(v, path, items, required, optional, keyfield="key", strings=()):
    """key가 있는 목록을 검증하고 {key: item} 사전을 만든다. 중복 key는 오류.

    `strings`에 적은 항목은 문자열인지까지 본다. 여기서 걸러 두지 않으면 라벨 자리에
    숫자나 배열이 들어와도 검증을 통과해, 계산 단계에서 스택 트레이스로 죽는다.
    """
    out = {}
    for i, item in enumerate(items):
        p = f"{path}[{i}]"
        if not v.obj(p, item, required, optional):
            if not isinstance(item, dict):
                continue
        for f in strings:
            if f in item:
                v.text(f"{p}.{f}", item[f], allow_empty=(f not in required))
        if keyfield not in item:
            continue
        if not v.key(f"{p}.{keyfield}", item[keyfield]):
            continue
        k = item[keyfield]
        if k in out:
            v.err(f"{p}.{keyfield}", f"id가 중복됩니다: {k!r}")
            continue
        out[k] = item
    return out


def _check_text_value(v, path, key, value):
    """문구 하나를 검증한다 — 자리표시자가 규격에 맞는가, 강조 표시를 넣지 않았는가."""
    allowed = PLACEHOLDERS.get(key, set())
    bad = sorted(set(PLACEHOLDER_RE.findall(value)) - allowed)
    if bad:
        v.err(path, "이 문구에 쓸 수 없는 자리표시자입니다: " + ", ".join("{%s}" % b for b in bad),
              ("쓸 수 있는 자리표시자: " + ", ".join("{%s}" % a for a in sorted(allowed)))
              if allowed else "이 문구에는 자리표시자를 쓸 수 없습니다")
    if "[[" in value or "]]" in value:
        v.err(path, "[[ ]] 강조 표시는 문구(text)에 쓸 수 없습니다",
              "숫자 강조는 엔진이 도출 문장에서만 씁니다 — 도출 문장은 config로 바꿀 수 없습니다")


def validate_config(v, cfg):
    """config를 검증하고, 기본값을 채운 정규화 결과를 돌려준다."""
    v.obj("config", cfg,
          required=("board", "lanes", "statuses", "areas", "cards"),
          optional=("bands", "verdicts", "assets", "checks", "teams", "drift",
                    "honesty", "axis", "thresholds", "text"))
    if not isinstance(cfg, dict):
        return None

    board = cfg.get("board", {})
    if v.obj("config.board", board, required=("title",),
             optional=("eyebrow", "subtitle", "docTitle")):
        for k in ("title", "eyebrow", "subtitle", "docTitle"):
            if k in board:
                v.text(f"config.board.{k}", board[k], allow_empty=(k != "title"))

    bands = dict((k, dict(b)) for k, b in DEFAULT_BANDS.items())
    if "bands" in cfg and v.obj("config.bands", cfg["bands"], optional=tuple(DEFAULT_BANDS)):
        for name, band in cfg["bands"].items():
            if name not in DEFAULT_BANDS:
                continue
            if v.obj(f"config.bands.{name}", band, optional=("n", "title", "en", "caption")):
                for k, val in band.items():
                    if v.text(f"config.bands.{name}.{k}", val, allow_empty=True):
                        bands[name][k] = val

    # 종합 판정의 말(위험/주의/정상)과 그 이유. 색(tone)은 의미색이라 엔진이 고정한다.
    #
    # 이유(why)는 기본값을 쓰지 않고 **판정을 몰고 간 카드에서 지어낸다**(compose_verdict_why).
    # 프로젝트가 config에 why를 직접 적었다면 그 문장이 이긴다. 어느 쪽인지 구분해야 하므로
    # 직접 적은 판정 이름을 따로 모아 둔다.
    verdicts = dict((k, dict(v0)) for k, v0 in DEFAULT_VERDICTS.items())
    why_fixed = set()
    if "verdicts" in cfg and v.obj("config.verdicts", cfg["verdicts"],
                                   optional=tuple(DEFAULT_VERDICTS)):
        for name, spec in cfg["verdicts"].items():
            if name not in DEFAULT_VERDICTS:
                continue
            if v.obj(f"config.verdicts.{name}", spec, optional=("word", "why")):
                for k, val in spec.items():
                    if v.text(f"config.verdicts.{name}.{k}", val, allow_empty=(k == "why")):
                        verdicts[name][k] = val
                        if k == "why":
                            why_fixed.add(name)

    axis = dict(DEFAULT_AXIS)
    if "axis" in cfg and v.obj("config.axis", cfg["axis"], optional=tuple(DEFAULT_AXIS)):
        for k, val in cfg["axis"].items():
            if v.whole(f"config.axis.{k}", val, lo=1, hi=3650):
                axis[k] = val
    if axis["tickDays"] > axis["days"]:
        v.err("config.axis.tickDays", "축 길이(days)보다 눈금 간격이 큽니다")

    # 경계값은 프로젝트마다 리듬이 달라 열어 둔다(2주 스프린트 vs 반년 리드타임).
    # 기본값은 그대로이므로, 적지 않으면 지금까지와 같은 판정이 나온다.
    thresholds = dict(DEFAULT_THRESHOLDS)
    if "thresholds" in cfg and v.obj("config.thresholds", cfg["thresholds"],
                                     optional=tuple(DEFAULT_THRESHOLDS)):
        for k, val in cfg["thresholds"].items():
            if v.whole(f"config.thresholds.{k}", val, lo=1, hi=3650):
                thresholds[k] = val

    text = dict(TEXT)
    if "text" in cfg and isinstance(cfg["text"], dict):
        for k, val in cfg["text"].items():
            path = f"config.text.{k}"
            if k not in TEXT:
                v.err(path, "알 수 없는 문구 키입니다",
                      "쓸 수 있는 키 목록은 reference/board-schema.md의 '문구(text)' 절에 있습니다")
            elif v.text(path, val, allow_empty=True):
                _check_text_value(v, path, k, val)
                text[k] = val
    elif "text" in cfg:
        v.err("config.text", f"객체여야 하는데 {_typename(cfg['text'])}가 왔습니다")

    # 팀 명부(선택) — 있으면 공용 자산의 "n팀 중 m팀"에서 n이 '전체 팀 수'가 된다.
    # 없으면 n은 '그 자산을 건드린 팀 수'다. 어느 쪽이든 그 자산과 무관한 작업이
    # 늘었다고 분모가 흔들리지 않는다.
    teams = None
    if "teams" in cfg:
        got = v.strlist("config.teams", cfg["teams"])
        if got is not None:
            teams = []
            for t in got:
                if t not in teams:
                    teams.append(t)

    lanes = checks = assets = areas = statuses = {}
    if v.lst("config.lanes", cfg.get("lanes"), 1):
        lanes = _keyed(v, "config.lanes", cfg["lanes"], ("key", "label"), ("en",),
                       strings=("label", "en"))
    if v.lst("config.areas", cfg.get("areas"), 1):
        areas = _keyed(v, "config.areas", cfg["areas"], ("key", "label"), ("en",),
                       strings=("label", "en"))
    if v.lst("config.statuses", cfg.get("statuses"), 1):
        statuses = _keyed(v, "config.statuses", cfg["statuses"],
                          ("key", "label", "tone"), ("mean",), strings=("label", "mean"))
        for k, st in statuses.items():
            v.enum(f"config.statuses[{k}].tone", st.get("tone"), TONES, "색 이름")
    if "assets" in cfg and v.lst("config.assets", cfg["assets"]):
        assets = _keyed(v, "config.assets", cfg["assets"], ("key", "label", "plain"), ("why",),
                        strings=("label", "plain", "why"))
    if "checks" in cfg and v.lst("config.checks", cfg["checks"]):
        # 이름 칸은 다른 항목(lanes·areas·statuses·assets)과 같은 `label`로 통일한다.
        # 예전 파일이 쓰던 `name`도 계속 받는다 — 둘 중 하나만 있으면 된다.
        for i, it in enumerate(cfg["checks"]):
            if not isinstance(it, dict):
                continue
            if "label" in it and "name" not in it:
                it["name"] = it["label"]
            elif "name" not in it and "label" not in it:
                v.err(f"config.checks[{i}].label", "필수 항목이 빠졌습니다",
                      "검사 이름을 적는 칸입니다 (예전 이름 `name`도 받습니다)")
                it["name"] = ""
        checks = _keyed(v, "config.checks", cfg["checks"], ("key", "name", "plain"), ("why", "label"),
                        strings=("name", "plain", "why", "label"))

    drift = cfg.get("drift")
    if drift is not None:
        v.obj("config.drift", drift, required=("gapLabel", "pivotLabel"),
              optional=("note", "linkLabel", "href"))
        if isinstance(drift, dict):
            for k, val in drift.items():
                v.text(f"config.drift.{k}", val,
                       allow_empty=(k not in ("gapLabel", "pivotLabel")))

    if "honesty" in cfg:
        v.text("config.honesty", cfg["honesty"], allow_empty=True)

    cards = {}
    if v.lst("config.cards", cfg.get("cards"), 1):
        cards = _keyed(v, "config.cards", cfg["cards"],
                       ("key", "lane", "title", "plain", "metric"),
                       ("why", "decide", "traceChecks"),
                       strings=("title", "plain", "why"))
        for k, card in cards.items():
            p = f"config.cards[{k}]"
            v.ref(f"{p}.lane", card.get("lane"), set(lanes), "관심사 열(lane)")
            if "decide" in card:
                v.flag(f"{p}.decide", card["decide"])
            if "traceChecks" in card:
                got = v.strlist(f"{p}.traceChecks", card["traceChecks"])
                for i, ck in enumerate(got or []):
                    v.ref(f"{p}.traceChecks[{i}]", ck, set(checks), "자동 검사")
            _validate_metric(v, f"{p}.metric", card.get("metric"),
                             assets, checks, areas, statuses, drift)

    return {"board": board, "bands": bands, "verdicts": verdicts, "whyFixed": why_fixed,
            "axis": axis, "thresholds": thresholds, "text": text, "lanes": lanes,
            "areas": areas, "statuses": statuses, "assets": assets, "checks": checks,
            "teams": teams, "cards": cards, "drift": drift, "honesty": cfg.get("honesty", ""),
            "laneOrder": _order(cfg.get("lanes"), "key", lanes),
            "cardOrder": _order(cfg.get("cards"), "key", cards)}


_METRIC_BINDING = {
    "asset": ("asset", "assets", "공용 자산"),
    "check": ("check", "checks", "자동 검사"),
    "area_stalled": ("area", "areas", "제품 영역"),
    "area_delay": ("area", "areas", "제품 영역"),
    "status_recent": ("status", "statuses", "상태"),
    "undated": (None, None, None),
    "drift": (None, None, None),
    "count": (None, None, None),
}


def _validate_metric(v, path, metric, assets, checks, areas, statuses, drift):
    if not isinstance(metric, dict):
        return v.err(path, f"객체여야 하는데 {_typename(metric)}가 왔습니다")
    kind = metric.get("kind")
    if kind is None:
        return v.err(f"{path}.kind", "필수 항목이 빠졌습니다",
                     "쓸 수 있는 값: " + ", ".join(METRIC_KINDS))
    if not v.enum(f"{path}.kind", kind, METRIC_KINDS, "지표 종류(kind)"):
        return False
    field, pool_name, what = _METRIC_BINDING[kind]
    required = ("kind",) + ((field,) if field else ())
    optional = ("match", "warnAt", "riskAt") if kind == "count" else ()
    if not v.obj(path, metric, required=required, optional=optional):
        return False
    if field:
        pool = {"assets": assets, "checks": checks, "areas": areas, "statuses": statuses}[pool_name]
        v.ref(f"{path}.{field}", metric.get(field), set(pool), what)
    if kind == "drift" and drift is None:
        v.err(path, "kind가 'drift'인 카드가 있는데 config.drift가 선언돼 있지 않습니다",
              "config에 drift: {gapLabel, pivotLabel} 를 넣으세요")
    if kind == "count":
        # 임의의 숫자를 손으로 적게 두지 않는다 — 조건을 선언하면 엔진이 데이터에서 센다.
        match = metric.get("match")
        if not isinstance(match, dict) or not match:
            v.err(f"{path}.match", "kind가 'count'인 카드에는 match가 최소 1개 필요합니다",
                  "셀 수 있는 항목: " + ", ".join(COUNT_FIELDS))
        else:
            for f, val in match.items():
                if f not in COUNT_FIELDS:
                    v.err(f"{path}.match.{f}", f"작업(work)에 없는 항목으로는 셀 수 없습니다: {f}",
                          "셀 수 있는 항목: " + ", ".join(COUNT_FIELDS))
                elif not isinstance(val, (str, bool)):
                    v.err(f"{path}.match.{f}", "셀 조건의 값은 문자열이나 true/false여야 합니다")
        for f in ("warnAt", "riskAt"):
            if f in metric:
                v.whole(f"{path}.{f}", metric[f], lo=0)
    return True


WORK_REQUIRED = ("id", "title", "area", "team", "status")
WORK_OPTIONAL = ("block", "touches", "fixes", "planStart", "planEnd", "progress", "eta",
                 "completedAt", "deps", "stalledDays", "drift")


def validate_data(v, data, C):
    """data를 검증한다. config(C)에 선언되지 않은 것을 참조하면 오류."""
    if not v.obj("data", data, required=("today", "works"),
                 optional=("updated", "target", "violations", "checkRuns", "drift")):
        return None

    if "today" in data:
        v.day("data.today", data["today"])
    if "updated" in data:
        v.stamp("data.updated", data["updated"])

    target = data.get("target")
    if target is not None and v.obj("data.target", target, required=("date",)):
        v.day("data.target.date", target["date"])

    works = {}
    if v.lst("data.works", data.get("works"), 1):
        works = _keyed(v, "data.works", data["works"], WORK_REQUIRED, WORK_OPTIONAL,
                       keyfield="id", strings=("title", "team"))
        for wid, w in works.items():
            p = f"data.works[{wid}]"
            if isinstance(w.get("team"), str) and C["teams"] is not None \
                    and w["team"] not in C["teams"]:
                v.err(f"{p}.team", f"config.teams 명부에 없는 팀입니다: {w['team']!r}",
                      "명부를 선언했으면 모든 작업의 팀이 그 안에 있어야 "
                      "「n팀 중 m팀」 표시가 정직해집니다")
            v.ref(f"{p}.area", w.get("area"), set(C["areas"]), "제품 영역")
            v.ref(f"{p}.status", w.get("status"), set(C["statuses"]), "상태")
            if "block" in w:
                v.enum(f"{p}.block", w["block"], BLOCK_VALUES, "막힘 사유(block)")
            if "touches" in w:
                # 문자열 하나도 받고 배열도 받는다 — 한 작업이 여러 자산을 건드릴 수 있다.
                got = v.strlist(f"{p}.touches", w["touches"])
                for i, t in enumerate(got or []):
                    suffix = "" if isinstance(w["touches"], str) else f"[{i}]"
                    v.ref(f"{p}.touches{suffix}", t, set(C["assets"]), "공용 자산")
            if "fixes" in w:
                # 이 작업이 어느 검사의 위반을 해소하는 일인지. touches와 같은 규칙으로 받는다.
                # touches가 "무엇을 건드리나"라면 fixes는 "무엇을 고치나"다 — 방향이 반대다.
                got = v.strlist(f"{p}.fixes", w["fixes"])
                for i, t in enumerate(got or []):
                    suffix = "" if isinstance(w["fixes"], str) else f"[{i}]"
                    v.ref(f"{p}.fixes{suffix}", t, set(C["checks"]), "자동 검사")
            if "progress" in w:
                v.whole(f"{p}.progress", w["progress"], 0, 100)
            if "stalledDays" in w:
                v.whole(f"{p}.stalledDays", w["stalledDays"], 0, 3650)
            if "drift" in w:
                v.flag(f"{p}.drift", w["drift"])
            if "deps" in w:
                v.strlist(f"{p}.deps", w["deps"])

            has_start, has_end = "planStart" in w, "planEnd" in w
            if has_start != has_end:
                v.err(p, "planStart와 planEnd는 함께 있거나 함께 없어야 합니다",
                      "한쪽만 있으면 시간 축에 그릴 수 없습니다")
            for k in ("planStart", "planEnd", "eta", "completedAt"):
                if k in w:
                    v.day(f"{p}.{k}", w[k])
            # 날짜 비교는 두 값이 모두 '달력에 실제로 있는 날'일 때만 한다.
            # 형식만 맞는 값(2026-02-31)으로 비교하면 여기서 예외가 터져,
            # 이미 잡아 둔 다른 오류들까지 함께 보여 주지 못한다.
            if has_start and has_end and _is_day(w["planStart"]) and _is_day(w["planEnd"]):
                if date.fromisoformat(w["planEnd"]) < date.fromisoformat(w["planStart"]):
                    v.err(f"{p}.planEnd", "계획 종료일이 시작일보다 빠릅니다")
            if "eta" in w and not has_end:
                v.err(f"{p}.eta", "eta(예상 종료)는 planEnd가 있는 작업에만 쓸 수 있습니다")

        for wid, w in works.items():
            deps = w.get("deps")
            deps = [deps] if isinstance(deps, str) else (deps if isinstance(deps, list) else [])
            for i, dep in enumerate(deps):
                path = f"data.works[{wid}].deps[{i}]"
                if v.ref(path, dep, set(works), "작업") and dep == wid:
                    v.err(path, "자기 자신을 선행 작업으로 둘 수 없습니다")

    violations = []
    if "violations" in data and v.lst("data.violations", data["violations"]):
        for i, vio in enumerate(data["violations"]):
            p = f"data.violations[{i}]"
            # ref는 선택이다 — 야간 보안 검사·의존성 취약점처럼 특정 작업이 아니라
            # 저장소·의존성 단위로 걸리는 위반이 실제로는 더 흔하다.
            if not v.obj(p, vio, required=("check", "severity"), optional=("ref",)):
                continue
            v.ref(f"{p}.check", vio["check"], set(C["checks"]), "자동 검사")
            if "ref" in vio:
                v.ref(f"{p}.ref", vio["ref"], set(works), "작업")
            v.enum(f"{p}.severity", vio["severity"], VIOLATION_SEVERITIES, "위반 무게(severity)")
            violations.append(vio)

    # 검사 실행 결과 — 보고된 것만 통과가 된다.
    runs = data.get("checkRuns")
    if runs is not None:
        if v.obj("data.checkRuns", runs, optional=tuple(C["checks"])):
            for k, val in runs.items():
                if k not in C["checks"]:
                    continue
                if v.enum(f"data.checkRuns.{k}", val, RUN_RESULTS, "실행 결과") and val == "pass":
                    n = sum(1 for x in violations if x.get("check") == k)
                    if n:
                        v.err(f"data.checkRuns.{k}",
                              f"실행 결과를 'pass'로 보고했는데 이 검사의 위반이 {n}건 있습니다",
                              "통과인지 실패인지 하나로 맞추세요")

    drift = data.get("drift")
    if drift is not None:
        if v.obj("data.drift", drift, required=("gaps", "pivots")):
            v.whole("data.drift.gaps", drift["gaps"], 0)
            v.whole("data.drift.pivots", drift["pivots"], 0)
        if C["drift"] is None:
            v.err("data.drift", "data에 drift가 있는데 config.drift가 선언돼 있지 않습니다",
                  "표시할 이름(gapLabel·pivotLabel)이 없어 그릴 수 없습니다")

    for key, card in C["cards"].items():
        metric = card.get("metric") or {}
        if isinstance(metric, dict) and metric.get("kind") == "drift" and drift is None:
            v.err(f"config.cards[{key}].metric",
                  "kind가 'drift'인 카드가 있는데 data.drift가 없습니다",
                  "data에 drift: {gaps, pivots} 를 넣으세요")

    return {"today": data.get("today"), "updated": data.get("updated"),
            "target": target, "works": works, "violations": violations,
            "checkRuns": runs, "drift": drift,
            "workOrder": _order(data.get("works"), "id", works)}


# ══════════════════════════════════════════════════════════════════════════
# 3. 도출 — 화면에 나오는 숫자와 판정은 전부 여기서 계산한다
# ══════════════════════════════════════════════════════════════════════════

def _josa(word, with_batchim, without):
    """받침에 따라 조사를 고른다(예: '검토대기'+가, '진행 중'+이)."""
    if not word:
        return without
    ch = word[-1]
    if "가" <= ch <= "힣":
        return with_batchim if (ord(ch) - 0xAC00) % 28 else without
    return without


def _sub(template, vals):
    """{이름} 자리만 골라 바꾼다. str.format을 쓰지 않는 이유는, 문구 안에 사용자에게
    보여 줄 JSON 조각({gapLabel, pivotLabel} 같은 것)이 그대로 들어 있기 때문이다."""
    return PLACEHOLDER_RE.sub(lambda m: str(vals.get(m.group(1), m.group(0))), template)


def _seg(t, em=None):
    return {"t": t, "em": em} if em is not None else {"t": t}


def _read(key, em, **vals):
    """엔진이 고정한 도출 문장 하나를 읽는 문장 조각들로 바꾼다.

    `[[ ]]`로 감싼 구간이 강조(색 굵은 글씨)가 된다. 강조 색(em)은 심각도에서 나온다.
    """
    out = []
    for i, part in enumerate(EMPH_RE.split(READ[key])):
        if not part:
            continue
        out.append(_seg(_sub(part, vals), em) if i % 2 else _seg(_sub(part, vals)))
    return out


def _touch_list(w):
    """touches를 언제나 목록으로 본다 — 문자열 하나도 배열도 같은 모양으로."""
    t = w.get("touches")
    if t is None:
        return []
    return [t] if isinstance(t, str) else list(t)


def _fix_list(w):
    """fixes를 언제나 목록으로 본다 — touches와 같은 규칙(문자열 하나도 배열도 같은 모양)."""
    f = w.get("fixes")
    if f is None:
        return []
    return [f] if isinstance(f, str) else list(f)


def _grid_cols(n, maxcols):
    """격자 열 수 — 선언한 개수만큼 놓되, 너무 좁아지면 줄을 고르게 나눈다.

    열 수를 화면에 박아 두면(예: 항상 6열) 7개를 넣었을 때 6+1이 되어 하나가 외따로 남는다.
    그렇다고 무한정 한 줄에 밀어 넣으면 열이 읽을 수 없이 좁아진다. 그래서 최대 열 수를
    넘으면 필요한 줄 수를 먼저 구하고, 그 줄 수로 고르게 나눈다(8개·최대 7 → 4+4).
    """
    if n <= 0:
        return 1
    if n <= maxcols:
        return n
    rows = -(-n // maxcols)          # 올림 나눗셈
    return -(-n // rows)


def _tick_label(axis0, d, grid):
    """눈금 글자. 첫 눈금과 연도가 바뀌는 눈금에는 연도를 붙인다 —
    3년짜리 축에서 '01-04'가 세 번 나오면 어느 해인지 알 수 없다."""
    day = axis0 + timedelta(days=d)
    prev = axis0 + timedelta(days=grid[grid.index(d) - 1]) if d != grid[0] else None
    if prev is None or prev.year != day.year:
        return day.strftime("%Y-%m-%d")
    return day.strftime("%m-%d")


def compute(C, D):
    """검증을 통과한 config·data에서 화면 모델을 만든다."""
    T = C["text"]
    TH = C["thresholds"]
    today = date.fromisoformat(D["today"])
    axis0 = today - timedelta(days=C["axis"]["leadDays"])
    span = C["axis"]["days"]

    def di(iso):
        return (date.fromisoformat(iso) - axis0).days

    order = D["workOrder"]
    works = D["works"]
    block_refs = {v["ref"] for v in D["violations"]
                  if v["severity"] == "block" and "ref" in v}

    # ── 작업 뷰 모델 ──────────────────────────────────────────────────────
    wm = {}
    for wid in order:
        w = works[wid]
        dated = "planStart" in w and "planEnd" in w
        wm[wid] = {
            "id": wid, "title": w["title"], "team": w["team"], "status": w["status"],
            "area": w["area"], "areaLabel": C["areas"][w["area"]]["label"],
            "block": w.get("block", ""), "touches": _touch_list(w), "fixes": _fix_list(w),
            "progress": w.get("progress", 0),
            "s": di(w["planStart"]) if dated else None,
            "e": di(w["planEnd"]) if dated else None,
            "et": di(w["eta"]) if "eta" in w else None,
            "risk": wid in block_refs,
        }

    # ── 칸반 열 ──────────────────────────────────────────────────────────
    columns = []
    for key, st in C["statuses"].items():
        columns.append({
            "key": key, "label": st["label"], "tone": TONES[st["tone"]],
            "mean": st.get("mean", ""),
            "works": [wid for wid in order if wm[wid]["status"] == key],
        })

    # ── 제품 영역 롤업 ────────────────────────────────────────────────────
    # 계획 구간은 자식의 min/max, 진척은 기간 가중 평균, 예상 종료는 자식의 max.
    # 손으로 넣는 숫자는 하나도 없다.
    rollup = []
    for key, area in C["areas"].items():
        ids = [wid for wid in order if wm[wid]["area"] == key]
        dated = [wid for wid in ids if wm[wid]["s"] is not None]
        conflicts = sum(1 for wid in ids if wm[wid]["block"] == "conflict")
        waits = sum(1 for wid in ids if wm[wid]["block"] == "waiting")
        badges = []
        if conflicts:
            badges.append({"label": _sub(T["areaBadgeConflict"], {"n": conflicts}), "tone": "r"})
        if waits:
            badges.append({"label": _sub(T["areaBadgeWaiting"], {"n": waits}), "tone": "a"})
        undated_n = len(ids) - len(dated)
        cnt = _sub(T["areaCountUndated"], {"n": len(dated), "u": undated_n}) if undated_n \
            else _sub(T["areaCount"], {"n": len(dated)})
        row = {"key": key, "label": area["label"], "works": ids, "dated": dated,
               "badges": badges, "cntText": cnt, "empty": not dated}
        if dated:
            total = sum(wm[i]["e"] - wm[i]["s"] + 1 for i in dated)
            row.update({
                "s": min(wm[i]["s"] for i in dated),
                "e": max(wm[i]["e"] for i in dated),
                "et": max((wm[i]["et"] if wm[i]["et"] is not None else wm[i]["e"]) for i in dated),
                "progress": round(sum(wm[i]["progress"] * (wm[i]["e"] - wm[i]["s"] + 1)
                                      for i in dated) / total),
            })
        rollup.append(row)

    undated_ids = [wid for wid in order if wm[wid]["s"] is None]

    # ── 공용 자산 집계 ────────────────────────────────────────────────────
    # 분모(all)의 뜻은 두 가지다. config.teams 명부를 선언했으면 '전체 팀 수',
    # 선언하지 않았으면 '그 자산을 건드린 팀 수'. 어느 쪽이든 이 자산과 무관한
    # 작업이 늘었다고 숫자가 흔들리지 않는다.
    roster = C["teams"]
    assets = []
    for key, a in C["assets"].items():
        linked = [wid for wid in order if key in wm[wid]["touches"]]
        teams = len({wm[wid]["team"] for wid in linked})
        c_teams = len({wm[wid]["team"] for wid in linked if wm[wid]["block"] == "conflict"})
        w_teams = len({wm[wid]["team"] for wid in linked if wm[wid]["block"] == "waiting"})
        denom = len(roster) if roster is not None else teams
        scope = T["assetScopeAll"] if roster is not None else T["assetScopeTouched"]
        denom_label = f"{scope} {denom}"
        if c_teams:
            sev = "risk"
            badge = _sub(T["assetBadgeConflict"], {"all": denom_label, "n": c_teams})
        elif w_teams:
            sev = "warn"
            badge = _sub(T["assetBadgeWaiting"], {"all": denom_label, "n": w_teams})
        else:
            sev, badge = "ok", T["assetBadgeOk"]
        assets.append({"key": key, "label": a["label"], "plain": a["plain"],
                       "why": a.get("why", ""), "sev": sev, "badge": badge,
                       "links": linked, "teams": teams, "denom": denom, "denomLabel": denom_label,
                       "conflictTeams": c_teams, "waitTeams": w_teams})
    assets_by_key = {a["key"]: a for a in assets}

    # ── 자동 검사 집계 ────────────────────────────────────────────────────
    # 핵심 규칙: **보고되지 않은 검사는 통과가 아니다.** data.checkRuns가 이번 회차에
    # 무엇이 실제로 돌았는지 말해 주고, 말해 주지 않은 검사는 회색 '정보 없음'으로 남는다.
    sev_label = {"block": T["vioBlock"], "warn": T["vioWarn"]}
    status_label = {"pass": T["checkPass"], "warn": T["checkWarn"], "block": T["checkBlock"],
                    "skipped": T["checkSkipped"], "never": T["checkNever"],
                    "unknown": T["checkUnknown"]}
    # 해소 작업 — "이 위반을 고치는 일이 목록에 있나". 위반이 떠 있는데 그것을 고치는
    # 작업이 하나도 없다는 사실은 위반 자체보다 급하다(위반은 이미 알려진 것이고, 아무도
    # 손대지 않고 있다는 것이 지금 결정할 일이기 때문이다).
    # 그러나 '없다'와 '아무도 안 적었다'를 같은 말로 하면 이 보드의 원칙이 무너진다.
    # 그래서 데이터가 fixes를 한 번이라도 쓴 회차에서만 '없음'이라 말하고,
    # 한 번도 쓰지 않았으면 '미보고'로 남긴다.
    fixes_reported = any(wm[wid]["fixes"] for wid in order)
    runs = D["checkRuns"]
    checks = []
    for key, c in C["checks"].items():
        vios = [v for v in D["violations"] if v["check"] == key]
        worst = "block" if any(v["severity"] == "block" for v in vios) \
            else "warn" if vios else None
        if runs is not None and key in runs:
            result = runs[key]
            if result == "pass":
                status = "pass"
            elif result == "fail":
                # 실패했다고 보고했으면 실패다. 걸린 항목을 따로 적지 않았다는 이유로
                # 주의(노랑)로 낮추면, 심각한 것을 심각하지 않게 보여 주는 셈이 된다.
                status = worst or "block"
            else:
                # 건너뜀·미실행이라고 보고했는데 위반이 올라와 있으면 그 검사는 분명히 돌았다.
                # 올라온 사실이 보고보다 강하다 — 위반을 회색 뒤에 숨기지 않는다.
                status = worst or result
        elif worst:
            # 실행 결과 보고가 없어도 위반이 올라왔다면 그 검사는 분명히 돌았다.
            status = worst
        else:
            status = "unknown"
        # 해소 작업은 걸린 검사에만 묻는다. 통과한 검사에 "해소 작업 없음"을 띄우면
        # 고칠 것이 없는데 없다고 말하는 셈이라 신호가 죽는다.
        fix_work = [wid for wid in order if key in wm[wid]["fixes"]]
        if status in ("block", "warn"):
            fix_state = "some" if fix_work else ("none" if fixes_reported else "unknown")
        else:
            fix_state = ""
        fix_label = {"some": _sub(T["fixSome"], {"n": len(fix_work)}),
                     "none": T["fixNone"], "unknown": T["fixUnknown"]}.get(fix_state, "")
        checks.append({
            "key": key, "name": c["name"], "plain": c["plain"], "why": c.get("why", ""),
            "status": status, "statusLabel": status_label[status],
            "reported": status not in GREY_CHECK_STATES,
            # 이 위반을 해소하는 작업(data.works[].fixes가 이 검사를 가리킨 것).
            "fixWork": fix_work, "fixState": fix_state, "fixLabel": fix_label,
            # 위반 문구는 참조된 작업의 제목을 그대로 쓴다 — 매번 새로 쓰는 설명문을 두지 않는다.
            # 작업을 가리키지 않는 위반(저장소·의존성 단위)은 그 사실만 적는다.
            "violations": [{"ref": v.get("ref", ""),
                            "what": wm[v["ref"]]["title"] if "ref" in v else T["vioNoTarget"],
                            "sevLabel": sev_label[v["severity"]],
                            "sev": v["severity"]} for v in vios],
        })
    checks_by_key = {c["key"]: c for c in checks}
    pass_checks = sum(1 for c in checks if c["status"] == "pass")
    unknown_checks = sum(1 for c in checks if c["status"] in GREY_CHECK_STATES)

    # ── 밴드 1 카드 — 읽는 문장과 심각도를 지표 종류별로 계산 ─────────────
    cards = {}
    traces = {}
    ctx = {"assets": assets_by_key, "checks": checks_by_key, "nChecks": len(checks),
           "passChecks": pass_checks, "unknownChecks": unknown_checks,
           "undated": undated_ids, "today": today}
    for key in C["cardOrder"]:
        card = C["cards"][key]
        sev, reading, trace = _metric(card["metric"], C, D, TH, wm, order, ctx)
        for ck in (card.get("traceChecks") or []):
            trace.setdefault("checks", [])
            if ck not in trace["checks"]:
                trace["checks"].append(ck)
        cards[key] = {"id": key, "lane": card["lane"], "title": card["title"],
                      "plain": card["plain"], "why": card.get("why", ""),
                      "severity": sev, "reading": reading,
                      # '결정 필요'는 실제로 걸려 있을 때만 붙인다. 늘 붙어 있으면 신호가 죽는다.
                      "decide": bool(card.get("decide")) and sev in ("risk", "warn")}
        traces[key] = trace

    lanes = []
    for lkey in C["laneOrder"]:
        lane = C["lanes"][lkey]
        mine = [cards[k] for k in C["cardOrder"] if cards[k]["lane"] == lkey]
        mine.sort(key=lambda c: SEV_ORDER[c["severity"]])   # 안정 정렬 — 같은 급이면 선언 순서
        shown = min(len(mine), TH["laneMaxCards"])
        rest = len(mine) - shown
        # 접힌 카드도 payload에 담는다. 화면에서 셀 수 있는 것은 화면에서 열 수도 있어야 한다.
        lanes.append({"key": lkey, "label": lane["label"], "en": lane.get("en", ""),
                      "count": len(mine), "cards": mine, "shown": shown,
                      "moreText": _sub(T["laneMore"], {"n": rest}) if rest else "",
                      "lessText": T["laneLess"]})

    # ── 종합 판정 ────────────────────────────────────────────────────────
    has_risk = any(c["severity"] == "risk" for c in cards.values()) \
        or any(c["status"] == "block" for c in checks)
    has_warn = any(c["severity"] == "warn" for c in cards.values())
    vkey = "risk" if has_risk else "warn" if has_warn else "ok"
    verdict = dict(C["verdicts"][vkey])
    verdict["tone"] = TONES[DEFAULT_VERDICTS[vkey]["tone"]]

    # 판정 사유는 원인을 이름으로 댄다.
    #
    # 예전에는 위험이면 언제나 "공용 자산 충돌이 풀리지 않았다"라고 적었다. 원인이 멈춤이든
    # 지연이든 검사 위반이든 같은 문장이 나오므로, 화면에서 사람이 가장 먼저 읽는 줄이
    # 사실과 어긋날 수 있었다. 이제 판정을 몰고 간 카드를 찾아 그 카드의 제목과 읽는 문장으로
    # 사유를 짓는다. 원인이 여러 개면 가장 급한 하나를 대고 나머지는 건수로 덧붙인다
    # (카드는 이미 심각도 순으로 정렬돼 있으므로 앞의 것이 가장 급하다).
    #
    # 프로젝트가 config.verdicts.<key>.why를 직접 적었으면 그 문장을 그대로 둔다.
    if vkey in ("risk", "warn") and vkey not in C["whyFixed"]:
        drivers = [cards[k] for k in C["cardOrder"] if cards[k]["severity"] == vkey]
        if drivers:
            head = drivers[0]
            # reading은 {t, em} 조각의 배열이다. 사유는 강조 없는 한 줄이라 글자만 잇는다.
            reading = "".join(seg.get("t", "") for seg in head["reading"]).strip()
            rest = len(drivers) - 1
            verdict["why"] = _sub(T["verdictWhyRisk" if vkey == "risk" else "verdictWhyWarn"], {
                "title": head["title"],
                "more": _sub(T["verdictWhyMore"], {"rest": rest}) if rest else "",
                "reading": reading})
    # 초록 램프가 "다 확인했다"로 읽히지 않게, 모르는 만큼을 램프 옆에 같이 적는다.
    verdict["noData"] = _sub(T["verdictNoData"], {"unknown": unknown_checks}) \
        if unknown_checks else ""
    decides = sum(1 for c in cards.values() if c["decide"])

    # ── 의존선 ───────────────────────────────────────────────────────────
    links = []
    for wid in order:
        deps = works[wid].get("deps") or []
        deps = [deps] if isinstance(deps, str) else deps
        for dep in deps:
            if wm[dep]["e"] is not None and wm[wid]["s"] is not None:
                links.append([dep, wid])

    # ── 축 ───────────────────────────────────────────────────────────────
    # 축 밖으로 나간 계획은 조용히 사라지면 안 된다. 좌표는 잘라 그리되,
    # 축 양끝에 "축 이전/이후 n건"을 붙여 무엇이 안 보이는지 화면에서 말한다.
    tick = C["axis"]["tickDays"]
    grid = list(range(0, span + 1, tick))
    off_before = [wid for wid in order if wm[wid]["s"] is not None and wm[wid]["s"] < 0]
    off_after = [wid for wid in order if wm[wid]["e"] is not None
                 and max(wm[wid]["e"], wm[wid]["et"] or wm[wid]["e"]) > span]
    axis = {
        "days": span,
        "gridlines": grid,
        "ticks": [{"d": d, "label": _tick_label(axis0, d, grid)} for d in grid],
        "todayD": di(D["today"]),
        "targetD": di(D["target"]["date"]) if D["target"] else None,
        "offBeforeText": _sub(T["axisOffBefore"], {"n": len(off_before)}) if off_before else "",
        "offAfterText": _sub(T["axisOffAfter"], {"n": len(off_after)}) if off_after else "",
        "offTip": T["axisOffTip"],
    }

    # ── 기획 정합성 막대 ──────────────────────────────────────────────────
    drift_m = None
    if D["drift"]:
        drift_m = {"reading": _read("drift", "i",
                                    gapLabel=C["drift"]["gapLabel"],
                                    pivotLabel=C["drift"]["pivotLabel"],
                                    gaps=D["drift"]["gaps"], pivots=D["drift"]["pivots"])}

    return {
        "updated": D["updated"] or D["today"],
        "byId": wm, "columns": columns, "rollup": rollup, "undated": undated_ids,
        "undatedCntText": _sub(T["undatedCount"], {"n": len(undated_ids)}),
        "assets": assets, "checks": checks, "lanes": lanes, "traces": traces,
        "cardTitle": {k: c["title"] for k, c in cards.items()},
        "verdict": verdict, "decides": decides, "links": links, "drift": drift_m,
        "passChecks": pass_checks, "unknownChecks": unknown_checks,
        # 격자 열 수 — 선언한 개수를 따른다(4열·6열에 박아 두지 않는다).
        "cols": {"lanes": _grid_cols(len(lanes), MAX_COLS["lanes"]),
                 "kanban": _grid_cols(len(columns), MAX_COLS["kanban"]),
                 "checks": _grid_cols(len(checks), MAX_COLS["checks"]),
                 "assets": _grid_cols(len(assets), MAX_COLS["assets"])},
    }, axis


def _metric(metric, C, D, TH, wm, order, ctx):
    """지표 한 종류를 계산한다 → (심각도, 읽는 문장 조각들, 연결 대상).

    문장의 **문법은 엔진이 고정**(READ)하고, 그 안에 들어가는 **이름**(제품 영역·상태·
    공용 자산·기획 용어)만 config에서 온다. 숫자는 전부 여기서 데이터를 세어 만든다.
    그래서 매 실행 같은 문장이 나오고, 사람이 새 문장을 지어 넣을 자리가 없다.
    """
    kind = metric["kind"]

    if kind == "asset":
        a = ctx["assets"][metric["asset"]]
        if a["conflictTeams"]:
            reading = _read("assetConflict", "r", all=a["denomLabel"], n=a["conflictTeams"])
        elif a["waitTeams"]:
            reading = _read("assetWaiting", "a", all=a["denomLabel"], n=a["waitTeams"])
        else:
            reading = _read("assetOk", "g", all=a["denomLabel"], n=a["teams"])
        return a["sev"], reading, {"work": list(a["links"]), "assets": [a["key"]]}

    if kind == "check":
        c = ctx["checks"][metric["check"]]
        n = len(c["violations"])
        # 밴드 1의 심각도와 밴드 3의 상태는 같은 사실에서 나와야 한다.
        # 막힘 위반이면 위험, 주의 위반이면 주의, 통과면 정상, 보고가 없으면 참고(회색).
        sev = {"block": "risk", "warn": "warn", "pass": "ok"}.get(c["status"], "info")
        head_key = "checkHit" if n else ("checkClean" if c["reported"] else "checkNoRun")
        head = _read(head_key, SEV_EM[sev], n=n)
        tail_key = "checkTailUnknown" if ctx["unknownChecks"] else "checkTail"
        tail = _read(tail_key, "g", all=ctx["nChecks"],
                     **{"pass": ctx["passChecks"], "unknown": ctx["unknownChecks"]})
        # 해소 작업의 유무를 밴드 1 문장에 함께 싣는다. 위반 건수만 읽고 나면
        # "그래서 누가 고치나"가 남는데, 그 답이 없다는 것이 이 카드에서 가장 급한 사실이다.
        if c["fixState"] == "none":
            fix = _read("checkFixNone", "r")
        elif c["fixState"] == "some":
            fix = _read("checkFixSome", "g", n=len(c["fixWork"]))
        else:
            fix = []
        # 연결 대상은 두 방향을 합친다 — 위반을 일으킨 작업(ref)과 해소하는 작업(fixes).
        # 절대규칙 위반에는 일으킨 작업이 없으므로, 실제로 밴드 2로 이어지는 것은 대개 뒤쪽이다.
        work = [v["ref"] for v in c["violations"] if v["ref"]]
        for wid in c["fixWork"]:
            if wid not in work:
                work.append(wid)
        return sev, head + tail + fix, {"work": work, "checks": [c["key"]]}

    if kind == "area_stalled":
        ids = [w for w in order if wm[w]["area"] == metric["area"]]
        # "멈춘 작업이 없다"와 "멈춘 기간을 아무도 안 적었다"는 전혀 다른 말이다.
        # 뒤쪽을 초록으로 그리면 보드가 모르는 것을 안다고 말하게 되므로, 회색으로 남긴다.
        told = [w for w in ids if "stalledDays" in D["works"][w]]
        if not told:
            return "info", _read("stallNoData", "i"), {"work": ids}
        days, worst = max((D["works"][w]["stalledDays"], w) for w in told)
        if not days:
            return "ok", _read("stallNone", "g"), {"work": ids}
        sev = "risk" if days >= TH["stallRiskDays"] else "warn"
        label = C["statuses"][wm[worst]["status"]]["label"]
        return sev, _read("stall", SEV_EM[sev], label=label,
                          josa=_josa(label, "이", "가"), days=days), {"work": ids}

    if kind == "area_delay":
        ids = [w for w in order if wm[w]["area"] == metric["area"]]
        # 늦음은 계획 종료일과 예상 종료일을 견줘야 나온다. 둘 다 적힌 작업이 하나도
        # 없으면 "늦은 작업 없음"이 아니라 "견줄 근거가 없음"이므로 회색으로 남긴다.
        told = [w for w in ids if wm[w]["e"] is not None and wm[w]["et"] is not None]
        late = [(w, wm[w]["et"] - wm[w]["e"]) for w in told if wm[w]["et"] > wm[w]["e"]]
        if not told:
            return "info", _read("delayNoData", "i"), {"work": ids}
        if not late:
            return "ok", _read("delayNone", "g"), {"work": ids}
        worst = max(d for _, d in late)
        sev = "risk" if worst >= TH["delayRiskDays"] else "warn"
        return sev, _read("delay", SEV_EM[sev], n=len(late), days=worst), \
            {"work": [w for w, _ in late]}

    if kind == "undated":
        undated = ctx["undated"]
        if not undated:
            return "ok", _read("undatedNone", "g"), {"work": []}
        return "warn", _read("undated", "a", n=len(undated)), {"work": list(undated)}

    if kind == "status_recent":
        skey = metric["status"]
        label = C["statuses"][skey]["label"]
        window = TH["recentDays"]
        recent = []
        for w in order:
            if wm[w]["status"] != skey:
                continue
            # 실제 완료일이 있으면 그것을 쓰고, 없을 때만 계획 종료일로 물러난다.
            # 늦게 나간 것을 안 세거나, 계획 날짜가 없는 작업을 영원히 0으로 두지 않기 위해서다.
            when = D["works"][w].get("completedAt") or D["works"][w].get("planEnd")
            if when and 0 <= (ctx["today"] - date.fromisoformat(when)).days <= window:
                recent.append(w)
        return "info", _read("recent", "g", label=label, n=len(recent), days=window), \
            {"work": recent}

    if kind == "drift":
        d = D["drift"]
        return "info", _read("drift", "i", gapLabel=C["drift"]["gapLabel"],
                             pivotLabel=C["drift"]["pivotLabel"],
                             gaps=d["gaps"], pivots=d["pivots"]), \
            {"work": [w for w in order if D["works"][w].get("drift")]}

    if kind == "count":
        # 프로젝트가 직접 고른 조건으로 작업을 센다 — 지표 7종에 없는 걱정거리를 위한 탈출구.
        # 숫자를 손으로 적는 것이 아니라 조건을 선언하면 엔진이 데이터에서 센다.
        match = metric["match"]
        hits = []
        for w in order:
            src = D["works"][w]
            ok = True
            for f, val in match.items():
                if f == "touches":
                    ok = ok and (val in _touch_list(src))
                elif f == "drift":
                    ok = ok and (bool(src.get("drift")) == bool(val))
                else:
                    ok = ok and (src.get(f) == val)
            if ok:
                hits.append(w)
        n = len(hits)
        risk_at, warn_at = metric.get("riskAt"), metric.get("warnAt", 1)
        if risk_at is not None and n >= risk_at:
            sev = "risk"
        elif n and n >= warn_at:
            sev = "warn"
        else:
            sev = "ok" if n == 0 else "info"
        if not n:
            return sev, _read("countNone", SEV_EM[sev]), {"work": []}
        return sev, _read("count", SEV_EM[sev], n=n), {"work": hits}

    raise AssertionError(f"검증을 통과했는데 모르는 지표 종류: {kind}")   # 도달 불가


# ══════════════════════════════════════════════════════════════════════════
# 4. 렌더 — 고정 템플릿에 한 곳으로 주입한다
# ══════════════════════════════════════════════════════════════════════════

def render(C, D):
    m, axis = compute(C, D)
    payload = {
        "cfg": {
            "board": {
                "title": C["board"]["title"],
                "eyebrow": C["board"].get("eyebrow", ""),
                "subtitle": C["board"].get("subtitle", ""),
                "docTitle": C["board"].get("docTitle") or C["board"]["title"],
            },
            "bands": C["bands"],
            "drift": ({"note": C["drift"].get("note", ""),
                       "linkLabel": C["drift"].get("linkLabel", ""),
                       "href": C["drift"].get("href", "#")} if C["drift"] else None),
            "honesty": C["honesty"],
        },
        "text": C["text"],
        "axis": axis,
        "m": m,
    }
    with open(TEMPLATE_PATH, encoding="utf-8") as fh:
        template = fh.read()
    if template.count(MARKER) != 1:
        raise SystemExit(f"템플릿이 손상됐습니다 — 주입 지점 {MARKER!r}이 정확히 1개가 아닙니다: "
                         f"{TEMPLATE_PATH}")
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    # <script> 안에 </script>나 HTML 주석 시작이 그대로 들어가면 문서가 잘린다.
    blob = blob.replace("</", "<\\/").replace("<!--", "<\\!--")
    return template.replace(MARKER, blob), m


# ══════════════════════════════════════════════════════════════════════════
# 5. 진입점
# ══════════════════════════════════════════════════════════════════════════

def load_json(path, what):
    if not os.path.exists(path):
        raise SystemExit(f"{what} 파일을 찾을 수 없습니다: {path}")
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{what} 파일이 올바른 JSON이 아닙니다: {path}\n  {exc}")


def report_errors(errors):
    print(f"통합 현황판 엔진 — 검증 실패 {len(errors)}건. 아무 것도 렌더하지 않았습니다.",
          file=sys.stderr)
    for i, (path, message, hint) in enumerate(errors, 1):
        print(f"  {i}. {path}: {message}", file=sys.stderr)
        if hint:
            print(f"       └ {hint}", file=sys.stderr)
    print("\n스키마 정본: reference/board-schema.md", file=sys.stderr)


def starter_data(today=None):
    """--init이 깔아 주는 첫 회차 사실. 날짜는 실행일 기준으로 계산해,
    처음 띄운 보드가 '지금 살아 있는 판'처럼 보이게 한다."""
    t = today or date.today()

    def d(offset):
        return (t + timedelta(days=offset)).isoformat()

    return {
        "today": t.isoformat(),
        "works": [
            # 두 팀이 같은 공용 컴포넌트를 서로 다르게 고쳤다 → '조화' 카드가 켜진다
            {"id": "W-1", "title": "목록 화면 버튼 정리", "area": "screen", "team": "앱팀",
             "status": "review", "block": "conflict", "touches": ["component"],
             "planStart": d(-12), "planEnd": d(2), "progress": 80, "eta": d(9)},
            {"id": "W-2", "title": "설정 화면 버튼 정리", "area": "screen", "team": "서버팀",
             "status": "review", "block": "conflict", "touches": ["component"],
             "planStart": d(-9), "planEnd": d(5), "progress": 60},
            # 데이터 규격을 두 팀이 함께 건드리지만 아직 충돌은 아니다
            {"id": "W-3", "title": "주문 응답에 배송 상태 추가", "area": "server", "team": "서버팀",
             "status": "doing", "touches": ["spec"],
             "planStart": d(-4), "planEnd": d(12), "progress": 35},
            # 다른 팀 답을 기다리는 일 → 'count' 카드가 켜진다
            {"id": "W-4", "title": "주문 목록 화면 연결", "area": "screen", "team": "앱팀",
             "status": "done", "block": "waiting", "touches": ["spec"],
             "planStart": d(-6), "planEnd": d(8), "progress": 90},
            # 이미 나간 일
            {"id": "W-5", "title": "집계 배치 정리", "area": "data", "team": "서버팀",
             "status": "shipped",
             "planStart": d(-20), "planEnd": d(-3), "progress": 100, "completedAt": d(-3)},
            # 아직 일정이 안 잡힌 일 → '일정 미정' 그룹으로 간다
            {"id": "W-6", "title": "지표 화면 개편 사전 검토", "area": "data", "team": "앱팀",
             "status": "doing"},
            # 아래 보안 위반을 해소하는 일. fixes로 검사를 가리켰으므로 '기준' 카드를
            # 누르면 밴드 2에서 이 작업이 함께 밝아진다 — 위반과 그것을 고치는 일이 이어진다.
            {"id": "W-7", "title": "의존성 취약점 정리", "area": "server", "team": "서버팀",
             "status": "doing", "fixes": ["security"],
             "planStart": d(-2), "planEnd": d(10), "progress": 20},
        ],
        # 보안 점검에 걸린 것이 있다 → '기준' 카드가 켜진다.
        # 특정 작업이 아니라 저장소 전체에 걸린 위반이라 ref를 비웠다.
        "violations": [
            {"check": "security", "severity": "block"},
        ],
        # 이번 회차에 어떤 검사가 실제로 돌았는지. 적지 않으면 '정보 없음' 회색으로 남는다.
        "checkRuns": {"style": "pass", "test": "pass", "security": "fail"},
    }


def do_init(out_path):
    """값을 먼저 보여 주고, 그다음 고치게 한다.
    빈 칸을 스무 개 채우게 하는 대신 돌아가는 보드와 편집용 파일을 함께 깔아 준다."""
    target = os.path.join(os.getcwd(), ".integration")
    cfg_path = os.path.join(target, "board.config.json")
    data_path = os.path.join(target, "board.json")

    existing = [p for p in (cfg_path, data_path) if os.path.exists(p)]
    if existing:
        print("이미 설정 파일이 있어 덮지 않았습니다:")
        for p in existing:
            print(f"  {p}")
        print("\n지금 것으로 보드를 다시 그리려면:")
        rel = os.path.relpath(__file__)
        if rel.startswith(".."):
            rel = os.path.abspath(__file__)
        print(f"  python3 {rel} \\\n"
              f"    --config .integration/board.config.json \\\n"
              f"    --data   .integration/board.json \\\n"
              f"    --out    {out_path}")
        return 1

    os.makedirs(target, exist_ok=True)
    with open(os.path.join(HERE, "board.config.starter.json"), encoding="utf-8") as fh:
        starter_cfg = fh.read()
    with open(cfg_path, "w", encoding="utf-8") as fh:
        fh.write(starter_cfg)
    with open(data_path, "w", encoding="utf-8") as fh:
        json.dump(starter_data(), fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="config + data → 고정 통합 현황판 HTML (외부 의존 0)")
    ap.add_argument("--init", action="store_true",
                    help="첫 보드를 지금 띄운다. 돌아가는 설정 파일 두 개를 "
                         ".integration/ 에 깔고 그것으로 보드를 그린다")
    ap.add_argument("--config", default=os.path.join(HERE, "board.config.example.json"),
                    help="프로젝트 config (기본: 번들된 예시)")
    ap.add_argument("--data", default=os.path.join(HERE, "board.example.json"),
                    help="이번 회차 data (기본: 번들된 예시)")
    ap.add_argument("--out", default="integration-board.html", help="산출 HTML 경로")
    args = ap.parse_args(argv)

    if args.init:
        rc = do_init(args.out)
        if rc:
            return rc
        args.config = os.path.join(os.getcwd(), ".integration", "board.config.json")
        args.data = os.path.join(os.getcwd(), ".integration", "board.json")

    raw_cfg = load_json(args.config, "config")
    raw_data = load_json(args.data, "data")

    v = Validator()
    C = validate_config(v, raw_cfg)
    D = validate_data(v, raw_data, C) if C else None
    if v.errors or C is None or D is None:
        report_errors(v.errors or [("config", "읽을 수 없습니다", None)])
        return 2

    html, m = render(C, D)
    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(html)

    print(f"통합 현황판을 만들었습니다 → {out}")
    print(f"  판정 {m['verdict']['word']} · 결정 필요 {m['decides']}건 · "
          f"작업 {len(m['byId'])}건(일정 미정 {len(m['undated'])}) · "
          f"공용 자산 {len(m['assets'])} · "
          f"자동 검사 {len(m['checks'])}(통과 {m['passChecks']} · 미보고 {m['unknownChecks']}) · "
          f"{len(html.encode('utf-8')):,} bytes")

    if args.init:
        # 프로젝트 안에 있으면 짧은 상대경로가 읽기 좋고, 밖에 있으면 절대경로여야 실제로 돈다.
        rel = os.path.relpath(__file__)
        if rel.startswith(".."):
            rel = os.path.abspath(__file__)
        print("\n"
              "── 이제 이렇게 하면 됩니다 ─────────────────────────────\n"
              "1. 만들어진 보드를 사람이 볼 수 있게 하세요.\n"
              f"     파일: {out}\n"
              "   클로드가 돌렸다면 이 파일을 아티팩트로 게시하고 링크를 주세요\n"
              "   (사람은 이 작업 공간의 파일 경로에 닿을 수 없습니다).\n"
              "   터미널에서 직접 돌렸다면 브라우저로 여세요.\n"
              "   지금 담긴 것은 예시 내용입니다. 우리 것이 아닙니다.\n"
              "\n"
              "2. 그 화면을 놓고 네 가지만 정하면 우리 것이 됩니다.\n"
              "   사람에게 JSON이나 스키마 용어를 묻지 마세요. 답만 받아 설정은 대신 씁니다.\n"
              "     팀 이름 / 제품 구획 / 여러 팀이 함께 쓰는 것 / 지금 무엇이 걱정인가\n"
              "   고칠 파일 두 개:\n"
              "     .integration/board.config.json   우리가 무엇을 지켜볼지 (한 번 정하면 오래 갑니다)\n"
              "     .integration/board.json          이번 회차의 사실   (돌릴 때마다 갱신합니다)\n"
              "\n"
              "3. 고쳤으면 이 줄을 그대로 다시 돌리세요:\n"
              f"     python3 {rel} \\\n"
              "       --config .integration/board.config.json \\\n"
              "       --data   .integration/board.json \\\n"
              f"       --out    {os.path.basename(out)}\n"
              "\n"
              "   틀린 값을 적으면 아무 것도 만들지 않고, 어디가 틀렸는지와\n"
              "   대신 쓸 수 있는 값을 알려 줍니다. 외울 것이 없습니다.\n"
              "\n"
              "4. 끝나면 .integration/ 두 파일을 저장소에 커밋하세요. 빠뜨리기 쉽습니다.\n"
              "   작업 공간은 세션이 끝나면 회수됩니다. 커밋해 두지 않으면 다음 회차에\n"
              "   설정이 없어 판이 달라지고, '매 실행 같은 판'이라는 약속이 깨집니다.\n"
              "───────────────────────────────────────────────────────")
    return 0


if __name__ == "__main__":
    sys.exit(main())
