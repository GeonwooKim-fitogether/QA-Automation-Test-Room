"""축 정의 판정 — 방향 · Home 쪽의 정본(dynamic-bench/config/axis-motor1.json)으로 움직여도 되는지 본다. 순수 함수 · pysoem 없음.

왼쪽 · 오른쪽은 파일의 viewpoint 에서 본 것이다. home_side 는 사람이 현장에서 확인해 채우기 전까지 null 이고, 그동안 move · hold · slow 는
움직이지 않는다(2026-10-27 — 확인된 적 없는 "왼쪽 Home" · "+ 는 Home 에서 멀어진다"를 코드와 안내문이 가정하고 있었다). 이 파일은 읽기만 한다.
"""
from __future__ import annotations

from datetime import datetime

from motionbench import safety

OTHER = {"왼쪽": "오른쪽", "오른쪽": "왼쪽"}
SIDES = tuple(OTHER)      # 값 검사는 이 튜플로 — 손 편집으로 목록 · 객체가 와도 dict 처럼 해시하다 예외가 나지 않고 거부 이유가 나온다
ORIGIN_TOL = 2000         # 원점 신뢰 판정 2 mm — 전원을 넣은 자리에서 2 mm 넘게 떨어져 시작하면 원점을 믿지 않는다. 1 pulse = 1 µm


def load(path):
    """→ (축 정의 dict, None) 또는 (None, 이유) — safety.read_json 그대로(없거나 깨졌거나 객체가 아니면 이유)."""
    return safety.read_json(path)


def _side(doc: dict, first_move_dir: int):
    """첫 이동이 실제로 가는 쪽 — + 면 plus_direction, - 면 그 반대."""
    plus = doc.get("plus_direction")
    return plus if first_move_dir == 1 else OTHER[plus] if plus in SIDES else None


def problem(doc, first_move_dir: int) -> str | None:
    """움직이면 안 되는 이유, 없으면 None. first_move_dir 은 승인 파일 검사(safety.approval_problem)를 지난 1 또는 -1."""
    if not isinstance(doc, dict):
        return "JSON 객체가 아니다"
    v = doc.get("viewpoint")
    if not isinstance(v, str) or not v.strip():
        return "viewpoint 가 비었다 — 어느 쪽에서 본 왼쪽 · 오른쪽인지 모른다"
    if doc.get("plus_direction") not in SIDES:
        return f"plus_direction {doc.get('plus_direction')!r} — \"왼쪽\" 또는 \"오른쪽\" 이어야 한다"
    home = doc.get("home_side")
    if home is None:
        return ("Home 쪽이 확인되지 않았다 — 현장에서 확인해 axis-motor1.json 의 home_side · confirmed_by · confirmed_at 을 "
                "채운 뒤에만 움직인다")
    if home not in SIDES:
        return f"home_side {home!r} — \"왼쪽\" 또는 \"오른쪽\" 이어야 한다"
    by, at = doc.get("confirmed_by"), doc.get("confirmed_at")
    if not isinstance(by, str) or not by.strip():
        return "confirmed_by 가 비었다"
    try:
        if datetime.fromisoformat(at).utcoffset() is None:
            return "confirmed_at 에 시간대가 없다"
    except (TypeError, ValueError):
        return f"confirmed_at {at!r} 가 ISO8601 이 아니다"
    if _side(doc, first_move_dir) == home:
        return (f"첫 이동({'+' if first_move_dir == 1 else '-'})이 Home 쪽({home})으로 간다 — "
                "첫 이동은 Home 에서 멀어지는 쪽이어야 한다. "
                f"고치는 길은 first_move_dir 를 {-first_move_dir} 로 한 새 승인이다(dynamic-bench/dev/m2-approval.json, 사용자 승인 필요). "
                "검사를 통과시키려고 home_side 를 바꾸지 않는다")
    return None


def start_problem(p0: int, tol: int = ORIGIN_TOL) -> str | None:
    """시작 위치가 원점(전원을 넣은 자리)에서 tol pulse 밖이면 움직이지 않는 이유 — 원점 신뢰 판정.
    앞 실행이 남긴 자리에서 또 가면 같은 쪽으로 쌓인다(2026-10-27 13:47 slow 는 hold 가 남긴 p0 = 9,566 에서 시작했다).
    원점은 전원을 넣은 자리일 뿐이다 — Home 이 아닌 곳에서 전원을 넣었으면 이 검사는 그것을 모른다."""
    if abs(p0) <= tol:
        return None
    k = safety.PULSE_PER_MM
    return (f"시작 위치 p0 {p0} pulse 가 원점(전원을 넣은 자리)에서 {p0 / k:+.3f} mm — {tol / k:g} mm 밖이다. "
            "서보가 꺼진 채 슬라이더를 원점(Home)으로 되돌려 scan 으로 확인하거나, 확인된 Home 끝에서 전원을 다시 넣은 뒤 돈다")


def first_move_words(doc: dict, first_move_dir: int, distance_mm) -> str:
    """첫 이동을 화면에 찍을 말 — 예: "Home 에서 멀어지는 쪽(왼쪽)으로 10 mm (+)".
    "Home 에서 멀어지는" 은 problem() 이 통과할 때만 붙는다 — 아니면 "왼쪽으로 10 mm (+)" 처럼 쪽만 말한다."""
    side = _side(doc, first_move_dir)
    where = f"Home 에서 멀어지는 쪽({side})" if problem(doc, first_move_dir) is None else side
    return f"{where}으로 {distance_mm:g} mm ({'+' if first_move_dir == 1 else '-'})"
