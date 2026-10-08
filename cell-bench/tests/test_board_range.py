import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from serve_board import bucket_rows


def _row(t, w, a, mn, mx, wh, cyc=1, phase="CHARGE", plug="on"):
    return {"time": t, "phase": phase, "plug": plug, "watts": str(w), "wh": str(wh), "cells": "24",
            "batt_min": str(mn), "batt_avg": str(a), "batt_max": str(mx), "hr_cells": "0", "cycle": cyc}


def test_bucket_rows_groups_by_window_and_aggregates():
    rows = [_row("2026-10-08 20:00:10", 60, 90.0, 88, 92, 1.0),
            _row("2026-10-08 20:05:10", 70, 92.0, 89, 94, 1.5),
            _row("2026-10-08 20:11:10", 30, 95.0, 93, 97, 2.0, cyc=2, phase="DISCHARGE", plug="off")]
    out = bucket_rows(rows, 600)
    assert [r["time"] for r in out] == ["2026-10-08 20:00:00", "2026-10-08 20:10:00"]
    first, second = out
    assert first["watts"] == "65.00" and first["batt_avg"] == "91.0" and first["batt_min"] == 88 and first["batt_max"] == 94
    assert first["wh"] == "1.500" and first["n"] == 2 and first["plug"] == "on" and first["cycle"] == 1
    assert second["cycle"] == 2 and second["phase"] == "DISCHARGE" and second["plug"] == "off"


def test_bucket_rows_skips_bad_time_and_blank_values():
    rows = [_row("bad", 1, 1, 1, 1, 1), {"time": "2026-10-08 20:00:00", "phase": "X", "plug": "", "watts": "", "wh": "", "cells": "", "batt_min": "", "batt_avg": "", "batt_max": ""}]
    out = bucket_rows(rows, 3600)
    assert len(out) == 1 and out[0]["watts"] == "" and out[0]["batt_min"] == "" and out[0]["wh"] == "0.000"
