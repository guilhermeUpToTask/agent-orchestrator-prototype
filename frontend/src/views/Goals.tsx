import React from 'react';
import { useParams } from 'react-router-dom';
import { PlanCanvas } from '../components/PlanCanvas';
import { DetailPanel } from '../components/DetailPanel';
import { usePlan } from '../lib/queries';
import styles from './Goals.module.css';

/**
 * The roadmap canvas: goal groups containing task nodes, laid out in
 * execution order, with the task inspector on the right when a node is
 * selected.
 */
export function GoalsView() {
  const { planId = '' } = useParams();
  const { data: plan, isLoading, error } = usePlan(planId || null);

  if (error) {
    return (
      <div className={styles.page}>
        <p className={styles.empty} role="alert">
          Couldn't load the plan: {(error as Error).message}
        </p>
      </div>
    );
  }

  if (isLoading) {
    return (
      <div className={styles.page} aria-busy="true">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="skeleton" style={{ height: 36, marginBottom: 6 }} />
        ))}
      </div>
    );
  }

  if (!plan || plan.goals.length === 0) {
    return (
      <div className={styles.page}>
        <p className={styles.empty}>
          No goals yet. The roadmap appears once the discovery conversation
          commits it.
        </p>
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', height: '100%', minHeight: 0 }}>
      <PlanCanvas planId={planId} />
      <DetailPanel />
    </div>
  );
}
