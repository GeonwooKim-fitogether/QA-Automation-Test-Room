"""셀 종류(X/Y) 세트 단위 인식 — 순수 함수.

X 세트 = X5/X6 (도크 DKPX-1C), Y 세트 = Y1/Y3B/Y4 (도크 DKPY-1C). 한 세트에는 한 종류만 실린다(사용자 확인) — 그래서 X 와 Y 가
함께 읽히면 오인식 · 배선 · 등록 오류로 보고 시작을 막고, 종류를 정하지 못한 셀이 하나라도 있어도 막는다(안전 쪽 실패).
신호는 둘이다. ① 셀이 스스로 보고한 모델 — 지금은 어느 패킷에도 없다, 생기면 model 로 넘긴다.
② 등록부 cell_types.json 의 시리얼 → 모델. 둘 다 있는데 다르면 그 셀은 '모순'이다. 시리얼로 가르는 실제 규칙은 [확인 필요].
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from cellbench.config import expand_serials

TABLE = Path(__file__).resolve().parents[1] / "cell_types.json"


def load(path=TABLE) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def classify(serial: int, model: str | None, table: dict) -> tuple[str | None, str | None, str]:
    """셀 하나 → (종류 "X"·"Y"·None, 모델, 근거 한 줄). model 이 "CLBY4B" 꼴이면 앞의 CLB 를 뗀다(durability-bench/README.md 1절 '셀 기종 표기' 의 보고 꼴)."""
    reg = next((m for spec, m in table["registry"].items() if serial in expand_serials(spec)), None)
    said = model.upper().removeprefix("CLB") if model else None
    if reg and said and reg != said:
        return None, None, f"모순 — 등록부 {reg} · 셀 보고 {said}"
    m = said or reg
    kind = table["models"].get(m, {}).get("type") if m else None
    if kind:
        return kind, m, f"{'셀 보고' if said else '등록부'} {m}"
    return None, m, f"모름 — {'모르는 모델 ' + m if m else '등록부에 없는 시리얼'}"


def judge_set(cells, table: dict | None = None) -> dict:
    """세트의 셀 [(시리얼, 보고한 모델 또는 None)] → {ok, kind, models, preset, reason, cells}.
    ok=False 면 시작하지 않는다 — reason 에 이유. ok 면 preset = types[kind] + 그 세트 모델들의 fw_expected · parser
    (모델끼리 다르거나 미확정이면 None — QC 는 기준 없음 · 회색)."""
    table = table or load()
    rows = [(s, *classify(s, m, table)) for s, m in cells]
    kinds = sorted({k for _, k, _, _ in rows if k})
    bad = [f"{s}({why})" for s, k, _, why in rows if k is None]
    reasons = (["셀 없음"] if not rows else []) \
        + (["세트에 X 와 Y 가 함께 보인다 — 세트는 섞이지 않으므로 도크 · 등록부 · 셀을 확인"] if len(kinds) > 1 else []) \
        + ([f"종류를 정하지 못한 셀 {len(bad)}대: {', '.join(bad)}"] if bad else [])
    models = Counter(m for _, k, m, _ in rows if k)
    out = {"ok": not reasons, "kind": None, "models": dict(models), "preset": None, "reason": " · ".join(reasons), "cells": rows}
    if out["ok"]:
        fw = {table["models"][m]["fw_expected"] for m in models}
        parser = {table["models"][m]["parser"] for m in models}
        out["kind"] = kinds[0]
        out["preset"] = {**table["types"][kinds[0]], "fw_expected": fw.pop() if len(fw) == 1 else None,
                         "parser": parser.pop() if len(parser) == 1 else None}
    return out
