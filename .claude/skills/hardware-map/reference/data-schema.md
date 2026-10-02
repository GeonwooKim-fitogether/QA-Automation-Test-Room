# hardware-map · `hw_data.py` 데이터 스키마

엔진(`hardware_map_engine.py`)이 소비하는 전역들. 모든 블록 id는 `B`·`CROP`에서 동일해야 한다.
CLBX-6A 실제 값은 `assets/hw_data.example.py` 참고.

## META (제품 메타)
```python
META = {
 'model':  'CLBX-6A',                                   # 제목·탭·헤더 배지
 'title':  'CLBX-6A 시스템 이해 — 스키매틱 기반 관계도',   # <title>
 'doc':    'CLBX-6A_SCH_251110',                        # 문서 번호(헤더에 표시)
 'source': 'CLBX-6A_SCH_251110 (4시트) · PCBA1-0043 / PCB01-0037',  # 푸터 출처
 'basis':  '블록다이어그램 · 전원/MCU · GNSS/IMU 시트',    # 푸터 L3 근거
 'sheet_titles': {'block':'블록다이어그램 (Sheet 1)', 'pwr':'…', 'gnss':'…'},  # sh값→전체시트 제목
}
OUT = 'clbx6a-understand.html'   # 산출물 경로 (META['out'] 대신 OUT 전역도 가능)
imgs = json.load(open('imgs.json')); crops = json.load(open('crops.json'))
```

## B — 블록(노드)
`id: (name, chip, group, one_line_role, x, y, sheet_key)`
- `name` 화면 표기명, `chip` 대표 칩/부품, `group` GC/GLAB의 키(색·라벨),
- `x,y` SVG 좌표(viewBox `0 0 980 760`) — 허브(MCU)를 가운데, 관련 블록끼리 모아 배치,
- `sheet_key` 이 블록의 시트 그룹(= SHEETS/sheet_titles의 key). 크롭 없으면 이 시트로 폴백.
```python
B = {'mcu': ('두뇌 (MCU)','ESP32-S3-MINI-1','mcu','전체를 지휘·처리·저장…',500,375,'pwr'), …}
```

## E — 관계(엣지)
`(a, b, bus, label, direction)` · `direction ∈ {'ab','ba','bi'}` (→ a에서 b / ← b에서 a / ↔ 양방향)
- `bus` = BC/BLAB의 키(색·라벨). `label` 짧은 신호명(버스명과 겹치면 엔진이 숨김).
```python
E = [('mcu','imu','SPI','SPI','bi'), ('power','mcu','PWR','3.3V','ab'), …]
```

## WF — 워크플로우
`(name, [block ids in flow order], one_line_desc)` — 각 블록 패널 ③에 "몇/몇 단계"로 접혀 표시.
```python
WF = [('RTK 고정밀 측위', ['gnssant','gnssfe','gnss','mcu'], '안테나→LNA→수신기→두뇌'), …]
```

## PARTS — 부품별 역할 (누락 없이!)
`{block: [[refdes, value, role_desc], …]}` — `parts.json`(전 부품 자동추출)을 로드해 핵심 부품에만
풍부한 설명을 덮어쓴다. 예시의 `_RICH`(refdes→설명) + `_BROLE`(블록·접두별 기본 역할) 오버레이 패턴 사용.
```python
_auto = json.load(open('parts.json'))
_RICH = {'U3':'메인 컨트롤러 ESP32-S3 …', 'L5':'전원 페라이트 …', …}
_BROLE = {'mcu':{'C':'칩 옆 디커플링 …','R':'풀업/설정 …'}, …}
# → PARTS[b] = [[refdes, value, _RICH.get(refdes) or f'{_BROLE[b][prefix]} ({value})'], …]
```

## FLOW — In→Out 흐름(L3)
`{block: {'in':[[sig,desc],…], 'core':'대표칩', 'out':[[sig,desc],…]}}` (없는 블록은 생략 가능)

## TUTOR — 한 줄 설명(L3 상단)
`{block: (intro_string, [])}`

## 색/라벨
- `GC = {group: '#hex'}`, `GLAB = {group: '표시명'}` — 블록 그룹.
- `BC = {bus: '#hex'}`, `BLAB = {bus: '표시명'}` — 버스(범례). 표준 키: I2C/SPI/UART/SDIO/RF/USB/PWR/CTRL.
- `SPEC = {block: [[key,val],…]}` — 블록 주요 스펙(선택, `{}` 가능).

## 데이터시트 / 스펙 카드 (DRBFM DB 씨앗)
- `DS = {block: [(label, drive_file_id), …]}` — 블록 단위 원본 링크(빈 목록 = 자료 공백).
- `PART_DS = [([match_tokens], [(label, file_id),…] 또는 None), …]` — 부품 행별 원본 링크.
- `PART_SPEC = [([match_tokens], {name, absmax:[[k,v]…], op:[[k,v]…], key:[[k,v]…], applied:[label,text]}), …]`
  - 부품 행의 "스펙 요약" 카드. `absmax`=절대최대(넘으면 파손), `applied`=실제 회로 적용 정합(초록 박스).
  - 토큰이 refdes+value에 포함되면 매칭. 데이터시트 없으면 웹검색해 abs-max/동작/핵심 스펙을 채운다.
