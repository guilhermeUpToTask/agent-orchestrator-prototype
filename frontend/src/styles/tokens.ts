export const tokens = {
  // surfaces
  bg:          '#0b0d12',
  panelBg:     '#0f1117',
  cardBg:      '#13161f',
  cardHover:   '#181c2a',
  inputBg:     '#0b0d12',

  // borders
  border:      '#1c2030',
  borderFocus: '#3b6ef5',
  borderMuted: '#161923',

  // brand
  accent:      '#3b6ef5',
  accentHover: '#5580f7',
  accentDim:   '#1a2e6e',
  accentGlow:  'rgba(59,110,245,0.18)',

  // semantic
  green:       '#22c55e',
  greenDim:    '#052e16',
  yellow:      '#f59e0b',
  yellowDim:   '#451a03',
  red:         '#ef4444',
  redDim:      '#450a0a',
  purple:      '#a855f7',
  purpleDim:   '#2e1065',
  cyan:        '#06b6d4',
  cyanDim:     '#083344',
  orange:      '#f97316',
  orangeDim:   '#431407',

  // text
  textPrimary: '#e2e8f0',
  textSecond:  '#94a3b8',
  textMuted:   '#475569',
  textDim:     '#2d3748',

  // fonts
  fontMono:    "'JetBrains Mono', 'Fira Code', monospace",
  fontSans:    "'Geist', 'DM Sans', system-ui, sans-serif",

  // radius
  r4: '4px',
  r6: '6px',
  r8: '8px',
  r12: '12px',
} as const;

// ─── Agent colors ──────────────────────────────────────────────────────────
export const AGENT_COLORS: Record<string, string> = {
  planner:     tokens.purple,
  architect:   tokens.cyan,
  coder:       tokens.accent,
  reviewer:    tokens.yellow,
  tester:      tokens.green,
  documenter:  tokens.orange,
  default:     tokens.textSecond,
};

// ─── Status styles ─────────────────────────────────────────────────────────
export type StatusKey =
  | 'created' | 'assigned' | 'in_progress'
  | 'succeeded' | 'failed' | 'canceled'
  | 'requeued' | 'merged';

export const STATUS_META: Record<StatusKey, { label: string; color: string; bg: string; dot: string }> = {
  created:     { label: 'CREATED',     color: tokens.textMuted,  bg: '#13151c', dot: tokens.textMuted },
  assigned:    { label: 'ASSIGNED',    color: tokens.cyan,       bg: '#0a1a20', dot: tokens.cyan },
  in_progress: { label: 'RUNNING',     color: tokens.yellow,     bg: '#1a1200', dot: tokens.yellow },
  succeeded:   { label: 'SUCCEEDED',   color: tokens.green,      bg: '#061310', dot: tokens.green },
  failed:      { label: 'FAILED',      color: tokens.red,        bg: '#150707', dot: tokens.red },
  canceled:    { label: 'CANCELED',    color: tokens.textMuted,  bg: '#13151c', dot: tokens.textMuted },
  requeued:    { label: 'REQUEUED',    color: tokens.orange,     bg: '#150d04', dot: tokens.orange },
  merged:      { label: 'MERGED',      color: tokens.purple,     bg: '#120a20', dot: tokens.purple },
};

export const GOAL_STATUS_META = {
  pending:               { label: 'PENDING',    color: tokens.textMuted },
  running:               { label: 'RUNNING',    color: tokens.yellow },
  ready_for_review:      { label: 'READY',      color: tokens.cyan },
  awaiting_pr_approval:  { label: 'PR REVIEW',  color: tokens.purple },
  approved:              { label: 'APPROVED',   color: tokens.accent },
  merged:                { label: 'MERGED',     color: tokens.green },
  failed:                { label: 'FAILED',     color: tokens.red },
  completed:             { label: 'COMPLETED',  color: tokens.green },
};

export const PHASE_STATUS_META = {
  discovery:    { label: 'DISCOVERY',    color: tokens.purple },
  architecture: { label: 'ARCHITECTURE', color: tokens.cyan },
  phase_active: { label: 'PHASE ACTIVE', color: tokens.green },
  phase_review: { label: 'PHASE REVIEW', color: tokens.yellow },
  done:         { label: 'DONE',         color: tokens.green },
};
