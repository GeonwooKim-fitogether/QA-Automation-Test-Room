"""LabSat 3 장면 파일(.LS3)에 GPS L1 C/A 위성이 몇 개 들어 있는지 신호에서 직접 센다(포착 = 코드 · 도플러 2차원 상관).

  python cell-bench/tools/ls3_sats.py <장면>_0000.LS3 [--at 5] [--ms 10]

셀이 본 위성 수는 셀 · 칸 · 감쇠의 영향을 받는다. 이 도구는 장면 파일 자체에 실린 위성을 센다 — 장면을 SD 에 넣기 전에 위성 수를 확인하는 근거.
형식: 단일 위성계 · 1비트 · 64비트 낱말(바이트 순서 그대로)에 I,Q 쌍 32개, 16.368 MHz, 중심 1575.42 MHz.
2026-11-13 S04_RailB100 에서 확인: 낱말 그대로 · 앞 비트 I 가 가장 강하게 잡힘(PRN 10 · 15 · 18 · 20 · 23 · 24, 최고/평균 53 ~ 72, 나머지 4 이하).
"""
from __future__ import annotations

import argparse

import numpy as np

FS = 16.368e6
SPM = int(FS / 1000)  # 1 ms 샘플 수
G2 = {1: (2, 6), 2: (3, 7), 3: (4, 8), 4: (5, 9), 5: (1, 9), 6: (2, 10), 7: (1, 8), 8: (2, 9), 9: (3, 10), 10: (2, 3),
      11: (3, 4), 12: (5, 6), 13: (6, 7), 14: (7, 8), 15: (8, 9), 16: (9, 10), 17: (1, 4), 18: (2, 5), 19: (3, 6),
      20: (4, 7), 21: (5, 8), 22: (6, 9), 23: (1, 3), 24: (4, 6), 25: (5, 7), 26: (6, 8), 27: (7, 9), 28: (8, 10),
      29: (1, 6), 30: (2, 7), 31: (3, 8), 32: (4, 9)}
THRESH = 6.0  # 최고/평균 — 없는 위성은 4 안팎, 있는 위성은 50 이상


def ca_code(prn: int) -> np.ndarray:
    a, b = G2[prn]
    g1 = [1] * 10; g2 = [1] * 10; out = []
    for _ in range(1023):
        out.append(g1[9] ^ g2[a - 1] ^ g2[b - 1])
        f1 = g1[2] ^ g1[9]; f2 = g2[1] ^ g2[2] ^ g2[5] ^ g2[7] ^ g2[8] ^ g2[9]
        g1 = [f1] + g1[:9]; g2 = [f2] + g2[:9]
    return 1 - 2 * np.array(out)


def read_iq(path: str, at_s: float, n: int) -> np.ndarray:
    off = int(at_s * FS) // 32 * 8
    raw = np.fromfile(path, dtype=np.uint8, count=(n // 32 + 2) * 8, offset=off)
    bits = np.unpackbits(raw).astype(np.float32) * 2 - 1
    return (bits[0::2] + 1j * bits[1::2])[:n]


def acquire(iq: np.ndarray, n_ms: int, dopp=np.arange(-8000, 8001, 250)) -> dict[int, tuple[float, float]]:
    t = np.arange(SPM) / FS
    idx = (np.arange(SPM) * 1.023e6 / FS).astype(int) % 1023
    mix = np.exp(-2j * np.pi * np.outer(dopp, t))
    segs = [np.fft.fft(iq[k * SPM:(k + 1) * SPM] * mix, axis=1) for k in range(n_ms)]
    res = {}
    for prn in G2:
        C = np.conj(np.fft.fft(ca_code(prn)[idx]))
        acc = sum(np.abs(np.fft.ifft(S * C, axis=1)) ** 2 for S in segs)
        j, c = np.unravel_index(np.argmax(acc), acc.shape)
        row = acc[j].copy(); row[max(0, c - 20):c + 21] = 0
        res[prn] = (float(acc[j, c] / row.mean()), float(dopp[j]))
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("ls3")
    ap.add_argument("--at", type=float, default=5.0, help="볼 자리(장면 시작부터 초)")
    ap.add_argument("--ms", type=int, default=10, help="더할 1 ms 조각 수")
    a = ap.parse_args(argv)
    r = acquire(read_iq(a.ls3, a.at, SPM * a.ms), a.ms)
    found = sorted(p for p, (m, _) in r.items() if m > THRESH)
    print(f"{a.ls3} · {a.at:g}초 · GPS 위성 {len(found)}개: {found}")
    for p, (m, d) in sorted(r.items(), key=lambda kv: -kv[1][0])[:len(found) + 3]:
        print(f"  PRN {p:2d}  최고/평균 {m:6.1f}  도플러 {d:+6.0f} Hz{'' if m > THRESH else '  (없음)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
