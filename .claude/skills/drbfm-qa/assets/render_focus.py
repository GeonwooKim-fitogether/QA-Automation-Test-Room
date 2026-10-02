#!/usr/bin/env python3
# drbfm-qa · 변경 부위 포커스 크롭 + 리비전 구름 마크.
# 변경점의 핵심 부품(RefDes) 주변만 타이트하게 잘라 data-URI로 반환하고,
# mark 로 지정한 부품에는 "구름 마크"(revision cloud) SVG 오버레이를 그려 찾기 쉽게 한다.
# drbfm_data.py 에서 change['asis_img']/['tobe_img'] = focus_crop(pdf, sheet, refs, mark=[…]).
import fitz, base64, math
from functools import lru_cache

@lru_cache(maxsize=8)
def _doc(pdf):
    try: return fitz.open(pdf)
    except Exception: return None

def _bbox(pg, refset):
    xs, ys = [], []
    for w in pg.get_text('words'):
        if w[4] in refset:
            xs += [w[0], w[2]]; ys += [w[1], w[3]]
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None

def _cloud_path(x, y, w, h, r=7):
    """박스(x,y,w,h)를 감싸는 리비전 구름(부채꼴 아크가 바깥으로 볼록한) SVG path d."""
    def edge(x0, y0, x1, y1):
        L = math.hypot(x1 - x0, y1 - y0); n = max(2, round(L / (2 * r)))
        seg = L / n; dx = (x1 - x0) / L; dy = (y1 - y0) / L; rr = seg / 2
        out = []
        for i in range(n):
            ex = x0 + dx * seg * (i + 1); ey = y0 + dy * seg * (i + 1)
            out.append(f'A{rr:.1f} {rr:.1f} 0 0 1 {ex:.1f} {ey:.1f}')  # sweep=1, 시계방향 → 바깥 볼록
        return out
    p = [f'M{x:.1f} {y:.1f}']
    p += edge(x, y, x + w, y); p += edge(x + w, y, x + w, y + h)
    p += edge(x + w, y + h, x, y + h); p += edge(x, y + h, x, y)
    p.append('Z')
    return ''.join(p)

def focus_crop(pdf, sheet_idx, refdes, mark=None, margin=55, scale=3.5, pad=22):
    """refdes(리스트/문자열) 배치를 감싸는 영역 + margin 크롭. mark 부품엔 구름 오버레이.
    반환 {'src': dataURI, 'overlay': svg문자열}. PDF/부품 없으면 None(엔진은 이미지 생략)."""
    if isinstance(refdes, str): refdes = [refdes]
    doc = _doc(pdf)
    if doc is None: return None
    pg = doc[sheet_idx]
    box = _bbox(pg, set(refdes))
    if box is None: return None
    r = pg.rect
    x0 = max(0, box[0] - margin); y0 = max(0, box[1] - margin)
    x1 = min(r.width, box[2] + margin); y1 = min(r.height, box[3] + margin)
    pix = pg.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=fitz.Rect(x0, y0, x1, y1))
    src = 'data:image/png;base64,' + base64.b64encode(pix.tobytes('png')).decode()
    cw, ch = pix.width, pix.height

    clouds = []
    for m in (mark or []):
        mb = _bbox(pg, {m})
        if not mb: continue
        mx = (mb[0] - pad - x0) * scale; my = (mb[1] - pad - y0) * scale
        mw = (mb[2] - mb[0] + 2 * pad) * scale; mh = (mb[3] - mb[1] + 2 * pad) * scale
        clouds.append(f'<path d="{_cloud_path(mx, my, mw, mh)}" fill="none" stroke="#d24a68" '
                      f'stroke-width="{2.2*scale:.1f}" opacity="0.9"/>')
    overlay = (f'<svg class="cloud" viewBox="0 0 {cw} {ch}" preserveAspectRatio="xMidYMid meet">'
               f'{"".join(clouds)}</svg>') if clouds else ''
    return {'src': src, 'overlay': overlay}
