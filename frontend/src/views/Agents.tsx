import React from 'react';
import { Link, useParams } from 'react-router-dom';
import { Cpu, CircleDot } from 'lucide-react';
import { useAgents, usePlan } from '../lib/queries';
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
 * The agent roster for THIS plan: every registered agent spec and the task
 * it is currently running here. Editing lives in Settings → Agents.
 */
export function AgentsView() {
  const { planId = '' } = useParams();
  const { data: agents = [] } = useAgents();
  const { data: plan } = usePlan(planId || null);
  const goals = plan?.goals ?? [];

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
          const capNames = (a.capabilities ?? []).map((c) => c.id ?? c.name);

          return (
            <div key={a.id} className={styles.card}>
              <div className={styles.cardHead}>
                <CircleDot
                  size={12}
                  color={running ? 'var(--gate)' : 'var(--ok)'}
                  aria-hidden
                />
                <span className={styles.name}>{a.name}</span>
                <span
                  className={`${styles.state} ${running ? styles.stateRunning : styles.stateIdle}`}
                >
                  {running ? 'running' : 'idle'}
                </span>
                <span className={styles.id}>{a.id}</span>
              </div>

              <div className={styles.meta}>
                {a.role} · {capNames.join(' · ') || 'no capabilities'}
              </div>

              {task ? (
                <div className={styles.task}>▸ {task.name}</div>
              ) : (
                <div className={styles.noTask}>no task in flight</div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
