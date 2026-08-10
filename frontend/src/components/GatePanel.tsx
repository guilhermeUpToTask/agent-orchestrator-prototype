import React from 'react';
import { Plus, Trash2 } from 'lucide-react';
import { usePlannerStore } from '../store/plannerStore';
import {
  useActivateCycle,
  useApproveIntentGate,
  useCancelCycleDraft,
  useCancelIntent,
  usePlan,
  useRecordOutputDisposition,
  useReviseCycleDraft,
  useReviseIntent,
} from '../lib/queries';
import { Dialog, ConfirmAction } from './ui';
import type { Plan } from '../types/ui';
import { PreExecutionGate, PostExecutionGate } from './LegacyGatePanel';
import styles from './GatePanel.module.css';

/**
 * The operator gate: one dialog, whichever artifact is waiting on a decision.
 *
 * The CYCLIC gates live here — intent, cycle draft, cycle completion. The
 * legacy nine-phase ones moved to `LegacyGatePanel.tsx` (P9 task 3): they are
 * reachable only by pre-cyclic plans, and carrying both lifecycles in one file
 * is what made this read as a monster when its components are each small.
 *
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
      {!cyclicGate && plan.legacy_phase != null && plan.phase === 'awaiting_review' && (
        <PreExecutionGate plan={plan} planId={planId} onDone={close} />
      )}
      {!cyclicGate && plan.legacy_phase != null && plan.phase === 'review' && (
        <PostExecutionGate plan={plan} planId={planId} onDone={close} />
      )}
      {!cyclicGate && (
        plan.legacy_phase == null || !['awaiting_review', 'review'].includes(plan.phase)
      ) && (
        <p className={styles.body}>
          Nothing is waiting on you — status is “{plan.status}” and activity is “{plan.activity}”.
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

