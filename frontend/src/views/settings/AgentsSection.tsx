import React, { useState } from 'react';
import { Pencil, Plus, Star } from 'lucide-react';
import {
  useAgents,
  useCapabilities,
  useCreateAgent,
  useDefaultAgent,
  useDeleteAgent,
  useModels,
  useProviders,
  useSetDefaultAgent,
  useUpdateAgent,
} from '../../lib/queries';
import {
  Button,
  Card,
  ConfirmAction,
  Dialog,
  Field,
  Input,
  Select,
  TextArea,
} from '../../components/ui';
import type { AgentBody, AgentSpec, FailureKind, RetryPolicy } from '../../types/ui';
import styles from './Settings.module.css';

const FAILURE_KINDS: FailureKind[] = [
  'connection_error',
  'rate_limit',
  'timeout',
  'tool_error',
  'token_limit',
  'auth_error',
];

const RUNTIME_TYPES = [
  { value: 'pi', label: 'pi — pi-mono CLI (default)' },
  { value: 'claude', label: 'claude — Claude Code CLI' },
  { value: 'gemini', label: 'gemini — Gemini CLI' },
  { value: 'dry-run', label: 'dry-run — simulated, no binary, no key' },
];

const RETRY_DEFAULTS: Required<RetryPolicy> = {
  max_attempts: 3,
  initial_backoff_seconds: 2.0,
  backoff_multiplier: 2.0,
  max_backoff_seconds: 60.0,
  non_retryable_kinds: [],
};

/**
 * The agent roster: capability bindings drive match_agent at planning time;
 * the default agent catches every task no specialist covers.
 */
export function AgentsSection() {
  const { data: agents = [] } = useAgents();
  const { data: defaultAgent } = useDefaultAgent();
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<AgentSpec | null>(null);

  return (
    <div className={styles.section}>
      <div className={styles.sectionHead}>
        <div>
          <h2 className={styles.sectionTitle}>Agents</h2>
          <p className={styles.sectionDesc}>
            Tasks bind to the first agent whose capabilities cover their
            requirements; anything uncovered falls back to the default agent.
          </p>
        </div>
        <Button variant="primary" onClick={() => setCreating(true)}>
          <Plus size={14} aria-hidden /> Add agent
        </Button>
      </div>

      {agents.length === 0 && (
        <div className={styles.empty}>
          No agents registered — tasks cannot be bound until one exists.
        </div>
      )}

      {agents.map((a) => (
        <AgentCard
          key={a.id}
          agent={a}
          isDefault={defaultAgent?.agent_id === a.id}
          onEdit={() => setEditing(a)}
        />
      ))}

      <AgentDialog open={creating} onClose={() => setCreating(false)} agent={null} />
      <AgentDialog
        open={editing !== null}
        onClose={() => setEditing(null)}
        agent={editing}
      />
    </div>
  );
}

function AgentCard({
  agent,
  isDefault,
  onEdit,
}: {
  agent: AgentSpec;
  isDefault: boolean;
  onEdit: () => void;
}) {
  const setDefault = useSetDefaultAgent();
  const deleteAgent = useDeleteAgent();
  const retry = { ...RETRY_DEFAULTS, ...agent.default_retry };

  return (
    <Card
      title={
        <>
          {agent.name}
          <span className={styles.itemMeta}>{agent.id}</span>
          {isDefault && <span className={styles.defaultBadge}>default</span>}
        </>
      }
      actions={
        <>
          <Button
            size="sm"
            disabled={isDefault}
            pending={setDefault.isPending}
            title={isDefault ? 'Already the default agent' : 'Make this the fallback agent'}
            onClick={() => setDefault.mutate(agent.id)}
          >
            <Star size={12} aria-hidden /> Set default
          </Button>
          <Button size="sm" onClick={onEdit}>
            <Pencil size={12} aria-hidden /> Edit
          </Button>
          <ConfirmAction
            label="Delete"
            tone="danger"
            consequence="Removes the agent. Blocked while a non-terminal plan references it."
            pending={deleteAgent.isPending}
            onConfirm={() => deleteAgent.mutate(agent.id)}
          />
        </>
      }
    >
      <div className={styles.kv}>
        <span className="label">role</span>
        <span className={styles.mono}>
          {agent.role} · model tier: {agent.model_role}
        </span>
        <span className="label">runtime</span>
        <span className={styles.mono}>
          {agent.runtime_type ?? 'pi'}
          {agent.provider_id ? (
            ` · ${agent.provider_id} / ${agent.model_id ?? '(no model)'}`
          ) : agent.runtime_type === 'dry-run' ? (
            ''
          ) : (
            <span className={styles.itemMeta}> · unbound — set a provider/model</span>
          )}
        </span>
        {agent.instructions && (
          <>
            <span className="label">instructions</span>
            <span className={styles.mono}>{agent.instructions}</span>
          </>
        )}
        <span className="label">capabilities</span>
        <span>
          {(agent.capabilities ?? []).length === 0 ? (
            <span className={styles.itemMeta}>none — matches only as default</span>
          ) : (
            <span className={styles.chips}>
              {(agent.capabilities ?? []).map((c) => (
                <span key={c.id} className={styles.chip}>
                  {c.id}
                </span>
              ))}
            </span>
          )}
        </span>
        <span className="label">retry</span>
        <span className={styles.mono}>
          {retry.max_attempts} attempts · backoff {retry.initial_backoff_seconds}s ×
          {retry.backoff_multiplier} (max {retry.max_backoff_seconds}s)
          {retry.non_retryable_kinds.length > 0 &&
            ` · never retries: ${retry.non_retryable_kinds.join(', ')}`}
        </span>
      </div>
    </Card>
  );
}

/* ── Create / edit agent dialog ───────────────────────────────────────────── */

function AgentDialog({
  open,
  onClose,
  agent,
}: {
  open: boolean;
  onClose: () => void;
  agent: AgentSpec | null; // null = create
}) {
  const { data: capabilities = [] } = useCapabilities();
  const { data: providers = [] } = useProviders();
  const { data: models = [] } = useModels();
  const create = useCreateAgent();
  const update = useUpdateAgent();

  const [name, setName] = useState('');
  const [role, setRole] = useState('');
  const [modelRole, setModelRole] = useState('');
  const [instructions, setInstructions] = useState('');
  const [capIds, setCapIds] = useState<string[]>([]);
  const [runtimeType, setRuntimeType] = useState('pi');
  const [providerId, setProviderId] = useState('');
  const [modelId, setModelId] = useState('');
  const [retry, setRetry] = useState<Record<string, string>>({});
  const [nonRetryable, setNonRetryable] = useState<FailureKind[]>([]);

  const [seededFor, setSeededFor] = useState<string | null>(null);
  const target = agent?.id ?? '__new__';
  if (open && seededFor !== target) {
    setSeededFor(target);
    setName(agent?.name ?? '');
    setRole(agent?.role ?? 'implementer');
    setModelRole(agent?.model_role ?? 'smart');
    setInstructions(agent?.instructions ?? '');
    setCapIds((agent?.capabilities ?? []).map((c) => c.id));
    setRuntimeType(agent?.runtime_type ?? 'pi');
    setProviderId(agent?.provider_id ?? '');
    setModelId(agent?.model_id ?? '');
    const r = { ...RETRY_DEFAULTS, ...agent?.default_retry };
    setRetry({
      max_attempts: String(r.max_attempts),
      initial_backoff_seconds: String(r.initial_backoff_seconds),
      backoff_multiplier: String(r.backoff_multiplier),
      max_backoff_seconds: String(r.max_backoff_seconds),
    });
    setNonRetryable([...r.non_retryable_kinds]);
  }
  if (!open && seededFor !== null) setSeededFor(null);

  const pending = create.isPending || update.isPending;
  const canSubmit = name.trim() !== '' && role.trim() !== '' && modelRole.trim() !== '';

  const providerModels = models.filter((m) => m.provider_id === providerId);
  const modelIsStray = modelId !== '' && !providerModels.some((m) => m.id === modelId);

  const toggleCap = (id: string) =>
    setCapIds((ids) => (ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id]));
  const toggleKind = (kind: FailureKind) =>
    setNonRetryable((ks) =>
      ks.includes(kind) ? ks.filter((k) => k !== kind) : [...ks, kind],
    );

  const submit = () => {
    if (!canSubmit) return;
    const body: AgentBody = {
      name: name.trim(),
      role: role.trim(),
      model_role: modelRole.trim(),
      instructions,
      capability_ids: capIds,
      runtime_type: runtimeType,
      provider_id: providerId || null,
      model_id: modelId || null,
      default_retry: {
        max_attempts: Number(retry.max_attempts) || RETRY_DEFAULTS.max_attempts,
        initial_backoff_seconds:
          Number(retry.initial_backoff_seconds) || RETRY_DEFAULTS.initial_backoff_seconds,
        backoff_multiplier:
          Number(retry.backoff_multiplier) || RETRY_DEFAULTS.backoff_multiplier,
        max_backoff_seconds:
          Number(retry.max_backoff_seconds) || RETRY_DEFAULTS.max_backoff_seconds,
        non_retryable_kinds: nonRetryable,
      },
    };
    if (agent === null) create.mutate(body, { onSuccess: onClose });
    else update.mutate({ id: agent.id, body }, { onSuccess: onClose });
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      ariaLabel={agent === null ? 'Add agent' : `Edit agent ${agent.name}`}
      title={agent === null ? 'Add agent' : 'Edit agent'}
      width={640}
    >
      <div className={styles.form}>
        <div className={styles.formGrid2}>
          <Field label="Name" htmlFor="agent-name">
            <Input
              id="agent-name"
              value={name}
              placeholder="e.g. dev-agent"
              onChange={(e) => setName(e.target.value)}
            />
          </Field>
          <Field label="Role" htmlFor="agent-role">
            <Input
              id="agent-role"
              value={role}
              placeholder="e.g. implementer"
              onChange={(e) => setRole(e.target.value)}
            />
          </Field>
        </div>
        <Field
          label="Model tier"
          htmlFor="agent-model-role"
          hint="A tier name resolved at runtime (e.g. smart, cheap, long_context) — not a model id."
        >
          <Input
            id="agent-model-role"
            mono
            value={modelRole}
            placeholder="smart"
            onChange={(e) => setModelRole(e.target.value)}
          />
        </Field>
        <Field label="Instructions" htmlFor="agent-instructions">
          <TextArea
            id="agent-instructions"
            value={instructions}
            placeholder="Standing instructions prepended to every task this agent runs."
            onChange={(e) => setInstructions(e.target.value)}
          />
        </Field>

        <Field
          label="Capabilities"
          hint={
            capabilities.length === 0
              ? 'No capabilities registered yet — add them under Capabilities.'
              : 'Tasks requiring a subset of these bind to this agent.'
          }
        >
          <div className={styles.checkGroup}>
            {capabilities.map((c) => (
              <label key={c.id} className={styles.checkRow}>
                <input
                  type="checkbox"
                  checked={capIds.includes(c.id)}
                  onChange={() => toggleCap(c.id)}
                />
                <span className={styles.mono}>{c.id}</span>
                <span className={styles.itemMeta}>{c.name}</span>
              </label>
            ))}
          </div>
        </Field>

        <fieldset className={styles.fieldset}>
          <legend className="label">Runtime</legend>
          <Field
            label="Runtime type"
            htmlFor="agent-runtime"
            hint="Which CLI executes this agent's tasks in agent_runner.mode=real. dry-run agents simulate."
          >
            <Select
              id="agent-runtime"
              mono
              value={runtimeType}
              onChange={(e) => setRuntimeType(e.target.value)}
              options={RUNTIME_TYPES}
            />
          </Field>
          {runtimeType !== 'dry-run' && (
            <div className={styles.formGrid2}>
              <Field
                label="Provider"
                htmlFor="agent-provider"
                hint={
                  providers.length === 0
                    ? 'No providers registered — add one under Providers & models.'
                    : runtimeType === 'pi'
                      ? 'pi needs a provider named anthropic, gemini or openrouter.'
                      : undefined
                }
              >
                <Select
                  id="agent-provider"
                  mono
                  value={providerId}
                  onChange={(e) => setProviderId(e.target.value)}
                  placeholder={providers.length === 0 ? 'No providers registered' : 'Choose a provider'}
                  disabled={providers.length === 0}
                  options={providers.map((p) => ({ value: p.id, label: `${p.name} — ${p.id}` }))}
                />
              </Field>
              <Field
                label="Model"
                htmlFor="agent-model"
                hint={modelIsStray ? 'This model does not belong to the selected provider.' : undefined}
              >
                <Select
                  id="agent-model"
                  mono
                  value={modelId}
                  onChange={(e) => setModelId(e.target.value)}
                  placeholder={
                    providerId === ''
                      ? 'Pick a provider first'
                      : providerModels.length === 0
                        ? 'The provider has no models'
                        : 'Choose a model'
                  }
                  disabled={providerId === '' || (providerModels.length === 0 && !modelIsStray)}
                  options={[
                    ...providerModels.map((m) => ({ value: m.id, label: `${m.name} — ${m.id}` })),
                    ...(modelIsStray
                      ? [{ value: modelId, label: `${modelId} — not in selected provider`, disabled: true }]
                      : []),
                  ]}
                />
              </Field>
            </div>
          )}
        </fieldset>

        <fieldset className={styles.fieldset}>
          <legend className="label">Retry policy</legend>
          <div className={styles.formGrid2}>
            <Field label="Max attempts" htmlFor="retry-max-attempts">
              <Input
                id="retry-max-attempts"
                mono
                type="number"
                min={1}
                step={1}
                value={retry.max_attempts ?? ''}
                onChange={(e) => setRetry((r) => ({ ...r, max_attempts: e.target.value }))}
              />
            </Field>
            <Field label="Initial backoff (s)" htmlFor="retry-initial">
              <Input
                id="retry-initial"
                mono
                type="number"
                min={0}
                step={0.5}
                value={retry.initial_backoff_seconds ?? ''}
                onChange={(e) =>
                  setRetry((r) => ({ ...r, initial_backoff_seconds: e.target.value }))
                }
              />
            </Field>
            <Field label="Backoff multiplier" htmlFor="retry-mult">
              <Input
                id="retry-mult"
                mono
                type="number"
                min={1}
                step={0.5}
                value={retry.backoff_multiplier ?? ''}
                onChange={(e) =>
                  setRetry((r) => ({ ...r, backoff_multiplier: e.target.value }))
                }
              />
            </Field>
            <Field label="Max backoff (s)" htmlFor="retry-max-backoff">
              <Input
                id="retry-max-backoff"
                mono
                type="number"
                min={0}
                step={1}
                value={retry.max_backoff_seconds ?? ''}
                onChange={(e) =>
                  setRetry((r) => ({ ...r, max_backoff_seconds: e.target.value }))
                }
              />
            </Field>
          </div>
          <Field
            label="Never retry on"
            hint="Failure kinds treated as terminal for this agent's tasks."
          >
            <div className={styles.checkGroup}>
              {FAILURE_KINDS.map((kind) => (
                <label key={kind} className={styles.checkRow}>
                  <input
                    type="checkbox"
                    checked={nonRetryable.includes(kind)}
                    onChange={() => toggleKind(kind)}
                  />
                  <span className={styles.mono}>{kind}</span>
                </label>
              ))}
            </div>
          </Field>
        </fieldset>

        <div className={styles.formFoot}>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={!canSubmit} pending={pending} onClick={submit}>
            {agent === null ? 'Add agent' : 'Save changes'}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
