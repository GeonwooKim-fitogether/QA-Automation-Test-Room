#!/usr/bin/env python3
# hardware-map · 2단계: 스키매틱에서 블록별 "모든 부품(RefDes)"을 자동 추출 → parts.json
# 실행: python3 extract_parts.py <schematic.pdf>
# 핵심: RefDes를 그냥 텍스트로 매칭하면 LGA/BGA "핀 이름"(C1,NC1,B2…)이 오검출된다.
#       → 실제 배치만 인정: 같은 계열 풋프린트(C1005 등)가 근처에 붙은 위치.
#       → 같은 RefDes가 여러 번(핀/네트라벨) 나오면 풋프린트 최근접 1곳만 채택(물리 부품 1개).
import sys, fitz, json, re, math
from collections import defaultdict
from crops_def import CROP

pdf = sys.argv[1] if len(sys.argv) > 1 else 'schematic.pdf'
doc = fitz.open(pdf)
SHEETS_USED = sorted({r[0] for r in CROP.values()})

def to_abs(si, x0, y0, x1, y1):
    r = doc[si].rect
    if max(x0, y0, x1, y1) <= 1.0:
        return x0*r.width, y0*r.height, x1*r.width, y1*r.height
    return x0, y0, x1, y1
CROP_ABS = {b: (r[0], *to_abs(*r)) for b, r in CROP.items()}

REF = re.compile(r'^(U|R|C|L|D|Q|Y|SW|CON|ANT|NR|J|E|MP)\d{1,3}[A-Z]?$')
# 접두별 풋프린트 토큰(실제 배치 판별용). 새 라이브러리 풋프린트가 있으면 여기 추가.
FOOTP = {
    'R': re.compile(r'^R\d{4}$'),
    'C': re.compile(r'^(C\d{4}|C0201)$'),
    'L': re.compile(r'^L\d{4}$'),
    'D': re.compile(r'^(D\d{4}|SOD.*|SOT.*|LED\d+|BAS70.*|1N\d.*|ASMB.*|VESD.*|TPD.*)$', re.I),
}
UNIT = re.compile(r'(uF|nF|pF|µF|mAh|MHz|kHz|Hz|/F|/J|K/|R/|Ω|Ohm|\dV\b|\d\.\d|0R)')

# ── RefDes→값 전역 맵: 라벨 "R46 1K5/J R1005"에서 풋프린트 앞 토큰이 값 ──
alltext = re.sub(r'\s+', ' ', ' '.join(doc[i].get_text() for i in SHEETS_USED))
VAL = {}
for pat in [r'\b(R\d{1,3})\s+(\S+)\s+R\d{4}\b', r'\b(C\d{1,3})\s+(\S+)\s+C\d{4}\b',
            r'\b(L\d{1,3})\s+(\S+)\s+(?:L\d{4})\b', r'\b(NR\d{1,3})\s+(\S+)\s+R\d{4}\b']:
    for m in re.finditer(pat, alltext):
        VAL.setdefault(m.group(1), m.group(2))
# IC/반도체/커넥터 값 — 넷리스트에서 확인해 제품별로 채운다(passive는 위에서 자동).
VAL.update({
    # 'U3':'ESP32-S3-MINI-1', 'U13':'NEO-F10N', 'U1':'BQ25176J', 'ANT1':'...', ...
})

# ── 실제 배치 좌표 검출 ──
def align_score(x, y, pfx, toks):
    fp = FOOTP[pfx]; best = None
    for fx, fy, ft in toks:
        if fp.match(ft):
            d = math.hypot(fx-x, fy-y)
            if d < 40 and (best is None or d < best):
                best = d
    return best

occ = defaultdict(list)  # refdes -> [(si,x,y,score)]
for si in SHEETS_USED:
    toks = [(w[0], w[1], w[4]) for w in doc[si].get_text('words')]
    for x, y, t in toks:
        if not REF.match(t): continue
        pfx = re.match(r'^([A-Z]+)', t).group(1)
        occ[t].append((si, x, y, align_score(x, y, pfx, toks) if pfx in FOOTP else 0))

PLACE = []
for refdes, cands in occ.items():
    pfx = re.match(r'^([A-Z]+)', refdes).group(1)
    if pfx in FOOTP:
        aligned = [c for c in cands if c[3] is not None]
        if aligned:                              # 풋프린트 최근접 1곳만(핀 오검출 제거)
            si, x, y, _ = min(aligned, key=lambda c: c[3])
            PLACE.append((refdes, si, x, y))
        # 풋프린트가 전혀 없으면 핀 이름 → 버림
    else:                                        # U/SW/ANT/Y/CON/MP 등은 핀 충돌 없음
        for si, x, y, _ in cands:
            PLACE.append((refdes, si, x, y))

# ── 크롭 rect에 들어오는 부품을 그 블록에 배정(목록 = 크롭에 보이는 부품) ──
parts = {b: [] for b in CROP}
for refdes, si, x, y in PLACE:
    for b, r in CROP_ABS.items():
        if r[0] == si and r[1] <= x <= r[3] and r[2] <= y <= r[4]:
            parts[b].append((refdes, VAL.get(refdes, '')))

ROLE = {'U':'IC','R':'저항 — 풀업/직렬/전류제한','C':'커패시터 — 디커플링/필터',
        'L':'페라이트 비드/인덕터 — 노이즈 억제·정합','D':'다이오드 — 정류/보호(ESD)',
        'Q':'MOSFET — 스위치/구동','MP':'MOSFET — 스위치/구동','Y':'크리스탈 — 클럭 기준',
        'SW':'택트 스위치','CON':'커넥터','J':'커넥터/테스트','ANT':'안테나',
        'NR':'NTC 서미스터 — 온도 감지','E':'커넥터 핀'}
pref = lambda r: re.match(r'^([A-Z]+)', r).group(1)
def keyf(r):
    m = re.match(r'^([A-Z]+)(\d+)', r); return (m.group(1), int(m.group(2))) if m else (r, 0)

out = {}
for b, lst in parts.items():
    seen = {}
    for r, v in lst:
        seen.setdefault(r, v)
    out[b] = [[r, seen[r], ROLE.get(pref(r), '부품')] for r in sorted(seen, key=keyf)]
json.dump(out, open('parts.json', 'w'), ensure_ascii=False)
print('blocks:', len(out))
for b in list(out)[:6]:
    print(' ', b, len(out[b]), '→', ' '.join(f'{r}({v})' for r, v, _ in out[b][:12]))
