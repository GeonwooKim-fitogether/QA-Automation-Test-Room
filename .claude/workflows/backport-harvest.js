export const meta = {
  name: 'backport-harvest',
  description: '교훈 백포트 수확 — 하위 저장소들의 docs/lessons.md를 읽어 항목을 모으고, 4분류(승인 후보/보류/폐기/추가검토) 초안을 만든다. 창고 반영은 이 워크플로가 하지 않는다 — 초안을 사람 승인 게이트에 올리는 것까지가 역할이다.',
  whenToUse: '창고 세션에서 주기적으로(또는 하위 저장소들에 lessons가 쌓였다고 판단될 때) 돌린다. args.repos로 읽을 저장소 루트 경로 배열을 넘긴다. 규약은 .claude/rules/lessons-backport.md가 정본이다.',
  phases: [
    { title: '수집', detail: '저장소별로 docs/lessons.md 항목을 구조화해 걷는다' },
    { title: '분류', detail: '전체를 한 눈에 놓고 중복을 합치고 4분류 초안을 만든다' },
  ],
}

// ── 입력 ────────────────────────────────────────────────────────────────
// args.repos (필수) 읽을 저장소 루트 경로 배열. 예: ['/home/user/Hardware-Team-System']
// args.sinceDate (선택) 'YYYY-MM-DD' — 이 날짜 이후 항목만. 기본은 전부.
const input = (typeof args === 'string' && args) ? JSON.parse(args) : (args || {})
const REPOS = input.repos || []
if (!REPOS.length) throw new Error('args.repos에 읽을 저장소 루트 경로 배열이 필요하다')
const SINCE = input.sinceDate || null

const ITEMS_SCHEMA = {
  type: 'object', required: ['repo', 'items'],
  properties: {
    repo: { type: 'string' },
    items: {
      type: 'array',
      items: {
        type: 'object', required: ['date', 'title', 'symptom', 'proposal', 'generalizable'],
        properties: {
          date: { type: 'string' }, title: { type: 'string' },
          symptom: { type: 'string' }, proposal: { type: 'string' },
          generalizable: { type: 'string' },
        },
      },
    },
  },
}

const DRAFT_SCHEMA = {
  type: 'object', required: ['approve', 'defer', 'reject', 'review'],
  properties: {
    approve: { type: 'array', items: { type: 'string' } },
    defer:   { type: 'array', items: { type: 'string' } },
    reject:  { type: 'array', items: { type: 'string' } },
    review:  { type: 'array', items: { type: 'string' } },
  },
}

phase('수집')
const collected = (await parallel(REPOS.map(repo => () =>
  agent(
    `저장소 ${repo} 의 docs/lessons.md 를 Read로 읽어라. 파일이 없으면 items를 빈 배열로 반환한다. ` +
    `있으면 각 "### 날짜 — 제목" 항목을 증상/제안/일반화 필드로 구조화해 반환한다.` +
    (SINCE ? ` ${SINCE} 이후 날짜의 항목만 포함한다.` : '') +
    ` repo 필드에는 "${repo}" 를 그대로 넣는다.`,
    { label: `수집:${repo.split('/').pop()}`, phase: '수집', schema: ITEMS_SCHEMA }
  )
))).filter(Boolean)

const all = collected.flatMap(c => c.items.map(i => ({ ...i, repo: c.repo })))
log(`${collected.length}개 저장소에서 ${all.length}건 수집`)
if (!all.length) return { collected, draft: null, note: 'lessons 항목 없음 — 분류 생략' }

phase('분류')
// 분류는 전체를 한 눈에 놓아야 중복 병합과 "N건 누적" 판정이 가능하므로 배리어가 정당하다.
const draft = await agent(
  `다음은 여러 저장소의 교훈(lessons) 항목이다:\n${JSON.stringify(all, null, 2)}\n\n` +
  `.claude/rules/lessons-backport.md 의 4분류 기준으로 초안을 만들어라. ` +
  `같은 패턴이 2개 저장소 이상 또는 2건 이상이면 승인 후보(approve), 1건뿐이면 보류(defer), ` +
  `저장소 특화나 진단 오류는 폐기(reject), 판단이 갈리면 추가검토(review). ` +
  `각 배열 원소는 "제목 — 근거 1문장 (출처: repo/날짜)" 형식의 문자열로 쓴다. 중복 항목은 하나로 합쳐 출처를 병기한다.`,
  { label: '4분류 초안', phase: '분류', schema: DRAFT_SCHEMA }
)

return { collectedCount: all.length, draft }
// 다음 단계(워크플로 밖, 사람 개입): 초안을 결정 카드/아티팩트로 게시하고,
// 승인된 항목만 창고에 항목별 커밋으로 반영한다. 자동 반영 금지.
