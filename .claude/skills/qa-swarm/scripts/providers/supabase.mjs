// ─────────────────────────────────────────────────────────────────────────
// 인증 provider(제공자) 어댑터 — Supabase.
//
// 이 파일은 "인증된 QA 세션" 능력에서 **Supabase 스택에만 해당하는 지식**을 전부
// 담는 곳이다. 범용 드라이버(mint-auth-state.mjs)는 스택을 모른 채 이 어댑터를 불러
// mint() 가 돌려준 쿠키를 Playwright storageState 로 옮길 뿐이다. 다른 스택(Auth0·
// NextAuth·Rails 등)을 쓰는 저장소는 이 파일을 흉내 낸 providers/<스택>.mjs 를
// 새로 쓰면 되고, 이 파일은 건드리지 않는다. (계약은 providers/README.md 참조.)
//
// 핵심 설계: 쿠키 이름·base64 인코딩·청킹(긴 값을 여러 조각으로 나누는 것)을 손으로
//   흉내 내지 않는다. **검증 대상 앱이 실제로 쓰는 그 @supabase/ssr 라이브러리**(앱
//   node_modules)를 그대로 불러 세션을 쿠키로 직렬화시킨다. 앱이 읽는 코드와 같은
//   코드로 쓰므로 형식이 어긋날 수 없다.
// ─────────────────────────────────────────────────────────────────────────
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";
import path from "node:path";

// 앱의 node_modules 에서 @supabase/ssr 의 createServerClient 를 해석해 온다.
async function loadCreateServerClient(appDir) {
  const req = createRequire(path.join(appDir, "package.json"));
  const mod = await import(pathToFileURL(req.resolve("@supabase/ssr")).href);
  if (!mod.createServerClient) throw new Error("@supabase/ssr 에 createServerClient 가 없습니다.");
  return mod.createServerClient;
}

// 이 어댑터가 필요로 하는 환경변수를 사람이 읽는 이름으로 정리(드라이버가 안내에 씀).
export const requiredEnv = [
  "QA_SUPABASE_URL (없으면 NEXT_PUBLIC_SUPABASE_URL) — 테스트 프로젝트 URL",
  "QA_SUPABASE_ANON_KEY (없으면 NEXT_PUBLIC_SUPABASE_ANON_KEY) — 공개(anon) 키",
  "QA_TEST_EMAIL / QA_TEST_PASSWORD — 테스트 유저 자격증명",
];

// ── mint: 테스트 유저로 로그인해 '앱이 읽을 수 있는 인증 쿠키'를 만들어 돌려준다.
//    반환: { cookies: [{name, value}], user: {email}, expiresAt?: number(unix초) }
//    (도메인·secure·만료 버퍼 같은 storageState 조립은 범용 드라이버가 맡는다.)
export async function mint({ appDir, env }) {
  const url = env.QA_SUPABASE_URL || env.NEXT_PUBLIC_SUPABASE_URL;
  const anon = env.QA_SUPABASE_ANON_KEY || env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  const email = env.QA_TEST_EMAIL;
  const password = env.QA_TEST_PASSWORD;
  if (!url || !anon) throw new Error("QA_SUPABASE_URL/QA_SUPABASE_ANON_KEY (또는 NEXT_PUBLIC_*) 가 없습니다.");
  if (!email || !password) throw new Error("QA_TEST_EMAIL/QA_TEST_PASSWORD 가 없습니다.");

  let createServerClient;
  try {
    createServerClient = await loadCreateServerClient(appDir);
  } catch (e) {
    throw new Error(`@supabase/ssr 를 앱 폴더(${appDir})에서 찾지 못했습니다. QA_APP_DIR 를 앱 폴더로 지정하세요. (${e.message})`);
  }

  // 인메모리 쿠키 항아리 — ssr 이 세션을 여기에 직렬화한다.
  const jar = new Map();
  const supabase = createServerClient(url, anon, {
    cookies: {
      getAll() { return [...jar.entries()].map(([name, value]) => ({ name, value })); },
      setAll(list) { for (const c of list) jar.set(c.name, c.value); },
    },
  });

  const { data, error } = await supabase.auth.signInWithPassword({ email, password });
  if (error) {
    // 흔한 원인을 사람 말로 풀어 준다.
    const m = error.message || String(error);
    let hint = "";
    if (/invalid login credentials/i.test(m)) hint = " — 유저가 없거나 비번이 틀렸습니다. authenticated-session.md 의 1회 프로비저닝을 먼저 하세요.";
    else if (/email logins are disabled|email provider/i.test(m)) hint = " — 테스트 프로젝트에서 Email(비밀번호) 인증이 꺼져 있습니다. Authentication > Providers 에서 켜세요.";
    else if (/email not confirmed/i.test(m)) hint = " — 유저 이메일이 미확인 상태입니다. email_confirm=true 로 프로비저닝하세요.";
    throw new Error(`로그인 실패: ${m}${hint}`);
  }
  if (!data?.session) throw new Error("로그인은 됐으나 세션이 없습니다(이메일 확인 대기일 수 있음).");
  if (jar.size === 0) throw new Error("세션 쿠키가 만들어지지 않았습니다(@supabase/ssr 버전 확인).");

  return {
    cookies: [...jar.entries()].map(([name, value]) => ({ name, value })),
    user: { email: data.session.user?.email },
    expiresAt: data.session.expires_at ?? null,
  };
}

// ── selftest: '세션을 쿠키로 옮겨 다시 읽는' 배관이 맞는지 네트워크 없이 증명한다(선택).
//    합성 세션을 쿠키로 쓰고 → 그 쿠키만 가진 새 클라이언트로 다시 읽어 같은 사용자·
//    토큰이 복원되는지 확인한다. GoTrue(Supabase 인증 서버) 사용자 검증 호출 하나만
//    스텁하고, 쿠키 인코딩·재읽기는 전부 라이브러리 실코드다. 라이브러리 버전이 올라
//    쿠키 형식이 바뀌면 이 테스트가 먼저 깨져 알려 준다. 실패 시 throw.
export async function selftest({ appDir }) {
  let createServerClient;
  try {
    createServerClient = await loadCreateServerClient(appDir);
  } catch (e) {
    throw new Error(`@supabase/ssr 를 앱 폴더(${appDir})에서 못 찾음. QA_APP_DIR 지정 필요. (${e.message})`);
  }

  const b64url = (o) => Buffer.from(JSON.stringify(o)).toString("base64url");
  const future = Math.floor(Date.now() / 1000) + 3600;
  const email = "qa-selftest@example.com";
  const sub = "00000000-0000-0000-0000-0000000000aa";
  const user = { id: sub, email, aud: "authenticated", role: "authenticated", app_metadata: {}, user_metadata: {} };
  // 구조적으로 유효한(서명 미검증) JWT — 오프라인 setSession/getSession 에 충분.
  const jwt = [
    b64url({ alg: "HS256", typ: "JWT" }),
    b64url({ sub, email, aud: "authenticated", role: "authenticated", exp: future, iat: future - 3600 }),
    "c2lnbmF0dXJl",
  ].join(".");
  const session = {
    access_token: jwt, refresh_token: "selftest-refresh", expires_at: future,
    expires_in: 3600, token_type: "bearer", user,
  };

  // GoTrue 사용자 검증 호출 하나만 스텁. 나머지(쿠키 인코딩/청킹/재읽기)는 실코드.
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (input, init) => {
    const u = typeof input === "string" ? input : input?.url ?? "";
    if (u.includes("/auth/v1/user")) {
      return new Response(JSON.stringify(user), { status: 200, headers: { "content-type": "application/json" } });
    }
    return realFetch(input, init);
  };

  try {
    const client = (jar) => createServerClient("https://selftestref.supabase.co", "anon-placeholder", {
      cookies: {
        getAll() { return [...jar.entries()].map(([name, value]) => ({ name, value })); },
        setAll(list) { for (const c of list) jar.set(c.name, c.value); },
      },
    });

    // 1) MINT: 세션을 쿠키로 직렬화.
    const writeJar = new Map();
    const { error: setErr } = await client(writeJar).auth.setSession(session);
    if (setErr) throw new Error(`setSession: ${setErr.message}`);
    const names = [...writeJar.keys()];
    if (names.length === 0) throw new Error("쿠키가 쓰이지 않음");

    // 2) INJECT + READ-BACK: 그 쿠키만 가진 새 클라이언트로 다시 읽기.
    const readJar = new Map(writeJar);
    const { data, error: getErr } = await client(readJar).auth.getSession();
    if (getErr) throw new Error(`getSession: ${getErr.message}`);
    const got = data.session;
    if (!got) throw new Error("세션이 재읽기되지 않음");
    if (got.user?.email !== email) throw new Error(`이메일 불일치: ${got.user?.email}`);
    if (got.access_token !== jwt) throw new Error("토큰 불일치");

    return { cookieNames: names, email };
  } finally {
    globalThis.fetch = realFetch;
  }
}
