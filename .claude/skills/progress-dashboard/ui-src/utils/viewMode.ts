// View mode badge resolver — produces a per-ticket label and tone for the
// secondary badge slot, based on the active ViewMode.

import { TOKENS, TONE_STYLES } from "../styles/atlassianTokens";
import type { ToneStyle } from "../styles/atlassianTokens";
import type { Ticket, ViewMode } from "../types";

export interface ViewBadge {
  label: string;
  style: ToneStyle;
}

export function viewBadgeFor(ticket: Ticket, mode: ViewMode): ViewBadge | null {
  switch (mode) {
    case "Default":
      return null;
    case "Status":
      return {
        label: ticket.column,
        style: TONE_STYLES[ticket.tone],
      };
    case "Actor":
      return {
        label: ticket.actor ?? "—",
        style: actorStyle(ticket.actor),
      };
    case "Risk":
      return {
        label: ticket.risk ? ticket.risk.toUpperCase() : "—",
        style: riskStyle(ticket.risk),
      };
    case "Links":
      return {
        label: `links: ${ticket.links?.length ?? 0}`,
        style: TONE_STYLES.selected,
      };
  }
}

function actorStyle(actor: Ticket["actor"]): ToneStyle {
  switch (actor) {
    case "Director":
      return TONE_STYLES.hcp;
    case "GPT":
      return TONE_STYLES.meta;
    case "Claude Code":
      return TONE_STYLES.selected;
    case "System":
    default:
      return {
        bg: TOKENS.divider,
        fg: TOKENS.textSecondary,
        border: TOKENS.border,
        accent: TOKENS.textMuted,
      };
  }
}

function riskStyle(risk: Ticket["risk"]): ToneStyle {
  switch (risk) {
    case "high":
      return TONE_STYLES.hcp;
    case "med":
      return TONE_STYLES.review;
    case "low":
      return TONE_STYLES.done;
    default:
      return {
        bg: TOKENS.divider,
        fg: TOKENS.textSecondary,
        border: TOKENS.border,
        accent: TOKENS.textMuted,
      };
  }
}

export const VIEW_MODES: ViewMode[] = ["Default", "Status", "Actor", "Risk", "Links"];
