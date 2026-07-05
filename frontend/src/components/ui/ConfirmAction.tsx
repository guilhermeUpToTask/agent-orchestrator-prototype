import React, { useState } from 'react';
import styles from './ConfirmAction.module.css';

/**
 * Two-step confirm: a button that arms into an inline confirm row stating
 * the consequence. `tone` follows the status color language — amber for
 * operator gates (waiting on you), red for destructive removals.
 */
export function ConfirmAction({
  label,
  consequence,
  pending,
  demoted,
  tone = 'gate',
  onConfirm,
}: {
  label: string;
  consequence: string;
  pending?: boolean;
  /** Render the arming button as a quiet bordered button. */
  demoted?: boolean;
  tone?: 'gate' | 'danger';
  onConfirm: () => void;
}) {
  const [arming, setArming] = useState(false);
  const toneCls = tone === 'danger' ? styles.danger : styles.gate;

  if (!arming) {
    return (
      <button
        className={`${demoted ? styles.demotedBtn : styles.armBtn} ${toneCls}`}
        onClick={() => setArming(true)}
        disabled={pending}
      >
        {label}
      </button>
    );
  }

  return (
    <div
      className={`${styles.confirmRow} ${toneCls}`}
      role="group"
      aria-label={`Confirm: ${label}`}
    >
      <span className={styles.consequence}>{consequence}</span>
      <button
        className={styles.cancelBtn}
        onClick={() => setArming(false)}
        disabled={pending}
      >
        Cancel
      </button>
      <button
        className={`${styles.confirmBtn} ${toneCls}`}
        onClick={onConfirm}
        disabled={pending}
      >
        {pending ? 'Working…' : `Confirm: ${label}`}
      </button>
    </div>
  );
}
