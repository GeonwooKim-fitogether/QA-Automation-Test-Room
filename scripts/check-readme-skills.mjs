#!/usr/bin/env node
// ─────────────────────────────────────────────────────────────────────────
// README 스킬 표 드리프트 검사 (창고 전용)
//
// 문제: .claude/skills/ 에 스킬 폴더가 추가됐는데 README "포함된 스킬" 표에
//       행을 안 넣으면, 첫 페이지가 실제 스킬 목록과 조용히 어긋난다.
// 이 검사: 스킬 폴더마다 README 표에 그 이름(백틱)이 있는지 대조하고, 빠진 게
//          있으면 알린다. **이름만** 검사한다 — 한 줄 설명은 사람이 쓴다
//          (무엇을 강조해 한국어로 어떻게 풀지는 기계가 판단하지 못하기 때문).
//
// 모드:
//   (기본) 경고 — 빠진 스킬 나열, exit 0.
//   --strict     — 빠진 게 있으면 exit 1(머지 차단).
//
// 사용: node scripts/check-readme-skills.mjs [--strict]
// ─────────────────────────────────────────────────────────────────────────
import { readFileSync, readdirSync, existsSync, statSync } from "node:fs";

const SKILLS_DIR = ".claude/skills";
const README = "README.md";
const strict = process.argv.includes("--strict");

// 1) 실제 스킬 폴더 목록 = SKILL.md 를 가진 디렉토리
let skillDirs = [];
try {
  skillDirs = readdirSync(SKILLS_DIR).filter((n) => {
    const p = `${SKILLS_DIR}/${n}`;
    return statSync(p).isDirectory() && existsSync(`${p}/SKILL.md`);
  });
} catch {
  console.error(`✗ 스킬 폴더를 못 읽음: ${SKILLS_DIR}`);
  process.exit(strict ? 1 : 0);
}

// 2) README "포함된 스킬" 표에서 백틱 스킬명 수집
let readme = "";
try {
  readme = readFileSync(README, "utf8");
} catch {
  console.error(`✗ README를 못 읽음: ${README}`);
  process.exit(strict ? 1 : 0);
}
const listed = new Set();
let inTable = false;
for (const line of readme.split("\n")) {
  if (/^##\s.*포함된 스킬/.test(line)) {
    inTable = true;
    continue;
  }
  if (inTable && /^##\s/.test(line)) break; // 다음 ## 섹션에서 종료
  if (inTable) {
    const m = line.match(/^\s*\|\s*`([^`]+)`/); // 표 행 첫 칸의 백틱 이름
    if (m) listed.add(m[1]);
  }
}

// 3) 판정 — 폴더엔 있는데 표엔 없는 스킬
const missing = skillDirs.filter((s) => !listed.has(s)).sort();

if (missing.length === 0) {
  console.log(
    `✓ README 스킬 표 정합 — 스킬 폴더 ${skillDirs.length}개 모두 등재 (표 항목 ${listed.size})`,
  );
  process.exit(0);
}

console.log(
  `\n${strict ? "✗" : "⚠"} README "포함된 스킬" 표에 빠진 스킬 ${missing.length}개:`,
);
for (const s of missing) console.log(`   - ${s}`);
console.log(
  `\n→ README.md 의 "📚 포함된 스킬" 표에 "\`${missing[0]}\` | (한 줄 설명)" 행을 추가하세요.` +
    `\n  이름은 이 검사가 자동으로 잡지만, 설명 문구는 사람이 한국어로 씁니다.` +
    (strict ? "" : `\n(경고 모드: 지금은 통과. --strict 로 차단 전환.)`),
);
process.exit(strict ? 1 : 0);
