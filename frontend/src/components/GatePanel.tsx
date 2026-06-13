import React, { useEffect, useRef, useState } from 'react';
import { X } from 'lucide-react';
import { usePlannerStore } from '../store/plannerStore';
import {
  useApproveArchitecture, useApproveBrief, useApprovePhase, usePlan,
  useStartDiscovery,
} from '../lib/queries';
import { toast } from '../lib/toast';
import { relTime, useNow } from '../lib/time';
import styles from './GatePanel.module.css';

/**
 * Approvals are the highest-stakes interactions in the product, so they
 * get a dedicated surface, not a toolbar button:
 *  - the operator SEES what they are approving (brief / decisions / phase),
 *  - every action states its consequence,
 *  - firing requires two explicit steps (no accidental clicks),
 *  - "Mark project done" is visually demoted below "Approve next phase".
 */
export function GatePanel() {
  const gateOpen = usePlannerStore((s) => s.ui.gateOpen);
  const setGateOpen = usePlannerStore((s) => s.setGateOpen);
  const { data: plan } = usePlan();
  const panelRef = useRef<HTMLDivElement>(null);

  // Esc closes; focus moves into the dialog on open.
  useEffect(() => {
    if (!gateOpen) return;
    panelRef.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setGateOpen(false); };
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

        {plan.status === 'discovery' && <BriefGate onDone={close} />}
        {plan.status === 'architecture' && <ArchitectureGate onDone={close} />}
        {plan.status === 'phase_review' && <PhaseGate onDone={close} />}
        {!['discovery', 'architecture', 'phase_review'].includes(plan.status) && (
          <p className={styles.body}>
            Nothing is waiting on you — the plan is in “{plan.status}”.
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
  /** Quieter styling for the less-likely choice (e.g. "Mark project done") */
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

/* ── Brief gate: read the brief, then approve it ─────────────────────────── */

function BriefGate({ onDone }: { onDone: () => void }) {
  const { data: plan } = usePlan();
  const approve = useApproveBrief();
  const restart = useStartDiscovery();
  const brief = plan?.brief;

  return (
    <div className={styles.content}>
      <h2 className={styles.title}>Approve project brief</h2>

      {brief ? (
        <div className={styles.doc}>
          <Doc label="Vision">{brief.vision}</Doc>
          {brief.constraints?.length > 0 && (
            <Doc label="Constraints">
              <ul className={styles.docList}>
                {brief.constraints.map((c, i) => <li key={i}>{c}</li>)}
              </ul>
            </Doc>
          )}
          <Doc label="Phase 1 exit criteria">{brief.phase_1_exit_criteria}</Doc>
          {brief.open_questions?.length > 0 && (
            <Doc label="Open questions">
              <ul className={styles.docList}>
                {brief.open_questions.map((q, i) => <li key={i}>{q}</li>)}
              </ul>
            </Doc>
          )}
        </div>
      ) : (
        <p className={styles.body}>
          No brief has been drafted yet. Finish the discovery session first.
        </p>
      )}

      {brief && (
        <>
          <ConfirmAction
            label="Approve brief"
            consequence="Locks the brief and starts architecture drafting."
            pending={approve.isPending}
            onConfirm={() => approve.mutate(undefined, { onSuccess: onDone })}
          />
          <ConfirmAction
            label="Discard & restart discovery"
            consequence="Throws away this brief and starts a fresh discovery interview. The current brief is replaced once the new one is drafted."
            pending={restart.isPending}
            demoted
            onConfirm={() => {
              restart.mutate(undefined, {
                onSuccess: () => {
                  toast.info(
                    'Discovery restarted',
                    'Answer the planner’s questions in the chat to draft a new brief.',
                  );
                  onDone();
                },
              });
            }}
          />
        </>
      )}
    </div>
  );
}

/* ── Architecture gate: see the decisions you're applying ────────────────── */

function ArchitectureGate({ onDone }: { onDone: () => void }) {
  const { data: plan } = usePlan();
  const decisions = usePlannerStore((s) => s.decisions);
  const approve = useApproveArchitecture();
  const now = useNow(5000);

  // Default: every proposed decision selected. Unchecking excludes it.
  const [selected, setSelected] = useState<Set<string>>(() => new Set(decisions.map((d) => d.id)));
  useEffect(() => {
    setSelected(new Set(decisions.map((d) => d.id)));
  }, [decisions]);

  const toggle = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });

  const all = decisions.length > 0 && selected.size === decisions.length;
  const ids = all ? [] : [...selected]; // backend treats [] as "approve all"

  return (
    <div className={styles.content}>
      <h2 className={styles.title}>Approve architecture</h2>

      {plan?.architecture_summary && (
        <div className={styles.doc}>
          <Doc label="Architecture summary">{plan.architecture_summary}</Doc>
        </div>
      )}

      {decisions.length > 0 ? (
        <fieldset className={styles.decisions}>
          <legend className="label">Proposed decisions — uncheck to exclude</legend>
          {decisions.map((d) => (
            <label key={d.id} className={styles.decisionRow}>
              <input
                type="checkbox"
                checked={selected.has(d.id)}
                onChange={() => toggle(d.id)}
              />
              <span className={styles.decisionDomain}>[{d.domain}]</span>
              <span className={styles.decisionId}>{d.id}</span>
              <span className={styles.decisionTime}>{relTime(d.at, now)}</span>
            </label>
          ))}
        </fieldset>
      ) : (
        <p className={styles.body}>
          No decision proposals were captured in this session (the page may have
          loaded after they streamed). Approving applies <strong>all</strong> proposed
          decisions on the backend.
        </p>
      )}

      <ConfirmAction
        label={all || decisions.length === 0
          ? 'Approve architecture'
          : `Approve ${selected.size} of ${decisions.length} decisions`}
        consequence="Applies the selected decisions and dispatches the first phase's goals to workers."
        pending={approve.isPending}
        onConfirm={() => approve.mutate(ids, { onSuccess: onDone })}
      />
    </div>
  );
}

/* ── Phase review gate: continue vs finish, clearly separated ────────────── */

function PhaseGate({ onDone }: { onDone: () => void }) {
  const { data: plan } = usePlan();
  const approve = useApprovePhase();

  const current = plan?.phases.find((p) => p.index === plan.current_phase_index);
  const next = plan?.phases.find((p) => p.index === (plan.current_phase_index ?? 0) + 1);

  return (
    <div className={styles.content}>
      <h2 className={styles.title}>Phase review</h2>

      {current && (
        <div className={styles.doc}>
          <Doc label={`Completed — Phase ${current.index}: ${current.name}`}>
            {current.goal}
          </Doc>
          {current.exit_criteria && <Doc label="Exit criteria">{current.exit_criteria}</Doc>}
          {current.lessons && <Doc label="Lessons">{current.lessons}</Doc>}
        </div>
      )}

      <ConfirmAction
        label={next ? `Approve next phase (Phase ${next.index}: ${next.name})` : 'Approve next phase'}
        consequence={next
          ? `Dispatches ${next.goal_names.length || 'the next phase\u2019s'} goals to workers.`
          : 'Releases the next phase to workers.'}
        pending={approve.isPending}
        onConfirm={() => approve.mutate(true, { onSuccess: onDone })}
      />
      <ConfirmAction
        label="Mark project done"
        consequence="Ends the project here — no further phases will run."
        pending={approve.isPending}
        demoted
        onConfirm={() => approve.mutate(false, { onSuccess: onDone })}
      />
    </div>
  );
}

function Doc({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <section className={styles.docSection}>
      <div className="label">{label}</div>
      <div className={styles.docBody}>{children}</div>
    </section>
  );
}
