import React, { useState } from 'react';
import { Pencil, Plus } from 'lucide-react';
import {
  useCapabilities,
  useCreateCapability,
  useDeleteCapability,
  useUpdateCapability,
} from '../../lib/queries';
import {
  Button,
  Card,
  ConfirmAction,
  Dialog,
  Field,
  Input,
} from '../../components/ui';
import type { Capability } from '../../types/ui';
import styles from './Settings.module.css';

const slugify = (s: string) =>
  s
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');

/**
 * Capability vocabulary: tasks require capability ids; agents advertise
 * them. The id is the contract — it appears in plans, so it is immutable
 * after creation.
 */
export function CapabilitiesSection() {
  const { data: capabilities = [] } = useCapabilities();
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<Capability | null>(null);
  const deleteCapability = useDeleteCapability();

  return (
    <div className={styles.section}>
      <div className={styles.sectionHead}>
        <div>
          <h2 className={styles.sectionTitle}>Capabilities</h2>
          <p className={styles.sectionDesc}>
            The shared vocabulary between tasks and agents: a task requires
            capability ids, an agent covers them.
          </p>
        </div>
        <Button variant="primary" onClick={() => setCreating(true)}>
          <Plus size={14} aria-hidden /> Add capability
        </Button>
      </div>

      <Card>
        {capabilities.length === 0 && (
          <div className={styles.empty}>No capabilities registered.</div>
        )}
        {capabilities.map((c) => (
          <div key={c.id} className={styles.itemRow}>
            <div className={styles.itemMain}>
              <span className={styles.itemName}>
                {c.name} <span className={styles.itemMeta}>{c.id}</span>
              </span>
              {c.description && <span className={styles.itemMeta}>{c.description}</span>}
              {(c.tools ?? []).length > 0 && (
                <span className={styles.chips}>
                  {(c.tools ?? []).map((t) => (
                    <span key={t} className={styles.chip}>
                      {t}
                    </span>
                  ))}
                </span>
              )}
            </div>
            <div className={styles.itemActions}>
              <Button
                variant="icon"
                size="sm"
                aria-label={`Edit ${c.name}`}
                onClick={() => setEditing(c)}
              >
                <Pencil size={13} aria-hidden />
              </Button>
              <ConfirmAction
                label="Delete"
                tone="danger"
                consequence="Removes the capability. Blocked while an agent or an active plan references it."
                pending={deleteCapability.isPending}
                onConfirm={() => deleteCapability.mutate(c.id)}
              />
            </div>
          </div>
        ))}
      </Card>

      <CapabilityDialog
        open={creating}
        onClose={() => setCreating(false)}
        capability={null}
      />
      <CapabilityDialog
        open={editing !== null}
        onClose={() => setEditing(null)}
        capability={editing}
      />
    </div>
  );
}

function CapabilityDialog({
  open,
  onClose,
  capability,
}: {
  open: boolean;
  onClose: () => void;
  capability: Capability | null; // null = create
}) {
  const create = useCreateCapability();
  const update = useUpdateCapability();

  const [id, setId] = useState('');
  const [idTouched, setIdTouched] = useState(false);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [tools, setTools] = useState('');

  const [seededFor, setSeededFor] = useState<string | null>(null);
  const target = capability?.id ?? '__new__';
  if (open && seededFor !== target) {
    setSeededFor(target);
    setId(capability?.id ?? '');
    setIdTouched(capability !== null);
    setName(capability?.name ?? '');
    setDescription(capability?.description ?? '');
    setTools((capability?.tools ?? []).join(', '));
  }
  if (!open && seededFor !== null) setSeededFor(null);

  const pending = create.isPending || update.isPending;
  const canSubmit = id.trim() !== '' && name.trim() !== '';

  const submit = () => {
    if (!canSubmit) return;
    const body: Capability = {
      id: id.trim(),
      name: name.trim(),
      description,
      tools: tools
        .split(',')
        .map((t) => t.trim())
        .filter(Boolean),
    };
    if (capability === null) create.mutate(body, { onSuccess: onClose });
    else update.mutate({ id: capability.id, cap: body }, { onSuccess: onClose });
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      ariaLabel={capability === null ? 'Add capability' : `Edit capability ${capability.name}`}
      title={capability === null ? 'Add capability' : 'Edit capability'}
    >
      <div className={styles.form}>
        <Field label="Name" htmlFor="cap-name">
          <Input
            id="cap-name"
            value={name}
            placeholder="e.g. Backend"
            onChange={(e) => {
              setName(e.target.value);
              if (!idTouched) setId(slugify(e.target.value));
            }}
          />
        </Field>
        <Field
          label="Id"
          htmlFor="cap-id"
          hint={
            capability === null
              ? 'The identifier tasks and agents reference. Immutable after creation.'
              : 'Ids are referenced by plans and agents — they cannot change.'
          }
        >
          <Input
            id="cap-id"
            mono
            value={id}
            disabled={capability !== null}
            placeholder="backend"
            onChange={(e) => {
              setId(e.target.value);
              setIdTouched(true);
            }}
          />
        </Field>
        <Field label="Description" htmlFor="cap-description">
          <Input
            id="cap-description"
            value={description}
            placeholder="What work this capability covers."
            onChange={(e) => setDescription(e.target.value)}
          />
        </Field>
        <Field label="Tools" htmlFor="cap-tools" hint="Comma-separated tool names.">
          <Input
            id="cap-tools"
            mono
            value={tools}
            placeholder="pytest, ruff"
            onChange={(e) => setTools(e.target.value)}
          />
        </Field>
        <div className={styles.formFoot}>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={!canSubmit} pending={pending} onClick={submit}>
            {capability === null ? 'Add capability' : 'Save changes'}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
