import {
  COLUMN_ACCENT,
  COLUMN_LABEL,
  COLUMN_ORDER,
  TOKENS,
} from "../styles/atlassianTokens";
import { TicketCard } from "./TicketCard";
import type { BoardColumn, Lane, Ticket, ViewMode } from "../types";

interface Props {
  lane: Lane;
  tickets: Ticket[];
  viewMode: ViewMode;
  focusedTicketId: string | null;
  relatedIds: Set<string>;
  onTicketClick: (t: Ticket) => void;
}

export function BoardLane({
  lane,
  tickets,
  viewMode,
  focusedTicketId,
  relatedIds,
  onTicketClick,
}: Props) {
  const hasFocus = focusedTicketId !== null;
  // v3: 6-column JIRA-aligned phase model rendered 1:1 from ticket.column.
  const grouped: Record<BoardColumn, Ticket[]> = {
    Backlog: [],
    Plan: [],
    Development: [],
    Verification: [],
    Done: [],
    Review: [],
  };
  for (const t of tickets) grouped[t.column].push(t);

  return (
    <section
      className="flex flex-col"
      style={{
        background: TOKENS.bgWhite,
        border: `1px solid ${TOKENS.border}`,
        borderRadius: 8,
        marginBottom: 14,
        // `clip` (not `hidden`) on purpose. `overflow: hidden` makes this box a
        // scroll container, and a scroll container's min-content contribution is
        // zero — so the 6-column grid below (which needs 6 × 200px + gaps) was
        // silently CROPPED instead of widening the lane, and the cropped columns
        // could not be reached by scrolling either. `clip` still clips children
        // to the border radius but keeps the intrinsic width contribution, so
        // `minWidth: min-content` below can push the lane out to its real width
        // and the board's `overflow-x-auto` ancestor scrolls to reach it.
        overflow: "clip",
        minWidth: "min-content",
      }}
      aria-label={`Lane ${lane.label}`}
    >
      <header
        className="flex items-start justify-between gap-3 px-3 py-2"
        style={{
          background: TOKENS.bg,
          borderBottom: `1px solid ${TOKENS.divider}`,
        }}
      >
        <div className="flex flex-col gap-0.5">
          <div
            className="text-[12px] font-semibold"
            style={{ color: TOKENS.textPrimary }}
          >
            <span style={{ marginRight: 12, color: TOKENS.textMuted }}>[{lane.parent}]</span>
            {lane.label}
          </div>
          {lane.outcome && (
            <div
              className="text-[11.5px]"
              style={{ color: TOKENS.textMuted, fontWeight: 400 }}
            >
              · {lane.outcome}
            </div>
          )}
        </div>
        <div className="text-[11px] flex-shrink-0 pt-0.5" style={{ color: TOKENS.textMuted }}>
          {tickets.length} card{tickets.length === 1 ? "" : "s"}
        </div>
      </header>

      <div
        className="grid gap-2 p-2"
        style={{
          gridTemplateColumns: `repeat(${COLUMN_ORDER.length}, minmax(200px, 1fr))`,
        }}
      >
        {COLUMN_ORDER.map((col) => (
          <div
            key={col}
            className="flex flex-col rounded-md"
            style={{
              background: TOKENS.bg,
              border: `1px solid ${TOKENS.divider}`,
              minHeight: 80,
            }}
          >
            <div
              className="flex items-center justify-between px-2 py-1.5 text-[11px] font-semibold tracking-wide"
              style={{
                color: COLUMN_ACCENT[col],
                borderBottom: `2px solid ${COLUMN_ACCENT[col]}`,
              }}
            >
              <span>{COLUMN_LABEL[col]}</span>
              <span style={{ color: TOKENS.textMuted }}>{grouped[col].length}</span>
            </div>
            <div className="flex flex-col gap-1.5 p-1.5">
              {grouped[col].map((t) => (
                <TicketCard
                  key={t.id}
                  ticket={t}
                  viewMode={viewMode}
                  isFocused={focusedTicketId === t.id}
                  isLinked={relatedIds.has(t.id)}
                  hasFocus={hasFocus}
                  onClick={onTicketClick}
                />
              ))}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
