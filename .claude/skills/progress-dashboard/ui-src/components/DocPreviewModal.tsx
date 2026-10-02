"use client";

import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { TOKENS } from "../styles/atlassianTokens";
import type { DocLink } from "../types";
import {
  type DocTone,
  docKindLabel,
  docTone,
  docToneCategory,
  vscodeFileUrl,
} from "../utils/docs";
import { STATIC_MODE } from "../staticMode";

const DOT_COLOR: Record<DocTone, string> = {
  neutral: TOKENS.textMuted,
  blue:    TOKENS.blue,
  amber:   TOKENS.amber,
  green:   TOKENS.green,
  purple:  TOKENS.purple,
};

interface Props {
  doc: DocLink;
  onClose: () => void;
}

export function DocPreviewModal({ doc, onClose }: Props) {
  const [content, setContent] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const isJson = doc.relPath.toLowerCase().endsWith(".json");

  // Fetch the doc body once per open.
  useEffect(() => {
    let cancelled = false;
    setContent(null);
    setError(null);
    if (STATIC_MODE) {
      // Standalone snapshot has no /api/doc endpoint to read repo files.
      setError("문서 미리보기는 정적 스냅샷에서 지원되지 않습니다. 아래 경로를 복사해 에디터에서 여세요.");
      return;
    }
    const ctrl = new AbortController();
    fetch(`/api/director-dashboard/doc?path=${encodeURIComponent(doc.relPath)}`, {
      cache: "no-store",
      signal: ctrl.signal,
    })
      .then(async (r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.text();
      })
      .then((text) => {
        if (!cancelled) setContent(text);
      })
      .catch((err) => {
        if (!cancelled && (err as { name?: string }).name !== "AbortError") {
          setError(String((err as Error).message ?? err));
        }
      });
    return () => {
      cancelled = true;
      ctrl.abort();
    };
  }, [doc.relPath]);

  // ESC to close.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  function copyPath() {
    void navigator.clipboard?.writeText(doc.relPath).catch(() => {});
  }

  function openInVscode() {
    window.open(vscodeFileUrl(doc.absPath), "_self");
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`Document preview · ${doc.title}`}
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        background: "var(--dd-modalScrim)",
        zIndex: 100,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 24,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: TOKENS.bgWhite,
          width: "min(900px, 70vw)",
          maxHeight: "85vh",
          borderRadius: 6,
          display: "flex",
          flexDirection: "column",
          boxShadow: "var(--dd-modalShadow)",
          overflow: "hidden",
        }}
      >
        {/* Header */}
        <header
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            padding: "10px 14px",
            background: TOKENS.bg,
            borderBottom: `1px solid ${TOKENS.divider}`,
          }}
        >
          {(() => {
            const tone = docTone(doc.kind);
            const label = docKindLabel(doc.kind);
            return (
              <div
                style={{ display: "flex", alignItems: "center", gap: 6, flexShrink: 0 }}
                title={`${label} — ${docToneCategory(tone)}`}
              >
                <span
                  aria-hidden
                  style={{
                    display: "inline-block",
                    width: 10,
                    height: 10,
                    borderRadius: "50%",
                    background: DOT_COLOR[tone],
                    boxShadow: tone === "neutral" ? `inset 0 0 0 1px ${TOKENS.border}` : "none",
                  }}
                />
                <span
                  style={{
                    fontSize: 10,
                    fontWeight: 700,
                    color: TOKENS.textMuted,
                    textTransform: "uppercase",
                    letterSpacing: 0.4,
                  }}
                >
                  {label}
                </span>
              </div>
            );
          })()}
          <div style={{ display: "flex", flexDirection: "column", minWidth: 0, flex: 1 }}>
            <span
              style={{
                fontSize: 13,
                fontWeight: 600,
                color: TOKENS.textPrimary,
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
              }}
              title={doc.title}
            >
              {doc.title}
            </span>
            <span
              style={{
                fontSize: 10,
                color: TOKENS.textMuted,
                fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
              }}
              title={doc.relPath}
            >
              {doc.relPath}
            </span>
          </div>

          <HeaderButton onClick={openInVscode} title="Open the file in VSCode">
            VSCode ↗
          </HeaderButton>
          <HeaderButton onClick={copyPath} title={`Copy: ${doc.relPath}`}>
            Copy path
          </HeaderButton>
          <HeaderButton onClick={onClose} title="Close (ESC)" emphasis="muted">
            ✕
          </HeaderButton>
        </header>

        {/* Body */}
        <main
          style={{
            overflow: "auto",
            padding: "20px 28px",
            background: TOKENS.bgWhite,
            color: TOKENS.textPrimary,
            fontSize: 13,
            lineHeight: 1.6,
          }}
        >
          {error && (
            <div
              style={{
                background: TOKENS.redLight,
                border: `1px solid ${TOKENS.red}`,
                color: TOKENS.redDark,
                borderRadius: 3,
                padding: 12,
                fontSize: 12,
              }}
            >
              <strong>Failed to load this document.</strong>
              <div style={{ marginTop: 4, fontFamily: "ui-monospace, monospace" }}>{error}</div>
            </div>
          )}

          {!content && !error && (
            <div style={{ color: TOKENS.textMuted, fontSize: 12 }}>Loading…</div>
          )}

          {content && isJson && (
            <pre
              style={{
                margin: 0,
                padding: 14,
                background: TOKENS.bg,
                border: `1px solid ${TOKENS.divider}`,
                borderRadius: 3,
                fontSize: 11,
                lineHeight: 1.5,
                fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
                color: TOKENS.textPrimary,
              }}
            >
              {content}
            </pre>
          )}

          {content && !isJson && (
            <div className="dd-md">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
            </div>
          )}
        </main>
      </div>

      {/* Lightweight markdown styling scoped to this modal. */}
      <style>{`
        .dd-md h1 { font-size: 22px; font-weight: 700; margin: 0 0 12px; color: ${TOKENS.textPrimary}; border-bottom: 1px solid ${TOKENS.divider}; padding-bottom: 6px; }
        .dd-md h2 { font-size: 18px; font-weight: 700; margin: 22px 0 8px; color: ${TOKENS.textPrimary}; }
        .dd-md h3 { font-size: 15px; font-weight: 700; margin: 18px 0 6px; color: ${TOKENS.textPrimary}; }
        .dd-md h4 { font-size: 13px; font-weight: 700; margin: 14px 0 4px; color: ${TOKENS.textPrimary}; }
        .dd-md p { margin: 0 0 10px; }
        .dd-md ul, .dd-md ol { margin: 0 0 10px 22px; padding: 0; }
        .dd-md li { margin: 2px 0; }
        .dd-md li > p { margin: 0; }
        .dd-md code { background: ${TOKENS.bg}; border: 1px solid ${TOKENS.divider}; border-radius: 3px; padding: 0 4px; font-size: 12px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
        .dd-md pre { background: ${TOKENS.bg}; border: 1px solid ${TOKENS.divider}; border-radius: 3px; padding: 10px; overflow: auto; font-size: 11px; line-height: 1.5; }
        .dd-md pre code { background: transparent; border: 0; padding: 0; }
        .dd-md table { border-collapse: collapse; margin: 8px 0 14px; font-size: 12px; }
        .dd-md th, .dd-md td { border: 1px solid ${TOKENS.divider}; padding: 6px 10px; text-align: left; }
        .dd-md th { background: ${TOKENS.bg}; font-weight: 700; }
        .dd-md blockquote { border-left: 3px solid ${TOKENS.border}; padding: 4px 12px; margin: 0 0 10px; color: ${TOKENS.textSecondary}; }
        .dd-md a { color: ${TOKENS.blueDark}; text-decoration: underline; }
        .dd-md hr { border: 0; border-top: 1px solid ${TOKENS.divider}; margin: 16px 0; }
      `}</style>
    </div>
  );
}

function HeaderButton({
  onClick,
  title,
  children,
  emphasis,
}: {
  onClick: () => void;
  title: string;
  children: React.ReactNode;
  emphasis?: "muted";
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      style={{
        flexShrink: 0,
        padding: "4px 10px",
        background: TOKENS.bgWhite,
        color: emphasis === "muted" ? TOKENS.textMuted : TOKENS.textSecondary,
        border: `1px solid ${TOKENS.border}`,
        borderRadius: 3,
        fontSize: 11,
        fontWeight: 600,
        cursor: "pointer",
        lineHeight: 1,
      }}
    >
      {children}
    </button>
  );
}
