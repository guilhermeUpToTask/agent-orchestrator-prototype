import React from 'react';
import { RefreshCw } from 'lucide-react';
import { useQueryClient } from '@tanstack/react-query';
import { usePlannerStore } from '../store/plannerStore';
import { usePlan } from '../lib/queries';
import { relTime, absTime, useNow } from '../lib/time';
import { StatusBadge } from './StatusBadge';
import styles from './TopBar.module.css';

/**
 * Persistent connection truth. A chat bubble that scrolls away is not a
 * connection indicator; this is. While not live, the shell shows a
 * stale-data notice ("data as of …") instead of silently lying.
 */
function ConnectionIndicator() {
  const { state, lastEventAt } = usePlannerStore((s) => s.connection);
  const qc = useQueryClient();
  const now = useNow(1000);

  const meta = {
    connecting:   { cls: styles.connIdle, dot: styles.dotIdle, text: 'connecting…' },
    live:         { cls: styles.connLive, dot: styles.dotLive, text: 'live' },
    reconnecting: { cls: styles.connWarn, dot: styles.dotWarn, text: 'reconnecting…' },
    down:         { cls: styles.connDown, dot: styles.dotDown, text: 'disconnected' },
  }[state];

  const lastLabel =
    state === 'live'
      ? lastEventAt
        ? `last event ${relTime(lastEventAt, now)}`
        : 'no events yet'
      : lastEventAt
        ? `data as of ${absTime(lastEventAt)}`
        : 'no data received';

  return (
    <div className={`${styles.conn} ${meta.cls}`} role="status" aria-live="polite">
      <span className={`${styles.dot} ${meta.dot} ${state === 'live' ? 'breathe' : ''}`} aria-hidden />
      <span className={styles.connState}>{meta.text}</span>
      <span className={styles.connLast} title={lastEventAt ? absTime(lastEventAt) : undefined}>
        {lastLabel}
      </span>
      <button
        className={styles.resync}
        title="Resync — refetch all data from the backend"
        aria-label="Resync all data"
        onClick={() => qc.invalidateQueries()}
      >
        <RefreshCw size={12} aria-hidden />
      </button>
    </div>
  );
}

export function TopBar() {
  const { data: plan } = usePlan();

  return (
    <header className={styles.bar}>
      <div className={styles.brand}>
        <span className={styles.brandMark} aria-hidden>A</span>
        <span className={styles.brandName}>AIPOM</span>
      </div>

      <div className={styles.planIdentity}>
        {plan ? (
          <>
            <span className={styles.planName} title={plan.vision}>
              {plan.vision ? plan.vision : plan.plan_id}
            </span>
            <StatusBadge domain="plan" value={plan.status} />
          </>
        ) : (
          <span className="skeleton" style={{ width: 220, height: 16 }} />
        )}
      </div>

      <div className={styles.spacer} />
      <ConnectionIndicator />
    </header>
  );
}
