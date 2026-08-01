import React from 'react';
import { useCycleEvidence } from '../lib/queries';
import type { Cycle } from '../types/ui';
import { CountChip } from './ui';
import styles from '../views/Overview.module.css';

/** Durable verification and publication truth for one preserved cycle. */
export function CycleEvidenceSummary({ planId, cycle }: { planId: string; cycle: Cycle }) {
  const evidence = useCycleEvidence(planId, cycle.id);

  if (evidence.isLoading) {
    return <div className="skeleton" style={{ height: 72 }} />;
  }
  if (evidence.error) {
    return (
      <p className={styles.errorBody} role="alert">
        Evidence unavailable: {(evidence.error as Error).message}
      </p>
    );
  }
  if (!evidence.data) return null;

  const accepted = evidence.data.goals.flatMap((goal) =>
    goal.tasks.flatMap((task) => task.accepted_evidence),
  );
  const rejected = evidence.data.goals.reduce(
    (count, goal) => count + goal.tasks.reduce((sum, task) => sum + task.rejected_evidence_count, 0),
    0,
  );
  const superseded = evidence.data.goals.reduce(
    (count, goal) => count + goal.tasks.reduce((sum, task) => sum + task.superseded_evidence_count, 0),
    0,
  );
  const disposition = evidence.data.disposition;

  return (
    <div className={styles.evidence}>
      <div className={styles.evidenceHead}>
        <span className="label">Verification evidence</span>
        <CountChip tone={accepted.length > 0 ? 'ok' : 'idle'}>{accepted.length} accepted</CountChip>
        {rejected > 0 && <CountChip tone="fail">{rejected} rejected</CountChip>}
        {superseded > 0 && <CountChip tone="idle">{superseded} superseded</CountChip>}
      </div>

      {evidence.data.goals.map((goal) => (
        <div className={styles.evidenceGoal} key={goal.goal_id}>
          <span className="label">{goal.goal_id} · {goal.status}</span>
          {goal.promotion && (
            <span className={styles.docText}>
              Promoted {goal.promotion.from_ref} into {goal.promotion.into_ref} at{' '}
              <code>{goal.promotion.merge_sha.slice(0, 12)}</code>
            </span>
          )}
          {goal.tasks.flatMap((task) => task.accepted_evidence.map((item) => (
            <div className={styles.evidenceItem} key={item.id}>
              <code>{item.exact_command}</code>
              <span className={styles.rowMeta}>
                exit {item.exit_code} · candidate {item.candidate_commit_sha.slice(0, 12)} · test{' '}
                {item.test_commit_sha.slice(0, 12)}
              </span>
              <span className={styles.rowMeta}>{item.bounded_output_ref}</span>
            </div>
          )))}
          {goal.tasks.map((task) => task.protected_scope && (
            <div className={styles.evidenceScope} key={`${task.task_id}-scope`}>
              <span className={styles.rowMeta}>{task.task_id} protected scope</span>
              <span className={styles.docText}>
                allowed: {task.protected_scope.allowed_scope.join(', ') || 'none declared'}
                {task.protected_scope.forbidden_scope.length > 0
                  ? ` · forbidden: ${task.protected_scope.forbidden_scope.join(', ')}`
                  : ''}
              </span>
            </div>
          ))}
        </div>
      ))}

      {evidence.data.unattributed_evidence_refs.length > 0 && (
        <div className={styles.docField}>
          <span className="label">Unattributed references</span>
          <span className={styles.docText}>{evidence.data.unattributed_evidence_refs.join(', ')}</span>
        </div>
      )}

      <div className={styles.outputDisposition}>
        <span className="label">Output</span>
        {disposition ? (
          <span className={styles.docText}>
            {humanize(disposition.disposition)}
            {disposition.output_reference ? ` · ${disposition.output_reference}` : ''}
          </span>
        ) : (
          <span className={styles.docText}>
            No output disposition recorded. To publish a reviewable result, push{' '}
            <code>refs/heads/cycle/{cycle.id}</code> and open a pull request into the repository's
            default branch.
          </span>
        )}
      </div>
    </div>
  );
}

function humanize(value: string): string {
  return value.replace(/_/g, ' ');
}
