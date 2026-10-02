// useLiveAlerts.ts — Browser Notification API integration for the 8s polling.
//
// v2 contract: dashboard fires a system notification whenever a NEW
// Blocked / Verification (Director Final 검수) ticket appears between two
// polls. Director can dismiss the dashboard tab during AI work; the
// browser will surface a notification the moment Director attention
// is required.
//
// First-load is intentionally silent — without this guard the hook would
// fire an alarm for every existing stuck card on session start (spam).
// Subsequent polls compare against localStorage and fire only on new ids.
//
// Permission is requested once on mount. If the user denies, the hook
// degrades silently — the dashboard's visual 🛑 column still works.

"use client";

import { useEffect, useRef } from "react";
import type { DashboardData } from "../types";

const STORAGE_KEY = "dd-lastSeenStuckIds";

function loadSeen(): Set<string> {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return new Set();
    const arr = JSON.parse(raw) as unknown;
    if (!Array.isArray(arr)) return new Set();
    return new Set(arr.filter((x): x is string => typeof x === "string"));
  } catch {
    return new Set();
  }
}

function saveSeen(ids: Iterable<string>): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(Array.from(ids)));
  } catch {
    // localStorage full / denied — silently ignore.
  }
}

export function useLiveAlerts(data: DashboardData | null): void {
  // One-time permission request on first mount.
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (typeof Notification === "undefined") return;
    if (Notification.permission === "default") {
      // Fire-and-forget; never await user response.
      Notification.requestPermission().catch(() => {});
    }
  }, []);

  // Hook-instance flag: avoid firing on the *first* data tick after mount
  // (those stucks are not "new" — they were there before Director opened
  // the tab). Subsequent ticks fire alerts on diff.
  const initialized = useRef(false);

  useEffect(() => {
    if (!data) return;
    if (typeof window === "undefined") return;
    if (typeof Notification === "undefined") return;

    const currentStuckIds = data.tickets
      .filter((t) => t.column === "Verification")
      .map((t) => t.id);
    const currentSet = new Set(currentStuckIds);

    if (!initialized.current) {
      initialized.current = true;
      // Seed localStorage with the current set; no alerts on first tick.
      saveSeen(currentSet);
      return;
    }

    if (Notification.permission !== "granted") {
      // Still update the seen set so we don't accumulate alerts when
      // permission is granted later mid-session.
      saveSeen(currentSet);
      return;
    }

    const lastSeen = loadSeen();
    const newStuckIds = currentStuckIds.filter((id) => !lastSeen.has(id));

    for (const id of newStuckIds) {
      const ticket = data.tickets.find((t) => t.id === id);
      if (!ticket) continue;
      const title = ticket.plainTitle ?? ticket.title;
      try {
        new Notification(`🛑 ${title}`, {
          body: "Director 결정 필요",
          tag: id,             // dedupes if alert is still on screen
        });
      } catch {
        // Some browsers throw outside a user gesture — silently ignore.
      }
    }

    saveSeen(currentSet);
  }, [data]);
}
