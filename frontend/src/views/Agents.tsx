import React from 'react';
import { Link, useParams } from 'react-router-dom';
import { Cpu, Play } from 'lucide-react';
import { useAgents, useDefaultAgent, usePlan } from '../lib/queries';
import { usePlannerStore } from '../store/plannerStore';
import { StatusBadge } from '../components/StatusBadge';
import { Card, CountChip, ErrorState } from '../components/ui';
import type { Goal, Task } from '../types/ui';
import styles from './Agents.module.css';

/** The task an agent is currently executing in this plan, if any. */
function currentTaskFor(agentId: string, goals: Goal[]): Task | null {
  for (const g of goals) {
    for (const t of g.tasks) {
      if (t.agent_id === agentId && t.status === 'running') return t;
    }
  }
  return null;
}

/**
 * The latest failed task this agent left behind in this plan, if any. Tasks
 * carry no timestamp, so "latest" is the last match in goal/task order —
 * goals append over replans, so later positions are newer work.
 */
function lastFailureFor(agentId: string, goals: Goal[]): Task | null {
  let last: Task | null = null;
  for (const g of goals) {
    for (const t of g.tasks) {
      if (t.agent_id === agentId && t.status === 'failed') last = t;
    }
  }
  return last;
}

/**
 * The agent roster for THIS plan: every registered agent spec and the task
 * it is currently running here. Editing lives in Settings → Agents.
 */
export function AgentsView() {
  const { planId = '' } = useParams();
  const { data: agents = [], isLoading, error, refetch } = useAgents();
  const { data: plan } = usePlan(planId || null);
  const { data: defaultAgent } = useDefaultAgent();
  const selectTask = usePlannerStore((s) => s.selectTask);
  const goals = plan?.goals ?? [];
  const base = `/plans/${encodeURIComponent(planId)}`;

  return (
    <div className={styles.page}>
      <div className={styles.head}>
        <h2 className={styles.title}>
          <Cpu size={15} aria-hidden /> AGENTS
        </h2>
        <Link to="/settings/agents" className={styles.manageLink}>
          Manage agents in Settings →
        </Link>
      </div>

      {error && agents.length === 0 ? (
        <ErrorState
          title="Can't reach the backend"
          message={`${(error as Error).message}. This is an error, not an empty roster.`}
          onRetry={() => refetch()}
        />
      ) : isLoading ? (
        <div className={styles.list} aria-busy="true" aria-label="Loading agents">
          {[0, 1, 2].map((i) => (
            <div key={i} className="skeleton" style={{ height: 72 }} />
          ))}
        </div>
      ) : (
        <>
          {agents.length === 0 && (
            <p className={styles.empty}>
              No agents registered. Add one in Settings → Agents, or seed with{' '}
              <code>orchestrate seed demo --stub</code>.
            </p>
          )}

          <div className={styles.list}>
            {agents.map((a) => {
              const task = currentTaskFor(a.id, goals);
              const running = task !== null;
              const failure = !running ? lastFailureFor(a.id, goals) : null;
              const capNames = (a.capabilities ?? []).map((c) => c.id ?? c.name);
              const isDefault = defaultAgent?.agent_id === a.id;

              return (
                <Card
                  key={a.id}
                  title={
                    <>
                      {a.name}
                      <span className={styles.id}>{a.id}</span>
                      {isDefault && <span className={styles.defaultTag}>default</span>}
                    </>
                  }
                  actions={
                    <StatusBadge domain="plan" value={running ? 'running' : 'idle'} />
                  }
                >
                  <div className={styles.meta}>
                    {a.role} · {a.runtime_type ?? 'unknown runtime'} ·{' '}
                    {capNames.join(' · ') || 'no capabilities'}
                  </div>

                  {task ? (
                    <Link
                      className={styles.task}
                      to={`${base}/goals`}
                      onClick={() => selectTask(task.id)}
                    >
                      <Play size={12} aria-hidden /> {task.name}
                    </Link>
                  ) : (
                    <div className={styles.noTask}>
                      no task in flight
                      {failure?.result?.failure_kind && (
                        <CountChip tone="fail">
                          last run: {failure.result.failure_kind}
                        </CountChip>
                      )}
                    </div>
                  )}
                </Card>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
