# drbfm-qa · `drbfm_data.py` 데이터 스키마

엔진(`drbfm_engine.py`)이 소비하는 전역. CLBX-6A→6B 실제 값은 `assets/drbfm_data.example.py` 참고.

## META
```python
META = {
 'title':  '...변경점 · DRBFM-QA List',   # 문서 제목(상단)
 'sub':    '한 줄 부제',                    # 제목 아래 설명
 'source': '기준 ... → 변경 ...',           # 푸터 출처
 'out':    'xxx-drbfm-qa.html',            # 산출물 경로
}
```

## ITEMS — 변경점(=Inquiry) 배열
항목마다 아래 구조. **CheckList 11항목 라벨은 엔진에 고정**되어 있고, 데이터는 항목별 `[Y/N, 근거]`만 채운다
(라벨을 다시 쓸 필요 없음. Y = 영향 있음/점검 필요).

```python
{
 'no': 'CLBX6B-001', 'date': '2026-05-22', 'severity': '높음',   # 높음/중간/낮음
 'title': '변경점 제목',
 'issue':  {'desc': '문제/개선 설명', 'affected': '영향 시스템·부품', 'attach': ['첨부…']},
 'cause':  {'situation': '문제상황', 'whys': ['Why1','Why2','Why3','Why4','Why5']},
 'checklist': {                       # 11항목 중 채운 것만. 라벨은 아래 고정 목록과 일치해야 표시됨.
    'SPEC변경 여부': ['Y', '근거 한 줄'],
    '재료비 변경 여부': ['N', '근거'],
    # … 나머지 항목 …
 },
 'solution': {
    'temp': {'desc':…, 'date':…, 'stock':…, 'svc':…},               # 임시 대책(선택)
    'perm': {'desc':…, 'plan':…, 'date':…, 'stock':…, 'svc':…},      # 근본 대책
    'test': {'need':'Y', 'type':'테스트 유형', 'plan':'테스트 계획'},   # 테스트 필요 여부
 },
 'change': {                          # 변경점(부품 비교)
    'asis': [['항목','값'], …],        # As Is(기준) 텍스트 비교
    'tobe': [['항목','값'], …],        # To Be(변경) 텍스트 비교
    'asis_img': focus_crop(PA, sheet, refs, mark=[…]),   # As Is 포커스 크롭(+구름). render_focus 참고
    'tobe_img': focus_crop(PB, sheet, refs, mark=[…]),   # To Be 포커스 크롭(+구름)
 },
 'drbfm': {                           # 3페이지: 관계도 + 걱정점 + 검증
    'block': 'charger',               # hardware-map 블록 id(관계도 초점)
    'graph': subgraph_svg(B,E,GC,GLAB,BC,BLAB,'charger'),  # 로드 시 계산해 넣는다
    'worries': [{'fn':'변화점·기능','fm':'잠재 고장모드','effect':'영향/전파','sev':'높음'}, …],
    'tests':   [{'item':'테스트 항목','method':'방법','pass':'합격 기준'}, …],
 },
}
```

## 3페이지 관계도 (`subgraph.subgraph_svg`)
`subgraph_svg(B, E, GC, GLAB, BC, BLAB, focus)` — hardware-map 그래프에서 `focus` 블록과 **직접 연결된
블록만** 뽑아 포커스 SVG를 만든다(하드웨어맵 좌표 재사용, 관련 영역으로 자동 확대, 변경 블록은 빨강 강조).
`B/E/GC/GLAB/BC/BLAB` 는 hardware-map 데이터(`from hw_data import …`)를 그대로 쓴다.

## 고정 CheckList 11항목 (엔진 순서)
`재료비 변경 여부` · `투자비 필요 여부` · `SVC 호환성 여부` · `불용재고 발생 여부` · `FW호환성 여부` ·
`SPEC변경 여부` · `SW 영향 여부` · `HW↔FW 영향 여부` · `사용자 시나리오 영향 여부` ·
`매뉴얼 변경 영향 여부` · `라벨 변경 영향 여부`

## 포커스 크롭 + 구름 마크 (`render_focus.focus_crop`)
`focus_crop(pdf, sheet_idx, refdes, mark=None, margin=55)` → `{'src': dataURI, 'overlay': svg}` 또는 `None`.
- `refdes`: 크롭 범위를 정하는 핵심 부품(리스트). 이들을 감싸는 최소영역 + margin 을 자른다.
- `mark`: **변경된 부품**(리스트). 그 부품 주변에 빨간 **리비전 구름(revision cloud)** 을 그려 찾기 쉽게 한다.
- 개정 간 RefDes가 다르면 As Is/To Be에 각각 다른 refdes·mark를 준다(예: 신규 부품은 To Be에서만).
- PDF가 없으면 `None` 반환 → 엔진은 그림 없이 텍스트 비교만 렌더(자체완결 유지).
