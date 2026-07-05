import React from 'react';
import { X } from 'lucide-react';
import { useParams } from 'react-router-dom';
import { usePlannerStore } from '../store/plannerStore';
import { useAgents, usePlan } from '../lib/queries';
import { StatusBadge } from './StatusBadge';
import styles from './DetailPanel.module.css';

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className={styles.field}>
      <div className={`label ${styles.fieldLabel}`}>{label}</div>
      {children}
    </div>
  );
}

/**
 * The task inspector: everything the aggregate knows about one task —
 * status, agent binding, capabilities, attempts, and the persisted
 * TaskResult (output / failure reason) once the task settles.
 */
export function DetailPanel() {
  const { planId = '' } = useParams();
  const selectedTaskId = usePlannerStore((s) => s.ui.selectedTaskId);
  const detailPanelOpen = usePlannerStore((s) => s.ui.detailPanelOpen);
  const selectTask = usePlannerStore((s) => s.selectTask);

  const { data: plan } = usePlan(planId || null);
  const { data: agents = [] } = useAgents();

  const goal = plan?.goals.find((g) => g.tasks.some((t) => t.id === selectedTaskId));
  const task = goal?.tasks.find((t) => t.id === selectedTaskId);

  if (!detailPanelOpen || !task || !goal) return null;

  const agent = task.agent_id ? agents.find((a) => a.id === task.agent_id) ?? null : null;

  return (
    <aside className={styles.panel} aria-label="Task detail">
      <div className={styles.head}>
        <StatusBadge domain="status" value={task.status} />
        <button
          onClick={() => selectTask(null)}
          aria-label="Close task detail"
          className={styles.close}
        >
          <X size={15} aria-hidden />
        </button>
      </div>

      <h2 className={styles.name}>{task.name}</h2>
      <div className={styles.id}>{task.id}</div>

      <Field label="Goal">
        <span className={styles.text}>{goal.name}</span>
      </Field>

      {task.description && (
        <Field label="Description">
          <p className={styles.text}>{task.description}</p>
        </Field>
      )}

      <Field label="Agent">
        {agent ? (
          <span className={styles.tag}>{agent.name}</span>
        ) : (
          <span className={styles.muted}>unbound (bound at enrichment)</span>
        )}
      </Field>

      {task.required_capabilities.length > 0 && (
        <Field label="Required capabilities">
          <div className={styles.tags}>
            {task.required_capabilities.map((c) => (
              <span key={c} className={styles.tag}>
                {c}
              </span>
            ))}
          </div>
        </Field>
      )}

      {(task.attempt > 0 || task.reopen_count > 0) && (
        <Field label="Attempts">
          <span className={styles.monoText}>
            attempt {task.attempt}
            {task.reopen_count > 0 && ` · reopened ${task.reopen_count}×`}
          </span>
        </Field>
      )}

      {task.retry_not_before && task.status === 'pending' && (
        <Field label="Backoff gate">
          <span className={styles.backoff}>
            retry not before {new Date(task.retry_not_before).toLocaleTimeString()}
          </span>
        </Field>
      )}

      {task.result && (
        <Field label={task.result.status === 'success' ? 'Result' : 'Failure'}>
          {task.result.failure_reason && (
            <div className={styles.failure}>
              {task.result.failure_reason}
              {task.result.failure_kind && ` (${task.result.failure_kind})`}
            </div>
          )}
          {task.result.output && <pre className={styles.output}>{task.result.output}</pre>}
        </Field>
      )}
    </aside>
  );
}
