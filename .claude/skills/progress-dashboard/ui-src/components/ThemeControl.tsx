"use client";

// ThemeControl — owns the dashboard's light/dark mode.
//
// Mounts `<ThemeStyles />` once (injects CSS-var palette into the page) and
// exposes `<ThemeToggle />` for the header. Theme state is held on the
// dashboard's `data-dd-root` wrapper as `data-dd-theme="dark"` (or absent
// for light); persisted to localStorage, and seeded from the OS preference
// the first time the user opens the dashboard.

import { useCallback, useEffect, useState } from "react";
import { TOKENS, THEME_CSS } from "../styles/atlassianTokens";

const STORAGE_KEY = "dd-theme";
type Theme = "light" | "dark";

function readInitialTheme(): Theme {
  if (typeof window === "undefined") return "light";
  const saved = window.localStorage.getItem(STORAGE_KEY);
  if (saved === "dark" || saved === "light") return saved;
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

/** Mount once at the dashboard root — injects the palette CSS. */
export function ThemeStyles() {
  return <style dangerouslySetInnerHTML={{ __html: THEME_CSS }} />;
}

/**
 * Hook that owns the theme state and applies it to the nearest
 * `[data-dd-root]` ancestor (the dashboard container). Returns the current
 * theme + a toggle function.
 */
export function useDashboardTheme() {
  const [theme, setTheme] = useState<Theme>("light");

  // Read initial value after mount (SSR-safe).
  useEffect(() => {
    setTheme(readInitialTheme());
  }, []);

  // Mirror to the [data-dd-root] wrapper + persist on every change.
  useEffect(() => {
    const root = document.querySelector<HTMLElement>("[data-dd-root]");
    if (root) {
      if (theme === "dark") root.dataset.ddTheme = "dark";
      else delete root.dataset.ddTheme;
    }
    try {
      window.localStorage.setItem(STORAGE_KEY, theme);
    } catch {
      // localStorage may be blocked (private mode / cookie policy). Theme
      // still works for the current session — just won't survive a reload.
    }
  }, [theme]);

  const toggle = useCallback(() => {
    setTheme((t) => (t === "dark" ? "light" : "dark"));
  }, []);

  return { theme, toggle };
}

interface ToggleProps {
  theme: Theme;
  onToggle: () => void;
}

/** Header pill — `☀ Light` / `🌙 Dark` label, click to flip. */
export function ThemeToggle({ theme, onToggle }: ToggleProps) {
  const isDark = theme === "dark";
  return (
    <button
      type="button"
      onClick={onToggle}
      title={isDark ? "Switch to light mode" : "Switch to dark mode"}
      aria-label={isDark ? "Switch to light mode" : "Switch to dark mode"}
      aria-pressed={isDark}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        padding: "4px 10px",
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
      <span aria-hidden style={{ fontSize: 12 }}>{isDark ? "🌙" : "☀"}</span>
      <span>{isDark ? "Dark" : "Light"}</span>
    </button>
  );
}
