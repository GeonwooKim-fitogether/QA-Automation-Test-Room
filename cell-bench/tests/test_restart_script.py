r"""코드 검토 3 — tools/restart_at_boundary.ps1 은 짝 검증 중이면 교체를 거부한다 · 새 엔진은 옛 엔진의 verifying · sets 플러그를 켠다.

교체 스크립트는 진짜 PowerShell 로 돌린다(Windows 에서만). 스크립트를 임시 폴더의 tools\ 로 복사하고 data\engine.json 을 꾸민 뒤,
명령줄에 run_cycle.py 가 들어간 가짜 엔진(잠만 자는 python)을 띄워 그 PID 를 준다. 가짜 엔진은 이 검사가 띄운 PID 만 끈다.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import run_cycle
import sim_world as sw
from cellbench import config as configmod

ROOT = Path(__file__).resolve().parent.parent
P2 = "AA:BB:CC:DD:EE:02"
PS = shutil.which("powershell") or shutil.which("pwsh")


def _run_script(tmp: Path, engine: dict, *extra: str, now: dict | None = None) -> tuple[int, str, bool]:
    (tmp / "tools").mkdir()
    (tmp / "data").mkdir()
    shutil.copy(ROOT / "tools" / "restart_at_boundary.ps1", tmp / "tools")
    fake = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "run_cycle.py"])
    try:
        (tmp / "data" / "engine.json").write_text(json.dumps({**engine, "pid": fake.pid}), encoding="utf-8")
        if now is not None:
            (tmp / "data" / "now.json").write_text(json.dumps(now), encoding="utf-8")
        p = subprocess.run([PS, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(tmp / "tools" / "restart_at_boundary.ps1"),
                            "-OldPid", str(fake.pid), "-TimeoutMin", "0", *extra],
                           capture_output=True, timeout=60, stdin=subprocess.DEVNULL)
        out = p.stdout.decode("utf-8", "replace") + p.stderr.decode("utf-8", "replace")
        return p.returncode, out, fake.poll() is None
    finally:
        fake.kill()
        fake.wait(10)


@pytest.mark.skipif(sys.platform != "win32" or not PS, reason="PowerShell 이 있는 Windows 에서만")
@pytest.mark.parametrize("extra", [(), ("-Now", "-Force")])
def test_restart_refuses_while_verifying(tmp_path, extra):
    code, out, alive = _run_script(tmp_path, {"exit": None, "verifying": [{"id": 2, "mac": P2, "ip": ""}]}, *extra)
    assert code == 4, out
    assert alive                                                   # 옛 엔진을 끝내지 않았다
    assert not (tmp_path / "data" / "supervisor_pause").exists() and not (tmp_path / "data" / "end_request").exists()


@pytest.mark.skipif(sys.platform != "win32" or not PS, reason="PowerShell 이 있는 Windows 에서만")
def test_restart_multi_without_verifying_still_places_end_request(tmp_path):
    code, _, alive = _run_script(tmp_path, {"exit": None, "mode": "multi", "sets": [{"id": 1}]})
    assert code == 1 and alive and (tmp_path / "data" / "end_request").exists()   # 0분 한도 — 표지만 놓고 기다림을 끝냈다


def test_inherited_plugs_skip_own_and_duplicates():
    cfg = configmod.Config(plug_mac=sw.SET1_MAC)
    prev = {"verifying": [{"id": 2, "mac": P2.lower(), "ip": "192.168.1.120"}],
            "sets": [{"id": 1, "mac": sw.SET1_MAC}, {"id": 2, "mac": P2}, {"id": 3, "mac": "AA:BB:CC:DD:EE:03", "ip": ""}]}
    got = run_cycle.inherited_plugs(cfg, prev)
    assert [(p["id"], p["mac"], p["why"]) for p in got] == [(2, P2, "verifying"), (3, "AA:BB:CC:DD:EE:03", "sets")]
    assert run_cycle.inherited_plugs(cfg, {}) == []


def test_new_engine_turns_on_previous_verifying_plug(tmp_path, monkeypatch):
    """옛 엔진이 세트 2 검증 도중 강제로 끝났다 — P2 가 꺼진 채다. 새 엔진은 덮어쓰기 전에 읽은 verifying 으로 P2 를 켠다(끊지 않고)."""
    w = sw.World([sw.Dock(sw.SET1_MAC, sw.SET1), sw.Dock(P2, list(range(11594, 11618)))], plugs={P2: False})
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "engine.json").write_text(json.dumps({"pid": 1, "exit": None,
                                                               "verifying": [{"id": 2, "mac": P2, "ip": ""}]}), encoding="utf-8")
    bench = {"sets": [dict(configmod.DEFAULT_SET), {"id": 2, "plug_mac": P2, "serials": "11594-11617", "registered_at": 1}]}
    code = sw.run_main(monkeypatch, tmp_path, w, ["--cycles", "1"], bench=bench)
    assert code == 0 and w.plugs[P2] is True
    assert w.plug_calls[P2][0] == "on"                             # 첫 호출이 켜기 — 끊었다 켜기(recharge)가 아니다
    assert "verifying 세트 2 플러그" in (tmp_path / "data" / "run.log").read_text(encoding="utf-8-sig")


@pytest.mark.skipif(sys.platform != "win32" or not PS, reason="PowerShell 이 있는 Windows 에서만")
@pytest.mark.parametrize("phase", ["DISCHARGE", "EXTRACT", "PRECHARGE", "PLUG_ON", "RECOVER", None])
def test_in_charge_replacement_refuses_every_phase_but_charge(tmp_path, phase):
    """-InCharge 는 단계가 CHARGE 일 때만 즉시 교체한다 — 그 밖(추출 · 예비 충전 · 플러그 켜기 · 방전 · 복구 대기 · 읽지 못함)은 거부하고 옛 엔진을 끝내지 않는다."""
    code, out, alive = _run_script(tmp_path, {"exit": None}, "-InCharge", now=None if phase is None else {"phase": phase})
    assert code == 2, out
    assert alive and not (tmp_path / "data" / "supervisor_pause").exists()


def test_in_charge_script_passes_precharge_and_keeps_other_switches():
    text = (ROOT / "tools" / "restart_at_boundary.ps1").read_text(encoding="utf-8-sig")
    assert "[switch]$InCharge" in text and "[switch]$Now, [switch]$Force" in text
    assert 'if ($InCharge) { $runArgs += "--precharge" }' in text and "restart_in_charge" in text
