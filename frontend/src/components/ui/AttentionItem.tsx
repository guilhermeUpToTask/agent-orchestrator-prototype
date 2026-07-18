import React from 'react';
import { KIND_VARS, type StatusKind } from '../../styles/tokens';
import styles from './AttentionItem.module.css';

/** A severity-weighted attention row: tone drives the left-border accent + wash. */
export function AttentionItem({
  tone,
  icon,
  title,
  meta,
  badge,
  detail,
  onClick,
}: {
  tone: StatusKind;
  icon: React.ReactNode;
  title: React.ReactNode;
  meta?: React.ReactNode;
  badge?: React.ReactNode;
  /** Optional full-width line below the title row — e.g. a failure reason. */
  detail?: React.ReactNode;
  onClick?: () => void;
}) {
  const v = KIND_VARS[tone];
  // Only a row with an action is a button — a static row (e.g. plan.block)
  // must not present as focusable/clickable.
  const Tag: 'button' | 'div' = onClick ? 'button' : 'div';

  return (
    <Tag
      type={onClick ? 'button' : undefined}
      onClick={onClick}
      className={styles.item}
      style={{
        background: v.bg,
        borderColor: `color-mix(in srgb, ${v.fg} 32%, transparent)`,
        borderLeftColor: v.fg,
      }}
    >
      <span className={styles.icon} style={{ color: v.fg }}>
        {icon}
      </span>
      <span className={styles.title}>{title}</span>
      {badge ?? <span />}
      {meta && (
        <span className="label" style={{ whiteSpace: 'nowrap' }}>
          {meta}
        </span>
      )}
      {detail && (
        <span className={styles.detail} style={{ color: v.text }}>
          {detail}
        </span>
      )}
    </Tag>
  );
}
