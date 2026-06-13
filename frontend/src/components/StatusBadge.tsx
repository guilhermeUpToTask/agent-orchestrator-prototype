import React from 'react';
import {
  GOAL_STATUS, KIND_VARS, PLAN_STATUS, TASK_STATUS, type StatusMeta,
} from '../styles/tokens';
import type { GoalStatus, ProjectPlanStatus, TaskStatus } from '../types/ui';

type Props =
  | { domain: 'task'; value: TaskStatus; bare?: boolean }
  | { domain: 'goal'; value: GoalStatus; bare?: boolean }
  | { domain: 'plan'; value: ProjectPlanStatus; bare?: boolean }
  | { domain: 'custom'; value: StatusMeta; bare?: boolean };

function metaFor(p: Props): StatusMeta {
  switch (p.domain) {
    case 'task': return TASK_STATUS[p.value] ?? TASK_STATUS.created;
    case 'goal': return GOAL_STATUS[p.value] ?? GOAL_STATUS.pending;
    case 'plan': return PLAN_STATUS[p.value] ?? PLAN_STATUS.discovery;
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
