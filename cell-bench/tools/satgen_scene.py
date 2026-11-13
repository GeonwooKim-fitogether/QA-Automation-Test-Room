"""SatGen 생성 엔진(Racelogic.Gnss.SatGen.BlackBox.exe)을 화면 없이 돌려 경로 파일(NMEA GGA)에서 LabSat 3 장면(.LS3)을 만든다.

  python cell-bench/tools/satgen_scene.py <장면 폴더>/<이름> --start 16:00:00 --mask 10
→ <장면 폴더>/<이름>/ls3/<이름>/<이름>_0000.LS3, 같은 폴더에 satgen_profile.txt(엔진에 넘긴 설정)와 satgen_log.txt(창 제목의 위성 수 기록)

SatGen 화면 프로그램(SatGenV3.exe)은 설정을 profile.txt 로 쓰고 이 엔진에 그 경로를 인자로 넘긴다. 이 도구는 그 단계를 직접 한다.
- 장면 시각 = 날짜(--date) + profile 의 GPS_Start_Time_in_sec(--start). 경로 첫 GGA 시각도 같게 둔다(scene_fw2.py --start).
- 동글이 없으면 엔진이 120초(119.9초)에서 자른다. 위성계는 GPS L1 C/A 하나.
- 엔진 창 제목이 "SatGen - N satellites - <시각>" 이라 위성 수를 실행 중에 읽어 남긴다.
2026-11-13 확인: 엔진이 혼자 끝나며 약 30초에 120초 장면을 만든다. 무거운 계산이라 레일 모터가 돌 때는 돌리지 않는다.
"""
from __future__ import annotations

import argparse
import ctypes
import subprocess
import time
from ctypes import wintypes
from pathlib import Path

ENGINE = Path(r"C:\Program Files\Racelogic\SatGen3\Racelogic.Gnss.SatGen.BlackBox.exe")
ALM = Path.home() / "Documents" / "SatGen" / "Almanacs"

PROFILE = """[Simulation Parameters]
Signal = GPS_L1CA
[Receiver Dynamics]
Static=0
Latitude_in_deg=0.0000
Longitude_in_deg=0.0000
Altitude_in_m=0
GPS_Week=0
GPS_Start_Time_in_sec={start_s}
Duration_in_sec = 0

Source_NMEA_file={nmea}
Simulation_Day={day}
Simulation_Mon={mon}
Simulation_Year={year}

[GPS_Sattelites Profile]
Almanac_File_GPS={alm}\\{gps}
Almanac_File_GLO={alm}\\{glo}
Almanac_File_BDS={alm}\\{bds}
Almanac_File_GAL={alm}\\{gal}
Almanac_File_NAV={alm}\\{nav}
Mask_in_deg={mask}

[Receiver Signal and Hardware Profile]
Attenuation = -3
IFFreq_in_Hz = 4092000
SampleRate_in_KHz = 12000
IQ_format=I
bitPerSample = 1
egm=none

[Simulation Output]
IF_IQ_File={out}
NMEA_File=nmea.txt
Approximation=false
"""


def window_titles(pid: int) -> list[str]:
    user32 = ctypes.windll.user32
    titles: list[str] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        p = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid:
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                titles.append(buf.value)
        return True

    user32.EnumWindows(cb, 0)
    return titles


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("scene", type=Path, help="장면 폴더(<이름>.nmea 가 있는 곳)")
    ap.add_argument("--date", default="2026-10-30", help="장면 날짜 YYYY-MM-DD (알마낙 날짜와 맞춘다)")
    ap.add_argument("--start", default="16:00:00", help="장면 시작 UTC HH:MM:SS")
    ap.add_argument("--mask", type=int, default=10, help="앙각 마스크 °")
    ap.add_argument("--gps-alm", default="2026-303.alm")
    ap.add_argument("--timeout", type=float, default=900.0)
    a = ap.parse_args(argv)

    scene = a.scene.resolve()
    name = scene.name
    nmea = scene / f"{name}.nmea"
    if not nmea.exists():
        raise SystemExit(f"경로 파일이 없다: {nmea}")
    first = next(l for l in nmea.read_text(encoding="ascii").splitlines() if l.startswith("$"))
    tod = first.split(",")[1]
    want = a.start.replace(":", "")
    if not tod.startswith(want):
        raise SystemExit(f"경로 첫 GGA 시각 {tod} 이 --start {a.start} 와 다르다 — scene_fw2.py --start 로 같게 만든다")
    y, m, d = (int(x) for x in a.date.split("-"))
    h, mi, s = (int(x) for x in a.start.split(":"))
    (scene / "ls3").mkdir(exist_ok=True)
    out = scene / "ls3" / f"{name}.ls3"
    prof = scene / "satgen_profile.txt"
    prof.write_text(PROFILE.format(start_s=h * 3600 + mi * 60 + s, nmea=nmea, day=d, mon=m, year=y, alm=ALM, gps=a.gps_alm,
                                   glo="MCCJ_261030.agl", bds="BDS_20261030.alm", gal="2026-10-30.xml", nav="NAV_2026-244.alm",
                                   mask=a.mask, out=out), encoding="mbcs")

    t0 = time.time()
    proc = subprocess.Popen([str(ENGINE), str(prof)], cwd=str(ENGINE.parent))
    seen: dict[str, None] = {}
    while proc.poll() is None:
        if time.time() - t0 > a.timeout:
            proc.kill()
            raise SystemExit(f"{a.timeout:.0f}초 안에 끝나지 않아 멈췄다")
        for t in window_titles(proc.pid):
            if "satellites" in t:
                seen[t] = None
        time.sleep(0.5)
    ls3 = scene / "ls3" / name / f"{name}_0000.LS3"
    log = [f"엔진 끝: 종료 코드 {proc.returncode}, {time.time() - t0:.0f}초",
           f"장면 파일: {ls3} ({ls3.stat().st_size if ls3.exists() else '없음'} 바이트)",
           "창 제목 기록:"] + [f"  {t}" for t in seen]
    (scene / "satgen_log.txt").write_text("\n".join(log) + "\n", encoding="utf-8")
    print("\n".join(log[:2] + [log[2]] + [f"  {t}" for t in list(seen)[:2]] + (["  …"] if len(seen) > 4 else []) + [f"  {t}" for t in list(seen)[-2:]]))
    return 0 if ls3.exists() else 1


if __name__ == "__main__":
    raise SystemExit(main())
