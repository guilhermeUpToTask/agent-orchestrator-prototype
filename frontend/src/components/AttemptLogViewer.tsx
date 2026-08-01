import React from 'react';
import { AlertTriangle, Radio, X } from 'lucide-react';
import { useQueryClient } from '@tanstack/react-query';
import { subscribeToAttemptLog, type AttemptLogEntry } from '../lib/api';
import { keys, useAttemptLog } from '../lib/queries';
import { Button, CountChip } from './ui';
import styles from './AttemptLogViewer.module.css';

const TERMINAL = new Set(['succeeded', 'failed', 'abandoned']);

/** Captured log for settled attempts; resumable raw SSE tail for a live one. */
export function AttemptLogViewer({
  planId,
  attemptId,
  status,
  onClose,
}: {
  planId: string;
  attemptId: string;
  status: string;
  onClose: () => void;
}) {
  const terminal = TERMINAL.has(status);
  const captured = useAttemptLog(planId, attemptId);
  const queryClient = useQueryClient();
  const [liveEntries, setLiveEntries] = React.useState<AttemptLogEntry[]>([]);
  const [truncated, setTruncated] = React.useState(false);
  const [streamStatus, setStreamStatus] = React.useState<
    'connecting' | 'live' | 'reconnecting' | 'down' | 'ended'
  >(terminal ? 'ended' : 'connecting');

  React.useEffect(() => {
    setLiveEntries([]);
    setTruncated(false);
    if (terminal) {
      setStreamStatus('ended');
      return;
    }
    return subscribeToAttemptLog(planId, attemptId, {
      onEntry: (entry) => setLiveEntries((current) => [...current.slice(-1999), entry]),
      onTruncated: () => {
        setTruncated(true);
        setLiveEntries([]);
      },
      onEnd: () => {
        setStreamStatus('ended');
        void queryClient.invalidateQueries({ queryKey: keys.attemptLog(planId, attemptId) });
        void queryClient.invalidateQueries({ queryKey: keys.attemptTimeline(planId) });
      },
      onStatus: setStreamStatus,
    });
  }, [attemptId, planId, queryClient, terminal]);

  const entries = terminal || streamStatus === 'ended'
    ? captured.data?.entries ?? liveEntries
    : liveEntries;
  const wasTruncated = truncated || captured.data?.truncated;

  return (
    <section className={styles.panel} aria-label={`Attempt ${attemptId} raw log`}>
      <header className={styles.header}>
        <div>
          <div className={styles.title}>Attempt log</div>
          <div className={styles.id}>{attemptId}</div>
        </div>
        <CountChip tone={terminal ? (status === 'succeeded' ? 'ok' : 'fail') : 'run'}>
          {!terminal && <Radio size={10} aria-hidden />}
          {terminal ? status : streamStatus}
        </CountChip>
        <Button variant="icon" size="sm" onClick={onClose} aria-label="Close attempt log">
          <X size={13} aria-hidden />
        </Button>
      </header>

      {wasTruncated && (
        <div className={styles.truncated} role="status">
          <AlertTriangle size={12} aria-hidden /> Older lines rotated out of the bounded log.
        </div>
      )}

      <div className={styles.log} aria-live={terminal ? 'off' : 'polite'}>
        {captured.isLoading && terminal && <span className={styles.empty}>Loading captured log…</span>}
        {captured.error && terminal && <span className={styles.error}>Captured log unavailable.</span>}
        {entries.map((entry, index) => (
          <div
            key={`${entry.monotonic_seconds}:${index}`}
            className={entry.stream === 'stderr' ? styles.stderr : styles.stdout}
          >
            <span className={styles.time}>{entry.monotonic_seconds.toFixed(3)}s</span>
            <span className={styles.stream}>{entry.stream}</span>
            <span className={styles.text}>{entry.text}</span>
          </div>
        ))}
        {!captured.isLoading && entries.length === 0 && (
          <span className={styles.empty}>
            {terminal ? 'This attempt captured no runtime output.' : 'Waiting for runtime output…'}
          </span>
        )}
      </div>
    </section>
  );
}
