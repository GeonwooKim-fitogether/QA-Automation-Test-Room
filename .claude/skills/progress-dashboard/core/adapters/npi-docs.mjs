// npi-docs.mjs — default "auto-fill" data source for the progress-dashboard
// skill. ESM port of the SaaS-platform server-side data loaders.
//
// This is a faithful, mechanical port of:
//   loadDecisions, loadDecisionQueue, loadWorklists, loadGitActivity,
//   loadCommitActivity, loadReReview, loadSpecOneLiners, loadDocs,
//   deriveProgress, deriveClosure
// plus the assembly previously done by loadState.ts.
//
// The originals hardcoded `const REPO_ROOT = process.cwd()`. Here every
// loader accepts a `projectRoot` argument so the skill can target any repo.
//
// Pure read-only. Failures degrade gracefully. Node built-ins only.

import fs from "node:fs/promises";
import { promises as fsPromises } from "node:fs";
import path from "node:path";
import { execFile } from "node:child_process";
import { promisify } from "node:util";

const execFileP = promisify(execFile);

// ===========================================================================
// loadDecisions — `meta/decisions.md` parser.
// ===========================================================================

const DECISIONS_HEADING_RE = /^###\s+(D-\d{3})\s*[—\-]\s*(.+)$/gm;
const DECISIONS_DATE_RE = /\*\*일자\*\*\s*[:：]\s*(\d{4}-\d{2}-\d{2})/;
const DECISIONS_WP_CODE_RE = /\b(WP-\d{3}|F-\d{3})\b/gi;

/** Decide verdict from title + early body. Order matters — Reject wins. */
function detectVerdict(text) {
  const t = text.toLowerCase();
  if (/\brejected?\b|\babandon(ed)?\b|✗|🚫\s*reject/.test(t)) return "rejected";
  if (/\bamended?\b|\brevised?\b|\bsupersed(ed|es)?\b/.test(t)) return "amended";
  if (/\bfinal cp accepted\b|\baccepted?\b|\bapproved?\b|\baccept with conditions\b|✅/.test(t))
    return "accepted";
  return "neutral";
}

/**
 * True only when this entry closes the *whole* WP (implementation cycle),
 * not just a sub-phase.
 */
function detectClosure(text) {
  const t = text.toLowerCase();
  // Strongest signal: "Implementation Final CP" + Accepted means a real WP closure.
  if (/\bimplementation\s+final\s*cp\s+accepted\b/.test(t)) return true;
  // "Phase N CLOSED" but NOT "Phase N Planning CLOSED" / not "Planning Final CP"
  if (/\bphase\s*\d+\s*closed\b/.test(t)) {
    if (!/\bplanning\s+closed\b/.test(t) && !/\bplanning\s+final\s*cp\b/.test(t)) return true;
  }
  // Direct "WP-NNN CLOSED" pattern (rare but unambiguous)
  if (/\bwp[-\s]*\d+\s+closed\b/.test(t)) return true;
  return false;
}

function extractWpCodesDecisions(text) {
  const out = new Set();
  let m;
  while ((m = DECISIONS_WP_CODE_RE.exec(text))) out.add(m[1].toUpperCase());
  return Array.from(out);
}

async function loadDecisions(projectRoot) {
  const DECISIONS_PATH = path.join(projectRoot, "meta", "decisions.md");
  let text;
  try {
    text = await fs.readFile(DECISIONS_PATH, "utf-8");
  } catch {
    return [];
  }

  // Find every "### D-NNN — title" heading + its body (up to the next ### or EOF).
  const matches = [];
  let h;
  // Reset lastIndex since HEADING_RE is module-scoped /g.
  DECISIONS_HEADING_RE.lastIndex = 0;
  while ((h = DECISIONS_HEADING_RE.exec(text))) {
    matches.push({ id: h[1].toUpperCase(), title: h[2].trim(), start: h.index });
  }
  if (matches.length === 0) return [];

  const out = [];
  for (let i = 0; i < matches.length; i++) {
    const m = matches[i];
    const next = matches[i + 1];
    const end = next ? next.start : text.length;
    const body = text.slice(m.start, end);
    // Limit verdict/WP scan to a reasonable head so we don't pick up
    // unrelated WP refs from a long entry's "참조" footer.
    const head = body.slice(0, 1500);
    const dateMatch = DECISIONS_DATE_RE.exec(body);
    out.push({
      id: m.id,
      title: m.title,
      date: dateMatch?.[1],
      verdict: detectVerdict(m.title + " " + head),
      wps: extractWpCodesDecisions(m.title + " " + head),
      titleWps: extractWpCodesDecisions(m.title),
      closes: detectClosure(m.title + " " + head),
    });
  }
  return out;
}

// ===========================================================================
// loadDecisionQueue — `meta/decision-queue.md` parser.
// ===========================================================================

const QUEUE_ROW_RE =
  /^\|\s*(?:\*\*)?(?:~~)?(DQ-\d{3})(?:~~)?(?:\*\*)?\s*\|\s*([^|]*?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|/i;

// "~~DQ-027~~" strikethrough = resolved, never a pending card.
const QUEUE_STRIKETHROUGH_RE = /~~\s*DQ-\d{3}\s*~~/i;

const QUEUE_PENDING_RE = /\bPENDING\b|\bTBD\b|대기/i;
const QUEUE_RESOLVED_RE = /✅|\bRESOLVED\b|\bCONFIRMED\b/i;
const QUEUE_HCP_RE = /\b(HCP-[A-Z][A-Z0-9-]*)/i;
const QUEUE_DATE_RE = /\b(\d{4}-\d{2}-\d{2})\b/;

function stripMd(s) {
  return s
    .replace(/\*{2}([^*]+)\*{2}/g, "$1")
    .replace(/~~([^~]+)~~/g, "$1")
    .replace(/`([^`]+)`/g, "$1")
    .trim();
}

async function loadDecisionQueue(projectRoot) {
  const QUEUE_PATH = path.join(projectRoot, "meta", "decision-queue.md");
  let text;
  try {
    text = await fs.readFile(QUEUE_PATH, "utf-8");
  } catch {
    return [];
  }

  const out = [];
  for (const line of text.split(/\r?\n/)) {
    if (QUEUE_STRIKETHROUGH_RE.test(line)) continue;
    const m = QUEUE_ROW_RE.exec(line);
    if (!m) continue;
    const [, idRaw, dateCell, topic, decisionNeeded, defaultAction, , risk, reviewTiming] = m;
    const id = idRaw.toUpperCase();
    const date = QUEUE_DATE_RE.exec(dateCell)?.[1];
    const defaultActionClean = stripMd(defaultAction);
    // RESOLVED wins over PENDING when both appear in the same cell.
    const resolved = QUEUE_RESOLVED_RE.test(defaultActionClean);
    const pending = !resolved && QUEUE_PENDING_RE.test(defaultActionClean);
    if (!pending) continue;
    const hcpMatch = QUEUE_HCP_RE.exec(`${topic} ${decisionNeeded}`);
    out.push({
      id,
      date,
      topic: stripMd(topic),
      decisionNeeded: stripMd(decisionNeeded),
      defaultAction: defaultActionClean,
      risk: stripMd(risk) || undefined,
      reviewTiming: stripMd(reviewTiming) || undefined,
      pending: true,
      isHcp: Boolean(hcpMatch),
      hcpCode: hcpMatch?.[1].toUpperCase(),
    });
  }
  return out;
}

// ===========================================================================
// loadWorklists — worklist scanner.
// ===========================================================================

// "WP-004_Worklist.md" / "F-001_NPI_Worklist.md" / legacy variants.
const WORKLIST_FILENAME_RE = /^(WP-\d{3}|F-\d{3})(?:_NPI)?_Worklist\.md$/i;

// One row of the canonical Status Matrix table.
const WORKLIST_ROW_RE =
  /^\|\s*\*{0,2}T-(\d{1,3})\*{0,2}\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|/i;

function detectStatus(cell) {
  const raw = cell.trim();
  // Order matters — check explicit emojis first, then plain-text keywords.
  if (/^✅|\bdone\b|\bclosed\b|\bcomplete\b|\bpass\b/i.test(raw))
    return { status: "done", raw };
  if (/^🟢|\bin\s*progress\b|\bimplementing\b|\bactive\b|\bexecuting\b/i.test(raw))
    return { status: "in-progress", raw };
  if (/^🚫|\bblocked\b|\bout-of-cycle\b|\brejected\b/i.test(raw))
    return { status: "blocked", raw };
  if (/^🟡|\bpending\b|\bwaiting\b|\bdirector\b|\breview\b/i.test(raw))
    return { status: "pending", raw };
  // ⚪ and any other unclassified cells default to "not-started" so they
  // never get mistaken for done.
  return { status: "not-started", raw };
}

function stripBoldMarkers(s) {
  // Removes ** wrappers and trailing parenthetical "(본 PR)" annotations
  // so the panel reads cleanly. Keep the rest intact.
  return s.replace(/\*{2}([^*]+)\*{2}/g, "$1").trim();
}

function parseWorklist(text) {
  const tasks = [];
  const seen = new Set();
  for (const line of text.split(/\r?\n/)) {
    const m = WORKLIST_ROW_RE.exec(line);
    if (!m) continue;
    const index = parseInt(m[1], 10);
    const id = `T-${index}`;
    if (seen.has(id)) continue; // Some files include the row twice (header + body) — dedupe by id.
    seen.add(id);
    const description = stripBoldMarkers(m[2]).slice(0, 240);
    const phase = stripBoldMarkers(m[3]);
    const statusCell = m[4];
    const deps = stripBoldMarkers(m[5]) || undefined;
    const { status, raw } = detectStatus(statusCell);
    tasks.push({ id, index, description, phase, status, statusRaw: raw, deps });
  }
  tasks.sort((a, b) => a.index - b.index);
  return tasks;
}

function computeTotals(tasks) {
  let done = 0;
  let inProgress = 0;
  let pending = 0;
  let blocked = 0;
  let notStarted = 0;
  for (const t of tasks) {
    if (t.status === "done") done++;
    else if (t.status === "in-progress") inProgress++;
    else if (t.status === "pending") pending++;
    else if (t.status === "blocked") blocked++;
    else notStarted++;
  }
  return { total: tasks.length, done, inProgress, pending, blocked, notStarted };
}

/** Extract the bare code prefix (WP-NNN or F-NNN) from a worklist filename. */
function codeFromFilename(name) {
  const m = WORKLIST_FILENAME_RE.exec(name);
  return m ? m[1].toUpperCase() : null;
}

async function loadWorklists(projectRoot) {
  const AINPI_DIR = path.join(projectRoot, "ai-npi");
  let entries;
  try {
    entries = await fs.readdir(AINPI_DIR);
  } catch {
    return {};
  }

  const out = {};
  await Promise.all(
    entries.map(async (name) => {
      const code = codeFromFilename(name);
      if (!code) return;
      const relPath = path.posix.join("ai-npi", name);
      const absPath = path.join(AINPI_DIR, name);
      try {
        const text = await fs.readFile(absPath, "utf-8");
        const tasks = parseWorklist(text);
        if (tasks.length === 0) return;
        out[code] = {
          code,
          relPath,
          absPath,
          tasks,
          totals: computeTotals(tasks),
        };
      } catch {
        // Skip unreadable files silently — worklists are optional.
      }
    }),
  );
  return out;
}

// ===========================================================================
// loadGitActivity + loadCommitActivity — `git log` snapshot + commit scanner.
// ===========================================================================

// Tab-separated so the parser never has to deal with author/subject
// containing the same delimiter character.
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
  const subject = subjectParts.join("\t");

  // Best-effort extras — failures don't invalidate the basic record.
  const [branchOut, statusOut] = await Promise.all([
    safeGit(projectRoot, ["rev-parse", "--abbrev-ref", "HEAD"]),
    safeGit(projectRoot, ["status", "--porcelain"]),
  ]);

  return {
    sha,
    author,
    relativeTime,
    isoTime,
    subject,
    branch: branchOut ?? undefined,
    dirtyFiles: statusOut
      ? statusOut.split("\n").filter((l) => l.trim().length > 0).length
      : undefined,
  };
}

const FULL_PRETTY = "%H%x09%aI%x09%s";

/**
 * Pull T-N numbers out of a commit subject, including ranges like
 * "T-5~T-23" (expands to every T in the range) and decimals like "T-23.5"
 * (treated as T-23 — decimals are not in the canonical task scheme).
 */
function extractTaskNumbers(text) {
  const out = new Set();
  const rangeRe = /T-(\d+)(?:\.\d+)?\s*(?:~|\.\.|to)\s*T-(\d+)(?:\.\d+)?/gi;
  let rm;
  while ((rm = rangeRe.exec(text))) {
    const a = parseInt(rm[1], 10);
    const b = parseInt(rm[2], 10);
    if (Number.isFinite(a) && Number.isFinite(b)) {
      const lo = Math.min(a, b);
      const hi = Math.max(a, b);
      for (let n = lo; n <= hi; n++) out.add(n);
    }
  }
  const singleRe = /\bT-(\d+)(?:\.\d+)?\b/gi;
  let sm;
  while ((sm = singleRe.exec(text))) {
    const n = parseInt(sm[1], 10);
    if (Number.isFinite(n)) out.add(n);
  }
  return Array.from(out).sort((a, b) => a - b);
}

/** Pull WP-NNN / F-NNN codes out of a commit subject. */
function extractWpCodesCommits(text) {
  const out = new Set();
  const re = /\b(WP-\d{3}|F-\d{3})\b/gi;
  let m;
  while ((m = re.exec(text))) out.add(m[1].toUpperCase());
  return Array.from(out);
}

async function loadCommitActivity(projectRoot, limit = 100) {
  const log = await safeGit(projectRoot, [
    "log",
    `-${Math.max(1, Math.min(500, limit))}`,
    `--pretty=format:${FULL_PRETTY}`,
  ]);
  if (!log) return [];
  const out = [];
  for (const line of log.split(/\r?\n/)) {
    if (!line) continue;
    const [sha, isoTime, ...subjectParts] = line.split("\t");
    if (!sha) continue;
    const subject = subjectParts.join("\t");
    out.push({
      sha,
      shortSha: sha.slice(0, 7),
      isoTime,
      subject,
      tasks: extractTaskNumbers(subject),
      wps: extractWpCodesCommits(subject),
    });
  }
  return out;
}

// ===========================================================================
// loadReReview — meta/re-review.md parser.
// ===========================================================================

const RE_REVIEW_WP_LINE_RE = /\b(WP-\d{3}|F-\d{3})\b/i;

/**
 * Parse meta/re-review.md → Set of flagged WP codes (uppercased).
 */
async function loadReReview(projectRoot) {
  const RE_REVIEW_PATH = path.join(projectRoot, "meta", "re-review.md");
  let text;
  try {
    text = await fs.readFile(RE_REVIEW_PATH, "utf-8");
  } catch {
    return new Set();
  }
  const out = new Set();
  for (const line of text.split(/\r?\n/)) {
    // Skip comments / headings / quoted prose so the parser focuses on
    // the bare-code lines a flag is supposed to be.
    const trimmed = line.trim();
    if (!trimmed) continue;
    if (trimmed.startsWith("#")) continue;
    if (trimmed.startsWith(">")) continue;
    const m = RE_REVIEW_WP_LINE_RE.exec(trimmed);
    if (m) out.add(m[1].toUpperCase());
  }
  return out;
}

// ===========================================================================
// loadSpecOneLiners — Spec frontmatter "director_one_liner" parser.
// ===========================================================================

// Matches the leading code in any spec filename.
const FILE_CODE_RE = /^(WP-\d{3}|F-\d{3}|IP-\d{3}|M-\d{3}|MetaSprint\d+)/i;

// Frontmatter block at the very top of the file: ---\n ... \n---\n
const FRONTMATTER_RE = /^---\r?\n([\s\S]*?)\r?\n---\r?\n/;

// director_one_liner field within the frontmatter.
const ONE_LINER_RE = /^director_one_liner\s*:\s*['"]?(.+?)['"]?\s*$/m;

/**
 * Read every spec file under ai-npi/ and return a map keyed by canonical
 * WP/IP/MetaSprint code → director-facing one-liner.
 */
async function loadSpecOneLiners(projectRoot) {
  const AINPI_DIR = path.join(projectRoot, "ai-npi");
  let entries;
  try {
    entries = await fs.readdir(AINPI_DIR);
  } catch {
    return {};
  }

  const out = {};
  await Promise.all(
    entries.map(async (name) => {
      const codeMatch = FILE_CODE_RE.exec(name);
      if (!codeMatch) return;
      const code = codeMatch[1].toUpperCase();
      try {
        const text = await fs.readFile(path.join(AINPI_DIR, name), "utf-8");
        const fm = FRONTMATTER_RE.exec(text);
        if (!fm) return;
        const oneLine = ONE_LINER_RE.exec(fm[1]);
        if (!oneLine) return;
        const value = oneLine[1].trim();
        if (!value) return;
        // If multiple spec files exist for the same code, prefer Spec → Design →
        // Brief → Worklist by filename heuristic. Spec wins.
        const existing = out[code];
        const priority = (n) =>
          /_Spec\.md$/i.test(n) ? 3 :
          /_Design\.md$/i.test(n) ? 2 :
          /_Brief\.md$/i.test(n) || /_NPI_Brief\.md$/i.test(n) ? 2 :
          /_Worklist\.md$/i.test(n) ? 1 : 0;
        if (existing && priority(name) <= priority(name)) {
          // existing has equal-or-higher priority — keep existing
          // (this branch is mostly a noop; kept for explicitness)
        }
        out[code] = value;
      } catch {
        // Skip unreadable files silently.
      }
    }),
  );
  return out;
}

// ===========================================================================
// loadDocs — repo markdown / json document scanner mapped to tree scopes.
// ===========================================================================

function lowerKind(s) {
  return s.toLowerCase();
}

const DOC_RULES = [
  // ai-npi/ — Spec / Design / Worklist / Verification (post-v0.3.1 naming)
  {
    dir: "ai-npi",
    pattern: /^WP-(\d{3})_(Spec|Design|Worklist|Verification)\.md$/i,
    build(m) {
      const n = parseInt(m[1], 10);
      return {
        title: `WP-${m[1]} ${m[2]}`,
        kind: lowerKind(m[2]),
        scopes: [
          { kind: "phase", phase: n },
          { kind: "wp", code: `WP-${m[1]}` },
          { kind: "wp", code: `F-${m[1]}` }, // legacy alias
        ],
      };
    },
  },

  // ai-npi/ — Legacy F-NNN_NPI_*.md
  {
    dir: "ai-npi",
    pattern: /^F-(\d{3})_NPI_(Brief|Blueprint|Worklist|Verification)\.md$/i,
    build(m) {
      const n = parseInt(m[1], 10);
      const kindMap = {
        Brief: "brief",
        Blueprint: "blueprint",
        Worklist: "worklist",
        Verification: "verification",
      };
      return {
        title: `F-${m[1]} ${m[2]}`,
        kind: kindMap[m[2]] ?? "other",
        scopes: [
          { kind: "phase", phase: n },
          { kind: "wp", code: `F-${m[1]}` },
          { kind: "wp", code: `WP-${m[1]}` }, // new alias
        ],
      };
    },
  },

  // ai-npi/ — IP (Improvement Package, Meta track)
  {
    dir: "ai-npi",
    pattern: /^IP-(\d{3})_(Spec|Design|Worklist|Verification)\.md$/i,
    build(m) {
      return {
        title: `IP-${m[1]} ${m[2]}`,
        kind: lowerKind(m[2]),
        scopes: [
          { kind: "ip", code: `IP-${m[1]}` },
          { kind: "ip", code: `M-${m[1]}` }, // legacy alias
          { kind: "meta-sprint", n: 1 },     // current assumption (Meta Sprint 1 holds all 3 IPs)
        ],
      };
    },
  },

  // ai-npi/ — Meta Sprint planning entry
  {
    dir: "ai-npi",
    pattern: /^MetaSprint(\d+)_Spec\.md$/i,
    build(m) {
      const n = parseInt(m[1], 10);
      return {
        title: `Meta Sprint ${m[1]} Spec`,
        kind: "meta-sprint-spec",
        scopes: [{ kind: "meta-sprint", n }],
      };
    },
  },

  // ai-npi/ — cross-cutting / root-level conceptual docs
  {
    dir: "ai-npi",
    pattern: /^OPERATING_MODEL\.md$/,
    build() { return { title: "Operating Model", kind: "operating-model", scopes: [{ kind: "root" }] }; },
  },
  {
    dir: "ai-npi",
    pattern: /^domain-ontology\.md$/,
    build() { return { title: "Domain Ontology", kind: "ontology", scopes: [{ kind: "root" }] }; },
  },
  {
    dir: "ai-npi",
    pattern: /^project-brief\.md$/,
    build() { return { title: "Project Brief", kind: "project-brief", scopes: [{ kind: "root" }] }; },
  },
  {
    dir: "ai-npi",
    pattern: /^NPI_(Brief|Blueprint|Worklist)\.md$/i,
    build(m) {
      return {
        title: `NPI ${m[1]} (root)`,
        kind: m[1].toLowerCase() === "blueprint" ? "blueprint" : "brief",
        scopes: [{ kind: "root" }],
      };
    },
  },

  // root — STATUS.md (director-view text mirror)
  {
    dir: ".",
    pattern: /^STATUS\.md$/,
    build() { return { title: "STATUS.md", kind: "status", scopes: [{ kind: "root" }] }; },
  },

  // meta/ — Control Plane records
  {
    dir: "meta",
    pattern: /^decisions\.md$/,
    build() { return { title: "Decisions log (D-NNN)", kind: "decisions", scopes: [{ kind: "control" }, { kind: "root" }] }; },
  },
  {
    dir: "meta",
    pattern: /^decision-queue\.md$/,
    build() { return { title: "Decision Queue (DQ-NNN)", kind: "decision-queue", scopes: [{ kind: "control" }] }; },
  },
  {
    dir: "meta",
    pattern: /^foundry-improvement-log\.md$/,
    build() { return { title: "Foundry Improvement Log (FIL)", kind: "improvement-log", scopes: [{ kind: "control" }] }; },
  },
  {
    dir: "meta",
    pattern: /^session-handoff\.md$/,
    build() { return { title: "Session Handoff", kind: "session-handoff", scopes: [{ kind: "control" }] }; },
  },
  {
    dir: "meta",
    pattern: /^execution-log\.md$/,
    build() { return { title: "Execution Log", kind: "execution-log", scopes: [{ kind: "control" }] }; },
  },

  // dashboard/ — SSoT JSON (handy to inspect raw)
  {
    dir: "dashboard",
    pattern: /^dashboard-state\.json$/,
    build() { return { title: "dashboard-state.json (SSoT)", kind: "state", scopes: [{ kind: "control" }, { kind: "root" }] }; },
  },
];

async function loadDocs(projectRoot) {
  const ROOT = projectRoot;
  const dirs = Array.from(new Set(DOC_RULES.map((r) => r.dir)));
  const out = [];

  for (const dir of dirs) {
    const abs = path.join(ROOT, dir);
    let files;
    try {
      files = await fsPromises.readdir(abs);
    } catch {
      // missing directory is fine — just skip its rules
      continue;
    }
    for (const file of files) {
      for (const rule of DOC_RULES) {
        if (rule.dir !== dir) continue;
        const m = file.match(rule.pattern);
        if (!m) continue;
        const built = rule.build(m);
        const relPath = dir === "." ? file : path.posix.join(dir, file);
        out.push({
          id: relPath,
          relPath,
          absPath: path.join(abs, file),
          title: built.title,
          kind: built.kind,
          scopes: built.scopes,
        });
        break; // first rule wins
      }
    }
  }

  // Stable order — group by kind then by title, so the UI is deterministic.
  const KIND_ORDER = [
    "spec", "design", "worklist", "verification",
    "brief", "blueprint",
    "meta-sprint-spec",
    "operating-model", "project-brief", "ontology",
    "status", "state",
    "decisions", "decision-queue", "improvement-log",
    "session-handoff", "execution-log",
    "other",
  ];
  out.sort((a, b) => {
    const ka = KIND_ORDER.indexOf(a.kind);
    const kb = KIND_ORDER.indexOf(b.kind);
    if (ka !== kb) return ka - kb;
    return a.title.localeCompare(b.title);
  });

  return out;
}

// ===========================================================================
// deriveProgress — auto-derive WP progress from git commits × worklists.
// ===========================================================================

/** "WP-004" → "F-004" alias used by some commit messages. */
function toFAlias(code) {
  const m = code.match(/^WP-(\d{3})$/i);
  return m ? `F-${m[1]}` : code;
}

/** "F-004" → "WP-004" alias used by some commit messages. */
function toWpAlias(code) {
  const m = code.match(/^F-(\d{3})$/i);
  return m ? `WP-${m[1]}` : code;
}

/**
 * Cross-reference commits with worklists to compute the "auto" progress
 * per WP. Returns a record keyed by WP code (uppercased, e.g. "WP-004").
 */
function deriveProgress(commits, worklists) {
  const out = {};

  for (const code of Object.keys(worklists)) {
    const wp = worklists[code];
    const total = wp.totals.total;
    if (total <= 0) continue;

    const seenTasks = new Set();
    let commitCount = 0;
    let latestSha = null;
    let latestIso = null;

    for (const c of commits) {
      // Match WP code OR its legacy F alias.
      const matchesWp = c.wps.includes(code) || c.wps.includes(toFAlias(code)) || c.wps.includes(toWpAlias(code));
      if (!matchesWp) continue;
      commitCount++;
      // git log is newest-first, so the *first* matching commit is latest.
      if (latestSha === null) {
        latestSha = c.shortSha;
        latestIso = c.isoTime;
      }
      for (const t of c.tasks) seenTasks.add(t);
    }

    if (commitCount === 0) continue;

    const maxTask = seenTasks.size > 0 ? Math.max(...seenTasks) : 0;
    const done = Math.min(maxTask, total);

    out[code] = {
      done,
      total,
      lastSha: latestSha ?? "",
      lastIso: latestIso ?? "",
      raw: `T-${done}/${total}`,
      commitCount,
    };
  }

  return out;
}

// ===========================================================================
// deriveClosure — cross-reference decisions × worklists → closure per WP.
// ===========================================================================

/** Parse "D-022" → 22 for ordering. Falls back to 0 on parse fail. */
function ordinal(id) {
  const m = id.match(/^D-(\d+)$/i);
  return m ? parseInt(m[1], 10) : 0;
}

/** "WP-002" ↔ "F-002" alias pair. Returns both forms for any input. */
function aliasPair(code) {
  const u = code.toUpperCase();
  const wp = u.match(/^WP-(\d{3})$/);
  if (wp) return [u, `F-${wp[1]}`];
  const f = u.match(/^F-(\d{3})$/);
  if (f) return [u, `WP-${f[1]}`];
  return [u];
}

function deriveClosure(decisions, wpCodes) {
  const out = {};
  if (!decisions.length || !wpCodes.length) return out;

  // Sort decisions newest-first by ordinal so the first matching entry wins.
  const sorted = [...decisions].sort((a, b) => ordinal(b.id) - ordinal(a.id));

  // Expand input codes to cover both alias forms (WP-002 ↔ F-002), and
  // dedupe to avoid redundant scans.
  const allCodes = Array.from(new Set(wpCodes.flatMap(aliasPair)));

  for (const wp of allCodes) {
    for (const d of sorted) {
      // Subject-only match: closure must come from an entry whose TITLE
      // mentions this WP (or its alias).
      const titleAlias = d.titleWps.flatMap(aliasPair);
      if (!titleAlias.includes(wp)) continue;
      if (d.verdict === "rejected") {
        out[wp] = { verdict: "rejected", decisionId: d.id, date: d.date, title: d.title };
        break;
      }
      if ((d.verdict === "accepted" || d.verdict === "amended") && d.closes) {
        out[wp] = { verdict: "closed", decisionId: d.id, date: d.date, title: d.title };
        break;
      }
      // Other accepted entries don't close the WP — keep scanning older entries.
    }
  }
  return out;
}

// ===========================================================================
// collect — assembly previously done by loadState.ts.
// ===========================================================================

export async function collect(projectRoot = process.cwd()) {
  const [worklists, gitActivity, commitActivity, decisions, decisionQueue, reReview, specOneLiners, docs] =
    await Promise.all([
      loadWorklists(projectRoot).catch((err) => {
        console.error("[progress-dashboard] loadWorklists failed:", err);
        return {};
      }),
      loadGitActivity(projectRoot).catch((err) => {
        console.error("[progress-dashboard] loadGitActivity failed:", err);
        return null;
      }),
      loadCommitActivity(projectRoot, 100).catch((err) => {
        console.error("[progress-dashboard] loadCommitActivity failed:", err);
        return [];
      }),
      loadDecisions(projectRoot).catch((err) => {
        console.error("[progress-dashboard] loadDecisions failed:", err);
        return [];
      }),
      loadDecisionQueue(projectRoot).catch((err) => {
        console.error("[progress-dashboard] loadDecisionQueue failed:", err);
        return [];
      }),
      loadReReview(projectRoot).catch((err) => {
        console.error("[progress-dashboard] loadReReview failed:", err);
        return new Set();
      }),
      loadSpecOneLiners(projectRoot).catch((err) => {
        console.error("[progress-dashboard] loadSpecOneLiners failed:", err);
        return {};
      }),
      loadDocs(projectRoot).catch((err) => {
        console.error("[progress-dashboard] loadDocs failed:", err);
        return [];
      }),
    ]);

  // Cross-reference: commits × worklists → derived progress per WP.
  const progress = deriveProgress(commitActivity, worklists);
  // Closure scan covers every WP/F code seen in worklists + every WP referenced
  // by a decision title.
  const closureCodes = new Set([
    ...Object.keys(worklists),
    ...decisions.flatMap((d) => d.titleWps),
  ]);
  const closure = deriveClosure(decisions, Array.from(closureCodes));

  return {
    decisions,
    decisionQueue,
    worklists,
    progress,
    closure,
    reReview,
    specOneLiners,
    gitActivity,
    docs,
  };
}
