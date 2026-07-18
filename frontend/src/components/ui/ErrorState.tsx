import React from 'react';
import { AlertTriangle, RotateCcw } from 'lucide-react';
import { Card } from './Card';
import { Button } from './Button';
import styles from './ErrorState.module.css';

/** Shared query-error surface: never a blank screen, never a silent empty state. */
export function ErrorState({
  title = "Can't reach the backend",
  message,
  onRetry,
}: {
  title?: string;
  message: string;
  onRetry: () => void;
}) {
  return (
    <Card>
      <div className={styles.wrap} role="alert">
        <div className={styles.heading}>
          <AlertTriangle size={16} aria-hidden />
          <strong className={styles.title}>{title}</strong>
        </div>
        <p className={styles.message}>{message}</p>
        <Button variant="primary" onClick={onRetry}>
          <RotateCcw size={13} aria-hidden /> Retry
        </Button>
      </div>
    </Card>
  );
}
