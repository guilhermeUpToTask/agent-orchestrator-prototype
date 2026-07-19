import React, { memo } from 'react';
import { Handle, Position, type Node, type NodeProps } from '@xyflow/react';
import type { TaskNodeData } from '../types/ui';
import { tokens } from '../styles/tokens';
import { usePlannerStore } from '../store/plannerStore';
import { StatusBadge } from './StatusBadge';
import { CountChip } from './ui';
import { attemptLabel, verificationLabel } from '../lib/taskLabels';

function TaskNodeComponent({ id, data }: NodeProps<Node<TaskNodeData>>) {
  const selectTask = usePlannerStore((s) => s.selectTask);
  const selectedTaskId = usePlannerStore((s) => s.ui.selectedTaskId);
  const isSelected = selectedTaskId === id;

  const task = data.task;
  const status = task?.status ?? 'pending';
  const isRunning = status === 'running';
  const isFailed = status === 'failed';
  const isSkipped = status === 'skipped';

  const attempt = task ? attemptLabel(task, data.agent) : null;
  const verification = task ? verificationLabel(task) : null;

  const borderColor = isSelected
    ? tokens.accent
    : isFailed
      ? 'color-mix(in srgb, var(--fail) 45%, transparent)'
      : isRunning
        ? 'color-mix(in srgb, var(--run) 45%, transparent)'
        : tokens.border;

  return (
    <>
      <Handle type="target" position={Position.Left} style={{
        background: tokens.accentDim, border: `1.5px solid ${isSelected ? tokens.accent : 'var(--border-1)'}`,
        width: 8, height: 8, left: -4,
      }} />

      <div onClick={() => selectTask(isSelected ? null : id)} style={{
        width: 250,
        background: isSelected ? 'var(--bg-3)' : 'var(--bg-1)',
        border: `1.5px solid ${borderColor}`,
        borderRadius: 'var(--r-2)', cursor: 'pointer', userSelect: 'none',
        padding: 'var(--sp-2) var(--sp-3)', display: 'flex', flexDirection: 'column', gap: 5,
        boxShadow: isSelected
          ? `0 0 0 1px ${tokens.accentGlow}, 0 8px 32px rgba(0,0,0,0.7)`
          : isRunning ? '0 0 16px color-mix(in srgb, var(--run) 13%, transparent), 0 4px 12px rgba(0,0,0,0.5)'
          : '0 4px 16px rgba(0,0,0,0.4)',
        transition: 'box-shadow 0.2s, border-color 0.2s',
        animation: 'fadein 0.18s ease both',
        opacity: isSkipped ? 0.55 : 1,
      }}>
        {/* Row 1: status + agent */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <StatusBadge domain="status" value={status} bare />
          <div style={{ flex: 1 }} />
          {data.agent && (
            <span style={{
              fontSize: 'var(--fs-micro)', fontFamily: tokens.fontMono, padding: '1px 6px',
              borderRadius: 'var(--r-1)', background: 'var(--run-bg)', color: 'var(--run-text)',
            }}>{data.agent.name}</span>
          )}
        </div>

        {/* Row 2: task name */}
        <div style={{ fontSize: 'var(--fs-body)', fontWeight: 500, color: tokens.textPrimary, lineHeight: 1.35 }}>
          {task?.name ?? id}
        </div>

        {task?.required_capabilities && task.required_capabilities.length > 0 && (
          <div style={{ fontSize: 'var(--fs-micro)', fontFamily: tokens.fontMono, color: tokens.textMuted }}>
            caps: {task.required_capabilities.join(', ')}
          </div>
        )}

        {/* Row 3: chips */}
        {(attempt || verification) && (
          <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
            {attempt && <CountChip tone="fail">{attempt}</CountChip>}
            {verification && (
              <CountChip tone={verification === 'verified' ? 'ok' : 'fail'}>
                {verification === 'verified' ? 'verified' : 'verification rejected'}
              </CountChip>
            )}
          </div>
        )}

        {isFailed && task?.result?.failure_reason && (
          <div style={{
            fontFamily: tokens.fontMono, fontSize: 'var(--fs-micro)', color: 'var(--fail-text)',
            lineHeight: 1.3,
            display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical',
            overflow: 'hidden',
          }}>
            {task.result.failure_reason}
          </div>
        )}
      </div>

      <Handle type="source" position={Position.Right} style={{
        background: status === 'done' ? tokens.green : tokens.accentDim,
        border: `1.5px solid ${isSelected ? tokens.accent : 'var(--border-1)'}`,
        width: 8, height: 8, right: -4,
      }} />
    </>
  );
}

export const TaskNode = memo(TaskNodeComponent);
