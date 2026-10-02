"use client";

// TopBarV3 — v3 unified top bar (mock δ format).
// Layout:
//   [Title]   GROUP [Phase|Agent]   FLOW [On|Off]   ▶ Active N   🛑 HCP N   [sub-controls]
//
// Hierarchy axes:
//   - meta-label (Group/Flow) = uppercase 10px dim — *분류 이름*
//   - segmented control       = bordered 24px height — *선택지*
//   - badge                   = pill bg color — *정보 카운터*
//
// Active/HCP counts derive from per-card highlights attribute set by
// buildDashboardData.deriveHighlights. Group/Flow toggles are state-only
// in this PR — Swimlane rebuild lives in PR-4, Flow overlay in PR-5.

import type { Ticket } from "../types";
import { TOKENS } from "../styles/atlassianTokens";

export type GroupMode = "phase" | "agent";
export type FlowMode = "on" | "off";
export type ThemeMode = "light" | "dark";

interface Props {
  projectName: string;
  tickets: Ticket[];
  groupMode: GroupMode;
  onGroupChange: (g: GroupMode) => void;
  flowMode: FlowMode;
  onFlowChange: (f: FlowMode) => void;
  themeMode: ThemeMode;
  onThemeChange: (t: ThemeMode) => void;
  /** Live indicator slot (SyncIndicator) — placed before Theme segment */
  liveSlot?: React.ReactNode;
  /** Misc controls (Clear, etc.) — appears at the start of the right cluster */
  rightSlot?: React.ReactNode;
}

export function TopBarV3({
  projectName,
  tickets,
  groupMode,
  onGroupChange,
  flowMode,
  onFlowChange,
  themeMode,
  onThemeChange,
  liveSlot,
  rightSlot,
}: Props) {
  const activeCount  = tickets.filter((t) => t.highlights?.includes("active")).length;
  const blockedCount = tickets.filter((t) => t.highlights?.includes("blocked")).length;

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        // Wrap instead of pushing the page wider. The bar's three right-hand
        // groups need ~820px on their own; on a narrow viewport a nowrap bar
        // forces the whole document to scroll horizontally, which puts the
        // Theme toggle and the badges off-screen. Wrapping only engages when
        // the row would not have fit anyway, so wide screens look unchanged.
        flexWrap: "wrap",
        rowGap: 8,
        columnGap: 12,
        padding: "10px 16px",
        background: TOKENS.bgWhite,
        borderBottom: `1px solid ${TOKENS.border}`,
        minHeight: 52,
      }}
    >
      {/* LEFT — Title */}
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, minWidth: 0 }}>
        <h1 style={{ margin: 0, fontSize: 14, fontWeight: 600, color: TOKENS.textPrimary, flexShrink: 0 }}>
          Director Dashboard
        </h1>
        <span
          style={{
            fontSize: 11.5,
            color: TOKENS.textMuted,
            // A long project name must ellipsize rather than re-introduce the
            // horizontal overflow this bar was just taught to avoid.
            minWidth: 0,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          · {projectName}
        </span>
      </div>

      {/* misc rightSlot (Clear button etc) — attached to title left side */}
      {rightSlot && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            marginLeft: 18,
            opacity: 0.85,
          }}
        >
          {rightSlot}
        </div>
      )}

      {/* RIGHT — 3 groups (Live | Toggles | Badges), wider inter-group gap */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          flexWrap: "wrap",
          justifyContent: "flex-end",
          columnGap: 24,
          rowGap: 8,
          marginLeft: "auto",
        }}
      >

        {/* Group 1: Live indicator */}
        {liveSlot && (
          <div style={{ display: "flex", alignItems: "center", opacity: 0.85 }}>
            {liveSlot}
          </div>
        )}

        {/* Group 2: Toggles (Group / Flow / Theme) */}
        <div style={{ display: "flex", alignItems: "center", flexWrap: "wrap", columnGap: 14, rowGap: 8 }}>
          <Field
            label="Group"
            options={[
              { value: "phase", label: "Phase" },
              { value: "agent", label: "Agent" },
            ]}
            value={groupMode}
            onChange={onGroupChange}
          />
          <Field
            label="Flow"
            options={[
              { value: "on",  label: "On" },
              { value: "off", label: "Off" },
            ]}
            value={flowMode}
            onChange={onFlowChange}
          />
          <Field
            label="Theme"
            options={[
              { value: "light", label: "Light" },
              { value: "dark",  label: "Dark" },
            ]}
            value={themeMode}
            onChange={onThemeChange}
          />
        </div>

        {/* Group 3: Badges (Active + HCP, 한 박스 안 직사각형, height align) */}
        <div
          style={{
            display: "inline-flex",
            alignItems: "stretch",
            height: 24,
            borderRadius: 5,
            overflow: "hidden",
            border: `1px solid ${TOKENS.border}`,
            boxSizing: "border-box",
          }}
        >
          <span
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 4,
              padding: "0 10px",
              background: "#0F766E",
              color: "#FFFFFF",
              fontSize: 11,
              fontWeight: 600,
              lineHeight: 1,
              height: "100%",
            }}
          >
            ▶ Active {activeCount}
          </span>
          <span
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 4,
              padding: "0 10px",
              background: "#B91C1C",
              color: "#FFFFFF",
              fontSize: 11,
              fontWeight: 600,
              lineHeight: 1,
              height: "100%",
            }}
          >
            🛑 HCP {blockedCount}
          </span>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Field — meta-label + segmented (hierarchy: label = dim caps / seg = action)
// ---------------------------------------------------------------------------

interface FieldOption<V extends string> {
  value: V;
  label: string;
}

interface FieldProps<V extends string> {
  label: string;
  options: FieldOption<V>[];
  value: V;
  onChange: (v: V) => void;
}

function Field<V extends string>({ label, options, value, onChange }: FieldProps<V>) {
  return (
    <div style={{ display: "inline-flex", alignItems: "center", gap: 7 }}>
      <span
        style={{
          fontSize: 10,
          fontWeight: 600,
          letterSpacing: 1,
          textTransform: "uppercase",
          color: TOKENS.textMuted,
        }}
      >
        {label}
      </span>
      <div
        style={{
          display: "inline-flex",
          height: 24,
          background: TOKENS.bg,
          border: `1px solid ${TOKENS.border}`,
          borderRadius: 5,
          overflow: "hidden",
        }}
      >
        {options.map((opt) => {
          const on = opt.value === value;
          return (
            <button
              key={opt.value}
              type="button"
              onClick={() => onChange(opt.value)}
              style={{
                border: 0,
                background: on ? "rgba(255,255,255,0.10)" : "transparent",
                color: on ? TOKENS.textPrimary : TOKENS.textMuted,
                fontWeight: on ? 600 : 400,
                padding: "0 11px",
                fontSize: 11.5,
                lineHeight: "22px",
                cursor: "pointer",
                fontFamily: "inherit",
              }}
            >
              {opt.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}
