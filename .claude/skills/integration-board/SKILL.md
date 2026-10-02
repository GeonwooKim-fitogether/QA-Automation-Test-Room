---
name: integration-board
description: Render a team-integration status board — a single self-contained HTML page showing whether many teams' work still fits together as one product. Fixed engine + per-project config + per-run data, so every run produces the identical layout and only the numbers change. Three bands (executive verdict, delivery status as kanban/gantt, quality baseline), a click-to-trace overlay linking a verdict card to the work items, shared assets and automated checks it depends on, and a Ground Control dark theme. Use when someone wants a cross-team/cross-part status board, an integration or program dashboard, a "what needs a decision" overview, or when /sysreport needs to publish its formal roll-up. Do NOT hand-author a new dashboard HTML — fill config + data and run the engine.
label_ko: 통합 현황판
summary_ko: 여러 팀의 일이 하나의 제품으로 맞물리는지 보여 주는 현황판 HTML을 매번 동일한 판으로 생성합니다. 종합 판정·실행 현황(칸반/간트)·품질 기준선 3단 구성에 연결 보기(카드를 누르면 얽힌 작업·공용 자산·검사만 밝아짐)를 갖췄고, 숫자와 판정은 엔진이 데이터에서 직접 계산합니다.
---

# Integration Board

여러 팀(파트·스쿼드)의 일이 **하나의 제품으로 맞물리고 있는지**를 한 장으로 보여 주는 현황판을 만든다. 산출물은 자체완결 HTML 하나이고, **매 실행 같은 판**이 나온다. 달라지는 것은 숫자뿐이다.

사람이 읽는 문서가 둘 있다. 보드를 **만들고 갱신하는** 법은 `USER-GUIDE.html`, 다 만들어진 보드를 **읽고 무엇을 결정하는지**는 `READING-GUIDE.html`이다. 보드를 사람에게 건넬 때는 뒤쪽을 함께 건넨다.

**사실을 손으로 옮기지 않아도 된다 — 자동 수집 경로가 있다.** `.claude/workflows/board-collect.mjs`가 GitHub에서 이번 회차 사실(열린 PR·충돌·멈춘 기간·검사 실패)을 모아 `board.json`을 쓰고, `.github/workflows/integration-board.yml`이 사건마다(PR 검사 완료·main 푸시·30분 심장박동) 그것을 돌려 보드를 GitHub Pages에 다시 게시한다. 규격과 켜는 법은 [`reference/board-collect.md`](reference/board-collect.md)에 있다. GitHub에 없는 사실(계획 시작·종료일, 진척률, 해소 작업)은 PR 본문에 한 줄로 선언하면 실리고, 선언하지 않으면 회색 "알 수 없다"로 남는다 — **자동 수집도 모르는 것을 지어내지 않는다.**

## 왜 이 스킬이 있나

현황판을 매번 새로 작성하면 세 가지가 무너진다. 첫째, 판이 매번 달라져 보는 사람이 매번 새로 해석해야 한다. 둘째, 숫자가 손으로 옮겨 적히다 틀린다. 셋째, 설명 자리에 그때그때의 산문이 들어가 보고서가 점점 길어진다.

그래서 이 스킬은 **화면을 만드는 일과 사실을 채우는 일을 갈라 놓는다.** 화면은 창고에 고정된 엔진이 그리고, 사람(또는 클로드)이 하는 일은 **사실을 스키마대로 채우는 것뿐**이다. 판정과 숫자는 엔진이 계산하므로 손으로 넣을 수 없고, 자유 서술 필드는 스키마에 아예 없으므로 끼워 넣을 수 없다.

## 세 겹 구조 (이 스킬의 핵심)

```
창고(고정)                     프로젝트가 소유                    매 실행
─────────────                 ─────────────                     ─────────
integration_board_engine.py    .integration/board.config.json    .integration/board.json
board_template.html            (어휘·관심사·설명·경계값)          (작업·상태·날짜·위반·검사 실행 결과)
        │                              │                                │
        └──────────────────────────────┴────────────────────────────────┘
                                       ↓
                          검증 → 계산 → 주입 → integration-board.html
```

- **엔진**은 레이아웃·색·상호작용·계산 규칙·문장 문법을 갖는다. 프로젝트별로 고치지 않는다.
- **config**는 그 프로젝트의 말(팀·제품 영역·공용 자산·자동 검사의 이름과 "뜻/걸리면" 설명)과 무엇을 상시로 지켜볼지(카드)를 담는다. 분기에 한 번 정도 바뀐다.
- **data**는 이번 회차의 사실만 담는다. 실행할 때마다 바뀐다.

## 고정 산출물 (엔진이 그리는 것 — 다시 디자인하지 말 것)

- **밴드 1 · 종합 판정** — 왼쪽에 판정 램프(위험/주의/정상)와 "결정 필요 n건", 오른쪽에 관심사 열별 카드. 카드에는 읽는 문장 한 줄과 "뜻/걸리면" 두 줄이 붙는다. 한 열에 카드가 많으면 급한 순으로 앞의 몇 장만 펼치고 나머지는 **"그 외 n건 펼치기" 버튼**으로 접는다(그 자리에서 다시 열린다).
- **밴드 2 · 실행 현황** — 같은 작업 목록을 **칸반**(상태 축)과 **간트**(시간 축)로 전환해 본다. 간트는 제품 영역 롤업 행을 눌러 펼치며, 계획·진척·지연 빗금·의존선·오늘선·목표일선을 그린다. 날짜가 없는 작업은 "일정 미정" 그룹으로 따로 모이고, 시간 축 범위 밖의 계획은 축 양끝에 "축 이전/이후 n건" 표식으로 알린다. 아래에 공용 자산 레인이 붙는다.
- **밴드 3 · 품질 기준선** — 자동 검사별 상태(통과·주의·위반, 그리고 **보고가 없으면 회색 "정보 없음"**)와 걸린 항목, 기획 정합성 막대, 이 보드가 못 보는 것을 적은 한 문단. 걸린 검사에는 **해소 작업**(그 위반을 고치는 일이 목록에 있나 — 있음 n건 / **없음** / 미보고) 줄이 위반 위에 붙고, 있으면 밴드 1 카드에서 그 작업까지 연결선이 이어진다.
- **격자 열 수는 선언한 개수를 따른다** — 관심사 열·칸반 상태·자동 검사·공용 자산이 몇 개든 그 수에 맞춰 나뉜다. 한 줄에 다 넣기에 너무 많으면 줄을 고르게 나눈다(검사 8개면 4+4).
- **연결 보기(Trace)** — 밴드 1의 카드를 누르면 그와 얽힌 작업·공용 자산·검사만 100%로 남고 나머지는 흐려지며, 세 밴드를 가로지르는 선이 그려진다. 한 번에 하나만 켜지고, 재클릭·여백 클릭·Esc로 해제된다. 간트에서 대상이 접힌 영역 안에 있으면 자동으로 펼친다.
- **테마** — 밝은 테마가 기본이고 Ground Control 다크로 토글된다.
- **고정 상태 링크** — `?theme=light|gc` · `?view=kanban|gantt` · `?trace=<카드 key>`로 특정 화면을 그대로 다시 열 수 있다(스크린샷·재게시용).

## 절대 규칙

- **판을 새로 그리지 않는다.** 매번 HTML을 작성하는 대신 config·data를 채우고 엔진을 돌린다. 시각 디자인은 확정본이다.
- **숫자를 손으로 넣지 않는다.** 판정·건수·롤업·집계는 전부 엔진이 계산한다. 스키마에 그런 필드가 없다.
- **보고되지 않은 것을 통과로 그리지 않는다.** 자동 검사는 `data.checkRuns`로 "이번 회차에 실제로 돌았다"고 보고했을 때만 초록 '통과'가 된다. 보고가 없으면 회색 '정보 없음'이다. 사실이 아닌 초록불은 보드가 없는 것보다 나쁘다.
- **산문 슬롯을 만들지 않는다.** data에 자유 서술 필드가 없다. 설명 문장은 config에 한 번 적고 재사용한다. 여분 필드를 넣으면 엔진이 거부한다.
- **자체완결.** 인라인 CSS/JS만 쓰고 외부 요청이 0이다(Jira·Artifacts에서 바로 열린다). 엔진은 파이썬 표준 라이브러리만 쓰며 외부 패키지가 없다.
- **엔진에 프로젝트 어휘를 박지 않는다.** 팀·영역·자산·검사의 이름과 설명은 전부 config에서 온다. 엔진 코드에 특정 회사의 용어가 하드코딩되면 그것은 버그다.

## 절차

### 0단계 — 이 스킬이 실제로 도는 환경을 전제한다

이 스킬을 쓰는 사람은 **터미널 앞에 있지 않다.** 클라우드에서 클로드에게 한 문장으로 시키고, 폰이나 브라우저로 결과를 받는다. 여기서 세 가지가 따라 나온다.

1. **명령은 네가 돌린다.** 사람에게 명령 줄을 알려 주고 시키지 않는다.
2. **산출 HTML의 파일 경로는 사람에게 아무 의미가 없다.** 작업 공간은 사람이 열 수 없는 곳이다. 보드는 반드시 **Artifact로 게시해 링크로** 전한다(5단계).
3. **작업 공간은 세션이 끝나면 회수된다.** 그래서 `.integration/` 설정을 **저장소에 커밋하지 않으면 다음 회차에 사라진다.** 커밋을 빠뜨리면 매번 초안 보드로 돌아가고, 이 스킬의 존재 이유인 "매 실행 같은 판"이 깨진다. 이것이 이 절차에서 가장 자주 빠뜨리는 단계다.

### 1단계 — 프로젝트에 config가 있는지 본다

`.integration/board.config.json`이 있으면 그것을 쓴다. **없으면 사람에게 스키마를 묻지 마라.** 먼저 돌아가는 보드를 띄워 화면을 보여 주고, 그 화면을 놓고 무엇을 우리 것으로 바꿀지 정한다.

```
python3 <skill>/assets/integration_board_engine.py --init --out integration-board.html
```

이 한 줄이 `.integration/`에 설정 파일 두 개를 깔고 **즉시 보드를 그린다.** 초안에는 세 밴드가 모두 채워져 있고 서로 다른 지표 카드가 여럿 있으므로, 사람은 **글로 배우는 대신 화면에서 골라** 말할 수 있다. 사람에게 물을 것은 스키마 용어가 아니라 아래 네 가지뿐이다.

1. 팀이 몇이고 이름이 무엇인가 (`teams`·작업의 `team`)
2. 제품을 어떤 구획으로 나누어 부르나 (`areas`)
3. 여러 팀이 함께 쓰는 것이 있나 (`assets`) — 없으면 그 구획은 화면에서 사라진다
4. 지금 무엇이 걱정인가 (`cards`) — 초안 카드 중 무엇을 남기고 무엇을 바꿀지로 물으면 빠르다

답을 받으면 **네가 config를 쓴다.** 사람에게 JSON을 보여 주거나 채우라고 하지 않는다. 사람은 화면을 보고 판단만 한다. 2주 스프린트처럼 리듬이 기본값과 다르면 `thresholds`의 숫자만 조인다(지연 30일·멈춤 14일·최근 7일·열당 3장이 기본).

이미 도메인이 가까운 예시가 있으면 초안 대신 그것으로 시작해도 된다.

```
mkdir -p .integration
cp <skill>/assets/board.config.web-example.json .integration/board.config.json   # 웹 서비스 팀
cp <skill>/assets/board.config.example.json     .integration/board.config.json   # 하드웨어팀(HTS)
```

두 예시는 **같은 스키마를 서로 다른 도메인으로 채운 것**이다. `board.config.web-example.json`은 사내 웹 서비스 팀(제품 영역이 결제·인증·검색·알림, 공용 자산이 API 스키마·디자인 시스템, 검사가 린트·타입체크·통합테스트)이고, `board.config.example.json`은 하드웨어팀 시스템이다. 엔진은 그 프로젝트가 무엇을 만드는지 모르므로, `areas`·`statuses`·`assets`·`checks`·`cards`의 이름과 "뜻/걸리면" 문장만 그 프로젝트의 말로 바꾸면 된다. **엔진은 건드리지 않는다.**

config를 처음 쓸 때 정할 것은 결국 하나다 — **"우리는 무엇을 상시로 지켜보는가"**(`cards`). 카드마다 어떤 지표(`metric`)로 셀지를 고르면, 그 문장과 심각도는 엔진이 매 실행 계산한다. 지표 8종과 각각이 세는 것은 `reference/board-schema.md`의 표에 있다.

우리 팀의 리듬이 기본값과 다르면 `thresholds`로 경계값만 바꾼다(지연 30일·멈춤 14일·최근 7일·열당 3장이 기본). 2주 스프린트로 도는 팀은 이 숫자를 줄여야 위험 신호가 제때 켜진다.

### 2단계 — 이번 회차 data를 만든다

`.integration/board.json`에 **사실만** 적는다. 근거는 실제 자료에서 읽는다(STATUS 문서, 열린 PR 목록, 최근 커밋, CI 결과 등). 지어내지 않는다.

```
cp <skill>/assets/board.web-example.json .integration/board.json    # 형태 참고용 (웹 서비스)
cp <skill>/assets/board.example.json     .integration/board.json    # 형태 참고용 (하드웨어팀)
```

적을 것은 작업 목록(`works`), 자동 검사에 걸린 것(`violations`), **이번 회차에 어떤 검사가 실제로 돌았는지**(`checkRuns`), 날짜(`today`·`target`) 정도다. 무엇이 필수이고 무엇이 선택인지는 `reference/board-schema.md` 2절에 있다. **판정·건수·요약문을 적으려 하지 말 것** — 그런 필드는 없고, 넣으면 검증에서 거부된다.

`checkRuns`는 선택이지만 **거의 항상 적어야 한다.** 적지 않으면 그 검사는 통과가 아니라 회색 '정보 없음'으로 그려진다. CI 결과를 아직 붙이지 못했다면 그 상태가 사실이므로 회색이 맞다.

### 3단계 — 엔진을 돌린다

```
python3 <skill>/assets/integration_board_engine.py \
  --config .integration/board.config.json \
  --data   .integration/board.json \
  --out    integration-board.html
```

인자를 모두 생략하면 번들된 예시로 데모가 나온다(엔진이 살아 있는지 확인할 때 쓴다).

검증에 걸리면 **아무 것도 만들지 않고** 오류를 전부 모아 보여 준 뒤 종료 코드 2로 끝난다. 메시지는 "어디가 · 무엇이 틀렸고 · 무엇을 쓸 수 있는지"를 함께 알려 준다. 조용히 넘어가는 일은 없으므로, 통과했다면 참조는 전부 성립한다는 뜻이다.

### 4단계 — 브라우저에서 확인한다 (필수)

헤드리스 Playwright(Chromium은 `/opt/pw-browsers`)로 산출 HTML을 열어 **페이지 오류가 0인지** 확인하고, 다음을 눌러 본다.

1. 칸반과 간트가 서로 전환되는지.
2. 밴드 1의 카드를 누르면 연결선이 그려지고 나머지가 흐려지는지, Esc로 해제되는지.
3. 간트에서 영역 행을 누르면 자식 작업이 펼쳐지는지.
4. 관심사 열에 "그 외 n건 펼치기"가 있으면 눌러 접힌 카드가 실제로 열리는지.

엔진이나 스키마를 고쳤다면 회귀 시험도 함께 돌린다(창고 저장소의 `tools/integration-board/test_validation.py`. 이 도구들은 프로젝트로 동기화되지 않고 창고에만 있다)(잘못된 입력이 막히고, 맞는 입력·예전 형식 data가 그대로 통과하는지 확인한다).

### 5단계 — 게시한다

Artifact로 올리고 링크를 준다. **갱신할 때는 같은 파일 경로로 재게시**해 링크가 바뀌지 않게 한다. 산출물이 곧 보고이므로, 채팅에는 판정과 결정 필요 항목만 짧게 적고 설명을 HTML 밖에 늘어놓지 않는다.

파일 경로를 알려 주고 "브라우저로 열어 보라"고 말하는 것은 **게시가 아니다.** 사람은 그 경로에 닿을 수 없다.

### 6단계 — 설정을 저장소에 남긴다 (빠뜨리기 쉬움)

`.integration/board.config.json`과 `.integration/board.json`을 커밋한다. 이 두 파일은 동기화 대상(`.claude/` 아래)이 아니라 **그 프로젝트가 소유하는 것**이므로, 창고에서 내려오지 않고 각 저장소가 직접 들고 있어야 한다.

커밋하지 않으면 세션 종료와 함께 사라지고, 다음 회차에 다시 초안 보드가 나온다. 그러면 판이 매번 달라져 보는 사람이 매번 새로 해석하게 되는데, 그것이 바로 이 스킬이 없애려던 문제다.

커밋해 둔 뒤부터는 사람이 **"현황판 갱신해줘"** 한마디만 하면 된다. `config`는 그대로 두고 `board.json`의 사실만 최신으로 다시 읽어 렌더하면, 같은 판 위로 새 숫자만 올라온다.

## 지표를 고르는 감각

카드는 "우리가 무엇을 걱정하는가"의 목록이다. 걱정의 종류에 따라 지표를 고른다.

- 여러 팀이 같은 것을 서로 다르게 고치고 있는가 → `asset`
- 지켜야 할 선이 실제로 지켜지는가 → `check`
- 어느 영역이 막혀 뒤가 밀리는가 → `area_stalled`
- 어느 영역이 계획보다 늦는가 → `area_delay`
- 언제 끝날지 모르는 일이 남아 있는가 → `undated`
- 최근에 무엇이 실제로 나갔는가 → `status_recent`
- 만들다 보니 기획과 벌어진 곳이 쌓이는가 → `drift`
- 위 어디에도 안 맞는 걱정거리인가 → `count` (조건을 선언하면 엔진이 작업을 센다. 숫자를 손으로 적는 것이 아니다)

`decide: true`는 "사람이 답해야 진행되는 것"에만 붙인다. 배지는 그 카드가 실제로 위험·주의일 때만 나타나므로, 평소에는 조용하다가 걸릴 때만 눈에 띈다.

## 참고

- **스키마 정본**: [`reference/board-schema.md`](reference/board-schema.md) — config·data의 모든 항목, 지표 8종, 엔진이 계산하는 것, **config로 바꿀 수 없는 것**, 검증이 막는 것.
- **번들 예시 2종**: `assets/board.config.web-example.json` + `assets/board.web-example.json`(사내 웹 서비스 팀) · `assets/board.config.example.json` + `assets/board.example.json`(하드웨어팀 시스템). 도메인만 다르고 스키마는 같다.
- **템플릿**: `assets/board_template.html` — 시각·상호작용의 확정본. 주입 지점은 `/*__BOARD_DATA__*/null` 한 곳뿐이며, CSS/JS를 손으로 고치지 않는다.
- **도구**(창고 저장소의 `tools/integration-board/`에만 있다 — 프로젝트로 동기화되지 않는다): `test_validation.py`는 잘못된 입력이 막히고 맞는 입력이 통과하는지 확인하는 회귀 시험이다 — **엔진이나 스키마를 고쳤으면 반드시 돌린다.** `build_template.py`는 **디자이너가 판을 새로 그린 시안을 가져왔을 때만** 쓴다(시안의 CSS·마크업을 손으로 옮겨 적지 않고 템플릿을 다시 파생시킨다. 기본은 미리보기 파일로 쓰고, 정본을 덮어쓰려면 `--force`가 필요하다).
- `/sysreport`(시스템층 통합 현황판 + 결정 큐)의 정식 종합은 이 스킬로 렌더한다.
