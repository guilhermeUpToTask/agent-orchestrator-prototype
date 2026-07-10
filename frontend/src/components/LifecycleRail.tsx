import React from 'react';
import { NavLink, useParams } from 'react-router-dom';
import {
  Activity, AlertTriangle, Check, ChevronRight, Cpu, LayoutDashboard, Pause,
  Play, RefreshCw, Target,
} from 'lucide-react';
import {
  usePausePlan, usePlan, useReplanMidRunning, useResumePlan,
} from '../lib/queries';
import { usePlannerStore } from '../store/plannerStore';
import { PLAN_PHASE } from '../styles/tokens';
import type { PlanPhase } from '../types/ui';
import styles from './LifecycleRail.module.css';

type StepState = 'done' | 'active' | 'pending';

/** The happy-path walk shown as the stepper. */
const WALK: PlanPhase[] = [
  'discovery',
  'architecture',
  'enriching',
  'awaiting_review',
  'running',
  'review',
  'done',
];

/**
 * The plan lifecycle is the top-level mental model, so it is the permanent
 * left rail: the 9-phase walk with the cursor on the current phase. Under
 * the cursor sits the GATE CARD when a human gate is waiting (amber — the
 * only route into approvals) or the replan affordance mid-RUNNING. The
 * operator's standing question — "what is the system doing, and what does
 * it need from me?" — is answered here, always.
 */
export function LifecycleRail() {
  const { planId = '' } = useParams();
  const { data: plan, isLoading } = usePlan(planId || null);
  const setGateOpen = usePlannerStore((s) => s.setGateOpen);
  const replanMidRunning = useReplanMidRunning(planId);
  const pausePlan = usePausePlan(planId);
  const resumePlan = useResumePlan(planId);

  const phase = plan?.phase;
  const paused = plan?.paused ?? false;
  // pause is a claim gate, only meaningful in the worker-driven phases
  const pausable =
    phase === 'running' || phase === 'architecture' || phase === 'enriching';
  const cursor = phase
    ? WALK.indexOf(phase === 'replanning' ? 'architecture' : phase)
    : -1;

  const steps = WALK.map((p, i): { key: string; label: string; state: StepState } => ({
    key: p,
    label: PLAN_PHASE[p].label,
    state:
      cursor < 0 ? 'pending'
      : i < cursor ? 'done'
      : i === cursor ? (phase === 'done' ? 'done' : 'active')
      : 'pending',
  }));

  const gate =
    phase === 'awaiting_review'
      ? {
          title: 'Roadmap ready for approval',
          body: 'Enrichment finished and agents are bound. Review the tasks and approve to start execution.',
        }
      : phase === 'review'
        ? {
            title: 'Execution finished — review',
            body: 'Every goal is settled. Finish the plan, or replan the next iteration on top of these results.',
          }
        : null;

  const chatting = phase === 'discovery' || phase === 'replanning';
  const base = `/plans/${encodeURIComponent(planId)}`;

  return (
    <nav className={styles.rail} aria-label="Plan lifecycle and navigation">
      <div className={styles.nav}>
        <RailLink to={base} icon={<LayoutDashboard size={14} aria-hidden />} label="Overview" end />
        <RailLink to={`${base}/goals`} icon={<Target size={14} aria-hidden />} label="Goals" />
        <RailLink to={`${base}/agents`} icon={<Cpu size={14} aria-hidden />} label="Agents" />
        <RailLink to={`${base}/activity`} icon={<Activity size={14} aria-hidden />} label="Activity" />
      </div>

      <div className={styles.sectionLabel + ' label'}>Lifecycle</div>

      {isLoading && (
        <div className={styles.steps}>
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="skeleton" style={{ height: 28, margin: '4px 12px' }} />
          ))}
        </div>
      )}

      <ol className={styles.steps}>
        {steps.map((s) => (
          <li key={s.key} className={`${styles.step} ${styles[s.state]}`}>
            <span className={styles.stepMark} aria-hidden>
              {s.state === 'done' ? <Check size={11} /> : s.state === 'active' ? <Play size={10} /> : null}
            </span>
            <span className={styles.stepLabel}>
              {s.label}
              {s.key === 'architecture' && phase === 'replanning' && (
                <span className={styles.stepSub}>replanning (chat)</span>
              )}
            </span>
            <span className={styles.visuallyHidden}>
              {s.state === 'done' ? ' (completed)' : s.state === 'active' ? ' (current)' : ' (upcoming)'}
            </span>
          </li>
        ))}
      </ol>

      <div className={styles.cursorSlot}>
        {chatting && (
          <div className={styles.sessionCard}>
            <div className={styles.cardTitle}>
              {phase === 'discovery' ? 'Discovery conversation' : 'Replanning conversation'}
            </div>
            <p className={styles.cardBody}>
              {phase === 'discovery'
                ? 'Agree the goal roadmap with the reasoner in the chat panel. The plan advances when it commits the goals.'
                : 'Plan the next iteration in the chat panel. Completed goals stay as history.'}
            </p>
          </div>
        )}

        {(phase === 'architecture' || phase === 'enriching') && (
          <div className={styles.sessionCard} aria-live="polite">
            <div className={styles.cardTitle}>
              <span className={`${styles.runDot} breathe`} aria-hidden />
              {phase === 'architecture' ? 'Structuring…' : 'Breaking goals into tasks…'}
            </div>
            <p className={styles.cardBody}>
              The worker is driving this phase autonomously. It pauses at the
              review gate when the roadmap is executable.
            </p>
          </div>
        )}

        {phase === 'running' && (
          <div className={styles.sessionCard}>
            <div className={styles.cardTitle}>
              <span className={`${styles.runDot} breathe`} aria-hidden />
              Executing
            </div>
            <p className={styles.cardBody}>
              Agents are working the roadmap. You can request a replan — pending
              work is skipped and the chat re-opens.
            </p>
            <button
              className={styles.primaryBtn}
              onClick={() => replanMidRunning.mutate()}
              disabled={replanMidRunning.isPending}
            >
              <RefreshCw size={12} aria-hidden /> Replan now
            </button>
          </div>
        )}

        {/* Pause is the standing intervention in the worker-driven phases. */}
        {pausable && !paused && (
          <button
            className={styles.secondaryBtn}
            onClick={() => pausePlan.mutate(undefined)}
            disabled={pausePlan.isPending}
          >
            <Pause size={12} aria-hidden /> Pause plan
          </button>
        )}

        {/* The pause gate — any phase. Amber, with Resume (= the manual retry). */}
        {paused && (
          <div className={styles.pausedCard} aria-live="polite">
            <div className={styles.gateTitle}>Plan paused</div>
            <p className={styles.cardBody}>
              {plan?.paused_reason ??
                'The worker is holding. Goals and tasks are editable while paused.'}
            </p>
            <button
              className={styles.gateBtn}
              onClick={() => resumePlan.mutate()}
              disabled={resumePlan.isPending}
            >
              <Play size={12} aria-hidden /> Resume &amp; retry failed work
            </button>
          </div>
        )}

        {phase === 'failed' && (
          <div className={styles.failedCard}>
            <div className={styles.failedTitle}>
              <AlertTriangle size={13} aria-hidden /> Plan failed
            </div>
            <p className={styles.cardBody}>
              Planning failed permanently (the reasoner was unavailable or its
              retry budget ran out). Create a new plan to try again.
            </p>
          </div>
        )}

        {gate && (
          <div className={styles.gateCard}>
            <div className={styles.gateTitle}>{gate.title}</div>
            <p className={styles.cardBody}>{gate.body}</p>
            <button className={styles.gateBtn} onClick={() => setGateOpen(true)}>
              Review &amp; decide <ChevronRight size={13} aria-hidden />
            </button>
          </div>
        )}
      </div>
    </nav>
  );
}

function RailLink({
  to, icon, label, end,
}: {
  to: string; icon: React.ReactNode; label: string; end?: boolean;
}) {
  return (
    <NavLink
      to={to}
      end={end}
      className={({ isActive }) => `${styles.navLink} ${isActive ? styles.navActive : ''}`}
    >
      {icon}
      <span>{label}</span>
    </NavLink>
  );
}
