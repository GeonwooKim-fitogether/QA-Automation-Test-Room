// Atlassian / Jira-like color tokens for the Director Dashboard MVP.
//
// Each TOKENS field is a CSS `var(--dd-…)` reference, NOT a literal color.
// The two palettes (light + dark) live in `THEME_CSS` and are mounted via
// <ThemeStyles /> at the dashboard root. Theme switches by flipping
// `data-dd-theme="dark"` on a wrapper element — no React re-render needed
// because every component just reads the same var() strings.
//
// Light tokens are the original Atlassian Light palette. Dark tokens are
// the published Atlassian Dark palette (neutral surfaces + accent overrides
// that read well on a dark ground). The shape of TOKENS / TONE_STYLES /
// TYPE_BADGE_STYLES is unchanged — every existing `TOKENS.bg` call site keeps
// working without edits.

import type { Tone, TicketType, BoardColumn } from "../types";

const v = (name: string) => `var(--dd-${name})` as const;

export const TOKENS = {
  textPrimary:   v("textPrimary"),
  textSecondary: v("textSecondary"),
  textMuted:     v("textMuted"),
  border:        v("border"),
  divider:       v("divider"),
  bg:            v("bg"),
  bgWhite:       v("bgWhite"),

  blue:       v("blue"),
  blueLight:  v("blueLight"),
  blueDark:   v("blueDark"),

  green:      v("green"),
  greenLight: v("greenLight"),
  greenDark:  v("greenDark"),

  amber:      v("amber"),
  amberLight: v("amberLight"),
  amberDark:  v("amberDark"),

  purple:     v("purple"),
  purpleLight:v("purpleLight"),
  purpleDark: v("purpleDark"),

  red:        v("red"),
  redLight:   v("redLight"),
  redDark:    v("redDark"),

  // v3: per-card "active" highlight (진행 중). column 과 독립 attribute.
  // teal hue → column 색 (blue Development 등) 과 충돌 X.
  active:      v("active"),
  activeLight: v("activeLight"),
  activeDark:  v("activeDark"),
} as const;

export interface ToneStyle {
  bg: string;
  fg: string;
  border: string;
  accent: string;
}

export const TONE_STYLES: Record<Tone, ToneStyle> = {
  done: {
    bg: TOKENS.greenLight,
    fg: TOKENS.greenDark,
    border: TOKENS.green,
    accent: TOKENS.green,
  },
  review: {
    bg: TOKENS.amberLight,
    fg: TOKENS.amberDark,
    border: TOKENS.amber,
    accent: TOKENS.amber,
  },
  selected: {
    bg: TOKENS.blueLight,
    fg: TOKENS.blueDark,
    border: TOKENS.blue,
    accent: TOKENS.blue,
  },
  progress: {
    bg: TOKENS.blueLight,
    fg: TOKENS.blueDark,
    border: TOKENS.blue,
    accent: TOKENS.blue,
  },
  backlog: {
    bg: TOKENS.bgWhite,
    fg: TOKENS.textSecondary,
    border: TOKENS.border,
    accent: TOKENS.textMuted,
  },
  meta: {
    bg: TOKENS.purpleLight,
    fg: TOKENS.purpleDark,
    border: TOKENS.purple,
    accent: TOKENS.purple,
  },
  hcp: {
    bg: TOKENS.redLight,
    fg: TOKENS.redDark,
    border: TOKENS.red,
    accent: TOKENS.red,
  },
};

export const TYPE_BADGE_STYLES: Record<TicketType, ToneStyle> = {
  "F-item": TONE_STYLES.progress,
  Gate: TONE_STYLES.review,
  "HCP Gate": TONE_STYLES.hcp,
  Task: TONE_STYLES.selected,
  Meta: TONE_STYLES.meta,
  Record: { bg: TOKENS.divider, fg: TOKENS.textSecondary, border: TOKENS.border, accent: TOKENS.textMuted },
  Queue: TONE_STYLES.hcp,
};

// v3 6-column model — JIRA-aligned cycle phase progression. Director-facing
// labels are English (JIRA convention) — Korean 부연은 swimlane outcome 에서
// 별도로 표시. Per-card 강조 (Active / Blocked) 는 column 과 독립 attribute.
//
// Order = left→right cycle flow:
//   Backlog → Plan → Development → Verification → Done → Review
export const COLUMN_ORDER: BoardColumn[] = [
  "Backlog",
  "Plan",
  "Development",
  "Verification",
  "Done",
  "Review",
];

export const COLUMN_LABEL: Record<BoardColumn, string> = {
  Backlog:      "Backlog",
  Plan:         "Plan",
  Development:  "Development",
  Verification: "Verification",
  Done:         "Done",
  Review:       "Review",
};

export const COLUMN_ACCENT: Record<BoardColumn, string> = {
  Backlog:      TOKENS.textMuted,
  Plan:         TOKENS.purple,
  Development:  TOKENS.blue,
  Verification: TOKENS.amber,
  Done:         TOKENS.green,
  Review:       TOKENS.red,
};

// ---------------------------------------------------------------------------
// Palette CSS — mounted once via <ThemeStyles />
// ---------------------------------------------------------------------------

/**
 * Two palettes scoped to `[data-dd-root]` so the dashboard's tokens never
 * leak into the rest of the host app. The light palette is the default;
 * a sibling rule overrides every token when `data-dd-theme="dark"` is set
 * on the same root.
 */
export const THEME_CSS = `
[data-dd-root] {
  --dd-textPrimary:   #172B4D;
  --dd-textSecondary: #42526E;
  --dd-textMuted:     #6B778C;
  --dd-border:        #DFE1E6;
  --dd-divider:       #EBECF0;
  --dd-bg:            #F4F5F7;
  --dd-bgWhite:       #FFFFFF;

  --dd-blue:       #0052CC;
  --dd-blueLight:  #DEEBFF;
  --dd-blueDark:   #0747A6;

  --dd-green:      #00875A;
  --dd-greenLight: #E3FCEF;
  --dd-greenDark:  #006644;

  --dd-amber:      #FF991F;
  --dd-amberLight: #FFFAE6;
  --dd-amberDark:  #974F0C;

  --dd-purple:     #6554C0;
  --dd-purpleLight:#EAE6FF;
  --dd-purpleDark: #403294;

  --dd-red:        #DE350B;
  --dd-redLight:   #FFEBE6;
  --dd-redDark:    #BF2600;

  --dd-active:      #14B8A6;
  --dd-activeLight: #CCFBF1;
  --dd-activeDark:  #0D9488;

  --dd-modalScrim: rgba(9, 30, 66, 0.54);
  --dd-cardShadow: 0 1px 0 rgba(9,30,66,0.12), 0 1px 2px rgba(9,30,66,0.06);
  --dd-cardLiftShadow: 0 2px 6px rgba(9,30,66,0.15);
  --dd-fabShadow: 0 4px 8px rgba(9,30,66,0.18);
  --dd-drawerShadow: -4px 0 12px rgba(9,30,66,0.10);
  --dd-modalShadow: 0 12px 32px rgba(9,30,66,0.30);
  --dd-toggleShadow: 0 1px 1px rgba(9,30,66,0.08);
  color-scheme: light;
}

[data-dd-root][data-dd-theme="dark"] {
  /* Atlassian Dark surfaces: ground → raised → overlay.
     Text uses the "blue-tinted neutral" family so it stays legible on dark. */
  --dd-textPrimary:   #C7D1DB;
  --dd-textSecondary: #9FADBC;
  --dd-textMuted:     #7C8B9D;
  --dd-border:        #2C333A;
  --dd-divider:       #232A31;
  --dd-bg:            #1D2125;
  --dd-bgWhite:       #22272B;

  /* Accent colors: brighter base + translucent tint that reads as "Light"
     in light mode but "Subtle" in dark. "Dark" variants stay readable on
     the dark ground so links and pill text don't go invisible. */
  --dd-blue:       #579DFF;
  --dd-blueLight:  rgba(87, 157, 255, 0.16);
  --dd-blueDark:   #85B8FF;

  --dd-green:      #4BCE97;
  --dd-greenLight: rgba(75, 206, 151, 0.16);
  --dd-greenDark:  #7EE2B8;

  --dd-amber:      #F5CD47;
  --dd-amberLight: rgba(245, 205, 71, 0.14);
  --dd-amberDark:  #F8E6A0;

  --dd-purple:     #9F8FEF;
  --dd-purpleLight:rgba(159, 143, 239, 0.16);
  --dd-purpleDark: #B8ACF6;

  --dd-red:        #F87168;
  --dd-redLight:   rgba(248, 113, 104, 0.14);
  --dd-redDark:    #FD9891;

  --dd-active:      #5EEAD4;
  --dd-activeLight: rgba(94, 234, 212, 0.14);
  --dd-activeDark:  #2DD4BF;

  --dd-modalScrim: rgba(0, 0, 0, 0.66);
  --dd-cardShadow: 0 1px 0 rgba(0,0,0,0.35), 0 1px 2px rgba(0,0,0,0.30);
  --dd-cardLiftShadow: 0 2px 6px rgba(0,0,0,0.45);
  --dd-fabShadow: 0 4px 12px rgba(0,0,0,0.55);
  --dd-drawerShadow: -4px 0 12px rgba(0,0,0,0.45);
  --dd-modalShadow: 0 12px 32px rgba(0,0,0,0.55);
  --dd-toggleShadow: 0 1px 1px rgba(0,0,0,0.35);
  color-scheme: dark;
}
`;
