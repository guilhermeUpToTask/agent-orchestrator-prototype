import React from 'react';
import { Pencil, RotateCcw, Trash2, X } from 'lucide-react';
import { useParams } from 'react-router-dom';
import { usePlannerStore } from '../store/plannerStore';
import { useAgentEvents, useAgents, useApplyEdit, usePlan, useRetryTask } from '../lib/queries';
import { tokens } from '../styles/tokens';
import { StatusBadge } from './StatusBadge';
import { Button, CountChip, Field, Input, Select, TextArea } from './ui';
import styles from './DetailPanel.module.css';
import { attemptLabel, verificationLabel } from '../lib/taskLabels';

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
  const retryTask = useRetryTask(planId);
  const {
    data: taskEvents = [],
    isLoading: eventsLoading,
    error: eventsError,
  } = useAgentEvents(planId || null, selectedTaskId ?? undefined);
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
  const attempt = attemptLabel(task, agent);
  const verification = verificationLabel(task);

  // Editable exactly where the backend allows: at the pre-execution gate, or
  // while the plan is paused; and only a pending task (or a failed one while
  // paused). Mirrors edit_service guards so we don't offer a 422.
  const blockTargetsTask = plan?.block?.task_id === task.id
    && plan.block.goal_id === goal.id;
  const blockedEdit = !!blockTargetsTask
    && !!plan?.block?.legal_resolutions.includes("edit_task");
  const editContext = plan?.phase === "awaiting_review" || !!plan?.paused || blockedEdit;
  const taskMutable =
    task.status === "pending"
    || (task.status === "failed" && (!!plan?.paused || blockedEdit));
  const canEdit = editContext && taskMutable;
  const canRetry = task.status === "failed" && (
    !!plan?.paused
    || (
      !!blockTargetsTask
      && (
        !!plan?.block?.legal_resolutions.includes("retry_stage")
        || !!plan?.block?.legal_resolutions.includes("wait_and_retry")
      )
    )
  );

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
        {canRetry && (
          <Button
            size="sm"
            onClick={() => retryTask.mutate({ goalId: goal.id, taskId: task.id })}
            pending={retryTask.isPending}
            title={plan?.paused ? "Retry task; Resume remains separate" : "Retry failed task"}
          >
            <RotateCcw size={12} aria-hidden /> Retry
          </Button>
        )}
        {canEdit && !editing && (
          <Button
            variant="icon"
            onClick={() => {
              // seed from the CURRENT task when entering edit mode, so a plan
              // refetch since mount can't leave the form showing stale values
              setName(task.name);
              setDescription(task.description);
              setEditing(true);
            }}
            aria-label="Edit task"
          >
            <Pencil size={14} aria-hidden />
          </Button>
        )}
        {canEdit && (
          <Button variant="icon" onClick={deleteTask} aria-label="Delete task">
            <Trash2 size={14} aria-hidden />
          </Button>
        )}
        <Button variant="icon" onClick={() => selectTask(null)} aria-label="Close task detail">
          <X size={15} aria-hidden />
        </Button>
      </div>

      {editing ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-2)', marginBottom: 'var(--sp-2)' }}>
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Task name"
            aria-label="Task name"
          />
          <TextArea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Description"
            rows={3}
            aria-label="Task description"
          />
          <div style={{ display: 'flex', gap: 'var(--sp-2)' }}>
            <Button
              variant="primary"
              onClick={saveEdit}
              disabled={!name.trim()}
              pending={applyEdit.isPending}
            >
              Save
            </Button>
            <Button onClick={() => setEditing(false)}>Cancel</Button>
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
          <Select
            value={task.agent_id ?? ''}
            onChange={(e) => rebind(e.target.value)}
            aria-label="Rebind agent"
            options={[
              { value: '', label: '(unbound)' },
              ...agents.map((a) => ({ value: a.id, label: a.name })),
            ]}
          />
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
          <div style={{ display: 'flex', gap: 'var(--sp-2)', alignItems: 'center', flexWrap: 'wrap' }}>
            {attempt ? (
              <CountChip tone="fail">{attempt}</CountChip>
            ) : (
              <span className={styles.monoText}>attempt {task.attempt}</span>
            )}
            {task.reopen_count > 0 && (
              <span className={styles.monoText}>reopened {task.reopen_count}×</span>
            )}
          </div>
        </Field>
      )}

      {verification && (
        <Field label="Verification">
          <CountChip tone={verification === 'verified' ? 'ok' : 'fail'}>
            {verification === 'verified' ? 'verified' : 'verification rejected'}
          </CountChip>
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

      {eventsLoading ? (
        <Field label="Agent log">
          <div className={styles.agentLog}>
            {[0, 1, 2].map((i) => (
              <div key={i} className="skeleton" style={{ height: 14, marginBottom: 4 }} />
            ))}
          </div>
        </Field>
      ) : eventsError ? (
        <Field label="Agent log">
          <div className={styles.agentLog} style={{ color: 'var(--fail-text)' }}>
            Agent log unavailable
          </div>
        </Field>
      ) : (
        taskEvents.length > 0 && (
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
        )
      )}
    </aside>
  );
}
