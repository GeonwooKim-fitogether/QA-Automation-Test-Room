// directorLexicon.mjs — code → Director-facing Korean translations.
//
// The dashboard's data layer uses developer codes (HCP-BILLING-API,
// "F-item", "HCP Gate", etc.) because that's what canonical 박제 sources
// carry. The Director's eye doesn't parse those — this lexicon maps each
// developer term to a 1-line Korean phrase that a planner can read.
//
// Single source of truth. Edit here; no other file should hardcode plain-
// Korean labels. Components import these maps and call friendlyHcp() /
// friendlyType() / gateLabel().
//
// All maps are intentionally plain objects (not enums) so missing entries
// fall back to the raw code via the friendly*() helpers — safer than
// throwing on unknown codes.

/**
 * HCP-XXX → Director-facing 1-line description. The dev code stays for
 * traceability; the Korean line is what shows on cards / summary chips.
 */
export const HCP_PLAIN = {
  "HCP-PAYMENT-SANDBOX-KEY":  "Toss 샌드박스 키 발급",
  "HCP-DB-SCHEMA-EXEC":       "DB 스키마 적용 (Director 직접 실행)",
  "HCP-DB-SCHEMA-EXEC-WP003": "DB 스키마 적용 (WP-003)",
  "HCP-DB-SCHEMA-EXEC-WP004": "DB 스키마 적용 (WP-004)",
  "HCP-DB-SCHEMA-EXEC-WP005": "DB 스키마 적용 (WP-005)",
  "HCP-BILLING-API":          "Billing Key API 실호출 동의",
  "HCP-CRON-SCHEDULER":       "Vercel Cron 스케줄 등록",
  "HCP-WEBHOOK":              "실제 결제 알림 URL 등록",
  "HCP-PAYMENT-PROD":         "운영 환경 결제 키 도입",
  "HCP-PAYMENT-LIVE":         "실제 결제 시도",
  "HCP-PRODUCTION":           "운영 배포 (사용자 공개)",
  "HCP-CRON-SCHEDULER-PROD":  "운영 Cron 등록 (out-of-cycle)",
  "HCP-INVOICE-DB-SCHEMA-EXEC":"청구서 DB 스키마 적용",
};

/**
 * Ticket type → Director-facing label. Replaces raw type strings on the
 * card header chip.
 */
export const TICKET_TYPE_PLAIN = {
  "F-item":   "기능",
  "Gate":     "관문",
  "HCP Gate": "🛑 보안 게이트",
  "Task":     "작업",
  "Meta":     "🔬 시스템 개선 후보",
  "Record":   "📜 결정 기록",
  "Queue":    "⏳ 결정 대기",
};

/**
 * Gate sub-type chip shown on cards in the "🔴 Director 차례" column.
 * PR-D consumes these. Three flavors map to one of:
 *   - Plan  : Planning Final CP awaiting Director acceptance
 *   - HCP   : One-off security/permission gate Director must approve
 *   - Final : Implementation Final CP (also covers Phase closure)
 */

export const GATE_TYPE_LABEL = {
  Plan:  "기획안 검토",
  HCP:   "보안 결정",
  Final: "최종 수락",
};

export const GATE_TYPE_CHIP = {
  Plan:  "[Plan]",
  HCP:   "[HCP]",
  Final: "[Final]",
};

// ---------------------------------------------------------------------------
// Friendly accessors with safe fallback to the raw code.
// ---------------------------------------------------------------------------

export function friendlyHcp(code) {
  if (HCP_PLAIN[code]) return HCP_PLAIN[code];
  // Reasonable fallback for unmapped HCPs — strip prefix, lowercase, dash→space.
  return code.replace(/^HCP-/i, "").toLowerCase().replace(/-/g, " ");
}

export function friendlyType(t) {
  return TICKET_TYPE_PLAIN[t] ?? t;
}

export function gateLabel(g) {
  return GATE_TYPE_LABEL[g];
}

export function gateChip(g) {
  return GATE_TYPE_CHIP[g];
}
