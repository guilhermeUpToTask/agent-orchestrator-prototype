import React from 'react';
import { Link, useParams } from 'react-router-dom';
import { ArrowLeft, ExternalLink } from 'lucide-react';
import { useAgents, useGoals, usePlan } from '../lib/queries';
import { StatusBadge } from '../components/StatusBadge';
import { checkMeta } from '../styles/tokens';
import type { GoalAggregate, TaskSummary } from '../types/ui';
import styles from './Goals.module.css';

/** Dense goal table — the drill-down entry point under the plan. */
export function GoalsView() {
  const { data: goals = [], isLoading, error } = useGoals();
  const { data: plan } = usePlan();

  const phaseOf = (g: GoalAggregate) =>
    plan?.phases.find((p) => p.goal_names.includes(g.name))?.index;

  if (error) {
    return <div className={styles.page}><p className={styles.empty} role="alert">Couldn't load goals: {error.message}</p></div>;
  }

  if (isLoading) {
    return (
      <div className={styles.page} aria-busy="true">
        {[0, 1, 2, 3].map((i) => <div key={i} className="skeleton" style={{ height: 36, marginBottom: 6 }} />)}
      </div>
    );
  }

  if (goals.length === 0) {
    return (
      <div className={styles.page}>
        <p className={styles.empty}>
          No goals yet. Goals are dispatched when you approve the architecture.
        </p>
      </div>
    );
  }

  return (
    <div className={styles.page}>
      <table className={styles.table}>
        <thead>
          <tr>
            <th>Goal</th><th>Status</th><th>Phase</th><th>Tasks</th><th>PR</th><th aria-label="Open" />
          </tr>
        </thead>
        <tbody>
          {goals.map((g) => {
            const done = g.tasks.filter((t) => ['succeeded', 'merged'].includes(t.status)).length;
            const phase = phaseOf(g);
            return (
              <tr key={g.goal_id}>
                <td className={styles.nameCell}>
                  <Link to={`/goals/${g.goal_id}`}>{g.name}</Link>
                </td>
                <td><StatusBadge domain="goal" value={g.status} /></td>
                <td className="mono">{phase != null ? `P${phase}` : '—'}</td>
                <td>
                  <span className={styles.progress}>
                    <span className="mono">{done}/{g.tasks.length}</span>
                    <span className={styles.bar} aria-hidden>
                      {g.tasks.map((t) => (
                        <span key={t.task_id} className={`${styles.seg} ${styles['seg_' + segKind(t)]}`} />
                      ))}
                    </span>
                  </span>
                </td>
                <td>
                  {g.pr_number != null ? (
                    <a href={g.pr_html_url ?? '#'} target="_blank" rel="noreferrer" className={styles.prLink}>
                      #{g.pr_number} <ExternalLink size={11} aria-hidden />
                    </a>
                  ) : <span className={styles.dim}>—</span>}
                </td>
                <td className={styles.openCell}>
                  <Link to={`/goals/${g.goal_id}`} aria-label={`Open ${g.name}`}>→</Link>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function segKind(t: TaskSummary): string {
  if (['succeeded', 'merged'].includes(t.status)) return 'ok';
  if (['in_progress', 'assigned', 'requeued'].includes(t.status)) return 'run';
  if (t.status === 'failed') return 'fail';
  return 'idle';
}

/** Goal detail: tasks with TRUE dependency-derived "waiting on". */
export function GoalDetail() {
  const { goalId } = useParams<{ goalId: string }>();
  const { data: goals = [], isLoading } = useGoals();
  const { data: agents = [] } = useAgents();

  const goal = goals.find((g) => g.goal_id === goalId);
  const agentName = (id: string | null | undefined) =>
    agents.find((a) => a.agent_id === id)?.name;

  if (isLoading) {
    return <div className={styles.page} aria-busy="true"><div className="skeleton" style={{ height: 200 }} /></div>;
  }

  if (!goal) {
    return (
      <div className={styles.page}>
        <p className={styles.empty} role="alert">
          Goal <code>{goalId}</code> wasn't found. <Link to="/goals">Back to goals</Link>.
        </p>
      </div>
    );
  }

  const byId = new Map(goal.tasks.map((t) => [t.task_id, t]));
  // Real blockers: this task's declared depends_on that aren't settled —
  // not "every unfinished task in the goal".
  const blockersOf = (t: TaskSummary) =>
    (t.depends_on ?? [])
      .map((id) => byId.get(id))
      .filter((d): d is TaskSummary => !!d && !['succeeded', 'merged'].includes(d.status));

  return (
    <div className={styles.page}>
      <Link to="/goals" className={styles.back}>
        <ArrowLeft size={13} aria-hidden /> Goals
      </Link>

      <header className={styles.detailHeader}>
        <h1 className={styles.detailTitle}>{goal.name}</h1>
        <StatusBadge domain="goal" value={goal.status} />
      </header>
      {goal.description && <p className={styles.desc}>{goal.description}</p>}

      {goal.pr_number != null && (
        <section className={styles.prCard} aria-label="Pull request">
          <div className={styles.prHead}>
            <a href={goal.pr_html_url ?? '#'} target="_blank" rel="noreferrer" className={styles.prLink}>
              PR #{goal.pr_number} <ExternalLink size={12} aria-hidden />
            </a>
            <span className="mono">{goal.pr_status ?? 'open'}</span>
          </div>
          <div className={styles.prChecks}>
            <span className={styles.prCheck}>
              <StatusBadge domain="custom" value={{ ...checkMeta(goal.pr_checks_passed), label: `CI ${checkMeta(goal.pr_checks_passed).label.toLowerCase()}` }} bare />
            </span>
            <span className={styles.prCheck}>
              <StatusBadge domain="custom" value={{ ...checkMeta(goal.pr_approved), label: goal.pr_approved ? 'Review approved' : goal.pr_approved === false ? 'Changes requested' : 'Review pending' }} bare />
            </span>
          </div>
          {goal.status === 'awaiting_pr_approval' && (
            <p className={styles.prNote}>
              Merging happens on GitHub — the orchestrator advances once this PR is merged.
            </p>
          )}
        </section>
      )}

      <table className={styles.table}>
        <thead>
          <tr><th>Task</th><th>Status</th><th>Agent</th><th>Waiting on</th><th>Retries</th></tr>
        </thead>
        <tbody>
          {goal.tasks.map((t) => {
            const blockers = blockersOf(t);
            return (
              <tr key={t.task_id}>
                <td className={styles.nameCell}>
                  <span className={styles.taskTitle}>{t.title || t.task_id}</span>
                  <span className={`mono ${styles.taskId}`}>{t.task_id}</span>
                </td>
                <td><StatusBadge domain="task" value={t.status} /></td>
                <td className="mono">{agentName(t.assigned_agent_id) ?? <span className={styles.dim}>—</span>}</td>
                <td className="mono">
                  {['succeeded', 'merged', 'in_progress'].includes(t.status) || blockers.length === 0
                    ? <span className={styles.dim}>—</span>
                    : blockers.map((b) => b.title || b.task_id).join(', ')}
                </td>
                <td className="mono">{t.retry_count || <span className={styles.dim}>0</span>}</td>
              </tr>
            );
          })}
          {goal.tasks.length === 0 && (
            <tr><td colSpan={5} className={styles.empty}>No tasks yet — this goal is planned but not expanded.</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
