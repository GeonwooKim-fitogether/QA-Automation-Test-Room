#!/usr/bin/env node
// PR 크로스컷 게이트 — 결정론적 검사 (LLM 없음, API 비용 없음)
//
// 왜 이 스크립트가 있나. `/pr-gate` 슬래시 커맨드는 PR 하나를 네 축으로 검토하는데,
// 그것을 GitHub Actions 에서 자동화하려면 Claude 를 API 로 불러야 하고 그건 별도
// 비용이 든다. 그런데 네 축이 실제로 검사하는 것의 대부분은 **LLM 이 필요 없다.**
//
// 근거가 정본 문서 자체에 있다. 축 A 의 기준인 `docs/doc-governance.md` §2 는
// "이 개발을 했으면 이 문서를 반드시 갱신" 을 마크다운 표로 갖고 있고, 축 C 의
// 기준인 `docs/system-layer/contract-registry.md` 는 "계약 → 소비자 파트" 를 표로
// 갖고 있다. 둘 다 기계가 읽는다. 그리고 축 D 에 대해 `pr-gate.md` 는 스스로
// 이렇게 적는다 — "이 결함은 고아 코드 패턴으로 나타나고, 이것은 검색으로 판정
// 가능하다."
//
// 그래서 이 스크립트가 기계로 판정 가능한 전부를 맡고, **판단이 필요한 잔여만**
// 사람이 세션에서 `/pr-gate` 를 불러 처리한다. 세션에서 부르는 것은 이미 쓰는
// Claude Code 안에서 돌아 추가 비용이 없다.
//
// ── 이 스크립트가 하지 않는 것 (정직하게) ─────────────────────────────────
// 판단이 필요한 것은 여기서 하지 않는다. 대신 "사람이 봐야 함" 으로 표시한다.
//   · 축 C 의 "이것이 계약 자체를 바꾸는 변경인가, 파트 내부 변경인가"
//     (단 함수에 대해서는 축 C-2 가 "같은 함수를 다른 갈래가 방금 바꿨는가" 를 기계로 잡는다)
//   · 축 A 의 "UI 재설계가 시안의 수용 기준에 맞는가"
//   · 발견된 것의 심각도를 문맥에 비추어 가늠하는 일
//
// ── 도입 2단계 ────────────────────────────────────────────────────────────
// 기본은 경고(exit 0)다. 오탐률을 관찰한 뒤 `--strict` 를 붙여 차단으로 올린다.
//
// 사용:
//   node .claude/workflows/pr-gate-check.mjs [--base origin/main] [--strict] [--pr-body <파일>]
//
// 기준 문서가 없는 저장소에서는 해당 축을 **건너뛰되 건너뛴 사실을 보고한다**
// (조용히 통과하지 않는다).

import { execSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";

const args = process.argv.slice(2);
const valOf = (k) => {
  const i = args.indexOf(k);
  return i >= 0 ? args[i + 1] : undefined;
};
const STRICT = args.includes("--strict");
const BASE = valOf("--base") || "origin/main";
const PR_BODY_FILE = valOf("--pr-body");

const sh = (cmd) => {
  try {
    return execSync(cmd, { encoding: "utf8", maxBuffer: 64 * 1024 * 1024 });
  } catch {
    return "";
  }
};

// ── diff 수집 ────────────────────────────────────────────────────────────────
const changed = sh(`git diff --name-only ${BASE}...HEAD`).split("\n").filter(Boolean);
const added = sh(`git diff --diff-filter=A --name-only ${BASE}...HEAD`).split("\n").filter(Boolean);
// 추가된 줄만 (파일 경로 주석 포함해 어느 파일의 줄인지 추적)
const diffRaw = sh(`git diff --unified=0 ${BASE}...HEAD`);
const addedLines = []; // {file, text}
{
  let cur = "";
  for (const line of diffRaw.split("\n")) {
    if (line.startsWith("+++ b/")) cur = line.slice(6);
    else if (line.startsWith("+") && !line.startsWith("+++")) addedLines.push({ file: cur, text: line.slice(1) });
  }
}

if (changed.length === 0) {
  console.log("ℹ 변경된 파일이 없습니다. 게이트가 검사할 것이 없습니다.");
  process.exit(0);
}

const findings = []; // {axis, level: 'block'|'warn'|'human', title, detail}
const skipped = []; // 기준 문서가 없어 건너뛴 축
const add = (axis, level, title, detail) => findings.push({ axis, level, title, detail });

const isTest = (p) => /(\.spec\.|\.test\.|__tests__|\/e2e\/)/.test(p);
const backticked = (s) => [...s.matchAll(/`([^`]+)`/g)].map((m) => m[1]);

// ── 축 A — 문서 갱신 매트릭스 이행 ───────────────────────────────────────────
// 정본: docs/doc-governance.md §2 표. "반드시 갱신" 칸의 백틱 경로를 그 표에서 읽는다.
// 표가 바뀌면 이 검사가 따라간다(요구 문서 목록을 스크립트에 복사하지 않는다).
{
  const GOV = "docs/doc-governance.md";
  if (!existsSync(GOV)) {
    skipped.push(`축 A — 기준 문서 \`${GOV}\` 가 없어 건너뛰었습니다.`);
  } else {
    const govFull = readFileSync(GOV, "utf8");
    // §2 의 갱신 트리거 매트릭스만 본다. 이 문서에는 문서 목록 표(§1)도 있어서
    // 전체를 긁으면 관계없는 행까지 세어 "검사하지 못한 행" 수를 부풀린다.
    const secStart = govFull.search(/^##\s*2\..*매트릭스/m);
    const govSec = secStart >= 0 ? govFull.slice(secStart) : govFull;
    const secEnd = govSec.slice(3).search(/^##\s/m);
    const gov = secEnd >= 0 ? govSec.slice(0, secEnd + 3) : govSec;
    // 표 행: | 개발 유형 | 반드시 갱신 | 권장 갱신 |
    const rows = gov
      .split("\n")
      .filter((l) => l.startsWith("|") && l.split("|").length >= 4)
      .map((l) => l.split("|").slice(1, -1).map((c) => c.trim()))
      .filter((c) => c.length >= 3 && !/^-+$/.test(c[0]) && c[0] !== "개발 유형 (했다면)");

    // 개발 유형을 경로 패턴으로 잇는 감지기. 표의 라벨에 이 키가 들어 있으면 그 행을 쓴다.
    const detectors = [
      { key: "스키마 마이그레이션", hit: () => added.some((p) => /^supabase\/migrations\/.*\.sql$/.test(p)) },
      {
        key: "척추",
        hit: () =>
          addedLines.some(
            (l) => /supabase\/migrations\//.test(l.file) && /(guard_revision_spine|make_change_effective|CREATE POLICY|ALTER POLICY)/i.test(l.text),
          ),
      },
      { key: "새 모듈/화면", hit: () => added.some((p) => /^app\/src\/app\/.*\/page\.tsx$/.test(p)) },
      { key: "디자인 토큰", hit: () => changed.some((p) => /design-tokens\.md$|globals\.css$/.test(p)) },
      {
        key: "외부 연동",
        // 앱·함수 소스에서만 본다. `.github/` 디렉터리가 "github" 에 걸리는 오탐을 겪어
        // 경로를 앱 소스로 좁혔다(워크플로 파일 추가는 외부 연동이 아니다).
        hit: () =>
          added.some(
            (p) => /^(app\/src|supabase\/functions)\//.test(p) && /(drive|github|erp|slack|jira)/i.test(p.replace(/^.*\//, "")),
          ),
      },
    ];

    let matchedRows = 0;
    for (const row of rows) {
      const [label, must] = row;
      const det = detectors.find((d) => label.includes(d.key));
      if (!det) continue;
      matchedRows++;
      if (!det.hit()) continue;
      const required = backticked(must).filter((p) => p.includes("/") || p.endsWith(".md") || p.endsWith(".html") || p.endsWith(".css"));
      const missing = required.filter((req) => {
        const bare = req.replace(/^.*?([^/]+)$/, "$1");
        return !changed.some((c) => c.endsWith(req) || c.endsWith(bare));
      });
      if (missing.length) {
        add(
          "A",
          "block",
          `"${label.replace(/\*\*/g, "")}" 을 했는데 반드시 갱신할 문서가 같은 PR 에 없습니다`,
          `빠진 것: ${missing.map((m) => `\`${m}\``).join(" · ")}\n기준: ${GOV} §2 갱신 트리거 매트릭스`,
        );
      }
    }
    const unmapped = rows.length - matchedRows;
    if (unmapped > 0) {
      skipped.push(
        `축 A — 매트릭스 ${rows.length}행 중 ${unmapped}행은 경로 패턴 감지기가 없어 검사하지 않았습니다(예: "기존 화면 UI 재설계"처럼 판단이 필요한 유형). 이 유형은 사람이 봐야 합니다.`,
      );
    }
  }
}

// ── 축 B — 척추 준수 ────────────────────────────────────────────────────────
{
  const SPINE_COLS = /(^|[^a-z_])(rev|lifecycle_phase|status|released_by_change)([^a-z_]|$)/i;
  for (const l of addedLines) {
    if (isTest(l.file)) continue;
    const t = l.text;
    // 발효 함수 본문 안의 UPDATE 는 정상 경로다.
    const inEffective = /make_change_effective/.test(t);
    if (!inEffective && /\b(UPDATE|INSERT\s+INTO)\b[\s\S]{0,80}\bitem_revision\b/i.test(t) && SPINE_COLS.test(t)) {
      add("B", "block", "척추 컬럼을 발효 경로 밖에서 쓰는 코드가 추가됐습니다", `\`${l.file}\`\n> ${t.trim().slice(0, 200)}`);
    }
    if (/\bDROP\s+(POLICY|TRIGGER)\b/i.test(t) && /(item_revision|guard_revision_spine|spine)/i.test(t)) {
      add("B", "block", "척추를 지키는 정책·트리거를 제거하는 변경입니다", `\`${l.file}\`\n> ${t.trim().slice(0, 200)}`);
    }
  }
}

// ── 축 C — 타 파트 영향 (계약 레지스트리 대조) ───────────────────────────────
{
  const REG = "docs/system-layer/contract-registry.md";
  if (!existsSync(REG)) {
    skipped.push(`축 C — 기준 문서 \`${REG}\` 가 없어 건너뛰었습니다.`);
  } else {
    const reg = readFileSync(REG, "utf8");
    const contracts = [];
    for (const line of reg.split("\n")) {
      if (!line.startsWith("|")) continue;
      const cells = line.split("|").slice(1, -1).map((c) => c.trim());
      if (cells.length < 3 || /^-+$/.test(cells[0]) || cells[0].startsWith("계약")) continue;
      const ids = backticked(cells[0]).filter((s) => /^[a-z_][a-z0-9_.]*$|^--/.test(s));
      if (ids.length) contracts.push({ ids, consumers: cells[2] || "(소비자 미기재)" });
    }
    const hits = new Map();
    for (const c of contracts) {
      for (const id of c.ids) {
        const re = new RegExp(`(^|[^a-zA-Z0-9_-])${id.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}([^a-zA-Z0-9_-]|$)`);
        const where = addedLines.filter((l) => !isTest(l.file) && re.test(l.text));
        if (where.length) {
          const key = id;
          if (!hits.has(key)) hits.set(key, { consumers: c.consumers, files: new Set() });
          where.forEach((w) => hits.get(key).files.add(w.file));
        }
      }
    }
    for (const [id, v] of hits) {
      add(
        "C",
        "human",
        `공유물 \`${id}\` 을 건드립니다 — 계약 변경인지 사람이 판단해야 합니다`,
        `소비자: ${v.consumers.replace(/\*\*/g, "").slice(0, 300)}\n건드린 곳: ${[...v.files].slice(0, 5).map((f) => `\`${f}\``).join(" · ")}\n` +
          `파트 내부 변경이면 팀 재량이고, **계약 자체를 바꾸는 변경**(컬럼 의미·토큰 값·공용 컴포넌트 인터페이스)이면 총괄 승인이 필요합니다.\n` +
          `이 계약이 데이터베이스 함수라면 이 질문만으로는 부족합니다 — 축 C-2 가 "같은 함수를 다른 갈래가 방금 바꿨는가"를 따로 묻습니다.`,
      );
    }
    if (contracts.length === 0) {
      skipped.push(`축 C — \`${REG}\` 에서 계약 표를 파싱하지 못했습니다(형식이 다를 수 있음).`);
    }
  }
}

// ── 축 C-2 — 같은 함수를 다른 갈래가 방금 바꿨나 (되돌림 탐지) ────────────────
//
// ## 왜 이 검사가 따로 있나 — 걸리고도 놓친 실패에서 나왔다
//
// 2026-08-18 에 두 세션이 척추 함수 `make_change_effective` 를 13분 차이로 고쳤고, 나중
// 적용이 앞의 것을 통째로 삼켰다. 그런데 **축 C 는 이미 걸렸다.** 그 함수가 계약
// 레지스트리에 등재돼 있어 "사람 판단 필요"로 올라갔다. 놓친 이유는 검사가 없어서가
// 아니라 **걸리고도 다른 것을 물어서**였다.
//
//   축 C 가 붙인 질문   "이것이 계약 자체를 바꾸는 변경인가, 파트 내부 변경인가"
//   그때의 답            "아니오, 출처를 이월할 뿐이다"  → 통과
//   물어야 했던 질문     "같은 함수를 다른 누군가가 방금 바꿨는가"
//
// 두 질문은 다르다. 앞의 것은 **무엇을 바꾸는가**를 묻고, 뒤의 것은 **무엇 위에
// 얹는가**를 묻는다. `create or replace function` 은 앞의 정의 위에 얹는 것이 아니라
// 통째로 지우고 새로 쓰므로, 전제가 낡았으면 남의 변경이 조용히 사라진다.
//
// ## 왜 git 도 옆 검사도 잡지 못하나
//
// 두 마이그레이션은 **이름이 다른 별개 파일**이라 텍스트 충돌이 없다. 그래서 git 은
// 조용히 둘 다 받아들이고, `번호 충돌 방지` 검사도 파일 이름만 보므로 통과시킨다.
// 함수는 이름이 하나이고 모든 마이그레이션이 그 하나를 덮어쓴다는 점이 이 축을
// 파일명 규칙과 다른 별개의 위험으로 만든다.
//
// ## 무엇을 재나
//
// 이 PR 이 재정의하는 함수와, **base(보통 main)가 분기점 이후 재정의한 함수**를 대조한다.
// 겹치면 이 PR 의 본문이 낡은 정의 위에 쓰였을 수 있다는 뜻이므로 사람에게 올린다.
//
// 이 검사가 못 하는 것도 적어 둔다. **저장소에 아예 push 되지 않은 변경은 볼 수 없다** —
// 사고 당시 07:13 세션의 파일이 정확히 그 상태였다. 그 구멍은 훅의 "적용 전 push 판정"과
// 마이그레이션 안의 지문 잠금 블록이 메운다(`scripts/function-guard.mjs`).
{
  const MIG = "supabase/migrations/";
  const fnRe = /create\s+or\s+replace\s+function\s+([A-Za-z_][\w.]*)\s*\(/gi;
  const norm = (n) => (n.includes(".") ? n : `public.${n}`).toLowerCase();
  const fnsIn = (sql) => {
    const code = sql.split("\n").filter((l) => !/^\s*--/.test(l)).join("\n");
    return [...new Set([...code.matchAll(fnRe)].map((m) => norm(m[1])))];
  };

  const ourMigrations = changed.filter((f) => f.startsWith(MIG) && f.endsWith(".sql") && !f.includes("/rollback/"));
  const ours = new Map(); // 함수 → 이 PR 에서 그것을 건드린 파일들
  for (const f of ourMigrations) {
    let sql = "";
    try {
      sql = readFileSync(f, "utf8");
    } catch {
      continue;
    }
    for (const fn of fnsIn(sql)) {
      if (!ours.has(fn)) ours.set(fn, new Set());
      ours.get(fn).add(f);
    }
  }

  if (ours.size) {
    const mergeBase = sh(`git merge-base ${BASE} HEAD`).trim();
    if (!mergeBase) {
      skipped.push(`축 C-2 — \`${BASE}\` 와의 분기점을 찾지 못해 건너뛰었습니다(원격을 fetch 했는지 확인하십시오).`);
    } else {
      // base 가 분기점 이후 건드린 마이그레이션 파일들
      const baseSide = sh(`git diff --name-only ${mergeBase} ${BASE} -- ${MIG}`)
        .split("\n")
        .filter((f) => f.endsWith(".sql") && !f.includes("/rollback/"));

      const theirs = new Map(); // 함수 → base 쪽에서 그것을 건드린 파일들
      for (const f of baseSide) {
        const sql = sh(`git show ${BASE}:${JSON.stringify(f).slice(1, -1)} 2>/dev/null`);
        if (!sql) continue;
        for (const fn of fnsIn(sql)) {
          if (!theirs.has(fn)) theirs.set(fn, new Set());
          theirs.get(fn).add(f);
        }
      }

      for (const [fn, ourFiles] of ours) {
        if (!theirs.has(fn)) continue;
        add(
          "C",
          "human",
          `\`${fn}\` 을 **${BASE} 도 분기점 이후 바꿨습니다** — 이 PR 의 정의가 낡은 것 위에 쓰였을 수 있습니다`,
          `이 PR 이 건드린 곳: ${[...ourFiles].map((f) => `\`${f}\``).join(" · ")}\n` +
            `${BASE} 가 건드린 곳: ${[...theirs.get(fn)].map((f) => `\`${f}\``).join(" · ")}\n` +
            `\n` +
            `**물어야 하는 것은 "이것이 계약을 바꾸는가"가 아니라 "무엇 위에 얹는가"입니다.** ` +
            `create or replace function 은 앞의 정의 위에 얹는 것이 아니라 통째로 지우고 새로 쓰므로, ` +
            `전제가 낡았으면 상대의 변경이 오류 없이 사라집니다. 두 마이그레이션은 이름이 다른 별개 ` +
            `파일이라 git 도 이 충돌을 보지 못합니다.\n` +
            `\n` +
            `확인할 것 둘. 첫째, 이 PR 의 함수 본문을 **라이브의 현재 본문(pg_proc.prosrc)** 을 읽어 ` +
            `그 위에 얹었는지. 저장소의 마지막 마이그레이션이 아니라 라이브가 기준입니다. 둘째, 그 ` +
            `마이그레이션에 **지문 잠금 블록**이 있는지 — 없으면 ` +
            `\`node scripts/function-guard.mjs --emit ${fn}\` 으로 서식을 받아 넣으십시오.`,
        );
      }
    }
  }
}

// ── 축 D — 배선·적용 ────────────────────────────────────────────────────────
{
  // D-1. 고아 코드: 새로 추가된 파일이 내보내는 심볼을, 자기 파일과 테스트 밖에서 부르나.
  for (const f of added) {
    if (!/\.(ts|tsx|js|mjs)$/.test(f) || isTest(f)) continue;
    let src = "";
    try {
      src = readFileSync(f, "utf8");
    } catch {
      continue;
    }
    const syms = [...src.matchAll(/export\s+(?:async\s+)?(?:function|const|class)\s+([A-Za-z_$][\w$]*)/g)]
      .map((m) => m[1])
      // 전부 대문자인 상수(MIN_SAMPLE 같은 것)는 제외한다. 내보냈지만 밖에서 안 쓰는
      // 상수는 배선 결함이 아니라 정리 대상이고, 실측에서 이 부류가 오탐의 대부분이었다.
      // 이 게이트가 잡으려는 것은 "만들어졌는데 사용자에게 닿지 않는 기능"이다.
      .filter((s) => !/^[A-Z0-9_]+$/.test(s));
    if (!syms.length) continue;
    const orphans = syms.filter((s) => {
      const out = sh(`git grep -l -w -- ${JSON.stringify(s)} 2>/dev/null`).split("\n").filter(Boolean);
      return out.every((p) => p === f || isTest(p));
    });
    if (orphans.length) {
      add(
        "D",
        "block",
        `새로 만든 것을 부르는 곳이 자기 파일과 테스트 밖에 없습니다 (고아 코드)`,
        `\`${f}\`\n내보내지만 아무도 부르지 않는 심볼: ${orphans.map((s) => `\`${s}\``).join(" · ")}\n` +
          `의도적으로 뒤 작업에서 연결할 계획이면 PR 설명에 **"미배선 — 후속 작업에서 연결"** 을 적으면 됩니다(침묵만 금지).`,
      );
    }
  }

  // D-2. 새 화면 진입점: 새 page 가 있으면 네비게이션·레이아웃도 같은 PR 에 있나.
  const newPages = added.filter((p) => /^app\/src\/app\/.*\/page\.tsx$/.test(p));
  if (newPages.length) {
    const navTouched = changed.some((p) => /(nav|sidebar|menu|layout)/i.test(p));
    if (!navTouched) {
      add(
        "D",
        "block",
        "새 화면을 만들었는데 진입점을 등록하는 변경이 같은 PR 에 없습니다",
        `새 화면: ${newPages.map((p) => `\`${p}\``).join(" · ")}\n` +
          `네비게이션·사이드바·메뉴·레이아웃 중 어느 것도 바뀌지 않았습니다. 사용자는 주소를 모르므로 이 화면의 존재를 알 방법이 없습니다.`,
      );
    }
  }

  // D-3. 적용 꼬리표: 환경을 바꾸는 파일이 있으면 PR 설명에 적용 상태가 있나.
  const envFiles = changed.filter((p) => /^supabase\/migrations\/|\.env|config\.toml$/.test(p));
  if (envFiles.length) {
    let body = "";
    if (PR_BODY_FILE && existsSync(PR_BODY_FILE)) body = readFileSync(PR_BODY_FILE, "utf8");
    if (!body) {
      skipped.push("축 D 적용 꼬리표 — PR 설명을 받지 못해(`--pr-body` 미지정) 검사하지 못했습니다.");
    } else if (!/(실환경 적용 대기|적용 확인|로컬 적용|이미 적용)/.test(body)) {
      add(
        "D",
        "block",
        "환경을 바꾸는 파일이 있는데 PR 설명에 적용 상태가 없습니다",
        `대상: ${envFiles.slice(0, 5).map((p) => `\`${p}\``).join(" · ")}\n` +
          `**"실환경 적용 대기"** 또는 적용 확인 로그를 PR 설명에 적어야 합니다(규칙 7-5). 미적용 자체가 아니라 침묵이 금지입니다.`,
      );
    }
  }

  // D-4. 죽은 스위치: 새 조건이 비교하는 리터럴이 데이터·설정에 실제로 선언돼 있나.
  //      실제 사고가 이 형태였고, 심볼 참조를 세는 D-1 로는 절대 잡히지 않는다.
  const literals = new Map(); // literal -> Set(file)
  for (const l of addedLines) {
    if (isTest(l.file) || !/\.(ts|tsx|js|mjs)$/.test(l.file)) continue;
    for (const m of l.text.matchAll(/[=!]==\s*["']([a-z][a-z0-9_-]{2,30})["']/gi)) {
      if (!literals.has(m[1])) literals.set(m[1], new Set());
      literals.get(m[1]).add(l.file);
    }
  }
  for (const [lit, files] of literals) {
    // 데이터·시드·마이그레이션·설정에 그 값이 있나. 구현·테스트 파일은 제외한다.
    const found = sh(`git grep -l -F -- ${JSON.stringify(lit)} 2>/dev/null`).split("\n").filter(Boolean);
    const inData = found.filter((p) => /^(supabase\/|.*seed|.*\.sql$|.*\.json$|.*\.toml$|.*\.ya?ml$)/i.test(p) && !isTest(p));
    if (inData.length === 0) {
      add(
        "D",
        "warn",
        `새 조건이 비교하는 값 \`"${lit}"\` 을 데이터·마이그레이션·설정에서 찾지 못했습니다 (죽은 스위치 후보)`,
        `조건이 있는 곳: ${[...files].slice(0, 3).map((f) => `\`${f}\``).join(" · ")}\n` +
          `이 값을 선언하는 데이터가 없으면 조건이 언제나 거짓이어서 그 코드는 **한 번도 실행되지 않습니다.** 오류도 나지 않습니다.\n` +
          `실제 사고가 이 형태였습니다 — 엔진은 호출부가 멀쩡했으나 조건값을 아무 데이터도 선언하지 않아 발화하지 않았습니다.\n` +
          `문자열이 다른 뜻(비교 대상이 데이터가 아닌 값)이면 오탐입니다.`,
      );
    }
  }
}

// ── 보고 ────────────────────────────────────────────────────────────────────
const AX = { A: "축 A — 문서 갱신 매트릭스", B: "축 B — 척추 준수", C: "축 C — 타 파트 영향", D: "축 D — 배선·적용" };
const blocks = findings.filter((f) => f.level === "block");
const warns = findings.filter((f) => f.level === "warn");
const humans = findings.filter((f) => f.level === "human");

const out = [];
out.push("## PR 크로스컷 게이트 (결정론적 검사)");
out.push("");
const verdict =
  blocks.length > 0 ? `**차단 후보 ${blocks.length}건**` : warns.length > 0 ? `**주의 ${warns.length}건**` : "**기계 검사에서 걸린 것 없음**";
out.push(
  `${verdict}${humans.length ? ` · 사람이 판단할 것 ${humans.length}건` : ""} — 변경 파일 ${changed.length}개를 네 축으로 검사했습니다. ` +
    `이 검사는 **LLM 을 쓰지 않아 API 비용이 없습니다.**`,
);
out.push("");

for (const [level, list, head] of [
  ["block", blocks, "### 차단 후보"],
  ["warn", warns, "### 주의"],
  ["human", humans, "### 사람이 판단할 것"],
]) {
  if (!list.length) continue;
  out.push(head);
  out.push("");
  for (const f of list) {
    out.push(`**[${AX[f.axis]}] ${f.title}**`);
    out.push("");
    out.push(f.detail.split("\n").map((l) => `> ${l}`).join("\n"));
    out.push("");
  }
}

out.push("---");
out.push("");
if (skipped.length) {
  out.push("### 검사하지 못한 것 — 조용히 넘기지 않습니다");
  out.push("");
  for (const s of skipped) out.push(`- ${s}`);
  out.push("");
}
out.push(
  "**이 검사는 기계로 판정 가능한 것만 봅니다.** 판단이 필요한 것(계약 변경 여부, 시안 수용 기준 부합, 심각도 가늠)은 하지 않으므로, " +
    "**걸린 것이 없다고 게이트를 통과한 것은 아닙니다.** 판단 검사가 필요하면 세션에서 `/pr-gate` 를 부르십시오(추가 비용 없음).",
);
if (!STRICT) {
  out.push("");
  out.push("도입 1단계라 차단하지 않습니다. 오탐률을 관찰한 뒤 `--strict` 로 올립니다.");
}

const report = out.join("\n");
console.log(report);
if (process.env.GITHUB_STEP_SUMMARY) {
  try {
    const { appendFileSync } = await import("node:fs");
    appendFileSync(process.env.GITHUB_STEP_SUMMARY, report + "\n");
  } catch {}
}

process.exit(STRICT && blocks.length > 0 ? 1 : 0);
