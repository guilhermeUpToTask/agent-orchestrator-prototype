import React from 'react';
import { KIND_VARS, PLAN_PHASE, PLAN_STATUS, STATUS, type StatusMeta } from '../styles/tokens';
import type { PlanPhase, PlanStatus, Status } from '../types/ui';

type Props =
  | { domain: 'status'; value: Status; bare?: boolean }
  | { domain: 'phase'; value: PlanPhase; bare?: boolean }
  | { domain: 'plan'; value: PlanStatus; bare?: boolean }
  | { domain: 'custom'; value: StatusMeta; bare?: boolean };

function metaFor(p: Props): StatusMeta {
  switch (p.domain) {
    case 'status': return STATUS[p.value] ?? STATUS.pending;
    case 'phase': return PLAN_PHASE[p.value] ?? PLAN_PHASE.discovery;
    case 'plan': return PLAN_STATUS[p.value] ?? PLAN_STATUS.idle;
    case 'custom': return p.value;
  }
}

/**
 * One status, one rendering: icon + label, never color alone.
 * `bare` drops the wash/border (for table cells and dense rows).
 */
export function StatusBadge(props: Props) {
  const meta = metaFor(props);
  const v = KIND_VARS[meta.kind];
  const Icon = meta.Icon;

  return (
    <span
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 5,
        fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-micro)',
        letterSpacing: '0.04em', whiteSpace: 'nowrap', lineHeight: 1,
        color: v.text,
        ...(props.bare ? {} : {
          padding: '3px 8px',
          background: v.bg,
          border: `1px solid color-mix(in srgb, ${v.fg} 35%, transparent)`,
          borderRadius: 'var(--r-1)',
        }),
      }}
    >
      <Icon size={12} className={meta.spin ? 'spin' : undefined} aria-hidden />
      {meta.label}
    </span>
  );
}
