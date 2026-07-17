import React, { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { ChevronRight, Plus } from 'lucide-react';
import { useCreatePlan, useCreateProject, usePlans, useProjects } from '../lib/queries';
import { StatusBadge } from '../components/StatusBadge';
import { Button, TextArea } from '../components/ui';
import { tokens } from '../styles/tokens';
import styles from './Overview.module.css';

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

  const [composing, setComposing] = useState(false);
  const [brief, setBrief] = useState('');
  const [projectId, setProjectId] = useState('');
  const [projectName, setProjectName] = useState('');
  const [repoUrl, setRepoUrl] = useState('');

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
        <section className={styles.section} aria-label="Open project plan">
          <h2 className={styles.sectionTitle + ' label'}>Open project plan — the brief</h2>
          <label className="label" htmlFor="new-plan-project">Project</label>
          {projects.length > 0 ? (
            <select
              id="new-plan-project"
              value={projectId || projects[0]?.id || ""}
              onChange={(event) => setProjectId(event.target.value)}
              style={{ width: "100%", marginBottom: 8 }}
            >
              {projects.map((project) => (
                <option key={project.id} value={project.id}>{project.name}</option>
              ))}
            </select>
          ) : (
            <div style={{ display: 'grid', gap: 8, marginBottom: 12 }}>
              <input
                id="new-plan-project"
                value={projectName}
                onChange={(event) => setProjectName(event.target.value)}
                placeholder="Project name"
              />
              <input
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
            </div>
          )}
          <TextArea
            value={brief}
            onChange={(e) => setBrief(e.target.value)}
            placeholder="Describe what you want built. The discovery conversation starts from this brief."
            rows={4}
            autoFocus
          />
          <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
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
        </section>
      )}

      <section className={styles.section} aria-label="All plans">
        <h2 className={styles.sectionTitle + ' label'}>All plans</h2>
        {isLoading ? (
          <div aria-busy="true">
            {[0, 1, 2].map((i) => (
              <div key={i} className="skeleton" style={{ height: 40, marginBottom: 6 }} />
            ))}
          </div>
        ) : plans.length === 0 ? (
          <p className={styles.empty}>No project plans yet — open one to begin.</p>
        ) : (
          <ul className={styles.rows}>
            {plans.map((p) => (
              <li key={p.id}>
                <Link className={styles.row} to={`/plans/${encodeURIComponent(p.id)}`}>
                  <StatusBadge domain="plan" value={p.status} bare />
                  <span className={styles.rowTitle} style={{ fontFamily: tokens.fontMono }}>
                    {p.id}
                  </span>
                  {(p.paused || p.pause_requested) && (
                    <span
                      style={{
                        fontSize: 10, fontWeight: 600, letterSpacing: '0.05em',
                        padding: '2px 7px', borderRadius: 5,
                        color: 'var(--gate-text, #b7791f)',
                        background: 'var(--gate-bg, color-mix(in srgb, #f5a623 14%, transparent))',
                        border: '1px solid color-mix(in srgb, #f5a623 45%, transparent)',
                      }}
                    >
                      {p.pause_requested ? "PAUSE REQUESTED" : "PAUSED"}
                    </span>
                  )}
                  <span className={styles.rowMeta}>
                    iter {p.iteration} · v{p.version}
                    {p.claimed_by && ` · claimed by ${p.claimed_by}`}
                  </span>
                  <ChevronRight size={14} className={styles.rowChev} aria-hidden />
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
