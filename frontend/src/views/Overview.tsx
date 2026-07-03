import React from 'react';
import { Link, useParams } from 'react-router-dom';
import { ChevronRight } from 'lucide-react';
import { useAgents, usePlan } from '../lib/queries';
import { usePlannerStore } from '../store/plannerStore';
import { StatusBadge } from '../components/StatusBadge';
import { PLAN_PHASE } from '../styles/tokens';
import type { Goal, Task } from '../types/ui';
import styles from './Overview.module.css';

/**
 * The operator's home for one plan: answers "what is happening, and what do
 * I owe?" — the current phase, the amber queue (gates + failed tasks), what
 * is executing right now, and the brief.
 */
export function Overview() {
  const { planId = '' } = useParams();
  const { data: plan, isLoading, error, refetch } = usePlan(planId || null);
  const { data: agents = [] } = useAgents();
  const setGateOpen = usePlannerStore((s) => s.setGateOpen);

  if (error) {
    return (
      <div className={styles.page}>
        <div className={styles.errorCard} role="alert">
          <div className={styles.errorTitle}>Can't reach the backend</div>
          <p className={styles.errorBody}>
            {(error as Error).message}. Check that the API server is running at{' '}
            <code>{import.meta.env.VITE_API_URL ?? 'http://localhost:8000'}</code>, then retry.
          </p>
          <button className={styles.retryBtn} onClick={() => refetch()}>Retry</button>
        </div>
      </div>
    );
  }

  if (isLoading || !plan) {
    return (
      <div className={styles.page} aria-busy="true" aria-label="Loading plan overview">
        <div className="skeleton" style={{ height: 84 }} />
        <div className="skeleton" style={{ height: 160 }} />
        <div className="skeleton" style={{ height: 160 }} />
      </div>
    );
  }

  const agentName = (id: string | null) =>
    agents.find((a) => a.id === id)?.name ?? 'unassigned';

  const gate =
    plan.phase === 'awaiting_review'
      ? 'Roadmap ready — review the tasks and approve to start execution'
      : plan.phase === 'review'
        ? 'Execution finished — finish the plan or replan the next iteration'
        : null;

  const failedTasks = flatTasks(plan.goals).filter((t) => t.task.status === 'failed');
  const runningTasks = flatTasks(plan.goals).filter((t) => t.task.status === 'running');
  const attentionCount = (gate ? 1 : 0) + failedTasks.length;

  const base = `/plans/${encodeURIComponent(planId)}`;

  return (
    <div className={styles.page}>
      {/* ── Current phase header ─────────────────────────────────────────── */}
      <header className={styles.phaseHeader}>
        <div className={styles.phaseTitleRow}>
          <h1 className={styles.phaseTitle}>
            {PLAN_PHASE[plan.phase].label} — iteration {plan.iteration}
          </h1>
          <StatusBadge domain="phase" value={plan.phase} />
        </div>
        <p className={styles.phaseGoal}>{plan.brief.split('\n')[0]}</p>
      </header>

      {/* ── Needs attention ──────────────────────────────────────────────── */}
      <section className={styles.section} aria-label="Needs attention">
        <h2 className={styles.sectionTitle + ' label'}>
          Needs attention {attentionCount > 0 && <span className={styles.gateCount}>{attentionCount}</span>}
        </h2>

        {attentionCount === 0 ? (
          <p className={styles.empty}>
            {plan.phase === 'discovery' || plan.phase === 'replanning'
              ? 'The reasoner is waiting for you in the chat panel.'
              : 'Nothing is waiting on you. The machine has the conn.'}
          </p>
        ) : (
          <ul className={styles.rows}>
            {gate && (
              <li>
                <button className={styles.row} onClick={() => setGateOpen(true)}>
                  <StatusBadge domain="phase" value={plan.phase} bare />
                  <span className={styles.rowTitle}>{gate}</span>
                  <ChevronRight size={14} className={styles.rowChev} aria-hidden />
                </button>
              </li>
            )}
            {failedTasks.map(({ task, goal }) => (
              <li key={task.id}>
                <Link className={styles.row} to={`${base}/goals`}>
                  <StatusBadge domain="status" value={task.status} bare />
                  <span className={styles.rowTitle}>{task.name}</span>
                  <span className={styles.rowMeta}>
                    {goal.name}
                    {task.attempt > 1 && ` · attempt ${task.attempt}`}
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
            {plan.phase === 'running'
              ? 'No tasks are executing right now.'
              : 'Workers run during the RUNNING phase.'}
          </p>
        ) : (
          <ul className={styles.rows}>
            {runningTasks.map(({ task, goal }) => (
              <li key={task.id}>
                <Link className={styles.row} to={`${base}/goals`}>
                  <StatusBadge domain="status" value={task.status} bare />
                  <span className={styles.rowTitle}>{task.name}</span>
                  <span className={styles.rowMeta}>
                    {goal.name} · {agentName(task.agent_id)}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* ── Roadmap summary ──────────────────────────────────────────────── */}
      <section className={styles.section} aria-label="Roadmap">
        <h2 className={styles.sectionTitle + ' label'}>Roadmap</h2>
        {plan.goals.length === 0 ? (
          <p className={styles.empty}>
            No goals yet — agree the roadmap with the reasoner in the chat.
          </p>
        ) : (
          <ul className={styles.rows}>
            {plan.goals
              .slice()
              .sort((a, b) => a.position - b.position)
              .map((g) => {
                const done = g.tasks.filter((t) => t.status === 'done').length;
                return (
                  <li key={g.id}>
                    <Link className={styles.row} to={`${base}/goals`}>
                      <StatusBadge domain="status" value={g.status} bare />
                      <span className={styles.rowTitle}>{g.name}</span>
                      <span className={styles.rowMeta}>
                        {g.tasks.length === 0
                          ? 'no tasks yet'
                          : `${done}/${g.tasks.length} tasks done`}
                      </span>
                    </Link>
                  </li>
                );
              })}
          </ul>
        )}
      </section>

      {/* ── The brief ────────────────────────────────────────────────────── */}
      <section className={styles.section} aria-label="Plan brief">
        <h2 className={styles.sectionTitle + ' label'}>Brief</h2>
        <p className={styles.empty} style={{ whiteSpace: 'pre-wrap' }}>{plan.brief}</p>
      </section>
    </div>
  );
}

function flatTasks(goals: Goal[]): { task: Task; goal: Goal }[] {
  return goals.flatMap((goal) => goal.tasks.map((task) => ({ task, goal })));
}
