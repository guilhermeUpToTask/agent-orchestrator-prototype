import React, { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Clock, Plus } from 'lucide-react';
import { useCreatePlan, useCreateProject, usePlans, useProjects } from '../lib/queries';
import { errorDetail } from '../lib/toast';
import { absTime, relTime, useNow } from '../lib/time';
import { StatusBadge } from '../components/StatusBadge';
import { Button, Card, CountChip, ErrorState, Field, Input, Select, TextArea } from '../components/ui';
import { PLAN_STATUS } from '../styles/tokens';
import type { PlanStatus } from '../types/ui';
import styles from './Overview.module.css';

/**
 * Per-row attention signal. `PlanSummary` (the cheap list read model) carries
 * no task-level data, so a failed-task count isn't derivable here without an
 * N+1 fetch — severity comes from the shared PLAN_STATUS map so a status
 * reclassified there (paused, blocked, …) can never read as "on track".
 */
function AttentionChip({ status }: { status: PlanStatus }) {
  const kind = PLAN_STATUS[status].kind;
  if (kind === 'gate' || kind === 'fail') return <StatusBadge domain="plan" value={status} />;
  return <CountChip tone="ok">on track</CountChip>;
}

/**
 * The entry point: every plan in the orchestrator, newest activity first,
 * plus the "new plan" composer. Creating a plan lands you in its shell with
 * the discovery chat open.
 */
export function PlansView() {
  const { data: plans = [], isLoading, error, refetch } = usePlans();
  const createPlan = useCreatePlan();
  const createProject = useCreateProject();
  const { data: projects = [] } = useProjects();
  const navigate = useNavigate();
  const now = useNow(30_000);

  const [composing, setComposing] = useState(false);
  const [brief, setBrief] = useState('');
  const [projectId, setProjectId] = useState('');
  const [projectName, setProjectName] = useState('');
  const [repoUrl, setRepoUrl] = useState('');

  const projectNameFor = (id: string | null) =>
    projects.find((p) => p.id === id)?.name ?? null;

  const submit = () => {
    const selectedProject = projectId || projects[0]?.id;
    if (!brief.trim() || !selectedProject || createPlan.isPending) return;
    createPlan.mutate({ brief, projectId: selectedProject }, {
      onSuccess: ({ plan_id }) => {
        setComposing(false);
        setBrief('');
        navigate(`/plans/${encodeURIComponent(plan_id)}`);
      },
    });
  };

  const createProjectInline = () => {
    if (!projectName.trim() || createProject.isPending) return;
    createProject.mutate(
      { name: projectName.trim(), repo_url: repoUrl.trim() || null },
      {
        onSuccess: (project) => {
          setProjectId(project.id);
          setProjectName('');
          setRepoUrl('');
        },
      },
    );
  };

  if (error && plans.length === 0) {
    return (
      <div className={styles.page}>
        <ErrorState
          message={`${(error as Error).message}. Check that the API server is running at ${import.meta.env.VITE_API_URL ?? 'http://localhost:8000'}, then retry.`}
          onRetry={() => refetch()}
        />
      </div>
    );
  }

  return (
    <div className={styles.page}>
      <header className={styles.phaseHeader}>
        <div className={styles.phaseTitleRow}>
          <h1 className={styles.phaseTitle}>Plans</h1>
          <Button variant="primary" onClick={() => setComposing((v) => !v)}>
            <Plus size={14} aria-hidden /> Open project plan
          </Button>
        </div>
        <p className={styles.phaseGoal}>
          Each project owns one long-lived plan. Review intent and cycle drafts,
          run verified work, publish a cycle, then return to idle.
        </p>
      </header>

      {composing && (
        <Card title="Open project plan — the brief">
          <Field label="Project" htmlFor="new-plan-project">
            {projects.length > 0 ? (
              <Select
                id="new-plan-project"
                value={projectId || projects[0]?.id || ''}
                onChange={(event) => setProjectId(event.target.value)}
                options={projects.map((project) => ({ value: project.id, label: project.name }))}
              />
            ) : (
              <div style={{ display: 'grid', gap: 8 }}>
                <Input
                  id="new-plan-project"
                  value={projectName}
                  onChange={(event) => setProjectName(event.target.value)}
                  placeholder="Project name"
                />
                <Input
                  value={repoUrl}
                  onChange={(event) => setRepoUrl(event.target.value)}
                  placeholder="Repository URL (optional)"
                />
                <Button
                  onClick={createProjectInline}
                  disabled={!projectName.trim()}
                  pending={createProject.isPending}
                >
                  Create project
                </Button>
                {createProject.error && (
                  <span className={styles.errorBody} role="alert">
                    {errorDetail(createProject.error)}
                  </span>
                )}
              </div>
            )}
          </Field>

          <Field
            label="Brief"
            hint="The discovery conversation starts from this brief."
            error={createPlan.error ? errorDetail(createPlan.error) : undefined}
          >
            <TextArea
              value={brief}
              onChange={(e) => setBrief(e.target.value)}
              placeholder="Describe what you want built."
              rows={4}
              autoFocus
            />
          </Field>

          <div style={{ display: 'flex', gap: 8 }}>
            <Button
              variant="primary"
              onClick={submit}
              disabled={!brief.trim() || projects.length === 0}
              pending={createPlan.isPending}
            >
              Create &amp; analyze brief
            </Button>
            <Button onClick={() => setComposing(false)}>Cancel</Button>
          </div>
        </Card>
      )}

      <section className={styles.section} aria-label="All plans">
        <h2 className={styles.sectionTitle + ' label'}>All plans</h2>
        {isLoading ? (
          <div className={styles.planList} aria-busy="true" aria-label="Loading plans">
            {[0, 1, 2].map((i) => (
              <div key={i} className="skeleton" style={{ height: 56 }} />
            ))}
          </div>
        ) : plans.length === 0 ? (
          <p className={styles.empty}>No project plans yet — open one to begin.</p>
        ) : (
          <div className={styles.planList}>
            {plans.map((p) => (
              <Link key={p.id} className={styles.planRow} to={`/plans/${encodeURIComponent(p.id)}`}>
                <StatusBadge domain="plan" value={p.status} bare />

                <span className={styles.planTitle}>
                  <span className={styles.planTitleName}>
                    {projectNameFor(p.project_id) ?? 'Unassigned project'}
                  </span>
                  <span className={styles.planTitleId}>{p.id}</span>
                  {p.paused ? (
                    <StatusBadge domain="plan" value="paused" bare />
                  ) : p.pause_requested ? (
                    <CountChip tone="gate">pause requested</CountChip>
                  ) : null}
                </span>

                <AttentionChip status={p.status} />

                <span className={styles.rowMeta}>
                  iter {p.iteration} · v{p.version}
                  {p.claimed_by && ` · claimed by ${p.claimed_by}`}
                </span>

                <span className={styles.planActivity} title={absTime(p.updated_at)}>
                  <Clock size={11} aria-hidden /> {relTime(p.updated_at, now)}
                </span>
              </Link>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
