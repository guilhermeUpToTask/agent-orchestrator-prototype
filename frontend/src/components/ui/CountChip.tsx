import React from 'react';
import { KIND_VARS, type StatusKind } from '../../styles/tokens';
import styles from './CountChip.module.css';

/** Prominent inline count/verdict chip — retry counts, failed counts, verification verdicts, ahead/behind. */
export function CountChip({
  tone,
  icon,
  children,
}: {
  tone: StatusKind;
  icon?: React.ReactNode;
  children: React.ReactNode;
}) {
  const v = KIND_VARS[tone];

  return (
    <span
      className={styles.chip}
      style={{
        color: v.text,
        background: v.bg,
        borderColor: `color-mix(in srgb, ${v.fg} 35%, transparent)`,
      }}
    >
      {icon}
      {children}
    </span>
  );
}
