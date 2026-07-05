import React, { useState } from 'react';
import { Check, Pencil, Plus, X } from 'lucide-react';
import {
  useCreateModel,
  useCreateProvider,
  useDeleteModel,
  useDeleteProvider,
  useProviders,
  useRenameModel,
  useUpdateProvider,
} from '../../lib/queries';
import {
  Button,
  Card,
  ConfirmAction,
  Dialog,
  Field,
  Input,
} from '../../components/ui';
import type { IaModel, ModelProvider } from '../../types/ui';
import styles from './Settings.module.css';

/**
 * The providers catalog: base_url + envelope-encrypted API key per provider,
 * with its model rows nested. Keys travel once on create/rotate and are
 * never readable again — the rows only ever carry the api_key_ref URI.
 */
export function ProvidersSection() {
  const { data: providers = [] } = useProviders();
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<ModelProvider | null>(null);

  return (
    <div className={styles.section}>
      <div className={styles.sectionHead}>
        <div>
          <h2 className={styles.sectionTitle}>Providers &amp; models</h2>
          <p className={styles.sectionDesc}>
            LLM endpoints the reasoner can resolve through. API keys are stored
            envelope-encrypted and never shown again.
          </p>
        </div>
        <Button variant="primary" onClick={() => setCreating(true)}>
          <Plus size={14} aria-hidden /> Add provider
        </Button>
      </div>

      {providers.length === 0 && (
        <div className={styles.empty}>
          No providers registered. Add one here, or seed a preset with{' '}
          <code>orchestrate seed demo --provider …</code>.
        </div>
      )}

      {providers.map((p) => (
        <ProviderCard key={p.id} provider={p} onEdit={() => setEditing(p)} />
      ))}

      <ProviderDialog
        open={creating}
        onClose={() => setCreating(false)}
        provider={null}
      />
      <ProviderDialog
        open={editing !== null}
        onClose={() => setEditing(null)}
        provider={editing}
      />
    </div>
  );
}

function ProviderCard({
  provider,
  onEdit,
}: {
  provider: ModelProvider;
  onEdit: () => void;
}) {
  const deleteProvider = useDeleteProvider();

  return (
    <Card
      title={
        <>
          {provider.name}
          <span className={styles.itemMeta}>{provider.id}</span>
        </>
      }
      actions={
        <>
          <Button size="sm" onClick={onEdit}>
            <Pencil size={12} aria-hidden /> Edit
          </Button>
          <ConfirmAction
            label="Delete"
            tone="danger"
            consequence="Removes the provider, its models and its stored key. Blocked while a model is referenced by config."
            pending={deleteProvider.isPending}
            onConfirm={() => deleteProvider.mutate(provider.id)}
          />
        </>
      }
    >
      <div className={styles.kv}>
        <span className="label">base url</span>
        <span className={styles.mono}>{provider.base_url || '—'}</span>
        <span className="label">api key</span>
        <span className={styles.mono}>{provider.api_key_ref} (encrypted)</span>
      </div>

      <ModelList provider={provider} />
    </Card>
  );
}

/* ── Models nested under their provider ───────────────────────────────────── */

function ModelList({ provider }: { provider: ModelProvider }) {
  const createModel = useCreateModel();
  const [newName, setNewName] = useState('');

  const submitNew = () => {
    const name = newName.trim();
    if (!name) return;
    createModel.mutate(
      { providerId: provider.id, name },
      { onSuccess: () => setNewName('') },
    );
  };

  return (
    <div>
      <div className="label" style={{ marginBottom: 6 }}>
        Models
      </div>
      {(provider.models ?? []).map((m) => (
        <ModelRow key={m.id} model={m} />
      ))}
      <div className={styles.inlineForm} style={{ marginTop: 8 }}>
        <Input
          mono
          placeholder="Provider model string, e.g. gpt-4o-mini"
          value={newName}
          aria-label={`Add model to ${provider.name}`}
          onChange={(e) => setNewName(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && submitNew()}
        />
        <Button size="sm" pending={createModel.isPending} onClick={submitNew}>
          <Plus size={12} aria-hidden /> Add model
        </Button>
      </div>
    </div>
  );
}

function ModelRow({ model }: { model: IaModel }) {
  const rename = useRenameModel();
  const deleteModel = useDeleteModel();
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(model.name);

  const submit = () => {
    const next = name.trim();
    if (!next || next === model.name) {
      setEditing(false);
      setName(model.name);
      return;
    }
    rename.mutate(
      { modelId: model.id, name: next },
      { onSuccess: () => setEditing(false) },
    );
  };

  return (
    <div className={styles.itemRow}>
      <div className={styles.itemMain}>
        {editing ? (
          <div className={styles.inlineForm}>
            <Input
              mono
              value={name}
              aria-label={`Rename model ${model.id}`}
              autoFocus
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') submit();
                if (e.key === 'Escape') {
                  setEditing(false);
                  setName(model.name);
                }
              }}
            />
            <Button variant="icon" size="sm" aria-label="Save model name" onClick={submit}>
              <Check size={13} aria-hidden />
            </Button>
            <Button
              variant="icon"
              size="sm"
              aria-label="Cancel rename"
              onClick={() => {
                setEditing(false);
                setName(model.name);
              }}
            >
              <X size={13} aria-hidden />
            </Button>
          </div>
        ) : (
          <>
            <span className={styles.itemName}>{model.name}</span>
            <span className={styles.itemMeta}>{model.id}</span>
          </>
        )}
      </div>
      {!editing && (
        <div className={styles.itemActions}>
          <Button
            variant="icon"
            size="sm"
            aria-label={`Rename ${model.name}`}
            onClick={() => setEditing(true)}
          >
            <Pencil size={13} aria-hidden />
          </Button>
          <ConfirmAction
            label="Delete"
            tone="danger"
            consequence="Removes the model row. Blocked while a config key references it."
            pending={deleteModel.isPending}
            onConfirm={() => deleteModel.mutate(model.id)}
          />
        </div>
      )}
    </div>
  );
}

/* ── Create / edit provider dialog ────────────────────────────────────────── */

function ProviderDialog({
  open,
  onClose,
  provider,
}: {
  open: boolean;
  onClose: () => void;
  provider: ModelProvider | null; // null = create
}) {
  const create = useCreateProvider();
  const update = useUpdateProvider();
  const [name, setName] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [apiKey, setApiKey] = useState('');

  // Reset the form each time the dialog opens for a target.
  const [seededFor, setSeededFor] = useState<string | null>(null);
  const target = provider?.id ?? '__new__';
  if (open && seededFor !== target) {
    setSeededFor(target);
    setName(provider?.name ?? '');
    setBaseUrl(provider?.base_url ?? '');
    setApiKey('');
  }
  if (!open && seededFor !== null) setSeededFor(null);

  const pending = create.isPending || update.isPending;
  const canSubmit =
    name.trim() !== '' && baseUrl.trim() !== '' && (provider !== null || apiKey !== '');

  const submit = () => {
    if (!canSubmit) return;
    if (provider === null) {
      create.mutate(
        { name: name.trim(), base_url: baseUrl.trim(), api_key: apiKey },
        { onSuccess: onClose },
      );
    } else {
      update.mutate(
        {
          id: provider.id,
          body: {
            name: name.trim(),
            base_url: baseUrl.trim(),
            api_key: apiKey === '' ? null : apiKey,
          },
        },
        { onSuccess: onClose },
      );
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      ariaLabel={provider === null ? 'Add provider' : `Edit provider ${provider.name}`}
      title={provider === null ? 'Add provider' : 'Edit provider'}
    >
      <div className={styles.form}>
        <Field label="Name" htmlFor="provider-name">
          <Input
            id="provider-name"
            value={name}
            placeholder="e.g. OpenRouter"
            onChange={(e) => setName(e.target.value)}
          />
        </Field>
        <Field
          label="Base URL"
          htmlFor="provider-base-url"
          hint="OpenAI-compatible endpoint, e.g. https://openrouter.ai/api/v1"
        >
          <Input
            id="provider-base-url"
            mono
            value={baseUrl}
            placeholder="https://…"
            onChange={(e) => setBaseUrl(e.target.value)}
          />
        </Field>
        <Field
          label={provider === null ? 'API key' : 'Rotate API key'}
          htmlFor="provider-api-key"
          hint={
            provider === null
              ? 'Stored envelope-encrypted; it cannot be read back after saving.'
              : 'Leave blank to keep the current key. A new value replaces it permanently.'
          }
        >
          <Input
            id="provider-api-key"
            mono
            type="password"
            autoComplete="off"
            value={apiKey}
            placeholder={provider === null ? 'sk-…' : '••••••••  (unchanged)'}
            onChange={(e) => setApiKey(e.target.value)}
          />
        </Field>
        <div className={styles.formFoot}>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={!canSubmit} pending={pending} onClick={submit}>
            {provider === null ? 'Add provider' : 'Save changes'}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
