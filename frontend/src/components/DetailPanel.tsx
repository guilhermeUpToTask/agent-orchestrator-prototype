import React from 'react';
import { X } from 'lucide-react';
import { useParams } from 'react-router-dom';
import { tokens } from '../styles/tokens';
import { usePlannerStore } from '../store/plannerStore';
import { useAgents, usePlan } from '../lib/queries';
import { StatusBadge } from './StatusBadge';

function Label({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      fontSize: 9, fontFamily: tokens.fontMono, color: tokens.textMuted,
      letterSpacing: '0.1em', marginBottom: 4, textTransform: 'uppercase',
    }}>{children}</div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 14 }}>
      <Label>{label}</Label>
      {children}
    </div>
  );
}

function Tag({ text, color }: { text: string; color?: string }) {
  const c = color ?? tokens.accent;
  return (
    <span style={{
      padding: '2px 8px', borderRadius: tokens.r4,
      background: c + '18', border: `1px solid ${c + '33'}`,
      fontSize: 9, fontFamily: tokens.fontMono, color: c,
    }}>{text}</span>
  );
}

/**
 * The task inspector: everything the aggregate knows about one task —
 * status, agent binding, capabilities, attempts, and the persisted
 * TaskResult (output / failure reason) once the task settles.
 */
export function DetailPanel() {
  const { planId = '' } = useParams();
  const selectedTaskId = usePlannerStore((s) => s.ui.selectedTaskId);
  const detailPanelOpen = usePlannerStore((s) => s.ui.detailPanelOpen);
  const selectTask = usePlannerStore((s) => s.selectTask);

  const { data: plan } = usePlan(planId || null);
  const { data: agents = [] } = useAgents();

  const goal = plan?.goals.find((g) => g.tasks.some((t) => t.id === selectedTaskId));
  const task = goal?.tasks.find((t) => t.id === selectedTaskId);

  if (!detailPanelOpen || !task || !goal) return null;

  const agent = task.agent_id ? agents.find((a) => a.id === task.agent_id) ?? null : null;

  return (
    <aside style={{
      width: 320, flexShrink: 0, overflowY: 'auto',
      background: tokens.panelBg, borderLeft: `1px solid ${tokens.border}`,
      padding: '14px 16px',
    }} aria-label="Task detail">
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 14 }}>
        <StatusBadge domain="status" value={task.status} />
        <div style={{ flex: 1 }} />
        <button
          onClick={() => selectTask(null)}
          aria-label="Close task detail"
          style={{ background: 'transparent', border: 'none', color: tokens.textMuted, cursor: 'pointer', display: 'flex' }}
        >
          <X size={15} />
        </button>
      </div>

      <h2 style={{
        fontSize: 14, fontWeight: 600, color: tokens.textPrimary,
        lineHeight: 1.4, margin: '0 0 4px',
      }}>{task.name}</h2>
      <div style={{ fontSize: 9, fontFamily: tokens.fontMono, color: tokens.textDim, marginBottom: 16 }}>
        {task.id}
      </div>

      <Field label="Goal">
        <span style={{ fontSize: 11, color: tokens.textSecond }}>{goal.name}</span>
      </Field>

      {task.description && (
        <Field label="Description">
          <p style={{ fontSize: 11, color: tokens.textSecond, lineHeight: 1.6, margin: 0, whiteSpace: 'pre-wrap' }}>
            {task.description}
          </p>
        </Field>
      )}

      <Field label="Agent">
        {agent
          ? <Tag text={agent.name} />
          : <span style={{ fontSize: 10, color: tokens.textMuted }}>unbound (bound at enrichment)</span>}
      </Field>

      {task.required_capabilities.length > 0 && (
        <Field label="Required capabilities">
          <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
            {task.required_capabilities.map((c) => <Tag key={c} text={c} color={tokens.purple} />)}
          </div>
        </Field>
      )}

      {(task.attempt > 0 || task.reopen_count > 0) && (
        <Field label="Attempts">
          <span style={{ fontSize: 11, fontFamily: tokens.fontMono, color: tokens.textSecond }}>
            attempt {task.attempt}
            {task.reopen_count > 0 && ` · reopened ${task.reopen_count}×`}
          </span>
        </Field>
      )}

      {task.retry_not_before && task.status === 'pending' && (
        <Field label="Backoff gate">
          <span style={{ fontSize: 10, fontFamily: tokens.fontMono, color: tokens.yellow }}>
            retry not before {new Date(task.retry_not_before).toLocaleTimeString()}
          </span>
        </Field>
      )}

      {task.result && (
        <Field label={task.result.status === 'success' ? 'Result' : 'Failure'}>
          {task.result.failure_reason && (
            <div style={{
              padding: '6px 9px', borderRadius: 5, marginBottom: 6,
              background: tokens.redDim, border: `1px solid ${tokens.red}33`,
              fontSize: 10, fontFamily: tokens.fontMono, color: tokens.red, lineHeight: 1.5,
            }}>
              {task.result.failure_reason}
              {task.result.failure_kind && ` (${task.result.failure_kind})`}
            </div>
          )}
          {task.result.output && (
            <pre style={{
              margin: 0, padding: '8px 10px', borderRadius: 5,
              background: '#0d1018', border: `1px solid ${tokens.borderMuted}`,
              fontSize: 9.5, fontFamily: tokens.fontMono, color: tokens.textSecond,
              lineHeight: 1.6, whiteSpace: 'pre-wrap', wordBreak: 'break-word',
              maxHeight: 260, overflowY: 'auto',
            }}>{task.result.output}</pre>
          )}
        </Field>
      )}
    </aside>
  );
}
