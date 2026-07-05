/**
 * src/lib/theme.ts
 *
 * Theme = the `data-theme` attribute on <html>; global.css defines dark as
 * the default and a full [data-theme='light'] override block. Applied at
 * module-eval time from main.tsx so the first paint is already themed.
 */

export type Theme = 'dark' | 'light';

const STORAGE_KEY = 'aipom.theme';

export function getInitialTheme(): Theme {
  const stored = localStorage.getItem(STORAGE_KEY);
  if (stored === 'dark' || stored === 'light') return stored;
  return window.matchMedia?.('(prefers-color-scheme: light)').matches
    ? 'light'
    : 'dark';
}

export function applyTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem(STORAGE_KEY, theme);
}

export function currentTheme(): Theme {
  return document.documentElement.dataset.theme === 'light' ? 'light' : 'dark';
}
