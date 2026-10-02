#!/usr/bin/env python3
# drbfm-qa · 변경점 관계도 — hardware-map 그래프(B/E/색)에서 변경 블록 + 직접 연결된 블록만
# 뽑아 포커스 SVG로 렌더(하드웨어맵 좌표 재사용, 관련 영역으로 자동 확대). 걱정 전파 범위 시각화.
import html, math
def _e(s): return html.escape(str(s))

def subgraph_svg(B, E, GC, GLAB, BC, BLAB, focus):
    foci = [focus] if isinstance(focus, str) else list(focus)
    nodes, edges = set(foci), []
    for a, b, bus, lab, d in E:
        if a in foci or b in foci:
            nodes.add(a); nodes.add(b); edges.append((a, b, bus, lab, d))
    nodes = [n for n in nodes if n in B]
    if not nodes: return ''
    P = {n: (B[n][4], B[n][5]) for n in nodes}
    xs = [p[0] for p in P.values()]; ys = [p[1] for p in P.values()]
    NW, NH, pad = 132, 46, 80
    minx, maxx = min(xs) - NW/2 - pad, max(xs) + NW/2 + pad
    miny, maxy = min(ys) - NH/2 - pad, max(ys) + NH/2 + pad
    vw, vh = maxx - minx, maxy - miny

    def arrow(mx, my, ang, c):
        h = 9
        p1 = (mx, my); p2 = (mx - h*math.cos(ang-0.5), my - h*math.sin(ang-0.5))
        p3 = (mx - h*math.cos(ang+0.5), my - h*math.sin(ang+0.5))
        return f'<polygon points="{p1[0]:.0f},{p1[1]:.0f} {p2[0]:.0f},{p2[1]:.0f} {p3[0]:.0f},{p3[1]:.0f}" fill="{c}"/>'

    ep = []
    for a, b, bus, lab, d in edges:
        ax, ay = P[a]; bx, by = P[b]; c = BC.get(bus, '#9aa7b2')
        ep.append(f'<line x1="{ax}" y1="{ay}" x2="{bx}" y2="{by}" stroke="{c}" stroke-width="2" opacity="0.8"/>')
        mx, my = (ax+bx)/2, (ay+by)/2; ang = math.atan2(by-ay, bx-ax)
        if d in ('ab', 'bi'): ep.append(arrow(mx+math.cos(ang)*8, my+math.sin(ang)*8, ang, c))
        if d in ('ba', 'bi'): ep.append(arrow(mx-math.cos(ang)*8, my-math.sin(ang)*8, ang+math.pi, c))
        if lab and lab not in ('I²C','SPI','UART','SDIO','USB','RF'):
            ep.append(f'<text x="{mx:.0f}" y="{my-6:.0f}" font-size="12" fill="{c}" text-anchor="middle" font-family="monospace">{_e(lab)}</text>')
    npart = []
    for n in nodes:
        x, y = P[n]; nm, chip, g = B[n][0], B[n][1], B[n][2]; c = GC.get(g, '#8b98a4')
        hot = n in foci
        npart.append(
            f'<g transform="translate({x-NW/2:.0f},{y-NH/2:.0f})">'
            f'<rect width="{NW}" height="{NH}" rx="9" fill="{"#fdeef1" if hot else "#fff"}" '
            f'stroke="{"#d24a68" if hot else c}" stroke-width="{3 if hot else 1.4}"/>'
            f'<text x="10" y="19" font-size="13" font-weight="800" fill="#16212c" font-family="sans-serif">{_e(nm[:14])}</text>'
            f'<text x="10" y="35" font-size="10.5" fill="#53626e" font-family="monospace">{_e(chip[:20])}</text></g>')
    # 사용된 버스 범례
    used = []
    seen = set()
    for a, b, bus, lab, d in edges:
        if bus not in seen and bus in BLAB:
            seen.add(bus); used.append(f'<span style="color:{BC.get(bus,"#888")}">■</span> {_e(BLAB[bus])}')
    legend = '<div class="dglegend">' + ' · '.join(used) + '</div>' if used else ''
    return (f'<svg viewBox="{minx:.0f} {miny:.0f} {vw:.0f} {vh:.0f}" class="dgsvg" '
            f'preserveAspectRatio="xMidYMid meet">{"".join(ep)}{"".join(npart)}</svg>{legend}')
