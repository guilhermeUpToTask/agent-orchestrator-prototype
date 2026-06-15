import React from 'react';
import { Link } from 'react-router-dom';
import { ChevronRight, ExternalLink } from 'lucide-react';
import { useAgents, useGoals, usePlan } from '../lib/queries';
import { usePlannerStore } from '../store/plannerStore';
import { StatusBadge } from '../components/StatusBadge';
import { relTime, useNow } from '../lib/time';
import type { GoalAggregate, TaskSummary } from '../types/ui';
import styles from './Overview.module.css';

/**
 * The operator's home: answers "what is happening, and what do I owe?"
 *  - current phase + exit criteria (what "done" means right now)
 *  - Needs attention: every amber item, each row a deep link
 *  - Running now: what the machine is executing this second
 *  - the brief & architecture documents (previously rendered nowhere)
 */
export function Overview() {
  const { data: plan, isLoading: planLoading, error: planError, refetch } = usePlan();
  const { data: goals = [], isLoading: goalsLoading } = useGoals();
  const { data: agents = [] } = useAgents();
  const setGateOpen = usePlannerStore((s) => s.setGateOpen);
  const events = usePlannerStore((s) => s.events);
  const completedRuns = usePlannerStore((s) => s.completedRuns);
  const now = useNow(1000);

  // ── Error state: direction, not mood ─────────────────────────────────────
  if (planError) {
    return (
      <div className={styles.page}>
        <div className={styles.errorCard} role="alert">
          <div className={styles.errorTitle}>Can't reach the backend</div>
          <p className={styles.errorBody}>
            {planError.message}. Check that the API server is running at{' '}
            <code>{import.meta.env.VITE_API_URL ?? 'http://localhost:8000'}</code>, then retry.
          </p>
          <button className={styles.retryBtn} onClick={() => refetch()}>Retry</button>
        </div>
      </div>
    );
  }

  // ── Loading: skeletons, never spinners ───────────────────────────────────
  if (planLoading || goalsLoading) {
    return (
      <div className={styles.page} aria-busy="true" aria-label="Loading plan overview">
        <div className="skeleton" style={{ height: 84 }} />
        <div className="skeleton" style={{ height: 160 }} />
        <div className="skeleton" style={{ height: 160 }} />
      </div>
    );
  }

  if (!plan) return null;

  const currentPhase = plan.phases.find((p) => p.index === plan.current_phase_index);
  const agentName = (id: string | null | undefined) =>
    agents.find((a) => a.agent_id === id)?.name ?? 'unassigned';

  // Amber queue: plan gates + goals waiting on review + failed tasks.
  // Architecture/phase-review approvals are only "waiting on you" once the
  // autonomous run has COMPLETED — offering them while the planner is still
  // drafting let the operator approve into a 409 dangle. Completion is the
  // unlock (completedRuns, kept durable by the status-sync poll).
  const planGate =
    plan.status === 'phase_review' && completedRuns.includes('phase_review')
      ? 'Phase review — approve the next phase or finish the project'
    : plan.status === 'architecture' && completedRuns.includes('architecture')
      ? 'Architecture approval — review decisions and dispatch the first phase'
    : plan.status === 'discovery' && plan.brief
      ? 'Project brief — review and approve to start architecture'
    : null;

  const gateGoals = goals.filter(
    (g) => g.status === 'ready_for_review' || g.status === 'awaiting_pr_approval',
  );
  const failedTasks = flatTasks(goals).filter((t) => t.task.status === 'failed');
  const runningTasks = flatTasks(goals).filter(
    (t) => t.task.status === 'in_progress' || t.task.status === 'assigned',
  );

  const taskEventAt = (taskId: string) =>
    [...events].reverse().find(
      (e) => e.type === 'task.status_changed' && e.payload.task_id === taskId,
    )?.at ?? null;

  const attentionCount = (planGate ? 1 : 0) + gateGoals.length + failedTasks.length;

  return (
    <div className={styles.page}>
      {/* ── Current phase header ─────────────────────────────────────────── */}
      <header className={styles.phaseHeader}>
        <div className={styles.phaseTitleRow}>
          <h1 className={styles.phaseTitle}>
            {currentPhase
              ? `Phase ${currentPhase.index} — ${currentPhase.name}`
              : plan.status === 'discovery' ? 'Discovery' : 'Plan'}
          </h1>
          <StatusBadge domain="plan" value={plan.status} />
        </div>
        {currentPhase?.goal && <p className={styles.phaseGoal}>{currentPhase.goal}</p>}
        {currentPhase?.exit_criteria && (
          <div className={styles.exit}>
            <span className="label">Exit criteria</span>
            <span className={styles.exitText}>{currentPhase.exit_criteria}</span>
          </div>
        )}
      </header>

      {/* ── Needs attention ──────────────────────────────────────────────── */}
      <section className={styles.section} aria-label="Needs attention">
        <h2 className={styles.sectionTitle + ' label'}>
          Needs attention {attentionCount > 0 && <span className={styles.gateCount}>{attentionCount}</span>}
        </h2>

        {attentionCount === 0 ? (
          <p className={styles.empty}>Nothing is waiting on you. The machine has the conn.</p>
        ) : (
          <ul className={styles.rows}>
            {planGate && (
              <li>
                <button className={styles.row} onClick={() => setGateOpen(true)}>
                  <StatusBadge domain="plan" value={plan.status} bare />
                  <span className={styles.rowTitle}>{planGate}</span>
                  <ChevronRight size={14} className={styles.rowChev} aria-hidden />
                </button>
              </li>
            )}
            {gateGoals.map((g) => (
              <li key={g.goal_id}>
                <Link className={styles.row} to={`/goals/${g.goal_id}`}>
                  <StatusBadge domain="goal" value={g.status} bare />
                  <span className={styles.rowTitle}>{g.name}</span>
                  {g.pr_number != null && (
                    <a
                      className={styles.prLink}
                      href={g.pr_html_url ?? '#'}
                      target="_blank"
                      rel="noreferrer"
                      onClick={(e) => e.stopPropagation()}
                      title="Open PR on GitHub"
                    >
                      PR #{g.pr_number} <ExternalLink size={11} aria-hidden />
                    </a>
                  )}
                  <ChevronRight size={14} className={styles.rowChev} aria-hidden />
                </Link>
              </li>
            ))}
            {failedTasks.map(({ task, goal }) => (
              <li key={task.task_id}>
                <Link className={styles.row} to={`/goals/${goal.goal_id}`}>
                  <StatusBadge domain="task" value={task.status} bare />
                  <span className={styles.rowTitle}>{task.title || task.task_id}</span>
                  <span className={styles.rowMeta}>
                    {goal.name}
                    {(task.retry_count ?? 0) > 0 && ` · retry ${task.retry_count}`}
                  </span>
                  <ChevronRight size={14} className={styles.rowChev} aria-hidden />
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* ── Running now ──────────────────────────────────────────────────── */}
      <section className={styles.section} aria-label="Running now">
        <h2 className={styles.sectionTitle + ' label'}>Running now</h2>
        {runningTasks.length === 0 ? (
          <p className={styles.empty}>
            {plan.status === 'phase_active'
              ? 'No tasks are executing right now.'
              : 'Workers run during active phases.'}
          </p>
        ) : (
          <ul className={styles.rows}>
            {runningTasks.map(({ task, goal }) => {
              const at = taskEventAt(task.task_id);
              return (
                <li key={task.task_id}>
                  <Link className={styles.row} to={`/goals/${goal.goal_id}`}>
                    <StatusBadge domain="task" value={task.status} bare />
                    <span className={styles.rowTitle}>{task.title || task.task_id}</span>
                    <span className={styles.rowMeta}>
                      {goal.name} · {agentName(task.assigned_agent_id)}
                    </span>
                    <span
                      className={styles.rowTime}
                      title={at ? new Date(at).toLocaleString() : undefined}
                    >
                      {at ? relTime(at, now) : ''}
                    </span>
                  </Link>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      {/* ── Documents: what was agreed ───────────────────────────────────── */}
      {(plan.brief || plan.architecture_summary) && (
        <section className={styles.section} aria-label="Plan documents">
          <h2 className={styles.sectionTitle + ' label'}>Documents</h2>
          <div className={styles.docs}>
            {plan.brief && (
              <details className={styles.doc} open={plan.status === 'discovery'}>
                <summary className={styles.docSummary}>Project brief</summary>
                <div className={styles.docBody}>
                  <DocField label="Vision">{plan.brief.vision}</DocField>
                  {plan.brief.constraints?.length > 0 && (
                    <DocField label="Constraints">
                      <ul className={styles.docList}>
                        {plan.brief.constraints.map((c, i) => <li key={i}>{c}</li>)}
                      </ul>
                    </DocField>
                  )}
                  <DocField label="Phase 1 exit criteria">{plan.brief.phase_1_exit_criteria}</DocField>
                  {plan.brief.open_questions?.length > 0 && (
                    <DocField label="Open questions">
                      <ul className={styles.docList}>
                        {plan.brief.open_questions.map((q, i) => <li key={i}>{q}</li>)}
                      </ul>
                    </DocField>
                  )}
                </div>
              </details>
            )}
            {plan.architecture_summary && (
              <details className={styles.doc} open={plan.status === 'architecture'}>
                <summary className={styles.docSummary}>Architecture summary</summary>
                <div className={styles.docBody}>
                  <p className={styles.docText}>{plan.architecture_summary}</p>
                </div>
              </details>
            )}
          </div>
        </section>
      )}
    </div>
  );
}

function flatTasks(goals: GoalAggregate[]): { task: TaskSummary; goal: GoalAggregate }[] {
  return goals.flatMap((goal) => goal.tasks.map((task) => ({ task, goal })));
}

function DocField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className={styles.docField}>
      <span className="label">{label}</span>
      <div className={styles.docText}>{children}</div>
    </div>
  );
}
