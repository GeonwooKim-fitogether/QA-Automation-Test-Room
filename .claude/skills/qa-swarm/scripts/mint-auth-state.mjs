#!/usr/bin/env node
// ─────────────────────────────────────────────────────────────────────────
// 인증된 QA 세션 발급기 (범용 드라이버) — 테스트 유저로 로그인해 Playwright
// storageState 를 만든다.
//
// 무엇을/왜: QA 스웜의 L2(정상 흐름을 깊게 공격)와 권한·쓰기 검증은 "로그인된 상태"
//   에서만 돈다. 이 스크립트는 사람이 매번 로그인 화면을 통과하지 않아도 되도록,
//   테스트 유저의 세션을 한 번 받아 **Playwright 가 그대로 물고 시작하는 storageState
//   파일**(로그인 쿠키를 담아 재사용하는 저장 상태)로 떨궈 둔다. qa-contract.md 가
//   "사람이 공급하는 유일한 것 = 인증 셋업 스크립트"라 부르는 그 스크립트의 구현이다.
//
// 어떻게(스택 무관): 이 드라이버는 특정 인증 스택을 모른다. 스택 지식은 provider(제공자)
//   어댑터 한 파일(providers/<이름>.mjs)에만 있다. 드라이버는 그 어댑터의 mint() 를
//   불러 '앱이 읽을 수 있는 인증 쿠키'를 받고, 그것을 Playwright storageState 로 조립해
//   저장한다. 즉 "로그인→쿠키"는 provider 가, "쿠키→storageState 파일"은 드라이버가 맡는다.
//
// provider 선택:
//   QA_AUTH_PROVIDER  (기본 supabase)  providers/<이름>.mjs 를 고른다.
//     기본 제공: supabase. 다른 스택이면 providers/README.md 계약대로 새 어댑터를 추가.
//
// 공통 환경변수 (비밀은 env 로만 — 저장소·로그에 남기지 않는다):
//   QA_APP_ORIGIN   (기본 http://localhost:3100)  쿠키를 심을 앱 주소(도메인·secure 판정)
//   QA_APP_DIR      (기본 cwd)                     provider 가 앱 라이브러리를 해석해 올 폴더
//   QA_STATE_OUT    (기본 <QA_APP_DIR>/e2e/.auth/qa-state.json)  storageState 출력 경로
//   (그 밖에 provider 별 자격증명 env — 예: supabase 는 QA_SUPABASE_URL 등. 어댑터 참조.)
//
// 출력: storageState 파일을 쓰고, 인증된 사용자·쿠키 이름만 출력한다(토큰 미출력).
// 실패: provider 미해석·자격증명 미설정·로그인 거부 등은 사유를 찍고 exit 1.
//
// ⚠ 산출된 storageState 는 실제 세션 토큰을 담은 **비밀**이다. 커밋 금지(.auth/ gitignore).
// ─────────────────────────────────────────────────────────────────────────
import { pathToFileURL } from "node:url";
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";

function fail(msg) { console.error(`[mint-auth-state] ✗ ${msg}`); process.exit(1); }

const PROVIDER = process.env.QA_AUTH_PROVIDER || "supabase";
const APP_ORIGIN = process.env.QA_APP_ORIGIN || "http://localhost:3100";
const APP_DIR = process.env.QA_APP_DIR || process.cwd();
const OUT = process.env.QA_STATE_OUT || path.join(APP_DIR, "e2e", ".auth", "qa-state.json");

// provider 어댑터를 이 스크립트 폴더 기준으로 해석해 온다.
let provider;
try {
  const url = new URL(`./providers/${PROVIDER}.mjs`, import.meta.url);
  provider = await import(url.href);
} catch (e) {
  fail(`인증 provider '${PROVIDER}' 를 불러오지 못했습니다: providers/${PROVIDER}.mjs 가 있는지 확인하세요. (${e.message})`);
}
if (typeof provider.mint !== "function") fail(`provider '${PROVIDER}' 에 mint() 가 없습니다(providers/README.md 계약 참조).`);

// provider 가 스택 지식으로 '로그인→인증 쿠키'를 만든다.
let result;
try {
  result = await provider.mint({ appDir: APP_DIR, appOrigin: APP_ORIGIN, env: process.env });
} catch (e) {
  fail(e.message || String(e));
}
if (!result?.cookies?.length) fail("provider 가 인증 쿠키를 돌려주지 않았습니다.");

// 드라이버가 '쿠키→Playwright storageState'를 조립한다(스택 무관). 도메인·secure 는 앱 주소에서 판정.
const origin = new URL(APP_ORIGIN);
const secure = origin.protocol === "https:";
const expires = (result.expiresAt ?? Math.floor(Date.now() / 1000) + 3600) + 7 * 24 * 3600;
const cookies = result.cookies.map(({ name, value }) => ({
  name, value,
  domain: origin.hostname,
  path: "/",
  expires,
  httpOnly: true,
  secure,
  sameSite: "Lax",
}));

mkdirSync(path.dirname(OUT), { recursive: true });
writeFileSync(OUT, JSON.stringify({ cookies, origins: [] }, null, 2));

console.log(`[mint-auth-state] ✓ 인증 세션 발급 완료 (provider=${PROVIDER})`);
if (result.user?.email) console.log(`  user     : ${result.user.email}`);
console.log(`  cookies  : ${cookies.map((c) => c.name).join(", ")}  (domain=${origin.hostname}, secure=${secure})`);
console.log(`  state    : ${OUT}   ← Playwright storageState (비밀: 커밋 금지)`);
