import React, { useEffect, useRef, useState } from 'react';
import { X } from 'lucide-react';
import { usePlannerStore } from '../store/plannerStore';
import {
  useApprovePlan,
  useFinishReview,
  usePlan,
  useReplanFromReview,
} from '../lib/queries';
import type { Plan } from '../types/ui';
import styles from './GatePanel.module.css';

/**
 * The two human gates of the 9-phase machine get a dedicated surface:
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
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!gateOpen) return;
    panelRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setGateOpen(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [gateOpen, setGateOpen]);

  if (!gateOpen || !plan) return null;

  const close = () => setGateOpen(false);

  return (
    <div className={styles.scrim} onClick={close}>
      <div
        ref={panelRef}
        className={styles.panel}
        role="dialog"
        aria-modal="true"
        aria-label="Approval gate"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
      >
        <header className={styles.header}>
          <span className="label">Operator gate</span>
          <button className={styles.close} onClick={close} aria-label="Close approval panel">
            <X size={15} aria-hidden />
          </button>
        </header>

        {plan.phase === 'awaiting_review' && (
          <PreExecutionGate plan={plan} planId={planId} onDone={close} />
        )}
        {plan.phase === 'review' && (
          <PostExecutionGate plan={plan} planId={planId} onDone={close} />
        )}
        {!['awaiting_review', 'review'].includes(plan.phase) && (
          <p className={styles.body}>
            Nothing is waiting on you — the plan is in “{plan.phase}”.
          </p>
        )}
      </div>
    </div>
  );
}

/* ── Two-step confirm: primary button → inline confirm with consequence ──── */

function ConfirmAction({
  label, consequence, pending, demoted, onConfirm,
}: {
  label: string;
  consequence: string;
  pending?: boolean;
  demoted?: boolean;
  onConfirm: () => void;
}) {
  const [arming, setArming] = useState(false);

  if (!arming) {
    return (
      <button
        className={demoted ? styles.demotedBtn : styles.armBtn}
        onClick={() => setArming(true)}
        disabled={pending}
      >
        {label}
      </button>
    );
  }

  return (
    <div className={styles.confirmRow} role="group" aria-label={`Confirm: ${label}`}>
      <span className={styles.consequence}>{consequence}</span>
      <button className={styles.cancelBtn} onClick={() => setArming(false)} disabled={pending}>
        Cancel
      </button>
      <button className={styles.confirmBtn} onClick={onConfirm} disabled={pending}>
        {pending ? 'Working…' : `Confirm: ${label}`}
      </button>
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

/* ── AWAITING_REVIEW: the pre-execution gate ──────────────────────────────── */

function PreExecutionGate({
  plan, planId, onDone,
}: {
  plan: Plan;
  planId: string;
  onDone: () => void;
}) {
  const approve = useApprovePlan(planId);
  return (
    <div className={styles.content}>
      <h2 className={styles.title}>Approve the roadmap (iteration {plan.iteration})</h2>
      <p className={styles.body}>
        Enrichment is done: every goal below carries executable tasks with bound
        agents. Approving starts autonomous execution.
      </p>
      <RoadmapDoc plan={plan} />
      <ConfirmAction
        label="Approve & start execution"
        consequence="Workers begin executing the tasks above, goal by goal."
        pending={approve.isPending}
        onConfirm={() => approve.mutate(undefined, { onSuccess: onDone })}
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
