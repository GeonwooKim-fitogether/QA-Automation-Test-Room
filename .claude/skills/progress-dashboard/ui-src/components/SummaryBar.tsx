import { TOKENS } from "../styles/atlassianTokens";

export type ChipTone = "neutral" | "blue" | "amber" | "green" | "red" | "purple";

export interface Chip {
  label: string;            // e.g. "Last shipped" / "Awaiting you"
  value: string | number;   // primary value
  sub?: string;             // secondary value (ID / date / actor)
  tone: ChipTone;
}

interface Props {
  projectName: string;
  chips: Chip[];
}

// Dot color carries the chip semantics; everything else is neutral.
const DOT: Record<ChipTone, string> = {
  neutral: TOKENS.textMuted,
  blue:    TOKENS.blue,
  amber:   TOKENS.amber,
  green:   TOKENS.green,
  red:     TOKENS.red,
  purple:  TOKENS.purple,
};

export function SummaryBar({ projectName, chips }: Props) {
  return (
    <div
      className="flex flex-wrap items-center gap-4 px-5 py-2.5"
      style={{
        background: TOKENS.bgWhite,
        borderBottom: `1px solid ${TOKENS.border}`,
        color: TOKENS.textPrimary,
      }}
    >
      <div className="flex flex-col" style={{ minWidth: 220 }}>
        <span
          className="text-[10px] font-semibold uppercase tracking-wider"
          style={{ color: TOKENS.textMuted }}
        >
          Director Dashboard
        </span>
        <span className="text-[13px] font-semibold" style={{ color: TOKENS.textPrimary }}>
          {projectName}
        </span>
      </div>

      <div className="h-8 w-px" style={{ background: TOKENS.divider }} />

      <div className="flex flex-wrap items-stretch gap-2">
        {chips.map((c) => {
          const dotColor = DOT[c.tone];
          return (
            <div
              key={c.label}
              className="flex min-w-0 flex-col rounded px-2.5 py-1.5"
              style={{
                background: TOKENS.bgWhite,
                border: `1px solid ${TOKENS.border}`,
                minWidth: 132,
                maxWidth: 240,
              }}
              title={c.sub ? `${c.label}: ${c.value} · ${c.sub}` : `${c.label}: ${c.value}`}
            >
              <span
                className="flex items-center gap-1.5 text-[9px] font-bold uppercase tracking-wider"
                style={{ color: TOKENS.textMuted }}
              >
                <span
                  aria-hidden
                  style={{
                    display: "inline-block",
                    width: 8,
                    height: 8,
                    borderRadius: "50%",
                    background: dotColor,
                    flexShrink: 0,
                    // subtle ring for low-contrast (e.g. neutral on white) so the
                    // dot remains visible without yelling.
                    boxShadow: c.tone === "neutral" ? `inset 0 0 0 1px ${TOKENS.border}` : "none",
                  }}
                />
                {c.label}
              </span>
              <span
                className="truncate text-[12px] font-semibold leading-tight"
                style={{ color: TOKENS.textPrimary, marginTop: 2 }}
              >
                {c.value}
              </span>
              {c.sub && (
                <span
                  className="truncate text-[10px] font-medium leading-tight"
                  style={{ color: TOKENS.textMuted }}
                >
                  {c.sub}
                </span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
