"""1층(원값) 지표 — 판정 창(GNSS-1 정지 · FIRMWARE-1 레일)마다 셀 기록(FTG)을 직접 읽어 GNSS 원값 · IMU 원값 지표를 셀 × 창 한 줄씩 낸다.
기록 구간 고르기는 protocol_v4.ftg_pick 을 그대로 쓴다(라이브 위치가 같은 구간). 셀 파일을 하나씩 읽어 그 셀의 모든 창을 계산하고 놓는다.
판정 파일의 시험은 conditions.test 로 읽는다 — 2026-11-12 전 파일은 conditions.part 글자로(motionbench/qc_ids.from_record).
사용: python -X utf8 raw_layer.py <세트...>   (Y · X · X5E)"""
import sys, json, glob, os, csv, math
from pathlib import Path
from datetime import datetime
sys.stdout.reconfigure(encoding='utf-8')
ROOT = Path(__file__).resolve().parents[2]          # <작업 트리>/cell-bench — 제어 PC 에서는 C:\Dev\dynamic-bench\cell-bench
sys.path.insert(0, str(ROOT / 'tools')); sys.path.insert(0, str(ROOT))
sys.path.insert(0, r'C:\Dev\ohcoach-cell-tools\cores\fitogether_data_parser')
import numpy as np, pandas as pd
import protocol_v4 as P
from motionbench import align as A, qc_ids
from fitogether_data_parser.ftg.parser import FtgParser
from fitogether_data_parser.ftg.messages.gps_message import GPS_MESSAGE_DATA_TYPE_V2 as GT
from fitogether_data_parser.ftg.messages.imu_message import Imu0x0EMessage, Imu0x0FMessage
from fitogether_data_parser.ftg.utils.ftg_message_utils import decode_datetime_vectorized

DATA = ROOT / 'data'
HERE = Path(__file__).resolve().parent
SETS = {'Y': ['collect_20261102/round1', 'collect_20261102/round2'] + [f'collect_20261102/nightA{i}' for i in range(1, 5)],
        'X': [f'collect_20261105/r0{i}' for i in range(1, 4)], 'X5E': [f'collect_20261106/r0{i}' for i in range(1, 4)]}
G_LSB = 2048.0            # 가정: 가속도 ±16 g 범위 → 1 g = 2048 LSB (정지 |acc| 중앙 2015 로 확인)
DPS_LSB = 16.4            # 가정: 각속도 ±2000 dps 범위
MMS2 = 9806.65 / G_LSB    # LSB → mm/s²
PAD, IMU_DT, IMU_MAX_LAG = 1.0, 0.01, 3.0
LAT0, LON0, _ = P.SCENE_POS['S01-Static-GLE-8h']; KLON = 111320.0 * math.cos(math.radians(LAT0))


def deg(v):
    v = v.astype(np.float64); s = np.where(v > 0, 1.0, -1.0); a = np.abs(v)
    return s * (a // 1e10 + (a % 1e10) / 1e8 / 60)


def windows(folders):
    out = []
    for fo in folders:
        for f in sorted(glob.glob(str(DATA / fo / 'judged' / 'protocol_*.json'))):
            j = json.load(open(f, encoding='utf-8')); co = j['conditions']; src = j['source']
            test = qc_ids.from_record(co)
            if test not in ('GNSS-1', 'FIRMWARE-1') or src.get('clock_offset_s') is None:
                continue
            w = {'kind': test, 'folder': fo.split('/')[-1], 'stamp': j['stamp'], 'off': src['clock_offset_s'],
                 'ftg': [str(DATA / os.path.basename(x)) for x in src['ftg']], 'rig_ok': int(j['rig']['유효']),
                 'dock': {int(s): c['dock'] for s, c in j['cells'].items()}}
            if test == 'GNSS-1':
                w['t0'] = datetime.strptime(j['stamp'], '%Y%m%d-%H%M%S').timestamp(); w['t1'] = w['t0'] + co['still_s']
            else:
                t, p = A.load_motion(src['motion']); w['t0'], w['t1'] = t[0], t[-1]; w['tp'] = (t, p)
            out.append(w)
    return out


def read_file(f: Path):
    """파일 하나 → GPS 열들(파일 차례 idx 포함)과 IMU 두 흐름(0x0E 0.01초 · 0x0F 0.1초 평균: 셀 시각 epoch · idx · acc · gyro)."""
    data = f.read_bytes(); p = FtgParser('CLBY-4B-00000_0.99_0_1761724328_0.ftg', filecontents=data)
    g, gi, e, ei, o, oi = [], [], [], [], [], []
    for k, (ty, cd, pl, crc) in enumerate(p.raw_parser(memoryview(data))):
        if not FtgParser._crc_check(cd, crc):
            continue
        if ty == 1: g.append(bytes(pl)); gi.append(k)
        elif ty == 0x0E: e.append(bytes(pl)); ei.append(k)
        elif ty == 0x0F: o.append(bytes(pl)); oi.append(k)
    gps = None
    if g:
        r = pd.DataFrame(np.frombuffer(b''.join(g), dtype=GT))
        dt = pd.to_datetime(pd.Series(decode_datetime_vectorized(r['date'].values, r['time_utc'].values)), errors='coerce', utc=True)
        ok = dt.notna().to_numpy()
        w = dt[ok].astype('int64').to_numpy() / 1e9
        gps = (w, r['filtered_sog_scaled'].to_numpy()[ok], r['sog_scaled'].to_numpy()[ok],
               deg(r['gps_nmea_latitude'].to_numpy()[ok]), deg(r['gps_nmea_longitude'].to_numpy()[ok]),
               r['h_acc_scaled'].to_numpy()[ok].astype(np.int32), r['navigation_satellites'].to_numpy()[ok].astype(np.int16),
               r['avg_cn0_scaled'].to_numpy()[ok].astype(np.int16), np.array(gi, dtype=np.int32)[ok])

    def imu(msgs, idxs, cls, n_per):
        if not msgs:
            return None
        raw = cls.get_raw_dataframe(msgs)
        valid = np.asarray(pd.notna(decode_datetime_vectorized(raw['date'].values, raw['time_utc'].values)))
        df = cls.translate_vectorized(raw)
        if df is None or len(df) == 0:
            return None
        t = df['datetime'].to_numpy().astype('datetime64[ns]').astype('int64') / 1e9
        idx = np.repeat(np.asarray(idxs, dtype=np.int32)[valid], n_per)
        acc = np.stack([df[c].to_numpy().astype(np.float32) for c in ('acc_x', 'acc_y', 'acc_z')], 1)
        gyr = np.stack([df[c].to_numpy().astype(np.float32) for c in ('gyro_x', 'gyro_y', 'gyro_z')], 1)
        return t, idx, acc, gyr
    return gps, imu(e, ei, Imu0x0EMessage, 10), imu(o, oi, Imu0x0FMessage, 1)


def segments(files):
    """P.ftg_segments 와 같은 규칙(GPS 시각이 뒤로 뛰면 자르고, 시각 · 위치가 같은 구간은 하나) — 구간 → (파일 번호, idx 범위) 도 함께."""
    segs, where, imus, seen = [], {}, [], set()
    for fid, f in enumerate(files):
        gps, e, o = read_file(f)
        imus.append((e, o))
        if gps is None:
            continue
        cut = np.where(np.diff(gps[0]) < -P.SEG_BACK_S)[0] + 1
        for idx in np.split(np.arange(len(gps[0])), cut):
            if len(idx) < 10:
                continue
            key = hash((gps[0][idx].tobytes(), gps[3][idx].tobytes(), gps[4][idx].tobytes()))
            if key in seen:
                continue
            seen.add(key)
            seg = tuple(c[idx].tolist() for c in gps[:5]) + tuple(c[idx] for c in gps[5:])
            segs.append(seg); where[id(seg)] = (fid, int(gps[8][idx][0]), int(gps[8][idx][-1]))
    return segs, where, imus


def imu_slice(imus, where_seg, lo_t, hi_t, prefer):
    fid, i0, i1 = where_seg
    for src in prefer:
        arr = imus[fid][0 if src == 'E' else 1]
        if arr is None:
            continue
        t, idx, acc, gyr = arr
        m = (idx >= i0) & (idx <= i1) & (t >= lo_t) & (t <= hi_t)
        if m.sum() >= 50:
            return src, t[m], acc[m], gyr[m]
    return None


def gnss_items(seg, off, t0, t1):
    w = np.asarray(seg[0]) + off; la, lo = np.asarray(seg[3]), np.asarray(seg[4])
    m = (w >= t0) & (w <= t1)
    fix = m & ~((la == 0.0) & (lo == 0.0))
    out = {'n_gps': int(m.sum()), 'fix_pct': round(100.0 * float(fix.sum()) / max(int(m.sum()), 1), 1)}
    if fix.sum() < 50:
        return out
    e, n = (lo[fix] - LON0) * KLON, (la[fix] - LAT0) * 110574.0
    d = np.hypot(e, n); out['pos_h'] = round(float(np.median(d)), 3)
    out['pos_sd'] = round(float(np.sqrt(np.mean((e - np.median(e)) ** 2 + (n - np.median(n)) ** 2))), 3)
    sog = np.asarray(seg[2], dtype=np.float64)[fix]; sog = sog[(sog >= 0) & (sog <= P.FTG_MAX_MM_S)]
    if len(sog):
        out['sog_rms'] = round(float(np.sqrt(np.mean(sog ** 2))), 1); out['sog_p95'] = round(float(np.percentile(sog, 95)), 1)
    out['h_acc'] = round(float(np.median(seg[5][fix])) / 1000, 3); out['sats'] = float(np.median(seg[6][fix])); out['cn0'] = float(np.median(seg[7][fix]))
    return out


def imu_still(sl):
    src, t, acc, gyr = sl
    mag = np.sqrt((acc.astype(np.float64) ** 2).sum(1))
    gb = np.median(gyr, 0)
    return {'imu_src': src, 'n_imu': len(t), 'g_mag': round(float(np.median(mag)) / G_LSB, 4), 'acc_sd_mg': round(float(mag.std()) / G_LSB * 1000, 2),
            'gyro_bias_dps': round(float(np.sqrt((gb.astype(np.float64) ** 2).sum())) / DPS_LSB, 3), 'gyro_sd_dps': round(float(gyr.std(0).mean()) / DPS_LSB, 3)}


def compare_affine(cell_t, cell_v, motor, res, span):
    """A.compare(fit='affine') 와 같은 일 — Python 3.10 의 statistics.linear_regression 에는 proportional 인자가 없어 numpy 로 한다."""
    tm = np.array([A.to_motor(x, res) for x in cell_t]); keep = (tm >= span[0]) & (tm <= span[1])
    if keep.sum() < 3:
        return {'n': 0}
    c = np.asarray(cell_v, dtype=np.float64)[keep]; m = np.asarray(motor(tm[keep].tolist()), dtype=np.float64)
    if m.std() == 0 or c.std() == 0:
        return {'n': int(keep.sum())}
    k, b = np.polyfit(m, c, 1); r = float(np.corrcoef(m, c)[0, 1]); e = (c - b) / k - m
    return {'n': int(keep.sum()), 'k': float(k), 'b': float(b), 'r': r, 'rms': float(np.sqrt(np.mean(e ** 2))), 'p95': float(np.percentile(np.abs(e), 95))}


def imu_rail(sl, tp, off):
    src, tc, acc, _ = sl
    t, p = tp
    spans = A.motion_spans(t, p)
    if not spans:
        return {'imu_src': src, 'imu_status': '모터 정지'}
    wins = [(max(t[0], x - PAD), min(t[-1], y + PAD)) for x, y in ([spans[0], spans[-1]] if len(spans) > 1 else spans)]
    span = (wins[0][0], wins[-1][1])
    it = (tc + off).tolist(); axes = {n: (acc[:, i].astype(np.float64) * MMS2).tolist() for i, n in enumerate('xyz')}
    accel = lambda g: A.motor_accel(t, p, g)
    a0, b0 = wins[0]
    name = max(axes, key=lambda n: abs(A.find_offset(accel, it, axes[n], a0, b0, IMU_DT, IMU_MAX_LAG, signed=True)['r']))
    res = A.offsets(accel, it, axes[name], wins, IMU_DT, IMU_MAX_LAG, signed=True)
    c = compare_affine(it, axes[name], accel, res, span)
    return {'imu_src': src, 'n_imu': len(it), 'imu_axis': name, 'imu_lag': round(res[0]['lag'], 3), 'imu_r0': round(res[0]['r'], 3), 'imu_edge': int(res[0]['edge']),
            'imu_status': A.quality(res), 'imu_k': None if c.get('k') is None else round(c['k'], 3), 'imu_r': None if c.get('r') is None else round(c['r'], 3),
            'imu_rms': None if 'rms' not in c else round(c['rms'], 1), 'imu_p95': None if 'p95' not in c else round(c['p95'], 1)}


COLS = ['set', 'folder', 'stamp', 'kind', 'rig_ok', 'cell', 'col', 'row', 'pick', 'n_gps', 'fix_pct', 'pos_h', 'pos_sd', 'sog_rms', 'sog_p95', 'h_acc', 'sats', 'cn0',
        'imu_src', 'n_imu', 'g_mag', 'acc_sd_mg', 'gyro_bias_dps', 'gyro_sd_dps', 'imu_axis', 'imu_lag', 'imu_r0', 'imu_edge', 'imu_status', 'imu_k', 'imu_r', 'imu_rms', 'imu_p95']


def main(sets):
    out = HERE / 'raw_items.csv'; new = not out.exists()
    fh = open(out, 'a', newline='', encoding='utf-8'); wr = csv.DictWriter(fh, fieldnames=COLS, extrasaction='ignore')
    if new: wr.writeheader()
    for sname in sets:
        ws = windows(SETS[sname])
        for w in ws:
            a, b = (w['t0'], w['t1']) if w['kind'] == 'GNSS-1' else (w['t0'] - 5, w['t1'] + 5)
            w['live'] = P.live(a, b); w['wide'] = P.wide_live(a, b); w['picks'] = {}
        files = {}
        for w in ws:
            for fo in w['ftg']:
                for f in sorted(Path(fo).glob('ftg_*.bin')):
                    files.setdefault(int(f.name.split('ftg_')[-1][:5]), set()).add(f)
        print(f'[{sname}] 창 {len(ws)}개 (GNSS-1 {sum(w["kind"]=="GNSS-1" for w in ws)} · FIRMWARE-1 {sum(w["kind"]=="FIRMWARE-1" for w in ws)}) · 셀 {len(files)}', flush=True)
        for s in sorted(files):
            segs, where, imus = segments(sorted(files[s]))
            n = 0
            for w in ws:
                pk = w['live'].get(s)
                dock = w['dock'].get(s) or {}
                row = {'set': sname, 'folder': w['folder'], 'stamp': w['stamp'], 'kind': w['kind'], 'rig_ok': w['rig_ok'], 'cell': s, 'col': dock.get('col'), 'row': dock.get('row')}
                if not pk:
                    row['pick'] = 'nolive'; wr.writerow(row); continue
                a, b = w['t0'], w['t1']
                seg = P.ftg_pick(segs, s, pk, a, b, w['wide'], w['picks'])
                if seg is None:
                    row['pick'] = 'nopick'; wr.writerow(row); continue
                row['pick'] = 'ok'; n += 1
                row.update(gnss_items(seg, w['off'], a, b))
                lo, hi = a - w['off'], b - w['off']
                if w['kind'] == 'GNSS-1':
                    sl = imu_slice(imus, where[id(seg)], lo, hi, 'OE')
                    if sl: row.update(imu_still(sl))
                else:
                    sl = imu_slice(imus, where[id(seg)], lo - 5, hi + 5, 'EO')
                    if sl:
                        try: row.update(imu_rail(sl, w['tp'], w['off']))
                        except Exception as ex: row['imu_status'] = f'오류 {type(ex).__name__}: {ex}'[:80]
                wr.writerow(row)
            fh.flush()
            print(f'  {s}: 구간 {len(segs)} · 창 {n}/{len(ws)} 계산', flush=True)
            del segs, where, imus
        print(f'[{sname}] 고른 방법', {k: sum(w['picks'].get(k, 0) for w in ws) for k in ('one', 'same', 'wide', 'tie', 'none')}, flush=True)
    fh.close(); print('끝', out)


if __name__ == '__main__':
    main(sys.argv[1:] or ['Y', 'X', 'X5E'])
