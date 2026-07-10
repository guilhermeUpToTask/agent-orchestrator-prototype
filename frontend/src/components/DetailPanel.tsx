import React from 'react';
import { Pencil, Trash2, X } from 'lucide-react';
import { useParams } from 'react-router-dom';
import { usePlannerStore } from '../store/plannerStore';
import { useAgentEvents, useAgents, useApplyEdit, usePlan } from '../lib/queries';
import { tokens } from '../styles/tokens';
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
  const applyEdit = useApplyEdit(planId);
  const { data: taskEvents = [] } = useAgentEvents(
    planId || null,
    selectedTaskId ?? undefined,
  );
  const [editing, setEditing] = React.useState(false);
  const [name, setName] = React.useState('');
  const [description, setDescription] = React.useState('');

  const goal = plan?.goals.find((g) => g.tasks.some((t) => t.id === selectedTaskId));
  const task = goal?.tasks.find((t) => t.id === selectedTaskId);

  // reset the edit form whenever the selected task changes
  React.useEffect(() => {
    setEditing(false);
    setName(task?.name ?? '');
    setDescription(task?.description ?? '');
  }, [selectedTaskId]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!detailPanelOpen || !task || !goal) return null;

  const agent = task.agent_id ? agents.find((a) => a.id === task.agent_id) ?? null : null;

  // Editable exactly where the backend allows: at the pre-execution gate, or
  // while the plan is paused; and only a pending task (or a failed one while
  // paused). Mirrors edit_service guards so we don't offer a 422.
  const editContext = plan?.phase === 'awaiting_review' || !!plan?.paused;
  const taskMutable =
    task.status === 'pending' || (task.status === 'failed' && !!plan?.paused);
  const canEdit = editContext && taskMutable;

  const saveEdit = () => {
    if (name !== task.name || description !== task.description) {
      applyEdit.mutate(
        { type: 'update_task', goal_id: goal.id, task_id: task.id, name, description },
        { onSuccess: () => setEditing(false) },
      );
    } else {
      setEditing(false);
    }
  };
  const deleteTask = () => {
    applyEdit.mutate({ type: 'remove_task', goal_id: goal.id, task_id: task.id });
    selectTask(null);
  };
  const rebind = (agentId: string) => {
    if (agentId && agentId !== task.agent_id) {
      applyEdit.mutate({
        type: 'rebind_task_agent',
        goal_id: goal.id,
        task_id: task.id,
        agent_id: agentId,
      });
    }
  };

  return (
    <aside className={styles.panel} aria-label="Task detail">
      <div className={styles.head}>
        <StatusBadge domain="status" value={task.status} />
        <div style={{ flex: 1 }} />
        {canEdit && !editing && (
          <button
            onClick={() => {
              // seed from the CURRENT task when entering edit mode, so a plan
              // refetch since mount can't leave the form showing stale values
              setName(task.name);
              setDescription(task.description);
              setEditing(true);
            }}
            aria-label="Edit task"
            className={styles.close}
          >
            <Pencil size={14} aria-hidden />
          </button>
        )}
        {canEdit && (
          <button onClick={deleteTask} aria-label="Delete task" className={styles.close}>
            <Trash2 size={14} aria-hidden />
          </button>
        )}
        <button
          onClick={() => selectTask(null)}
          aria-label="Close task detail"
          className={styles.close}
        >
          <X size={15} aria-hidden />
        </button>
      </div>

      {editing ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 8 }}>
          <input
            className={styles.editInput}
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Task name"
            aria-label="Task name"
          />
          <textarea
            className={styles.editInput}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Description"
            rows={3}
            aria-label="Task description"
          />
          <div style={{ display: 'flex', gap: 8 }}>
            <button
              className={styles.saveBtn}
              onClick={saveEdit}
              disabled={applyEdit.isPending || !name.trim()}
            >
              Save
            </button>
            <button className={styles.cancelBtn} onClick={() => setEditing(false)}>
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <>
          <h2 className={styles.name}>{task.name}</h2>
          <div className={styles.id}>{task.id}</div>
        </>
      )}

      <Field label="Goal">
        <span className={styles.text}>{goal.name}</span>
      </Field>

      {task.description && !editing && (
        <Field label="Description">
          <p className={styles.text}>{task.description}</p>
        </Field>
      )}

      <Field label="Agent">
        {canEdit ? (
          <select
            className={styles.editInput}
            value={task.agent_id ?? ''}
            onChange={(e) => rebind(e.target.value)}
            aria-label="Rebind agent"
          >
            <option value="">(unbound)</option>
            {agents.map((a) => (
              <option key={a.id} value={a.id}>{a.name}</option>
            ))}
          </select>
        ) : agent ? (
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

      {taskEvents.length > 0 && (
        <Field label="Agent log">
          <div className={styles.agentLog}>
            {taskEvents.map((e) => (
              <div key={e.event_id} className={styles.agentLogLine}>
                <span style={{ color: tokens.textDim }}>
                  {new Date(e.occurred_at).toLocaleTimeString()}{' '}
                </span>
                <span
                  style={{
                    color:
                      e.type === 'agent.failed' ? tokens.red
                      : e.type === 'agent.finished' ? tokens.green
                      : tokens.purple,
                  }}
                >
                  a{e.attempt}#{e.seq} {e.type}
                </span>{' '}
                {e.payload.reason ?? e.payload.elapsed_seconds ?? e.payload.runtime ?? ''}
              </div>
            ))}
          </div>
        </Field>
      )}
    </aside>
  );
}
