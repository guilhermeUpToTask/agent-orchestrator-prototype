import React, { useState } from 'react';
import { Pencil, Plus } from 'lucide-react';
import {
  useCloneProject,
  useCreateProject,
  useDeleteProject,
  useProbeRepository,
  useProjects,
  useSetForgeBinding,
  useUpdateProject,
} from '../../lib/queries';
import {
  Button,
  Card,
  ConfirmAction,
  Dialog,
  Field,
  Input,
} from '../../components/ui';
import type { ProjectDefinition } from '../../types/ui';
import type { RepositoryBindingKind, RepositoryProbe } from '../../lib/api';
import styles from './Settings.module.css';

/** Project registry: names + repo URLs for project-scoped config. */
export function ProjectsSection() {
  const { data: projects = [] } = useProjects();
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<ProjectDefinition | null>(null);
  const deleteProject = useDeleteProject();

  return (
    <div className={styles.section}>
      <div className={styles.sectionHead}>
        <div>
          <h2 className={styles.sectionTitle}>Projects</h2>
          <p className={styles.sectionDesc}>
            Registered target repositories. Each project id doubles as a config
            scope for project-level settings.
          </p>
        </div>
        <Button variant="primary" onClick={() => setCreating(true)}>
          <Plus size={14} aria-hidden /> Add project
        </Button>
      </div>

      <Card>
        {projects.length === 0 && (
          <div className={styles.empty}>No projects registered.</div>
        )}
        {projects.map((p) => (
          <div key={p.id} className={styles.itemRow}>
            <div className={styles.itemMain}>
              <span className={styles.itemName}>
                {p.name} <span className={styles.itemMeta}>{p.id}</span>
              </span>
              <span className={styles.itemMeta}>{p.repo_url ?? 'no repository URL'}</span>
            </div>
            <div className={styles.itemActions}>
              <Button
                variant="icon"
                size="sm"
                aria-label={`Edit ${p.name}`}
                onClick={() => setEditing(p)}
              >
                <Pencil size={13} aria-hidden />
              </Button>
              <ConfirmAction
                label="Delete"
                tone="danger"
                consequence="Removes the project registration. Its config scope values remain."
                pending={deleteProject.isPending}
                onConfirm={() => deleteProject.mutate(p.id)}
              />
            </div>
          </div>
        ))}
      </Card>

      <ProjectWizard open={creating} onClose={() => setCreating(false)} />
      <ProjectDialog
        open={editing !== null}
        onClose={() => setEditing(null)}
        project={editing}
      />
    </div>
  );
}

function ProjectDialog({
  open,
  onClose,
  project,
}: {
  open: boolean;
  onClose: () => void;
  project: ProjectDefinition | null; // null = create
}) {
  const create = useCreateProject();
  const update = useUpdateProject();
  const [name, setName] = useState('');
  const [repoUrl, setRepoUrl] = useState('');

  const [seededFor, setSeededFor] = useState<string | null>(null);
  const target = project?.id ?? '__new__';
  if (open && seededFor !== target) {
    setSeededFor(target);
    setName(project?.name ?? '');
    setRepoUrl(project?.repo_url ?? '');
  }
  if (!open && seededFor !== null) setSeededFor(null);

  const pending = create.isPending || update.isPending;
  const canSubmit = name.trim() !== '';

  const submit = () => {
    if (!canSubmit) return;
    const body = {
      name: name.trim(),
      repo_url: repoUrl.trim() === '' ? null : repoUrl.trim(),
    };
    if (project === null) create.mutate(body, { onSuccess: onClose });
    else update.mutate({ id: project.id, body }, { onSuccess: onClose });
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      ariaLabel={project === null ? 'Add project' : `Edit project ${project.name}`}
      title={project === null ? 'Add project' : 'Edit project'}
    >
      <div className={styles.form}>
        <Field label="Name" htmlFor="project-name">
          <Input
            id="project-name"
            value={name}
            placeholder="e.g. storefront"
            onChange={(e) => setName(e.target.value)}
          />
        </Field>
        <Field label="Repository URL" htmlFor="project-repo" hint="Optional.">
          <Input
            id="project-repo"
            mono
            value={repoUrl}
            placeholder="https://github.com/org/repo"
            onChange={(e) => setRepoUrl(e.target.value)}
          />
        </Field>
        <div className={styles.formFoot}>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={!canSubmit} pending={pending} onClick={submit}>
            {project === null ? 'Add project' : 'Save changes'}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}


// ─── The repository-choice wizard (P8.1) ─────────────────────────────────────
//
// Two questions, deliberately kept separate. WHERE THE CODE LIVES needs no
// credentials; WHETHER WE CAN OPEN A PULL REQUEST does. Declining the second
// changes only how work comes back — it never substitutes a scratch repository
// for the project the operator named.

type WizardStep = 'where' | 'delivery' | 'confirm';

const WHERE_OPTIONS: {
  kind: RepositoryBindingKind;
  label: string;
  lands: string;
}[] = [
  {
    kind: 'local',
    label: 'Point at a local repository',
    lands: 'Work lands in your own checkout, on a cycle/<id> branch.',
  },
  {
    kind: 'remote',
    label: 'Clone a remote',
    lands: "Work lands in a clone the orchestrator owns, under its state directory.",
  },
  {
    kind: 'scratch',
    label: 'Create an empty one',
    lands: 'A scratch repository. Good for trying the flow; not code you will keep.',
  },
];

const PROBLEM_COPY: Record<string, string> = {
  needs_credentials:
    'That repository needs credentials. Check the URL, or use one you can read anonymously.',
  not_found: 'No repository there — check the URL for a typo.',
  unreachable: 'Could not reach the host.',
  timeout: 'The host did not answer in time.',
};

function ProjectWizard({ open, onClose }: { open: boolean; onClose: () => void }) {
  const create = useCreateProject();
  const clone = useCloneProject();
  const probeRepo = useProbeRepository();
  const bindForge = useSetForgeBinding();

  const [step, setStep] = useState<WizardStep>('where');
  const [name, setName] = useState('');
  const [binding, setBinding] = useState<RepositoryBindingKind>('local');
  const [repoUrl, setRepoUrl] = useState('');
  const [probe, setProbe] = useState<RepositoryProbe | null>(null);
  const [wantsPr, setWantsPr] = useState(false);
  const [repository, setRepository] = useState('');
  const [token, setToken] = useState('');

  const [seeded, setSeeded] = useState(false);
  if (open && !seeded) {
    setSeeded(true);
    setStep('where');
    setName('');
    setBinding('local');
    setRepoUrl('');
    setProbe(null);
    setWantsPr(false);
    setRepository('');
    setToken('');
  }
  if (!open && seeded) setSeeded(false);

  const url = binding === 'scratch' ? null : repoUrl.trim() || null;
  const canLeaveWhere =
    name.trim() !== '' && (binding === 'scratch' || repoUrl.trim() !== '');

  const submit = () => {
    create.mutate(
      { name: name.trim(), repo_url: url, binding },
      {
        onSuccess: (project) => {
          if (binding === 'remote') clone.mutate(project.id);
          if (wantsPr && repository.trim() && token.trim()) {
            bindForge.mutate({
              id: project.id,
              body: { repository: repository.trim(), token: token.trim() },
            });
          }
          onClose();
        },
      },
    );
  };

  const chosen = WHERE_OPTIONS.find((o) => o.kind === binding)!;

  return (
    <Dialog open={open} onClose={onClose} ariaLabel="Add project" title="Add project">
      <div className={styles.form}>
        {step === 'where' && (
          <>
            <Field label="Name" htmlFor="wiz-name">
              <Input
                id="wiz-name"
                value={name}
                placeholder="e.g. storefront"
                onChange={(e) => setName(e.target.value)}
              />
            </Field>
            <fieldset>
              <legend className={styles.sectionDesc}>Where does the code live?</legend>
              {WHERE_OPTIONS.map((option) => (
                <label key={option.kind} className={styles.itemRow}>
                  <input
                    type="radio"
                    name="binding"
                    aria-label={option.label}
                    checked={binding === option.kind}
                    onChange={() => {
                      setBinding(option.kind);
                      setProbe(null);
                    }}
                  />
                  <span className={styles.itemMain}>
                    <span className={styles.itemName}>{option.label}</span>
                    <span className={styles.itemMeta}>{option.lands}</span>
                  </span>
                </label>
              ))}
            </fieldset>
            {binding !== 'scratch' && (
              <Field
                label="Repository URL"
                htmlFor="wiz-repo"
                hint={
                  binding === 'local'
                    ? 'A path to a git repository on this machine.'
                    : 'An https:// or ssh:// URL.'
                }
              >
                <Input
                  id="wiz-repo"
                  mono
                  value={repoUrl}
                  placeholder={
                    binding === 'local' ? '/home/you/code/app' : 'https://github.com/org/repo.git'
                  }
                  onChange={(e) => {
                    setRepoUrl(e.target.value);
                    setProbe(null);
                  }}
                />
              </Field>
            )}
            {binding === 'remote' && (
              <div className={styles.formFoot}>
                <Button
                  pending={probeRepo.isPending}
                  disabled={repoUrl.trim() === ''}
                  onClick={() =>
                    probeRepo.mutate(repoUrl.trim(), { onSuccess: (r) => setProbe(r) })
                  }
                >
                  Check
                </Button>
              </div>
            )}
            {probe && (
              <p className={styles.itemMeta}>
                {probe.reachable
                  ? `Reachable. Default branch ${probe.default_branch ?? 'unknown'}.`
                  : (PROBLEM_COPY[probe.problem_kind ?? ''] ?? probe.problem)}
              </p>
            )}
            <div className={styles.formFoot}>
              <Button onClick={onClose}>Cancel</Button>
              <Button
                variant="primary"
                disabled={!canLeaveWhere}
                onClick={() => setStep('delivery')}
              >
                Continue
              </Button>
            </div>
          </>
        )}

        {step === 'delivery' && (
          <>
            <fieldset>
              <legend className={styles.sectionDesc}>How should the work come back?</legend>
              <label className={styles.itemRow}>
                <input
                  type="radio"
                  name="delivery"
                  aria-label="Leave it on a branch"
                  checked={!wantsPr}
                  onChange={() => setWantsPr(false)}
                />
                <span className={styles.itemMain}>
                  <span className={styles.itemName}>Leave it on a branch</span>
                  <span className={styles.itemMeta}>
                    No credentials needed. You get the branch name and the command to see it.
                  </span>
                </span>
              </label>
              <label className={styles.itemRow}>
                <input
                  type="radio"
                  name="delivery"
                  aria-label="Open a pull request for me"
                  checked={wantsPr}
                  onChange={() => setWantsPr(true)}
                />
                <span className={styles.itemMain}>
                  <span className={styles.itemName}>Open a pull request for me</span>
                  <span className={styles.itemMeta}>
                    Needs a GitHub token with write access. The orchestrator opens the pull
                    request; it never merges one.
                  </span>
                </span>
              </label>
            </fieldset>
            {wantsPr && (
              <>
                <Field label="GitHub repository" htmlFor="wiz-forge-repo" hint="owner/repo">
                  <Input
                    id="wiz-forge-repo"
                    mono
                    value={repository}
                    placeholder="acme/widgets"
                    onChange={(e) => setRepository(e.target.value)}
                  />
                </Field>
                <Field
                  label="Token"
                  htmlFor="wiz-forge-token"
                  hint="Stored encrypted. Verified against that repository before it is saved."
                >
                  <Input
                    id="wiz-forge-token"
                    mono
                    value={token}
                    onChange={(e) => setToken(e.target.value)}
                  />
                </Field>
              </>
            )}
            <div className={styles.formFoot}>
              <Button onClick={() => setStep('where')}>Back</Button>
              <Button variant="primary" onClick={() => setStep('confirm')}>
                Continue
              </Button>
            </div>
          </>
        )}

        {step === 'confirm' && (
          <>
            <p className={styles.sectionDesc}>{chosen.lands}</p>
            <p className={styles.itemMeta}>
              {wantsPr
                ? `Finished work arrives as a pull request in ${repository || 'the repository you named'}.`
                : binding === 'remote'
                  ? 'Finished work stays on a cycle branch in the orchestrator\'s clone; the console shows the path and the command to fetch it.'
                  : 'Finished work stays on a cycle branch you can diff locally.'}
            </p>
            <div className={styles.formFoot}>
              <Button onClick={() => setStep('delivery')}>Back</Button>
              <Button variant="primary" pending={create.isPending} onClick={submit}>
                Create project
              </Button>
            </div>
          </>
        )}
      </div>
    </Dialog>
  );
}
