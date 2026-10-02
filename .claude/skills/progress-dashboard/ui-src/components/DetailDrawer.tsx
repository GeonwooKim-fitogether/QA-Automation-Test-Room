"use client";

// v3 PR-6: Detail panel — 좌측 WorkTree 와 대칭으로 우측 grid 3rd column.
// 카드 클릭 시 동적 render. width 280.

import { TOKENS, TONE_STYLES, TYPE_BADGE_STYLES } from "../styles/atlassianTokens";
import type { Ticket, WorklistData } from "../types";
import { WorklistPanel } from "./WorklistPanel";

interface Props {
  ticket: Ticket | null;
  related: Ticket[];
  isOpen: boolean;
  onClose: () => void;
  onTicketClick: (t: Ticket) => void;
  worklist?: WorklistData;
}

export function DetailDrawer({
  ticket,
  related,
  isOpen,
  onClose,
  onTicketClick,
  worklist,
}: Props) {
  if (!isOpen) return null;

  return (
    <aside
      className="flex h-full flex-col overflow-y-auto"
      style={{
        background: TOKENS.bgWhite,
        borderLeft: `1px solid ${TOKENS.border}`,
      }}
      aria-label="Detail panel"
    >
      {/* Header — code chip + type badge + close × */}
      <header
        className="flex items-center justify-between gap-2 px-3 py-2"
        style={{
          background: TOKENS.bg,
          borderBottom: `1px solid ${TOKENS.divider}`,
        }}
      >
        {ticket ? (
          <>
            <span
              className="font-mono text-[11px] font-semibold"
              style={{ color: TONE_STYLES[ticket.tone].fg }}
            >
              {ticket.code}
            </span>
            <div className="flex items-center gap-1.5">
              <span
                className="rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide"
                style={{
                  background: TYPE_BADGE_STYLES[ticket.type].bg,
                  color: TYPE_BADGE_STYLES[ticket.type].fg,
                  border: `1px solid ${TYPE_BADGE_STYLES[ticket.type].border}`,
                }}
              >
                {ticket.type}
              </span>
              <button
                type="button"
                onClick={onClose}
                aria-label="Close detail"
                title="Close (ESC)"
                className="flex items-center justify-center rounded"
                style={{
                  width: 22,
                  height: 22,
                  color: TOKENS.textMuted,
                  background: TOKENS.bgWhite,
                  border: `1px solid ${TOKENS.border}`,
                  fontSize: 12,
                  cursor: "pointer",
                  lineHeight: 1,
                }}
              >
                ×
              </button>
            </div>
          </>
        ) : (
          <>
            <span
              className="text-[11px] font-semibold uppercase tracking-wide"
              style={{ color: TOKENS.textMuted }}
            >
              Detail
            </span>
            <button
              type="button"
              onClick={onClose}
              aria-label="Close detail"
              className="flex items-center justify-center rounded"
              style={{
                width: 22,
                height: 22,
                color: TOKENS.textMuted,
                background: TOKENS.bgWhite,
                border: `1px solid ${TOKENS.border}`,
                fontSize: 12,
                cursor: "pointer",
                lineHeight: 1,
              }}
            >
              ×
            </button>
          </>
        )}
      </header>

      {!ticket ? (
        <div className="p-4 text-[12.5px] leading-relaxed" style={{ color: TOKENS.textMuted }}>
          보드 위 카드를 클릭하면 상세 정보 · 연결된 issue 가 여기에 나타나요.
        </div>
      ) : (
        <div className="space-y-3 p-3">
          <h2
            className="text-[14px] font-semibold leading-snug"
            style={{ color: TOKENS.textPrimary }}
          >
            {ticket.title}
          </h2>

          <dl className="grid grid-cols-[64px_1fr] gap-x-2 gap-y-1 text-[12px]">
            <dt style={{ color: TOKENS.textMuted }}>Column</dt>
            <dd style={{ color: TOKENS.textPrimary }}>{ticket.column}</dd>
            <dt style={{ color: TOKENS.textMuted }}>Lane</dt>
            <dd style={{ color: TOKENS.textPrimary }}>{ticket.lane}</dd>
            {ticket.actor && (
              <>
                <dt style={{ color: TOKENS.textMuted }}>Actor</dt>
                <dd style={{ color: TOKENS.textPrimary }}>{ticket.actor}</dd>
              </>
            )}
            {ticket.risk && (
              <>
                <dt style={{ color: TOKENS.textMuted }}>Risk</dt>
                <dd style={{ color: TOKENS.textPrimary }}>{ticket.risk}</dd>
              </>
            )}
          </dl>

          {ticket.description && (
            <p
              className="rounded p-2 text-[12px] leading-relaxed"
              style={{
                background: TOKENS.bg,
                color: TOKENS.textSecondary,
                border: `1px solid ${TOKENS.divider}`,
              }}
            >
              {ticket.description}
            </p>
          )}

          {worklist && (
            <WorklistPanel worklist={worklist} cardProgress={ticket.progress} />
          )}

          <div>
            <div
              className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider"
              style={{ color: TOKENS.textMuted }}
            >
              Linked Issues ({related.length})
            </div>
            {related.length === 0 ? (
              <div
                className="rounded p-2 text-[12px]"
                style={{ color: TOKENS.textMuted, background: TOKENS.bg }}
              >
                No linked issues.
              </div>
            ) : (
              <ul className="space-y-1">
                {related.map((r) => (
                  <li key={r.id}>
                    <button
                      type="button"
                      className="block w-full rounded px-2 py-1.5 text-left text-[12px] transition-colors"
                      style={{
                        background: TOKENS.bgWhite,
                        border: `1px solid ${TOKENS.border}`,
                        color: TOKENS.textPrimary,
                      }}
                      onClick={() => onTicketClick(r)}
                    >
                      <span
                        className="font-mono text-[10px] font-semibold"
                        style={{ color: TONE_STYLES[r.tone].fg }}
                      >
                        {r.code}
                      </span>
                      <span className="ml-1.5" style={{ color: TOKENS.textSecondary }}>
                        {r.plainTitle ?? r.title}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </aside>
  );
}
