#!/usr/bin/env node
// ─────────────────────────────────────────────────────────────────────────
// mint-auth-state 의 오프라인 자기검증 (범용 드라이버) — 세션 주입 배관이 맞는지
// network 없이 증명한다.
//
// 왜: 인증 쿠키를 만드는 배관(세션을 앱이 읽을 수 있는 쿠키로 옮기는 것)은 스택
//   라이브러리 버전에 묶여 있어, 손으로 흉내 내면 언제든 어긋난다. 이 드라이버는
//   선택된 provider(제공자) 어댑터의 selftest() 를 불러, 그 배관이 왕복하는지를
//   네트워크 없이 확인한다. 라이브러리 버전이 올라 형식이 바뀌면 이 테스트가 먼저 깨진다.
//
//   provider 가 selftest() 를 제공하지 않으면(오프라인 검증이 불가능한 스택도 있다)
//   건너뛰고 exit 0 한다 — 이건 실패가 아니라 "이 provider 엔 오프라인 자기검증이 없음"이다.
//
// 사용: QA_AUTH_PROVIDER=<provider> QA_APP_DIR=<앱 폴더> node mint-auth-state.selftest.mjs
//       (기본 provider=supabase, 기본 APP_DIR=cwd. exit 0 = PASS 또는 스킵)
// ─────────────────────────────────────────────────────────────────────────
const PROVIDER = process.env.QA_AUTH_PROVIDER || "supabase";
const APP_DIR = process.env.QA_APP_DIR || process.cwd();

let provider;
try {
  const url = new URL(`./providers/${PROVIDER}.mjs`, import.meta.url);
  provider = await import(url.href);
} catch (e) {
  console.error(`[selftest] ✗ 인증 provider '${PROVIDER}' 를 불러오지 못했습니다. (${e.message})`);
  process.exit(1);
}

if (typeof provider.selftest !== "function") {
  console.log(`[selftest] · provider '${PROVIDER}' 는 오프라인 자기검증을 제공하지 않습니다 — 스킵(정상).`);
  process.exit(0);
}

try {
  const r = await provider.selftest({ appDir: APP_DIR });
  const names = r?.cookieNames ? JSON.stringify(r.cookieNames) : "(이름 미보고)";
  const who = r?.email ? `, 사용자 ${r.email} 복원` : "";
  console.log(`[selftest] ✓ PASS (provider=${PROVIDER}) — 쿠키 ${names} 로 왕복${who}, 토큰 일치`);
  process.exit(0);
} catch (e) {
  console.error(`[selftest] ✗ ${e.message || String(e)}`);
  process.exit(1);
}
