#!/usr/bin/env python3
# hardware-map · 1단계: 스키매틱 PDF → 블록별 크롭(crops.json) + 전체 시트(imgs.json)
# 실행: python3 render_crops.py <schematic.pdf>
# 필요: pip install pymupdf   (import fitz)
# 편집: crops_def.py 의 CROP / SHEETS 를 이 제품에 맞게 채운다(엔진·추출기와 공용).
import sys, fitz, base64, json
from crops_def import CROP, SHEETS

pdf = sys.argv[1] if len(sys.argv) > 1 else 'schematic.pdf'
doc = fitz.open(pdf)

def png_b64(pix):
    return 'data:image/png;base64,' + base64.b64encode(pix.tobytes('png')).decode()

def to_abs(si, x0, y0, x1, y1):
    # 좌표가 모두 0~1이면 페이지 대비 분수로 간주 → 절대좌표(pt) 환산
    r = doc[si].rect
    if max(x0, y0, x1, y1) <= 1.0:
        return x0*r.width, y0*r.height, x1*r.width, y1*r.height
    return x0, y0, x1, y1

crops = {}
for bid, (si, x0, y0, x1, y1) in CROP.items():
    ax0, ay0, ax1, ay1 = to_abs(si, x0, y0, x1, y1)
    pix = doc[si].get_pixmap(matrix=fitz.Matrix(3, 3), clip=fitz.Rect(ax0, ay0, ax1, ay1))
    crops[bid] = png_b64(pix)
json.dump(crops, open('crops.json', 'w'))

# 전체 시트 이미지 — key = 블록의 sh 값(시트 그룹), value = 페이지 인덱스
imgs = {}
for sh_key, page_idx in SHEETS.items():
    imgs[sh_key] = png_b64(doc[page_idx].get_pixmap(matrix=fitz.Matrix(2, 2)))
json.dump(imgs, open('imgs.json', 'w'))

print('crops:', len(crops), '| sheets:', len(imgs),
      '| crop bytes:', sum(len(v) for v in crops.values()),
      '| sheet bytes:', sum(len(v) for v in imgs.values()))
