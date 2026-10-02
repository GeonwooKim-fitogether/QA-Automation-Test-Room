import { TOKENS } from "../styles/atlassianTokens";
import { VIEW_MODES } from "../utils/viewMode";
import type { ViewMode } from "../types";

interface Props {
  value: ViewMode;
  onChange: (next: ViewMode) => void;
}

export function ViewModeToggle({ value, onChange }: Props) {
  return (
    <div
      className="inline-flex rounded-md p-0.5"
      role="tablist"
      aria-label="View mode"
      style={{
        background: TOKENS.divider,
        border: `1px solid ${TOKENS.border}`,
      }}
    >
      {VIEW_MODES.map((mode) => {
        const active = mode === value;
        return (
          <button
            key={mode}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onChange(mode)}
            className="rounded-sm px-2.5 py-1 text-xs font-medium transition-colors"
            style={{
              background: active ? TOKENS.bgWhite : "transparent",
              color: active ? TOKENS.blueDark : TOKENS.textSecondary,
              border: active ? `1px solid ${TOKENS.border}` : "1px solid transparent",
              boxShadow: active ? "var(--dd-toggleShadow)" : "none",
            }}
          >
            {mode}
          </button>
        );
      })}
    </div>
  );
}
