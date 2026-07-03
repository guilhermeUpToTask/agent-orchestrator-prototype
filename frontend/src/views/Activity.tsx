import React, { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowDown } from 'lucide-react';
import { usePlannerStore, type BufferedEvent } from '../store/plannerStore';
import { absTime, relTime, useNow } from '../lib/time';
import { tokens } from '../styles/tokens';
import styles from './Activity.module.css';

type EventKind = 'ok' | 'fail' | 'neutral';

/** Classify an event for color: success / failure / neutral. */
function eventKind(e: BufferedEvent): EventKind {
  const t = e.type;
  if (t === 'TaskFailedEvent' || t === 'GoalFailedEvent' || t === 'PlanFailed') return 'fail';
  if (t === 'TaskCompleted' || t === 'GoalCompleted' || t === 'PlanCompleted') return 'ok';
  return 'neutral';
}

const KIND_COLOR: Record<EventKind, string> = {
  ok: tokens.green,
  fail: tokens.red,
  neutral: tokens.textMuted,
};

function compact(payload: Record<string, unknown>): string {
  const parts: string[] = [];
  for (const [k, v] of Object.entries(payload)) {
    if (k === 'event_id' || k === 'occurred_at' || v == null) continue;
    parts.push(`${k}=${typeof v === 'string' ? v : JSON.stringify(v)}`);
  }
  return parts.join(' ');
}

/**
 * The system event log: monospace, dense, filterable — fed by the outbox
 * relay over SSE. Scroll position is preserved while reading; auto-follow
 * only when the operator is pinned to the bottom.
 */
export function ActivityView() {
  const events = usePlannerStore((s) => s.events);
  const now = useNow(1000);

  const [text, setText] = useState('');
  const [type, setType] = useState('all');

  const scrollRef = useRef<HTMLDivElement>(null);
  const pinnedRef = useRef(true);
  const [unseen, setUnseen] = useState(0);
  const lastCount = useRef(events.length);

  const types = useMemo(
    () => ['all', ...Array.from(new Set(events.map((e) => e.type))).sort()],
    [events],
  );

  const filtered = useMemo(() => {
    const q = text.trim().toLowerCase();
    return events.filter((e) => {
      if (type !== 'all' && e.type !== type) return false;
      if (!q) return true;
      return e.type.toLowerCase().includes(q) || compact(e.payload).toLowerCase().includes(q);
    });
  }, [events, text, type]);

  // Follow the stream only while pinned; otherwise count what arrived.
  useEffect(() => {
    const grew = events.length - lastCount.current;
    lastCount.current = events.length;
    if (grew <= 0) return;
    if (pinnedRef.current) {
      scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
    } else {
      setUnseen((n) => n + grew);
    }
  }, [events.length]);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
    pinnedRef.current = atBottom;
    if (atBottom) setUnseen(0);
  };

  const jumpToLatest = () => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
    pinnedRef.current = true;
    setUnseen(0);
  };

  return (
    <div className={styles.page}>
      <div className={styles.toolbar}>
        <input
          className={styles.search}
          placeholder="Filter events…"
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
        <select
          className={styles.typeSelect}
          value={type}
          onChange={(e) => setType(e.target.value)}
          aria-label="Filter by event type"
        >
          {types.map((t) => (
            <option key={t} value={t}>{t}</option>
          ))}
        </select>
        <span className={styles.count}>
          {filtered.length} / {events.length} events
        </span>
      </div>

      <div className={styles.logWrap}>
        <div className={styles.log} ref={scrollRef} onScroll={onScroll}>
          {filtered.length === 0 ? (
            <div className={styles.empty}>
              No events yet — the stream fills as the system works.
            </div>
          ) : (
            filtered.map((e) => (
              <div key={e.id} className={styles.line}>
                <span className={styles.time} title={absTime(e.at)}>
                  {relTime(e.at, now)}
                </span>
                <span className={styles.type} style={{ color: KIND_COLOR[eventKind(e)] }}>
                  {e.type}
                </span>
                <span className={styles.payload}>{compact(e.payload)}</span>
              </div>
            ))
          )}
        </div>
        {unseen > 0 && (
          <button className={styles.jumper} onClick={jumpToLatest}>
            <ArrowDown size={12} aria-hidden /> {unseen} new event{unseen === 1 ? '' : 's'}
          </button>
        )}
      </div>
    </div>
  );
}
