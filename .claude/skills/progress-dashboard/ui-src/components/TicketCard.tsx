import { useEffect, useState } from "react";
import { TOKENS, TONE_STYLES } from "../styles/atlassianTokens";
import { viewBadgeFor } from "../utils/viewMode";
import type { Ticket, ViewMode } from "../types";
import { STATIC_MODE } from "../staticMode";

interface Props {
  ticket: Ticket;
  viewMode: ViewMode;
  isFocused: boolean;
  isLinked: boolean;
  hasFocus: boolean;
  onClick: (ticket: Ticket) => void;
}

export function TicketCard({
  ticket,
  viewMode,
  isFocused,
  isLinked,
  hasFocus,
  onClick,
}: Props) {
  const [expanded, setExpanded] = useState(false);
  const [detailExpanded, setDetailExpanded] = useState(false);

  // Optimistic local override for the re-review flag. Polling sync (8s)
  // will reconcile from the canonical file shortly after the POST.
  const [localReviewFlag, setLocalReviewFlag] = useState<boolean | undefined>(undefined);
  const [reviewBusy, setReviewBusy] = useState(false);
  useEffect(() => {
    // When the prop updates (next poll), drop the local override.
    setLocalReviewFlag(undefined);
  }, [ticket.reviewFlag]);
  const effectiveReviewFlag = localReviewFlag ?? ticket.reviewFlag ?? false;

  const tone = TONE_STYLES[ticket.tone];
  const viewBadge = viewBadgeFor(ticket, viewMode);
  const dimmed = hasFocus && !isLinked;

  // v3 per-card highlights — column 과 독립.
  const hasActive  = ticket.highlights?.includes("active") ?? false;
  const hasBlocked = ticket.highlights?.includes("blocked") ?? false;

  // accent (좌측 stripe) precedence: focused > review > highlights > legacy
  const accent = isFocused
    ? TOKENS.blue
    : effectiveReviewFlag
    ? TOKENS.amber               // 재검토중 → amber accent on Done card
    : hasActive
    ? TOKENS.active              // v3: 진행 중 → teal
    : hasBlocked
    ? TOKENS.red                 // v3: 막힘 → red
    : ticket.gateType
    ? TOKENS.red                 // legacy fallback
    : ticket.tone === "hcp"
    ? TOKENS.red
    : tone.border;

  // Re-review button: meaningful on completed or re-review WP cards.
  // Done card → "기획 의도 재검토 필요" 표시 (Done → Review).
  // Review card → "재검토 해제" (Review → Done).
  const showReviewButton =
    (ticket.column === "Done" || ticket.column === "Review") && ticket.type === "F-item";

  async function toggleReReview(e: React.MouseEvent) {
    e.stopPropagation();
    if (reviewBusy) return;
    const next = !effectiveReviewFlag;
    setLocalReviewFlag(next);
    setReviewBusy(true);
    if (STATIC_MODE) {
      // Standalone snapshot: toggle is local-only, no endpoint to persist to.
      setReviewBusy(false);
      return;
    }
    try {
      const wpCode = ticket.code.match(/WP-\d{3}/i)?.[0]
        ?? ticket.code.match(/F-\d{3}/i)?.[0];
      if (!wpCode) {
        setLocalReviewFlag(undefined);
        return;
      }
      const res = await fetch("/api/director-dashboard/re-review", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ wpCode, action: next ? "flag" : "unflag" }),
      });
      if (!res.ok) {
        // eslint-disable-next-line no-console
        console.error("[re-review] POST failed:", res.status);
        setLocalReviewFlag(undefined); // revert
      }
    } catch (err) {
      // eslint-disable-next-line no-console
      console.error("[re-review] POST error:", err);
      setLocalReviewFlag(undefined);
    } finally {
      setReviewBusy(false);
    }
  }

  // Headline strategy: plainTitle wins when present (Director-language);
  // dev label (`title`) gets demoted to the collapsible "개발 디테일" panel.
  const headline = ticket.plainTitle ?? ticket.title;
  const devLabel = ticket.plainTitle ? ticket.title : undefined;

  // v3 highlights — visual treatment varies by combination:
  //   active only         → teal bg tint + teal left stripe
  //   blocked only        → red bg tint + red left stripe
  //   active + blocked    → gradient bg + teal left stripe + red right stripe
  //   neither             → 기본 (tone bg, legacy accent left stripe)
  const cardStyle: React.CSSProperties = {
    position: "relative",
    background: hasActive && hasBlocked
      ? `linear-gradient(135deg, ${TOKENS.activeLight} 0%, ${TOKENS.redLight} 100%)`
      : hasActive
      ? TOKENS.activeLight
      : hasBlocked
      ? TOKENS.redLight
      : TOKENS.bgWhite,
    border: `1px solid ${isFocused ? TOKENS.blue : TOKENS.border}`,
    borderLeft: `3px solid ${accent}`,
    borderRight: hasActive && hasBlocked
      ? `3px solid ${TOKENS.red}`
      : `1px solid ${isFocused ? TOKENS.blue : TOKENS.border}`,
    opacity: dimmed ? 0.35 : 1,
    boxShadow: isFocused ? "var(--dd-cardLiftShadow)" : "var(--dd-cardShadow)",
    cursor: "pointer",
  };

  return (
    <div
      className="select-none rounded-md p-2.5 text-left transition-all"
      style={cardStyle}
      role="button"
      tabIndex={0}
      aria-pressed={isFocused}
      // v3 PR-5: data-ticket-id 로 FlowOverlay 가 카드 위치 측정
      data-ticket-id={ticket.id}
      onClick={() => onClick(ticket)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onClick(ticket);
        }
      }}
    >
      {/* v3 highlight badges — bg 진한 톤 (theme 무관) + white text + 보통 굵기. */}
      {hasActive && (
        <span
          aria-hidden
          style={{
            position: "absolute", top: -10, left: 8,
            fontSize: 10.5, fontWeight: 600, letterSpacing: 0.2,
            padding: "2px 9px", borderRadius: 9,
            background: "#0F766E", color: "#FFFFFF",
            lineHeight: 1.5,
          }}
        >
          ▶ Active
        </span>
      )}
      {hasBlocked && (
        <span
          aria-hidden
          style={{
            position: "absolute", top: -10, right: 8,
            fontSize: 10.5, fontWeight: 600, letterSpacing: 0.2,
            padding: "2px 9px", borderRadius: 9,
            background: "#B91C1C", color: "#FFFFFF",
            lineHeight: 1.5,
          }}
        >
          🛑 Blocked
        </span>
      )}
      {/* v3 Headline: plain title (large, full width). Type / gate chip 제거 —
          의미는 Active/Blocked badge + nextAction 으로 표현. */}
      <div
        className="mb-1 text-[14px] font-semibold leading-snug"
        style={{
          color: TOKENS.textPrimary,
          // top badges (Active/Blocked) 가 -9px 으로 튀어나오므로 약간 padding-top
          paddingTop: (hasActive || hasBlocked) ? 4 : 0,
        }}
      >
        {headline}
      </div>

      {/* Dev reference row: code (always), original English label if demoted */}
      <div
        className="font-mono text-[11px]"
        style={{ color: TOKENS.textMuted }}
      >
        {ticket.code}
        {devLabel && <span className="ml-1.5" style={{ fontFamily: "inherit" }}>· {devLabel}</span>}
      </div>

      {viewBadge && (
        <div className="mt-2">
          <span
            className="inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-semibold"
            style={{
              background: viewBadge.style.bg,
              color: viewBadge.style.fg,
              border: `1px solid ${viewBadge.style.border}`,
            }}
          >
            {viewBadge.label}
          </span>
        </div>
      )}

      {/* Done-card badge row: closure (always) + 🔄재검토중 marker (when flagged)
          + 🔄 toggle button (Director's outer-loop control). */}
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        {ticket.closure && (
          <div
            className="inline-flex items-center gap-1.5 rounded px-1.5 py-0.5"
            title={`${ticket.closure.title}${ticket.closure.date ? ` (${ticket.closure.date})` : ""}`}
            style={{
              background: ticket.closure.verdict === "closed" ? TOKENS.greenLight : TOKENS.redLight,
              border: `1px solid ${ticket.closure.verdict === "closed" ? TOKENS.green : TOKENS.red}`,
            }}
          >
            <span aria-hidden style={{ fontSize: 10 }}>
              {ticket.closure.verdict === "closed" ? "✓" : "✗"}
            </span>
            <span
              className="font-mono text-[10px] font-semibold"
              style={{
                color: ticket.closure.verdict === "closed" ? TOKENS.greenDark : TOKENS.redDark,
              }}
            >
              {ticket.closure.decisionId}
            </span>
            {ticket.closure.date && (
              <span className="text-[10px]" style={{ color: TOKENS.textMuted }}>
                {ticket.closure.date}
              </span>
            )}
          </div>
        )}
        {effectiveReviewFlag && (
          <span
            className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-semibold"
            title="Director가 기획 의도 어긋남으로 표시 — meta/re-review.md"
            style={{
              background: TOKENS.amberLight,
              color: TOKENS.amberDark,
              border: `1px solid ${TOKENS.amber}`,
            }}
          >
            🔄 재검토중
          </span>
        )}
        {showReviewButton && (
          <button
            type="button"
            disabled={reviewBusy}
            onClick={toggleReReview}
            title={effectiveReviewFlag ? "재검토 해제" : "기획 의도 어긋남 표시"}
            className="ml-auto inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium transition-opacity"
            style={{
              background: "transparent",
              color: effectiveReviewFlag ? TOKENS.amberDark : TOKENS.textMuted,
              border: `1px solid ${effectiveReviewFlag ? TOKENS.amber : TOKENS.border}`,
              cursor: reviewBusy ? "wait" : "pointer",
              opacity: reviewBusy ? 0.5 : 1,
            }}
          >
            🔄
          </button>
        )}
      </div>


      {ticket.progress && (() => {
        const p = ticket.progress;
        // Short word printed next to the number. The dashboard shows more than
        // one progress figure for the same card (this one, and the worklist
        // tally in the detail panel), and they are derived differently, so they
        // legitimately disagree. Naming each number's origin on the face of the
        // card is what stops the reader from having to guess which one is real.
        const sourceTag =
          p.source === "commits" ? "commits" : p.source === "merged" ? "merged" : "sentinel";
        const sourceLabel =
          p.source === "commits"
            ? `auto-derived from ${p.commitCount ?? 0} commit(s)`
            : p.source === "merged"
            ? "sentinel + commits agree"
            : "from state.json sentinel only";
        return (
          <div
            className="mt-2 flex items-center gap-2"
            title={
              `진행률 ${p.done}/${p.total} — 출처: ${sourceTag} (${sourceLabel})\n` +
              `원본: ${p.raw}${p.lastSha ? ` · last commit ${p.lastSha}` : ""}\n` +
              `상세 패널의 worklist 집계는 문서의 체크 상태를 세므로 이 숫자와 다를 수 있습니다.`
            }
          >
            <div
              aria-hidden
              style={{
                flex: 1,
                height: 4,
                borderRadius: 2,
                background: TOKENS.divider,
                overflow: "hidden",
              }}
            >
              <div
                style={{
                  height: "100%",
                  width: `${Math.min(
                    100,
                    Math.round((p.done / p.total) * 100),
                  )}%`,
                  background: p.done >= p.total
                    ? TOKENS.green
                    : hasBlocked
                    ? TOKENS.red
                    : hasActive
                    ? TOKENS.active
                    : TOKENS.blue,
                  transition: "width 0.3s ease",
                }}
              />
            </div>
            <span
              className="font-mono text-[10px] font-semibold"
              style={{ color: TOKENS.textSecondary }}
            >
              T-{p.done}/{p.total}
            </span>
            {/* Replaces the old 📡/🔗/✋ glyph. The glyph carried exactly this
                information but could only be decoded from the tooltip, so a
                plain lowercase word is both clearer and quieter. Kept muted and
                un-bolded so it annotates the number without competing with it. */}
            <span className="text-[10px]" style={{ color: TOKENS.textMuted }}>
              {sourceTag}
            </span>
            {p.lastSha && (
              <span
                className="font-mono text-[10px]"
                style={{ color: TOKENS.textMuted }}
              >
                {p.lastSha}
              </span>
            )}
          </div>
        );
      })()}

      {/* Director-facing "next action" — single Korean line that tells the
          Director what's happening / what to do without parsing the badges. */}
      {ticket.nextAction && (
        <div
          className="mt-2 text-[12px] leading-snug"
          style={{ color: TOKENS.textSecondary }}
        >
          {ticket.nextAction}
        </div>
      )}

      {ticket.subtasks && ticket.subtasks.length > 0 && (
        <button
          type="button"
          className="mt-2 text-[11px] font-medium"
          style={{ color: TOKENS.textMuted }}
          onClick={(e) => {
            e.stopPropagation();
            setExpanded((x) => !x);
          }}
        >
          {expanded ? "▾" : "▸"} {ticket.subtasks.length} subtask
          {ticket.subtasks.length === 1 ? "" : "s"}
        </button>
      )}
      {expanded && ticket.subtasks && (
        <ul className="mt-1.5 space-y-1">
          {ticket.subtasks.map((s) => (
            <li
              key={s.id}
              className="flex items-center gap-1.5 text-[11px]"
              style={{ color: TOKENS.textSecondary }}
            >
              <span
                aria-hidden
                style={{
                  display: "inline-block",
                  width: 10,
                  height: 10,
                  borderRadius: 2,
                  background: s.done ? TOKENS.green : "transparent",
                  border: `1px solid ${s.done ? TOKENS.green : TOKENS.border}`,
                }}
              />
              <span style={{ textDecoration: s.done ? "line-through" : "none" }}>
                {s.label}
              </span>
            </li>
          ))}
        </ul>
      )}

      {/* Collapsible developer details — keeps the dev-language description,
          closure tooltip text, and other technical context out of Director's
          face but still 1 click away when debugging. */}
      {(ticket.description || ticket.closure?.title) && (
        <>
          <button
            type="button"
            className="mt-2 text-[11px] font-medium"
            style={{ color: TOKENS.textMuted }}
            onClick={(e) => {
              e.stopPropagation();
              setDetailExpanded((x) => !x);
            }}
          >
            {detailExpanded ? "▾" : "▸"} 개발 디테일
          </button>
          {detailExpanded && (
            <div
              className="mt-1.5 rounded px-2 py-1.5 text-[11px] leading-relaxed"
              style={{
                background: TOKENS.bg,
                color: TOKENS.textSecondary,
                border: `1px solid ${TOKENS.divider}`,
              }}
            >
              {ticket.description && <div>{ticket.description}</div>}
              {ticket.closure?.title && (
                <div className="mt-1.5">
                  <span className="font-mono text-[10px]" style={{ color: TOKENS.textMuted }}>
                    {ticket.closure.decisionId}:
                  </span>{" "}
                  {ticket.closure.title}
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
