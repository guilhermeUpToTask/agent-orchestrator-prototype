import type { AgentSpec, Task } from '../types/ui';

/**
 * "attempt N/max · exhausted" / "attempt N · tool_error" — the fail-tone chip
 * shown on the canvas node, the detail panel, and the Overview attention row.
 * Only failed tasks get one: a task that succeeded (or is still running) on
 * its final attempt is not "exhausted".
 */
export function attemptLabel(task: Task, agent: AgentSpec | null): string | null {
  if (task.status !== 'failed' || (task.attempt ?? 0) < 1) return null;
  const max = agent?.default_retry?.max_attempts;
  const exhausted = max != null && task.attempt >= max;
  const count = max != null ? `attempt ${task.attempt}/${max}` : `attempt ${task.attempt}`;
  const suffix = exhausted ? 'exhausted' : task.result?.failure_kind ?? null;
  return suffix ? `${count} · ${suffix}` : count;
}

/** Verified / rejected — derived from the durable verification_evidence on the task. */
export function verificationLabel(task: Task): 'verified' | 'rejected' | null {
  const evidence = task.verification_evidence ?? [];
  if (evidence.length === 0) return null;
  return evidence[evidence.length - 1].accepted ? 'verified' : 'rejected';
}
