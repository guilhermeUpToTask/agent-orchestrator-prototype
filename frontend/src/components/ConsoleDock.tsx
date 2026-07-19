import React, { useEffect, useMemo, useRef } from 'react';
import { AlertTriangle, ChevronDown, ChevronUp, Terminal } from 'lucide-react';
import { useParams } from 'react-router-dom';
import { useAttemptTimeline } from '../lib/queries';
import { useNow } from '../lib/time';
import type { ExecutionAttemptRow } from '../lib/api';
import { tokens } from '../styles/tokens';
import { usePlannerStore } from '../store/plannerStore';

function duration(start: string, end: string | null, now: number): string {
  const milliseconds = Math.max(0, Date.parse(end ?? new Date(now).toISOString()) - Date.parse(start));
  if (milliseconds < 1_000) return `${milliseconds}ms`;
  return `${(milliseconds / 1_000).toFixed(milliseconds < 10_000 ? 1 : 0)}s`;
}

function retryCopy(retryAt: string | null, now: number): string | null {
  if (!retryAt) return null;
  const seconds = Math.max(0, Math.ceil((Date.parse(retryAt) - now) / 1_000));
  return seconds > 0 ? `automatic retry in ${seconds}s` : 'automatic retry is due';
}

function attemptColor(attempt: ExecutionAttemptRow): string {
  if (attempt.status === 'failed') return tokens.red;
  if (attempt.status === 'succeeded') return tokens.green;
  if (attempt.status === 'abandoned') return tokens.textMuted;
  return tokens.accent;
}

/**
 * Durable operational timeline. HTTP history hydrates before SSE so refreshes
 * retain task -> run -> attempt identity; live agent events remain visible as
 * supplemental rows until runtimes provide a fully correlated stream.
 */
export function ConsoleDock() {
  const { planId = '' } = useParams();
  const consoleOpen = usePlannerStore((state) => state.ui.consoleOpen);
  const toggleConsole = usePlannerStore((state) => state.toggleConsole);
  const selectedTaskId = usePlannerStore((state) => state.ui.selectedTaskId);
  const agentLog = usePlannerStore((state) => state.agentLog);
  const { data: timeline, isLoading } = useAttemptTimeline(planId || null);
  const now = useNow();
  const [taskOnly, setTaskOnly] = React.useState(false);
  const [failedOnly, setFailedOnly] = React.useState(false);

  const tasks = useMemo(() => {
    const rows = timeline?.tasks ?? [];
    return rows
      .filter((task) => !taskOnly || !selectedTaskId || task.task_id === selectedTaskId)
      .map((task) => ({
        ...task,
        runs: task.runs
          .map((run) => ({
            ...run,
            attempts: failedOnly
              ? run.attempts.filter((attempt) => attempt.status === 'failed')
              : run.attempts,
          }))
          .filter((run) => !failedOnly || run.attempts.length > 0),
      }))
      .filter((task) => !failedOnly || task.runs.length > 0);
  }, [failedOnly, selectedTaskId, taskOnly, timeline?.tasks]);

  const liveRows = agentLog.filter(
    (row) =>
      row.plan_id === planId &&
      (!taskOnly || !selectedTaskId || row.task_id === selectedTaskId) &&
      (!failedOnly || row.type.includes('failed')),
  );
  const count =
    (timeline?.planning_operations.length ?? 0) +
    tasks.reduce((sum, task) => sum + task.runs.reduce((n, run) => n + run.attempts.length, 0), 0);

  const scrollRef = useRef<HTMLDivElement>(null);
  const pinnedRef = useRef(true);
  useEffect(() => {
    const element = scrollRef.current;
    if (element && pinnedRef.current) element.scrollTop = element.scrollHeight;
  }, [count, liveRows.length]);

  const toggleStyle = (active: boolean): React.CSSProperties => ({
    fontSize: 'var(--fs-micro)',
    fontFamily: tokens.fontMono,
    letterSpacing: '0.06em',
    padding: '2px 7px',
    borderRadius: 'var(--r-2)',
    border: `1px solid ${tokens.border}`,
    color: active ? tokens.accent : tokens.textMuted,
    background: active ? 'color-mix(in srgb, var(--accent) 14%, transparent)' : 'transparent',
  });

  return (
    <div style={{
      borderTop: `1px solid ${tokens.border}`,
      background: 'var(--bg-0)',
      display: 'flex',
      flexDirection: 'column',
      flexShrink: 0,
      height: consoleOpen ? 260 : 30,
      transition: 'height 0.15s ease',
    }}>
      <button
        onClick={toggleConsole}
        style={{
          display: 'flex', alignItems: 'center', gap: 'var(--sp-2)', padding: '6px 14px',
          background: 'transparent', border: 'none', cursor: 'pointer',
          color: tokens.textMuted, flexShrink: 0,
        }}
        aria-expanded={consoleOpen}
      >
        <Terminal size={12} aria-hidden />
        <span style={{ fontSize: 'var(--fs-micro)', fontFamily: tokens.fontMono, letterSpacing: '0.1em' }}>
          AGENT EVENTS {count > 0 && `· ${count}`}
        </span>
        <div style={{ flex: 1 }} />
        {selectedTaskId && (
          <span
            role="button"
            tabIndex={0}
            onClick={(event) => { event.stopPropagation(); setTaskOnly((value) => !value); }}
            onKeyDown={(event) => {
              if (event.key === 'Enter' || event.key === ' ') setTaskOnly((value) => !value);
            }}
            style={toggleStyle(taskOnly)}
          >
            SELECTED TASK
          </span>
        )}
        <span
          role="button"
          tabIndex={0}
          onClick={(event) => { event.stopPropagation(); setFailedOnly((value) => !value); }}
          onKeyDown={(event) => {
            if (event.key === 'Enter' || event.key === ' ') setFailedOnly((value) => !value);
          }}
          style={toggleStyle(failedOnly)}
        >
          FAILED ONLY
        </span>
        {consoleOpen ? <ChevronDown size={13} aria-hidden /> : <ChevronUp size={13} aria-hidden />}
      </button>

      {consoleOpen && (
        <div
          ref={scrollRef}
          onScroll={() => {
            const element = scrollRef.current;
            if (element) pinnedRef.current = element.scrollHeight - element.scrollTop - element.clientHeight < 24;
          }}
          style={{ flex: 1, overflowY: 'auto', padding: 'var(--sp-1) 14px 10px', fontFamily: tokens.fontMono }}
        >
          {isLoading && <div style={{ color: tokens.textMuted, fontSize: 'var(--fs-micro)' }}>Hydrating attempt history…</div>}

          {!failedOnly && timeline?.planning_operations.map((operation) => (
            <div key={operation.id} style={{ fontSize: 'var(--fs-micro)', lineHeight: 1.7, color: operation.status === 'failed' ? tokens.red : tokens.purple }}>
              planner/{operation.purpose}
              {operation.target_goal_id && ` · goal ${operation.target_goal_id.slice(0, 8)}`}
              {` · ${operation.status} · ${operation.model_request_count} model request(s)`}
              {operation.retry_at && ` · ${retryCopy(operation.retry_at, now)}`}
              {operation.safe_message && ` · ${operation.safe_message}`}
            </div>
          ))}

          {tasks.map((task) => (
            <div key={`${task.goal_id}:${task.task_id}`} style={{ marginTop: 5 }}>
              <div style={{ color: tokens.accent, fontSize: 'var(--fs-micro)', lineHeight: 1.7 }}>
                task {task.task_id.slice(0, 8)} · goal {task.goal_id.slice(0, 8)}
              </div>
              {task.runs.map((run) => (
                <div key={run.id} style={{ paddingLeft: 'var(--sp-3)' }}>
                  <div style={{ color: tokens.textSecond, fontSize: 'var(--fs-micro)', lineHeight: 1.7 }}>
                    run {run.id.slice(0, 8)} · {run.status} · {duration(run.started_at, run.completed_at, now)}
                  </div>
                  {run.attempts.map((attempt) => {
                    const retry = retryCopy(attempt.retry_at, now);
                    const provider = [attempt.runtime, attempt.provider_id, attempt.model_id].filter(Boolean).join('/');
                    return (
                      <div key={attempt.id} style={{ paddingLeft: 'var(--sp-3)', color: attemptColor(attempt), fontSize: 'var(--fs-micro)', lineHeight: 1.7 }}>
                        attempt {attempt.number} · {attempt.status} · {duration(attempt.started_at, attempt.completed_at, now)}
                        {provider && ` · ${provider}`}
                        {attempt.failure_kind && ` · ${attempt.failure_kind}`}
                        {attempt.provider_code && ` (${attempt.provider_code})`}
                        {retry && ` · ${retry}`}
                        {attempt.safe_message && ` · ${attempt.safe_message}`}
                        {attempt.status === 'failed' && !attempt.retryable && (
                          <span style={{ color: tokens.textMuted }}>
                            {' · '}recovery: switch provider/model, edit the task, or pause
                          </span>
                        )}
                        {(attempt.stdout_tail || attempt.stderr_tail) && (
                          <div style={{ color: tokens.textDim, whiteSpace: 'pre-wrap', paddingLeft: 'var(--sp-2)' }}>
                            {(attempt.stderr_tail || attempt.stdout_tail).slice(-500)}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              ))}
            </div>
          ))}

          {liveRows.length > 0 && (
            <div style={{ marginTop: 7, borderTop: `1px solid ${tokens.border}`, paddingTop: 'var(--sp-1)' }}>
              <div style={{ fontSize: 'var(--fs-micro)', color: tokens.textMuted }}>LIVE RUNTIME EVENTS</div>
              {liveRows.map((row) => (
                <div key={row.id} style={{ fontSize: 'var(--fs-micro)', lineHeight: 1.7, color: row.type.includes('failed') ? tokens.red : tokens.textSecond }}>
                  {new Date(row.at).toLocaleTimeString()} · {row.task_id.slice(0, 8) || 'plan'} · a{row.attempt}#{row.seq} · {row.type} · {row.text}
                </div>
              ))}
            </div>
          )}

          {!isLoading && count === 0 && liveRows.length === 0 && (
            <div style={{ display: 'flex', gap: 6, alignItems: 'center', color: tokens.textDim, fontSize: 'var(--fs-micro)' }}>
              {failedOnly && <AlertTriangle size={11} aria-hidden />}
              {failedOnly ? 'No failed attempts.' : 'No planning or agent attempts yet.'}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
