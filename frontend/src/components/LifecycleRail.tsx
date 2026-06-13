import React from 'react';
import { NavLink } from 'react-router-dom';
import {
  Activity, Check, ChevronRight, Compass, LayoutDashboard, Play, Target,
} from 'lucide-react';
import { usePlan, useStartDiscovery, useGoals } from '../lib/queries';
import { usePlannerStore } from '../store/plannerStore';
import { relTime, useNow } from '../lib/time';
import styles from './LifecycleRail.module.css';

type StepState = 'done' | 'active' | 'pending';

interface Step {
  key: string;
  label: string;
  sub?: string;
  state: StepState;
}

/**
 * The plan lifecycle is the top-level mental model, so it is the permanent
 * left rail: discovery → architecture → phases → done, rendered with the
 * status system (green above the cursor, blue/amber at it, gray below).
 * Under the cursor sits either the GATE CARD (amber — the only route into
 * approvals) or the live session card (blue — streamed progress, never a
 * bare spinner). The operator's standing question — "what is the system
 * doing, and what does it need from me?" — is answered here, always.
 */
export function LifecycleRail() {
  const { data: plan, isLoading } = usePlan();
  const { data: goals = [] } = useGoals();
  const setGateOpen = usePlannerStore((s) => s.setGateOpen);
  const decisions = usePlannerStore((s) => s.decisions);
  const events = usePlannerStore((s) => s.events);
  const isThinking = usePlannerStore((s) => s.ui.isThinking);
  const startDiscovery = useStartDiscovery();
  const now = useNow(1000);

  const status = plan?.status;

  // ── Build the step list ───────────────────────────────────────────────────
  const steps: Step[] = [];
  if (plan) {
    const past = (s: string) => {
      const order = ['discovery', 'architecture', 'phase_active', 'phase_review', 'done'];
      return order.indexOf(plan.status) > order.indexOf(s);
    };
    steps.push({
      key: 'discovery', label: 'Discovery',
      state: status === 'discovery' ? 'active' : 'done',
    });
    steps.push({
      key: 'architecture', label: 'Architecture',
      state: status === 'architecture' ? 'active' : past('architecture') ? 'done' : 'pending',
    });
    for (const p of plan.phases) {
      steps.push({
        key: `phase-${p.index}`,
        label: `Phase ${p.index} — ${p.name}`,
        sub: p.goal_names.length ? `${p.goal_names.length} goals` : undefined,
        state: p.status === 'completed' ? 'done' : p.status === 'active' ? 'active' : 'pending',
      });
    }
    steps.push({ key: 'done', label: 'Done', state: status === 'done' ? 'done' : 'pending' });
  }

  // ── What goes in the cursor slot: gate, CTA, or live session ─────────────
  const briefReady = status === 'discovery' && plan?.brief != null;
  const gate =
    briefReady
      ? { title: 'Brief ready for approval', body: 'Review the project brief and approve it to start architecture drafting.' }
      : status === 'architecture'
        ? {
            title: 'Architecture approval',
            body: decisions.length > 0
              ? `${decisions.length} decision${decisions.length === 1 ? '' : 's'} proposed. Review and approve to dispatch the first phase.`
              : 'Approve the drafted architecture to dispatch the first phase to workers.',
          }
        : status === 'phase_review'
          ? { title: 'Phase review', body: 'The phase has completed. Approve the next phase or mark the project done.' }
          : null;

  const gateGoals = goals.filter(
    (g) => g.status === 'ready_for_review' || g.status === 'awaiting_pr_approval',
  );

  const lastProgress = [...events].reverse().find((e) => e.type === 'plan.jit_progress');

  return (
    <nav className={styles.rail} aria-label="Plan lifecycle and navigation">
      <div className={styles.nav}>
        <RailLink to="/" icon={<LayoutDashboard size={14} aria-hidden />} label="Overview" end />
        <RailLink
          to="/goals"
          icon={<Target size={14} aria-hidden />}
          label="Goals"
          badge={gateGoals.length > 0 ? gateGoals.length : undefined}
        />
        <RailLink to="/activity" icon={<Activity size={14} aria-hidden />} label="Activity" />
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
              {s.sub && <span className={styles.stepSub}>{s.sub}</span>}
            </span>
            <span className={styles.visuallyHidden}>
              {s.state === 'done' ? ' (completed)' : s.state === 'active' ? ' (current)' : ' (upcoming)'}
            </span>
          </li>
        ))}
      </ol>

      <div className={styles.cursorSlot}>
        {/* Pre-discovery empty state: the way in, not a blank void */}
        {status === 'discovery' && !briefReady && !isThinking && (
          <div className={styles.sessionCard}>
            <div className={styles.cardTitle}>No brief yet</div>
            <p className={styles.cardBody}>
              Start a discovery session — the planner interviews you and drafts the project brief.
            </p>
            <button
              className={styles.primaryBtn}
              onClick={() => startDiscovery.mutate()}
              disabled={startDiscovery.isPending}
            >
              Start discovery
            </button>
          </div>
        )}

        {/* Live session: streamed progress, never a bare spinner */}
        {(isThinking || (status === 'architecture' && !gateGoals.length && lastProgress)) && (
          <div className={styles.sessionCard} aria-live="polite">
            <div className={styles.cardTitle}>
              <span className={`${styles.runDot} breathe`} aria-hidden />
              Planner working
            </div>
            {lastProgress && (
              <pre className={styles.progressLine}>
                {summarizeProgress(lastProgress.payload)}
              </pre>
            )}
            <div className={styles.cardMeta}>
              last activity {relTime(lastProgress?.at ?? null, now)}
            </div>
          </div>
        )}

        {/* The gate card — anything amber is your queue */}
        {gate && (
          <div className={styles.gateCard}>
            <div className={styles.gateTitle}>{gate.title}</div>
            <p className={styles.cardBody}>{gate.body}</p>
            <button className={styles.gateBtn} onClick={() => setGateOpen(true)}>
              Review &amp; approve <ChevronRight size={13} aria-hidden />
            </button>
          </div>
        )}
      </div>
    </nav>
  );
}

function summarizeProgress(payload: Record<string, unknown>): string {
  const text =
    (payload.message as string) ??
    (payload.step as string) ??
    JSON.stringify(payload);
  return text.length > 120 ? text.slice(0, 117) + '…' : text;
}

function RailLink({
  to, icon, label, badge, end,
}: {
  to: string; icon: React.ReactNode; label: string; badge?: number; end?: boolean;
}) {
  return (
    <NavLink
      to={to}
      end={end}
      className={({ isActive }) => `${styles.navLink} ${isActive ? styles.navActive : ''}`}
    >
      {icon}
      <span>{label}</span>
      {badge !== undefined && (
        <span className={styles.navBadge} title={`${badge} item${badge === 1 ? '' : 's'} waiting on you`}>
          {badge}
        </span>
      )}
    </NavLink>
  );
}
