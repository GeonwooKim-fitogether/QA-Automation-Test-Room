"use client";

import { useEffect, useState } from "react";
import { TOKENS } from "../styles/atlassianTokens";

interface Props {
  lastSyncAt: Date | null;
  error: string | null;
  intervalMs: number;
}

function formatElapsed(now: Date, last: Date): string {
  const sec = Math.max(0, Math.round((now.getTime() - last.getTime()) / 1000));
  if (sec < 60)         return `${sec}s ago`;
  if (sec < 3600)       return `${Math.floor(sec / 60)}m ago`;
  return `${Math.floor(sec / 3600)}h ago`;
}

export function SyncIndicator({ lastSyncAt, error, intervalMs }: Props) {
  // Re-render every second so the "Ns ago" label stays fresh.
  const [, force] = useState(0);
  useEffect(() => {
    const id = setInterval(() => force((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, []);

  const now = new Date();
  const isError = error !== null;
  const stale = lastSyncAt && now.getTime() - lastSyncAt.getTime() > intervalMs * 2;

  const dotColor = isError
    ? TOKENS.red
    : stale
    ? TOKENS.amber
    : TOKENS.green;

  const label = isError
    ? "Sync error"
    : lastSyncAt
    ? `Live · ${formatElapsed(now, lastSyncAt)}`
    : "Syncing…";

  return (
    <div
      title={
        isError
          ? `Last error: ${error}`
          : lastSyncAt
          ? `Last sync at ${lastSyncAt.toLocaleTimeString()} · polling every ${intervalMs / 1000}s`
          : "Waiting for first sync…"
      }
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        padding: "4px 8px",
        background: TOKENS.bgWhite,
        border: `1px solid ${TOKENS.border}`,
        borderRadius: 3,
        fontSize: 11,
        fontWeight: 600,
        color: TOKENS.textSecondary,
      }}
    >
      <span
        aria-hidden
        style={{
          display: "inline-block",
          width: 8,
          height: 8,
          borderRadius: "50%",
          background: dotColor,
          animation: isError || stale ? "none" : "dd-pulse 2s ease-in-out infinite",
        }}
      />
      <span>{label}</span>
      <style>{`
        @keyframes dd-pulse {
          0%, 100% { opacity: 1;   }
          50%      { opacity: 0.45; }
        }
      `}</style>
    </div>
  );
}
