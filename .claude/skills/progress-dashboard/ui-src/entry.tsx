// entry.tsx — standalone static bundle entry point.
//
// Reads the dashboard data injected by render.mjs as window.__DASHBOARD_DATA__
// and mounts the dashboard. window.__STATIC__ is also set by the generated
// HTML, which flips staticMode.ts → polling/doc-fetch/re-review POST off.
//
// This is the ONLY file unique to the standalone build; everything else under
// ui-src/ is the vendored Director Dashboard UI, unmodified except the three
// STATIC_MODE guards.

import { createElement } from "react";
import { createRoot } from "react-dom/client";
import { DirectorDashboardMVP } from "./components/DirectorDashboardMVP";
import type { DashboardData } from "./types";

declare global {
  interface Window {
    __DASHBOARD_DATA__?: DashboardData;
    __STATIC__?: boolean;
  }
}

const data = typeof window !== "undefined" ? window.__DASHBOARD_DATA__ : undefined;
const el = typeof document !== "undefined" ? document.getElementById("root") : null;

if (el) {
  createRoot(el).render(createElement(DirectorDashboardMVP, { data }));
}
