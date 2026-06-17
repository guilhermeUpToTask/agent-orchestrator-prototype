import React, { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowDown, Check, Copy } from 'lucide-react';
import { usePlannerStore, type DomainEvent } from '../store/plannerStore';
import { absTime, relTime, useNow } from '../lib/time';
import { tokens } from '../styles/tokens';
import styles from './Activity.module.css';

type EventKind = 'ok' | 'fail' | 'neutral';

/** Classify an event for color: success / failure / neutral. */
function eventKind(e: DomainEvent): EventKind {
  const t = e.type;
  const status = String((e.payload as Record<string, unknown>).status ?? '');
  if (
    t.endsWith('_failed') || t === 'task.unassignable' || t === 'goal.dispatch_failed'
    || status === 'failed' || status === 'canceled'
  ) return 'fail';
  if (
    t === 'task.completed' || t === 'goal.merged' || t === 'goal.finalized'
    || t.endsWith('_completed') || status === 'succeeded' || status === 'merged'
  ) return 'ok';
  return 'neutral';
}

const KIND_COLOR: Record<EventKind, string> = {
  ok: tokens.green,
  fail: tokens.red,
  neutral: tokens.textMuted,
};

async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

const formatLine = (e: DomainEvent): string =>
  `${absTime(e.at)}  ${e.type}  ${compact(e.payload)}`.trimEnd();

/**
 * The system event log, extracted from chat: monospace, dense, filterable.
 * Scroll position is preserved while reading; auto-follow only when the
 * operator is pinned to the bottom, with a "new events" jumper otherwise.
 */
export function ActivityView() {
  const events = usePlannerStore((s) => s.events);
  const selectNode = usePlannerStore((s) => s.selectNode);
  const now = useNow(1000);

  const [text, setText] = useState('');
  const [type, setType] = useState('all');

  const scrollRef = useRef<HTMLDivElement>(null);
  const pinnedRef = useRef(true);
  const [unseen, setUnseen] = useState(0);
  const lastCount = useRef(events.length);

  // Transient "copied ✓" feedback (per-row id, plus the copy-all control).
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [copiedAll, setCopiedAll] = useState(false);

  const types = useMemo(
    () => ['all', ...Array.from(new Set(events.map((e) => e.type))).sort()],
    [events],
  );

  const filtered = useMemo(() => {
    const q = text.trim().toLowerCase();
    return events.filter((e) => {
      if (type !== 'all' && e.type !== type) return false;
      if (!q) return true;
      return e.type.toLowerCase().includes(q) || JSON.stringify(e.payload).toLowerCase().includes(q);
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

  const copyOne = async (e: DomainEvent) => {
    if (await copyText(formatLine(e))) {
      setCopiedId(e.id);
      setTimeout(() => setCopiedId((id) => (id === e.id ? null : id)), 1200);
    }
  };

  const copyAll = async () => {
    if (await copyText(filtered.map(formatLine).join('\n'))) {
      setCopiedAll(true);
      setTimeout(() => setCopiedAll(false), 1200);
    }
  };

  return (
    <div className={styles.page}>
      <div className={styles.toolbar}>
        <input
          className={styles.search}
          type="search"
          placeholder="Filter events…"
          value={text}
          onChange={(e) => setText(e.target.value)}
          aria-label="Filter events by text"
        />
        <select
          className={styles.typeSelect}
          value={type}
          onChange={(e) => setType(e.target.value)}
          aria-label="Filter events by type"
        >
          {types.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
        <span className={styles.count + ' mono'}>
          {filtered.length} / {events.length}
        </span>
        <button
          className={styles.copyAll}
          onClick={copyAll}
          disabled={filtered.length === 0}
          aria-label="Copy all visible events"
          title="Copy all visible events"
        >
          {copiedAll ? <Check size={12} aria-hidden /> : <Copy size={12} aria-hidden />}
          {copiedAll ? 'Copied' : 'Copy all'}
        </button>
      </div>

      <div className={styles.logWrap}>
        <div className={styles.log} ref={scrollRef} onScroll={onScroll} role="log" aria-label="Event log">
          {events.length === 0 && (
            <p className={styles.empty}>
              No events received this session. Events stream in live as the system works.
            </p>
          )}
          {filtered.map((e) => {
            const taskId = e.payload.task_id as string | undefined;
            return (
            <div key={e.id} className={styles.line} style={{ borderLeft: `2px solid ${KIND_COLOR[eventKind(e)]}`, paddingLeft: 6 }}>
              <span className={styles.time} title={absTime(e.at)}>{relTime(e.at, now)}</span>
              <span className={styles.type} style={{ color: KIND_COLOR[eventKind(e)] }}>{e.type}</span>
              <span className={styles.payload}>
                {taskId && (
                  <button
                    onClick={() => selectNode(taskId)}
                    title="Open this task"
                    style={{
                      background: 'none', border: 'none', cursor: 'pointer', padding: 0,
                      color: tokens.accent, fontFamily: 'inherit', fontSize: 'inherit',
                      textDecoration: 'underline', marginRight: 8,
                    }}
                  >
                    {taskId}
                  </button>
                )}
                {compact(taskId ? omit(e.payload, 'task_id') : e.payload)}
              </span>
              <button
                className={`${styles.copyBtn} ${copiedId === e.id ? styles.copied : ''}`}
                onClick={() => copyOne(e)}
                aria-label="Copy event"
                title="Copy this event"
              >
                {copiedId === e.id ? <Check size={11} aria-hidden /> : <Copy size={11} aria-hidden />}
              </button>
            </div>
            );
          })}
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

function omit(payload: Record<string, unknown>, key: string): Record<string, unknown> {
  const { [key]: _drop, ...rest } = payload;
  return rest;
}

function compact(payload: Record<string, unknown>): string {
  const entries = Object.entries(payload);
  if (entries.length === 0) return '';
  return entries.map(([k, v]) => `${k}=${typeof v === 'string' ? v : JSON.stringify(v)}`).join('  ');
}
