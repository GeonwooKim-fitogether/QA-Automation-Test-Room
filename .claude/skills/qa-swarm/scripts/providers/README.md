# 인증 provider(제공자) 어댑터 — 계약 + 새 저장소 온보딩

> **한 줄 요지:** "인증된 QA 세션" 능력에서 **스택마다 다른 부분(로그인해서 인증 쿠키를 얻는 방법)** 만 이 폴더의 어댑터 한 파일에 가둔다. 범용 드라이버(`../mint-auth-state.mjs`)는 스택을 모른 채 이 어댑터를 불러 쓴다. 그래서 새 스택을 지원하려면 이 폴더에 `<스택이름>.mjs` 하나만 계약대로 추가하면 되고, 드라이버·검증 스크립트는 손대지 않는다.

## 왜 이렇게 나눴나

이 능력의 대부분은 스택과 무관하다. 세션 쿠키를 Playwright `storageState`(로그인 쿠키를 담아 재사용하는 저장 상태) 파일로 조립하는 일, 그 파일로 앱을 인증 상태로 여는 일은 어느 앱에서나 같다. **딱 한 가지, "테스트 유저로 로그인해서 앱이 읽을 수 있는 인증 쿠키를 얻는 방법"만 인증 스택(Supabase·Auth0·NextAuth·Rails 세션 등)마다 다르다.** 그 다른 부분만 provider 어댑터로 떼어 두면, 범용 코어는 모든 저장소가 그대로 공유하고(창고 정본으로 sync), 스택 차이는 어댑터 교체로 흡수된다.

## 어댑터가 구현할 계약

`providers/<이름>.mjs` 는 아래를 export 한다. `<이름>` 은 `QA_AUTH_PROVIDER` 환경변수로 고른다(기본 `supabase`).

### `mint({ appDir, appOrigin, env })` — 필수

테스트 유저로 로그인해 **앱이 로그인 상태로 인식하는 인증 쿠키**를 만들어 돌려준다.

- 인자
  - `appDir` — 앱 폴더 경로. 어댑터가 그 앱의 인증 라이브러리를 `node_modules` 에서 해석해 올 때 쓴다(앱이 쓰는 바로 그 코드로 쿠키를 만들어 형식이 어긋나지 않게 하기 위함).
  - `appOrigin` — 앱 주소 문자열(예: `http://localhost:3100`). 대개 어댑터는 안 써도 되고(도메인·secure 판정은 드라이버가 한다), 필요할 때만 참조.
  - `env` — `process.env`. 자격증명 등 어댑터가 필요로 하는 환경변수를 여기서 읽는다.
- 반환(객체)
  - `cookies` — `[{ name, value }]`. 앱이 읽는 인증 쿠키들. **name·value 만** 준다(도메인·만료·secure 같은 storageState 조립은 드라이버가 `appOrigin` 으로 채운다).
  - `user` — `{ email }`(선택). 로그에 "누구로 로그인됐는지"만 찍는 용도. 토큰은 반환·출력하지 않는다.
  - `expiresAt` — unix 초(선택). 세션 만료 시각. 드라이버가 여유(+7일)를 더해 쿠키 만료로 쓴다. 없으면 드라이버가 기본값을 쓴다.
- 실패는 `throw new Error("사람이 읽는 사유")` 로 알린다. 드라이버가 그 메시지를 그대로 보여 준다.

### `selftest({ appDir })` — 선택

"세션을 쿠키로 옮겨 다시 읽는" 배관이 맞는지를 **네트워크 없이** 증명한다(라이브러리 버전이 올라 쿠키 형식이 바뀌면 먼저 깨져 알려 주는 조기경보). 제공하면 `../mint-auth-state.selftest.mjs` 가 불러 실행하고, 없으면 그 스크립트는 "이 provider 엔 오프라인 자기검증이 없음"으로 건너뛴다(실패 아님). 실패 시 `throw`.

### `requiredEnv` — 선택

이 어댑터가 필요로 하는 환경변수를 사람이 읽는 문자열 배열로 둔다(안내·문서용). 예: `["QA_SUPABASE_URL — 테스트 프로젝트 URL", …]`.

## 기본 제공 어댑터

- **`supabase.mjs`** — Supabase 스택. 앱의 `@supabase/ssr` 를 그대로 불러 `signInWithPassword` → 세션을 쿠키로 직렬화한다. 오프라인 `selftest()` 포함. Supabase 관련 자격증명 env 와 1회 프로비저닝은 상위 `authenticated-session.md` 의 "Supabase provider" 절 참조.

## 새 저장소를 온보딩하려면 — 단계별 안내

> **결론 먼저:** 새 저장소가 이 능력을 쓰려면 딱 두 조각만 그 저장소가 마련하면 된다. **(1) 자기 인증 스택용 provider 어댑터 하나**(이 폴더 규약대로), **(2) 자기 앱의 실제 쓰기 흐름을 담은 쓰기 프로브 하나**(앱 저장소 안). 나머지(세션 파일 조립·인증 도달 확인·실행 골격)는 창고 정본이 sync 로 내려와 그대로 돌아간다.

Supabase 를 쓰는 저장소라면 (1)은 이미 있는 `supabase.mjs` 가 처리하므로 어댑터를 새로 쓸 필요가 없고 (2)만 마련하면 된다. Supabase 가 아닌 스택(예: Auth0·NextAuth·Rails 세션·자체 세션 쿠키)일 때만 (1)을 새로 쓴다.

### 단계 0 — 준비 확인

새 저장소가 아래를 갖췄는지 먼저 본다. 이 능력은 **인증이 걸린 웹앱**을 대상으로 하고, 브라우저 검증에 Playwright 를 쓴다.

- 앱을 **테스트(비프로덕션) 환경**으로 향하게 띄울 수 있어야 한다(프로덕션에 로그인·쓰기를 하지 않는다).
- 그 앱 폴더의 `node_modules` 에 `@playwright/test` 가 있어야 한다(범용 검증기가 그 앱 폴더에서 Playwright 를 해석해 온다).
- 테스트 유저 자격증명을 **환경변수로만** 공급할 수 있어야 한다(저장소·로그에 남기지 않는다).

### 단계 1 — provider 어댑터 작성 (Supabase 가 아닐 때만)

이 폴더에 `<스택이름>.mjs` 를 만들고 위 "어댑터가 구현할 계약"의 `mint()` 를 구현한다. 핵심은 **"테스트 유저로 로그인해서, 앱이 로그인 상태로 인식하는 인증 쿠키를 만들어 `{ cookies, user }` 로 돌려주는 것"** 하나다. 이때 쿠키를 만드는 방법은 스택에 따라 대개 아래 둘 중 하나다.

- **방식 A — 라이브러리 직렬화(가능하면 권장).** 앱이 세션을 쿠키로 굽는 데 쓰는 바로 그 라이브러리를 앱 `node_modules` 에서 불러, 로그인해서 받은 세션을 그 라이브러리로 쿠키에 직렬화한다. `supabase.mjs` 가 `@supabase/ssr` 로 하는 방식이 이것이다. 앱이 읽는 코드와 같은 코드로 쓰므로 쿠키 형식이 어긋날 수 없다는 것이 장점이다.
- **방식 B — 실제 로그인 화면 통과(어느 스택에나 통하는 만능 대안).** Playwright 로 앱의 실제 `/login` 화면을 열어 아이디·비밀번호를 넣고 로그인한 뒤, 그 브라우저 컨텍스트의 쿠키를 그대로 걷어 온다. 스택 내부를 몰라도 되지만, 로그인 폼의 선택자(입력칸·버튼)를 앱에 맞게 집어야 한다.

방식 B 로 어댑터를 쓴다면 뼈대는 이렇다(계약에 맞춰 `{ cookies, user }` 반환).

```js
// providers/<스택이름>.mjs  — 방식 B(실제 로그인 화면 통과) 예시 뼈대
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";
import path from "node:path";

export const requiredEnv = [
  "QA_TEST_EMAIL / QA_TEST_PASSWORD — 테스트 유저 자격증명",
];

export async function mint({ appDir, appOrigin, env }) {
  const email = env.QA_TEST_EMAIL, password = env.QA_TEST_PASSWORD;
  if (!email || !password) throw new Error("QA_TEST_EMAIL/QA_TEST_PASSWORD 가 없습니다.");

  // 앱 폴더에서 Playwright 를 해석해 온다(앱에 설치된 그 버전).
  const req = createRequire(path.join(appDir, "package.json"));
  const mod = await import(pathToFileURL(req.resolve("@playwright/test")).href);
  const chromium = mod.chromium ?? mod.default?.chromium;

  const browser = await chromium.launch();
  try {
    const ctx = await browser.newContext();
    const page = await ctx.newPage();
    await page.goto(`${appOrigin}/login`, { waitUntil: "networkidle" });
    // ↓ 이 세 줄만 앱의 로그인 폼에 맞게 고치면 된다.
    await page.getByLabel(/email/i).fill(email);
    await page.getByLabel(/password/i).fill(password);
    await page.getByRole("button", { name: /log ?in|sign ?in|로그인/i }).click();
    await page.waitForLoadState("networkidle");

    const jar = await ctx.cookies();               // 로그인 뒤 브라우저가 가진 쿠키 전부
    if (jar.length === 0) throw new Error("로그인 뒤 쿠키가 없습니다(폼 선택자·자격증명 확인).");
    return {
      cookies: jar.map(({ name, value }) => ({ name, value })),  // name·value 만; 조립은 드라이버가
      user: { email },
    };
  } finally {
    await browser.close();
  }
}
```

`selftest()` 는 선택이다. 방식 B 처럼 실제 네트워크 로그인에 기대는 어댑터는 오프라인 자기검증이 어려우므로 생략해도 되고, 그러면 자기검증 스크립트가 "이 provider 엔 오프라인 자기검증이 없음"으로 건너뛴다(실패 아님).

### 단계 2 — 쓰기 프로브 작성 (모든 저장소 공통, 앱 저장소 안에 둔다)

실제 쓰기 흐름(어느 화면에서 무엇을 만들어 저장하는가)은 앱마다 다르므로 **정본이 아니라 그 저장소의 앱 코드 안**에 둔다(예: `<app>/e2e/qa/write-probe.mjs`). 계약은 `authenticated-write-check.mjs` 가 부르는 아래 하나다.

```js
// <app>/e2e/qa/write-probe.mjs
export default async function probe({ page, appOrigin }) {
  // 인증된 Playwright page 로 실제 쓰기 1건을 수행하고,
  // 앱의 읽기 경로(목록 등)로 저장을 되짚어 확인한다.
  // 성공이면 정상 반환, 실패면 throw(또는 false 반환).
}
```

쓰기 프로브를 두지 않으면 검증기는 **인증 도달만** 확인하고 "쓰기 미검증"을 알린다(부분 통과). 즉 프로브는 "로그인만 되는 게 아니라 쓰기까지 되는 세션인가"를 보고 싶을 때 마련한다.

### 단계 3 — 환경변수 세팅 (실행할 때)

| 환경변수 | 뜻 | 기본값 |
|---|---|---|
| `QA_AUTH_PROVIDER` | 쓸 provider 어댑터 이름(`providers/<이름>.mjs`) | `supabase` |
| `QA_APP_DIR` | 앱 폴더(Playwright·인증 라이브러리를 여기서 해석) | 현재 폴더 |
| `QA_APP_ORIGIN` | 떠 있는 앱 주소(쿠키 도메인·secure 판정) | `http://localhost:3100` |
| `QA_STATE_OUT` | storageState 출력 경로 | `<APP_DIR>/e2e/.auth/qa-state.json` |
| `QA_WRITE_PROBE` | 쓰기 프로브 모듈 경로(선택) | 없음(인증 도달만 검증) |
| `QA_AUTH_CHECK_PATH` / `QA_LOGIN_PATH` | 인증 도달을 확인할 보호 경로 / 미인증 시 튕겨 가는 경로 | `/` · `/login` |
| (provider 별 자격증명) | 어댑터의 `requiredEnv` 참조 | 어댑터마다 다름 |

### 단계 4 — 실행

```bash
node "$SKILL/scripts/mint-auth-state.selftest.mjs"    # (있으면) 배관 자기검증
node "$SKILL/scripts/mint-auth-state.mjs"             # 세션 발급 → storageState 생성
node "$SKILL/scripts/authenticated-write-check.mjs"   # 인증 도달(+프로브 있으면 쓰기) 검증
```

프록시 뒤(사내 egress 프록시 등)에서 돌린다면 Node 가 그 프록시 CA 를 신뢰하도록 `NODE_EXTRA_CA_CERTS=<ca-bundle>` 를 함께 준다.

### 단계 5 — 됐다고 보는 기준(정의)

- `mint-auth-state.mjs` 가 exit 0 으로 storageState 파일을 만들었다(인증 발급 성공).
- `authenticated-write-check.mjs` 가 인증 도달을 확인했다(로그인 경로로 안 튕김). 쓰기 프로브를 뒀다면 그 쓰기까지 저장 확인됐다.
- 발급된 storageState 경로(`e2e/.auth/`)를 그 저장소 `.gitignore` 에 넣어 두었다(세션 토큰은 비밀이라 커밋 금지).

이 셋이 되면 그 저장소는 QA 스웜을 인증 상태로 돌릴 준비가 된 것이다. 새로 쓴 부분은 provider 어댑터(스택이 Supabase 가 아니었다면)와 쓰기 프로브뿐이고, 나머지는 창고 정본을 그대로 공유한다.
