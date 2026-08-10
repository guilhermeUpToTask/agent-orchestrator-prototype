import React, { useEffect, useMemo, useRef } from 'react';
import { AlertTriangle, ChevronDown, ChevronUp, Terminal } from 'lucide-react';
import { useParams } from 'react-router-dom';
import { useAttemptTimeline } from '../lib/queries';
import { useNow } from '../lib/time';
import type { ExecutionAttemptRow } from '../lib/api';
import { usePlannerStore } from '../store/plannerStore';
import { AttemptLogViewer } from './AttemptLogViewer';
import styles from './ConsoleDock.module.css';

/** Join the class names that are actually present. */
const cx = (...names: (string | false | undefined)[]) => names.filter(Boolean).join(' ');

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

const ATTEMPT_CLASS: Record<string, string | undefined> = {
  failed: styles.attemptFailed,
  succeeded: styles.attemptSucceeded,
  abandoned: styles.attemptAbandoned,
};

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
  const [selectedAttempt, setSelectedAttempt] = React.useState<ExecutionAttemptRow | null>(null);

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

  const height = consoleOpen
    ? (selectedAttempt ? styles.dockOpenWithLog : styles.dockOpen)
    : undefined;

  return (
    <div className={cx(styles.dock, height)}>
      {/*
        Three sibling buttons, not one button containing two `role="button"`
        spans. The old markup nested interactive content inside a <button>,
        which is invalid and collapsed the accessibility tree into a single
        control named "AGENT EVENTS · 1 FAILED ONLY" — one name for three
        actions. The filters are toggles, so they now say so with
        `aria-pressed` instead of only looking pressed.
      */}
      <div className={styles.toolbar}>
        <button
          type="button"
          onClick={toggleConsole}
          className={styles.expandToggle}
          aria-expanded={consoleOpen}
        >
          <Terminal size={12} aria-hidden />
          <span className={styles.expandLabel}>
            AGENT EVENTS {count > 0 && `· ${count}`}
          </span>
          {consoleOpen ? <ChevronDown size={13} aria-hidden /> : <ChevronUp size={13} aria-hidden />}
        </button>
        <div className={styles.spacer} />
        {selectedTaskId && (
          <button
            type="button"
            className={styles.filter}
            aria-pressed={taskOnly}
            onClick={() => setTaskOnly((value) => !value)}
          >
            SELECTED TASK
          </button>
        )}
        <button
          type="button"
          className={styles.filter}
          aria-pressed={failedOnly}
          onClick={() => setFailedOnly((value) => !value)}
        >
          FAILED ONLY
        </button>
      </div>

      {consoleOpen && (
        <div
          ref={scrollRef}
          onScroll={() => {
            const element = scrollRef.current;
            if (element) pinnedRef.current = element.scrollHeight - element.scrollTop - element.clientHeight < 24;
          }}
          className={styles.log}
        >
          {isLoading && <div className={styles.hydrating}>Hydrating attempt history…</div>}

          {!failedOnly && timeline?.planning_operations.map((operation) => (
            <div
              key={operation.id}
              className={cx(styles.planning, operation.status === 'failed' && styles.planningFailed)}
            >
              planner/{operation.purpose}
              {operation.target_goal_id && ` · goal ${operation.target_goal_id.slice(0, 8)}`}
              {` · ${operation.status} · ${operation.model_request_count} model request(s)`}
              {operation.retry_at && ` · ${retryCopy(operation.retry_at, now)}`}
              {operation.safe_message && ` · ${operation.safe_message}`}
            </div>
          ))}

          {tasks.map((task) => (
            <div key={`${task.goal_id}:${task.task_id}`} className={styles.task}>
              <div className={styles.taskHead}>
                task {task.task_id.slice(0, 8)} · goal {task.goal_id.slice(0, 8)}
              </div>
              {task.runs.map((run) => (
                <div key={run.id} className={styles.run}>
                  <div className={styles.runHead}>
                    run {run.id.slice(0, 8)} · {run.status} · {duration(run.started_at, run.completed_at, now)}
                  </div>
                  {run.attempts.map((attempt) => {
                    const retry = retryCopy(attempt.retry_at, now);
                    const provider = [attempt.runtime, attempt.provider_id, attempt.model_id].filter(Boolean).join('/');
                    return (
                      <div
                        key={attempt.id}
                        className={cx(styles.attempt, ATTEMPT_CLASS[attempt.status])}
                      >
                        attempt {attempt.number} · {attempt.status} · {duration(attempt.started_at, attempt.completed_at, now)}
                        {provider && ` · ${provider}`}
                        {attempt.failure_kind && ` · ${attempt.failure_kind}`}
                        {attempt.provider_code && ` (${attempt.provider_code})`}
                        {retry && ` · ${retry}`}
                        {attempt.safe_message && ` · ${attempt.safe_message}`}
                        {attempt.status === 'failed' && !attempt.retryable && (
                          <span className={styles.recovery}>
                            {' · '}recovery: switch provider/model, edit the task, or pause
                          </span>
                        )}
                        <button
                          type="button"
                          onClick={() => setSelectedAttempt(attempt)}
                          className={styles.rawLogButton}
                        >
                          view raw log
                        </button>
                        {(attempt.stdout_tail || attempt.stderr_tail) && (
                          <div className={styles.tail}>
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
            <div className={styles.live}>
              <div className={styles.liveHead}>LIVE RUNTIME EVENTS</div>
              {liveRows.map((row) => (
                <div
                  key={row.id}
                  className={cx(styles.liveRow, row.type.includes('failed') && styles.liveRowFailed)}
                >
                  {new Date(row.at).toLocaleTimeString()} · {row.task_id.slice(0, 8) || 'plan'} · a{row.attempt}#{row.seq} · {row.type} · {row.text}
                </div>
              ))}
            </div>
          )}

          {!isLoading && count === 0 && liveRows.length === 0 && (
            <div className={styles.empty}>
              {failedOnly && <AlertTriangle size={11} aria-hidden />}
              {failedOnly ? 'No failed attempts.' : 'No planning or agent attempts yet.'}
            </div>
          )}

          {selectedAttempt && (
            <AttemptLogViewer
              planId={planId}
              attemptId={selectedAttempt.id}
              status={selectedAttempt.status}
              onClose={() => setSelectedAttempt(null)}
            />
          )}
        </div>
      )}
    </div>
  );
}
