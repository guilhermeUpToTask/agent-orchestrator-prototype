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
  Ban, CheckCircle2, CircleDashed, Compass, Eye, GitMerge, GitPullRequest,
  Hand, Loader2, PencilRuler, Play, RotateCcw, XCircle, type LucideIcon,
} from 'lucide-react';
import type { GoalStatus, ProjectPlanStatus, TaskStatus } from '../types/ui';

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

export const TASK_STATUS: Record<TaskStatus, StatusMeta> = {
  created:     { kind: 'idle', label: 'Queued',    Icon: CircleDashed },
  assigned:    { kind: 'run',  label: 'Assigned',  Icon: Play },
  in_progress: { kind: 'run',  label: 'Running',   Icon: Loader2, spin: true },
  succeeded:   { kind: 'ok',   label: 'Succeeded', Icon: CheckCircle2 },
  merged:      { kind: 'ok',   label: 'Merged',    Icon: GitMerge },
  failed:      { kind: 'fail', label: 'Failed',    Icon: XCircle },
  canceled:    { kind: 'idle', label: 'Canceled',  Icon: Ban },
  requeued:    { kind: 'run',  label: 'Requeued',  Icon: RotateCcw },
};

export const GOAL_STATUS: Record<GoalStatus, StatusMeta> = {
  pending:              { kind: 'idle', label: 'Pending',       Icon: CircleDashed },
  running:              { kind: 'run',  label: 'Running',       Icon: Loader2, spin: true },
  ready_for_review:     { kind: 'gate', label: 'Ready for review', Icon: Eye },
  awaiting_pr_approval: { kind: 'gate', label: 'PR review',     Icon: GitPullRequest },
  approved:             { kind: 'run',  label: 'Approved',      Icon: GitMerge },
  merged:               { kind: 'ok',   label: 'Merged',        Icon: GitMerge },
  completed:            { kind: 'ok',   label: 'Completed',     Icon: CheckCircle2 },
  failed:               { kind: 'fail', label: 'Failed',        Icon: XCircle },
};

export const PLAN_STATUS: Record<ProjectPlanStatus, StatusMeta> = {
  discovery:    { kind: 'run',  label: 'Discovery',    Icon: Compass },
  architecture: { kind: 'run',  label: 'Architecture', Icon: PencilRuler },
  phase_active: { kind: 'run',  label: 'Phase active', Icon: Play },
  phase_review: { kind: 'gate', label: 'Phase review', Icon: Hand },
  done:         { kind: 'ok',   label: 'Done',         Icon: CheckCircle2 },
};

/** PR check / approval line items (booleans from GoalResponse). */
export const checkMeta = (ok: boolean | null | undefined): StatusMeta =>
  ok ? { kind: 'ok', label: 'Passed', Icon: CheckCircle2 }
    : ok === false ? { kind: 'fail', label: 'Failing', Icon: XCircle }
    : { kind: 'run', label: 'Pending', Icon: Loader2, spin: true };

// ─── Raw values for canvas-rendered chrome (React Flow can't read CSS vars
//     in every prop) — keep in sync with global.css dark theme ─────────────

export const raw = {
  bg0: '#131416', bg1: '#1a1b1e', bg2: '#222327',
  border0: '#2e3035', border1: '#3d4046',
  text3: '#84878f',
  idle: '#84878f', run: '#5a9cf8', gate: '#e0a430', ok: '#4cc38a', fail: '#ef6a6a',
} as const;

/* ════════════════════════════════════════════════════════════════════════
   LEGACY SECTION — keeps not-yet-migrated components (ChatPanel,
   DetailPanel, TaskNode, GoalGroupNode, PhaseTimeline, PlanCanvas)
   compiling against the new palette. Delete as each is migrated.
   ═══════════════════════════════════════════════════════════════════════ */

export const tokens = {
  bg: raw.bg0, panelBg: raw.bg1, cardBg: raw.bg2, cardHover: '#2a2c31', inputBg: raw.bg0,
  border: raw.border0, borderFocus: raw.run, borderMuted: '#26272b',
  accent: raw.run, accentHover: '#7cb0fa', accentDim: '#16243c', accentGlow: 'rgba(90,156,248,0.18)',
  green: raw.ok, greenDim: '#122b1f',
  yellow: raw.gate, yellowDim: '#332708',
  red: raw.fail, redDim: '#371616',
  purple: raw.run, purpleDim: '#16243c',   // purple deleted from the system → blue
  cyan: raw.run, cyanDim: '#16243c',
  orange: raw.gate, orangeDim: '#332708',
  textPrimary: '#ececee', textSecond: '#b4b6bc', textMuted: raw.text3, textDim: '#5a5d64',
  fontMono: "'IBM Plex Mono', ui-monospace, monospace",
  fontSans: "'IBM Plex Sans', system-ui, sans-serif",
  r4: '3px', r6: '6px', r8: '6px', r12: '10px',
} as const;

/** @deprecated agent identity colors are removed — agents render as neutral name chips */
export const AGENT_COLORS: Record<string, string> = {};

export type StatusKey =
  | 'created' | 'assigned' | 'in_progress'
  | 'succeeded' | 'failed' | 'canceled'
  | 'requeued' | 'merged';

/** @deprecated use TASK_STATUS + StatusBadge */
export const STATUS_META: Record<StatusKey, { label: string; color: string; bg: string; dot: string }> =
  Object.fromEntries(
    (Object.keys(TASK_STATUS) as StatusKey[]).map((k) => {
      const m = TASK_STATUS[k];
      const v = { idle: raw.idle, run: raw.run, gate: raw.gate, ok: raw.ok, fail: raw.fail }[m.kind];
      const bg = { idle: '#25262a', run: '#16243c', gate: '#332708', ok: '#122b1f', fail: '#371616' }[m.kind];
      return [k, { label: m.label.toUpperCase(), color: v, bg, dot: v }];
    }),
  ) as Record<StatusKey, { label: string; color: string; bg: string; dot: string }>;

/** @deprecated use GOAL_STATUS + StatusBadge */
export const GOAL_STATUS_META = Object.fromEntries(
  Object.entries(GOAL_STATUS).map(([k, m]) => [k, {
    label: m.label.toUpperCase(),
    color: { idle: raw.idle, run: raw.run, gate: raw.gate, ok: raw.ok, fail: raw.fail }[m.kind],
  }]),
) as Record<GoalStatus, { label: string; color: string }>;

/** @deprecated use PLAN_STATUS + StatusBadge */
export const PHASE_STATUS_META = Object.fromEntries(
  Object.entries(PLAN_STATUS).map(([k, m]) => [k, {
    label: m.label.toUpperCase(),
    color: { idle: raw.idle, run: raw.run, gate: raw.gate, ok: raw.ok, fail: raw.fail }[m.kind],
  }]),
) as Record<ProjectPlanStatus, { label: string; color: string }>;
