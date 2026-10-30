"""레일 위치 ↔ 지구 좌표, NMEA 문장 — 시뮬레이터 동기(프로토콜 v4 파트 B)와 장면 파일 생성의 공용 순수 함수.

원점(origin)은 레일 0 mm 의 위도 · 경도 · 타원체고이고, 방위(bearing_deg)는 레일 +pos 방향을 북에서 시계 방향으로 잰 각이다.
위경도 → mm 변환은 짧은 거리(수 m)에서만 쓰는 평면 근사다(1° 위도 ≈ 110,574 m, 1° 경도 ≈ 111,320 m × cos(위도)). 레일 2 m 에서 오차는 µm 아래다.
NMEA 는 GGA(위치 · 고정 · 위성 수)와 RMC(위치 · 속도 · 방향 · 날짜)만 만든다 — LabSat SatGen Real-Time+ 가 받는 두 문장(제조사 페이지).
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

M_PER_DEG_LAT = 110_574.0


def m_per_deg_lon(lat_deg: float) -> float:
    return 111_320.0 * math.cos(math.radians(lat_deg))


def rail_to_geo(pos_mm: float, origin: dict, bearing_deg: float) -> tuple[float, float, float]:
    """레일 위치(mm, 원점 기준) → (위도, 경도, 높이 m). origin = {"lat", "lon", "h"}."""
    b = math.radians(bearing_deg)
    east, north = pos_mm / 1000 * math.sin(b), pos_mm / 1000 * math.cos(b)
    lat = origin["lat"] + north / M_PER_DEG_LAT
    lon = origin["lon"] + east / m_per_deg_lon(origin["lat"])
    return lat, lon, origin["h"]


def geo_to_rail(lat: float, lon: float, origin: dict, bearing_deg: float) -> float:
    """(위도, 경도) → 레일 방향 성분 mm (rail_to_geo 의 역, 레일 직각 성분은 버린다)."""
    b = math.radians(bearing_deg)
    north = (lat - origin["lat"]) * M_PER_DEG_LAT
    east = (lon - origin["lon"]) * m_per_deg_lon(origin["lat"])
    return (east * math.sin(b) + north * math.cos(b)) * 1000


def _dm(deg: float, width: int) -> str:
    """도 → NMEA 의 ddmm.mmmmm (위도 width 2, 경도 3)."""
    d = int(abs(deg))
    m = (abs(deg) - d) * 60
    return f"{d:0{width}d}{m:08.5f}"


def checksum(body: str) -> str:
    c = 0
    for ch in body:
        c ^= ord(ch)
    return f"{c:02X}"


def sentence(body: str) -> str:
    return f"${body}*{checksum(body)}\r\n"


def gga(t: datetime, lat: float, lon: float, h: float, sats: int = 12, hdop: float = 0.8, fix: int = 1) -> str:
    hhmmss = t.strftime("%H%M%S") + f".{t.microsecond // 10000:02d}"
    body = (f"GPGGA,{hhmmss},{_dm(lat, 2)},{'N' if lat >= 0 else 'S'},{_dm(lon, 3)},{'E' if lon >= 0 else 'W'},"
            f"{fix},{sats:02d},{hdop:.1f},{h:.1f},M,0.0,M,,")
    return sentence(body)


def rmc(t: datetime, lat: float, lon: float, v_mm_s: float, course_deg: float) -> str:
    hhmmss = t.strftime("%H%M%S") + f".{t.microsecond // 10000:02d}"
    knots = abs(v_mm_s) / 1000 * 1.943844
    body = (f"GPRMC,{hhmmss},A,{_dm(lat, 2)},{'N' if lat >= 0 else 'S'},{_dm(lon, 3)},{'E' if lon >= 0 else 'W'},"
            f"{knots:.3f},{course_deg:.1f},{t.strftime('%d%m%y')},,,A")
    return sentence(body)


def nmea_pair(t: datetime, pos_mm: float, v_mm_s: float, origin: dict, bearing_deg: float) -> str:
    """레일 위치 · 속도 한 표본 → GGA + RMC. 속도가 음수(−pos 방향)면 방향을 180° 돌린다."""
    lat, lon, h = rail_to_geo(pos_mm, origin, bearing_deg)
    course = bearing_deg % 360 if v_mm_s >= 0 else (bearing_deg + 180) % 360
    return gga(t, lat, lon, h) + rmc(t, lat, lon, v_mm_s, course)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
