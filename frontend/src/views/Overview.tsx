import React from 'react';
import { Link, useParams } from 'react-router-dom';
import { ChevronRight } from 'lucide-react';
import { useAgents, usePlan } from '../lib/queries';
import { usePlannerStore } from '../store/plannerStore';
import { StatusBadge } from '../components/StatusBadge';
import { PLAN_STATUS } from '../styles/tokens';
import type { Goal, Task } from '../types/ui';
import styles from './Overview.module.css';

/**
 * The operator's home for one plan: answers "what is happening, and what do
 * I owe?" — the canonical root status/activity, review or recovery queue,
 * current execution, preserved cycle history, and brief.
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

  const gate = plan.pending_gate?.continuation ?? null;

  const failedTasks = flatTasks(plan.goals).filter((t) => t.task.status === 'failed');
  const runningTasks = flatTasks(plan.goals).filter((t) => t.task.status === 'running');
  const attentionCount = (gate ? 1 : 0) + (plan.block ? 1 : 0) + failedTasks.length;

  const base = `/plans/${encodeURIComponent(planId)}`;

  return (
    <div className={styles.page}>
      {/* ── Current ProjectPlan status ───────────────────────────────────── */}
      <header className={styles.phaseHeader}>
        <div className={styles.phaseTitleRow}>
          <h1 className={styles.phaseTitle}>
            {PLAN_STATUS[plan.status].label} — {humanize(plan.activity)}
          </h1>
          <StatusBadge domain="plan" value={plan.status} />
        </div>
        <p className={styles.phaseGoal}>
          {plan.status_reason.message ?? plan.brief.split("\n")[0]}
        </p>
      </header>

      {/* ── Needs attention ──────────────────────────────────────────────── */}
      <section className={styles.section} aria-label="Needs attention">
        <h2 className={styles.sectionTitle + ' label'}>
          Needs attention {attentionCount > 0 && <span className={styles.gateCount}>{attentionCount}</span>}
        </h2>

        {attentionCount === 0 ? (
          <p className={styles.empty}>
            {plan.status === "idle"
              ? "Nothing is waiting on you. Start a new intent when ready."
              : "Nothing needs operator attention right now."}
          </p>
        ) : (
          <ul className={styles.rows}>
            {gate && (
              <li>
                <button className={styles.row} onClick={() => setGateOpen(true)}>
                  <StatusBadge domain="plan" value={plan.status} bare />
                  <span className={styles.rowTitle}>{gate}</span>
                  <ChevronRight size={14} className={styles.rowChev} aria-hidden />
                </button>
              </li>
            )}
            {plan.block && (
              <li>
                <div className={styles.row}>
                  <StatusBadge domain="plan" value="blocked" bare />
                  <span className={styles.rowTitle}>{plan.block.explanation}</span>
                  <span className={styles.rowMeta}>{humanize(plan.block.stage)}</span>
                </div>
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
            {plan.status === "running"
              ? "No task invocation is active at this instant."
              : "Workers advance only while the ProjectPlan is running."}
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

      {/* ── Cycle history ───────────────────────────────────────────────── */}
      <section className={styles.section} aria-label="Cycle history">
        <h2 className={styles.sectionTitle + " label"}>Cycle history</h2>
        {plan.cycles.length === 0 ? (
          <p className={styles.empty}>No cycle has been activated yet.</p>
        ) : (
          <div className={styles.docs}>
            {plan.cycles
              .slice()
              .sort((a, b) => Date.parse(b.started_at) - Date.parse(a.started_at))
              .map((cycle) => {
                const tasks = cycle.goals.flatMap((goal) => goal.tasks);
                const done = tasks.filter((task) => task.status === "done").length;
                return (
                  <details className={styles.doc} key={cycle.id}>
                    <summary className={styles.docSummary}>
                      {cycle.status.toUpperCase()} · {cycle.id}
                      <span className={styles.rowMeta}>
                        {" · "}{done}/{tasks.length} tasks done
                      </span>
                    </summary>
                    <div className={styles.docBody}>
                      {cycle.status === "superseded" && (
                        <p className={styles.docText}>
                          Preserved source cycle. Completed work is locked history;
                          unfinished work was replaced by an approved replan.
                        </p>
                      )}
                      {cycle.goals.map((goal) => (
                        <div className={styles.docField} key={goal.id}>
                          <span className="label">{goal.name} · {goal.status}</span>
                          <span className={styles.docText}>
                            {goal.tasks.filter((task) => task.status === "done").length}
                            /{goal.tasks.length} tasks completed
                          </span>
                        </div>
                      ))}
                    </div>
                  </details>
                );
              })}
          </div>
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

function humanize(value: string): string {
  return value.replace(/:/g, " · ").replace(/_/g, " ");
}

function flatTasks(goals: Goal[]): { task: Task; goal: Goal }[] {
  return goals.flatMap((goal) => goal.tasks.map((task) => ({ task, goal })));
}
