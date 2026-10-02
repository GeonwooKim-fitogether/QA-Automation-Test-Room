#!/usr/bin/env python3
# drbfm-qa · 1단계: 두 설계(hardware-map 산출)의 변경점 초안 추출 → changes.json
# 실행: python3 diff_designs.py parts_a.json parts_b.json  [blocks_a.json blocks_b.json]
#   parts_*.json : hardware-map extract_parts.py 산출({block:[[refdes,value,role],…]})
#   blocks_*.json: (선택) {block:{'name':…,'chip':…}} — 블록 추가/삭제·칩 교체 감지용
# 주의: 개정 간 RefDes 재번호가 흔하다 → 이 결과는 "초안". 엔지니어가 의미 있는 변경점으로
#       정리(재번호·미실장 옵션 노이즈 제거, 의도 변경 보완)한 뒤 DRBFM-QA를 작성한다.
import sys, json, re

pa = json.load(open(sys.argv[1], encoding='utf-8')) if len(sys.argv) > 1 else {}
pb = json.load(open(sys.argv[2], encoding='utf-8')) if len(sys.argv) > 2 else {}
ba = json.load(open(sys.argv[3], encoding='utf-8')) if len(sys.argv) > 3 else {}
bb = json.load(open(sys.argv[4], encoding='utf-8')) if len(sys.argv) > 4 else {}

def vmap(block_parts):
    return {r: (v, role) for r, v, role in block_parts}

changes = {'blocks': [], 'parts': {}}

# ── 블록 추가/삭제 · 칩 교체 ──
if ba or bb:
    for b in sorted(set(ba) | set(bb)):
        if b not in ba: changes['blocks'].append({'block': b, 'kind': 'block-added', 'new': bb[b]})
        elif b not in bb: changes['blocks'].append({'block': b, 'kind': 'block-removed', 'old': ba[b]})
        else:
            ca, cb = ba[b].get('chip', ''), bb[b].get('chip', '')
            if ca != cb:
                changes['blocks'].append({'block': b, 'kind': 'chip-changed', 'old': ca, 'new': cb})

# ── 블록별 부품 추가/삭제/값변경 ──
def base_val(v):  # 값에서 풋프린트 접미 등 잡토큰 제거(대략)
    return re.sub(r'\s+', '', str(v)).upper()

for b in sorted(set(pa) | set(pb)):
    A, B = vmap(pa.get(b, [])), vmap(pb.get(b, []))
    added   = [(r, B[r][0], B[r][1]) for r in B if r not in A]
    removed = [(r, A[r][0], A[r][1]) for r in A if r not in B]
    changed = [(r, A[r][0], B[r][0]) for r in (set(A) & set(B))
               if base_val(A[r][0]) != base_val(B[r][0])]
    if added or removed or changed:
        changes['parts'][b] = {'added': added, 'removed': removed, 'changed': changed}

json.dump(changes, open('changes.json', 'w'), ensure_ascii=False, indent=1)

# ── 사람이 읽는 요약 ──
print('== 변경점 초안 (curate 필요) ==')
for c in changes['blocks']:
    print(f"[{c['block']}] {c['kind']}: {c.get('old','')} → {c.get('new','')}")
for b, d in changes['parts'].items():
    for r, ov, nv in d['changed']:
        print(f"[{b}] 값변경 {r}: {ov} → {nv}")
    if d['added']:   print(f"[{b}] 추가: " + ', '.join(f'{r}({v})' for r, v, _ in d['added']))
    if d['removed']: print(f"[{b}] 삭제: " + ', '.join(f'{r}({v})' for r, v, _ in d['removed']))
print('\n→ changes.json 저장. 재번호/미실장(NC) 노이즈를 걸러 의미 있는 변경점만 남기고 DRBFM-QA 작성.')
