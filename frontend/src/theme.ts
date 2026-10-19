import { useSyncExternalStore } from "react";

/** Light or dark, chosen by the user (kept in localStorage) or following the system. */
export type Theme = "light" | "dark";
const KEY = "dea-theme";
const listeners = new Set<() => void>();

function stored(): Theme | null {
  try { const v = localStorage.getItem(KEY); return v === "light" || v === "dark" ? v : null; } catch { return null; }
}
function system(): Theme {
  return typeof window !== "undefined" && window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}
export function currentTheme(): Theme { return stored() ?? system(); }

function apply(): void {
  const chosen = stored();
  const root = document.documentElement;
  if (chosen) root.setAttribute("data-theme", chosen); else root.removeAttribute("data-theme");
  listeners.forEach((l) => l());
}

export function setTheme(t: Theme | null): void {
  try { if (t) localStorage.setItem(KEY, t); else localStorage.removeItem(KEY); } catch { /* private mode */ }
  apply();
}

/** The theme in effect; re-renders on toggle and when the system switches while following it. */
export function useTheme(): Theme {
  return useSyncExternalStore(
    (cb) => { listeners.add(cb); return () => { listeners.delete(cb); }; },
    currentTheme,
    () => "light",
  );
}

if (typeof window !== "undefined") {
  apply();
  window.matchMedia?.("(prefers-color-scheme: dark)").addEventListener?.("change", () => { if (!stored()) listeners.forEach((l) => l()); });
}
