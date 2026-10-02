// Director Dashboard MVP — type definitions
// Control Plane prototype only. Not product mainline.

/**
 * v3 6-column model — JIRA-aligned cycle phase progression.
 *
 * Per-cycle phases (left → right):
 *   Backlog       — 미시작, cycle 진입 전
 *   Plan          — 기획 단계 (Spec / Design / Worklist 작성 + Planning Final CP)
 *   Development   — 개발/구현 단계 (Entry Gate 후 dev tasks 진행)
 *   Verification  — 검증 단계 (PASS/FAIL marker, Final 검수 대기)
 *   Done          — 박제 완료 (D-NNN closed)
 *   Review        — 재검토 (outer-loop: meta/re-review.md 신호)
 *
 * **Per-card highlights (Active / Blocked) are attributes, not columns.**
 * 같은 카드가 동시에 진행 중 + 막힘 (Active + Blocked) 일 수 있음.
 * See Ticket.highlight.
 *
 * v2 (Todo/AIWorking/DirectorStuck/Done/Review) → v3 migration:
 *   Todo          → Backlog
 *   AIWorking     → Development     (대부분의 진행 중)
 *   DirectorStuck → Plan / Verification   (gateType 기준)
 *   Done          → Done
 *   Review        → Review
 */
export type BoardColumn =
  | "Backlog"
  | "Plan"
  | "Development"
  | "Verification"
  | "Done"
  | "Review";

/**
 * v3 카드 강조 attribute — column 과 독립. 같은 카드가 둘 다일 수 있음.
 *   active  — 현재 진행 중인 카드
 *   blocked — 막힘 (HCP / review 대기 / 결정 필요)
 */
export type CardHighlight = "active" | "blocked";

export type ViewMode =
  | "Default"
  | "Status"
  | "Actor"
  | "Risk"
  | "Links";

// Actor is free-form: projects define their own actors via config
// (dashboard.config.json `actors`). The known names below stay as
// autocomplete hints but any string is valid (e.g. "Hardware Eng", "Vendor").
export type Actor =
  | "Director"
  | "GPT"
  | "Claude Code"
  | "System"
  | (string & {});

export type TicketType =
  | "F-item"
  | "Gate"
  | "HCP Gate"
  | "Task"
  | "Meta"
  | "Record"
  | "Queue";

export type Tone =
  | "done"
  | "review"
  | "selected"
  | "progress"
  | "backlog"
  | "meta"
  | "hcp";

export type LaneId =
  | "phase1"
  | "phase2"
  | "phase3"
  | "phase4"
  | "phase5"
  | "meta"
  | "control";

export type Risk = "low" | "med" | "high";

export interface Ticket {
  id: string;
  code: string;
  title: string;
  lane: LaneId;
  column: BoardColumn;
  type: TicketType;
  tone: Tone;
  actor?: Actor;
  risk?: Risk;
  links?: string[];
  subtasks?: { id: string; label: string; done: boolean }[];
  description?: string;
  /**
   * Optional progress badge. Two possible upstream sources:
   *   - sentinel: parsed from dashboard-state.json status string
   *               ("🟢 Implementing T-23/30 · last commit 2fb6754")
   *   - commits:  auto-derived from git log via deriveProgress() —
   *               cross-references commit-mentioned T-NN with the WP's
   *               worklist total. Fresher than the sentinel by design.
   *   - merged:   both sources agree (sentinel ≤ commits done AND same sha)
   *
   * The mapper prefers commits when the auto-derived `done` is ≥ the
   * sentinel `done`, since commits are immutable 박제 and the sentinel
   * can lag. Tooltip surface the source so the Director understands
   * where the number came from.
   */
  progress?: {
    done: number;
    total: number;
    /** Optional 7-char short sha of the latest related commit. */
    lastSha?: string;
    /** Raw fragment as it appeared / was computed (for tooltips). */
    raw: string;
    /** Where did this number come from? */
    source: "sentinel" | "commits" | "merged";
    /** Only set when source includes commits — count of contributing commits. */
    commitCount?: number;
  };
  /**
   * Optional closure signal — present when a D-NNN entry in
   * meta/decisions.md formally closes (or rejects) this WP. Set by
   * deriveClosure(); when populated, the column is forced to Done
   * (or Backlog for rejected) regardless of other signals. This is how
   * the dashboard catches Phase-CLOSED gates even when other sources
   * haven't been manually synced.
   */
  closure?: {
    verdict: "closed" | "rejected";
    /** "D-022" */
    decisionId: string;
    /** YYYY-MM-DD if available */
    date?: string;
    /** Full heading text of the deciding entry (for tooltip). */
    title: string;
  };

  /**
   * Director-facing plain-Korean title. When set, the card renders this as
   * the primary headline (큰 글씨) and demotes `title` + `code` to a
   * secondary dev-reference line. Lets the dashboard speak planning
   * language to the Director without losing the developer code paths.
   */
  plainTitle?: string;

  /**
   * One-line "what to do next" in Korean. Rendered prominently on every
   * card so the Director can act without parsing progress/closure/HCP
   * separately. Examples:
   *   - "완료됨 — D-022 (2026-05-16)"
   *   - "→ AI 구현 중 — 6/33"
   *   - "→ Director가 보안 결정 필요"
   *   - "→ 본 cycle 외 — 발생 시 cycle 중단"
   */
  nextAction?: string;

  /**
   * Gate sub-type when this ticket lives in the "🔴 Director 차례" column.
   * Renders as a chip on the card so Director can scan multiple pending
   * gates and prioritize by category. See directorLexicon GATE_TYPE_LABEL.
   *   - "Plan"  — Planning Final CP awaiting Director acceptance
   *   - "HCP"   — Security/permission gate Director must approve
   *   - "Final" — Implementation Final CP (also covers Phase closure)
   */
  gateType?: "Plan" | "HCP" | "Final";

  /**
   * Outer-loop signal: Director flagged this completed WP as needing
   * re-review (기획 의도와 어긋남). Sourced from meta/re-review.md via
   * loadReReview(). When true, the Done card shows a 🔄재검토중 marker
   * instead of the default ✅완료. Toggle via the 🔄 button on the card
   * (POST /api/director-dashboard/re-review).
   */
  reviewFlag?: boolean;

  /**
   * v3 강조 attributes — column 과 독립. 한 카드가 동시에 여러 강조 가능.
   *   - "active": 진행 중 (지금 작업 중인 카드)
   *   - "blocked": 막힘 (HCP, Director 답변 대기 등)
   * 동시 표시 시 카드 좌측 = active 색, 우측 = blocked 색 stripe (hybrid).
   */
  highlights?: CardHighlight[];
}

export interface Lane {
  /**
   * Lane id. In Phase view this is a LaneId (phase1/meta/control/...).
   * In Agent view we build synthetic lanes from ticket.actor, so id can be
   * any stable string ("agent-Director" 등) — widened accordingly.
   */
  id: LaneId | string;
  label: string;
  parent: "Product" | "Meta" | "Control" | "Agent";
  /**
   * Director-facing 1-line semantic: what does this phase/lane mean in
   * planning terms? Rendered as subtitle under the lane label so the
   * Director can grasp "what this phase is for" without reading dev refs.
   */
  outcome?: string;
}

export type TreeNodeKind = "root" | "group" | "lane" | "ticket" | "queue";

export interface TreeNode {
  id: string;
  label: string;
  kind: TreeNodeKind;
  laneId?: LaneId;
  ticketId?: string;
  typeFilter?: TicketType;
  children?: TreeNode[];
}

export interface Scope {
  lanes: LaneId[];
  typeFilter?: TicketType;
  focusedTicketId?: string;
}

export interface SummaryChips {
  foundation: string;
  gate: string;
  next: string;
  hcp: number;
}

/**
 * Director-first chip model used by SummaryBar.
 * Each chip answers one of the 4 five-second questions:
 *   lastShipped — What just finished?
 *   now         — Where are we?
 *   awaiting    — What's the next Director decision?
 *   blockers    — Am I blocked right now?
 */
export interface SummaryDisplayChip {
  key: "lastShipped" | "now" | "awaiting" | "blockers";
  label: string;          // Human label (e.g. "Last shipped")
  value: string;          // Primary value (e.g. "WP-002 Subscription Core")
  sub?: string;           // Secondary value, smaller (e.g. "D-013")
  tone: "green" | "blue" | "amber" | "red" | "neutral";
}

export interface SummaryPayload {
  projectName: string;
  /** Legacy chip values (kept for backward compatibility with the seed). */
  chips: SummaryChips;
  /** New Director-first chip model. Preferred by SummaryBar when present. */
  display?: SummaryDisplayChip[];
}

export type DocKind =
  | "spec"
  | "design"
  | "worklist"
  | "verification"
  | "brief"
  | "blueprint"
  | "operating-model"
  | "ontology"
  | "project-brief"
  | "meta-sprint-spec"
  | "decisions"
  | "decision-queue"
  | "improvement-log"
  | "session-handoff"
  | "execution-log"
  | "status"
  | "state"
  | "other";

/**
 * Which tree node(s) a doc belongs to.
 * A single doc can carry multiple scopes (e.g. an F-001 brief is both
 * "Phase 1" and "WP-001").
 */
export type DocScopeId =
  | { kind: "root" }
  | { kind: "phase"; phase: 1 | 2 | 3 | 4 | 5 }
  | { kind: "wp"; code: string }            // "WP-002" or legacy "F-002"
  | { kind: "ip"; code: string }            // "IP-001" or legacy "M-001"
  | { kind: "meta-sprint"; n: number }
  | { kind: "control" };

export interface DocLink {
  id: string;                  // unique stable id (rel path)
  relPath: string;             // e.g. "ai-npi/WP-002_Spec.md"
  absPath: string;             // OS-absolute path (used for vscode://file)
  title: string;               // display name
  kind: DocKind;
  scopes: DocScopeId[];
}

/**
 * One row of an `ai-npi/WP-NNN_Worklist.md` (or legacy `F-NNN_NPI_Worklist.md`)
 * table. Parsed server-side by loadWorklists().
 */
export type WorklistTaskStatus = "done" | "in-progress" | "pending" | "blocked" | "not-started";

export interface WorklistTask {
  /** "T-23" — stable id within the worklist */
  id: string;
  /** Numeric index for sorting (23) */
  index: number;
  /** Short description of what the task covers */
  description: string;
  /**
   * Phase tag from the worklist's phase column.
   * Used to group tasks in the drill-down panel.
   */
  phase: string;
  status: WorklistTaskStatus;
  /** Raw status cell from the markdown (preserves emojis + Director notes). */
  statusRaw: string;
  /** Optional dependency hint ("T-4" or "T-3 + T-13"). */
  deps?: string;
}

export interface WorklistData {
  /** WP-NNN or F-NNN code this worklist belongs to. */
  code: string;
  /** Relative path to the worklist file (for "open in VSCode" links). */
  relPath: string;
  /** OS-absolute path. */
  absPath: string;
  tasks: WorklistTask[];
  /** Quick counts to avoid re-deriving on the client. */
  totals: {
    total: number;
    done: number;
    inProgress: number;
    pending: number;
    blocked: number;
    notStarted: number;
  };
}

/**
 * One-shot snapshot of the latest commit on the current branch — used by
 * the header's GitActivityChip to show "Claude Code 가 마지막에 무엇을 했지?"
 * at a glance. Git itself is already a 박제 system; this just surfaces it.
 */
export interface GitActivity {
  sha: string;
  author: string;
  /** "4 hours ago" — git's own relative formatter */
  relativeTime: string;
  /** ISO-8601 absolute date (for the tooltip) */
  isoTime: string;
  /** First line of the commit message */
  subject: string;
  branch?: string;
  /** Number of uncommitted file changes; > 0 means working tree is dirty. */
  dirtyFiles?: number;
}

export interface DashboardData {
  lanes: Lane[];
  tickets: Ticket[];
  tree: TreeNode;
  summary: SummaryPayload;
  /**
   * Ordered actor definitions for the Agent-view swimlanes. Each entry is one
   * swimlane in Agent mode (label = the `ticket.actor` value it groups,
   * outcome = the lane subtitle). Sourced from dashboard.config.json `actors`.
   * When absent, the board falls back to the legacy four-actor order. Any
   * actor present on a ticket but missing here still gets a lane (appended).
   */
  agents?: { label: string; outcome?: string }[];
  /** Repo documents matched to tree scopes (server-scanned). */
  docs?: DocLink[];
  /**
   * Parsed worklists keyed by code (WP-NNN or F-NNN). Used by DetailDrawer
   * to render the T-NNN drill-down panel when a WP/F-item card is focused.
   * Empty / undefined when no worklists are available.
   */
  worklists?: Record<string, WorklistData>;
  /** Last commit snapshot — shown in the header next to SyncIndicator. */
  gitActivity?: GitActivity;
  /**
   * Parsed entries from meta/decisions.md (D-NNN log). Used by the
   * dashboard for closure auto-derive (E step) and could be surfaced
   * in a future "decision history" view.
   */
  decisions?: Array<{
    id: string;
    title: string;
    date?: string;
    verdict: "accepted" | "rejected" | "amended" | "neutral";
    wps: string[];
    closes: boolean;
  }>;
  /** Provenance — which file / when. Optional metadata for the footer. */
  source?: {
    file?: string;
    generatedAt?: string;
    schemaVersion?: string;
    /** Which adapter read this project ("npi-docs" / "status-md"). */
    adapter?: string;
    /** Which config file was used, or the auto-derive note when there is none. */
    configSource?: string;
  };
}
