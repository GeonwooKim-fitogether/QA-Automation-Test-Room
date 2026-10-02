#!/usr/bin/env python3
# drbfm-qa ENGINE — 고정 템플릿(FITogether 변경점/DRBFM-QA 양식). 편집 금지.
# 입력: 같은 폴더의 drbfm_data.py (META + ITEMS). 실행: python3 drbfm_engine.py → META['out'] HTML.
import html, json
from drbfm_data import META, ITEMS

# 고정 CheckList 11항목(FITogether 양식). 데이터는 항목별 [Y/N, 근거]만 채운다.
CHECKLIST = ['재료비 변경 여부','투자비 필요 여부','SVC 호환성 여부','불용재고 발생 여부',
             'FW호환성 여부','SPEC변경 여부','SW 영향 여부','HW↔FW 영향 여부',
             '사용자 시나리오 영향 여부','매뉴얼 변경 영향 여부','라벨 변경 영향 여부']
def e(s): return html.escape(str(s if s is not None else ''))
SEV = {'높음':'#d24a68','중간':'#d99320','낮음':'#2e9e68'}

def kv(label, val):
    return f'<div class="kv"><b>{e(label)}</b> {e(val)}</div>' if val else ''

def checklist_html(cl):
    rows=[]
    yn_count=0
    for lab in CHECKLIST:
        v = cl.get(lab)
        yn = (v[0] if v else '').upper()
        why = v[1] if v and len(v)>1 else ''
        if yn=='Y': yn_count+=1
        cls = 'y' if yn=='Y' else ('n' if yn=='N' else 'u')
        badge = {'y':'Y','n':'N'}.get(cls,'—')
        rows.append(f'<li class="cli {cls}"><span class="ck">{badge}</span>'
                    f'<span class="cl-lab">{e(lab)}</span>'
                    f'{f"<span class=cl-why>{e(why)}</span>" if why else ""}</li>')
    return f'<ul class="cklist">{"".join(rows)}</ul>', yn_count

def sol_block(title, d, keys):
    if not d: return ''
    inner=''.join(kv(k, d.get(k2)) for k,k2 in keys if d.get(k2))
    return f'<div class="solb"><div class="solh">{e(title)}</div>{inner}</div>' if inner else ''

def compare_html(ch):
    def col(rows):
        return ''.join(f'<div class="cmpr"><span class="cmpk">{e(k)}</span>'
                       f'<span class="cmpv">{e(v)}</span></div>' for k,v in (rows or []))
    def img(o):
        if not o: return ''
        src = o['src'] if isinstance(o, dict) else o
        ov = o.get('overlay', '') if isinstance(o, dict) else ''
        return f'<div class="cmpimg"><img src="{src}" alt="회로도">{ov}</div>'
    return (f'<div class="cmpcol asis"><div class="cmph">As Is</div>{img(ch.get("asis_img"))}{col(ch.get("asis"))}</div>'
            f'<div class="cmpcol tobe"><div class="cmph">To Be</div>{img(ch.get("tobe_img"))}{col(ch.get("tobe"))}</div>')

def drbfm_html(it):
    d = it.get('drbfm')
    if not d: return ''
    graph = d.get('graph', '')
    worries = d.get('worries', []); tests = d.get('tests', [])
    # 걱정 → 검증 테스트 역참조(W#를 검증하는 T#)
    w2t = {}
    for ti, t in enumerate(tests):
        for wn in t.get('covers', []):
            w2t.setdefault(wn, []).append(ti + 1)
    wr = ''
    for wi, w in enumerate(worries):
        wn = wi + 1
        cover = ' '.join(f'<span class="lnk t">T{n}</span>' for n in w2t.get(wn, [])) or '<span class="none">—</span>'
        wr += (f'<tr><td class="wno"><span class="lnk w">W{wn}</span></td><td>{e(w.get("fn"))}</td>'
               f'<td>{e(w.get("fm"))}</td><td>{e(w.get("effect"))}</td>'
               f'<td><span class="wsev" style="background:{SEV.get(w.get("sev"),"#8b98a4")}">{e(w.get("sev","—"))}</span></td>'
               f'<td class="wcov">{cover}</td></tr>')
    tc = ''
    for ti, t in enumerate(tests):
        covs = ' '.join(f'<span class="lnk w">W{n}</span>' for n in t.get('covers', [])) or '<span class="none">—</span>'
        rows = ''
        for lab, key in [('조건', 'cond'), ('프로토콜', 'method'), ('합격 기준', 'pass'), ('샘플·반복', 'sample')]:
            if t.get(key):
                rows += f'<div class="trow"><span class="tlab">{lab}</span><span class="tval">{e(t[key])}</span></div>'
        tc += (f'<div class="tcard"><div class="tch"><span class="lnk t">T{ti+1}</span>'
               f'<span class="tit">{e(t.get("item"))}</span>'
               f'<span class="tcovs">검증 걱정 {covs}</span></div>{rows}</div>')
    return f'''
  <div class="secttl">3. DRBFM — 관계도 · 걱정점 전개 · 검증 테스트</div>
  <div class="drbfm">
    <div class="dgraph">{graph}<div class="dgcap">변경 블록(<b style="color:#d24a68">빨강</b>)과 직접 연결된 블록 — 걱정이 전파될 수 있는 범위</div></div>
    <div class="dblk"><div class="colsub">걱정점 전개 <span class="psc">{len(worries)}</span> <span class="subnote">· 맨 오른쪽 = 이 걱정을 닫는 검증</span></div>
      <table class="dtbl"><colgroup><col style="width:40px"><col style="width:20%"><col style="width:29%"><col style="width:27%"><col style="width:52px"><col style="width:56px"></colgroup>
      <thead><tr><th>#</th><th>변화점·기능</th><th>걱정(잠재 고장모드)</th><th>영향(전파)</th><th>심각</th><th>검증</th></tr></thead><tbody>{wr}</tbody></table></div>
    <div class="dblk"><div class="colsub">검증 테스트 <span class="psc">{len(tests)}</span> <span class="subnote">· 각 테스트가 검증하는 걱정(W#)을 표시</span></div>
      <div class="tcards">{tc}</div></div>
  </div>'''

def item_html(it, idx):
    iss, cau, sol = it.get('issue',{}), it.get('cause',{}), it.get('solution',{})
    cl_html, ycnt = checklist_html(it.get('checklist',{}))
    whys = ''.join(f'<div class="why"><b>Why {i+1}</b> {e(w)}</div>' for i,w in enumerate(cau.get('whys',[])))
    attach = iss.get('attach') or []
    sev = it.get('severity','')
    return f'''<section class="item">
  <div class="ihd"><span class="ino">{e(it.get('no',''))}</span>
    <h2>{e(it.get('title',''))}</h2>
    <span class="sev" style="background:{SEV.get(sev,'#8b98a4')}">{e(sev or '—')}</span>
    <span class="date">{e(it.get('date',''))}</span></div>

  <div class="secttl">{idx}. 제품 개선 내용 정의</div>
  <div class="grid3">
    <div class="col"><div class="colh">Issue</div>
      <div class="colsub">문제점 / 개선점</div>
      {kv('Inquiry No', it.get('no'))}
      {kv('발견 날짜', it.get('date'))}
      {kv('문제 설명', iss.get('desc'))}
      {kv('영향 시스템·부품', iss.get('affected'))}
      {f'<div class="kv"><b>첨부</b> '+' · '.join(e(a) for a in attach)+'</div>' if attach else ''}
      <div class="kv"><b>심각도</b> <span class="sevtxt" style="color:{SEV.get(sev,'#8b98a4')}">{e(sev or '—')}</span></div>
    </div>
    <div class="col"><div class="colh">Cause Analysis</div>
      <div class="colsub">5 Why 분석</div>
      {kv('문제상황', cau.get('situation'))}
      {whys}
      <div class="colsub" style="margin-top:12px">CheckList <span class="ycnt">Y {ycnt}/{len(CHECKLIST)}</span></div>
      {cl_html}
    </div>
    <div class="col"><div class="colh">Solution</div>
      {sol_block('임시 대책', sol.get('temp'), [('대책 설명','desc'),('적용 일자','date'),('재고품 운용','stock'),('SVC 방법','svc')])}
      {sol_block('근본 대책', sol.get('perm'), [('대책 설명','desc'),('적용 계획','plan'),('적용 일자','date'),('재고품 운용','stock'),('SVC 방법','svc')])}
      {sol_block('테스트 필요 여부', sol.get('test'), [('필요','need'),('테스트 유형','type'),('테스트 계획','plan')])}
    </div>
  </div>

  <div class="secttl">변경점 (부품 비교)</div>
  <div class="cmp">{compare_html(it.get('change',{}))}</div>
{drbfm_html(it)}
</section>'''

items = ''.join(item_html(it, 2) for it in ITEMS)
CSS = '''
*{box-sizing:border-box} body{margin:0;background:#eef1f5;color:#16212c;font:14px -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;line-height:1.5}
.wrap{max-width:1240px;margin:0 auto;padding:22px 18px 60px}
.top{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;margin-bottom:8px}
.top h1{font-size:22px;font-weight:800;margin:0}
.top .sub{color:#53626e;font-size:13px;margin-top:4px}
.conf{border:2px solid #d24a68;color:#d24a68;font-weight:800;font-size:13px;padding:5px 12px;border-radius:4px;white-space:nowrap;letter-spacing:.02em}
.legend{display:flex;gap:12px;flex-wrap:wrap;font-size:11.5px;color:#53626e;margin:8px 0 18px}
.item{background:#fff;border:1px solid #dce2e8;border-radius:12px;box-shadow:0 1px 2px rgba(22,33,44,.05),0 8px 22px rgba(22,33,44,.06);padding:16px 18px;margin-bottom:22px}
.ihd{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:12px}
.ihd .ino{font-family:ui-monospace,Menlo,monospace;font-size:11px;font-weight:700;color:#53626e;background:#eef1f5;border-radius:5px;padding:2px 8px}
.ihd h2{font-size:16px;font-weight:800;margin:0;flex:1;min-width:220px}
.ihd .sev{font-size:10.5px;font-weight:800;color:#fff;padding:2px 9px;border-radius:6px}
.ihd .date{font-size:11px;color:#8b98a4;font-family:ui-monospace,monospace}
.secttl{font-size:12px;font-weight:800;color:#16212c;background:#e6eaef;border:1px solid #dce2e8;border-radius:6px;padding:6px 11px;margin:14px 0 10px}
.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:0;border:1px solid #dce2e8;border-radius:8px;overflow:hidden}
@media(max-width:900px){.grid3{grid-template-columns:1fr}}
.col{padding:12px 14px;border-left:1px solid #dce2e8}
.col:first-child{border-left:0}
.colh{font-size:12px;font-weight:800;text-align:center;color:#16212c;background:#f4f6f8;margin:-12px -14px 10px;padding:7px;border-bottom:1px solid #dce2e8}
.colsub{font-size:11px;font-weight:800;color:#53626e;margin:8px 0 5px;letter-spacing:.02em}
.kv{font-size:12px;line-height:1.5;margin:3px 0;word-break:keep-all}
.kv b{color:#16212c;font-weight:700}
.why{font-size:12px;margin:2px 0;color:#16212c} .why b{color:#d24a68;font-family:ui-monospace,monospace;font-size:11px;margin-right:4px}
.cklist{list-style:none;margin:4px 0 0;padding:0;display:flex;flex-direction:column;gap:3px}
.cli{display:flex;align-items:flex-start;gap:7px;font-size:11.5px}
.cli .ck{flex:none;width:17px;height:17px;border-radius:5px;font-size:10px;font-weight:800;color:#fff;display:flex;align-items:center;justify-content:center}
.cli.y .ck{background:#d24a68} .cli.n .ck{background:#9aa7b2} .cli.u .ck{background:#cbd3da}
.cli.y .cl-lab{font-weight:700}
.cl-lab{color:#16212c} .cl-why{color:#8b98a4;margin-left:5px}
.ycnt{font-family:ui-monospace,monospace;font-weight:700;color:#d24a68;font-size:10.5px;margin-left:4px}
.solb{border:1px solid #dce2e8;border-radius:7px;padding:8px 10px;margin-bottom:8px}
.solh{font-size:11.5px;font-weight:800;color:#16212c;margin-bottom:5px}
.cmp{display:grid;grid-template-columns:1fr 1fr;gap:0;border:1px solid #dce2e8;border-radius:8px;overflow:hidden}
.cmpcol{padding:10px 12px} .cmpcol.tobe{border-left:1px solid #dce2e8;background:#fbfcfd}
.cmph{font-size:12px;font-weight:800;text-align:center;padding:6px;margin:-10px -12px 8px;border-bottom:1px solid #dce2e8;background:#f4f6f8}
.cmpcol.tobe .cmph{background:#fdeef1;color:#d24a68}
.cmpimg{position:relative;background:#fff;border:1px solid #dce2e8;border-radius:6px;margin-bottom:8px;overflow:hidden;line-height:0}
.cmpimg img{display:block;width:100%;height:auto}
.cmpimg .cloud{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}
.cmpr{display:flex;gap:8px;font-size:12px;padding:3px 0;border-bottom:1px dotted #e6eaef}
.cmpk{flex:none;width:44%;color:#53626e;font-weight:600;word-break:keep-all}
.cmpv{font-family:ui-monospace,monospace;color:#16212c;word-break:break-all}
.drbfm{border:1px solid #dce2e8;border-radius:8px;padding:12px 14px}
.dgraph{background:#fbfcfd;border:1px solid #e6eaef;border-radius:8px;padding:10px;margin-bottom:12px}
.dgsvg{width:100%;height:auto;max-height:340px;display:block}
.dgcap{font-size:11px;color:#8b98a4;text-align:center;margin-top:6px}
.dglegend{font-size:11px;color:#53626e;text-align:center;margin-top:4px}
.dblk{margin-top:10px}
.dtbl{width:100%;border-collapse:collapse;font-size:11.5px;table-layout:fixed}
.dtbl th{background:#f4f6f8;color:#53626e;font-size:10.5px;font-weight:800;text-align:left;padding:5px 8px;border-bottom:1px solid #dce2e8}
.dtbl td{padding:5px 8px;border-bottom:1px solid #eef1f5;vertical-align:top;word-break:keep-all;overflow-wrap:break-word;line-height:1.45}
.dtbl .tno{font-family:ui-monospace,monospace;color:#8b98a4;width:34px}
.dtbl thead th:first-child{width:34px}
.wsev{font-size:9.5px;font-weight:800;color:#fff;padding:1px 7px;border-radius:5px;white-space:nowrap}
.subnote{font-weight:500;color:var(--faint);font-size:10px}
.lnk{display:inline-block;font-family:ui-monospace,monospace;font-size:10px;font-weight:800;padding:1px 6px;border-radius:5px;line-height:1.5;white-space:nowrap}
.lnk.w{background:#fdeef1;color:#d24a68;border:1px solid #f3c6d1}
.lnk.t{background:#e9f0fa;color:#2f6fc4;border:1px solid #c7dbf1}
.wcov{white-space:normal}
.none{color:#cbd3da;font-size:11px}
.tcards{display:grid;grid-template-columns:1fr 1fr;gap:8px}
@media(max-width:820px){.tcards{grid-template-columns:1fr}}
.tcard{border:1px solid #dce2e8;border-radius:8px;padding:8px 10px;background:#fbfcfd}
.tch{display:flex;align-items:center;gap:7px;flex-wrap:wrap;margin-bottom:5px}
.tch .tit{font-size:12px;font-weight:800;color:#16212c}
.tcovs{font-size:10px;color:#8b98a4;margin-left:auto}
.trow{display:flex;gap:7px;font-size:11.5px;line-height:1.5;padding:1px 0}
.trow .tlab{flex:none;width:56px;color:#53626e;font-weight:700}
.trow .tval{color:#16212c;word-break:keep-all;overflow-wrap:break-word}
.foot{margin-top:16px;font-size:11px;color:#8b98a4}
@media print{body{background:#fff} .item{box-shadow:none;break-inside:avoid}}
'''
HTML = f'''<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(META.get('title','변경점 · DRBFM-QA'))}</title><style>{CSS}</style></head><body>
<div class="wrap">
  <div class="top">
    <div><h1>{e(META.get('title',''))}</h1><div class="sub">{e(META.get('sub',''))}</div></div>
    <div class="conf">CONFIDENTIAL OF FITOGETHER</div>
  </div>
  <div class="legend"><span>변경점 {len(ITEMS)}건</span><span>· CheckList Y = 영향 있음(점검 필요)</span>
    <span>· As Is=이전(기준) / To Be=변경(신규)</span></div>
  {items}
  <div class="foot">{e(META.get('source',''))} · FITogether 하드웨어팀 · DRBFM-QA (자동 초안 + 엔지니어 검토)</div>
</div></body></html>'''
open(META.get('out','drbfm-qa.html'), 'w', encoding='utf-8').write(HTML)
print('items:', len(ITEMS), '| bytes:', len(HTML), '| out:', META.get('out'))
