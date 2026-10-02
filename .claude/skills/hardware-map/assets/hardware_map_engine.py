#!/usr/bin/env python3
# hardware-map ENGINE — 고정 템플릿. 편집 금지(수정하면 매 산출물이 달라짐).
# 입력: 같은 폴더의 hw_data.py(제품별 데이터) + crops.json + imgs.json
# 실행: python3 hardware_map_engine.py  → META['out'] 경로에 자체완결 HTML 생성
import json, html, math, os, re
from hw_data import *   # META, OUT, imgs, crops, B, E, WF, PARTS, SPEC, FLOW, TUTOR, DS, PART_DS, PART_SPEC, GC, GLAB, BC, BLAB

def esc(s): return html.escape(str(s))
# 인접 관계
adj={k:[] for k in B}
for a,b,bus,lab,dr in E:
    adj[a].append((b,bus,lab)); adj[b].append((a,bus,lab))

# 노드 SVG
def node_svg():
    out=[]
    for k,(nm,chip,g,role,x,y,sh) in B.items():
        c=GC[g]; big=(k=='mcu')
        w,h=(150,58) if big else (128,50)
        out.append(f'''<g class="gnode" data-id="{k}" transform="translate({x-w//2},{y-h//2})" tabindex="0">
          <rect class="nbg" width="{w}" height="{h}" rx="11" fill="var(--card)" fill-opacity="0.92" stroke="{c}" stroke-width="{1.5 if big else 1}"/>
          <rect class="nsel" x="3" y="3" width="{w-6}" height="{h-6}" rx="8" fill="none" stroke="#fff"/>
          <text class="nnm" x="13" y="{22 if big else 20}">{esc(nm)}</text>
          <text class="nchip" x="13" y="{38 if big else 34}">{esc(chip)}</text>
        </g>''')
    return '\n'.join(out)

def nsz(k):
    return (76,30) if k=='mcu' else (65,26)  # half-width, half-height
def trim(cx,cy,ox,oy,hw,hh,gap=8):
    # 선을 노드 사각형 경계 + gap 바깥에서 끊는다 → 선이 노드 안으로 안 들어감
    dx,dy=ox-cx,oy-cy
    if dx==0 and dy==0: return cx,cy
    sx=hw/abs(dx) if dx else 1e9
    sy=hh/abs(dy) if dy else 1e9
    s=min(sx,sy)
    bx,by=cx+dx*s, cy+dy*s
    L=math.hypot(dx,dy)
    return round(bx+dx/L*gap,1), round(by+dy/L*gap,1)
def arrowhead(px,py,ang,c,a,b,h=5.5):
    # (px,py) 중심, ang 방향으로 향하는 삼각형 화살촉
    tip=(px+math.cos(ang)*h, py+math.sin(ang)*h)
    b1=(px+math.cos(ang+2.6)*h, py+math.sin(ang+2.6)*h)
    b2=(px+math.cos(ang-2.6)*h, py+math.sin(ang-2.6)*h)
    pts=f'{tip[0]:.1f},{tip[1]:.1f} {b1[0]:.1f},{b1[1]:.1f} {b2[0]:.1f},{b2[1]:.1f}'
    return f'<polygon class="garrow" data-a="{a}" data-b="{b}" points="{pts}" fill="{c}"/>'
def edge_svg():
    out=[]
    for a,b,bus,lab,dr in E:
        ax,ay=B[a][4],B[a][5]; bx,by=B[b][4],B[b][5]
        # 중심→중심으로 그려 선이 끊기지 않게. 노드 반투명 채움이 위에 얹혀
        # 노드 안 구간은 자연히 옅게(반투명) 보이고 밖은 진하게 보인다.
        x1,y1=ax,ay
        x2,y2=bx,by
        mx,my=(ax+bx)/2,(ay+by)/2
        c=BC[bus]; dash='' if bus!='CTRL' else 'stroke-dasharray="2 4"'
        out.append(f'<path class="gedge" data-a="{a}" data-b="{b}" data-bus="{bus}" d="M{x1} {y1} L{x2} {y2}" stroke="{c}" stroke-width="{1.4 if bus in ("PWR","RF") else 1.1}" {dash}/>')
        # 방향 화살표(중간점). ab=a→b, ba=b→a, bi=양방향(↔)
        fwd=math.atan2(by-ay,bx-ax)
        if dr=='ab':
            out.append(arrowhead(mx,my,fwd,c,a,b))
        elif dr=='ba':
            out.append(arrowhead(mx,my,fwd+math.pi,c,a,b))
        else:  # bi
            off=4.5
            out.append(arrowhead(mx+math.cos(fwd)*off,my+math.sin(fwd)*off,fwd,c,a,b))
            out.append(arrowhead(mx-math.cos(fwd)*off,my-math.sin(fwd)*off,fwd+math.pi,c,a,b))
        if lab and bus in ('I2C','SPI','UART','SDIO','RF','USB'):
            out.append(f'<text class="elab" data-a="{a}" data-b="{b}" x="{mx}" y="{my-8}" fill="{c}">{esc(lab)}</text>')
    return '\n'.join(out)

def detail_js_data():
    d={}
    for k,(nm,chip,g,role,x,y,sh) in B.items():
        d[k]={'nm':nm,'chip':chip,'g':g,'role':role,'sh':sh,
              'specs':SPEC.get(k,[]),
              'conns':[{'to':t,'toNm':B[t][0],'bus':bus,'lab':lab} for (t,bus,lab) in adj[k]]}
    return json.dumps(d,ensure_ascii=False)

# 버스 요약(A-Z 완전성)
def bus_summary():
    from collections import defaultdict
    m=defaultdict(set)
    for a,b,bus,lab,dr in E: m[bus].add(a); m[bus].add(b)
    rows=[]
    for bus in ['PWR','I2C','SPI','UART','SDIO','RF','USB','CTRL']:
        if bus in m:
            members=' · '.join(B[x][0] for x in m[bus])
            rows.append(f'<tr><td><span class="bpill" style="background:{BC[bus]}">{esc(BLAB[bus])}</span></td><td>{esc(members)}</td></tr>')
    return '\n'.join(rows)

HTML=f'''<title>{META['model']} 시스템 이해 — 스키매틱 기반 관계도</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{{--paper:#eef1f5;--card:#ffffff;--ink:#16212c;--ink2:#53626e;--faint:#8b98a4;--line:#dce2e8;--accent:#d24a68;
 --mono:ui-monospace,"SFMono-Regular",Menlo,Consolas,monospace;--sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
 --shadow:0 1px 2px rgba(22,33,44,.05),0 10px 26px rgba(22,33,44,.08)}}
@media(prefers-color-scheme:dark){{:root{{--paper:#0e141a;--card:#182029;--ink:#e7edf2;--ink2:#a6b2bd;--faint:#6f7d88;--line:#26313b;--shadow:0 1px 2px rgba(0,0,0,.3),0 12px 30px rgba(0,0,0,.4)}}}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--paper);color:var(--ink);font-family:var(--sans);line-height:1.5}}
.wrap{{max-width:1180px;margin:0 auto;padding:22px 18px 60px}}
h1,h2,h3,p{{margin:0;text-wrap:balance}} .mono{{font-family:var(--mono)}}
.hd{{margin-bottom:14px}}
.hd .eyebrow{{font-size:11.5px;font-weight:800;letter-spacing:.1em;text-transform:uppercase;color:var(--accent)}}
.hd h1{{font-size:24px;font-weight:800;letter-spacing:-.02em;margin:5px 0 6px}}
.hd p{{color:var(--ink2);font-size:13.5px;max-width:720px}}
.levels{{display:flex;gap:6px;flex-wrap:wrap;margin-top:12px;font-size:11.5px}}
.lv{{background:var(--card);border:1px solid var(--line);border-radius:999px;padding:5px 11px;color:var(--ink2);box-shadow:var(--shadow);cursor:pointer;font-family:inherit;font-size:11.5px;line-height:1.4}}
.lv:hover{{border-color:var(--accent);color:var(--accent)}}
.lv b{{color:var(--ink)}} .lv.cur{{border-color:var(--accent);color:var(--accent);background:color-mix(in srgb,var(--accent) 8%,var(--card))}}
.lv.cur b{{color:var(--accent)}}

.stage{{display:grid;grid-template-columns:1fr 420px;gap:16px;margin-top:6px;align-items:start}}
@media(max-width:920px){{.stage{{grid-template-columns:1fr}}}}
/* 포커스 모드 — L1: 관계도 그래프가 전체폭 / L2: 상세 패널이 전체폭 */
.stage.mode-graph{{grid-template-columns:1fr}}
.stage.mode-graph .side{{display:none}}
.stage.mode-graph #svgwrap{{min-height:74vh;display:flex;align-items:center}}
.stage.mode-graph svg#g{{min-height:72vh}}
.stage.mode-panel{{grid-template-columns:1fr}}
.stage.mode-panel .graphcard{{display:none}}
.stage.mode-panel .side{{position:static}}
.stage.mode-panel .panel{{width:100%;max-width:1120px;margin:0 auto}}
/* 상세 ①②③를 가로로 펼쳐 전체폭을 채움 */
.stage.mode-panel .pb{{display:grid;grid-template-columns:repeat(3,1fr);gap:4px 26px;align-items:start;font-size:13px}}
.stage.mode-panel .pb .psec{{border-top:0;border-left:1px solid var(--line);padding:4px 0 4px 16px}}
.stage.mode-panel .pb .psec:first-child{{border-left:0;padding-left:0}}
/* 흐름·부품표는 전체폭 행으로 */
.stage.mode-panel .pb #sec-flow,.stage.mode-panel .pb #sec-parts,.stage.mode-panel .pb .seebtn{{grid-column:1/-1;border-left:0;padding-left:0;margin-top:6px;border-top:1px solid var(--line);padding-top:10px}}
@media(max-width:820px){{.stage.mode-panel .pb{{grid-template-columns:1fr}} .stage.mode-panel .pb .psec{{border-left:0;padding-left:0}}}}
@media(max-width:720px){{.stage.mode-panel .pb{{grid-template-columns:1fr}} .stage.mode-panel .pb .psec{{border-left:0;padding-left:0;border-top:1px solid var(--line)}}}}
.graphcard{{background:var(--card);border:1px solid var(--line);border-radius:16px;box-shadow:var(--shadow);overflow:hidden}}
.gchd{{display:flex;align-items:center;justify-content:space-between;padding:12px 16px;border-bottom:1px solid var(--line)}}
.gchd .t{{font-size:15px;font-weight:800}} .gchd .t span{{color:var(--faint);font-weight:600;font-size:12px}}
.legend{{display:flex;gap:11px;flex-wrap:wrap;font-size:11px;color:var(--ink2)}}
.legend .li{{display:inline-flex;align-items:center;gap:5px}} .legend .sw{{width:16px;height:3px;border-radius:2px}}
#svgwrap{{width:100%;overflow-x:auto}}
svg#g{{display:block;width:100%;height:auto;min-width:680px}}
.gedge{{transition:.15s;opacity:.85}} .elab{{font:600 10px var(--mono);pointer-events:none;opacity:.9}}
.gnode{{cursor:pointer}} .gnode .nbg{{transition:stroke-width .12s}}
.nnm{{font:800 13px var(--sans);fill:var(--ink)}} .gnode[data-id=mcu] .nnm{{font-size:14px}}
.nchip{{font:600 9.5px var(--mono);fill:var(--faint)}}
/* 선택: 자기 색 테두리만 살짝 두껍게 (노란/흰색 없음) */
.gnode.hot .nbg{{stroke-width:2.4}} .gnode[data-id=mcu].hot .nbg{{stroke-width:2.8}}
.nsel{{stroke-width:0;opacity:.9;transition:stroke-width .12s}} .gnode.hot .nsel{{stroke-width:1.1}}
.dim{{opacity:.14}}
.gedge.hot{{opacity:1;stroke-width:1.8}} .gedge.dim,.elab.dim{{opacity:.08}}
.garrow{{opacity:.85;transition:.15s}} .garrow.hot{{opacity:1}} .garrow.dim{{opacity:.08}}
:focus-visible{{outline:2px solid var(--accent);outline-offset:2px}}
.gnode:focus,.gnode:focus-visible{{outline:none}}

.side{{display:flex;flex-direction:column;gap:14px}}
.panel{{background:var(--card);border:1px solid var(--line);border-radius:14px;box-shadow:var(--shadow);overflow:hidden}}
.panel .ph{{padding:11px 15px;border-bottom:1px solid var(--line)}}
.backbtn{{display:inline-flex;align-items:center;max-width:100%;margin-bottom:8px;background:var(--paper);border:1px solid var(--line);border-radius:7px;color:var(--ink2);font-size:11px;font-weight:600;padding:3px 9px;cursor:pointer;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.backbtn:hover{{border-color:var(--accent);color:var(--accent)}}
.panel .ph .tag{{font-size:10px;font-weight:800;padding:2px 8px;border-radius:6px;color:#fff}}
.panel .ph h3{{font-size:16px;font-weight:800;margin:7px 0 1px}}
.panel .ph .chip{{font-family:var(--mono);font-size:11.5px;color:var(--ink2)}}
.panel .pb{{padding:4px 15px 14px}}
/* 3분할 섹션 — 번호 + 제목 */
.psec{{padding:9px 0;border-top:1px solid var(--line)}}
.psec:first-child{{border-top:0;padding-top:9px}}
.psec-h{{display:flex;align-items:center;gap:6px;font-size:10.5px;font-weight:800;letter-spacing:.02em;color:var(--ink2);margin:0 0 7px}}
.psn{{display:inline-flex;align-items:center;justify-content:center;width:16px;height:16px;border-radius:5px;background:color-mix(in srgb,var(--ink) 16%,transparent);color:var(--ink);font-size:10px;font-weight:800;font-family:var(--mono);flex:none}}
.psc{{color:var(--faint);font-weight:700;font-family:var(--mono);font-size:10.5px}}
.panel .role{{font-size:12.5px;line-height:1.55;color:var(--ink)}}
/* 직접 연결 — 버스별 그룹(1줄=1버스) 컴팩트 */
.conns{{display:flex;flex-direction:column;gap:2px}}
.congrp{{display:flex;align-items:center;gap:7px;font-size:11.5px;line-height:1.3}}
.congrp .bus{{font-size:8.5px;font-weight:600;color:#fff;padding:1px 5px;border-radius:4px;flex:none;min-width:34px;text-align:center}}
.cnames{{display:flex;flex-wrap:wrap;align-items:center;gap:1px 5px}}
.conname{{font-weight:400;color:var(--ink);cursor:pointer}}
.conname:hover{{color:var(--accent);text-decoration:underline}}
.conname .clab{{color:var(--faint);font-size:10px;font-weight:400;margin-left:2px}}
.cdot{{color:var(--faint);font-size:10px}}
/* 관여 워크플로우 — 접이식 (한 줄 → 펼치면 흐름) */
.wflist{{display:flex;flex-direction:column;gap:3px}}
.wfitem{{background:var(--paper);border:1px solid var(--line);border-radius:8px;overflow:hidden}}
.wfhead{{width:100%;display:flex;align-items:center;justify-content:space-between;gap:8px;background:none;border:0;cursor:pointer;
 padding:5px 10px;font:inherit;text-align:left;color:var(--ink)}}
.wfhead:hover{{background:color-mix(in srgb,var(--accent) 6%,transparent)}}
.wfhead.on{{background:color-mix(in srgb,var(--accent) 9%,transparent)}}
.wfname{{font-size:11.5px;font-weight:600;color:var(--ink)}}
.wfmeta{{display:inline-flex;align-items:center;gap:5px;flex:none;font-size:10px;font-weight:600;color:var(--faint);font-family:var(--mono)}}
.wfcv{{color:var(--faint)}}
.wfbody{{padding:7px 10px 9px}}
.wfchain{{display:flex;flex-wrap:wrap;align-items:center;gap:6px 9px;margin-bottom:6px}}
.wfstep{{font-size:10.5px;font-weight:600;color:var(--ink2);background:var(--card);border:1px solid var(--line);border-radius:5px;padding:2px 8px;cursor:pointer;white-space:nowrap}}
.wfstep:hover{{border-color:var(--accent)}}
.wfstep.cur{{font-weight:800;border-width:1.5px}}
.wfar{{color:var(--faint);font-family:var(--mono);font-size:10px;margin:0 1px}}
.wfdesc{{font-size:11px;color:var(--ink2);line-height:1.45}}
.wfnone{{font-size:12px;color:var(--faint);padding:6px 2px}}
.seebtn{{margin-top:14px;width:100%;border:1px solid var(--line);background:var(--paper);color:var(--ink);border-radius:10px;
 padding:10px;font-size:12.5px;font-weight:700;cursor:pointer;display:flex;align-items:center;justify-content:center;gap:7px}}
.seebtn:hover{{border-color:var(--accent);color:var(--accent)}}
.hintbox{{padding:16px;font-size:12.5px;color:var(--ink2);text-align:center}}

.summ{{margin-top:16px}} .summ h3{{font-size:14px;margin-bottom:9px}}
.tw{{overflow-x:auto;border:1px solid var(--line);border-radius:12px;background:var(--card);box-shadow:var(--shadow)}}
table{{width:100%;border-collapse:collapse;font-size:12.5px;min-width:520px}}
th,td{{text-align:left;padding:9px 13px;border-bottom:1px solid var(--line)}}
th{{color:var(--faint);font-size:10.5px;text-transform:uppercase;letter-spacing:.05em}}
.bpill{{font-size:10px;font-weight:800;color:#fff;padding:2px 9px;border-radius:6px;white-space:nowrap}}

/* schematic modal (Level 3) */
.scrim{{display:none;margin-top:16px;scroll-margin-top:12px}}
.scrim.on{{display:block;animation:l4in .22s ease}}
@keyframes l4in{{from{{opacity:0;transform:translateY(6px)}}to{{opacity:1;transform:none}}}}
.modal{{background:var(--card);border:1px solid var(--accent);border-radius:14px;width:100%;overflow:hidden;box-shadow:var(--shadow)}}
.modal .mh{{background:color-mix(in srgb,var(--accent) 10%,var(--card));display:flex;justify-content:space-between;align-items:center;padding:12px 16px;border-bottom:1px solid var(--line)}}
.modal .mh b{{font-size:14px}} .modal .mh .x{{border:1px solid var(--line);background:var(--paper);border-radius:7px;font-size:11.5px;font-weight:600;padding:3px 9px;cursor:pointer;color:var(--ink2)}}
.modal .mh .x:hover{{border-color:var(--accent);color:var(--accent)}}
.modal .whatis{{padding:12px 16px;font-size:12.5px;line-height:1.6;color:var(--ink2);background:color-mix(in srgb,var(--accent) 7%,transparent);border-bottom:1px solid var(--line)}}
.modal .whatis b{{color:var(--ink)}}
.modal .mbody{{padding:10px;background:#fff}} .modal img{{width:100%;display:block}}
.modal .mbody2{{display:grid;grid-template-columns:1.15fr .85fr;gap:0}}
@media(max-width:760px){{.modal .mbody2{{grid-template-columns:1fr}}}}
.modal .cropbox{{background:#fff;padding:14px;display:flex;align-items:center;justify-content:center;border-right:1px solid var(--line);min-width:0}}
.modal .cropbox img{{width:100%;height:auto;border-radius:6px;cursor:zoom-in}}
.modal .tutor{{padding:16px 18px;display:flex;flex-direction:column;gap:16px;min-width:0}}
.modal .tutor>*{{min-width:0;max-width:100%}}
/* 이미지 클릭 → 회로도만 전체폭으로 크게 */
.modal.imgfull .tutor{{display:none}}
.modal.imgfull .mbody2{{grid-template-columns:1fr}}
.modal.imgfull .cropbox{{border-right:0;padding:18px 22px}}
.modal.imgfull .cropbox img{{cursor:zoom-out}}
.modal .mfoot{{padding:12px 16px;border-top:1px solid var(--line);display:flex;justify-content:center;gap:10px}}
.modal .mfoot .fullbtn{{max-width:360px}}
.modal .tintro{{font-size:14px;font-weight:700;color:var(--ink);line-height:1.5}}
.seclab{{font-size:11px;font-weight:800;letter-spacing:.03em;color:var(--ink2);margin-bottom:9px}}
/* flow diagram */
.flowdiag{{display:flex;align-items:center;gap:6px}}
.flowdiag .fcol{{display:flex;flex-direction:column;gap:5px;flex:1;min-width:0}}
.flowdiag .farr{{color:var(--faint);font-size:11px;flex:none}}
.flowdiag .fcore{{flex:0 0 auto;max-width:34%;background:color-mix(in srgb,var(--ink) 10%,var(--card));color:var(--ink);border:1px solid var(--line);border-radius:9px;padding:8px 10px;font-size:12px;font-weight:800;text-align:center;line-height:1.3}}
.fchip{{border-radius:8px;padding:5px 8px;font-size:11px;line-height:1.3;border:1px solid var(--line)}}
.fchip b{{display:block;font-family:var(--mono);font-weight:700;color:var(--ink)}}
.fchip span{{color:var(--ink2);font-size:10.5px}}
.fchip.in{{background:color-mix(in srgb,#2f6fc4 8%,transparent);border-color:color-mix(in srgb,#2f6fc4 26%,transparent)}}
.fchip.out{{background:color-mix(in srgb,#2e9e68 9%,transparent);border-color:color-mix(in srgb,#2e9e68 28%,transparent)}}
/* parts table */
table.ptbl{{width:100%;min-width:0;max-width:100%;border-collapse:collapse;font-size:12px;table-layout:fixed}}
table.ptbl td{{padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}}
table.ptbl .pr{{font-family:var(--mono);font-weight:700;color:var(--ink);width:20%;word-break:break-all}}
table.ptbl .pv{{font-family:var(--mono);color:var(--ink2);width:22%;font-size:11px;word-break:break-all}}
table.ptbl .pd{{color:var(--ink);line-height:1.45;white-space:normal;word-break:keep-all;overflow-wrap:anywhere}}
/* 부품 행 액션(스펙요약 버튼 + 원본 링크) — 버튼 먼저, 링크 오른쪽 */
.pactions{{margin-top:7px;display:flex;flex-wrap:wrap;align-items:center;gap:6px 12px}}
.pdsl{{display:inline-flex;align-items:center;gap:3px;font-size:10.5px;font-weight:600;text-decoration:none;color:var(--ink2);white-space:nowrap;border-bottom:1px dotted currentColor;padding-bottom:1px}}
.pdsl.none{{color:var(--faint);font-weight:500;border-bottom-style:none;cursor:default}}
.pdsl:hover{{opacity:.75}}
/* 접이식 스펙 요약 카드 */
.specbtn{{display:inline-flex;align-items:center;gap:5px;font-size:10.5px;font-weight:700;color:var(--ink2);background:color-mix(in srgb,var(--ink) 6%,transparent);border:1px solid var(--line);border-radius:7px;padding:4px 9px;cursor:pointer;line-height:1.1}}
.specbtn:hover{{background:color-mix(in srgb,var(--ink) 12%,transparent)}}
.specbtn.on{{background:color-mix(in srgb,var(--ink) 16%,transparent);color:var(--ink);border-color:var(--faint)}}
.psrow>td{{padding:0 8px 10px!important;border-bottom:1px solid var(--line)}}
.pspec{{background:color-mix(in srgb,var(--accent) 6%,var(--card));border:1px solid color-mix(in srgb,var(--accent) 22%,transparent);border-radius:9px;padding:11px 12px}}
.psname{{font-weight:800;font-size:12px;margin-bottom:8px;color:var(--ink)}}
.pslab{{font-size:10px;font-weight:800;letter-spacing:.02em;color:var(--ink2);margin:9px 0 3px}}
.pslab.red{{color:#e0567a}}
table.pst{{width:100%;border-collapse:collapse;font-size:11px;table-layout:fixed}}
table.pst td{{padding:3px 6px;border-bottom:1px solid var(--line);vertical-align:top;word-break:keep-all;overflow-wrap:anywhere}}
table.pst td:first-child{{color:var(--ink2);width:46%}}
table.pst td:last-child{{font-family:var(--mono);color:var(--ink)}}
.psapplied{{margin-top:9px;font-size:11px;color:var(--ink);background:color-mix(in srgb,#2e9e68 13%,transparent);border:1px solid color-mix(in srgb,#2e9e68 26%,transparent);border-radius:6px;padding:6px 9px;line-height:1.4}}
.fullbtn{{border:1px solid var(--line);background:var(--paper);color:var(--ink2);border-radius:9px;padding:9px;font-size:12px;font-weight:600;cursor:pointer}}
.fullbtn:hover{{border-color:var(--accent);color:var(--accent)}}
table.spec{{width:100%;border-collapse:collapse;font-size:12px;margin-top:2px;table-layout:fixed}}
table.spec td{{padding:6px 9px;border-bottom:1px solid var(--line);vertical-align:top}}
table.spec td:first-child{{color:var(--ink2);width:38%;white-space:nowrap}}
table.spec td:last-child{{font-family:var(--mono);color:var(--ink);white-space:normal;word-break:keep-all;overflow-wrap:anywhere;line-height:1.4}}
.foot{{margin-top:18px;font-size:11.5px;color:var(--faint);display:flex;flex-wrap:wrap;gap:6px 16px}}
.tag2{{font-size:11px;font-weight:700;color:var(--accent);background:color-mix(in srgb,var(--accent) 14%,transparent);border-radius:6px;padding:2px 8px}}
/* 데이터시트 원본 링크 */
.dshead{{font-size:11px;font-weight:800;letter-spacing:.02em;color:var(--accent);margin-top:16px}}
.dshead span{{font-weight:500;color:var(--faint);letter-spacing:0}}
.dslist{{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}}
.dsl{{display:inline-flex;align-items:center;gap:5px;font-size:11px;font-weight:600;text-decoration:none;color:var(--accent);border:1px solid color-mix(in srgb,var(--accent) 35%,transparent);background:color-mix(in srgb,var(--accent) 8%,transparent);border-radius:7px;padding:5px 9px;line-height:1.2}}
.dsl:hover{{background:color-mix(in srgb,var(--accent) 18%,transparent)}}
.dsl.none{{color:var(--faint);border-color:var(--line);border-style:dashed;background:transparent;font-weight:500;cursor:default}}
</style>

<div class="wrap">
  <div class="hd">
    <div class="eyebrow">스키매틱 기반 시스템 이해 · 리뷰용</div>
    <h1>{META['model']} 관계도</h1>
    <p>이 문서(<b class="mono">{META['doc']}</b>) 하나로 시스템 전체를 이해합니다. <b>블록을 클릭하면 무엇과 어떤 버스로 연결되는지(관계)</b>가 강조되고, 실제 스키매틱까지 내려갑니다.</p>
    <div class="levels">
      <button class="lv cur" data-lv="0" title="중심 항목 기준 L1~L3을 한눈에"><b>L0</b> 제품</button>
      <button class="lv" data-lv="1" title="선택한 블록의 연결 관계"><b>L1</b> 관계도</button>
      <button class="lv" data-lv="2" title="선택한 블록의 상세 패널"><b>L2</b> 블록 상세</button>
      <button class="lv" data-lv="3" title="회로도 크롭 + 흐름 + 부품별 역할"><b>L3</b> 회로도·부품 상세</button>
    </div>
  </div>

  <div class="stage">
    <div class="graphcard">
      <div class="gchd">
        <div class="t">관계도 <span>· 노드=블록, 선=버스, 화살표=흐름 방향(→ 단방향 · ↔ 양방향). 클릭해 추적</span></div>
      </div>
      <div style="padding:8px 14px 0"><div class="legend">
        {''.join(f'<span class="li"><span class="sw" style="background:{BC[b]}"></span>{esc(BLAB[b])}</span>' for b in ['PWR','I2C','SPI','UART','SDIO','RF','USB','CTRL'])}
      </div></div>
      <div id="svgwrap"><svg id="g" viewBox="0 0 980 760" role="img" aria-label="{META['model']} 블록 관계도">
        <g id="edges">{edge_svg()}</g>
        <g id="nodes">{node_svg()}</g>
      </svg></div>
    </div>

    <div class="side">
      <div class="panel" id="panel"><div class="hintbox">← 블록을 클릭하면 <b>연결 관계 + 상세 + 근거 스키매틱</b>이 여기 나옵니다.</div></div>
    </div>
  </div>

  <div class="scrim" id="scrim"><div class="modal">
    <div class="mh"><b id="mtitle">회로도</b><button class="x" id="mx">✕ 닫기</button></div>
    <div class="whatis"><b>회로도(Schematic)란?</b> 부품들이 <b>전기적으로 어떻게 연결되는지</b>를 기호로 그린 <b>설계도</b>입니다 —
      거버(제조 이미지)·좌표(배치)와 달리 <b>"무엇이 무엇과 이어지는가"의 최종 근거</b>. 전체는 복잡하니, <b>이 블록에 필요한 부분만 잘라 하나씩 설명</b>해 드릴게요.</div>
    <div class="mbody2">
      <div class="cropbox"><img id="mimg" src="" alt="이 블록 회로도" title="클릭하면 회로도만 전체폭으로 크게 보기"></div>
      <div class="tutor">
        <div class="flowsec"><div class="seclab">흐름 · In → Out (회로도는 결국 흐름)</div><div class="flowdiag" id="flowdiag"></div></div>
        <div class="partsec"><div class="seclab">부품별 역할 <span style="font-weight:500;color:var(--faint);text-transform:none;letter-spacing:0">· 부품마다 스펙 요약(펼치기) + 원본 데이터시트 링크</span></div><table class="ptbl"><tbody id="ptbody"></tbody></table></div>
      </div>
    </div>
    <div class="mfoot"><button class="fullbtn" id="fullbtn">전체 회로도 시트로 보기 (엔지니어용)</button></div>
  </div></div>

  <div class="foot"><span class="tag2">스키매틱 기반</span>
    <span>출처: {META['source']}</span>
    <span>L3 근거: {META['basis']}</span>
  </div>
</div>

<script>
const B={detail_js_data()};
const GC={json.dumps(GC)}, GLAB={json.dumps(GLAB,ensure_ascii=False)}, BC={json.dumps(BC)}, BLAB={json.dumps(BLAB,ensure_ascii=False)};
const IMG={json.dumps(imgs)};
const CROPS={json.dumps(crops)};
const TUTOR={json.dumps({k:{'intro':v[0]} for k,v in TUTOR.items()},ensure_ascii=False)};
const FLOW={json.dumps(FLOW,ensure_ascii=False)};
const PARTS={json.dumps(PARTS,ensure_ascii=False)};
const DS={json.dumps(DS,ensure_ascii=False)};
const WF={json.dumps(WF,ensure_ascii=False)};
function wfHTML(id){{
  const mine=WF.filter(w=>w[1].includes(id));
  if(!mine.length) return '<div class="wfnone">이 블록만 관여하는 정의된 워크플로우 없음</div>';
  return mine.map((w,wi)=>{{
    const pos=w[1].indexOf(id)+1, tot=w[1].length;
    const chain=w[1].map(bid=>{{
      const nm=(B[bid]?B[bid].nm:bid); const cur=bid===id;
      return `<span class="wfstep${{cur?' cur':''}}" data-to="${{bid}}" style="${{cur?`border-color:${{GC[B[bid].g]}};color:var(--ink)`:''}}">${{nm}}</span>`;
    }}).join('<span class="wfar">→</span>');
    const op=false;  // 기본 전부 접힘(높이 최소화) · 클릭해 펼침
    return `<div class="wfitem">
      <button class="wfhead${{op?' on':''}}" data-w="${{wi}}"><span class="wfname">${{w[0]}}</span><span class="wfmeta">${{pos}}/${{tot}}단계<span class="wfcv">${{op?'▴':'▾'}}</span></span></button>
      <div class="wfbody" data-w="${{wi}}"${{op?'':' hidden'}}><div class="wfchain">${{chain}}</div><div class="wfdesc">${{w[2]}}</div></div>
    </div>`;
  }}).join('');
}}
const PART_DS={json.dumps([{'toks':t,'docs':i} for t,i in PART_DS],ensure_ascii=False)};
function dsHTML(id){{
  const arr=DS[id]||[];
  if(!arr.length) return '<span class="dsl none">원본 데이터시트가 공용 드라이브에 미등록</span>';
  return arr.map(d=>`<a class="dsl" href="https://drive.google.com/file/d/${{d[1]}}/view" target="_blank" rel="noopener">${{d[0]}} ↗</a>`).join('');
}}
const PART_SPEC={json.dumps([{'toks':t,'spec':s} for t,s in PART_SPEC],ensure_ascii=False)};
const nrm=s=>String(s).toUpperCase().replace(/[^A-Z0-9]/g,'');
function partSpec(refdes,value){{
  const hay=nrm(refdes)+''+nrm(value);
  for(const e of PART_SPEC){{ if(e.toks.some(t=>hay.includes(nrm(t)))) return e.spec; }}
  return null;
}}
function specCard(s){{
  const rows=a=>a.map(r=>`<tr><td>${{r[0]}}</td><td>${{r[1]}}</td></tr>`).join('');
  return `<div class="pspec">
    <div class="psname">${{s.name}}</div>
    <div class="pslab red">⚠ 절대최대정격 · 넘으면 파손 (DRBFM 1순위)</div><table class="pst"><tbody>${{rows(s.absmax)}}</tbody></table>
    <div class="pslab">동작 조건</div><table class="pst"><tbody>${{rows(s.op)}}</tbody></table>
    <div class="pslab">핵심 성능</div><table class="pst"><tbody>${{rows(s.key)}}</tbody></table>
    ${{s.applied?`<div class="psapplied"><b>✅ ${{s.applied[0]}}</b> · ${{s.applied[1]}}</div>`:''}}
  </div>`;
}}
function partDsHTML(refdes,value){{
  const hay=nrm(refdes)+''+nrm(value);
  const out=[];
  for(const e of PART_DS){{
    if(e.toks.some(t=>hay.includes(nrm(t)))){{
      if(e.docs) e.docs.forEach(d=>out.push(`<a class="pdsl" href="https://drive.google.com/file/d/${{d[1]}}/view" target="_blank" rel="noopener">${{d[0]}} ↗</a>`));
      else out.push('<span class="pdsl none">원본 없음</span>');
    }}
  }}
  return out.join('');
}}
const SHTITLE={json.dumps(META['sheet_titles'],ensure_ascii=False)};
// 흐름 In→Out 다이어그램(패널용)
function flowDiagHTML(id){{
  const f=FLOW[id]||{{in:[],core:'',out:[]}};
  if(!f.core && !(f.in||[]).length && !(f.out||[]).length) return '';
  const chip=(arr,cls)=>(arr||[]).map(x=>`<div class="fchip ${{cls}}"><b>${{x[0]}}</b><span>${{x[1]}}</span></div>`).join('');
  return `<div class="flowdiag"><div class="fcol">${{chip(f.in,'in')}}</div><div class="farr">▶</div><div class="fcore">${{f.core}}</div><div class="farr">▶</div><div class="fcol">${{chip(f.out,'out')}}</div></div>`;
}}
// 부품별 역할 표(패널용) — 스펙 요약(펼치기) + 데이터시트 링크
function partsTableHTML(id){{
  const pt=PARTS[id]||[];
  if(!pt.length) return '<div class="wfnone">추출된 부품 목록 없음</div>';
  const rows=pt.map((p,i)=>{{
    const s=partSpec(p[0],p[1]);
    const toggle=s?`<button class="specbtn" data-i="${{i}}">스펙 요약 <span class="cv">▾</span></button>`:'';
    const acts=toggle+partDsHTML(p[0],p[1]);
    const main=`<tr><td class="pr">${{p[0]}}</td><td class="pv">${{p[1]}}</td><td class="pd">${{p[2]}}${{acts?`<div class="pactions">${{acts}}</div>`:''}}</td></tr>`;
    const card=s?`<tr class="psrow" data-i="${{i}}" hidden><td colspan="3">${{specCard(s)}}</td></tr>`:'';
    return main+card;
  }}).join('');
  return `<table class="ptbl"><tbody>${{rows}}</tbody></table>`;
}}
function bindSpecToggles(){{
  document.querySelectorAll('#panel .specbtn').forEach(btn=>btn.onclick=()=>{{
    const r=document.querySelector(`#panel .psrow[data-i="${{btn.dataset.i}}"]`);
    r.hidden=!r.hidden; btn.classList.toggle('on',!r.hidden);
    btn.querySelector('.cv').textContent=r.hidden?'▾':'▴';
  }});
}}
const nodes=[...document.querySelectorAll('.gnode')], edges=[...document.querySelectorAll('.gedge')], elabs=[...document.querySelectorAll('.elab')], arrows=[...document.querySelectorAll('.garrow')];

function clear(){{nodes.forEach(n=>n.classList.remove('hot','dim'));edges.forEach(e=>e.classList.remove('hot','dim'));elabs.forEach(e=>e.classList.remove('dim'));arrows.forEach(e=>e.classList.remove('hot','dim'))}}
let selHist=[], curSel=null;
function goBack(){{ if(selHist.length){{ const prev=selHist.pop(); select(prev,true); }} }}
function select(id, fromBack){{
  const b=B[id]; if(!b)return;
  if(!fromBack && curSel && curSel!==id) selHist.push(curSel);
  curSel=id;
  const near=new Set([id]); b.conns.forEach(c=>near.add(c.to));
  clear();
  nodes.forEach(n=>{{const i=n.dataset.id; n.classList.toggle('hot',i===id); n.classList.toggle('dim',!near.has(i));}});
  edges.forEach(e=>{{const on=(e.dataset.a===id||e.dataset.b===id); e.classList.toggle('hot',on); e.classList.toggle('dim',!on);}});
  elabs.forEach(e=>{{const on=(e.dataset.a===id||e.dataset.b===id); e.classList.toggle('dim',!on);}});
  arrows.forEach(e=>{{const on=(e.dataset.a===id||e.dataset.b===id); e.classList.toggle('hot',on); e.classList.toggle('dim',!on);}});
  renderPanel(id);
  if(scrim.classList.contains('on')) openCircuit(id,false); // L4가 열려 있으면 같이 갱신(관계도는 그대로)
}}
function renderPanel(id){{
  const b=B[id]; const c=GC[b.g];
  // 직접 연결을 버스별로 묶어 컴팩트하게(버스 1줄 = 그 버스로 이어진 블록들)
  const BORD=['PWR','I2C','SPI','UART','SDIO','RF','USB','CTRL'];
  const byBus={{}}; b.conns.forEach(cn=>{{(byBus[cn.bus]=byBus[cn.bus]||[]).push(cn);}});
  const conns=BORD.filter(bus=>byBus[bus]).map(bus=>{{
    const gen=['I²C','SPI','UART','SDIO','USB','RF'];  // 버스 태그와 겹치는 라벨은 숨김
    const names=byBus[bus].map(cn=>{{const lab=(cn.lab&&!gen.includes(cn.lab))?`<span class="clab">${{cn.lab}}</span>`:'';return `<span class="conname" data-to="${{cn.to}}">${{cn.toNm}}${{lab}}</span>`;}}).join('<span class="cdot">·</span>');
    return `<div class="congrp"><span class="bus" style="background:${{BC[bus]}}">${{BLAB[bus]}}</span><span class="cnames">${{names}}</span></div>`;
  }}).join('');
  const wfn=WF.filter(w=>w[1].includes(id)).length;
  const back=selHist.length?`<button class="backbtn" title="이전 블록으로">← ${{B[selHist[selHist.length-1]].nm}}</button>`:'';
  document.getElementById('panel').innerHTML=`
    <div class="ph" style="border-top:4px solid ${{c}}">
      ${{back}}
      <span class="tag" style="background:${{c}}">${{GLAB[b.g]}}</span>
      <h3>${{b.nm}}</h3><div class="chip">${{b.chip}}</div></div>
    <div class="pb">
      <section class="psec">
        <div class="psec-h"><span class="psn">1</span>이 블록은 무엇을 하나</div>
        <div class="role">${{b.role}}</div>
      </section>
      <section class="psec">
        <div class="psec-h"><span class="psn">2</span>무엇과 연결되나 <span class="psc">직접 ${{b.conns.length}}</span></div>
        <div class="conns">${{conns}}</div>
      </section>
      <section class="psec">
        <div class="psec-h"><span class="psn">3</span>어떤 흐름에 쓰이나 <span class="psc">워크플로우 ${{wfn}}</span></div>
        <div class="wflist">${{wfHTML(id)}}</div>
      </section>
      <button class="seebtn" data-block="${{id}}" style="margin-top:14px">L3 · 회로도 + 흐름 + 부품별 역할 자세히 →</button>
    </div>`;
  {{const bb=document.querySelector('#panel .backbtn'); if(bb) bb.onclick=goBack;}}
  document.querySelectorAll('#panel .conname').forEach(el=>el.onclick=()=>select(el.dataset.to));
  document.querySelectorAll('#panel .wfhead').forEach(btn=>btn.onclick=()=>{{
    const body=document.querySelector(`#panel .wfbody[data-w="${{btn.dataset.w}}"]`);
    body.hidden=!body.hidden; btn.classList.toggle('on',!body.hidden);
    btn.querySelector('.wfcv').textContent=body.hidden?'▾':'▴';
  }});
  document.querySelectorAll('#panel .wfstep').forEach(el=>el.onclick=()=>{{if(el.dataset.to!==id)select(el.dataset.to);}});
  document.querySelector('#panel .seebtn').onclick=()=>gotoLevel(3);
}}
function selectNode(id){{
  select(id);
  const st=document.querySelector('.stage');
  // 포커스 모드(L1 그래프 / L2 패널)면 그대로 유지, 아니면 L3(스크림 열림) 또는 L1
  if(!st.classList.contains('mode-graph') && !st.classList.contains('mode-panel'))
    setLevel(scrim.classList.contains('on')?3:1);
}}
nodes.forEach(n=>{{n.onclick=()=>selectNode(n.dataset.id); n.onkeydown=(e)=>{{if(e.key==='Enter')selectNode(n.dataset.id)}};}});
document.getElementById('g').addEventListener('click',e=>{{if(e.target.id==='g'||e.target.tagName==='svg'){{showAll();}}}});

// ===== L0~L4 이정표 = 클릭 네비게이션(포커스 모드) + 현재 위치 표시 =====
const levelPills=[...document.querySelectorAll('.lv')];
const stageEl=document.querySelector('.stage');
function setLevel(n){{ levelPills.forEach(p=>p.classList.toggle('cur', +p.dataset.lv===n)); }}
function applyLayout(mode){{ // 'split' | 'graph' | 'panel'
  stageEl.classList.toggle('mode-graph', mode==='graph');
  stageEl.classList.toggle('mode-panel', mode==='panel');
}}
const CENTER='mcu'; // 가장 중심이 되는 항목(허브)
function showAll(){{ // L0 · 중심 항목(MCU) 기준으로 L1(관계도)+L2(상세)+L3(회로도·부품)을 한 화면에
  selHist=[]; curSel=CENTER;
  applyLayout('split');
  clear(); // 전체 그래프를 밝게
  renderPanel(CENTER);
  openCircuit(CENTER,false); // L3(크롭 회로도+흐름+부품)도 함께 표시
  setLevel(0);
  window.scrollTo({{top:0,behavior:'smooth'}});
}}
function gotoLevel(n){{
  if(n===0){{ showAll(); return; }}
  const id=curSel||CENTER; curSel=id;
  if(n===1){{ // L1 관계도 — 그래프를 전체폭으로 크게(패널 숨김), 선택 블록 연결 강조
    scrim.classList.remove('on'); applyLayout('graph');
    select(id);
    setLevel(1);
    document.querySelector('.graphcard').scrollIntoView({{behavior:'smooth',block:'start'}});
  }} else if(n===2){{ // L2 블록 상세 — 상세 패널 전체폭(①②③)
    scrim.classList.remove('on'); renderPanel(id); applyLayout('panel');
    setLevel(2);
    document.querySelector('.stage').scrollIntoView({{behavior:'smooth',block:'start'}});
  }} else if(n===3){{ // L3 회로도·부품 상세 — 크롭 회로도 + 흐름 + 부품별 역할
    applyLayout('split'); openCircuit(id); setLevel(3);
  }}
}}
levelPills.forEach(p=>p.onclick=()=>gotoLevel(+p.dataset.lv));

const scrim=document.getElementById('scrim');
function openCircuit(id,scroll=true){{ // L3 · 크롭 회로도 + 흐름 + 부품별 역할 (이미지 클릭 시 전체폭)
  const b=B[id]; const f=FLOW[id]||{{in:[],core:'',out:[]}}; const pt=PARTS[id]||[];
  document.querySelector('.modal').classList.remove('imgfull');
  document.getElementById('mtitle').textContent='회로도·부품 · '+b.nm;
  document.getElementById('mimg').src=CROPS[id]||IMG[b.sh];
  const chip=(arr,cls)=>(arr||[]).map(x=>`<div class="fchip ${{cls}}"><b>${{x[0]}}</b><span>${{x[1]}}</span></div>`).join('');
  document.getElementById('flowdiag').innerHTML=(f.core||(f.in||[]).length||(f.out||[]).length)?
    (`<div class="fcol">${{chip(f.in,'in')}}</div><div class="farr">▶</div><div class="fcore">${{f.core}}</div><div class="farr">▶</div><div class="fcol">${{chip(f.out,'out')}}</div>`)
    :'<div class="wfnone">정의된 In→Out 흐름 없음</div>';
  document.getElementById('ptbody').innerHTML=pt.map((p,i)=>{{
    const s=partSpec(p[0],p[1]);
    const toggle=s?`<button class="specbtn" data-i="${{i}}">스펙 요약 <span class="cv">▾</span></button>`:'';
    const acts=toggle+partDsHTML(p[0],p[1]);
    const main=`<tr><td class="pr">${{p[0]}}</td><td class="pv">${{p[1]}}</td><td class="pd">${{p[2]}}${{acts?`<div class="pactions">${{acts}}</div>`:''}}</td></tr>`;
    const card=s?`<tr class="psrow" data-i="${{i}}" hidden><td colspan="3">${{specCard(s)}}</td></tr>`:'';
    return main+card;
  }}).join('');
  document.querySelectorAll('#ptbody .specbtn').forEach(btn=>btn.onclick=()=>{{
    const r=document.querySelector(`#ptbody .psrow[data-i="${{btn.dataset.i}}"]`);
    r.hidden=!r.hidden; btn.classList.toggle('on',!r.hidden);
    btn.querySelector('.cv').textContent=r.hidden?'▾':'▴';
  }});
  const cropSrc=CROPS[id]||IMG[b.sh], fullSrc=IMG[b.sh];
  const cropTitle='회로도 · '+b.nm, fullTitle='전체 회로도 · '+SHTITLE[b.sh];
  const fb=document.getElementById('fullbtn'); let showFull=false;
  fb.textContent='전체 회로도 시트로 보기 (엔지니어용)';
  fb.onclick=()=>{{
    showFull=!showFull;
    document.getElementById('mimg').src=showFull?fullSrc:cropSrc;
    document.getElementById('mtitle').textContent=showFull?fullTitle:cropTitle;
    fb.textContent=showFull?'↩ 이 블록 부분만 보기':'전체 회로도 시트로 보기 (엔지니어용)';
  }};
  scrim.classList.add('on');
  if(scroll) requestAnimationFrame(()=>scrim.scrollIntoView({{behavior:'smooth',block:'start'}}));
}}
document.getElementById('mimg').onclick=()=>document.querySelector('.modal').classList.toggle('imgfull');
document.getElementById('mx').onclick=()=>{{scrim.classList.remove('on'); applyLayout('split'); setLevel(2); document.querySelector('.graphcard').scrollIntoView({{behavior:'smooth',block:'start'}});}};
addEventListener('keydown',e=>{{if(e.key==='Escape'){{const m=document.querySelector('.modal'); if(m.classList.contains('imgfull')){{m.classList.remove('imgfull');}} else {{scrim.classList.remove('on'); applyLayout('split'); setLevel(2);}}}}}});
// 초기: L0 · 전체 보기(아무 블록도 선택하지 않은 전체 관계도)
showAll();
</script>'''
open(OUT,'w').write(HTML)
print('blocks:',len(B),'edges:',len(E),'bytes:',len(HTML))

