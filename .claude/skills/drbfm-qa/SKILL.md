---
name: drbfm-qa
description: Turn a hardware design change (baseline → revised schematic) into FITogether's standard 변경점·DRBFM-QA List — a per-change worksheet (Issue / Cause Analysis with 5-Why + auto-evaluated 11-item CheckList / Solution) plus an As Is → To Be part comparison, rendered as a self-contained "CONFIDENTIAL OF FITOGETHER" HTML. Change points are drafted automatically by diffing two hardware-map outputs, then curated and enriched into closed-loop DRBFM (does the new design actually mitigate the worry?). Use when the user has a revised board/schematic (e.g. CLBX-6A → 6B) and wants a change-point summary, DRBFM review, or the CheckList (재료비·SPEC·FW/SW·HW↔FW·불용재고·SVC·라벨…) evaluated.
label_ko: 변경점·DRBFM-QA
summary_ko: 개정 전/후 스키매틱을 hardware-map으로 비교해 변경점을 자동 도출하고, FITogether 표준 양식(개선 내용 정의 3열 + 변경점 As Is/To Be, CheckList 11항목 Y/N 자동 점검)의 자체완결 DRBFM-QA HTML을 생성합니다.
---

# DRBFM-QA (변경점 검토)

Produce FITogether's standard **변경점·DRBFM-QA List** from a design change. Fixed output layout
(the `drbfm_engine.py` template — Issue / Cause Analysis / Solution + As Is/To Be, red **CONFIDENTIAL
OF FITOGETHER** header). **Do not redesign the layout** — draft change points, curate, author data, run
the engine. Builds on the `hardware-map` skill for the per-version understanding + parts.

## Design principle
```
baseline.pdf ─[hardware-map]→ parts_a.json ─┐
revised.pdf  ─[hardware-map]→ parts_b.json ─┴[diff_designs.py]→ changes.json ─[curate + enrich]→ drbfm_data.py ─[drbfm_engine.py]→ <model>-drbfm-qa.html
```

## The fixed output (per change point)
- **2. 제품 개선 내용 정의** (3 columns):
  - **Issue** — 문제점/개선점: Inquiry No · 발견 날짜 · 문제 설명 · 영향 시스템/부품 · 첨부 · 심각도(높음/중간/낮음).
  - **Cause Analysis** — 5 Why(문제상황 + Why 1~5) + **CheckList 11항목** (each Y/N + one-line 근거; Y = 영향 있음).
  - **Solution** — 임시 대책 · 근본 대책 · 테스트 필요 여부.
- **변경점 (부품 비교)** — As Is (baseline) → To Be (revised).
- **3. DRBFM (관계도 · 걱정점 · 검증)** — hardware-map 그래프에서 변경 블록 주변 관계도(걱정이 전파될
  범위) + 걱정점 전개(변화점·기능 → 잠재 고장모드 → 영향/전파 → 심각) + 검증 테스트(항목·방법·합격 기준).

## Closed-loop principle (중요)
DRBFM은 "걱정을 나열"이 아니라 **"그 걱정이 새 설계에서 실제로 회피됐는지"** 확인하는 것. 각 변경점의
근본 대책·테스트는 걱정(고장모드)을 닫는 방향으로 쓴다. 예: 만충 4.4V>셀 4.35V(과충전) → Vset 하향으로
4.3V<4.35V 확보 → 완충전압 실측으로 검증.

## Procedure

### Step 1 — 두 버전의 hardware-map 산출
`hardware-map` 스킬로 **기준(baseline)** 과 **변경(revised)** 스키매틱을 각각 처리해 `parts_a.json`,
`parts_b.json`(그리고 가능하면 블록 메타 `blocks_a.json`/`blocks_b.json` = `{block:{name,chip}}`)을 만든다.
같은 계열 개정이면 `crops_def.py`(블록 크롭)를 재사용할 수 있다.

### Step 2 — 변경점 초안 자동 도출
`python3 diff_designs.py parts_a.json parts_b.json [blocks_a.json blocks_b.json]` → `changes.json` + 요약.
블록/칩 교체, 부품 추가·삭제, 값 변경을 잡는다.

### Step 3 — 큐레이션 (반드시)
자동 diff는 **개정 간 RefDes 재번호·미실장(NC) 옵션·크롭 정렬 오차로 노이즈가 섞인다.** 스키매틱 시트
텍스트(예: "Vset 24.3K = 4.3V")와 데이터시트로 대조해 **의미 있는 변경점만** 남긴다: IC/값 교체(충전 상한,
전류제한, 인덕턴스…), 기능 추가(센서/션트), 토폴로지 변경 등. 재번호만 된 동일 부품은 제외.

### Step 4 — DRBFM-QA 작성 (`drbfm_data.py`)
`drbfm_data.example.py`(CLBX-6A→6B 실제 예시)를 참고해 변경점마다 채운다. **CheckList 11항목은 변경
성격으로 판정**한다(근거 한 줄 필수):
- `재료비 변경` Y = 부품 추가/교체(원가 영향). `SPEC변경` Y = 사양 수치/특성 변경.
- `FW호환성`·`SW 영향`·`HW↔FW 영향` Y = 펌웨어 설정/드라이버/소프트 로직에 반영 필요(예: 게이지 션트값·β).
- `불용재고` Y = 기존 보드/부품 재고를 못 쓰거나 검토 필요. `SVC 호환성` Y = 서비스/교체 동작 변화.
- `투자비`·`사용자 시나리오`·`매뉴얼`·`라벨` = 해당 시 Y. 애매하면 Y로 두고 근거에 "검토 필요"라 적는다.
`심각도`는 안전·기능 영향으로 높음/중간/낮음. `change.asis`/`change.tobe`로 부품 비교(텍스트)를 채운다.
데이터시트가 없으면 웹검색으로 정격을 확인해 근거에 반영(hardware-map의 스펙 접근과 동일).

**As Is/To Be 회로도 그림(필수)**: 블록 전체가 아니라 **변경 부위만** 타이트하게 크롭해 넣는다.
`render_focus.focus_crop(pdf, sheet_idx, [핵심 RefDes…])` 로 그 부품 주변만 잘라 data-URI를 얻고,
`change['asis_img']`(기준 PDF) / `change['tobe_img']`(변경 PDF)에 넣는다. 개정 간 RefDes가 다르면
As Is/To Be의 RefDes를 각각 지정한다(예: 신규 션트는 To Be에서만 `['U2','R9','NR2']`). PDF가 없으면
`focus_crop`이 `None`을 반환해 그림 없이 텍스트 비교만 렌더된다.
`mark=[…]` 인자로 변경 부품에 리비전 구름을 그릴 수 있으나 **기본은 끔**(도면이 지저분해질 수 있어
꼭 필요할 때만 쓴다).

**3페이지 DRBFM(관계도·걱정점·테스트)**: 변경점마다 `item['drbfm'] = {'block','worries','tests'}` 를 채운다.
- `block`: hardware-map 블록 id(변경이 속한 블록). 관계도의 초점이 된다.
- `worries`: `{'fn':'변화점·기능','fm':'잠재 고장모드','effect':'영향/전파 대상','sev':'높음/중간/낮음'}` 배열.
  변경이 연결된 블록으로 어떻게 전파되는지(관계도)를 보고 걱정을 전개한다.
- `tests`: `{'item':'테스트 항목','method':'방법','pass':'합격 기준'}` 배열. 각 걱정을 닫는 검증을 쓴다.
관계도는 hardware-map 그래프에서 자동으로 뽑는다: `from hw_data import B,E,GC,GLAB,BC,BLAB` 후
`subgraph.subgraph_svg(B,E,GC,GLAB,BC,BLAB, block)` 로 변경 블록 + 직접 연결 블록만 그린 SVG를 얻어
`item['drbfm']['graph']` 에 넣는다(예시는 `clbx_graph.example.py` 임포트). 하드웨어가 아닌 FW/SW 변경도
같은 3열 구조로 다룰 수 있다(회로도 그림 없이 텍스트 As Is/To Be + FW호환성·SW영향 체크리스트).

### Step 5 — 생성
`python3 drbfm_engine.py` → `META['out']` HTML(자체완결, 인쇄 시 항목 단위 분리).

### Step 6 — 검증 + 배포
헤드리스 브라우저로 `pageerror` 없음 · 항목/CheckList 렌더 확인. Artifact로 게시(favicon `📋`).
레포 작업이면 `prototypes/<model>-drbfm-qa.html`로 저장 후 커밋. 요약은 채팅에, 문서는 HTML에.

## Notes
- **기밀**: 산출물 상단에 "CONFIDENTIAL OF FITOGETHER" 고정. 외부 공유 주의.
- 이 문서는 **자동 초안 + 엔지니어 검토**용 — 5 Why·대책은 리뷰에서 확정한다.
- 데이터 스키마 상세는 `reference/data-schema.md`.
