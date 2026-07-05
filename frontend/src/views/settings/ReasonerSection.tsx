import React, { useState } from 'react';
import { CheckCircle2, TriangleAlert } from 'lucide-react';
import {
  useConfigScope,
  useModels,
  useProviders,
  useReasonerStatus,
  useSetConfigKey,
} from '../../lib/queries';
import { Button, Card, Input, Select } from '../../components/ui';
import styles from './Settings.module.css';

const SCOPE = 'orchestrator';

/**
 * The reasoner resolves through the providers catalog (never env vars):
 * mode stub|llm, then provider/model picked from the registered rows.
 * The banner re-validates the STORED config live via /api/reasoner/status.
 */
export function ReasonerSection() {
  const { data: config = {} } = useConfigScope(SCOPE);
  const { data: providers = [] } = useProviders();
  const { data: models = [] } = useModels();
  const save = useSetConfigKey(SCOPE);

  const [draft, setDraft] = useState<Record<string, string>>({});
  const valueOf = (key: string) => draft[key] ?? config[key] ?? '';
  const isDirty = (key: string) => valueOf(key) !== (config[key] ?? '');
  const setKey = (key: string, value: string) =>
    setDraft((d) => ({ ...d, [key]: value }));

  // Models offered for reasoner.model_id follow the provider being edited.
  const selectedProvider = valueOf('reasoner.provider_id');
  const providerModels = models.filter((m) => m.provider_id === selectedProvider);
  const storedModel = valueOf('reasoner.model_id');
  const modelIsStray =
    storedModel !== '' && !providerModels.some((m) => m.id === storedModel);

  const saveRow = (key: string) => save.mutate({ key, value: valueOf(key) });

  return (
    <div className={styles.section}>
      <div className={styles.sectionHead}>
        <div>
          <h2 className={styles.sectionTitle}>Reasoner</h2>
          <p className={styles.sectionDesc}>
            The planning brain behind discovery, enrichment and replanning.
            Changes apply after the API and worker restart.
          </p>
        </div>
      </div>

      <StatusBanner />

      <Card title="Configuration" actions={<span className="label">scope: orchestrator</span>}>
        <ConfigRow label="reasoner.mode" dirty={isDirty('reasoner.mode')}>
          <Select
            aria-label="reasoner.mode"
            mono
            value={valueOf('reasoner.mode') || 'stub'}
            onChange={(e) => setKey('reasoner.mode', e.target.value)}
            options={[
              { value: 'stub', label: 'stub — deterministic, no LLM, no API key' },
              { value: 'llm', label: 'llm — reason with a registered provider model' },
            ]}
          />
          <SaveButton dirty={isDirty('reasoner.mode')} pending={save.isPending} onClick={() => saveRow('reasoner.mode')} />
        </ConfigRow>

        <ConfigRow
          label="reasoner.provider_id"
          dirty={isDirty('reasoner.provider_id')}
          hint={providers.length === 0 ? 'No providers registered yet — add one under Providers & models.' : undefined}
        >
          <Select
            aria-label="reasoner.provider_id"
            mono
            value={valueOf('reasoner.provider_id')}
            onChange={(e) => {
              setKey('reasoner.provider_id', e.target.value);
            }}
            placeholder={providers.length === 0 ? 'No providers registered' : 'Choose a provider'}
            disabled={providers.length === 0}
            options={providers.map((p) => ({ value: p.id, label: `${p.name} — ${p.id}` }))}
          />
          <SaveButton
            dirty={isDirty('reasoner.provider_id')}
            pending={save.isPending}
            onClick={() => saveRow('reasoner.provider_id')}
          />
        </ConfigRow>

        <ConfigRow
          label="reasoner.model_id"
          dirty={isDirty('reasoner.model_id')}
          hint={
            modelIsStray
              ? 'The stored model does not belong to the selected provider — pick one of its models.'
              : selectedProvider === ''
                ? 'Pick a provider first; models are scoped to it.'
                : undefined
          }
        >
          <Select
            aria-label="reasoner.model_id"
            mono
            value={storedModel}
            onChange={(e) => setKey('reasoner.model_id', e.target.value)}
            placeholder={
              selectedProvider === ''
                ? 'Pick a provider first'
                : providerModels.length === 0
                  ? 'The selected provider has no models'
                  : 'Choose a model'
            }
            disabled={selectedProvider === '' || (providerModels.length === 0 && !modelIsStray)}
            options={[
              ...providerModels.map((m) => ({ value: m.id, label: `${m.name} — ${m.id}` })),
              ...(modelIsStray
                ? [{ value: storedModel, label: `${storedModel} — not in selected provider`, disabled: true }]
                : []),
            ]}
          />
          <SaveButton
            dirty={isDirty('reasoner.model_id')}
            pending={save.isPending}
            onClick={() => saveRow('reasoner.model_id')}
          />
        </ConfigRow>

        <ConfigRow label="reasoner.temperature" dirty={isDirty('reasoner.temperature')}>
          <Input
            aria-label="reasoner.temperature"
            mono
            type="number"
            step={0.1}
            min={0}
            max={2}
            placeholder="0.2"
            value={valueOf('reasoner.temperature')}
            onChange={(e) => setKey('reasoner.temperature', e.target.value)}
          />
          <SaveButton
            dirty={isDirty('reasoner.temperature')}
            pending={save.isPending}
            onClick={() => saveRow('reasoner.temperature')}
          />
        </ConfigRow>

        <ConfigRow label="reasoner.max_turns" dirty={isDirty('reasoner.max_turns')}>
          <Input
            aria-label="reasoner.max_turns"
            mono
            type="number"
            step={1}
            min={1}
            placeholder="8"
            value={valueOf('reasoner.max_turns')}
            onChange={(e) => setKey('reasoner.max_turns', e.target.value)}
          />
          <SaveButton
            dirty={isDirty('reasoner.max_turns')}
            pending={save.isPending}
            onClick={() => saveRow('reasoner.max_turns')}
          />
        </ConfigRow>
      </Card>
    </div>
  );
}

function StatusBanner() {
  const { data: status } = useReasonerStatus();
  if (!status) return null;

  if (status.valid) {
    return (
      <div className={`${styles.banner} ${styles.bannerOk}`} role="status">
        <CheckCircle2 size={15} aria-hidden />
        <span>
          Reasoner wiring valid — mode <strong>{status.mode}</strong>
          {status.mode === 'llm' && (
            <>
              {': '}
              <span className={styles.bannerWiring}>
                {status.provider_name} / {status.model_name}
              </span>
            </>
          )}
          .
        </span>
      </div>
    );
  }
  return (
    <div className={`${styles.banner} ${styles.bannerWarn}`} role="status">
      <TriangleAlert size={15} aria-hidden />
      <span>{status.detail}</span>
    </div>
  );
}

function ConfigRow({
  label,
  dirty,
  hint,
  children,
}: {
  label: string;
  dirty: boolean;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <>
      <div className={styles.cfgRow}>
        <span className={styles.cfgKey}>
          {label}
          {dirty ? ' *' : ''}
        </span>
        {children}
      </div>
      {hint && <div className={styles.cfgHint}>{hint}</div>}
    </>
  );
}

function SaveButton({
  dirty,
  pending,
  onClick,
}: {
  dirty: boolean;
  pending: boolean;
  onClick: () => void;
}) {
  return (
    <Button variant="primary" size="sm" disabled={!dirty} pending={pending && dirty} onClick={onClick}>
      Save
    </Button>
  );
}
