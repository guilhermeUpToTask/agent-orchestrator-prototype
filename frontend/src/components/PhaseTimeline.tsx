import React from 'react';
import { tokens, PLAN_PHASE } from '../styles/tokens';
import type { PlanPhase } from '../types/ui';

/** The happy-path walk; REPLANNING re-enters at architecture, FAILED is terminal. */
const WALK: PlanPhase[] = [
  'discovery',
  'architecture',
  'enriching',
  'awaiting_review',
  'running',
  'review',
  'done',
];

/**
 * Horizontal timeline of the 9-phase machine. The current phase is
 * highlighted; phases before the cursor render as settled. REPLANNING and
 * FAILED are shown as an annotation since they sit outside the happy path.
 */
export function PhaseTimeline({
  phase, iteration,
}: {
  phase: PlanPhase;
  iteration: number;
}) {
  const cursor = WALK.indexOf(phase === 'replanning' ? 'architecture' : phase);

  return (
    <div style={{
      display: 'flex', flexDirection: 'column', gap: 6,
      padding: '10px 14px',
      background: 'color-mix(in srgb, var(--bg-1) 93%, transparent)',
      border: `1px solid ${tokens.border}`,
      borderRadius: tokens.r8,
      backdropFilter: 'blur(8px)',
      maxWidth: 640,
    }}>
      <div style={{ fontSize: 8, fontFamily: tokens.fontMono, color: tokens.textMuted, letterSpacing: '0.1em' }}>
        PHASES · {PLAN_PHASE[phase].label.toUpperCase()} · ITERATION {iteration}
        {phase === 'replanning' && ' · REPLANNING'}
        {phase === 'failed' && ' · FAILED'}
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 0, flexWrap: 'wrap' }}>
        {WALK.map((p, i) => {
          const isCurrent = p === phase || (phase === 'replanning' && p === 'architecture');
          const isPast = cursor >= 0 && i < cursor;
          const color = isCurrent
            ? PLAN_PHASE[p].kind === 'gate' ? tokens.yellow : tokens.green
            : isPast ? tokens.purple : tokens.textMuted;
          return (
            <React.Fragment key={p}>
              {i > 0 && (
                <div style={{ width: 18, height: 1, background: tokens.border, margin: '0 4px' }} />
              )}
              <div style={{
                display: 'flex', alignItems: 'center', gap: 5,
                padding: '3px 8px', borderRadius: tokens.r6,
                background: isCurrent ? `${color}14` : 'transparent',
                border: `1px solid ${isCurrent ? color + '55' : 'transparent'}`,
              }}>
                <span style={{ fontSize: 10, color }}>
                  {isPast ? '●' : isCurrent ? '◉' : '○'}
                </span>
                <span style={{
                  fontSize: 9, fontFamily: tokens.fontMono,
                  color: isCurrent ? tokens.textPrimary : tokens.textSecond,
                }}>
                  {PLAN_PHASE[p].label}
                </span>
              </div>
            </React.Fragment>
          );
        })}
      </div>
    </div>
  );
}
