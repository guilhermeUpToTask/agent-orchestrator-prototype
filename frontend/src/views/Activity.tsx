import React, { useEffect, useMemo, useRef, useState } from 'react';
import { AlertTriangle, ArrowDown } from 'lucide-react';
import { useParams } from 'react-router-dom';
import { usePlannerStore, type BufferedEvent } from '../store/plannerStore';
import { useMetrics } from '../lib/queries';
import { absTime, relTime, useNow } from '../lib/time';
import { tokens } from '../styles/tokens';
import { Card } from '../components/ui';
import styles from './Activity.module.css';

type EventKind = 'ok' | 'fail' | 'neutral';

/** Classify an event for color: success / failure / neutral. */
function eventKind(e: BufferedEvent): EventKind {
  const t = e.type;
  if (t === 'ReasonerFailed') return e.payload.transient ? 'neutral' : 'fail';
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

/** A compact counter tile for the metrics strip. */
function Metric({ label, value, warn }: { label: string; value: number | null; warn?: boolean }) {
  return (
    <div className={styles.metric}>
      <span
        className={styles.metricValue}
        style={warn && (value ?? 0) > 0 ? { color: tokens.red } : undefined}
      >
        {value === null ? 'Unavailable' : value.toLocaleString()}
      </span>
      <span className={styles.metricLabel}>{label}</span>
    </div>
  );
}

/**
 * Global (or per-plan) telemetry roll-up — LLM token usage and agent
 * run/failure counts, refreshed on a poll. rate_limit failures are the run's
 * usual cause of death, so they get their own highlighted tile.
 */
function MetricsStrip({ planId }: { planId?: string }) {
  const { data, isLoading, error } = useMetrics(planId);

  if (error && !data) {
    return (
      <Card>
        <div className={styles.metricsError}>
          <AlertTriangle size={14} aria-hidden />
          Metrics unavailable — the roll-up request failed
        </div>
      </Card>
    );
  }

  if (isLoading || !data) {
    return (
      <div className={styles.metricsStrip} aria-busy="true" aria-label="Loading metrics">
        {[0, 1, 2, 3, 4, 5].map((i) => (
          <div key={i} className="skeleton" style={{ height: 46, minWidth: 118 }} />
        ))}
      </div>
    );
  }

  const rateLimited = data.agent.failures_by_kind['rate_limit'] ?? 0;
  const planner = data.llm.scopes.planner;
  const child = data.llm.scopes.child;
  return (
    <div className={styles.metricsStrip}>
      <Metric label="Planner calls" value={planner.calls} />
      <Metric label="Planner tokens" value={planner.total_tokens} />
      <Metric label="Child calls" value={child.calls} />
      <Metric label="Child tokens" value={child.total_tokens} />
      <Metric label="Combined tokens" value={data.llm.total_tokens} />
      <Metric label="Usage unavailable" value={data.llm.coverage.unavailable} />
      <Metric label="Agent runs" value={data.agent.runs} />
      <Metric label="Failures" value={data.agent.failed} warn />
      <Metric label="Rate-limited" value={rateLimited} warn />
    </div>
  );
}

/**
 * The system event log: monospace, dense, filterable — fed by the outbox
 * relay over SSE. Scroll position is preserved while reading; auto-follow
 * only when the operator is pinned to the bottom. A metrics strip sits on top.
 */
export function ActivityView() {
  const events = usePlannerStore((s) => s.events);
  const { planId } = useParams();
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
      <MetricsStrip planId={planId} />
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
