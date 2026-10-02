// staticMode.ts — true when this bundle runs as a standalone static HTML
// snapshot (data injected via window.__DASHBOARD_DATA__) rather than inside a
// Next.js app with live /api/director-dashboard endpoints.
//
// In static mode every /api fetch is disabled:
//   - DirectorDashboardMVP: no 8s polling (initial injected data is final)
//   - DocPreviewModal: no doc-body fetch (show path + hint instead)
//   - TicketCard: re-review toggle is local-only (nothing to persist)
//
// The render orchestrator sets `window.__STATIC__ = true` in the generated HTML.
export const STATIC_MODE =
  typeof window !== "undefined" &&
  (window as unknown as { __STATIC__?: boolean }).__STATIC__ === true;
