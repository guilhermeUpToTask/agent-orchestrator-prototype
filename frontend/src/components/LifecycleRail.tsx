import React from 'react';
import { NavLink, useParams } from 'react-router-dom';
import {
  Activity,
  AlertTriangle,
  ChevronRight,
  Cpu,
  LayoutDashboard,
  Pause,
  Play,
  RefreshCw,
  Target,
} from 'lucide-react';
import {
  usePausePlan,
  usePlan,
  useReplanMidRunning,
  useResumePlan,
  useStartIntent,
} from '../lib/queries';
import { usePlannerStore } from '../store/plannerStore';
import { StatusBadge } from './StatusBadge';
import styles from './LifecycleRail.module.css';

/**
 * Root lifecycle controls. Status, reason, activity, and legal actions all come
 * from the backend; this component does not reproduce transition rules.
 */
export function LifecycleRail() {
  const { planId = '' } = useParams();
  const { data: plan, isLoading } = usePlan(planId || null);
  const setGateOpen = usePlannerStore((state) => state.setGateOpen);
  const pausePlan = usePausePlan(planId);
  const resumePlan = useResumePlan(planId);
  const replan = useReplanMidRunning(planId);
  const startIntent = useStartIntent(planId, 'initial');
  const startReplan = useStartIntent(planId, 'replan');
  const base = `/plans/${encodeURIComponent(planId)}`;

  const legal = new Set(plan?.legal_actions ?? []);
  const reason = plan?.status_reason.message;
  const gate = plan?.pending_gate;
  const block = plan?.block;

  return (
    <nav className={styles.rail} aria-label="Project plan lifecycle and navigation">
      <div className={styles.nav}>
        <RailLink to={base} icon={<LayoutDashboard size={14} aria-hidden />} label="Overview" end />
        <RailLink to={`${base}/goals`} icon={<Target size={14} aria-hidden />} label="Goals" />
        <RailLink to={`${base}/agents`} icon={<Cpu size={14} aria-hidden />} label="Agents" />
        <RailLink to={`${base}/activity`} icon={<Activity size={14} aria-hidden />} label="Activity" />
      </div>

      <div className={styles.sectionLabel + ' label'}>Project plan</div>

      {isLoading && (
        <div className="skeleton" style={{ height: 76, margin: '8px 12px' }} />
      )}

      {plan && (
        <div className={styles.cursorSlot}>
          <div className={styles.sessionCard} aria-live="polite">
            <div className={styles.cardTitle}>
              <StatusBadge domain="plan" value={plan.status} />
            </div>
            <p className={styles.cardBody}>
              {reason ?? humanize(plan.activity)}
            </p>
            <p className={styles.cardBody}>
              Activity: <strong>{humanize(plan.activity)}</strong>
              {plan.tdd_stage && <> - TDD: <strong>{humanize(plan.tdd_stage)}</strong></>}
            </p>
          </div>

          {plan.pause_requested && (
            <div className={styles.pausedCard} aria-live="polite">
              <div className={styles.gateTitle}>Pause requested</div>
              <p className={styles.cardBody}>
                No new work can start. The current atomic action may finalize;
                the plan will then become fully paused.
              </p>
              {plan.active_run && (
                <p className={styles.cardBody}>
                  Run {plan.active_run.run_id} - attempt {plan.active_run.attempt_number}
                </p>
              )}
            </div>
          )}

          {plan.status === 'blocked' && (
            <div className={styles.failedCard} role="alert">
              <div className={styles.failedTitle}>
                <AlertTriangle size={13} aria-hidden /> Blocked
              </div>
              <p className={styles.cardBody}>
                {reason ?? 'A permanent or exhausted failure requires an explicit resolution.'}
              </p>
              {block && Array.isArray(block.legal_resolutions) && (
                <p className={styles.cardBody}>
                  Legal resolutions: {block.legal_resolutions.join(', ')}
                </p>
              )}
            </div>
          )}

          {plan.status === 'waiting' && gate && (
            <div className={styles.gateCard}>
              <div className={styles.gateTitle}>Review required</div>
              <p className={styles.cardBody}>
                {typeof gate.continuation === 'string'
                  ? gate.continuation
                  : 'Review the version-bound artifact and choose a legal decision.'}
              </p>
              <button className={styles.gateBtn} onClick={() => setGateOpen(true)}>
                Review &amp; decide <ChevronRight size={13} aria-hidden />
              </button>
            </div>
          )}

          {plan.status === 'idle' && (
            <div className={styles.sessionCard}>
              <div className={styles.cardTitle}>Ready for another cycle</div>
              <p className={styles.cardBody}>
                No cycle or planning proposal is active. Completed cycle history remains available.
              </p>
            </div>
          )}

          {legal.has('pause') && !plan.pause_requested && (
            <button
              className={styles.secondaryBtn}
              onClick={() => pausePlan.mutate(undefined)}
              disabled={pausePlan.isPending}
            >
              <Pause size={12} aria-hidden /> Pause at boundary
            </button>
          )}

          {legal.has('resume') && (
            <button
              className={styles.gateBtn}
              onClick={() => resumePlan.mutate()}
              disabled={resumePlan.isPending}
            >
              <Play size={12} aria-hidden /> Resume
            </button>
          )}

          {legal.has('start_intent') && (
            <button
              className={styles.gateBtn}
              onClick={() => startIntent.mutate()}
              disabled={startIntent.isPending}
            >
              <Play size={12} aria-hidden /> Start next cycle
            </button>
          )}

          {legal.has('start_replan') && (
            <button
              className={styles.secondaryBtn}
              onClick={() => plan.active_cycle ? startReplan.mutate() : replan.mutate()}
              disabled={plan.active_cycle ? startReplan.isPending : replan.isPending}
            >
              <RefreshCw size={12} aria-hidden /> Start replan
            </button>
          )}
        </div>
      )}
    </nav>
  );
}

function humanize(value: string): string {
  return value.replace(/:/g, ' - ').replace(/_/g, ' ');
}

function RailLink({
  to,
  icon,
  label,
  end,
}: {
  to: string;
  icon: React.ReactNode;
  label: string;
  end?: boolean;
}) {
  return (
    <NavLink
      to={to}
      end={end}
      className={({ isActive }) =>
        `${styles.navLink} ${isActive ? styles.navActive : ''}`
      }
    >
      {icon}
      <span>{label}</span>
    </NavLink>
  );
}
