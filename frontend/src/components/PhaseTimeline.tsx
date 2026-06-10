import React from 'react';
import { tokens } from '../styles/tokens';
import { usePlannerStore } from '../store/plannerStore';
import type { Phase } from '../types/domain';

const PHASE_DOT: Record<Phase['status'], { glyph: string; color: string }> = {
  planned:   { glyph: '○', color: tokens.textMuted },
  active:    { glyph: '◉', color: tokens.green },
  completed: { glyph: '●', color: tokens.purple },
};

/**
 * Horizontal timeline of the project's phases. The active phase is
 * highlighted and its exit criteria are surfaced so the operator always
 * knows what "done" means for the current slice of work.
 */
export function PhaseTimeline() {
  const plan = usePlannerStore((s) => s.plan);
  if (!plan || plan.phases.length === 0) return null;

  const active = plan.phases.find((p) => p.index === plan.current_phase_index);

  return (
    <div style={{
      display: 'flex', flexDirection: 'column', gap: 6,
      padding: '10px 14px',
      background: tokens.panelBg + 'ee',
      border: `1px solid ${tokens.border}`,
      borderRadius: tokens.r8,
      backdropFilter: 'blur(8px)',
      maxWidth: 520,
    }}>
      <div style={{ fontSize: 8, fontFamily: tokens.fontMono, color: tokens.textMuted, letterSpacing: '0.1em' }}>
        PHASES · {plan.status.toUpperCase()}
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 0, flexWrap: 'wrap' }}>
        {plan.phases.map((p, i) => {
          const dot = PHASE_DOT[p.status];
          const isActive = p.index === plan.current_phase_index;
          return (
            <React.Fragment key={p.index}>
              {i > 0 && (
                <div style={{ width: 18, height: 1, background: tokens.border, margin: '0 4px' }} />
              )}
              <div
                title={`${p.goal}\nexit: ${p.exit_criteria || '—'}`}
                style={{
                  display: 'flex', alignItems: 'center', gap: 5,
                  padding: '3px 8px', borderRadius: tokens.r6,
                  background: isActive ? `${tokens.green}14` : 'transparent',
                  border: `1px solid ${isActive ? tokens.green + '55' : 'transparent'}`,
                }}
              >
                <span style={{ fontSize: 10, color: dot.color }}>{dot.glyph}</span>
                <span style={{
                  fontSize: 9, fontFamily: tokens.fontMono,
                  color: isActive ? tokens.textPrimary : tokens.textSecond,
                }}>
                  P{p.index} {p.name}
                </span>
              </div>
            </React.Fragment>
          );
        })}
      </div>

      {active && active.exit_criteria && (
        <div style={{ fontSize: 9, fontFamily: tokens.fontMono, color: tokens.textMuted, lineHeight: 1.5 }}>
          <span style={{ color: tokens.green }}>exit:</span> {active.exit_criteria}
        </div>
      )}
    </div>
  );
}
