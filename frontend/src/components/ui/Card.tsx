import React from 'react';
import styles from './Card.module.css';

/** Instrument card: bg-1 surface, optional header row (title + actions). */
export function Card({
  title,
  actions,
  children,
  className,
}: {
  title?: React.ReactNode;
  actions?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={[styles.card, className].filter(Boolean).join(' ')}>
      {(title !== undefined || actions !== undefined) && (
        <header className={styles.header}>
          <div className={styles.title}>{title}</div>
          {actions && <div className={styles.actions}>{actions}</div>}
        </header>
      )}
      <div className={styles.body}>{children}</div>
    </section>
  );
}
