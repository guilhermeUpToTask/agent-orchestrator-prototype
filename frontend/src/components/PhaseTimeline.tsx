import React from 'react';
import { PLAN_PHASE } from '../styles/tokens';
import type { PlanPhase } from '../types/ui';
import styles from './PhaseTimeline.module.css';

/** Join the class names that are actually present. */
const cx = (...names: (string | false | undefined)[]) => names.filter(Boolean).join(' ');

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
    <div className={styles.timeline}>
      <div className={styles.caption}>
        PHASES · {PLAN_PHASE[phase].label.toUpperCase()} · ITERATION {iteration}
        {phase === 'replanning' && ' · REPLANNING'}
        {phase === 'failed' && ' · FAILED'}
      </div>

      <div className={styles.walk}>
        {WALK.map((p, i) => {
          const isCurrent = p === phase || (phase === 'replanning' && p === 'architecture');
          const isPast = cursor >= 0 && i < cursor;
          return (
            <React.Fragment key={p}>
              {i > 0 && <div className={styles.connector} />}
              <div
                className={cx(
                  styles.pill,
                  isPast && styles.pillPast,
                  isCurrent && styles.pillCurrent,
                  isCurrent && PLAN_PHASE[p].kind === 'gate' && styles.pillCurrentGate,
                )}
              >
                <span className={styles.marker}>
                  {isPast ? '●' : isCurrent ? '◉' : '○'}
                </span>
                <span className={styles.label}>{PLAN_PHASE[p].label}</span>
              </div>
            </React.Fragment>
          );
        })}
      </div>
    </div>
  );
}
