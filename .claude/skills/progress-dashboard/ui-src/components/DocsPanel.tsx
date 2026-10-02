"use client";

import { useState } from "react";
import { TOKENS } from "../styles/atlassianTokens";
import type { DocKind, DocLink, TreeNode } from "../types";
import {
  type DocTone,
  docKindLabel,
  docTone,
  docToneCategory,
  docsForTreeNode,
  emptyStateHint,
} from "../utils/docs";
import { DocPreviewModal } from "./DocPreviewModal";

interface Props {
  selectedNode: TreeNode | null;
  docs: DocLink[] | undefined;
}

const DOT: Record<DocTone, string> = {
  neutral: TOKENS.textMuted,
  blue:    TOKENS.blue,
  amber:   TOKENS.amber,
  green:   TOKENS.green,
  purple:  TOKENS.purple,
};

// One example row per tone so the spectrum is self-documenting.
const LEGEND_ROWS: { tone: DocTone; kinds: DocKind[] }[] = [
  { tone: "blue",    kinds: ["spec", "design", "worklist", "verification", "brief", "blueprint"] },
  { tone: "green",   kinds: ["operating-model", "ontology", "project-brief"] },
  { tone: "amber",   kinds: ["decisions", "decision-queue"] },
  { tone: "purple",  kinds: ["meta-sprint-spec", "improvement-log"] },
  { tone: "neutral", kinds: ["session-handoff", "execution-log", "status", "state"] },
];

export function DocsPanel({ selectedNode, docs }: Props) {
  const matches = docsForTreeNode(selectedNode, docs);
  const [previewing, setPreviewing] = useState<DocLink | null>(null);
  const [legendOpen, setLegendOpen] = useState(false);

  return (
    <div
      aria-label="Related documents"
      style={{
        borderTop: `1px solid ${TOKENS.divider}`,
        background: TOKENS.bg,
        padding: "8px 4px",
        display: "flex",
        flexDirection: "column",
        gap: 4,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          padding: "0 4px 4px",
        }}
      >
        <span
          style={{
            fontSize: 10,
            fontWeight: 700,
            letterSpacing: 0.4,
            color: TOKENS.textMuted,
            textTransform: "uppercase",
          }}
        >
          Documents
        </span>
        <span style={{ fontSize: 10, color: TOKENS.textMuted }}>
          {!docs ? "…" : matches.length === 0 ? "0" : matches.length}
        </span>
      </div>

      {!docs && (
        <div style={{ padding: "6px 4px", fontSize: 11, color: TOKENS.textMuted }}>
          Loading…
        </div>
      )}

      {docs && matches.length === 0 && (
        <div style={{ padding: "6px 6px", fontSize: 11, color: TOKENS.textMuted, lineHeight: 1.45 }}>
          {emptyStateHint(selectedNode)}
        </div>
      )}

      {docs && matches.length > 0 && (
        <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: 2 }}>
          {matches.map((d) => {
            const tone = docTone(d.kind);
            const label = docKindLabel(d.kind);
            return (
              <li key={d.id}>
                <button
                  type="button"
                  onClick={() => setPreviewing(d)}
                  title={`${label} — ${docToneCategory(tone)}\n${d.relPath}`}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                    width: "100%",
                    textAlign: "left",
                    padding: "4px 6px",
                    fontSize: 11,
                    lineHeight: 1.3,
                    color: TOKENS.textPrimary,
                    borderRadius: 3,
                    background: TOKENS.bgWhite,
                    border: `1px solid ${TOKENS.divider}`,
                    cursor: "pointer",
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.borderColor = TOKENS.blue;
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.borderColor = TOKENS.divider;
                  }}
                >
                  <span
                    aria-hidden
                    style={{
                      display: "inline-block",
                      width: 8,
                      height: 8,
                      borderRadius: "50%",
                      background: DOT[tone],
                      boxShadow: tone === "neutral" ? `inset 0 0 0 1px ${TOKENS.border}` : "none",
                      flexShrink: 0,
                    }}
                  />
                  <span
                    style={{
                      fontSize: 10,
                      color: TOKENS.textMuted,
                      minWidth: 56,
                      fontWeight: 600,
                      flexShrink: 0,
                    }}
                  >
                    {label}
                  </span>
                  <span
                    style={{
                      minWidth: 0,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                      flex: 1,
                    }}
                  >
                    {d.title}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}

      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "4px 4px 0",
          fontSize: 10,
          color: TOKENS.textMuted,
        }}
      >
        <span>click to preview · VSCode option inside</span>
        <button
          type="button"
          onClick={() => setLegendOpen((v) => !v)}
          style={{
            background: "transparent",
            border: 0,
            color: TOKENS.blueDark,
            cursor: "pointer",
            fontSize: 10,
            padding: "0 2px",
          }}
          aria-expanded={legendOpen}
        >
          {legendOpen ? "hide legend ▾" : "what do the dots mean? ▸"}
        </button>
      </div>

      {legendOpen && <Legend />}

      {previewing && (
        <DocPreviewModal doc={previewing} onClose={() => setPreviewing(null)} />
      )}
    </div>
  );
}

function Legend() {
  return (
    <div
      style={{
        marginTop: 4,
        padding: 8,
        background: TOKENS.bgWhite,
        border: `1px solid ${TOKENS.divider}`,
        borderRadius: 3,
        display: "flex",
        flexDirection: "column",
        gap: 4,
      }}
    >
      {LEGEND_ROWS.map((row) => (
        <div key={row.tone} style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span
            aria-hidden
            style={{
              display: "inline-block",
              width: 8,
              height: 8,
              borderRadius: "50%",
              background: DOT[row.tone],
              boxShadow: row.tone === "neutral" ? `inset 0 0 0 1px ${TOKENS.border}` : "none",
              flexShrink: 0,
            }}
          />
          <span
            style={{
              fontSize: 10,
              fontWeight: 700,
              color: TOKENS.textSecondary,
              minWidth: 84,
            }}
          >
            {docToneCategory(row.tone)}
          </span>
          <span style={{ fontSize: 10, color: TOKENS.textMuted, lineHeight: 1.35 }}>
            {row.kinds.map(docKindLabel).join(" · ")}
          </span>
        </div>
      ))}
    </div>
  );
}
