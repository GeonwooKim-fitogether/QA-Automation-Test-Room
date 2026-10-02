"use client";

// GitActivityChip — header pill showing the last commit on the current branch.
//
// Surfaces git's own 박제 (sha + author + date + subject) so the Director
// can answer "Claude Code 가 마지막에 무엇을 했지?" without leaving the
// dashboard. Sourced from loadGitActivity() on the server; refreshed every
// 8s via the dashboard's polling loop.
//
// Click to copy the sha. Tooltip carries full subject + ISO date + branch.

import { useState } from "react";
import { TOKENS } from "../styles/atlassianTokens";
import type { GitActivity } from "../types";

interface Props {
  activity?: GitActivity;
}

/** Pull a leading T-NNN / WP-NNN / D-NNN / FIL-NNN code from a commit subject. */
function leadingCode(subject: string): string | null {
  const m = subject.match(/\b(T-\d{1,3}|WP-\d{3}|F-\d{3}|D-\d{3}|DQ-\d{3}|FIL-\d{3}|IP-\d{3})\b/i);
  return m ? m[1].toUpperCase() : null;
}

export function GitActivityChip({ activity }: Props) {
  const [copied, setCopied] = useState(false);

  if (!activity) {
    return (
      <span
        title="git log unavailable (not a git repo or git not on PATH)"
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 4,
          padding: "4px 8px",
          background: TOKENS.bgWhite,
          color: TOKENS.textMuted,
          border: `1px solid ${TOKENS.border}`,
          borderRadius: 3,
          fontSize: 11,
          fontWeight: 600,
        }}
      >
        <span aria-hidden style={{ fontSize: 10 }}>—</span>
        <span>no git</span>
      </span>
    );
  }

  // Capture into a const so the copySha closure sees a narrowed value
  // (TS can't narrow `activity` across a function declaration boundary).
  const sha = activity.sha;
  const code = leadingCode(activity.subject);
  const dirty = (activity.dirtyFiles ?? 0) > 0;

  function copySha() {
    void navigator.clipboard?.writeText(sha).catch(() => {});
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  const tooltip =
    `${activity.subject}\n` +
    `${activity.sha} · ${activity.author} · ${activity.isoTime}\n` +
    (activity.branch ? `branch: ${activity.branch}\n` : "") +
    (dirty ? `dirty: ${activity.dirtyFiles} file(s) uncommitted` : "working tree clean");

  return (
    <button
      type="button"
      onClick={copySha}
      title={tooltip}
      aria-label={`Last commit ${activity.sha} ${activity.relativeTime}. Click to copy sha.`}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        padding: "4px 8px",
        background: TOKENS.bgWhite,
        color: TOKENS.textSecondary,
        border: `1px solid ${TOKENS.border}`,
        borderRadius: 3,
        fontSize: 11,
        fontWeight: 600,
        cursor: "pointer",
        lineHeight: 1,
      }}
    >
      <span
        aria-hidden
        style={{
          display: "inline-block",
          width: 8,
          height: 8,
          borderRadius: "50%",
          background: dirty ? TOKENS.amber : TOKENS.green,
        }}
      />
      <span
        style={{
          fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
          color: TOKENS.textPrimary,
        }}
      >
        {copied ? "copied!" : activity.sha}
      </span>
      {code && (
        <span
          style={{
            fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
            color: TOKENS.blueDark,
          }}
        >
          {code}
        </span>
      )}
      <span style={{ color: TOKENS.textMuted, fontWeight: 500 }}>
        {activity.relativeTime}
      </span>
      {dirty && (
        <span
          style={{
            color: TOKENS.amberDark,
            fontWeight: 600,
          }}
        >
          · {activity.dirtyFiles}∆
        </span>
      )}
    </button>
  );
}
