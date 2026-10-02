// docs.ts — pure helpers that match DocLink[] to TreeNode / Ticket and the
// label/tone helpers that drive the DocsPanel's visual layer.
// Client-safe (no IO).

import type { DocKind, DocLink, LaneId, Ticket, TreeNode } from "../types";

// ---------------------------------------------------------------------------
// Lane / code helpers
// ---------------------------------------------------------------------------

function laneToPhase(lane: LaneId): 1 | 2 | 3 | 4 | 5 | null {
  if (lane === "phase1") return 1;
  if (lane === "phase2") return 2;
  if (lane === "phase3") return 3;
  if (lane === "phase4") return 4;
  if (lane === "phase5") return 5;
  return null;
}

function phaseWpCodes(phase: 1 | 2 | 3 | 4 | 5): string[] {
  const n = String(phase).padStart(3, "0");
  return [`WP-${n}`, `F-${n}`];
}

function extractCodes(s: string): { wp: string[]; ip: string[]; metaSprint: number[] } {
  const wp = new Set<string>();
  const ip = new Set<string>();
  const ms = new Set<number>();
  const wpRe = /\b(WP|F)-(\d{2,3})\b/gi;
  let m: RegExpExecArray | null;
  while ((m = wpRe.exec(s))) {
    const n = m[2].padStart(3, "0");
    wp.add(`WP-${n}`);
    wp.add(`F-${n}`);
  }
  const ipRe = /\b(IP|M)-(\d{2,3})\b/gi;
  while ((m = ipRe.exec(s))) {
    const n = m[2].padStart(3, "0");
    ip.add(`IP-${n}`);
    ip.add(`M-${n}`);
  }
  const msRe = /\bMeta\s*Sprint\s*(\d+)\b/gi;
  while ((m = msRe.exec(s))) {
    ms.add(parseInt(m[1], 10));
  }
  return { wp: Array.from(wp), ip: Array.from(ip), metaSprint: Array.from(ms) };
}

function scopeHasRoot(d: DocLink): boolean {
  return d.scopes.some((s) => s.kind === "root");
}
function scopeHasControl(d: DocLink): boolean {
  return d.scopes.some((s) => s.kind === "control");
}
function scopeHasPhase(d: DocLink, phase: 1 | 2 | 3 | 4 | 5): boolean {
  return d.scopes.some((s) => s.kind === "phase" && s.phase === phase);
}
function scopeHasWp(d: DocLink, codes: string[]): boolean {
  return d.scopes.some((s) => s.kind === "wp" && codes.includes(s.code));
}
function scopeHasIp(d: DocLink, codes: string[]): boolean {
  return d.scopes.some((s) => s.kind === "ip" && codes.includes(s.code));
}
function scopeHasMetaSprint(d: DocLink, ns?: number[]): boolean {
  return d.scopes.some((s) => s.kind === "meta-sprint" && (!ns || ns.includes(s.n)));
}

// ---------------------------------------------------------------------------
// Level-aware tree → docs matcher
//
// Different tree levels surface different "shapes" of documents:
//   root        → strategic / foundation docs only
//   product     → each phase's Spec (index view)
//   lane(phaseN)→ that phase's work products + any legacy F-NNN_NPI_*
//   lane(meta)  → Meta Sprint Spec + all IP specs
//   lane(control) → control records (decisions, queue, FIL, handoff, log, state)
//   queue(...)  → narrowed control records
//   ticket      → docs whose WP/IP code appears in the ticket label
// ---------------------------------------------------------------------------

export function docsForTreeNode(node: TreeNode | null, docs: DocLink[] | undefined): DocLink[] {
  if (!node || !docs || docs.length === 0) return [];

  switch (node.kind) {
    case "root":
      return docs.filter(scopeHasRoot);

    case "group":
      if (node.id === "product") {
        return docs.filter(
          (d) =>
            d.kind === "spec" &&
            d.scopes.some((s) => s.kind === "phase" || s.kind === "wp"),
        );
      }
      return docs.filter(scopeHasRoot);

    case "lane": {
      const phase = node.laneId ? laneToPhase(node.laneId) : null;
      if (phase) {
        const codes = phaseWpCodes(phase);
        return docs.filter((d) => scopeHasPhase(d, phase) || scopeHasWp(d, codes));
      }
      if (node.laneId === "meta") {
        return docs.filter(
          (d) =>
            d.kind === "meta-sprint-spec" ||
            scopeHasMetaSprint(d) ||
            d.scopes.some((s) => s.kind === "ip"),
        );
      }
      if (node.laneId === "control") {
        return docs.filter(scopeHasControl);
      }
      return [];
    }

    case "queue":
      if (node.typeFilter === "Record") {
        return docs.filter((d) => d.kind === "decisions" || d.kind === "decision-queue");
      }
      if (node.typeFilter === "Queue") {
        return docs.filter(
          (d) =>
            d.kind === "decisions" ||
            d.kind === "decision-queue" ||
            d.kind === "improvement-log",
        );
      }
      return docs.filter(scopeHasControl);

    case "ticket": {
      const codes = extractCodes(node.label ?? "");
      return docs.filter((d) => scopeHasWp(d, codes.wp) || scopeHasIp(d, codes.ip));
    }

    default:
      return [];
  }
}

export function docsForTicket(ticket: Ticket | null, docs: DocLink[] | undefined): DocLink[] {
  if (!ticket || !docs || docs.length === 0) return [];

  const codes = extractCodes(`${ticket.code} ${ticket.title} ${ticket.description ?? ""}`);
  const phase = laneToPhase(ticket.lane);

  return docs.filter((d) => {
    if (scopeHasWp(d, codes.wp)) return true;
    if (scopeHasIp(d, codes.ip)) return true;
    if (phase && scopeHasPhase(d, phase) && d.kind === "spec") return true;
    if (ticket.lane === "control" && scopeHasControl(d)) return true;
    return false;
  });
}

// ---------------------------------------------------------------------------
// Doc kind → display label + tone color
// ---------------------------------------------------------------------------

export type DocTone = "blue" | "green" | "amber" | "purple" | "neutral";

/** Short single-word label for the kind prefix in the docs list. */
export function docKindLabel(kind: DocKind): string {
  switch (kind) {
    case "spec":             return "spec";
    case "design":           return "design";
    case "worklist":         return "worklist";
    case "verification":     return "verify";
    case "brief":            return "brief";
    case "blueprint":        return "blueprint";
    case "operating-model":  return "operating";
    case "ontology":         return "ontology";
    case "project-brief":    return "brief";
    case "meta-sprint-spec": return "meta";
    case "decisions":        return "decisions";
    case "decision-queue":   return "queue";
    case "improvement-log":  return "fil";
    case "session-handoff":  return "handoff";
    case "execution-log":    return "log";
    case "status":           return "status";
    case "state":            return "state";
    default:                 return "doc";
  }
}

/**
 * Atlassian-spectrum tone for a doc kind. Reuses the SummaryBar's dot
 * spectrum so the Director only needs to learn one color system.
 */
export function docTone(kind: DocKind): DocTone {
  switch (kind) {
    case "spec":
    case "design":
    case "worklist":
    case "verification":
    case "brief":
    case "blueprint":
      return "blue"; // work products
    case "operating-model":
    case "ontology":
    case "project-brief":
      return "green"; // foundation
    case "decisions":
    case "decision-queue":
      return "amber"; // decision records
    case "meta-sprint-spec":
    case "improvement-log":
      return "purple"; // meta / improvement
    case "session-handoff":
    case "execution-log":
    case "status":
    case "state":
    default:
      return "neutral"; // operational / info
  }
}

/** Human-readable category label for the tone — used in tooltips and legend. */
export function docToneCategory(tone: DocTone): string {
  switch (tone) {
    case "blue":    return "work product";
    case "green":   return "foundation";
    case "amber":   return "decision record";
    case "purple":  return "meta / improvement";
    case "neutral": return "operational";
  }
}

/** Build a vscode://file deep-link from an OS-absolute path. */
export function vscodeFileUrl(absPath: string): string {
  const normalized = absPath.replace(/\\/g, "/");
  const prefixed = normalized.startsWith("/") ? normalized : `/${normalized}`;
  return `vscode://file${prefixed}`;
}

// ---------------------------------------------------------------------------
// Contextual empty-state hint per tree level
// ---------------------------------------------------------------------------

export function emptyStateHint(node: TreeNode | null): string {
  if (!node) return "노드를 선택해 주세요.";
  switch (node.kind) {
    case "root":
      return "프로젝트 전반의 운영 / 도메인 문서가 여기에 표시됩니다. 더 자세한 문서는 Phase / Meta / Control 노드를 선택하세요.";
    case "group":
      return "그룹 인덱스 — 각 Phase 의 Spec 만 표시됩니다.";
    case "lane":
      return "이 노드에 매칭된 문서가 없습니다.";
    case "queue":
      return "이 큐에 매칭된 문서가 없습니다.";
    case "ticket":
      return "이 카드의 코드(WP / F / IP / M)와 매칭된 문서가 없습니다.";
    default:
      return "매칭된 문서가 없습니다.";
  }
}
