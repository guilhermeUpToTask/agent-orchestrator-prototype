import React from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { Clock3, Hand, Pencil, RefreshCw, RotateCcw, XCircle } from 'lucide-react';
import {
  useAgents,
  useBindProject,
  usePlan,
  usePlanningArtifacts,
  useProjects,
  useReplanMidRunning,
  useRetryPlanningStage,
  useRetryTask,
} from '../lib/queries';
import { usePlannerStore } from '../store/plannerStore';
import { StatusBadge } from '../components/StatusBadge';
import { AttentionItem, Button, CountChip, ErrorState, Select } from '../components/ui';
import { CycleEvidenceSummary } from '../components/CycleEvidenceSummary';
import { CycleReviewPanel } from '../components/CycleReviewPanel';
import { PLAN_STATUS } from '../styles/tokens';
import type { Goal, Plan, PlanBlock, Task } from '../types/ui';
import styles from './Overview.module.css';
import { attemptLabel } from '../lib/taskLabels';
import { absTime, useNow } from '../lib/time';

/** The failure reason inline — never force a Goals navigation just to learn why. */
function failureDetail(task: Task): string | null {
  if (!task.result?.failure_reason) return null;
  return task.result.failure_kind
    ? `${task.result.failure_reason} (${task.result.failure_kind})`
    : task.result.failure_reason;
}

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
  const selectTask = usePlannerStore((s) => s.selectTask);
  const navigate = useNavigate();
  const now = useNow();
  const retryTask = useRetryTask(planId);
  const retryPlanningStage = useRetryPlanningStage(planId);
  const replan = useReplanMidRunning(planId);
  const { data: planningArtifacts = [] } = usePlanningArtifacts(planId || null);

  if (error && !plan) {
    return (
      <div className={styles.page}>
        <ErrorState
          message={`${(error as Error).message}. Check that the API server is running at ${import.meta.env.VITE_API_URL ?? 'http://localhost:8000'}, then retry.`}
          onRetry={() => refetch()}
        />
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
  // Cyclic plans are authoritative through active_cycle. Root goals remain a
  // compatibility surface for pre-cyclic rows only.
  const currentGoals = plan.active_cycle?.goals ?? plan.goals;

  const goalBlocks = Object.entries(plan.goal_blocks).map(([goalId, block]) => ({
    goalId,
    goal: currentGoals.find((goal) => goal.id === goalId) ?? null,
    block,
  }));
  const humanGoalBlocks = goalBlocks.filter(({ block }) => block.requires_human);
  const automaticGoalBlocks = goalBlocks.filter(({ block }) => !block.requires_human);
  const humanPlanBlock = plan.block?.requires_human ? plan.block : null;
  const automaticPlanBlock = plan.block && !plan.block.requires_human ? plan.block : null;
  const blockedTaskIds = new Set(
    [plan.block?.task_id, ...goalBlocks.map(({ block }) => block.task_id)]
      .filter((taskId): taskId is string => !!taskId),
  );
  const failedTasks = flatTasks(currentGoals).filter(
    ({ task }) => task.status === 'failed' && !blockedTaskIds.has(task.id),
  );
  const runningTasks = flatTasks(currentGoals).filter((t) => t.task.status === 'running');
  const providerNeedsAttention = !!plan.provider_waiting?.needs_attention
    && ![humanPlanBlock, ...humanGoalBlocks.map(({ block }) => block)]
      .some((block) => block?.kind === 'provider_capacity');
  const attentionCount = (gate ? 1 : 0)
    + (humanPlanBlock ? 1 : 0)
    + humanGoalBlocks.length
    + (providerNeedsAttention ? 1 : 0)
    + failedTasks.length;
  const automaticRecoveryCount = (plan.provider_waiting && !plan.provider_waiting.needs_attention ? 1 : 0)
    + (automaticPlanBlock ? 1 : 0)
    + automaticGoalBlocks.length;

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

      {/* ── Current operation ───────────────────────────────────────────── */}
      {(plan.planning_operation || plan.active_run || plan.worker_lease || plan.tdd_stage) && (
        <section className={styles.section} aria-label="Current operation">
          <h2 className={styles.sectionTitle + ' label'}>Current operation</h2>
          <div className={styles.operationGrid}>
            {plan.planning_operation && (
              <div className={styles.operationCard}>
                <span className="label">Planning · {humanize(plan.planning_operation.purpose)}</span>
                <span className={styles.docText}>
                  {plan.planning_operation.safe_message ?? plan.planning_progress ?? humanize(plan.planning_operation.status)}
                </span>
                {plan.planning_operation.retry_at && (
                  <span className={styles.rowMeta}>retry at {absTime(plan.planning_operation.retry_at)}</span>
                )}
              </div>
            )}
            {plan.active_run && (
              <div className={styles.operationCard}>
                <span className="label">Execution attempt {plan.active_run.attempt_number}</span>
                <span className={styles.docText}>
                  task {plan.active_run.task_id} · run {plan.active_run.run_id}
                </span>
                <span className={styles.rowMeta}>started {absTime(plan.active_run.started_at)}</span>
              </div>
            )}
            {plan.worker_lease && (
              <div className={styles.operationCard}>
                <span className="label">{humanize(plan.worker_lease.scope)} lease</span>
                <span className={styles.docText}>
                  {plan.worker_lease.expired
                    ? 'Worker lease expired; this operation may be orphaned.'
                    : `${plan.worker_lease.seconds_remaining}s remaining · ${plan.worker_lease.worker_id}`}
                </span>
                <span className={styles.rowMeta}>expires {absTime(plan.worker_lease.expires_at)}</span>
              </div>
            )}
            {plan.tdd_stage && (
              <div className={styles.operationCard}>
                <span className="label">Verification stage</span>
                <span className={styles.docText}>{humanize(plan.tdd_stage)}</span>
              </div>
            )}
          </div>
        </section>
      )}

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
          <div className={styles.attentionList}>
            {humanPlanBlock && (
              <AttentionItem
                tone="fail"
                icon={<XCircle size={16} aria-hidden />}
                title={humanPlanBlock.explanation}
                meta={humanize(humanPlanBlock.stage)}
                actions={(
                  <BlockActions
                    planId={planId}
                    block={humanPlanBlock}
                    onRetry={() => retryBlock(humanPlanBlock, retryTask.mutate, retryPlanningStage.mutate)}
                    onEdit={() => openBlockedTask(humanPlanBlock, selectTask, navigate, base)}
                    onReplan={() => replan.mutate()}
                    pending={retryTask.isPending || retryPlanningStage.isPending || replan.isPending}
                  />
                )}
              />
            )}
            {humanGoalBlocks.map(({ goalId, goal, block }) => (
              <AttentionItem
                key={block.id}
                tone="gate"
                icon={<Hand size={16} aria-hidden />}
                title={block.explanation}
                meta={goal?.name ?? goalId}
                detail={humanize(block.stage)}
                actions={(
                  <BlockActions
                    planId={planId}
                    block={block}
                    onRetry={() => retryBlock(block, retryTask.mutate, retryPlanningStage.mutate)}
                    onEdit={() => openBlockedTask(block, selectTask, navigate, base)}
                    onReplan={() => replan.mutate()}
                    pending={retryTask.isPending || retryPlanningStage.isPending || replan.isPending}
                  />
                )}
              />
            ))}
            {providerNeedsAttention && plan.provider_waiting && (
              <AttentionItem
                tone="fail"
                icon={<XCircle size={16} aria-hidden />}
                title={plan.provider_waiting.safe_message}
                meta={providerLabel(plan.provider_waiting)}
                detail="The automatic recovery window expired; operator action is required."
              />
            )}
            {gate && (
              <AttentionItem
                tone="gate"
                icon={<Hand size={16} aria-hidden />}
                title={gate}
                onClick={() => setGateOpen(true)}
              />
            )}
            {failedTasks.map(({ task, goal }) => {
              const attempt = attemptLabel(
                task,
                agents.find((a) => a.id === task.agent_id) ?? null,
              );
              return (
                <AttentionItem
                  key={task.id}
                  tone="fail"
                  icon={<XCircle size={16} aria-hidden />}
                  title={task.name}
                  meta={goal.name}
                  badge={attempt ? <CountChip tone="fail">{attempt}</CountChip> : undefined}
                  detail={failureDetail(task)}
                  onClick={() => {
                    selectTask(task.id);
                    navigate(`${base}/goals`);
                  }}
                />
              );
            })}
          </div>
        )}
      </section>

      {/* ── Automatic recovery ─────────────────────────────────────────── */}
      {automaticRecoveryCount > 0 && (
        <section className={styles.section} aria-label="Recovering automatically">
          <h2 className={styles.sectionTitle + ' label'}>
            Recovering automatically
            <span className={styles.runCount}>{automaticRecoveryCount}</span>
          </h2>
          <div className={styles.attentionList}>
            {plan.provider_waiting && !plan.provider_waiting.needs_attention && (
              <AttentionItem
                tone="run"
                icon={<Clock3 size={16} aria-hidden />}
                title={plan.provider_waiting.safe_message}
                meta={providerLabel(plan.provider_waiting)}
                detail={providerWaitDetail(plan.provider_waiting, now)}
              />
            )}
            {automaticPlanBlock && (
              <AttentionItem
                tone="run"
                icon={<RefreshCw size={16} aria-hidden />}
                title={automaticPlanBlock.explanation}
                meta={humanize(automaticPlanBlock.stage)}
                detail="No operator action is required; the orchestrator owns this recovery loop."
              />
            )}
            {automaticGoalBlocks.map(({ goalId, goal, block }) => (
              <AttentionItem
                key={block.id}
                tone="run"
                icon={<RefreshCw size={16} aria-hidden />}
                title={block.explanation}
                meta={goal?.name ?? goalId}
                detail={`${humanize(block.stage)} · no operator action required`}
              />
            ))}
          </div>
        </section>
      )}

      {planningArtifacts.length > 0 && (
        <section className={styles.section} aria-label="Planning recovery history">
          <h2 className={styles.sectionTitle + ' label'}>Planning recovery history</h2>
          <div className={styles.docs}>
            {planningArtifacts.slice(0, 6).map((artifact) => (
              <div className={styles.recoveryRow} key={`${artifact.purpose}-${artifact.sequence}-${artifact.created_at}`}>
                <CountChip tone={artifact.outcome === 'accepted' ? 'ok' : 'gate'}>
                  {humanize(artifact.outcome)}
                </CountChip>
                <span className={styles.rowTitle}>
                  {humanize(artifact.purpose)} · attempt {artifact.sequence}
                </span>
                <span className={styles.rowMeta}>
                  {artifact.turns_used === null ? 'turn count unavailable' : `${artifact.turns_used} turns`}
                </span>
                {artifact.rejection_reasons.length > 0 && (
                  <span className={styles.recoveryReason}>{artifact.rejection_reasons.join(' · ')}</span>
                )}
              </div>
            ))}
          </div>
        </section>
      )}

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
        {currentGoals.length === 0 ? (
          <p className={styles.empty}>
            No goals yet — agree the roadmap with the reasoner in the chat.
          </p>
        ) : (
          <ul className={styles.rows}>
            {currentGoals
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
                      <CycleEvidenceSummary planId={planId} cycle={cycle} />
                      <CycleReviewPanel planId={planId} cycleId={cycle.id} />
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

function BlockActions({
  planId,
  block,
  onRetry,
  onEdit,
  onReplan,
  pending,
}: {
  planId: string;
  block: PlanBlock;
  onRetry: () => void;
  onEdit: () => void;
  onReplan: () => void;
  pending: boolean;
}) {
  const canRetry = block.legal_resolutions.includes('retry_stage')
    || block.legal_resolutions.includes('wait_and_retry');
  const canEdit = block.legal_resolutions.includes('edit_task') && !!block.task_id;
  const canReplan = block.legal_resolutions.includes('start_replan');
  const canBindProject = block.legal_resolutions.includes('bind_project');

  if (!canRetry && !canEdit && !canReplan && !canBindProject) return null;

  return (
    <>
      {canRetry && (
        <Button size="sm" onClick={onRetry} disabled={pending}>
          <RotateCcw size={12} aria-hidden />
          {block.legal_resolutions.includes('wait_and_retry') ? 'Clear wait & retry' : 'Retry work'}
        </Button>
      )}
      {canEdit && (
        <Button size="sm" onClick={onEdit} disabled={pending}>
          <Pencil size={12} aria-hidden /> Edit task
        </Button>
      )}
      {canReplan && (
        <Button size="sm" onClick={onReplan} disabled={pending}>
          <RefreshCw size={12} aria-hidden /> Start replan
        </Button>
      )}
      {canBindProject && <ProjectBindingAction planId={planId} pending={pending} />}
    </>
  );
}

function ProjectBindingAction({ planId, pending }: { planId: string; pending: boolean }) {
  const { data: projects = [] } = useProjects();
  const bindProject = useBindProject(planId);
  const [projectId, setProjectId] = React.useState('');
  const selected = projectId || projects[0]?.id || '';

  if (projects.length === 0) {
    return <Link className={styles.readinessCta} to="/settings/projects">Create a project</Link>;
  }
  return (
    <span className={styles.bindingAction}>
      <Select
        value={selected}
        onChange={(event) => setProjectId(event.target.value)}
        options={projects.map((project) => ({ value: project.id, label: project.name }))}
        aria-label="Project to bind"
      />
      <Button
        size="sm"
        onClick={() => bindProject.mutate(selected)}
        pending={bindProject.isPending}
        disabled={pending || !selected}
      >
        Bind project
      </Button>
    </span>
  );
}

function retryBlock(
  block: PlanBlock,
  retryTask: (target: { goalId: string; taskId: string }) => void,
  retryPlanningStage: () => void,
) {
  if (block.goal_id && block.task_id) {
    retryTask({ goalId: block.goal_id, taskId: block.task_id });
    return;
  }
  retryPlanningStage();
}

function openBlockedTask(
  block: PlanBlock,
  selectTask: (taskId: string | null) => void,
  navigate: (to: string) => void,
  base: string,
) {
  if (!block.task_id) return;
  selectTask(block.task_id);
  navigate(`${base}/goals`);
}

function providerLabel(waiting: NonNullable<Plan['provider_waiting']>): string {
  const target = waiting.model_id
    ? `${waiting.provider_id} / ${waiting.model_id}`
    : waiting.provider_id;
  return waiting.limit_scope ? `${target} · ${humanize(waiting.limit_scope)}` : target;
}

function providerWaitDetail(
  waiting: NonNullable<Plan['provider_waiting']>,
  now: number,
): string {
  const seconds = Math.max(0, Math.ceil((Date.parse(waiting.retry_at) - now) / 1_000));
  const retry = seconds > 0 ? `retrying automatically in ${seconds}s` : 'automatic retry is due';
  return `${retry} · waiting since ${absTime(waiting.since)} · ${waiting.failure_count} failure${waiting.failure_count === 1 ? '' : 's'}`;
}
