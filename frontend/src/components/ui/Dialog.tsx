import React, { useEffect, useRef } from 'react';
import { X } from 'lucide-react';
import styles from './Dialog.module.css';

/**
 * The one modal pattern: scrim + panel, Escape and scrim-click close, focus
 * moves into the panel on open. Extracted from the GatePanel gate surface.
 */
export function Dialog({
  open,
  onClose,
  ariaLabel,
  title,
  width = 560,
  children,
}: {
  open: boolean;
  onClose: () => void;
  ariaLabel: string;
  /** Mono micro-label in the sticky header. */
  title?: string;
  width?: number;
  children: React.ReactNode;
}) {
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    panelRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className={styles.scrim} onClick={onClose}>
      <div
        ref={panelRef}
        className={styles.panel}
        style={{ width: `min(${width}px, 100%)` }}
        role="dialog"
        aria-modal="true"
        aria-label={ariaLabel}
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
      >
        <header className={styles.header}>
          <span className="label">{title ?? ariaLabel}</span>
          <button className={styles.close} onClick={onClose} aria-label="Close dialog">
            <X size={15} aria-hidden />
          </button>
        </header>
        <div className={styles.content}>{children}</div>
      </div>
    </div>
  );
}
