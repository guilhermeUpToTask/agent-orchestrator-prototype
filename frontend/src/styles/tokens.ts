/**
 * src/styles/tokens.ts
 *
 * The source of truth for design tokens is CSS variables in global.css.
 * This file is (1) a thin typed mirror for the few places React needs raw
 * values (React Flow edges, minimap colors), and (2) the unified STATUS
 * model: one semantic kind per status, identical everywhere.
 *
 * Status semantics — color is reserved for state:
 *   idle (gray)  not started        run (blue)  machine working
 *   gate (amber) waiting on YOU     ok (green)  settled
 *   fail (red)   failed / disconnected
 */

import {
  Ban, CheckCircle2, CircleDashed, Compass, Eye, Hand, ListTree, Loader2,
  PencilRuler, Play, RefreshCw, XCircle, type LucideIcon,
} from 'lucide-react';
import type { PlanPhase, Status } from '../types/ui';

// ─── Semantic kinds ─────────────────────────────────────────────────────────

export type StatusKind = 'idle' | 'run' | 'gate' | 'ok' | 'fail';

/** CSS variable names per kind — components style via these, never raw hex. */
export const KIND_VARS: Record<StatusKind, { fg: string; bg: string; text: string }> = {
  idle: { fg: 'var(--idle)', bg: 'var(--idle-bg)', text: 'var(--idle-text)' },
  run:  { fg: 'var(--run)',  bg: 'var(--run-bg)',  text: 'var(--run-text)' },
  gate: { fg: 'var(--gate)', bg: 'var(--gate-bg)', text: 'var(--gate-text)' },
  ok:   { fg: 'var(--ok)',   bg: 'var(--ok-bg)',   text: 'var(--ok-text)' },
  fail: { fg: 'var(--fail)', bg: 'var(--fail-bg)', text: 'var(--fail-text)' },
};

export interface StatusMeta {
  kind: StatusKind;
  label: string;
  Icon: LucideIcon;
  /** Spin the icon (running states only) */
  spin?: boolean;
}

// ─── Per-domain status maps ─────────────────────────────────────────────────

/** Goal/task lifecycle (the shared Status enum). */
export const STATUS: Record<Status, StatusMeta> = {
  pending: { kind: 'idle', label: 'Pending', Icon: CircleDashed },
  running: { kind: 'run',  label: 'Running', Icon: Loader2, spin: true },
  done:    { kind: 'ok',   label: 'Done',    Icon: CheckCircle2 },
  failed:  { kind: 'fail', label: 'Failed',  Icon: XCircle },
  skipped: { kind: 'idle', label: 'Skipped', Icon: Ban },
};

/** The 9-phase machine. Gates are amber — anything amber is your queue. */
export const PLAN_PHASE: Record<PlanPhase, StatusMeta> = {
  discovery:       { kind: 'run',  label: 'Discovery',     Icon: Compass },
  replanning:      { kind: 'run',  label: 'Replanning',    Icon: RefreshCw },
  architecture:    { kind: 'run',  label: 'Architecture',  Icon: PencilRuler },
  enriching:       { kind: 'run',  label: 'Enriching',     Icon: ListTree, spin: false },
  awaiting_review: { kind: 'gate', label: 'Awaiting review', Icon: Eye },
  running:         { kind: 'run',  label: 'Running',       Icon: Play },
  review:          { kind: 'gate', label: 'Review',        Icon: Hand },
  done:            { kind: 'ok',   label: 'Done',          Icon: CheckCircle2 },
  failed:          { kind: 'fail', label: 'Failed',        Icon: XCircle },
};

/** Phases the operator drives through chat (the conversational phases). */
export const CHAT_PHASES: PlanPhase[] = ['discovery', 'replanning'];

// ─── Raw values for canvas-rendered chrome (React Flow can't read CSS vars
//     in every prop) — keep in sync with global.css dark theme ─────────────

export const raw = {
  bg0: '#131416', bg1: '#1a1b1e', bg2: '#222327',
  border0: '#2e3035', border1: '#3d4046',
  text3: '#84878f',
  idle: '#84878f', run: '#5a9cf8', gate: '#e0a430', ok: '#4cc38a', fail: '#ef6a6a',
} as const;

export const tokens = {
  bg: raw.bg0, panelBg: raw.bg1, cardBg: raw.bg2, cardHover: '#2a2c31', inputBg: raw.bg0,
  border: raw.border0, borderFocus: raw.run, borderMuted: '#26272b',
  accent: raw.run, accentHover: '#7cb0fa', accentDim: '#16243c', accentGlow: 'rgba(90,156,248,0.18)',
  green: raw.ok, greenDim: '#122b1f',
  yellow: raw.gate, yellowDim: '#332708',
  red: raw.fail, redDim: '#371616',
  purple: raw.run, purpleDim: '#16243c',
  cyan: raw.run, cyanDim: '#16243c',
  orange: raw.gate, orangeDim: '#332708',
  textPrimary: '#ececee', textSecond: '#b4b6bc', textMuted: raw.text3, textDim: '#5a5d64',
  fontMono: "'IBM Plex Mono', ui-monospace, monospace",
  fontSans: "'IBM Plex Sans', system-ui, sans-serif",
  r4: '3px', r6: '6px', r8: '6px', r12: '10px',
} as const;
