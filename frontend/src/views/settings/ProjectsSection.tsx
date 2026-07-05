import React, { useState } from 'react';
import { Pencil, Plus } from 'lucide-react';
import {
  useCreateProject,
  useDeleteProject,
  useProjects,
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

      <ProjectDialog open={creating} onClose={() => setCreating(false)} project={null} />
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
