#!/usr/bin/env node
// ─────────────────────────────────────────────────────────────────────────
// 인증 쓰기 검증 (범용) — mint-auth-state 로 만든 세션이 "정말 쓰기까지 되는" 세션인지
// 증명한다.
//
// 왜: 세션을 주입해 로그인 화면을 건너뛰는 것만으로는 부족하다. 그 세션의 유저가
//   실제로 쓸 권한(예: DB 의 RLS)이 있어야 QA 가 작성 흐름(생성→편집→제출…)을 태울 수
//   있다. 이 스크립트는 storageState 를 물고 앱을 인증 상태로 연 뒤, 두 가지를 본다.
//     (1) 인증 도달 — 보호된 경로로 갔을 때 로그인 화면으로 튕기지 않는가(범용).
//     (2) 실제 쓰기 — 앱마다 다른 쓰기 흐름 1건이 저장되는가(쓰기 프로브, 저장소별).
//
// 스택·앱 무관: 실제 쓰기 흐름(어떤 화면에서 무엇을 만들어 저장하는가)은 저장소마다
//   다르다. 그래서 이 범용 스크립트는 쓰기 단계를 **플러그인(쓰기 프로브)** 으로 둔다.
//   QA_WRITE_PROBE 로 저장소가 자기 흐름을 담은 모듈을 지정하면 그걸 실행하고, 지정이
//   없으면 (1) 인증 도달만 검증하고 "쓰기 미검증"을 분명히 알린다.
//
// 쓰기 프로브 계약: `export default async function probe({ page, appOrigin })`.
//   인증된 Playwright page 를 받아 실제 쓰기 1건을 수행하고, 앱의 읽기 경로로 저장을
//   되짚어 확인한다. 성공이면 정상 반환, 실패면 throw(또는 false 반환).
//
// 필요 환경변수:
//   QA_APP_ORIGIN     (기본 http://localhost:3100)  떠 있는 앱 주소
//   QA_STATE_OUT      (기본 <QA_APP_DIR>/e2e/.auth/qa-state.json)  storageState 경로
//   QA_APP_DIR        (기본 cwd)  @playwright/test 를 해석해 올 앱 디렉터리
//   QA_AUTH_CHECK_PATH(기본 /)      인증 도달을 확인할 보호 경로
//   QA_LOGIN_PATH     (기본 /login) 미인증 시 튕겨 가는 로그인 경로(이 경로로 가면 실패)
//   QA_WRITE_PROBE    (선택)  실제 쓰기 흐름을 담은 저장소별 모듈 경로. 없으면 인증만 검증.
//   PLAYWRIGHT_BROWSERS_PATH (선택)  미리 설치된 chromium 재사용
//
// 결과: 인증 도달(+프로브가 있으면 쓰기)까지 통과면 exit 0. 튕기거나 쓰기 실패면 exit 1.
// ─────────────────────────────────────────────────────────────────────────
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";
import { existsSync, readdirSync } from "node:fs";
import path from "node:path";

function fail(msg) { console.error(`[auth-write-check] ✗ ${msg}`); process.exit(1); }

const APP_ORIGIN = process.env.QA_APP_ORIGIN || "http://localhost:3100";
const APP_DIR = process.env.QA_APP_DIR || process.cwd();
const STATE = process.env.QA_STATE_OUT || path.join(APP_DIR, "e2e", ".auth", "qa-state.json");
const AUTH_CHECK_PATH = process.env.QA_AUTH_CHECK_PATH || "/";
const LOGIN_PATH = process.env.QA_LOGIN_PATH || "/login";
const WRITE_PROBE = process.env.QA_WRITE_PROBE || "";
if (!existsSync(STATE)) fail(`storageState 가 없습니다: ${STATE} — 먼저 mint-auth-state.mjs 를 실행하세요.`);

let chromium;
try {
  const req = createRequire(path.join(APP_DIR, "package.json"));
  // @playwright/test 는 ESM 동적 import 시 named export 를 노출하지 않고 default 하나만 준다.
  // (CJS 모듈이라 module.exports 전체가 default 로 들어옴) → default 폴백으로 chromium 을 집는다.
  const mod = await import(pathToFileURL(req.resolve("@playwright/test")).href);
  chromium = mod.chromium ?? mod.default?.chromium;
  if (!chromium) throw new Error("@playwright/test 에서 chromium 진입점을 찾지 못했습니다.");
} catch (e) {
  fail(`@playwright/test 를 APP_DIR(${APP_DIR}) 에서 못 찾음. QA_APP_DIR 지정 필요. (${e.message})`);
}

// 저장소별 쓰기 프로브가 지정됐으면 미리 해석해 둔다(브라우저 띄우기 전에 경로 오류를 잡는다).
let writeProbe = null;
if (WRITE_PROBE) {
  const probePath = path.resolve(WRITE_PROBE);
  if (!existsSync(probePath)) fail(`QA_WRITE_PROBE 모듈이 없습니다: ${probePath}`);
  try {
    const mod = await import(pathToFileURL(probePath).href);
    writeProbe = mod.default ?? mod.probe;
    if (typeof writeProbe !== "function") throw new Error("default export(또는 probe) 가 함수가 아닙니다.");
  } catch (e) {
    fail(`QA_WRITE_PROBE 를 불러오지 못했습니다(${probePath}): ${e.message}`);
  }
}

// playwright.config 와 같은 규약으로 미리 설치된 chromium 을 재사용(있으면).
function prebuiltChrome() {
  const base = process.env.PLAYWRIGHT_BROWSERS_PATH;
  if (!base) return undefined;
  try {
    const dir = readdirSync(base).find((d) => d.startsWith("chromium-"));
    const p = dir && `${base}/${dir}/chrome-linux/chrome`;
    return p && existsSync(p) ? p : undefined;
  } catch { return undefined; }
}
const executablePath = prebuiltChrome();

const browser = await chromium.launch(executablePath ? { executablePath } : {});
const ctx = await browser.newContext({ storageState: STATE });
const page = await ctx.newPage();
let ok = false;
try {
  // 1) 인증 도달 확인 — 보호 경로로 갔을 때 로그인 경로로 튕기지 않아야 한다(범용).
  await page.goto(`${APP_ORIGIN}${AUTH_CHECK_PATH}`, { waitUntil: "networkidle" });
  if (new URL(page.url()).pathname.startsWith(LOGIN_PATH)) {
    fail(`인증 실패 — ${LOGIN_PATH} 로 리다이렉트됨(세션 미주입/만료).`);
  }
  console.log(`[auth-write-check] · 인증 도달 확인 OK (${page.url()})`);

  // 2) 실제 쓰기 — 저장소별 쓰기 프로브가 있으면 실행, 없으면 인증만 검증.
  if (writeProbe) {
    const r = await writeProbe({ page, appOrigin: APP_ORIGIN });
    if (r === false) fail("쓰기 프로브가 실패를 반환했습니다(저장 확인 실패).");
    console.log(`[auth-write-check] ✓ PASS — 인증 상태에서 쓰기 프로브 통과`);
  } else {
    console.log(`[auth-write-check] ⚠ 쓰기 프로브 미설정(QA_WRITE_PROBE) — 인증 도달만 검증됨(쓰기 미검증).`);
    console.log(`[auth-write-check] ✓ PASS(부분) — 인증 도달 확인. 쓰기까지 보려면 QA_WRITE_PROBE 를 지정하세요.`);
  }
  ok = true;
} finally {
  await browser.close();
}
process.exit(ok ? 0 : 1);
