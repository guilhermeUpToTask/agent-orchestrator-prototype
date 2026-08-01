import React from 'react';
import { Pencil, RotateCcw, Trash2, Wrench, X } from 'lucide-react';
import { useParams } from 'react-router-dom';
import { usePlannerStore } from '../store/plannerStore';
import { useAgentEvents, useAgents, useApplyEdit, usePlan, useRetryTask } from '../lib/queries';
import { tokens } from '../styles/tokens';
import { StatusBadge } from './StatusBadge';
import { Button, CountChip, Field, Input, Select, TextArea } from './ui';
import styles from './DetailPanel.module.css';
import { attemptLabel, verificationLabel } from '../lib/taskLabels';
import type { ContractCriterion, Task, VerificationStrategy } from '../types/ui';
import { currentPlanGoals } from '../lib/planTruth';
import {
  changedContractFields,
  contractFormBaseline,
  type ContractFormValues,
} from '../lib/contractEdit';

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
  const [contractEditing, setContractEditing] = React.useState(false);
  const [name, setName] = React.useState('');
  const [description, setDescription] = React.useState('');

  const goal = currentPlanGoals(plan).find((g) => g.tasks.some((t) => t.id === selectedTaskId));
  const task = goal?.tasks.find((t) => t.id === selectedTaskId);

  // reset the edit form whenever the selected task changes
  React.useEffect(() => {
    setEditing(false);
    setContractEditing(false);
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
  const targetBlock = (
    plan?.block?.task_id === task.id && plan.block.goal_id === goal.id
      ? plan.block
      : plan?.goal_blocks[goal.id]?.task_id === task.id
        ? plan.goal_blocks[goal.id]
        : null
  );
  const blockedEdit = !!targetBlock?.legal_resolutions.includes("edit_task");
  const editContext = !!plan?.legal_actions.includes('edit_pending_work')
    || !!plan?.paused
    || blockedEdit;
  const taskMutable =
    task.status === "pending"
    || (task.status === "failed" && (!!plan?.paused || blockedEdit));
  const canEdit = editContext && taskMutable;
  const canRetry = task.status === "failed" && (
    !!plan?.paused
    || (
      !!targetBlock
      && (
        !!targetBlock?.legal_resolutions.includes("retry_stage")
        || !!targetBlock?.legal_resolutions.includes("wait_and_retry")
      )
    )
  );
  const canRepairContract = blockedEdit && !!task.contract;

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
              setContractEditing(false);
              setEditing(true);
            }}
            aria-label="Edit task"
          >
            <Pencil size={14} aria-hidden />
          </Button>
        )}
        {canRepairContract && !contractEditing && (
          <Button
            variant="icon"
            onClick={() => {
              setEditing(false);
              setContractEditing(true);
            }}
            aria-label="Repair task contract"
            title="Repair the frozen execution contract"
          >
            <Wrench size={14} aria-hidden />
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

      {contractEditing && task.contract && (
        <ContractEditor
          task={task}
          pending={applyEdit.isPending}
          onCancel={() => setContractEditing(false)}
          onSave={(fields) => {
            // A save with nothing changed is not a revision. Submitting it
            // anyway would spend a mutable-task window on a no-op.
            if (Object.keys(fields).length === 0) {
              setContractEditing(false);
              return;
            }
            applyEdit.mutate(
              {
                type: 'update_task_contract',
                goal_id: goal.id,
                task_id: task.id,
                ...fields,
              },
              { onSuccess: () => setContractEditing(false) },
            );
          }}
        />
      )}

      <Field label="Goal">
        <span className={styles.text}>{goal.name}</span>
      </Field>

      {task.description && !editing && (
        <Field label="Description">
          <p className={styles.text}>{task.description}</p>
        </Field>
      )}

      {task.contract && !contractEditing && (
        <Field label="Execution contract">
          <div className={styles.contractSummary}>
            <span>{task.contract.objective}</span>
            <span className={styles.monoText}>
              {task.contract.acceptance_criteria.length} criteria ·{' '}
              {task.contract.verification_strategy.replace(/_/g, ' ')} · revision{' '}
              {task.contract.revision ?? task.revision ?? 1}
            </span>
          </div>
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

function ContractEditor({
  task,
  pending,
  onSave,
  onCancel,
}: {
  task: Task;
  pending: boolean;
  onSave: (fields: Partial<ContractFormValues>) => void;
  onCancel: () => void;
}) {
  const contract = task.contract!;
  const [objective, setObjective] = React.useState(contract.objective);
  const [criteria, setCriteria] = React.useState(
    contract.acceptance_criteria.map((criterion) => `${criterion.id} | ${criterion.description}`).join('\n'),
  );
  const [strategy, setStrategy] = React.useState<VerificationStrategy>(contract.verification_strategy);
  const [allowedScope, setAllowedScope] = React.useState(contract.allowed_scope.join('\n'));
  const [forbiddenScope, setForbiddenScope] = React.useState((contract.forbidden_scope ?? []).join('\n'));
  const [commands, setCommands] = React.useState(contract.verification_commands.join('\n'));
  const [goalCriterionIds, setGoalCriterionIds] = React.useState(contract.goal_criterion_ids.join('\n'));
  const [capabilities, setCapabilities] = React.useState(
    (contract.required_capabilities ?? task.required_capabilities).join('\n'),
  );

  // The contract as loaded, normalized the way the form parses its text areas:
  // the baseline every save diffs against, so an untouched field never travels.
  const loaded = contractFormBaseline(contract, task.required_capabilities);

  const parsedCriteria = lines(criteria).map((line, index) => {
    const separator = line.indexOf('|');
    return separator < 0
      ? { id: contract.acceptance_criteria[index]?.id ?? `criterion-${index + 1}`, description: line }
      : { id: line.slice(0, separator).trim(), description: line.slice(separator + 1).trim() };
  }).filter((criterion) => criterion.id && criterion.description);
  const canSave = objective.trim() !== '' && parsedCriteria.length > 0 && lines(commands).length > 0;

  return (
    <div className={styles.contractEditor} aria-label="Repair execution contract">
      <div className="label">Contract repair</div>
      <p className={styles.muted}>
        Saving creates a new contract revision. Attempt identity and observed evidence stay read-only.
      </p>
      <Field label="Objective">
        <TextArea value={objective} onChange={(event) => setObjective(event.target.value)} rows={3} />
      </Field>
      <Field label="Acceptance criteria" hint="One per line: criterion-id | description">
        <TextArea value={criteria} onChange={(event) => setCriteria(event.target.value)} rows={5} mono />
      </Field>
      <Field label="Verification strategy">
        <Select
          value={strategy}
          onChange={(event) => setStrategy(event.target.value as VerificationStrategy)}
          options={[
            { value: 'tdd', label: 'Test-driven development' },
            { value: 'characterization', label: 'Characterization' },
            { value: 'executable_check', label: 'Executable check' },
          ]}
        />
      </Field>
      <Field label="Verification commands" hint="One exact command per line">
        <TextArea value={commands} onChange={(event) => setCommands(event.target.value)} rows={3} mono />
      </Field>
      <Field label="Allowed scope" hint="One repository path per line">
        <TextArea value={allowedScope} onChange={(event) => setAllowedScope(event.target.value)} rows={3} mono />
      </Field>
      <Field label="Forbidden scope" hint="One repository path per line">
        <TextArea value={forbiddenScope} onChange={(event) => setForbiddenScope(event.target.value)} rows={3} mono />
      </Field>
      <Field label="Goal criterion IDs" hint="One criterion ID per line">
        <TextArea value={goalCriterionIds} onChange={(event) => setGoalCriterionIds(event.target.value)} rows={2} mono />
      </Field>
      <Field label="Required capabilities" hint="One capability ID per line">
        <TextArea value={capabilities} onChange={(event) => setCapabilities(event.target.value)} rows={2} mono />
      </Field>
      <div className={styles.contractActions}>
        <Button onClick={onCancel}>Cancel</Button>
        <Button
          variant="primary"
          disabled={!canSave}
          pending={pending}
          onClick={() => onSave(changedContractFields(loaded, {
            objective: objective.trim(),
            acceptance_criteria: parsedCriteria,
            verification_strategy: strategy,
            verification_commands: lines(commands),
            allowed_scope: lines(allowedScope),
            forbidden_scope: lines(forbiddenScope),
            goal_criterion_ids: lines(goalCriterionIds),
            required_capabilities: lines(capabilities),
          }))}
        >
          Save contract revision
        </Button>
      </div>
    </div>
  );
}

function lines(value: string): string[] {
  return value.split('\n').map((line) => line.trim()).filter(Boolean);
}
