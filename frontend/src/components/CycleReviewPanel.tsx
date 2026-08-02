import React, { useState } from 'react';
import { useCycleReview, useReviewPatch } from '../lib/queries';
import type { ReviewUnit } from '../lib/api';
import { Button } from './ui';
import styles from './CycleReviewPanel.module.css';

/**
 * A cycle split into review-sized units.
 *
 * A cycle branch is one large diff, and defect detection falls off sharply past
 * a few hundred changed lines. This does not try to be a better diff viewer
 * than the operator's editor — it answers the question the editor cannot:
 * WHICH PART SHOULD I LOOK AT FIRST, and what did the orchestrator already
 * prove about it. Every unit carries the local command that opens the same
 * thing, so the terminal stays the place you actually read code.
 *
 * Read-only by design. There is no accept/reject here: half-accepting a
 * candidate would invalidate the revision-bound evidence that makes it
 * trustworthy, so acceptance stays at the granularity the orchestrator can
 * verify.
 */

const BAND_LABEL: Record<string, string> = {
  small: 'small',
  moderate: 'moderate',
  large: 'large',
  very_large: 'very large',
};

const KIND_LABEL: Record<string, string> = {
  whole_cycle: 'Whole cycle',
  goal_merge: 'Goal merge',
  test_authoring: 'Test (RED first)',
  implementation: 'Implementation',
};

function UnitRow({
  unit,
  planId,
  cycleId,
}: {
  unit: ReviewUnit;
  planId: string;
  cycleId: string;
}) {
  const [open, setOpen] = useState(false);
  const patch = useReviewPatch(
    open ? planId : null,
    open ? cycleId : null,
    open ? unit.base : null,
    open ? unit.sha : null,
  );

  return (
    <div className={styles.unit}>
      <div className={styles.unitHead}>
        <span className={styles.kind}>{KIND_LABEL[unit.kind] ?? unit.kind}</span>
        <code className={styles.sha}>{unit.sha.slice(0, 8)}</code>
        {unit.diff ? (
          <>
            <span className={styles.stat}>
              {unit.diff.files_changed} file{unit.diff.files_changed === 1 ? '' : 's'}
            </span>
            <span className={styles.added}>+{unit.diff.insertions}</span>
            <span className={styles.removed}>−{unit.diff.deletions}</span>
            <span
              className={styles.band}
              data-band={unit.diff.review_band}
              title="How carefully this wants to be read"
            >
              {BAND_LABEL[unit.diff.review_band] ?? unit.diff.review_band}
            </span>
          </>
        ) : (
          <span className={styles.unavailable}>
            {unit.unavailable_reason ?? 'diff unavailable'}
          </span>
        )}
        {unit.diff && (
          <Button size="sm" onClick={() => setOpen((v) => !v)}>
            {open ? 'Hide diff' : 'Show diff'}
          </Button>
        )}
      </div>

      <code className={styles.command} title="Opens the same change in your own tools">
        {unit.local_command}
      </code>

      {open && (
        <div className={styles.patchBox}>
          {patch.isPending && <div className={styles.meta}>Loading diff…</div>}
          {patch.isError && (
            <div className={styles.unavailable}>Could not read this diff.</div>
          )}
          {patch.data && (
            <>
              {patch.data.truncated && (
                <div className={styles.truncated}>
                  Showing the first {Math.round(patch.data.patch.length / 1024)} KB of{' '}
                  {Math.round(patch.data.total_bytes / 1024)} KB. Run the command above
                  for the whole change.
                </div>
              )}
              <pre className={styles.patch}>{patch.data.patch}</pre>
            </>
          )}
        </div>
      )}
    </div>
  );
}

export function CycleReviewPanel({
  planId,
  cycleId,
}: {
  planId: string;
  cycleId: string;
}) {
  const { data, isPending, isError } = useCycleReview(planId, cycleId);

  if (isPending) return <div className={styles.meta}>Loading review…</div>;
  if (isError || !data) return null;

  return (
    <div className={styles.panel}>
      <div className={styles.head}>
        <h4 className={styles.title}>Review</h4>
        <span className={styles.meta}>
          {data.cycle_branch} against {data.default_branch ?? 'the default branch'}
        </span>
      </div>

      {data.whole_cycle && (
        <UnitRow unit={data.whole_cycle} planId={planId} cycleId={cycleId} />
      )}

      {data.goals.map((goal) => (
        <details key={goal.goal_id} className={styles.goal}>
          <summary className={styles.goalHead}>
            <span className={styles.goalName}>{goal.name}</span>
            {goal.merge?.diff && (
              <span className={styles.meta}>
                {goal.merge.diff.changed_lines} lines across{' '}
                {goal.merge.diff.files_changed} files
              </span>
            )}
          </summary>

          {goal.merge && (
            <UnitRow unit={goal.merge} planId={planId} cycleId={cycleId} />
          )}

          {goal.tasks.map((task) => (
            <div key={task.task_id} className={styles.task}>
              <div className={styles.taskHead}>
                <span className={styles.taskName}>{task.name}</span>
                {task.verification_command && (
                  <code className={styles.verified} title="What the orchestrator ran">
                    {task.verification_command} → exit {task.exit_code}
                  </code>
                )}
              </div>
              {task.allowed_scope.length > 0 && (
                <div className={styles.scope}>
                  Scope: {task.allowed_scope.join(', ')}
                  {task.forbidden_scope.length > 0 && (
                    <> · forbidden: {task.forbidden_scope.join(', ')}</>
                  )}
                </div>
              )}
              {task.units.map((unit) => (
                <UnitRow
                  key={unit.sha + unit.kind}
                  unit={unit}
                  planId={planId}
                  cycleId={cycleId}
                />
              ))}
            </div>
          ))}
        </details>
      ))}
    </div>
  );
}
