# 인증된 QA 세션 (Authenticated QA Session)

> **한 줄 요지:** QA 스웜이 로그인 화면을 손으로 통과하지 않고도 **쓰기 흐름(생성→편집→제출→승인→발효)을 인증된 상태로 자동 관통**하게 하는 능력이다. 테스트 유저의 세션을 한 번 받아 Playwright 가 그대로 물고 시작하는 저장 상태(storageState)로 만들어 두고, 그 세션이 실제로 쓰기까지 되는지 검증한다. **인증 스택(Supabase·Auth0 등)에 매이지 않도록, 스택마다 다른 부분은 provider 어댑터 한 파일로 분리했다.**

## 왜 이 능력이 있나

QA 검증에서 가장 새기 쉬운 결함은 **"화면엔 뜨는데 정작 작성이 안 되는"** 쓰기 경로의 단절이다. 이런 결함은 읽기전용 픽스처(미리 만든 가짜 데이터)만 눈으로 확인하면 절대 드러나지 않는다. 실제로 로그인해서, 실제로 한 건을 만들어 저장해 봐야 드러난다.

그런데 인증이 걸린 앱에서 "실제로 로그인해서"는 매번 사람이 로그인 화면을 통과해야 하는 부담이 된다. 그래서 QA 스웜의 환경 계약(`qa-contract.md`)은 **사람이 공급하는 유일한 입력을 "인증 셋업 스크립트" 하나**로 정의해 두었다. 이 문서와 함께 제공되는 스크립트가 바로 그 구현이다. 인증을 이 능력으로 자동화하면, L2(정상 흐름을 깊게 공격하는 층)와 권한·쓰기 검증이 비로소 돌 수 있다.

## 스택에 매이지 않는 구조 — 범용 코어 + provider 어댑터

이 능력은 대부분 스택과 무관하다. 세션 쿠키를 Playwright storageState 파일로 조립하는 일, 그 파일로 앱을 인증 상태로 여는 일은 어느 앱에서나 같다. **딱 하나, "테스트 유저로 로그인해서 앱이 읽는 인증 쿠키를 얻는 방법"만** 인증 스택마다 다르다. 그 다른 부분만 `scripts/providers/<스택>.mjs` 어댑터로 떼어 두었다.

그래서 구성은 이렇게 나뉜다.

- **범용 코어(스택 무관, 창고 정본으로 모든 저장소가 공유):**
  1. `scripts/mint-auth-state.mjs` — 세션 발급 드라이버. provider 를 골라 `mint()` 로 인증 쿠키를 받고, 그걸 Playwright storageState 파일로 조립해 저장한다.
  2. `scripts/authenticated-write-check.mjs` — 인증 도달을 확인하고(로그인 화면으로 안 튕기는지), 실제 쓰기는 저장소별 **쓰기 프로브**(플러그인)로 검증한다.
  3. `scripts/mint-auth-state.selftest.mjs` — 선택된 provider 의 오프라인 자기검증을 불러 배관이 맞는지 네트워크 없이 확인한다.
- **provider 어댑터(스택 지식, 기본 제공 + 확장 가능):** `scripts/providers/` 아래. 기본으로 `supabase.mjs` 를 제공한다. 계약은 `scripts/providers/README.md` 참조.
- **쓰기 프로브(저장소·앱 지식):** 실제 쓰기 흐름은 앱마다 다르므로 저장소가 자기 모듈을 두고 `QA_WRITE_PROBE` 로 지정한다. 없으면 인증 도달만 검증하고 "쓰기 미검증"을 알린다.

provider 는 `QA_AUTH_PROVIDER`(기본 `supabase`)로 고른다. 다른 스택(Auth0·NextAuth·Rails 세션 등)은 `README.md` 계약대로 어댑터만 새로 쓰면 나머지 코어는 그대로 쓴다.

## 실행 순서 (공통)

```bash
# 0) 공통 env — 앱 주소·폴더·출력 경로. (provider 별 자격증명은 아래 provider 절 참조.)
export QA_AUTH_PROVIDER="supabase"          # 기본값. 다른 스택이면 그 이름으로.
export QA_APP_DIR="/path/to/app"            # 인증 라이브러리·@playwright/test 를 여기서 해석
export QA_APP_ORIGIN="http://localhost:3100"
export QA_STATE_OUT="$QA_APP_DIR/e2e/.auth/qa-state.json"

# (선택) 저장소별 실제 쓰기 흐름을 검증하려면 쓰기 프로브를 지정한다.
export QA_WRITE_PROBE="$QA_APP_DIR/e2e/qa/write-probe.mjs"
# (선택) 인증 도달 확인 경로/로그인 경로가 기본과 다르면 지정.
export QA_AUTH_CHECK_PATH="/"     # 보호된 경로
export QA_LOGIN_PATH="/login"     # 미인증 시 튕겨 가는 경로

# 1) 배관 자기검증(오프라인) → 2) 세션 발급 → 3) 인증(+쓰기) 검증
node "$SKILL/scripts/mint-auth-state.selftest.mjs"
node "$SKILL/scripts/mint-auth-state.mjs"            # → e2e/.auth/qa-state.json 생성
node "$SKILL/scripts/authenticated-write-check.mjs"  # → 인증 도달(+프로브 있으면 쓰기) 검증
```

프록시 뒤(사내 egress 프록시 등)에서 돌린다면 Node 가 그 프록시 CA 를 신뢰해야 하므로 `NODE_EXTRA_CA_CERTS=<ca-bundle>` 를 함께 준다.

## 기본 provider — Supabase

`QA_AUTH_PROVIDER=supabase`(기본). 앱이 실제로 쓰는 `@supabase/ssr` 라이브러리를 그대로 불러 `signInWithPassword` → 세션을 쿠키로 직렬화하므로, 쿠키 이름·인코딩·청킹을 손으로 흉내 낼 필요가 없다(형식이 어긋날 수 없다). 필요한 자격증명 env:

```bash
export QA_SUPABASE_URL="https://<test-ref>.supabase.co"       # 없으면 NEXT_PUBLIC_SUPABASE_URL
export QA_SUPABASE_ANON_KEY="<test anon(publishable) key>"    # 없으면 NEXT_PUBLIC_SUPABASE_ANON_KEY, 공개 키
export QA_TEST_EMAIL="<test user email>"
export QA_TEST_PASSWORD="<test user password>"
```

### 1회 프로비저닝 — 테스트 유저를 테스트 프로젝트에 만든다

로그인하려면 먼저 그 유저가 있어야 한다. **테스트(비프로덕션) 프로젝트에서만** 만든다. 권장은 Admin API 다(GoTrue 버전에 견고하고, 비밀번호 해싱·이메일 확인·identity 생성을 서버가 알아서 처리한다).

```bash
# 서비스 롤 키는 env 로만 전달 — 저장소·로그·문서에 절대 남기지 않는다.
curl -sS -X POST "$QA_SUPABASE_URL/auth/v1/admin/users" \
  -H "apikey: $SUPABASE_SERVICE_ROLE_KEY" \
  -H "Authorization: Bearer $SUPABASE_SERVICE_ROLE_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$QA_TEST_EMAIL\",\"password\":\"$QA_TEST_PASSWORD\",\"email_confirm\":true}"
```

- `email_confirm:true` 로 만들어야 곧바로 비밀번호 로그인이 된다(이메일 확인 대기 없이).
- 이미 있으면 422/409 가 나는데, 그건 "이미 준비됨"이라 무시해도 된다.
- 유저가 만들어지면 대개 프로젝트의 가입 트리거가 프로필(역할 등)을 자동 생성한다. 이 프로필이 쓰기 권한을 좌우한다 — 아래 "권한 모델" 참조.

**이메일 도메인 제한이 있는 프로젝트라면** 그 도메인에 맞는 주소를 써야 한다. 일부 프로젝트는 가입 트리거가 사내 도메인(예: 회사 메일)만 프로필 생성을 허용하고 나머지는 거부한다. 그런 경우 아무 이메일이나 쓰면 유저는 만들어져도 프로필이 없어 쓰기가 막힌다.

### 권한 모델 — 테스트 유저가 "쓰기까지" 되려면

로그인이 되는 것과 쓰기가 되는 것은 다르다. 쓰기는 데이터베이스의 RLS(행 수준 보안, 각 행에 누가 접근·수정할 수 있는지를 DB 가 직접 통제하는 기능)가 최종 판정한다. 그래서 세 가지가 모두 맞아야 한다.

1. **유저 존재 + 이메일 확인.** 위 프로비저닝으로 충족.
2. **프로필(역할) 존재.** 가입 트리거가 유저마다 프로필 행을 만든다. 도메인 제한 트리거가 있으면 허용 도메인 주소여야 프로필이 생긴다.
3. **역할이 쓰기 등급.** 프로젝트의 쓰기 판정 함수(예: `is_editor()`)가 그 역할을 통과시켜야 한다. 신규 가입 기본 역할이 이미 쓰기 등급(예: engineer)이면 별도 승격이 필요 없고, 기본이 읽기전용(viewer)이면 admin 이 그 프로필을 쓰기 역할로 올려 줘야 한다.

즉 확인 순서는 이렇다 — 먼저 로그인이 되는지(1·2), 그다음 쓰기 프로브가 통과하는지(3). 쓰기가 막히면 대개 원인은 프로필이 없거나(도메인 제한) 역할이 viewer 인 경우다.

## 다른 스택이라면

`scripts/providers/README.md` 의 계약대로 `scripts/providers/<스택>.mjs` 를 만들고 `QA_AUTH_PROVIDER` 로 고른다. 어댑터는 "테스트 유저 로그인 → 앱이 읽는 인증 쿠키(`{ cookies, user }`) 반환"만 책임지고, storageState 조립·인증 도달·쓰기 프로브는 범용 코어가 그대로 처리한다.

## 쓰기 프로브 — 저장소가 자기 쓰기 흐름을 담는다

실제 쓰기 흐름(어느 화면에서 무엇을 만들어 저장하는가)은 앱마다 다르므로 저장소가 모듈로 둔다. 계약은 간단하다.

```js
// 예: <app>/e2e/qa/write-probe.mjs
export default async function probe({ page, appOrigin }) {
  // 인증된 Playwright page 로 실제 쓰기 1건을 수행하고,
  // 앱의 읽기 경로(목록 등)로 저장을 되짚어 확인한다.
  // 성공이면 정상 반환, 실패면 throw(또는 false 반환).
}
```

`QA_WRITE_PROBE` 로 이 모듈 경로를 지정하면 `authenticated-write-check.mjs` 가 인증 도달 확인 뒤 이 프로브를 실행한다. 지정하지 않으면 인증 도달만 검증하고 "쓰기 미검증"을 분명히 알린다(부분 통과).

## 반드시 지킬 안전 규칙

- **테스트(비프로덕션) 프로젝트에서만.** 프로덕션 프로젝트에는 절대 유저를 만들거나 쓰기를 하지 않는다.
- **비밀은 env 로만.** 서비스 롤 키·공개 키·테스트 비밀번호를 저장소·문서·로그에 커밋하지 않는다. 이 문서에도 실제 값이 아니라 "이 env 를 설정하라"만 적혀 있다.
- **발급된 `storageState` 는 비밀이다.** 실제 세션 토큰을 담고 있으므로 커밋하지 않는다. 기본 출력 경로(`e2e/.auth/`)를 `.gitignore` 에 넣어 둔다.
