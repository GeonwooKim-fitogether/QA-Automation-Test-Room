// status-md.mjs — adapter for projects that track work in a single root
// STATUS.md instead of the NPI doc convention (meta/ + ai-npi/).
//
// Built for repos like Hardware-Team-System whose STATUS.md is:
//   - "## N. 제목" sections (plus "### A · Items" subsections) = lanes
//   - markdown table rows (| 항목 | 상태 | 근거 |) and top-level bullets = cards
//   - a status vocabulary the file itself declares:
//       ✅완료 · 🟡부분 · ❌미착수 · ⏸️의도적 보류  (+ ⧖ 검증 대기 · ⚠️ 주의 · 🔀 열린 결정)
//   - 🔀 lines and "다음 결정 포인트" items = pending decisions (Director queue)
//
// Returns the exact same shape as the npi-docs adapter (decisions,
// decisionQueue, worklists, progress, closure, reReview, specOneLiners,
// gitActivity, docs) plus `suggestedConfig` — lanes/workPackages derived from
// the document structure so the per-project config file does not have to
// mirror STATUS.md item-by-item (a hand-maintained mirror would drift).
//
// Parser contract: NEVER crash on prose. Lines that look like items but carry
// no status marker are skipped and COUNTED — the count is logged to stderr so
// silent drops are impossible.
//
// Column mapping (buildDashboardData derives columns from these signals):
//   Done         ← closure {verdict:"closed"}        (✅ / 배포완료)
//   Verification ← progress {done:1, total:1}        (⧖ / ⚠️ / ✅ 작업완료 — 남은 확인 있음)
//   Development  ← progress {done:1, total:2}        (🟡 부분 구현. 1/2는 실측치가 아니라
//                                                     "부분 완료" 표시용 — STATUS.md에는
//                                                     항목별 태스크 수가 없다)
//   Backlog      ← no signal                         (❌ / ⏸️ / ❓)
//
// Pure read-only. Node built-ins only.

import fs from "node:fs/promises";
import path from "node:path";
import { execFile } from "node:child_process";
import { promisify } from "node:util";

const execFileP = promisify(execFile);

// ---------------------------------------------------------------------------
// git snapshot (same logic as npi-docs — duplicated to keep adapters independent)
// ---------------------------------------------------------------------------

const GIT_PRETTY = "%h%x09%an%x09%ar%x09%aI%x09%s";

async function safeGit(projectRoot, args) {
  try {
    const { stdout } = await execFileP("git", args, { cwd: projectRoot });
    return stdout.trim();
  } catch {
    return null;
  }
}

async function loadGitActivity(projectRoot) {
  const last = await safeGit(projectRoot, ["log", "-1", `--pretty=format:${GIT_PRETTY}`]);
  if (!last) return null;
  const [sha, author, relativeTime, isoTime, ...subjectParts] = last.split("\t");
  if (!sha) return null;
  const [branchOut, statusOut] = await Promise.all([
    safeGit(projectRoot, ["rev-parse", "--abbrev-ref", "HEAD"]),
    safeGit(projectRoot, ["status", "--porcelain"]),
  ]);
  return {
    sha,
    author,
    relativeTime,
    isoTime,
    subject: subjectParts.join("\t"),
    branch: branchOut ?? undefined,
    dirtyFiles: statusOut
      ? statusOut.split("\n").filter((l) => l.trim().length > 0).length
      : undefined,
  };
}

// ---------------------------------------------------------------------------
// small text helpers
// ---------------------------------------------------------------------------

const DATE_RE = /\b(\d{4}-\d{2}-\d{2})\b/;

/** Strip markdown decorations (bold, strike, code) but keep the words. */
function stripMd(s) {
  return s
    .replace(/\*{2}([^*]+)\*{2}/g, "$1")
    .replace(/~~([^~]+)~~/g, "$1")
    .replace(/`([^`]+)`/g, "$1")
    .trim();
}

/** Remove status marker characters + leading list/arrow punctuation. */
function stripMarkers(s) {
  return s
    .replace(/[✅🟡❌⏸❓⚠🔀⭐✚★📦]|⧖|️/g, "")
    .replace(/^[\s↳·\-–—:]+/, "")
    .replace(/\s+/g, " ")
    .trim();
}

function truncate(s, max) {
  if (s.length <= max) return s;
  return s.slice(0, max - 1) + "…";
}

/**
 * Item title: prefer a **bold** run only when it LEADS the text (STATUS.md
 * items open with a bold name — "**BOM Redline**(0014): …", "↳ **팀 업무 보드…**").
 * A bold run buried mid-sentence is emphasis, not the item's name, so those
 * fall back to the head of the text up to a separator.
 */
function extractTitle(text) {
  const bold = /\*{2}([^*]+)\*{2}/.exec(text);
  const leadingBold = bold && bold.index <= 4; // allows "↳ ", "✅ " etc. before it
  let base = leadingBold ? bold[1] : text;
  base = stripMarkers(stripMd(base));
  if (!leadingBold) {
    const cut = base.search(/ — |: /);
    if (cut > 8) base = base.slice(0, cut);
  }
  return truncate(base, 90) || "(제목 없음)";
}

// ---------------------------------------------------------------------------
// status classification
// ---------------------------------------------------------------------------

/**
 * Map a status text (the 상태 cell of a table row, or the whole bullet line)
 * to a board column. Precedence matters:
 *   🔀 → open decision (queue, handled by caller)
 *   ⧖ → Verification    (something remains to verify, even next to ✅)
 *   검토대기 → Verification (규칙 7-1: PR 리뷰 대기)
 *   작업완료 without 배포완료 → Verification (규칙 7-1: 작업완료 ≠ 완료)
 *   🟡 → Development     (부분 구현)
 *   ⚠️ → Verification    (known issue awaiting attention/decision follow-up)
 *   ✅ / 배포완료 → Done
 *   ❌ ⏸️ ❓ → Backlog
 */
function classifyStatus(text) {
  if (text.includes("🔀")) return { kind: "queue" };
  if (text.includes("⧖")) return { kind: "card", column: "Verification", word: "검증 대기" };
  if (text.includes("검토대기"))
    return { kind: "card", column: "Verification", word: "검토대기 — 리뷰 대기" };
  if (text.includes("작업완료") && !text.includes("배포완료"))
    return { kind: "card", column: "Verification", word: "작업완료 — 배포 확인 대기" };
  if (text.includes("🟡")) return { kind: "card", column: "Development", word: "부분 구현" };
  if (text.includes("⚠")) return { kind: "card", column: "Verification", word: "주의 — 후속 확인 필요" };
  if (text.includes("✅") || text.includes("배포완료"))
    return { kind: "card", column: "Done", word: text.includes("배포완료") ? "배포완료" : "완료" };
  if (text.includes("❌") || text.includes("⏸") || text.includes("❓"))
    return { kind: "card", column: "Backlog", word: "미착수/보류" };
  return null;
}

// ---------------------------------------------------------------------------
// STATUS.md parser
// ---------------------------------------------------------------------------

const TABLE_SEP_RE = /^\|[\s\-:|]+\|\s*$/;
const HEADING_RE = /^(#{1,3})\s+(.+)$/;
const TOP_ITEM_RE = /^(?:[-*]|\d+\.)\s+(.+)$/;
const NESTED_ITEM_RE = /^\s+(?:[-*]|\d+\.)\s+(.+)$/;

/** Compact code prefix for a section: "## 0. …" → "§0", "### A · …" → "§2A". */
function sectionCode(level, headingText, lastTopNumber) {
  const num = /^(\d+(?:-\d+)?)\./.exec(headingText);
  if (num) return `§${num[1]}`;
  const letter = /^([A-Z])\s*·/.exec(headingText);
  if (letter && level === 3 && lastTopNumber !== null) return `§${lastTopNumber}${letter[1]}`;
  if (/최근/.test(headingText)) return "§REC";
  return null; // caller assigns a sequential fallback
}

function cleanHeading(text) {
  return truncate(stripMarkers(stripMd(text)), 60);
}

/**
 * Parse STATUS.md into sections → items + queue entries.
 * Returns { sections, queue, stats } where sections is an ordered array of
 * { code, label, items: [{code, title, column, statusWord, date, fullText, statusText}] }.
 */
function parseStatusMd(text) {
  const lines = text.split(/\r?\n/);
  const sections = [];
  const queue = [];
  const stats = { cards: 0, queue: 0, skippedNoMarker: 0, skippedQuoteOrFence: 0 };

  let current = null;       // current section accumulator
  let lastTopNumber = null; // "## 2." → 2, for lettered subsections
  let fallbackSeq = 0;
  let inFence = false;
  let queueSeq = 0;

  const isSeparatorNext = (i) => {
    for (let j = i + 1; j < lines.length; j++) {
      const l = lines[j];
      if (!l.trim()) continue;
      return TABLE_SEP_RE.test(l);
    }
    return false;
  };

  const openSection = (level, headingText) => {
    const numMatch = /^(\d+)\./.exec(headingText);
    if (level === 2 && numMatch) lastTopNumber = parseInt(numMatch[1], 10);
    let code = sectionCode(level, headingText, lastTopNumber);
    if (!code) code = `§X${++fallbackSeq}`;
    current = {
      code,
      label: cleanHeading(headingText),
      isDecisionSection: /결정 포인트/.test(headingText),
      isBacklogSection: /백로그/.test(headingText),
      items: [],
    };
    sections.push(current);
  };

  const addQueueEntry = (rawText) => {
    queueSeq += 1;
    const cleaned = stripMarkers(stripMd(rawText));
    queue.push({
      id: `SQ-${String(queueSeq).padStart(3, "0")}`,
      date: DATE_RE.exec(rawText)?.[1],
      topic: truncate(cleaned, 80),
      decisionNeeded: truncate(cleaned, 140),
      defaultAction: "PENDING — 대표 판단 대기 (STATUS.md 열린 결정)",
      pending: true,
      isHcp: false,
    });
    stats.queue += 1;
  };

  const addCard = (itemText, statusText) => {
    const cls = classifyStatus(statusText);
    if (!cls) {
      // No status marker. In an explicit backlog / decision section the items
      // are still real work / decisions; elsewhere it is prose — skip + count.
      if (current?.isDecisionSection) { addQueueEntry(itemText); return; }
      if (current?.isBacklogSection) {
        pushItem(itemText, statusText, { column: "Backlog", word: "미착수" });
        return;
      }
      stats.skippedNoMarker += 1;
      return;
    }
    if (cls.kind === "queue") { addQueueEntry(itemText); return; }
    pushItem(itemText, statusText, { column: cls.column, word: cls.word });
  };

  const pushItem = (itemText, statusText, { column, word }) => {
    if (!current) return;
    const n = current.items.length + 1;
    current.items.push({
      code: `${current.code}-${String(n).padStart(2, "0")}`,
      sectionLabel: current.label,
      title: extractTitle(itemText),
      column,
      statusWord: word,
      date: DATE_RE.exec(statusText)?.[1] ?? DATE_RE.exec(itemText)?.[1],
      fullText: truncate(stripMarkers(stripMd(itemText)), 240),
      statusText: truncate(stripMarkers(stripMd(statusText)), 120),
    });
    stats.cards += 1;
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    if (line.trimStart().startsWith("```")) { inFence = !inFence; continue; }
    if (inFence) { stats.skippedQuoteOrFence += 1; continue; }

    const h = HEADING_RE.exec(line);
    if (h) {
      if (h[1].length >= 2) openSection(h[1].length, h[2].trim());
      continue;
    }

    if (/^\s*>/.test(line)) { stats.skippedQuoteOrFence += 1; continue; }

    // Table row?
    if (/^\|.*\|\s*$/.test(line)) {
      if (TABLE_SEP_RE.test(line)) continue;
      if (isSeparatorNext(i)) continue; // header row of a table
      const cells = line.replace(/^\|/, "").replace(/\|\s*$/, "").split("|").map((c) => c.trim());
      if (cells.length === 0) continue;
      const titleCell = cells[0];
      // Status is read from the non-title cells so stray ✅ inside a long
      // description cell can't misclassify the row. Single-cell rows fall
      // back to the whole row.
      const statusText = cells.length > 1 ? cells.slice(1).join(" · ") : cells[0];
      if (line.includes("🔀")) { addQueueEntry(titleCell); continue; }
      addCard(titleCell, statusText);
      continue;
    }

    // Top-level list item → card candidate (status scanned over the whole line).
    const top = TOP_ITEM_RE.exec(line);
    if (top) { addCard(top[1], top[1]); continue; }

    // Nested list item → only mined for open decisions (🔀); details stay prose.
    const nested = NESTED_ITEM_RE.exec(line);
    if (nested) {
      if (nested[1].includes("🔀")) addQueueEntry(nested[1]);
      continue;
    }
  }

  return { sections, queue, stats };
}

// ---------------------------------------------------------------------------
// lane assembly — sections → at most 5 Product lanes (frozen-UI contract)
// ---------------------------------------------------------------------------
//
// The prebuilt UI's scope resolver hardcodes the visible lane ids
// (phase1..phase5, meta, control — see ui-src/utils/scope.ts ALL_LANES).
// Tickets on any other lane id are silently filtered out of the root view.
// Since the UI is frozen by design, the adapter must fit the document into
// at most five Product lanes:
//   1. subsections (### A · Items) merge into their parent ## section
//   2. if more than 5 sections remain, the 4 with the most items keep
//      dedicated lanes (document order preserved) and the rest fold into a
//      fifth "그 외 절" lane. Card codes (§2B-01) and the detail panel's
//      section label keep the original provenance either way.

function mergeSubsections(sections) {
  const byTop = new Map(); // top key ("2", "REC") → merged section
  for (const sec of sections) {
    const top = /^§(\d+)/.exec(sec.code)?.[1] ?? sec.code.replace(/^§/, "");
    if (!byTop.has(top)) {
      byTop.set(top, { code: `§${top}`, label: sec.label, items: [] });
    }
    // Note: the FIRST section seen for a top key sets the label, which is the
    // parent "## N." heading when subsections follow it — the general label wins.
    byTop.get(top).items.push(...sec.items);
  }
  return [...byTop.values()].filter((s) => s.items.length > 0);
}

function buildLanes(sections) {
  const merged = mergeSubsections(sections);
  let groups;
  if (merged.length <= 5) {
    groups = merged;
  } else {
    const ranked = [...merged].sort((a, b) => b.items.length - a.items.length);
    const dedicated = new Set(ranked.slice(0, 4));
    const rest = merged.filter((s) => !dedicated.has(s));
    groups = [
      ...merged.filter((s) => dedicated.has(s)), // document order
      {
        code: "§ETC",
        label: truncate(`그 외 절 (${rest.map((s) => s.code).join(" · ")})`, 60),
        items: rest.flatMap((s) => s.items),
      },
    ];
  }
  return groups.map((g, i) => ({ laneId: `phase${i + 1}`, ...g }));
}

// ---------------------------------------------------------------------------
// assembly — parsed items → adapter-contract signals
// ---------------------------------------------------------------------------

export async function collect(projectRoot = process.cwd()) {
  const statusPath = path.join(projectRoot, "STATUS.md");
  let text = null;
  try {
    text = await fs.readFile(statusPath, "utf-8");
  } catch {
    console.error(`[status-md] ${statusPath} not found — returning an empty board.`);
  }

  const { sections, queue, stats } = text
    ? parseStatusMd(text)
    : { sections: [], queue: [], stats: { cards: 0, queue: 0, skippedNoMarker: 0, skippedQuoteOrFence: 0 } };

  const worklists = {};
  const progress = {};
  const closure = {};
  const lanes = [];
  const workPackages = [];

  for (const laneSec of buildLanes(sections)) {
    const laneId = laneSec.laneId; // phase1..phase5 — the frozen UI's lane vocabulary
    lanes.push({ id: laneId, label: laneSec.label, parent: "Product" });

    for (const it of laneSec.items) {
      workPackages.push({
        laneId,
        wp: it.code,
        legacy: it.code,
        title: it.title,
        plainTitle: truncate(it.title, 56),
      });

      // Detail-panel truth: one pseudo-task carrying the real STATUS.md text.
      // Only attached when the item has a stronger signal (closure/progress),
      // because a bare worklist would bump a Backlog item to Plan.
      const task = {
        id: "T-1",
        index: 1,
        description: it.fullText,
        phase: it.sectionLabel,
        status:
          it.column === "Done" ? "done"
          : it.column === "Development" ? "in-progress"
          : "pending",
        statusRaw: it.statusText,
      };

      if (it.column === "Done") {
        closure[it.code] = {
          verdict: "closed",
          decisionId: it.statusWord,
          date: it.date,
          title: it.title,
        };
        worklists[it.code] = wrapWorklist(it.code, [task]);
      } else if (it.column === "Verification") {
        progress[it.code] = { done: 1, total: 1, lastSha: "", lastIso: "", raw: it.statusText, commitCount: 0 };
        worklists[it.code] = wrapWorklist(it.code, [task]);
      } else if (it.column === "Development") {
        // 1/2 is NOT a measured task ratio — STATUS.md has no per-item task
        // counts. It is the minimal signal that places the card in the
        // Development column ("부분 구현"). The raw status text rides along.
        progress[it.code] = { done: 1, total: 2, lastSha: "", lastIso: "", raw: it.statusText, commitCount: 0 };
        worklists[it.code] = wrapWorklist(it.code, [task]);
      }
      // Backlog: no signal at all.
    }
  }

  lanes.push({ id: "control", label: "Control Plane", parent: "Control" });

  const gitActivity = await loadGitActivity(projectRoot).catch(() => null);

  const projectName = path.basename(projectRoot);

  console.error(
    `[status-md] STATUS.md parsed: ${stats.cards} cards · ${stats.queue} open decisions · ` +
    `${stats.skippedNoMarker} list/table lines skipped (no status marker — headers/prose) · ` +
    `${stats.skippedQuoteOrFence} blockquote/fence lines ignored`,
  );

  return {
    decisions: [], // STATUS.md has no D-NNN decision log — honest empty.
    decisionQueue: queue,
    worklists,
    progress,
    closure,
    reReview: new Set(),
    specOneLiners: {},
    gitActivity,
    docs: text
      ? [{
          id: "STATUS.md",
          relPath: "STATUS.md",
          absPath: statusPath,
          title: "STATUS.md",
          kind: "status",
          scopes: [{ kind: "root" }],
        }]
      : [],
    sourceFile: "STATUS.md + git log",
    suggestedConfig: {
      projectName,
      lanes,
      workPackages,
    },
  };
}

function wrapWorklist(code, tasks) {
  const done = tasks.filter((t) => t.status === "done").length;
  const inProgress = tasks.filter((t) => t.status === "in-progress").length;
  const pending = tasks.filter((t) => t.status === "pending").length;
  return {
    code,
    relPath: "STATUS.md",
    absPath: null,
    tasks,
    totals: {
      total: tasks.length,
      done,
      inProgress,
      pending,
      blocked: 0,
      notStarted: tasks.length - done - inProgress - pending,
    },
  };
}
