import React from 'react';
import { Plus, Trash2 } from 'lucide-react';
import { usePlannerStore } from '../store/plannerStore';
import {
  useActivateCycle,
  useApplyEdit,
  useApproveIntentGate,
  useApprovePlan,
  useCancelCycleDraft,
  useCancelIntent,
  useFinishReview,
  usePlan,
  useRecordOutputDisposition,
  useReopenReview,
  useReviseCycleDraft,
  useReviseIntent,
  useReplanFromReview,
} from '../lib/queries';
import { Dialog, ConfirmAction } from './ui';
import type { Plan } from '../types/ui';
import styles from './GatePanel.module.css';

/**
 * Legacy phase gates and cyclic artifact gates share a dedicated surface:
 *   AWAITING_REVIEW — the pre-execution gate: review the enriched roadmap,
 *                     approve to start execution.
 *   REVIEW          — the post-execution gate: finish the plan, or replan
 *                     the next phase (chat re-opens).
 * Every action states its consequence and requires a two-step confirm.
 */
export function GatePanel({ planId }: { planId: string }) {
  const gateOpen = usePlannerStore((s) => s.ui.gateOpen);
  const setGateOpen = usePlannerStore((s) => s.setGateOpen);
  const { data: plan } = usePlan(planId);

  if (!plan) return null;

  const close = () => setGateOpen(false);
  const gate = plan.pending_gate;
  const cyclicGate = gate && [
    'intent',
    'cycle_draft',
    'cycle_completion',
  ].includes(gate.subject_type);

  return (
    <Dialog
      open={gateOpen}
      onClose={close}
      ariaLabel="Approval gate"
      title="Operator gate"
      width={640}
    >
      {cyclicGate && gate && (
        <CyclicReviewGate plan={plan} planId={planId} onDone={close} />
      )}
      {!cyclicGate && plan.phase === 'awaiting_review' && (
        <PreExecutionGate plan={plan} planId={planId} onDone={close} />
      )}
      {!cyclicGate && plan.phase === 'review' && (
        <PostExecutionGate plan={plan} planId={planId} onDone={close} />
      )}
      {!cyclicGate && !['awaiting_review', 'review'].includes(plan.phase) && (
        <p className={styles.body}>
          Nothing is waiting on you — the plan is in “{plan.phase}”.
        </p>
      )}
    </Dialog>
  );
}

function CyclicReviewGate({
  plan,
  planId,
  onDone,
}: {
  plan: Plan;
  planId: string;
  onDone: () => void;
}) {
  const gate = plan.pending_gate;
  if (!gate) return null;

  return (
    <CyclicReviewGateActions
      plan={plan}
      planId={planId}
      gateId={gate.id}
      revision={gate.subject_revision}
      subjectType={gate.subject_type}
      continuation={gate.continuation}
      allowedDecisions={gate.allowed_decisions}
      onDone={onDone}
    />
  );
}

function CyclicReviewGateActions({
  plan,
  planId,
  gateId,
  revision,
  subjectType,
  continuation,
  allowedDecisions,
  onDone,
}: {
  plan: Plan;
  planId: string;
  gateId: string;
  revision: number;
  subjectType: string;
  continuation: string;
  allowedDecisions: string[];
  onDone: () => void;
}) {
  const approveIntent = useApproveIntentGate(planId, gateId, revision);
  const cancelIntent = useCancelIntent(planId);
  const activateCycle = useActivateCycle(planId, gateId, revision);
  const cancelDraft = useCancelCycleDraft(planId);
  const publish = useRecordOutputDisposition(planId, gateId, revision);
  const cycleRef = plan.active_cycle
    ? `refs/heads/cycle/${plan.active_cycle.id}`
    : null;

  const complete = (
    disposition: 'open_pr' | 'merge' | 'retain_branch' | 'discard',
  ) => publish.mutate(
    {
      disposition,
      outputReference: disposition === 'discard' ? null : cycleRef,
    },
    { onSuccess: onDone },
  );

  return (
    <div className={styles.content}>
      <h2 className={styles.title}>Review {subjectType.replace(/_/g, ' ')}</h2>
      <p className={styles.body}>{continuation}</p>

      {subjectType === "intent" && (
        <>
          <IntentProposalEditor plan={plan} planId={planId} />
          <ConfirmAction
            label="Approve intent"
            consequence="Locks this intent revision and starts cycle architecture."
            pending={approveIntent.isPending}
            onConfirm={() => approveIntent.mutate(undefined, { onSuccess: onDone })}
          />
          {allowedDecisions.includes('cancel') && (
            <ConfirmAction
              label="Cancel intent"
              consequence="Discards this proposal and returns to the prior idle or paused state."
              pending={cancelIntent.isPending}
              demoted
              onConfirm={() => cancelIntent.mutate(undefined, { onSuccess: onDone })}
            />
          )}
        </>
      )}

      {subjectType === "cycle_draft" && (
        <>
          <CycleDraftEditor plan={plan} planId={planId} />
          <ConfirmAction
            label="Approve & activate cycle"
            consequence="Freezes this draft revision and makes its goals executable."
            pending={activateCycle.isPending}
            onConfirm={() => activateCycle.mutate(undefined, { onSuccess: onDone })}
          />
          {allowedDecisions.includes('cancel') && (
            <ConfirmAction
              label="Cancel cycle draft"
              consequence="Discards this draft without starting its goals."
              pending={cancelDraft.isPending}
              demoted
              onConfirm={() => cancelDraft.mutate(undefined, { onSuccess: onDone })}
            />
          )}
        </>
      )}

      {subjectType === 'cycle_completion' && allowedDecisions.map((decision) => {
        const disposition = decision as 'open_pr' | 'merge' | 'retain_branch' | 'discard';
        return (
          <ConfirmAction
            key={decision}
            label={decision.replace(/_/g, ' ')}
            consequence={
              decision === 'discard'
                ? 'Marks the cycle cancelled and records no promoted output reference.'
                : `Records ${cycleRef ?? 'the cycle branch'} as the promoted output.`
            }
            pending={publish.isPending}
            demoted={decision !== 'merge'}
            tone={decision === 'discard' ? 'danger' : 'gate'}
            onConfirm={() => complete(disposition)}
          />
        );
      })}
    </div>
  );
}

function IntentProposalEditor({ plan, planId }: { plan: Plan; planId: string }) {
  const proposal = plan.intent_proposal;
  const revise = useReviseIntent(planId);
  const [objective, setObjective] = React.useState(proposal?.objective ?? "");
  const [scope, setScope] = React.useState((proposal?.scope ?? []).join("\n"));
  const [constraints, setConstraints] = React.useState(
    (proposal?.constraints ?? []).join("\n"),
  );
  const [exclusions, setExclusions] = React.useState(
    (proposal?.exclusions ?? []).join("\n"),
  );

  React.useEffect(() => {
    setObjective(proposal?.objective ?? "");
    setScope((proposal?.scope ?? []).join("\n"));
    setConstraints((proposal?.constraints ?? []).join("\n"));
    setExclusions((proposal?.exclusions ?? []).join("\n"));
  }, [proposal?.id, proposal?.revision]);

  if (!proposal) {
    return <p className={styles.body}>The intent artifact is unavailable.</p>;
  }

  const lines = (value: string) =>
    value.split("\n").map((item) => item.trim()).filter(Boolean);

  return (
    <div className={styles.artifact}>
      <div className={styles.artifactHeader}>
        <span className="label">
          {proposal.kind === "replan" ? "Replan intent" : "Initial intent"} · revision {proposal.revision}
        </span>
        {proposal.source_cycle_id && (
          <span className={styles.sourceTag}>from {proposal.source_cycle_id}</span>
        )}
      </div>
      <label className={styles.editorField}>
        <span className="label">Objective</span>
        <textarea
          className={styles.editInput}
          value={objective}
          rows={4}
          onChange={(event) => setObjective(event.target.value)}
        />
      </label>
      <label className={styles.editorField}>
        <span className="label">Scope · one item per line</span>
        <textarea
          className={styles.editInput}
          value={scope}
          rows={3}
          onChange={(event) => setScope(event.target.value)}
        />
      </label>
      <label className={styles.editorField}>
        <span className="label">Constraints · one item per line</span>
        <textarea
          className={styles.editInput}
          value={constraints}
          rows={3}
          onChange={(event) => setConstraints(event.target.value)}
        />
      </label>
      <label className={styles.editorField}>
        <span className="label">Exclusions · one item per line</span>
        <textarea
          className={styles.editInput}
          value={exclusions}
          rows={3}
          onChange={(event) => setExclusions(event.target.value)}
        />
      </label>
      <button
        className={styles.reviseBtn}
        disabled={!objective.trim() || revise.isPending}
        onClick={() => revise.mutate({
          objective: objective.trim(),
          scope: lines(scope),
          constraints: lines(constraints),
          exclusions: lines(exclusions),
          kind: proposal.kind,
          planner_session_ref: proposal.planner_session_ref,
        })}
      >
        Save as revision {proposal.revision + 1}
      </button>
    </div>
  );
}

function CycleDraftEditor({ plan, planId }: { plan: Plan; planId: string }) {
  const draft = plan.cycle_draft;
  const revise = useReviseCycleDraft(planId);
  const [goals, setGoals] = React.useState(() => draft?.goals ?? []);
  const [treatment, setTreatment] = React.useState(
    draft?.unfinished_source_treatment ?? "",
  );

  React.useEffect(() => {
    setGoals(draft?.goals ?? []);
    setTreatment(draft?.unfinished_source_treatment ?? "");
  }, [draft?.id, draft?.revision]);

  if (!draft) {
    return <p className={styles.body}>The cycle draft artifact is unavailable.</p>;
  }

  const source = plan.cycles.find((cycle) => cycle.id === draft.source_cycle_id);
  const save = () => revise.mutate({
    goals: goals.map((goal, position) => ({ ...goal, position })),
    unfinished_source_treatment: treatment.trim() || null,
  });

  return (
    <div className={styles.diffGrid}>
      {source && (
        <section className={styles.artifact}>
          <div className="label">Locked source cycle · {source.id}</div>
          <p className={styles.body}>
            Completed work remains history. Unfinished work is superseded only after approval.
          </p>
          {source.goals
            .slice()
            .sort((a, b) => a.position - b.position)
            .map((goal) => {
              const done = goal.tasks.filter((task) => task.status === "done").length;
              return (
                <div className={styles.sourceGoal} key={goal.id}>
                  <span>{goal.name}</span>
                  <span className={styles.sourceTag}>
                    {goal.status} · {done}/{goal.tasks.length} done
                  </span>
                </div>
              );
            })}
        </section>
      )}

      <section className={styles.artifact}>
        <div className={styles.artifactHeader}>
          <span className="label">Proposed cycle · revision {draft.revision}</span>
          <span className={styles.sourceTag}>{goals.length} goals</span>
        </div>
        {goals.map((goal, index) => (
          <div className={styles.proposedGoal} key={goal.key}>
            <input
              className={styles.editInput}
              value={goal.name}
              aria-label={"Proposed goal " + (index + 1) + " name"}
              onChange={(event) => setGoals((current) =>
                current.map((item, itemIndex) =>
                  itemIndex === index ? { ...item, name: event.target.value } : item,
                ),
              )}
            />
            <textarea
              className={styles.editInput}
              value={goal.objective}
              rows={2}
              aria-label={"Proposed goal " + (index + 1) + " objective"}
              onChange={(event) => setGoals((current) =>
                current.map((item, itemIndex) =>
                  itemIndex === index ? { ...item, objective: event.target.value } : item,
                ),
              )}
            />
            <div className={styles.editRow}>
              <span className={styles.sourceTag}>
                depends on: {goal.depends_on.join(", ") || "none"}
              </span>
              <button
                className={styles.iconBtn}
                aria-label={"Remove proposed goal " + goal.name}
                onClick={() => setGoals((current) =>
                  current.filter((_, itemIndex) => itemIndex !== index),
                )}
                disabled={goals.length <= 1}
              >
                <Trash2 size={12} aria-hidden />
              </button>
            </div>
          </div>
        ))}
        <button
          className={styles.reviseBtn}
          onClick={() => setGoals((current) => [
            ...current,
            {
              key: "goal-" + Date.now().toString(36),
              name: "New goal",
              objective: "",
              position: current.length,
              depends_on: [],
            },
          ])}
        >
          <Plus size={12} aria-hidden /> Add proposed goal
        </button>
        <label className={styles.editorField}>
          <span className="label">Unfinished source treatment</span>
          <textarea
            className={styles.editInput}
            value={treatment}
            rows={3}
            onChange={(event) => setTreatment(event.target.value)}
          />
        </label>
        <button
          className={styles.reviseBtn}
          disabled={
            revise.isPending
            || goals.length === 0
            || goals.some((goal) => !goal.name.trim() || !goal.objective.trim())
          }
          onClick={save}
        >
          Save as revision {draft.revision + 1}
        </button>
      </section>
    </div>
  );
}

/* ── Roadmap summary shared by both gates ─────────────────────────────────── */

function RoadmapDoc({ plan }: { plan: Plan }) {
  const liveGoals = plan.goals.filter(
    (g) => !['done', 'failed', 'skipped'].includes(g.status),
  );
  const shown = liveGoals.length > 0 ? liveGoals : plan.goals;
  return (
    <div className={styles.doc}>
      {shown
        .slice()
        .sort((a, b) => a.position - b.position)
        .map((g) => (
          <section key={g.id} className={styles.docSection}>
            <div className="label">
              {g.name} · {g.status}
            </div>
            <div className={styles.docBody}>
              <ul className={styles.docList}>
                {g.tasks
                  .slice()
                  .sort((a, b) => a.position - b.position)
                  .map((t) => (
                    <li key={t.id}>
                      {t.name}
                      {t.agent_id ? ` — ${t.agent_id}` : ''}
                      {t.required_capabilities.length > 0
                        ? ` [${t.required_capabilities.join(', ')}]`
                        : ''}
                    </li>
                  ))}
              </ul>
            </div>
          </section>
        ))}
    </div>
  );
}

/* ── Editable roadmap (pre-execution gate) ────────────────────────────────── */

function RoadmapEditor({ plan, planId }: { plan: Plan; planId: string }) {
  const edit = useApplyEdit(planId);
  const [newTaskByGoal, setNewTaskByGoal] = React.useState<Record<string, string>>({});
  const liveGoals = plan.goals
    .filter((g) => !['done', 'failed', 'skipped'].includes(g.status))
    .slice()
    .sort((a, b) => a.position - b.position);

  const renameGoal = (goalId: string, name: string, current: string) => {
    if (name.trim() && name !== current) {
      edit.mutate({ type: 'update_goal', goal_id: goalId, name });
    }
  };
  const renameTask = (goalId: string, taskId: string, name: string, current: string) => {
    if (name.trim() && name !== current) {
      edit.mutate({ type: 'update_task', goal_id: goalId, task_id: taskId, name });
    }
  };

  return (
    <div className={styles.doc}>
      {liveGoals.map((g) => (
        <section key={g.id} className={styles.docSection}>
          <div className={styles.editRow}>
            <input
              className={styles.editInput}
              defaultValue={g.name}
              onBlur={(e) => renameGoal(g.id, e.target.value, g.name)}
              aria-label={`Goal ${g.name} name`}
            />
            <button
              className={styles.iconBtn}
              onClick={() => edit.mutate({ type: 'remove_goal', goal_id: g.id })}
              aria-label={`Remove goal ${g.name}`}
              disabled={liveGoals.length <= 1}
              title={liveGoals.length <= 1 ? 'A plan needs at least one goal' : 'Remove goal'}
            >
              <Trash2 size={13} aria-hidden />
            </button>
          </div>
          <ul className={styles.docList}>
            {g.tasks
              .slice()
              .sort((a, b) => a.position - b.position)
              .map((t) => (
                <li key={t.id} className={styles.editRow}>
                  <input
                    className={styles.editInput}
                    defaultValue={t.name}
                    onBlur={(e) => renameTask(g.id, t.id, e.target.value, t.name)}
                    aria-label={`Task ${t.name} name`}
                  />
                  <button
                    className={styles.iconBtn}
                    onClick={() => edit.mutate({ type: 'remove_task', goal_id: g.id, task_id: t.id })}
                    aria-label={`Remove task ${t.name}`}
                  >
                    <Trash2 size={12} aria-hidden />
                  </button>
                </li>
              ))}
            <li className={styles.editRow}>
              <input
                className={styles.editInput}
                placeholder="Add a task…"
                value={newTaskByGoal[g.id] ?? ''}
                onChange={(e) =>
                  setNewTaskByGoal((m) => ({ ...m, [g.id]: e.target.value }))
                }
                aria-label={`New task for ${g.name}`}
              />
              <button
                className={styles.iconBtn}
                disabled={!(newTaskByGoal[g.id] ?? '').trim()}
                onClick={() => {
                  const name = (newTaskByGoal[g.id] ?? '').trim();
                  if (!name) return;
                  edit.mutate(
                    { type: 'add_task', goal_id: g.id, task: { name } },
                    { onSuccess: () => setNewTaskByGoal((m) => ({ ...m, [g.id]: '' })) },
                  );
                }}
                aria-label={`Add task to ${g.name}`}
              >
                <Plus size={13} aria-hidden />
              </button>
            </li>
          </ul>
        </section>
      ))}
    </div>
  );
}

/* ── AWAITING_REVIEW: the pre-execution gate ──────────────────────────────── */

function PreExecutionGate({
  plan, planId, onDone,
}: {
  plan: Plan;
  planId: string;
  onDone: () => void;
}) {
  const approve = useApprovePlan(planId);
  const reopen = useReopenReview(planId);
  return (
    <div className={styles.content}>
      <h2 className={styles.title}>Approve the roadmap (iteration {plan.iteration})</h2>
      <p className={styles.body}>
        Enrichment is done: every goal below carries executable tasks. Edit the
        roadmap inline, approve to start execution, or reopen the chat to plan a
        different roadmap.
      </p>
      <RoadmapEditor plan={plan} planId={planId} />
      <ConfirmAction
        label="Approve & start execution"
        consequence="Workers begin executing the tasks above, goal by goal."
        pending={approve.isPending}
        onConfirm={() => approve.mutate(undefined, { onSuccess: onDone })}
      />
      <ConfirmAction
        label="Request changes (reopen chat)"
        consequence="Reopens the planning conversation. The next commit REPLACES this roadmap."
        pending={reopen.isPending}
        demoted
        onConfirm={() => reopen.mutate(undefined, { onSuccess: onDone })}
      />
    </div>
  );
}

/* ── REVIEW: the post-execution gate ──────────────────────────────────────── */

function PostExecutionGate({
  plan, planId, onDone,
}: {
  plan: Plan;
  planId: string;
  onDone: () => void;
}) {
  const finish = useFinishReview(planId);
  const replan = useReplanFromReview(planId);
  return (
    <div className={styles.content}>
      <h2 className={styles.title}>Review the results (iteration {plan.iteration})</h2>
      <p className={styles.body}>
        Execution has exhausted the roadmap. Finish the plan, or open a replan
        conversation to plan the next iteration on top of these results.
      </p>
      <RoadmapDoc plan={plan} />
      <ConfirmAction
        label="Finish plan"
        consequence="Marks the plan DONE. No further work will run."
        pending={finish.isPending}
        onConfirm={() => finish.mutate(undefined, { onSuccess: onDone })}
      />
      <ConfirmAction
        label="Replan next iteration"
        consequence="Opens the replanning chat. Completed goals stay as history; a new goal set is planned with the reasoner."
        pending={replan.isPending}
        demoted
        onConfirm={() => replan.mutate(undefined, { onSuccess: onDone })}
      />
    </div>
  );
}
