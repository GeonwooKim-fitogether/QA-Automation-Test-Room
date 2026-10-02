"use client";

// Director Dashboard MVP — top-level container.
// Live data via 8s client-side polling against /api/director-dashboard/state.
// initialData (server-component hydration) is used as the first render so the
// HTML is non-empty; subsequent polls swap in fresh data without a page reload.
//
// No backend writes / no DB / no payments / no secrets.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  LANES as SEED_LANES,
  SUMMARY as SEED_SUMMARY,
  TICKETS as SEED_TICKETS,
  TREE as SEED_TREE,
} from "../data/directorDashboardSeed";
import { TOKENS } from "../styles/atlassianTokens";
import type { DashboardData, Lane, Ticket, TreeNode, ViewMode } from "../types";
import { applyScope, findTreeNodeById, resolveScope } from "../utils/scope";
import { relatedIdSet } from "../utils/relations";
import { TopBarV3, type GroupMode, type FlowMode, type ThemeMode } from "./TopBarV3";
import { WorkTree } from "./WorkTree";
import { ProjectControlBoard } from "./ProjectControlBoard";
import { DetailDrawer } from "./DetailDrawer";
import { SyncIndicator } from "./SyncIndicator";
import { ThemeStyles, useDashboardTheme } from "./ThemeControl";
import { useLiveAlerts } from "../hooks/useLiveAlerts";
import { STATIC_MODE } from "../staticMode";

interface Props {
  /**
   * Server-side hydrated data. Used as the initial render and the seed for the
   * polling loop. When omitted the seed is used.
   */
  data?: DashboardData;
}

const POLL_INTERVAL_MS = 8000;
const STATE_URL = "/api/director-dashboard/state";

export function DirectorDashboardMVP({ data: initialData }: Props = {}) {
  // ---------- live data (polling) ----------
  const [data, setData] = useState<DashboardData | undefined>(initialData);
  const [lastSyncAt, setLastSyncAt] = useState<Date | null>(initialData ? new Date() : null);
  const [syncError, setSyncError] = useState<string | null>(null);

  // Source-of-truth views (data ?? seed). When `data` swaps, the rest re-derives.
  const LANES: Lane[]    = data?.lanes   ?? SEED_LANES;
  const TICKETS: Ticket[] = data?.tickets ?? SEED_TICKETS;
  const TREE: TreeNode    = data?.tree    ?? SEED_TREE;

  // Live alerts: browser notification when a new 🛑 내 차례 card appears
  // between polls. Silent on first load. Permission requested once on mount.
  useLiveAlerts(data ?? null);
  const SUMMARY           = data?.summary ?? SEED_SUMMARY;

  // ---------- interaction state ----------
  // Track by id (not node reference) so polling-driven data swaps don't drop
  // the user's selection / focus.
  const [selectedNodeId, setSelectedNodeId] = useState<string>(TREE.id);
  // v3: ViewMode UI 폐기. 5 modes (Default/Status/Actor/Risk/Links) 의 의미는
  // highlights + agent chip + Detail panel 로 흡수. state 는 "Default" fixed.
  const viewMode: ViewMode = "Default";
  const [focusedTicketId, setFocusedTicketId] = useState<string | null>(null);
  // Drawer is closed by default — opens only when the user clicks a card
  // (or a tree-ticket node). Matches the Director's reading flow: scan board,
  // then drill into a specific card.
  const [drawerOpen, setDrawerOpen] = useState<boolean>(false);
  // v3: Group toggle (Phase swimlane vs Agent swimlane — actual rebuild in PR-4)
  const [groupMode, setGroupMode] = useState<GroupMode>("phase");
  // v3: Flow toggle (cross-card 점선 overlay — wired in PR-5)
  const [flowMode, setFlowMode] = useState<FlowMode>("on");

  // Re-derive selected node from the current tree. Fallback to the root if
  // the id no longer exists (e.g. tree shape changed between polls).
  const selectedNode: TreeNode = useMemo(
    () => findTreeNodeById(TREE, selectedNodeId) ?? TREE,
    [TREE, selectedNodeId],
  );

  const scope = useMemo(() => resolveScope(selectedNode), [selectedNode]);
  const ticketsInScope = useMemo(() => applyScope(TICKETS, scope), [scope, TICKETS]);
  const relatedIds = useMemo(
    () => relatedIdSet(focusedTicketId, TICKETS),
    [focusedTicketId, TICKETS],
  );
  const focusedTicket =
    focusedTicketId !== null
      ? TICKETS.find((t) => t.id === focusedTicketId) ?? null
      : null;

  const linkedForDrawer = useMemo<Ticket[]>(() => {
    if (!focusedTicket) return [];
    const ids = new Set<string>(focusedTicket.links ?? []);
    for (const t of TICKETS) {
      if (t.links?.includes(focusedTicket.id)) ids.add(t.id);
    }
    ids.delete(focusedTicket.id);
    return TICKETS.filter((t) => ids.has(t.id));
  }, [focusedTicket, TICKETS]);

  // Pick the worklist matching the focused ticket's code. Codes can carry
  // both legacy + new aliases ("F-004 / WP-004"), so we check each token.
  const focusedWorklist = useMemo(() => {
    if (!focusedTicket || !data?.worklists) return undefined;
    const tokens = focusedTicket.code.match(/(WP-\d{3}|F-\d{3})/gi) ?? [];
    for (const tok of tokens) {
      const hit = data.worklists[tok.toUpperCase()];
      if (hit) return hit;
    }
    return undefined;
  }, [focusedTicket, data?.worklists]);

  // v3 note: SummaryBar 4 chips (Last shipped / Now / Awaiting / Blockers) 폐기.
  // 정보 hierarchy 가 mock δ 와 안 맞아 — Active/HCP badge 로 축약.

  // ---------- polling loop ----------
  // 8-second interval, AbortController for in-flight cancellation, pauses while
  // the tab is hidden. Errors keep the last good data on screen.
  const pollingRef = useRef<{
    cancelled: boolean;
    timer: ReturnType<typeof setTimeout> | null;
    controller: AbortController | null;
  }>({ cancelled: false, timer: null, controller: null });

  const tick = useCallback(async () => {
    const ref = pollingRef.current;
    if (ref.cancelled) return;
    if (typeof document !== "undefined" && document.hidden) {
      // Skip while hidden; visibilitychange listener will resume.
      ref.timer = setTimeout(tick, POLL_INTERVAL_MS);
      return;
    }
    ref.controller = new AbortController();
    try {
      const res = await fetch(STATE_URL, {
        cache: "no-store",
        signal: ref.controller.signal,
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const next = (await res.json()) as DashboardData;
      if (!ref.cancelled) {
        setData(next);
        setLastSyncAt(new Date());
        setSyncError(null);
      }
    } catch (err) {
      const name = (err as { name?: string }).name;
      if (!ref.cancelled && name !== "AbortError") {
        setSyncError(String((err as Error).message ?? err));
      }
    } finally {
      if (!ref.cancelled) {
        ref.timer = setTimeout(tick, POLL_INTERVAL_MS);
      }
    }
  }, []);

  useEffect(() => {
    // Standalone static snapshot: no live /api endpoint to poll. The injected
    // window.__DASHBOARD_DATA__ (passed in as initialData) is the final state.
    if (STATIC_MODE) return;
    const ref = pollingRef.current;
    ref.cancelled = false;
    tick();
    function onVis() {
      if (!document.hidden && ref.timer === null) tick();
    }
    document.addEventListener("visibilitychange", onVis);
    return () => {
      ref.cancelled = true;
      if (ref.timer) clearTimeout(ref.timer);
      ref.controller?.abort();
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [tick]);

  // ---------- handlers ----------
  function handleTreeSelect(node: TreeNode) {
    setSelectedNodeId(node.id);
    if (node.kind === "ticket" && node.ticketId) {
      setFocusedTicketId(node.ticketId);
      setDrawerOpen(true);
    }
  }

  function handleTicketClick(ticket: Ticket) {
    setFocusedTicketId((prev) => (prev === ticket.id ? null : ticket.id));
    setDrawerOpen(true);
  }

  function clearFocus() {
    setFocusedTicketId(null);
  }

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        if (focusedTicketId !== null) clearFocus();
        else if (drawerOpen) setDrawerOpen(false);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [focusedTicketId, drawerOpen]);

  // ---------- theme (light/dark) ----------
  const { theme, toggle: toggleTheme } = useDashboardTheme();

  // The whole dashboard produced no cards — a setup problem, not a filter.
  // In that state two header affordances actively mislead: the "Live · Ns ago"
  // badge implies a healthy feed is arriving, and "Detail 열기" offers a drawer
  // that can only ever say "click a card" when there is no card to click. Both
  // are suppressed so the empty-state explanation is the only thing on screen.
  const boardIsEmpty = TICKETS.length === 0;

  // ---------- render ----------
  return (
    <div
      data-dd-root
      data-dd-theme={theme === "dark" ? "dark" : undefined}
      style={{
        background: TOKENS.bg,
        color: TOKENS.textPrimary,
        minHeight: "100vh",
        fontFamily:
          "-apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif",
      }}
    >
      <ThemeStyles />
      <div style={{ maxWidth: 1800, margin: "0 auto", position: "relative" }}>
        <TopBarV3
          projectName={SUMMARY.projectName}
          tickets={TICKETS}
          groupMode={groupMode}
          onGroupChange={setGroupMode}
          flowMode={flowMode}
          onFlowChange={setFlowMode}
          themeMode={theme as ThemeMode}
          onThemeChange={(t) => {
            if (t !== theme) toggleTheme();
          }}
          liveSlot={
            boardIsEmpty ? undefined : (
              <SyncIndicator
                lastSyncAt={lastSyncAt}
                error={syncError}
                intervalMs={POLL_INTERVAL_MS}
              />
            )
          }
          rightSlot={
            focusedTicketId !== null ? (
              <button
                type="button"
                onClick={clearFocus}
                title="ESC 또는 같은 카드 재클릭으로도 해제"
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 4,
                  padding: "0 10px",
                  height: 24,
                  background: TOKENS.bgWhite,
                  color: TOKENS.textSecondary,
                  border: `1px solid ${TOKENS.border}`,
                  borderRadius: 4,
                  fontSize: 11,
                  fontWeight: 500,
                  cursor: "pointer",
                }}
              >
                <span aria-hidden style={{ fontSize: 10 }}>✕</span>
                Clear
              </button>
            ) : undefined
          }
        />

        <div
          style={{
            display: "grid",
            gridTemplateColumns: drawerOpen
              ? "minmax(180px, 1fr) minmax(0, 5fr) 280px"
              : "minmax(180px, 1fr) minmax(0, 5fr)",
            alignItems: "stretch",
            minHeight: "calc(100vh - 56px)",
            transition: "grid-template-columns 0.18s ease",
          }}
        >
          <div
            style={{
              maxHeight: "calc(100vh - 56px)",
              position: "sticky",
              top: 0,
              alignSelf: "start",
            }}
          >
            <WorkTree
              root={TREE}
              selectedId={selectedNode.id}
              selectedNode={selectedNode}
              onSelect={handleTreeSelect}
              docs={data?.docs}
            />
          </div>

          <ProjectControlBoard
            lanes={LANES}
            tickets={ticketsInScope}
            viewMode={viewMode}
            focusedTicketId={focusedTicketId}
            relatedIds={relatedIds}
            onTicketClick={handleTicketClick}
            groupMode={groupMode}
            flowMode={flowMode}
            agents={data?.agents}
            totalTicketCount={TICKETS.length}
            emptyDiagnostics={{
              adapter: data?.source?.adapter,
              configSource: data?.source?.configSource,
            }}
          />

          {drawerOpen && (
            <div
              style={{
                // explicit height (not maxHeight) so child h-full evaluates,
                // overflow-y-auto 안의 스크롤이 작동한다.
                height: "calc(100vh - 56px)",
                position: "sticky",
                top: 0,
                alignSelf: "start",
                overflow: "hidden",
              }}
            >
              <DetailDrawer
                ticket={focusedTicket}
                related={linkedForDrawer}
                isOpen={drawerOpen}
                onClose={() => {
                  setDrawerOpen(false);
                  setFocusedTicketId(null);
                }}
                onTicketClick={(t) => setFocusedTicketId(t.id)}
                worklist={focusedWorklist}
              />
            </div>
          )}
        </div>

        {!drawerOpen && !boardIsEmpty && (
          <button
            type="button"
            onClick={() => setDrawerOpen(true)}
            style={{
              position: "fixed",
              right: 16,
              bottom: 16,
              background: TOKENS.blue,
              color: TOKENS.bgWhite,
              border: 0,
              borderRadius: 999,
              padding: "8px 14px",
              fontWeight: 600,
              fontSize: 12,
              boxShadow: "var(--dd-fabShadow)",
              cursor: "pointer",
            }}
          >
            Detail 열기
          </button>
        )}
      </div>
    </div>
  );
}
