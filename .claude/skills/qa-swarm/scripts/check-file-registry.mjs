#!/usr/bin/env node
// ─────────────────────────────────────────────────────────────────────────
// 반발산 게이트 — 등록부에 없는 신규 파일을 잡는다. (범용: 어느 프로젝트나)
//
// 등록부(registry) = "존재해도 되는 파일 목록". 형식은 **첫 컬럼이 백틱 경로인 마크다운 표**.
//   - 전용 파일(예: `qa/file-registry.md`)이든, 기존 문서(예: doc-governance식 표)든 동일하게 파싱.
//   - `.../` 로 끝나는 항목 = 디렉토리 prefix(그 아래 전부 허가).
// 등록부 위치는 `--registry <path>` 로 지정. 미지정이면 관례 후보를 탐색하고,
//   **하나도 없으면 게이트는 비활성(no-op, exit 0)** — 프로젝트가 등록부를 정의해야 켜진다.
// 검사 범위(scope)도 프로젝트가 `--scope docs,...` 로 지정(기본 docs). 코드(app/·src/ 등)는 제외 권장.
//
// 모드: (기본) 경고 exit 0 · --strict 미등록 있으면 exit 1(머지 차단).
// 사용: node check-file-registry.mjs [--strict] [--registry <path>] [--scope docs,...]
// ─────────────────────────────────────────────────────────────────────────
import { existsSync, readFileSync } from "node:fs";
import { execSync } from "node:child_process";

const args = process.argv.slice(2);
const strict = args.includes("--strict");
const valOf = (f) => { const i = args.indexOf(f); return i >= 0 && args[i + 1] ? args[i + 1] : null; };

// 1) 등록부 위치 결정 — 명시 우선, 없으면 관례 후보 탐색
const REGISTRY_CANDIDATES = ["qa/file-registry.md", "docs/file-registry.md", "docs/doc-governance.md"];
const regPath = valOf("--registry") || valOf("--governance") || REGISTRY_CANDIDATES.find((p) => existsSync(p));

if (!regPath || !existsSync(regPath)) {
  console.log("ℹ 등록부 미설정 — 반발산 게이트 비활성(no-op). 프로젝트가 --registry <표파일> 로 등록부를 정의하면 켜집니다.");
  process.exit(0); // 맨 프로젝트에서 안전하게 통과
}

// 2) 등록부 파싱 — 표 첫 칸의 백틱 경로만(다른 컬럼 서식 변화에 안 깨짐)
const registered = new Set();
for (const line of readFileSync(regPath, "utf8").split("\n")) {
  const m = line.match(/^\s*\|\s*`([^`]+)`/);
  if (m) registered.add(m[1].replace(/\/+$/, "/"));
}
if (registered.size === 0) {
  console.log(`ℹ 등록부(${regPath})에서 백틱 경로를 못 찾음 — 게이트 비활성. 표 첫 컬럼을 \`경로\` 형식으로 두세요.`);
  process.exit(0);
}
const dirs = [...registered].filter((p) => p.endsWith("/"));
const files = new Set([...registered].filter((p) => !p.endsWith("/")));
const isRegistered = (p) => files.has(p) || dirs.some((d) => p === d.slice(0, -1) || p.startsWith(d));

// 3) 검사 대상 — git 추적 파일 중 scope 안. README·아카이브는 관용.
const scope = (valOf("--scope") || "docs").split(",").map((s) => s.trim()).filter(Boolean);
const IGNORE = [/(^|\/)README\.md$/, /(^|\/)_archive\//];
const tracked = execSync("git ls-files", { encoding: "utf8" }).split("\n").filter(Boolean);
const candidates = tracked.filter(
  (p) => scope.some((s) => p === s || p.startsWith(s.endsWith("/") ? s : s + "/")) && !IGNORE.some((re) => re.test(p)),
);
const unregistered = candidates.filter((p) => !isRegistered(p));

if (unregistered.length === 0) {
  console.log(`✓ 등록부 정합 — ${scope.join(", ")} 아래 미등록 파일 없음 (등록부 ${regPath}, 항목 ${registered.size})`);
  process.exit(0);
}
console.log(`\n${strict ? "✗" : "⚠"} 등록부에 없는 파일 ${unregistered.length}개 (등록부: ${regPath}):`);
for (const p of unregistered) console.log(`   - ${p}`);
console.log(`\n→ 새 파일은 ${regPath} 표에 \`경로\` 한 줄로 등록 후 다시 올리세요.` + (strict ? "" : `\n(경고 모드: 지금은 통과. 등록부 정비 후 --strict 로 차단.)`));
process.exit(strict ? 1 : 0);
