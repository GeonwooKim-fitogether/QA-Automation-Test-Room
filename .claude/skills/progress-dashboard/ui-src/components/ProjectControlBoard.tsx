"use client";

import { useEffect, useMemo, useRef } from "react";
import { TOKENS } from "../styles/atlassianTokens";
import { BoardLane } from "./BoardLane";
import { FlowOverlay } from "./FlowOverlay";
import type { Lane, Ticket, ViewMode } from "../types";
import type { FlowMode, GroupMode } from "./TopBarV3";

interface Props {
  lanes: Lane[];
  tickets: Ticket[];
  viewMode: ViewMode;
  focusedTicketId: string | null;
  relatedIds: Set<string>;
  onTicketClick: (t: Ticket) => void;
  /** Ordered actor definitions for Agent-view swimlanes (from config). */
  agents?: { label: string; outcome?: string }[];
  /**
   * v3 swimlane axis toggle.
   *   "phase" — Lane.parent / Lane.label (Product / Meta / Control)
   *   "agent" — synthesized from ticket.actor (Director / Claude Code / GPT / System)
   */
  groupMode: GroupMode;
  /**
   * v3 PR-5: Flow 점선 overlay 토글.
   *   "on"  — 강조 카드 ↔ 인접 카드 bezier (Phase: same-lane only / Agent: cross-lane OK)
   *   "off" — overlay 숨김
   */
  flowMode: FlowMode;
  /**
   * How many cards the whole dashboard has, ignoring the Work Tree scope.
   * Lets the empty state tell two very different situations apart:
   *   0  — the project produced no cards at all (a setup problem worth
   *        explaining, because "No cards" reads as "the tool is broken")
   *   >0 — the project has cards, the selected tree node just has none
   *        (normal filtering, one sentence is enough)
   */
  totalTicketCount: number;
  /**
   * Provenance shown in the project-wide empty state so the reader can see
   * which adapter ran and which config it used before being told what to fix.
   */
  emptyDiagnostics?: { adapter?: string; configSource?: string };
}

// Legacy fallback order + outcomes — used only when the data carries no
// `agents` metadata (e.g. the static seed). Config-driven projects supply
// their own actors via DashboardData.agents.
const LEGACY_AGENT_ORDER: string[] = ["Director", "Claude Code", "GPT", "System"];
const LEGACY_AGENT_OUTCOME: Record<string, string> = {
  "Director":    "사람 의사 결정 · HCP 승인 · 본질 정의",
  "Claude Code": "구현 · 코드 박제 · 검증 실행",
  "GPT":         "오케스트레이션 · 기획 받아쓰기",
  "System":      "자동 신호 · 컨트롤 플레인",
};

interface SwimlaneRow {
  lane: Lane;
  tickets: Ticket[];
}

type AgentMeta = { label: string; outcome?: string };

// Data-driven Agent swimlanes. Order + subtitles come from `agents` (config).
// Any actor present on a ticket but absent from `agents` still gets a lane
// (appended in first-seen order) so nothing is silently dropped.
function buildAgentLanes(tickets: Ticket[], agents?: AgentMeta[]): SwimlaneRow[] {
  const buckets = new Map<string, Ticket[]>();
  for (const t of tickets) {
    const actor = t.actor ?? "System";
    const arr = buckets.get(actor) ?? [];
    arr.push(t);
    buckets.set(actor, arr);
  }

  const hasMeta = Array.isArray(agents) && agents.length > 0;
  const order = hasMeta ? agents!.map((a) => a.label) : LEGACY_AGENT_ORDER;
  const outcomeOf = (actor: string): string | undefined => {
    if (hasMeta) {
      const m = agents!.find((a) => a.label === actor);
      if (m) return m.outcome;
    }
    return LEGACY_AGENT_OUTCOME[actor];
  };

  // Configured/known order first, then any remaining actors present in data.
  const seen = new Set<string>(order);
  const finalOrder = [...order, ...[...buckets.keys()].filter((a) => !seen.has(a))];

  const rows: SwimlaneRow[] = [];
  for (const actor of finalOrder) {
    const arr = buckets.get(actor);
    if (!arr || arr.length === 0) continue;
    rows.push({
      lane: {
        id: `agent-${actor}`,
        label: actor,
        parent: "Agent",
        outcome: outcomeOf(actor),
      },
      tickets: arr,
    });
  }
  return rows;
}

function buildPhaseLanes(lanes: Lane[], tickets: Ticket[]): SwimlaneRow[] {
  const rows: SwimlaneRow[] = [];
  for (const lane of lanes) {
    const laneTickets = tickets.filter((t) => t.lane === lane.id);
    if (laneTickets.length === 0) continue;
    rows.push({ lane, tickets: laneTickets });
  }
  return rows;
}

/**
 * Empty board.
 *
 * The old copy was a single line — "No cards in this scope." — which reads as
 * "the dashboard is broken" when the whole project comes up empty. It named
 * neither the cause nor a next step. This version answers three questions in
 * order: what happened, why it probably happened, and what to do about it.
 * The scope-filtered case stays one sentence, because there nothing is wrong.
 */
function EmptyBoard({
  projectWide,
  diagnostics,
}: {
  projectWide: boolean;
  diagnostics?: { adapter?: string; configSource?: string };
}) {
  const shellStyle = {
    background: TOKENS.bgWhite,
    border: `1px dashed ${TOKENS.border}`,
  } as const;

  if (!projectWide) {
    return (
      <div
        className="rounded-md p-6 text-center text-[13px]"
        style={{ ...shellStyle, color: TOKENS.textMuted }}
      >
        선택한 범위에는 카드가 없습니다. 왼쪽 Work Tree 에서 상위 노드를 선택하면 더 넓은
        범위의 카드를 볼 수 있습니다.
      </div>
    );
  }

  const rows: [string, string][] = [
    ["Adapter", diagnostics?.adapter ?? "(알 수 없음)"],
    ["Config", diagnostics?.configSource ?? "(알 수 없음)"],
  ];

  return (
    <div
      className="rounded-md p-6"
      style={{ ...shellStyle, maxWidth: 720, margin: "0 auto" }}
    >
      <div>
        <div
          className="text-[15px] font-semibold"
          style={{ color: TOKENS.textPrimary }}
        >
          표시할 카드가 없습니다
        </div>
        <p
          className="mt-2 text-[12.5px] leading-relaxed"
          style={{ color: TOKENS.textSecondary }}
        >
          대시보드가 고장난 것이 아닙니다. 이 프로젝트에서 카드로 만들 정본 문서(작업
          워크리스트와 결정 로그)를 하나도 찾지 못한 상태입니다. 아래는 방금 어떤 설정으로
          읽었는지입니다.
        </p>

        <dl
          className="mt-3 grid grid-cols-[72px_1fr] gap-x-3 gap-y-1 rounded p-2.5 text-[12px]"
          style={{ background: TOKENS.bg, border: `1px solid ${TOKENS.divider}` }}
        >
          {rows.map(([k, val]) => (
            <div key={k} style={{ display: "contents" }}>
              <dt style={{ color: TOKENS.textMuted }}>{k}</dt>
              <dd className="font-mono text-[11.5px]" style={{ color: TOKENS.textPrimary }}>
                {val}
              </dd>
            </div>
          ))}
        </dl>

        <div
          className="mt-3 text-[11px] font-semibold uppercase tracking-wider"
          style={{ color: TOKENS.textMuted }}
        >
          다음에 할 일
        </div>
        <ol
          className="mt-1.5 space-y-1.5 text-[12.5px] leading-relaxed"
          style={{ color: TOKENS.textSecondary, listStyle: "decimal", paddingLeft: 18 }}
        >
          <li>
            프로젝트 루트의 <Code>dashboard.config.json</Code> 에{" "}
            <Code>&quot;adapter&quot;</Code> 를 지정합니다. 지금 쓸 수 있는 값은{" "}
            <Code>npi-docs</Code> 와 <Code>status-md</Code> 두 가지입니다.
          </li>
          <li>
            그 어댑터가 읽는 정본 문서가 실제로 있는지 확인합니다.{" "}
            <Code>npi-docs</Code> 는 워크리스트 문서와 결정 로그를,{" "}
            <Code>status-md</Code> 는 <Code>STATUS.md</Code> 를 읽습니다.
          </li>
          <li>
            설정 항목 전체는 스킬 폴더의 <Code>reference/CONFIG.md</Code> 에 정리돼
            있습니다.
          </li>
        </ol>
      </div>
    </div>
  );
}

function Code({ children }: { children: React.ReactNode }) {
  return (
    <code
      className="font-mono text-[11.5px]"
      style={{
        background: TOKENS.bg,
        border: `1px solid ${TOKENS.divider}`,
        borderRadius: 3,
        padding: "1px 4px",
        color: TOKENS.textPrimary,
        // Identifiers must not be split across lines — "npi-docs" broken into
        // "npi-" / "docs" reads as two different values.
        whiteSpace: "nowrap",
      }}
    >
      {children}
    </code>
  );
}

export function ProjectControlBoard({
  lanes,
  tickets,
  viewMode,
  focusedTicketId,
  relatedIds,
  onTicketClick,
  groupMode,
  flowMode,
  agents,
  totalTicketCount,
  emptyDiagnostics,
}: Props) {
  const rows = useMemo<SwimlaneRow[]>(
    () =>
      groupMode === "agent"
        ? buildAgentLanes(tickets, agents)
        : buildPhaseLanes(lanes, tickets),
    [groupMode, lanes, tickets, agents],
  );

  // Flow 연결 id — focused 카드 ↔ 관련 카드. Phase / Agent 양쪽 모두 cross-lane
  // 연결을 그린다 (관련 카드가 다른 swimlane 에 있어도 backlink 를 표시). relatedIds
  // 는 focused id 자기 자신을 포함 (dim 처리용) — flow 에서는 제외.
  const flowIds = useMemo<Set<string>>(() => {
    if (flowMode !== "on" || !focusedTicketId || relatedIds.size === 0) {
      return new Set<string>();
    }
    const filtered = new Set<string>();
    relatedIds.forEach((rid) => {
      if (rid !== focusedTicketId) filtered.add(rid); // self-curve 방지
    });
    return filtered;
  }, [flowMode, focusedTicketId, relatedIds]);

  const flowContainerRef = useRef<HTMLDivElement>(null);

  // When a card is focused (drawer opens + board narrows), scroll that card to
  // the center of the board viewport so it isn't pushed out of view / clipped
  // by the detail panel. Runs after the grid-width transition settles.
  useEffect(() => {
    if (!focusedTicketId) return;
    const c = flowContainerRef.current;
    if (!c) return;
    const timer = window.setTimeout(() => {
      const el = c.querySelector(`[data-ticket-id="${focusedTicketId}"]`);
      // Instant (not smooth): a smooth animation leaves the flow curves measured
      // against a mid-animation scroll for a few frames → transient endpoint
      // drift. Instant scroll + the settle re-measure keep curves glued.
      if (el) el.scrollIntoView({ inline: "center", block: "nearest", behavior: "auto" });
    }, 180);
    return () => window.clearTimeout(timer);
  }, [focusedTicketId]);

  return (
    <main
      className="flex h-full flex-col overflow-y-auto"
      style={{ background: TOKENS.bg }}
      aria-label="Project Control Board"
    >
      <div className="overflow-x-auto p-3" style={{ minWidth: 0 }}>
        {/* minWidth is `min-content`, not a hardcoded 1200px: the lanes below
            declare their own real minimum (6 columns × 200px + gaps), so the
            track widens to whatever the column model actually needs and the
            parent `overflow-x-auto` scrolls to reach every column. A fixed
            1200px was narrower than the lanes' true 1258px, which is how the
            last column ended up cropped and unreachable. */}
        <div ref={flowContainerRef} style={{ minWidth: "min-content", position: "relative" }}>
          {rows.map(({ lane, tickets: laneTickets }) => (
            <BoardLane
              key={lane.id}
              lane={lane}
              tickets={laneTickets}
              viewMode={viewMode}
              focusedTicketId={focusedTicketId}
              relatedIds={relatedIds}
              onTicketClick={onTicketClick}
            />
          ))}
          {tickets.length === 0 && (
            <EmptyBoard
              projectWide={totalTicketCount === 0}
              diagnostics={emptyDiagnostics}
            />
          )}
          <FlowOverlay
            containerRef={flowContainerRef}
            focusedTicketId={focusedTicketId}
            flowIds={flowIds}
            enabled={flowMode === "on"}
          />
        </div>
      </div>
    </main>
  );
}
