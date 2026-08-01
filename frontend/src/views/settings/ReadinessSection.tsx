import React from 'react';
import { AlertTriangle, CheckCircle2, RefreshCw, XCircle } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useProjectReadiness, useProjects, useReadiness } from '../../lib/queries';
import { Button, Card, CountChip, ErrorState, Field, Select } from '../../components/ui';
import styles from './Settings.module.css';

const CHECK_ROUTES: Record<string, string> = {
  reasoner: '/settings/reasoner',
  runner: '/settings/runner',
  binaries: '/settings/runner',
  secrets: '/settings/providers',
  catalog: '/settings/providers',
  projects: '/settings/projects',
  workers: '/settings/runner',
};

/** One first-mile checklist backed by GET /api/readiness. */
export function ReadinessSection() {
  const readiness = useReadiness();
  const { data: projects = [] } = useProjects();
  const [projectId, setProjectId] = React.useState('');
  const selectedProjectId = projectId || projects[0]?.id || null;
  const projectReadiness = useProjectReadiness(selectedProjectId);

  return (
    <div className={styles.section}>
      <div className={styles.sectionHead}>
        <div>
          <h2 className={styles.sectionTitle}>Launch readiness</h2>
          <p className={styles.sectionDesc}>
            One checklist for the API, worker, runtime, catalog, secrets, and repository binding.
            Fix failed checks before opening a long-running cycle.
          </p>
        </div>
        <Button onClick={() => readiness.refetch()} pending={readiness.isFetching}>
          <RefreshCw size={13} aria-hidden /> Recheck
        </Button>
      </div>

      {readiness.error ? (
        <ErrorState message={(readiness.error as Error).message} onRetry={() => readiness.refetch()} />
      ) : readiness.isLoading || !readiness.data ? (
        <div className="skeleton" style={{ height: 260 }} />
      ) : (
        <Card
          title={readiness.data.ok ? 'Ready to launch Tier 0' : 'Setup needs attention'}
          actions={(
            <CountChip tone={readiness.data.ok ? 'ok' : 'fail'}>
              {readiness.data.ok ? 'ready' : 'not ready'}
            </CountChip>
          )}
        >
          <div className={styles.readinessList}>
            {readiness.data.checks.map((check) => {
              const Icon = check.status === 'ok'
                ? CheckCircle2
                : check.status === 'warn'
                  ? AlertTriangle
                  : XCircle;
              return (
                <div className={styles.readinessRow} key={check.name}>
                  <Icon size={15} aria-hidden className={styles[`readiness_${check.status}`]} />
                  <div className={styles.readinessMain}>
                    <span className={styles.itemName}>{humanize(check.name)}</span>
                    <span className={styles.itemMeta}>{check.detail}</span>
                  </div>
                  {check.status !== 'ok' && CHECK_ROUTES[check.name] && (
                    <Link className={styles.readinessLink} to={CHECK_ROUTES[check.name]}>
                      Configure
                    </Link>
                  )}
                </div>
              );
            })}
          </div>
        </Card>
      )}

      <Card title="Repository readiness">
        {projects.length === 0 ? (
          <div className={styles.empty}>
            Create a project first. Scratch projects need no repository URL; bound projects must name an existing Git repository.
          </div>
        ) : (
          <>
            <Field label="Project" htmlFor="readiness-project">
              <Select
                id="readiness-project"
                value={selectedProjectId ?? ''}
                onChange={(event) => setProjectId(event.target.value)}
                options={projects.map((project) => ({ value: project.id, label: project.name }))}
              />
            </Field>
            {projectReadiness.isLoading ? (
              <div className="skeleton" style={{ height: 84 }} />
            ) : projectReadiness.data ? (
              <div className={styles.kv}>
                <span className="label">binding</span>
                <span className={styles.mono}>{projectReadiness.data.binding}</span>
                <span className="label">resolved path</span>
                <span className={styles.mono}>{projectReadiness.data.resolved_path ?? '—'}</span>
                <span className="label">repository</span>
                <span className={styles.mono}>
                  {projectReadiness.data.is_git_repository
                    ? `${projectReadiness.data.default_branch ?? 'unknown branch'} · ${projectReadiness.data.clean ? 'clean' : 'uncommitted changes present'}`
                    : projectReadiness.data.problem ?? 'not materialized yet'}
                </span>
              </div>
            ) : null}
          </>
        )}
      </Card>
    </div>
  );
}

function humanize(value: string): string {
  return value.replace(/_/g, ' ').replace(/^./, (letter) => letter.toUpperCase());
}
