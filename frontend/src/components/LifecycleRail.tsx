import React from 'react';
import { NavLink, useParams } from 'react-router-dom';
import {
  Activity,
  AlertTriangle,
  ChevronRight,
  Cpu,
  LayoutDashboard,
  Pause,
  Pencil,
  Play,
  RefreshCw,
  RotateCcw,
  Target,
} from 'lucide-react';
import {
  usePausePlan,
  usePlan,
  useReplanMidRunning,
  useResumePlan,
  useRetryPlanningStage,
  useRetryTask,
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
  const selectTask = usePlannerStore((state) => state.selectTask);
  const pausePlan = usePausePlan(planId);
  const resumePlan = useResumePlan(planId);
  const retryTask = useRetryTask(planId);
  const retryPlanningStage = useRetryPlanningStage(planId);
  const replan = useReplanMidRunning(planId);
  const startIntent = useStartIntent(planId, 'initial');
  const startReplan = useStartIntent(planId, 'replan');
  const base = `/plans/${encodeURIComponent(planId)}`;
  const [replanOpen, setReplanOpen] = React.useState(false);
  const [replanObjective, setReplanObjective] = React.useState("");

  const legal = new Set(plan?.legal_actions ?? []);
  const reason = plan?.status_reason.message;
  const gate = plan?.pending_gate;
  const block = plan?.block;
  const failedTasks = plan?.goals.flatMap((goal) =>
    goal.tasks
      .filter((task) => task.status === "failed")
      .map((task) => ({ goal, task })),
  ) ?? [];
  const blockCanRetry = !!block && (
    block.legal_resolutions.includes("retry_stage")
    || block.legal_resolutions.includes("wait_and_retry")
  );
  const retryBlockedWork = () => {
    if (block?.goal_id && block.task_id) {
      retryTask.mutate({ goalId: block.goal_id, taskId: block.task_id });
    } else {
      retryPlanningStage.mutate();
    }
  };

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
                Pause requested; current attempt is still running. No new work will start.
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
              {blockCanRetry && (
                <button
                  className={styles.gateBtn}
                  onClick={retryBlockedWork}
                  disabled={retryTask.isPending || retryPlanningStage.isPending}
                >
                  <RotateCcw size={12} aria-hidden />
                  {block?.kind === "agent_capability"
                    ? "Retry agent binding"
                    : block?.legal_resolutions.includes("wait_and_retry")
                      ? "Clear capacity gate & retry"
                      : "Retry blocked work"}
                </button>
              )}
              {block?.kind === "agent_capability" && (
                <NavLink className={styles.secondaryBtn} to="/settings/agents">
                  <Cpu size={12} aria-hidden /> Repair agent registry
                </NavLink>
              )}
              {block?.legal_resolutions.includes("edit_task") && block.task_id && (
                <button
                  className={styles.secondaryBtn}
                  onClick={() => selectTask(block.task_id)}
                >
                  <Pencil size={12} aria-hidden /> Edit failed task
                </button>
              )}
            </div>
          )}

          {plan.paused && failedTasks.length > 0 && (
            <div className={styles.failedCard} role="status">
              <div className={styles.failedTitle}>
                <AlertTriangle size={13} aria-hidden /> Failed work
              </div>
              <p className={styles.cardBody}>
                Retry selected tasks first, then Resume to release the manual pause.
              </p>
              {failedTasks.map(({ goal, task }) => (
                <button
                  key={task.id}
                  className={styles.gateBtn}
                  onClick={() => retryTask.mutate({ goalId: goal.id, taskId: task.id })}
                  disabled={retryTask.isPending}
                >
                  <RotateCcw size={12} aria-hidden /> Retry {task.name}
                </button>
              ))}
            </div>
          )}

          {plan.status === "waiting" && gate && (
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
              onClick={() => startIntent.mutate(undefined)}
              disabled={startIntent.isPending}
            >
              <Play size={12} aria-hidden /> Start next cycle
            </button>
          )}

          {legal.has("start_replan") && !replanOpen && (
            <button
              className={styles.secondaryBtn}
              onClick={() => plan.active_cycle ? setReplanOpen(true) : replan.mutate()}
              disabled={replan.isPending}
            >
              <RefreshCw size={12} aria-hidden /> Propose replan
            </button>
          )}

          {replanOpen && plan.active_cycle && (
            <div className={styles.replanComposer}>
              <div className={styles.gateTitle}>Propose the next cycle</div>
              <p className={styles.cardBody}>
                Completed work stays locked as history. Describe only what should
                change, be retried, or be added.
              </p>
              <textarea
                className={styles.replanInput}
                value={replanObjective}
                onChange={(event) => setReplanObjective(event.target.value)}
                placeholder="Example: retry the failed migration task, keep the completed API work, and add rollback verification."
                rows={5}
                autoFocus
              />
              <button
                className={styles.gateBtn}
                disabled={!replanObjective.trim() || startReplan.isPending}
                onClick={() => startReplan.mutate(
                  {
                    objective: replanObjective.trim(),
                    scope: [],
                    constraints: ["Preserve completed source-cycle work"],
                    exclusions: ["Redoing completed tasks"],
                    kind: "replan",
                  },
                  { onSuccess: () => setReplanOpen(false) },
                )}
              >
                <RefreshCw size={12} aria-hidden /> Review proposal
              </button>
              <button
                className={styles.secondaryBtn}
                onClick={() => setReplanOpen(false)}
                disabled={startReplan.isPending}
              >
                Cancel
              </button>
            </div>
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
