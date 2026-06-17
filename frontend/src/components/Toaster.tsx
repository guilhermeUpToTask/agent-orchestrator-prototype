import { AlertCircle, CheckCircle2, Info, X } from 'lucide-react';
import { useToastStore, type ToastKind } from '../lib/toast';
import styles from './Toaster.module.css';

const ICONS: Record<ToastKind, typeof AlertCircle> = {
  error: AlertCircle,
  success: CheckCircle2,
  info: Info,
};

/**
 * Renders the toast stack (bottom-right). Mounted once in App so flow errors
 * are visible regardless of which view or panel the operator is in.
 */
export function Toaster() {
  const toasts = useToastStore((s) => s.toasts);
  const dismiss = useToastStore((s) => s.dismiss);

  if (toasts.length === 0) return null;

  return (
    <div className={styles.stack} role="region" aria-label="Notifications">
      {toasts.map((t) => {
        const Icon = ICONS[t.kind];
        return (
          <div
            key={t.id}
            className={`${styles.toast} ${styles[t.kind]}`}
            role={t.kind === 'error' ? 'alert' : 'status'}
          >
            <Icon size={15} className={styles.icon} aria-hidden />
            <div className={styles.body}>
              <div className={styles.title}>{t.title}</div>
              {t.detail && <div className={styles.detail}>{t.detail}</div>}
            </div>
            <button
              className={styles.close}
              onClick={() => dismiss(t.id)}
              aria-label="Dismiss notification"
            >
              <X size={13} aria-hidden />
            </button>
          </div>
        );
      })}
    </div>
  );
}
