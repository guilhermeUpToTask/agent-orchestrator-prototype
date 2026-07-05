import React, { useState } from 'react';
import { CheckCircle2, CircleSlash, TriangleAlert } from 'lucide-react';
import {
  useConfigScope,
  useRunnerStatus,
  useSetConfigKey,
} from '../../lib/queries';
import { Button, Card, Input, Select } from '../../components/ui';
import type { RunnerAgentStatus, RunnerBinaryStatus } from '../../types/ui';
import styles from './Settings.module.css';

const SCOPE = 'orchestrator';

/**
 * The agent runner resolves through the AGENT REGISTRY + providers catalog
 * (never env vars): agent_runner.mode picks dry-run|real globally; in real
 * mode each agent's runtime_type/provider/model binding (edited under
 * Agents) resolves per task. The banner re-validates the STORED state live
 * via /api/runner/status, including the CLI binary probes.
 */
export function RunnerSection() {
  const { data: config = {} } = useConfigScope(SCOPE);
  const { data: status } = useRunnerStatus();
  const save = useSetConfigKey(SCOPE);

  const [draft, setDraft] = useState<Record<string, string>>({});
  const valueOf = (key: string) => draft[key] ?? config[key] ?? '';
  const isDirty = (key: string) => valueOf(key) !== (config[key] ?? '');
  const setKey = (key: string, value: string) =>
    setDraft((d) => ({ ...d, [key]: value }));
  const saveRow = (key: string) => save.mutate({ key, value: valueOf(key) });

  return (
    <div className={styles.section}>
      <div className={styles.sectionHead}>
        <div>
          <h2 className={styles.sectionTitle}>Agent runtime</h2>
          <p className={styles.sectionDesc}>
            How tasks execute. dry-run needs no binaries or keys; real mode
            resolves each agent&apos;s runtime binding (set under Agents)
            through the providers catalog per run.
          </p>
        </div>
      </div>

      <StatusBanner />

      <Card title="Configuration" actions={<span className="label">scope: orchestrator</span>}>
        <ConfigRow label="agent_runner.mode" dirty={isDirty('agent_runner.mode')}>
          <Select
            aria-label="agent_runner.mode"
            mono
            value={valueOf('agent_runner.mode') || 'dry-run'}
            onChange={(e) => setKey('agent_runner.mode', e.target.value)}
            options={[
              { value: 'dry-run', label: 'dry-run — simulated runs, no binaries, no keys' },
              { value: 'real', label: 'real — each agent runs on its bound CLI runtime' },
            ]}
          />
          <SaveButton
            dirty={isDirty('agent_runner.mode')}
            pending={save.isPending}
            onClick={() => saveRow('agent_runner.mode')}
          />
        </ConfigRow>

        <ConfigRow
          label="agent_runner.timeout_seconds"
          dirty={isDirty('agent_runner.timeout_seconds')}
          hint="Per-attempt subprocess timeout for the CLI runtimes."
        >
          <Input
            aria-label="agent_runner.timeout_seconds"
            mono
            type="number"
            step={30}
            min={30}
            placeholder="600"
            value={valueOf('agent_runner.timeout_seconds')}
            onChange={(e) => setKey('agent_runner.timeout_seconds', e.target.value)}
          />
          <SaveButton
            dirty={isDirty('agent_runner.timeout_seconds')}
            pending={save.isPending}
            onClick={() => saveRow('agent_runner.timeout_seconds')}
          />
        </ConfigRow>
      </Card>

      {status && (
        <Card title="Agent bindings">
          {status.agents.length === 0 ? (
            <div className={styles.empty}>No agents registered yet.</div>
          ) : (
            status.agents.map((a) => <AgentBindingRow key={a.agent_id} agent={a} />)
          )}
        </Card>
      )}

      {status && (
        <Card title="Runtime binaries">
          {status.binaries.map((b) => (
            <BinaryRow key={b.name} binary={b} />
          ))}
        </Card>
      )}
    </div>
  );
}

function StatusBanner() {
  const { data: status } = useRunnerStatus();
  if (!status) return null;

  if (status.valid) {
    return (
      <div className={`${styles.banner} ${styles.bannerOk}`} role="status">
        <CheckCircle2 size={15} aria-hidden />
        <span>
          Agent runner valid — mode <strong>{status.mode}</strong>
          {status.mode === 'dry-run' &&
            ' (simulated runs; switch to real to execute with CLI agents)'}
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

function AgentBindingRow({ agent }: { agent: RunnerAgentStatus }) {
  return (
    <div className={styles.itemRow}>
      <div className={styles.itemMain}>
        <span className={styles.itemName}>
          {agent.agent_name}
          <span className={styles.itemMeta}>{agent.agent_id}</span>
        </span>
        <span className={styles.mono}>
          {agent.runtime_type}
          {agent.provider_id &&
            ` · ${agent.provider_name ?? agent.provider_id} / ${agent.model_name ?? agent.model_id}`}
        </span>
        {!agent.valid && <span className={styles.cfgHint}>{agent.detail}</span>}
      </div>
      <div className={styles.itemActions}>
        {agent.valid ? (
          <CheckCircle2 size={15} aria-hidden color="var(--ok)" />
        ) : (
          <TriangleAlert size={15} aria-hidden color="var(--gate)" />
        )}
      </div>
    </div>
  );
}

function BinaryRow({ binary }: { binary: RunnerBinaryStatus }) {
  return (
    <div className={styles.itemRow}>
      <div className={styles.itemMain}>
        <span className={styles.itemName}>
          {binary.name}
          <span className={styles.itemMeta}>{binary.binary}</span>
        </span>
        <span className={styles.mono}>{binary.message}</span>
        {!binary.ok && binary.install_hint && (
          <span className={styles.cfgHint}>{binary.install_hint}</span>
        )}
      </div>
      <div className={styles.itemActions}>
        {binary.ok ? (
          <CheckCircle2 size={15} aria-hidden color="var(--ok)" />
        ) : (
          <CircleSlash size={15} aria-hidden color="var(--text-3)" />
        )}
      </div>
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
